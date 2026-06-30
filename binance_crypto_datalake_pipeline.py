#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Importamos argparse para poder ejecutar el pipeline desde terminal con parámetros configurables.
import argparse
# Importamos csv para escribir el log de descargas en formato CSV de forma controlada.
import csv
# Importamos hashlib para calcular SHA256 local de cada ZIP descargado.
import hashlib
# Importamos json para exportar metadata y reportes de calidad auditables.
import json
# Importamos logging para registrar mensajes técnicos durante la ejecución.
import logging
# Importamos re para validar símbolos, intervalos y años-mes sin depender de suposiciones frágiles.
import re
# Importamos sys para terminar el programa con códigos de salida claros cuando haya errores críticos.
import sys
# Importamos zipfile para leer los CSV comprimidos que entrega Binance Vision.
import zipfile
# Importamos dataclass para agrupar la configuración del pipeline de forma explícita.
from dataclasses import dataclass
# Importamos date para calcular rangos mensuales reproducibles.
from datetime import date
# Importamos datetime y timezone para sellar logs en UTC.
from datetime import datetime, timezone
# Importamos Path para manejar rutas de forma portable en Windows, Linux y macOS.
from pathlib import Path
# Importamos typing para documentar tipos esperados en funciones clave.
from typing import Dict, Iterable, List, Optional, Tuple

# Importamos numpy para operaciones vectorizadas en validaciones de timestamps mixtos ms/us.
import numpy as np
# Importamos pandas porque será el motor principal para limpieza, unión y control de calidad tabular.
import pandas as pd
# Importamos requests para descargar archivos HTTP desde data.binance.vision.
import requests


# Definimos la URL base pública de Binance Vision para datos históricos.
BASE_URL = "https://data.binance.vision"

# Definimos los nombres oficiales de columnas para klines Spot de Binance.
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]

# Definimos los intervalos soportados por Binance Vision para klines.
SUPPORTED_INTERVALS = {
    "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1mo"
}

# Definimos equivalencias entre intervalos Binance y frecuencias de pandas para detectar huecos.
PANDAS_FREQ_BY_INTERVAL = {
    "1s": "1s",
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1D",
    "3d": "3D",
    "1w": "7D",
}

# FIX: constante única para las columnas del log de descargas. Antes estaba
# duplicada en ensure_download_log y append_download_log; una desincronización
# entre ambas podía corromper el CSV silenciosamente.
DOWNLOAD_LOG_FIELDNAMES = [
    "downloaded_at_utc",
    "symbol",
    "interval",
    "month",
    "file_name",
    "zip_url",
    "checksum_url",
    "size_bytes",
    "expected_sha256",
    "actual_sha256",
    "status",
    "message",
]

# Definimos las columnas que deben ser numéricas para analizar OHLCV.
NUMERIC_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]

# Definimos columnas de precio para reglas de calidad sobre OHLC.
PRICE_COLUMNS = ["open", "high", "low", "close"]

# Definimos columnas de volumen para reglas de calidad sobre valores negativos.
VOLUME_COLUMNS = ["volume", "quote_asset_volume", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]


# Creamos una clase de configuración para que el pipeline no dependa de variables sueltas.
@dataclass(frozen=True)
class PipelineConfig:
    # Símbolo de Binance, por ejemplo BTCUSDT.
    symbol: str
    # Intervalo de vela, por ejemplo 1h.
    interval: str
    # Primer mes a procesar en formato YYYY-MM.
    start_month: str
    # Último mes a procesar en formato YYYY-MM.
    end_month: str
    # Raíz del Data Lake local.
    data_root: Path
    # Timeout HTTP por petición.
    timeout_seconds: int
    # Bandera para decidir si se fuerza la descarga aunque el archivo ya exista.
    overwrite: bool


# Configuramos logging para que el usuario vea progreso técnico en consola.
def setup_logging() -> None:
    # Definimos un formato simple con fecha, nivel y mensaje.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# Validamos el símbolo para evitar construir URLs peligrosas o inválidas.
def validate_symbol(symbol: str) -> str:
    # Convertimos el símbolo a mayúsculas porque Binance usa símbolos como BTCUSDT.
    clean_symbol = symbol.upper().strip()
    # Permitimos letras y números, que cubren pares como BTCUSDT, ETHUSDC o 1000SHIBUSDT.
    if not re.fullmatch(r"[A-Z0-9]+", clean_symbol):
        # Lanzamos error explícito si el símbolo no cumple el patrón esperado.
        raise ValueError(f"Símbolo inválido: {symbol}")
    # Regresamos el símbolo normalizado.
    return clean_symbol


# Validamos el intervalo contra la lista oficial conocida.
def validate_interval(interval: str) -> str:
    # Quitamos espacios para evitar errores por captura accidental.
    clean_interval = interval.strip()
    # Revisamos que el intervalo esté soportado por los archivos de klines.
    if clean_interval not in SUPPORTED_INTERVALS:
        # Lanzamos error con la lista permitida para corregir rápido.
        raise ValueError(f"Intervalo inválido: {interval}. Soportados: {sorted(SUPPORTED_INTERVALS)}")
    # Regresamos el intervalo validado.
    return clean_interval


