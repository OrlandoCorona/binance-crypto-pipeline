"""
ingestion.py — Carga de datos históricos de Binance.

Responsabilidad: leer los archivos Parquet del data lake y devolver
DataFrames limpios con las columnas correctas. No transforma ni valida.

Funciones principales:
    load_symbol(symbol, interval)   → DataFrame de un símbolo
    load_all_symbols(symbols)       → dict {symbol: DataFrame}
"""

import pandas as pd
from pathlib import Path

from src.config import DATALAKE_DIR, SYMBOLS, INTERVALS, LOG_LEVEL, LOGS_DIR
from src.utils import get_logger, Timer

log = get_logger(__name__, log_dir=LOGS_DIR, level=LOG_LEVEL)

# Columnas mínimas que debe tener cualquier archivo Parquet cargado
REQUIRED_COLUMNS = [
    "symbol", "interval", "open_time_utc",
    "open", "high", "low", "close", "volume",
]


def find_parquet(symbol: str, interval: str = "1h") -> Path | None:
    """
    Busca el archivo Parquet de un símbolo en el data lake.

    El data lake tiene la estructura:
        crypto_datalake/processed/binance/spot/klines/{SYMBOL}/{INTERVAL}/
        {SYMBOL}_{INTERVAL}_{start}_to_{end}.parquet

    Returns:
        Path al archivo si existe, None si no se encuentra.
    """
    search_dir = DATALAKE_DIR / "processed" / "binance" / "spot" / "klines" / symbol / interval
    if not search_dir.exists():
        return None

    parquets = sorted(search_dir.glob("*.parquet"))
    if not parquets:
        return None

    # Si hay más de uno, usar el más reciente (mayor rango de fechas)
    return parquets[-1]


def load_symbol(symbol: str, interval: str = "1h") -> pd.DataFrame:
    """
    Carga el archivo Parquet de un símbolo y devuelve un DataFrame limpio.

    Args:
        symbol:   Símbolo de Binance, ej. 'BTCUSDT'
        interval: Intervalo de tiempo, ej. '1h'

    Returns:
        DataFrame con las columnas de REQUIRED_COLUMNS.
        Vacío si el archivo no existe o tiene un error.

    Example:
        df = load_symbol('BTCUSDT')
        print(df.shape)  # (43817, 17)
    """
    path = find_parquet(symbol, interval)

    if path is None:
        log.warning(
            "Parquet not found for %s %s. "
            "Expected path: %s",
            symbol, interval,
            DATALAKE_DIR / "processed" / "binance" / "spot" / "klines" / symbol / interval,
        )
        return pd.DataFrame()

    try:
        with Timer(f"load {symbol}") as t:
            df = pd.read_parquet(path)

        # Verificar columnas mínimas
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            log.error(
                "Missing required columns in %s: %s",
                path.name, missing
            )
            return pd.DataFrame()

        # Asegurar que open_time_utc sea timezone-aware
        if df["open_time_utc"].dt.tz is None:
            df["open_time_utc"] = df["open_time_utc"].dt.tz_localize("UTC")

        log.info(
            "Loaded %s %s: %d rows | %s → %s | elapsed: %s",
            symbol, interval,
            len(df),
            df["open_time_utc"].min().date(),
            df["open_time_utc"].max().date(),
            t.elapsed_str,
        )
        return df

    except Exception as e:
        log.error("Failed to load %s %s: %s", symbol, interval, e)
        return pd.DataFrame()


def load_all_symbols(
    symbols: list[str] = None,
    interval: str = "1h",
) -> dict[str, pd.DataFrame]:
    """
    Carga múltiples símbolos y devuelve un diccionario {symbol: DataFrame}.

    Símbolos que no se encuentran o tienen errores se omiten con un WARNING
    en lugar de detener todo el pipeline.

    Args:
        symbols:  Lista de símbolos. Si es None, usa config.SYMBOLS.
        interval: Intervalo de tiempo.

    Returns:
        Dict con los DataFrames cargados correctamente.

    Example:
        data = load_all_symbols(['BTCUSDT', 'ETHUSDT'])
        for symbol, df in data.items():
            print(symbol, df.shape)
    """
    if symbols is None:
        symbols = SYMBOLS

    result = {}
    log.info("Loading %d symbols: %s", len(symbols), symbols)

    for symbol in symbols:
        df = load_symbol(symbol, interval)
        if df.empty:
            log.warning("Skipping %s — no data loaded", symbol)
            continue
        result[symbol] = df

    log.info(
        "Loaded %d / %d symbols successfully",
        len(result), len(symbols)
    )
    return result
