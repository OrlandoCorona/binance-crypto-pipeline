"""
rebuild_datalake.py — Reconstruye el data lake de BTCUSDT desde data.binance.vision

Descarga las velas horarias (klines 1h) de BTCUSDT del archivo PUBLICO de Binance,
verifica su integridad (SHA256), las une en un solo DataFrame limpio y lo guarda
como Parquet en la ruta EXACTA que espera tu pipeline:
    crypto_datalake/processed/binance/spot/klines/BTCUSDT/1h/BTCUSDT_1h_<ini>_to_<fin>.parquet

v2: (1) maneja que Binance MEZCLA milisegundos (datos viejos) y microsegundos
        (2025+) en el mismo historico -> decide la unidad POR VALOR.
    (2) cachea los ZIP en crypto_datalake/raw/ -> los re-runs no vuelven a descargar.

No necesita cuenta ni API key. Corre en TU maquina, dentro de la raiz del proyecto.

Uso:
    python rebuild_datalake.py
    python rebuild_datalake.py --symbol BTCUSDT --interval 1h --start 2021-06 --end 2026-05

Requisitos:
    pip install requests pandas pyarrow
"""

from __future__ import annotations
import argparse
import hashlib
import io
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/spot"

RAW_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_base", "taker_quote", "ignore",
]

RAW_CACHE: Path | None = None   # se fija en main(): carpeta donde se cachean los ZIP