# Validamos una cadena año-mes en formato YYYY-MM.
def validate_month(month: str) -> str:
    # Quitamos espacios para evitar errores por entrada manual.
    clean_month = month.strip()
    # Exigimos cuatro dígitos, guion y dos dígitos.
    if not re.fullmatch(r"\d{4}-\d{2}", clean_month):
        # Lanzamos error si el formato no es reproducible.
        raise ValueError(f"Mes inválido: {month}. Usa formato YYYY-MM.")
    # Separamos año y mes para validar el rango del mes.
    year, month_number = map(int, clean_month.split("-"))
    # Validamos que el mes esté entre 1 y 12.
    if month_number < 1 or month_number > 12:
        # Lanzamos error si el mes no existe.
        raise ValueError(f"Mes inválido: {month}. El mes debe estar entre 01 y 12.")
    # Validamos un año razonable para datos de Binance.
    if year < 2017:
        # Lanzamos error porque Binance no tiene historial Spot anterior a su existencia operativa.
        raise ValueError(f"Año inválido: {year}. Revisa el rango solicitado.")
    # Regresamos la cadena validada.
    return clean_month


# Sumamos meses a una fecha representada como primer día de mes.
def add_months(first_day: date, months: int) -> date:
    # Calculamos un índice lineal de meses desde el año cero.
    month_index = first_day.year * 12 + first_day.month - 1 + months
    # Convertimos el índice lineal de vuelta a año.
    new_year = month_index // 12
    # Convertimos el índice lineal de vuelta a mes de 1 a 12.
    new_month = month_index % 12 + 1
    # Regresamos siempre día 1 para representar un mes completo.
    return date(new_year, new_month, 1)


# Convertimos YYYY-MM a date usando el día 1 como representación del mes.
def month_to_date(month: str) -> date:
    # Validamos el mes antes de convertirlo.
    clean_month = validate_month(month)
    # Separamos año y mes.
    year, month_number = map(int, clean_month.split("-"))
    # Regresamos el primer día de ese mes.
    return date(year, month_number, 1)


# Convertimos una fecha de primer día de mes a YYYY-MM.
def date_to_month(first_day: date) -> str:
    # Formateamos con dos dígitos para el mes.
    return f"{first_day.year:04d}-{first_day.month:02d}"


# Generamos todos los meses entre start_month y end_month, ambos incluidos.
def month_range(start_month: str, end_month: str) -> List[str]:
    # Convertimos el mes inicial a date.
    start = month_to_date(start_month)
    # Convertimos el mes final a date.
    end = month_to_date(end_month)
    # Validamos que el rango no vaya al revés.
    if start > end:
        # Lanzamos error si el usuario pide un rango imposible.
        raise ValueError("start_month no puede ser mayor que end_month.")
    # Creamos una lista vacía para almacenar meses.
    months = []
    # Iniciamos el cursor en el mes inicial.
    cursor = start
    # Iteramos hasta incluir el mes final.
    while cursor <= end:
        # Agregamos el mes actual en formato YYYY-MM.
        months.append(date_to_month(cursor))
        # Avanzamos exactamente un mes.
        cursor = add_months(cursor, 1)
    # Regresamos la lista completa.
    return months


# Calculamos el rango de meses completos para los últimos N años.
def default_last_complete_months(years: int, today: Optional[date] = None) -> Tuple[str, str]:
    # Usamos la fecha actual si no se recibió una fecha de referencia.
    today = today or date.today()
    # Tomamos el primer día del mes actual para ubicar el mes en curso.
    current_month = date(today.year, today.month, 1)
    # El último mes completo es el mes anterior al mes actual.
    end = add_months(current_month, -1)
    # Para 5 años mensuales completos necesitamos 60 meses, contando el final incluido.
    start = add_months(end, -(years * 12 - 1))
    # Regresamos los meses en formato YYYY-MM.
    return date_to_month(start), date_to_month(end)


# Construimos la ruta relativa oficial del ZIP mensual de klines Spot.
def build_relative_zip_path(symbol: str, interval: str, month: str) -> str:
    # El patrón oficial es data/spot/monthly/klines/SYMBOL/INTERVAL/SYMBOL-INTERVAL-YYYY-MM.zip.
    return f"data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"


# Construimos la URL absoluta del ZIP mensual.
def build_zip_url(symbol: str, interval: str, month: str) -> str:
    # Reutilizamos la ruta relativa para evitar duplicar lógica.
    return f"{BASE_URL}/{build_relative_zip_path(symbol, interval, month)}"


# Construimos la URL absoluta del archivo CHECKSUM del ZIP.
def build_checksum_url(symbol: str, interval: str, month: str) -> str:
    # Binance publica el checksum agregando .CHECKSUM al nombre del ZIP.
    return f"{build_zip_url(symbol, interval, month)}.CHECKSUM"


# Calculamos rutas locales para almacenar datos raw, logs, procesados y reportes.
def build_local_paths(config: PipelineConfig) -> Dict[str, Path]:
    # Definimos la carpeta raw preservando la jerarquía de la fuente.
    raw_dir = config.data_root / "raw" / "binance" / "spot" / "monthly" / "klines" / config.symbol / config.interval
    # Definimos la carpeta de logs.
    logs_dir = config.data_root / "logs"
    # Definimos la carpeta processed particionada por fuente, mercado, dataset, símbolo e intervalo.
    processed_dir = config.data_root / "processed" / "binance" / "spot" / "klines" / config.symbol / config.interval
    # Definimos la carpeta de reportes.
    reports_dir = config.data_root / "reports" / "binance" / "spot" / "klines" / config.symbol / config.interval
    # Creamos las carpetas si no existen.
    for directory in [raw_dir, logs_dir, processed_dir, reports_dir]:
        # mkdir con parents=True permite crear toda la ruta completa.
        directory.mkdir(parents=True, exist_ok=True)
    # Regresamos las rutas en un diccionario con nombres claros.
    return {
        "raw_dir": raw_dir,
        "logs_dir": logs_dir,
        "processed_dir": processed_dir,
        "reports_dir": reports_dir,
        "download_log": logs_dir / "download_log.csv",
    }


# Descargamos texto desde una URL, usado principalmente para archivos CHECKSUM.
def download_text(url: str, timeout_seconds: int) -> str:
    # Ejecutamos una petición GET con timeout para evitar bloqueos infinitos.
    response = requests.get(url, timeout=timeout_seconds)
    # Si HTTP no es 2xx, requests levantará una excepción clara.
    response.raise_for_status()
    # Regresamos el contenido como texto sin espacios extremos.
    return response.text.strip()


# Descargamos un archivo binario grande por streaming.
def download_binary_file(url: str, output_path: Path, timeout_seconds: int) -> int:
    # Abrimos la petición en streaming para no cargar todo el ZIP en memoria.
    with requests.get(url, stream=True, timeout=timeout_seconds) as response:
        # Validamos que el servidor haya respondido correctamente.
        response.raise_for_status()
        # Abrimos el archivo local en modo binario de escritura.
        with output_path.open("wb") as file_handle:
            # Iteramos por bloques para soportar archivos grandes.
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                # Ignoramos chunks vacíos que pueden aparecer por keep-alive.
                if chunk:
                    # Escribimos el bloque al disco.
                    file_handle.write(chunk)
    # Regresamos el tamaño final en bytes.
    return output_path.stat().st_size


# Extraemos el hash SHA256 esperado desde el contenido del archivo CHECKSUM.
def parse_checksum(checksum_text: str) -> str:
    # Tomamos el primer token porque el formato típico es: hash  filename.
    first_token = checksum_text.split()[0]
    # Validamos que el token tenga 64 caracteres hexadecimales.
    if not re.fullmatch(r"[a-fA-F0-9]{64}", first_token):
        # Lanzamos error si el CHECKSUM no parece SHA256.
        raise ValueError(f"CHECKSUM inválido: {checksum_text}")
    # Regresamos el hash en minúsculas para comparación estable.
    return first_token.lower()


# Calculamos el SHA256 real de un archivo local.
def sha256_file(path: Path) -> str:
    # Creamos un objeto hash SHA256 incremental.
    digest = hashlib.sha256()
    # Abrimos el archivo en modo binario de lectura.
    with path.open("rb") as file_handle:
        # Leemos bloques de 1 MB para no cargar todo en memoria.
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            # Actualizamos el hash con cada bloque leído.
            digest.update(block)
    # Regresamos el hash final hexadecimal.
    return digest.hexdigest().lower()


# Inicializamos el log CSV de descargas si no existe.
def ensure_download_log(log_path: Path) -> None:
    # Revisamos si el archivo ya existe para no duplicar encabezados.
    if log_path.exists():
        # Si existe, no hacemos nada.
        return
    # Abrimos el CSV en modo escritura con newline controlado.
    with log_path.open("w", newline="", encoding="utf-8") as file_handle:
        # Creamos un escritor CSV con encabezados usando la constante central.
        writer = csv.DictWriter(file_handle, fieldnames=DOWNLOAD_LOG_FIELDNAMES)
        # Escribimos la fila de encabezados.
        writer.writeheader()


# Agregamos una fila al log CSV de descargas.
def append_download_log(log_path: Path, row: Dict[str, object]) -> None:
    # Inicializamos el log si todavía no existe.
    ensure_download_log(log_path)
    # Abrimos el CSV en modo append para conservar historial.
    with log_path.open("a", newline="", encoding="utf-8") as file_handle:
        # Usamos la misma constante DOWNLOAD_LOG_FIELDNAMES para evitar desincronización.
        writer = csv.DictWriter(file_handle, fieldnames=DOWNLOAD_LOG_FIELDNAMES)
        # Escribimos la fila recibida.
        writer.writerow(row)