def month_range(start: str, end: str) -> list[str]:
    """Genera ['2021-06', '2021-07', ...] entre start y end inclusive (formato YYYY-MM)."""
    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")
    out, y, m = [], s.year, s.month
    while (y, m) <= (e.year, e.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def http_get(url: str) -> bytes | None:
    """Descarga una URL. Devuelve None si es 404 (archivo no existe aun)."""
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def get_zip(url: str, cache_name: str) -> bytes | None:
    """Igual que http_get pero con cache local: si el ZIP ya se bajo, lo lee del disco."""
    cache_path = RAW_CACHE / cache_name
    if cache_path.exists():
        return cache_path.read_bytes()
    data = http_get(url)
    if data is not None:
        cache_path.write_bytes(data)
    return data


def verify_checksum(zip_bytes: bytes, checksum_text: str) -> bool:
    """Compara el SHA256 del ZIP contra el que publica Binance en el .CHECKSUM."""
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(zip_bytes).hexdigest().lower()
    return expected == actual


def read_kline_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Extrae el CSV del ZIP. Detecta si trae encabezado (Binance lo agrego en 2025)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            first = f.readline()
        has_header = not first.split(b",")[0].strip().isdigit()
        with z.open(name) as f:
            df = pd.read_csv(
                f,
                header=0 if has_header else None,
                names=None if has_header else RAW_COLS,
            )
    df.columns = RAW_COLS[: len(df.columns)]
    return df


def normalize_timestamp(series: pd.Series) -> pd.Series:
    """
    Binance MEZCLA unidades a lo largo del historico: archivos viejos en
    milisegundos, archivos de 2025+ en microsegundos. Por eso hay que decidir la
    unidad POR VALOR, no una sola vez para toda la serie (ese fue el bug de la v1).

    Regla: un timestamp valido en ms cabe en ~1.6e12; en us cabe en ~1.6e15.
    Cualquier valor >= 1e14 esta en microsegundos -> se pasa a milisegundos.
    """
    s = pd.to_numeric(series, errors="coerce").astype("Int64")
    ms = s.where(s < 100_000_000_000_000, s // 1000)   # >=1e14 (us) -> ms
    return pd.to_datetime(ms, unit="ms", utc=True)


def download_daily_for_month(symbol: str, interval: str, ym: str) -> list[pd.DataFrame]:
    """Fallback: baja los archivos DIARIOS de un mes que aun no esta empaquetado en mensual."""
    frames = []
    y, m = map(int, ym.split("-"))
    d = date(y, m, 1)
    today = datetime.now(timezone.utc).date()
    while d.month == m and d <= today:
        fname = f"{symbol}-{interval}-{d.isoformat()}.zip"
        url = f"{BASE}/daily/klines/{symbol}/{interval}/{fname}"
        zb = get_zip(url, fname)
        if zb is not None:
            frames.append(read_kline_zip(zb))
        d = date.fromordinal(d.toordinal() + 1)
    if frames:
        print(f"  - {ym}: {len(frames)} dias via archivos diarios")
    return frames


def build(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ym in month_range(start, end):
        fname = f"{symbol}-{interval}-{ym}.zip"
        url = f"{BASE}/monthly/klines/{symbol}/{interval}/{fname}"
        zb = get_zip(url, fname)
        if zb is None:
            frames += download_daily_for_month(symbol, interval, ym)
            continue
        chk = http_get(url + ".CHECKSUM")
        if chk and not verify_checksum(zb, chk.decode()):
            print(f"  ! CHECKSUM invalido en {fname} -> se omite")
            continue
        frames.append(read_kline_zip(zb))
        print(f"  - {fname} OK")

    if not frames:
        sys.exit("No se descargo ningun dato. Revisa simbolo/intervalo/fechas.")

    df = pd.concat(frames, ignore_index=True)

    # --- Limpieza / control de calidad ---
    df["open_time_utc"] = normalize_timestamp(df["open_time"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open_time_utc", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="open_time_utc").sort_values("open_time_utc")
    df = df.reset_index(drop=True)
    df["symbol"] = symbol
    df["interval"] = interval

    # Sanidad de fechas: si algo se sale de un rango razonable, avisar y no romper
    yr_min, yr_max = df["open_time_utc"].dt.year.min(), df["open_time_utc"].dt.year.max()
    if yr_min < 2017 or yr_max > datetime.now(timezone.utc).year + 1:
        print(f"  ! Aviso: fechas fuera de rango esperado ({yr_min}-{yr_max}). Revisa unidades.")

    # Chequeo de huecos temporales (velas faltantes)
    expected = pd.date_range(df["open_time_utc"].iloc[0], df["open_time_utc"].iloc[-1], freq="1h")
    faltan = len(expected) - len(df)
    if faltan > 0:
        print(f"  ! Aviso: faltan {faltan} velas ({faltan/len(expected)*100:.3f}%) en el rango.")

    return df[["symbol", "interval", "open_time_utc", "open", "high", "low", "close", "volume"]]


def main():
    global RAW_CACHE
    ap = argparse.ArgumentParser(description="Reconstruye el data lake desde data.binance.vision")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--start", default="2021-06")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    ap.add_argument("--out", default=".", help="Raiz del proyecto (donde vive crypto_datalake/)")
    args = ap.parse_args()

    RAW_CACHE = Path(args.out) / "crypto_datalake" / "raw"
    RAW_CACHE.mkdir(parents=True, exist_ok=True)

    print(f"Descargando {args.symbol} {args.interval} de {args.start} a {args.end} ...")
    print(f"(cache de ZIP en {RAW_CACHE} — los re-runs no vuelven a descargar)")
    df = build(args.symbol, args.interval, args.start, args.end)

    d0, d1 = df["open_time_utc"].min().date(), df["open_time_utc"].max().date()
    outdir = (Path(args.out) / "crypto_datalake" / "processed" / "binance"
              / "spot" / "klines" / args.symbol / args.interval)
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"{args.symbol}_{args.interval}_{d0}_to_{d1}.parquet"
    df.to_parquet(outpath, index=False)

    print("\n== LISTO ==")
    print(f"Filas:   {len(df):,}")
    print(f"Rango:   {d0} -> {d1} (UTC)")
    print(f"Parquet: {outpath}")
    print("\nNOTA de reproducibilidad / holdout:")
    print("  - Para reproducir tus numeros viejos, corta el dataset en 2026-05-31.")
    print("  - Aparta 2026-06-01 en adelante como HOLDOUT intacto: NO lo mires al calibrar.")


if __name__ == "__main__":
    main()