# Descargamos y verificamos un mes específico.
def download_and_verify_month(config: PipelineConfig, month: str, paths: Dict[str, Path]) -> Optional[Path]:
    # Construimos la URL del ZIP mensual.
    zip_url = build_zip_url(config.symbol, config.interval, month)
    # Construimos la URL del CHECKSUM correspondiente.
    checksum_url = build_checksum_url(config.symbol, config.interval, month)
    # Extraemos el nombre del archivo desde la URL.
    file_name = f"{config.symbol}-{config.interval}-{month}.zip"
    # Definimos la ruta local donde quedará el ZIP raw.
    local_zip_path = paths["raw_dir"] / file_name
    # Creamos una fila base para el log de auditoría.
    log_row = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": config.symbol,
        "interval": config.interval,
        "month": month,
        "file_name": file_name,
        "zip_url": zip_url,
        "checksum_url": checksum_url,
        "size_bytes": "",
        "expected_sha256": "",
        "actual_sha256": "",
        "status": "",
        "message": "",
    }
    # Intentamos descargar primero el CHECKSUM porque define la integridad esperada.
    try:
        # Descargamos el texto del CHECKSUM.
        checksum_text = download_text(checksum_url, config.timeout_seconds)
        # Parseamos el SHA256 esperado.
        expected_sha256 = parse_checksum(checksum_text)
        # Guardamos el checksum esperado en el log.
        log_row["expected_sha256"] = expected_sha256
    except Exception as exc:
        # Marcamos el fallo como CHECKSUM_MISSING_OR_INVALID.
        log_row["status"] = "CHECKSUM_MISSING_OR_INVALID"
        # Guardamos el mensaje técnico del error.
        log_row["message"] = str(exc)
        # Escribimos el fallo en el log.
        append_download_log(paths["download_log"], log_row)
        # Reportamos en consola el problema.
        logging.warning("No se pudo obtener CHECKSUM para %s: %s", file_name, exc)
        # Regresamos None porque no usaremos archivos sin checksum.
        return None
    # Si el archivo ya existe y overwrite=False, evitamos descargar de nuevo.
    if local_zip_path.exists() and not config.overwrite:
        # Calculamos el hash del archivo ya existente.
        actual_sha256 = sha256_file(local_zip_path)
        # Guardamos el tamaño del archivo existente.
        log_row["size_bytes"] = local_zip_path.stat().st_size
        # Guardamos el SHA256 calculado.
        log_row["actual_sha256"] = actual_sha256
        # Comparamos el hash real contra el esperado.
        if actual_sha256 == expected_sha256:
            # Marcamos que se reutilizó un archivo válido.
            log_row["status"] = "REUSED_VERIFIED"
            # Guardamos mensaje legible.
            log_row["message"] = "Archivo local existente verificado contra CHECKSUM."
            # Escribimos la fila en el log.
            append_download_log(paths["download_log"], log_row)
            # Regresamos la ruta del ZIP válido.
            return local_zip_path
        # Si el hash no coincide, forzamos redescarga.
        logging.warning("Hash local no coincide para %s. Se redescargará.", file_name)
    # Intentamos descargar el ZIP mensual.
    try:
        # Descargamos el ZIP al disco.
        size_bytes = download_binary_file(zip_url, local_zip_path, config.timeout_seconds)
        # Calculamos el SHA256 real del ZIP descargado.
        actual_sha256 = sha256_file(local_zip_path)
        # Guardamos el tamaño descargado en el log.
        log_row["size_bytes"] = size_bytes
        # Guardamos el hash real en el log.
        log_row["actual_sha256"] = actual_sha256
        # Verificamos integridad estricta.
        if actual_sha256 != expected_sha256:
            # Marcamos estado de fallo si el hash no coincide.
            log_row["status"] = "CHECKSUM_MISMATCH"
            # Guardamos detalle de auditoría.
            log_row["message"] = "El SHA256 local no coincide con el CHECKSUM remoto."
            # Escribimos el fallo en log.
            append_download_log(paths["download_log"], log_row)
            # Eliminamos el archivo inválido para evitar contaminar el Data Lake.
            local_zip_path.unlink(missing_ok=True)
            # Regresamos None porque no se permite procesar datos no verificados.
            return None
        # Marcamos descarga verificada.
        log_row["status"] = "DOWNLOADED_VERIFIED"
        # Guardamos mensaje legible.
        log_row["message"] = "Descarga correcta y SHA256 verificado."
        # Escribimos la fila de éxito en log.
        append_download_log(paths["download_log"], log_row)
        # Reportamos en consola.
        logging.info("Verificado: %s", file_name)
        # Regresamos la ruta local válida.
        return local_zip_path
    except Exception as exc:
        # Marcamos cualquier fallo de descarga.
        log_row["status"] = "DOWNLOAD_FAILED"
        # Guardamos el mensaje técnico.
        log_row["message"] = str(exc)
        # Escribimos la fila de fallo en log.
        append_download_log(paths["download_log"], log_row)
        # Reportamos en consola.
        logging.warning("No se pudo descargar %s: %s", file_name, exc)
        # Eliminamos archivo parcial si existe.
        local_zip_path.unlink(missing_ok=True)
        # Regresamos None porque no hay ZIP usable.
        return None


# Descargamos y verificamos todos los meses del rango.
def download_all_months(config: PipelineConfig, paths: Dict[str, Path]) -> List[Path]:
    # Generamos la lista explícita de meses a procesar.
    months = month_range(config.start_month, config.end_month)
    # Creamos una lista para guardar solo los ZIP válidos.
    verified_zip_paths = []
    # Iteramos mes por mes para que cada descarga sea auditable.
    for month in months:
        # Descargamos y verificamos el mes actual.
        zip_path = download_and_verify_month(config, month, paths)
        # Si regresó una ruta, significa que el archivo pasó integridad.
        if zip_path is not None:
            # Agregamos el ZIP verificado a la lista final.
            verified_zip_paths.append(zip_path)
    # Regresamos únicamente archivos verificados.
    return verified_zip_paths


# Convertimos timestamps mixtos de Binance a UTC.
def convert_binance_timestamp_to_utc(series: pd.Series) -> pd.Series:
    # Convertimos la serie a numérico para detectar milisegundos o microsegundos por magnitud.
    numeric = pd.to_numeric(series, errors="coerce")
    # Binance Spot usa microsegundos desde 2025-01-01, por eso detectamos valores >= 1e15 como microsegundos.
    is_microseconds = numeric.abs() >= 1_000_000_000_000_000
    # Convertimos valores en milisegundos a microsegundos multiplicando por 1000.
    as_microseconds = np.where(is_microseconds, numeric, numeric * 1000)
    # Convertimos el arreglo final a datetime UTC.
    return pd.to_datetime(as_microseconds, unit="us", utc=True, errors="coerce")


# Leemos un ZIP de klines y devolvemos un DataFrame normalizado a nivel raw-clean.
def read_kline_zip(zip_path: Path, symbol: str, interval: str) -> pd.DataFrame:
    # Abrimos el ZIP local verificado.
    with zipfile.ZipFile(zip_path, "r") as archive:
        # Buscamos archivos CSV dentro del ZIP.
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        # Validamos que el ZIP tenga al menos un CSV.
        if not csv_names:
            # Lanzamos error si el archivo no contiene datos tabulares.
            raise ValueError(f"El ZIP no contiene CSV: {zip_path}")
        # Elegimos el primer CSV; los ZIP mensuales de klines normalmente contienen uno.
        csv_name = csv_names[0]
        # Abrimos el CSV interno como archivo binario.
        with archive.open(csv_name) as csv_file:
            # Leemos sin encabezado y asignamos los nombres oficiales de columnas.
            df = pd.read_csv(csv_file, header=None, names=KLINE_COLUMNS, dtype=str)
    # Agregamos el símbolo como columna explícita para facilitar particionado posterior.
    df["symbol"] = symbol
    # Agregamos el intervalo como columna explícita para trazabilidad.
    df["interval"] = interval
    # Agregamos el nombre del ZIP fuente para auditoría fila por fila.
    df["source_file"] = zip_path.name
    # Regresamos el DataFrame leído.
    return df


# Estandarizamos tipos, nombres y timestamps.
def standardize_klines(df: pd.DataFrame) -> pd.DataFrame:
    # Copiamos el DataFrame para no modificar el objeto original accidentalmente.
    clean = df.copy()
    # Convertimos columnas numéricas con errores como NaN para poder detectar corrupción.
    for column in NUMERIC_COLUMNS:
        # Aplicamos conversión numérica robusta por columna.
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    # Convertimos open_time a datetime UTC con detección ms/us.
    clean["open_time_utc"] = convert_binance_timestamp_to_utc(clean["open_time"])
    # Convertimos close_time a datetime UTC con detección ms/us.
    clean["close_time_utc"] = convert_binance_timestamp_to_utc(clean["close_time"])
    # Convertimos number_of_trades a entero nullable para conservar NaN si hay corrupción.
    clean["number_of_trades"] = clean["number_of_trades"].astype("Int64")
    # Ordenamos por tiempo de apertura para análisis temporal correcto.
    clean = clean.sort_values(["open_time_utc", "source_file"]).reset_index(drop=True)
    # Reordenamos columnas para que las columnas auditables queden al final.
    ordered_columns = [
        "symbol",
        "interval",
        "open_time_utc",
        "close_time_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
        "open_time",
        "close_time",
        "ignore",
        "source_file",
    ]
    # Regresamos el DataFrame con columnas estándar.
    return clean[ordered_columns]


# Unificamos todos los ZIP verificados en un solo DataFrame estandarizado.
def unify_verified_zips(zip_paths: List[Path], symbol: str, interval: str) -> pd.DataFrame:
    # Creamos una lista para acumular DataFrames mensuales.
    frames = []
    # Iteramos cada ZIP verificado.
    for zip_path in zip_paths:
        # Informamos qué archivo se está leyendo.
        logging.info("Leyendo: %s", zip_path.name)
        # Leemos el ZIP mensual como DataFrame.
        monthly_df = read_kline_zip(zip_path, symbol, interval)
        # Agregamos el DataFrame mensual a la lista.
        frames.append(monthly_df)
    # Validamos que haya al menos un DataFrame.
    if not frames:
        # Lanzamos error si no hubo datos verificados.
        raise ValueError("No hay ZIPs verificados para unificar.")
    # Concatenamos todos los meses en un solo DataFrame raw.
    unified_raw = pd.concat(frames, ignore_index=True)
    # Estandarizamos tipos, columnas y timestamps.
    return standardize_klines(unified_raw)


# Calculamos un resumen de huecos temporales.
def detect_temporal_gaps(df: pd.DataFrame, interval: str) -> Dict[str, object]:
    # Si el intervalo no tiene frecuencia fija simple, reportamos que no se evalúa automáticamente.
    if interval not in PANDAS_FREQ_BY_INTERVAL:
        # Regresamos reporte explícito para no fingir validación.
        return {
            "evaluated": False,
            "reason": f"El intervalo {interval} no tiene frecuencia fija simple en este script.",
            "missing_count": None,
            "first_missing_timestamps": [],
        }
    # Eliminamos timestamps nulos para construir el rango esperado.
    valid_times = df["open_time_utc"].dropna().drop_duplicates().sort_values()
    # Si no hay timestamps válidos, no se puede evaluar continuidad.
    if valid_times.empty:
        # Regresamos reporte explícito.
        return {
            "evaluated": False,
            "reason": "No hay open_time_utc válidos.",
            "missing_count": None,
            "first_missing_timestamps": [],
        }
    # Tomamos el primer timestamp observado.
    start = valid_times.iloc[0]
    # Tomamos el último timestamp observado.
    end = valid_times.iloc[-1]
    # Construimos el índice esperado con la frecuencia del intervalo.
    expected_index = pd.date_range(start=start, end=end, freq=PANDAS_FREQ_BY_INTERVAL[interval], tz="UTC")
    # Construimos un índice real con timestamps únicos.
    actual_index = pd.DatetimeIndex(valid_times)
    # Calculamos los timestamps esperados que no aparecen en los datos.
    missing = expected_index.difference(actual_index)
    # Regresamos conteo y primeras muestras para reporte legible.
    return {
        "evaluated": True,
        "expected_candles_between_min_max": int(len(expected_index)),
        "actual_unique_candles": int(len(actual_index)),
        "missing_count": int(len(missing)),
        "first_missing_timestamps": [ts.isoformat() for ts in missing[:50]],
    }


# Ejecutamos controles de calidad sobre el DataFrame final.
def run_quality_checks(df: pd.DataFrame, interval: str) -> Dict[str, object]:
    # Creamos un diccionario para guardar máscaras booleanas por regla.
    masks = {}
    # Detectamos filas con timestamps o OHLCV principales no numéricos o no convertibles.
    masks["corrupt_rows_required_fields_null"] = df[["open_time_utc", "open", "high", "low", "close", "volume"]].isna().any(axis=1)
    # Detectamos duplicados por open_time_utc, porque una vela debe identificarse por su apertura.
    masks["duplicated_open_time"] = df["open_time_utc"].duplicated(keep=False) & df["open_time_utc"].notna()
    # Detectamos open fuera del rango high/low.
    masks["open_outside_high_low"] = (df["open"] > df["high"]) | (df["open"] < df["low"])
    # Detectamos close fuera del rango high/low como parte de velas corruptas.
    masks["close_outside_high_low"] = (df["close"] > df["high"]) | (df["close"] < df["low"])
    # Detectamos high menor que open o close, regla solicitada explícitamente.
    masks["high_less_than_open_or_close"] = (df["high"] < df["open"]) | (df["high"] < df["close"])
    # Detectamos low mayor que open o close como complemento lógico de vela corrupta.
    masks["low_greater_than_open_or_close"] = (df["low"] > df["open"]) | (df["low"] > df["close"])
    # Detectamos high menor que low, condición imposible en una vela válida.
    masks["high_less_than_low"] = df["high"] < df["low"]
    # Detectamos volúmenes negativos en todas las columnas de volumen disponibles.
    masks["negative_volume"] = df[VOLUME_COLUMNS].lt(0).any(axis=1)
    # Detectamos precios cero o negativos; aunque el usuario pidió cero, <=0 es más seguro para mercado real.
    masks["zero_or_negative_prices"] = df[PRICE_COLUMNS].le(0).any(axis=1)
    # Detectamos número de trades negativo, que no tiene sentido operativo.
    masks["negative_number_of_trades"] = df["number_of_trades"].lt(0).fillna(False)
    # Detectamos close_time menor que open_time, condición temporal inválida.
    masks["close_time_before_open_time"] = df["close_time_utc"] < df["open_time_utc"]
    # Generamos el reporte de huecos temporales.
    temporal_gaps = detect_temporal_gaps(df, interval)
    # Calculamos conteos por regla.
    rule_counts = {rule: int(mask.sum()) for rule, mask in masks.items()}
    # Combinamos todas las máscaras para identificar filas con cualquier problema.
    any_issue_mask = np.logical_or.reduce([mask.fillna(False).to_numpy() for mask in masks.values()])
    # Extraemos una muestra pequeña de filas problemáticas para auditoría humana.
    sample_issue_rows = df.loc[any_issue_mask, ["open_time_utc", "open", "high", "low", "close", "volume", "source_file"]].head(100)
    # Construimos el reporte completo.
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": int(len(df)),
        "min_open_time_utc": None if df["open_time_utc"].dropna().empty else df["open_time_utc"].min().isoformat(),
        "max_open_time_utc": None if df["open_time_utc"].dropna().empty else df["open_time_utc"].max().isoformat(),
        "quality_rule_counts": rule_counts,
        "temporal_gaps": temporal_gaps,
        "total_rows_with_any_issue": int(any_issue_mask.sum()),
        "sample_issue_rows": json.loads(sample_issue_rows.astype(str).to_json(orient="records")),
    }
    # Regresamos el reporte de calidad.
    return report


# Escribimos JSON con indentación estable para auditoría humana.
def write_json(path: Path, payload: Dict[str, object]) -> None:
    # Abrimos el archivo en modo escritura UTF-8.
    with path.open("w", encoding="utf-8") as file_handle:
        # Dump con sort_keys para facilitar diffs entre ejecuciones.
        json.dump(payload, file_handle, ensure_ascii=False, indent=2, sort_keys=True)


# Calculamos SHA256 de un archivo de salida para metadata reproducible.
def optional_file_sha256(path: Path) -> Optional[str]:
    # Revisamos si el archivo existe antes de calcular hash.
    if not path.exists():
        # Si no existe, regresamos None.
        return None
    # Regresamos el SHA256 usando la función común.
    return sha256_file(path)


# Exportamos CSV, Parquet, metadata JSON y reporte de calidad.
def export_outputs(df: pd.DataFrame, report: Dict[str, object], config: PipelineConfig, paths: Dict[str, Path], zip_paths: List[Path]) -> Dict[str, Path]:
    # Definimos un prefijo reproducible para los archivos de salida.
    output_prefix = f"{config.symbol}_{config.interval}_{config.start_month}_to_{config.end_month}"
    # Definimos la ruta del CSV limpio.
    csv_path = paths["processed_dir"] / f"{output_prefix}.csv"
    # Definimos la ruta del Parquet limpio.
    parquet_path = paths["processed_dir"] / f"{output_prefix}.parquet"
    # Definimos la ruta del reporte de calidad.
    report_path = paths["reports_dir"] / f"{output_prefix}_quality_report.json"
    # Definimos la ruta de metadata.
    metadata_path = paths["processed_dir"] / f"{output_prefix}_metadata.json"
    # Exportamos CSV sin índice para que sea interoperable.
    df.to_csv(csv_path, index=False)
    # Exportamos Parquet sin índice para investigación cuantitativa eficiente.
    df.to_parquet(parquet_path, index=False)
    # Escribimos el reporte de calidad en JSON.
    write_json(report_path, report)
    # Construimos metadata de linaje y reproducibilidad.
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "data.binance.vision",
        "market": "spot",
        "dataset": "monthly_klines",
        "symbol": config.symbol,
        "interval": config.interval,
        "start_month": config.start_month,
        "end_month": config.end_month,
        "row_count": int(len(df)),
        "min_open_time_utc": None if df["open_time_utc"].dropna().empty else df["open_time_utc"].min().isoformat(),
        "max_open_time_utc": None if df["open_time_utc"].dropna().empty else df["open_time_utc"].max().isoformat(),
        "raw_zip_files": [path.name for path in zip_paths],
        "download_log": str(paths["download_log"]),
        "quality_report": str(report_path),
        "output_files": {
            "csv": str(csv_path),
            "parquet": str(parquet_path),
        },
        "output_sha256": {
            "csv": optional_file_sha256(csv_path),
            "parquet": optional_file_sha256(parquet_path),
        },
        "timestamp_note": "Binance Spot puede mezclar ms antes de 2025 y us desde 2025; este pipeline detecta la unidad por magnitud.",
    }
    # Escribimos metadata en JSON.
    write_json(metadata_path, metadata)
    # Regresamos rutas principales de salida.
    return {
        "csv": csv_path,
        "parquet": parquet_path,
        "quality_report": report_path,
        "metadata": metadata_path,
    }


# Creamos el parser de argumentos de terminal.
def parse_args() -> argparse.Namespace:
    # Inicializamos el parser con descripción clara.
    parser = argparse.ArgumentParser(description="Pipeline Data Lake histórico de criptomonedas desde Binance Vision.")
    # Parámetro símbolo, default BTCUSDT por requerimiento inicial.
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo Binance, ejemplo BTCUSDT.")
    # Parámetro intervalo, default 1h por requerimiento inicial.
    parser.add_argument("--interval", default="1h", help="Intervalo kline, ejemplo 1h.")
    # Parámetro inicio opcional en formato YYYY-MM.
    parser.add_argument("--start-month", default=None, help="Mes inicial YYYY-MM. Si se omite, usa últimos N años completos.")
    # Parámetro final opcional en formato YYYY-MM.
    parser.add_argument("--end-month", default=None, help="Mes final YYYY-MM. Si se omite, usa últimos N años completos.")
    # Parámetro años para calcular rango automático.
    parser.add_argument("--years", type=int, default=5, help="Años completos a descargar si no pasas start/end.")
    # Parámetro raíz del Data Lake local.
    parser.add_argument("--data-root", default="./crypto_datalake", help="Carpeta raíz local del Data Lake.")
    # Parámetro timeout HTTP.
    parser.add_argument("--timeout-seconds", type=int, default=60, help="Timeout HTTP por descarga.")
    # Bandera para redescargar aunque exista archivo local.
    parser.add_argument("--overwrite", action="store_true", help="Redescarga archivos aunque ya existan localmente.")
    # Regresamos argumentos parseados.
    return parser.parse_args()


# Construimos la configuración final desde argumentos.
def build_config_from_args(args: argparse.Namespace) -> PipelineConfig:
    # Validamos y normalizamos símbolo.
    symbol = validate_symbol(args.symbol)
    # Validamos intervalo.
    interval = validate_interval(args.interval)
    # Si el usuario no indicó start/end, calculamos últimos N meses completos.
    if args.start_month is None and args.end_month is None:
        # Calculamos rango mensual completo para evitar mes en curso incompleto.
        start_month, end_month = default_last_complete_months(args.years)
    # Si el usuario indicó ambos extremos, respetamos exactamente su rango.
    elif args.start_month is not None and args.end_month is not None:
        # Validamos el mes inicial.
        start_month = validate_month(args.start_month)
        # Validamos el mes final.
        end_month = validate_month(args.end_month)
    # Si indicó solo uno, no asumimos el otro.
    else:
        # Lanzamos error porque el rango quedaría ambiguo.
        raise ValueError("Debes pasar ambos: --start-month y --end-month, o ninguno.")
    # Validamos que el rango tenga sentido.
    _ = month_range(start_month, end_month)
    # Creamos la configuración inmutable.
    return PipelineConfig(
        symbol=symbol,
        interval=interval,
        start_month=start_month,
        end_month=end_month,
        data_root=Path(args.data_root),
        timeout_seconds=args.timeout_seconds,
        overwrite=args.overwrite,
    )


# Función principal que orquesta todo el pipeline.
def main() -> int:
    # Activamos logging para la ejecución.
    setup_logging()
    # Parseamos argumentos CLI.
    args = parse_args()
    # Construimos configuración validada.
    config = build_config_from_args(args)
    # Creamos rutas locales del Data Lake.
    paths = build_local_paths(config)
    # Informamos el rango exacto que se procesará.
    logging.info("Pipeline iniciado: %s %s %s -> %s", config.symbol, config.interval, config.start_month, config.end_month)
    # Descargamos y verificamos todos los ZIPs mensuales.
    verified_zip_paths = download_all_months(config, paths)
    # Si ningún ZIP pasó verificación, detenemos el pipeline.
    if not verified_zip_paths:
        # Registramos error claro.
        logging.error("No hubo archivos verificados. No se generarán datasets.")
        # Regresamos código de error.
        return 2
    # Unificamos todos los CSV dentro de los ZIPs verificados.
    df = unify_verified_zips(verified_zip_paths, config.symbol, config.interval)
    # Ejecutamos controles de calidad.
    quality_report = run_quality_checks(df, config.interval)
    # Exportamos datasets y metadatos.
    outputs = export_outputs(df, quality_report, config, paths, verified_zip_paths)
    # Informamos rutas finales al usuario.
    logging.info("CSV exportado: %s", outputs["csv"])
    # Informamos ruta Parquet.
    logging.info("Parquet exportado: %s", outputs["parquet"])
    # Informamos ruta del reporte.
    logging.info("Reporte de calidad: %s", outputs["quality_report"])
    # Informamos ruta de metadata.
    logging.info("Metadata JSON: %s", outputs["metadata"])
    # Si hay problemas de calidad, lo indicamos sin detener automáticamente la investigación.
    if quality_report["total_rows_with_any_issue"] > 0 or quality_report["temporal_gaps"].get("missing_count", 0):
        # Advertimos que el usuario debe revisar el reporte antes de modelar.
        logging.warning("Se detectaron posibles problemas de calidad. Revisa el reporte JSON antes de investigar estrategias.")
    # Regresamos código de éxito.
    return 0


# Ejecutamos main solo si el archivo se corre directamente desde terminal.
if __name__ == "__main__":
    # Convertimos el código de salida de main en código de salida del sistema.
    sys.exit(main())
