"""
main.py — Punto de entrada del pipeline ETL.

Uso:
    python main.py                       # corre todos los símbolos
    python main.py --symbols BTCUSDT     # corre un solo símbolo
    python main.py --dry-run             # valida sin guardar en PostgreSQL

El pipeline sigue estos pasos para cada símbolo:
    1. Ingesta    → carga el Parquet del data lake
    2. Validación → verifica calidad de datos (7 checks)
    3. Transformación → calcula features (retornos, SMA, régimen)
    4. Carga      → guarda en PostgreSQL

Los errores de un símbolo no detienen el pipeline completo.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Asegurar que el directorio raíz esté en el path de Python
sys.path.insert(0, str(Path(__file__).parent))

from src.config import SYMBOLS, LOGS_DIR, EXPORTS_DIR, LOG_LEVEL
from src.utils import get_logger, ensure_dirs, Timer
from src.ingestion import load_all_symbols
from src.validation import validate_ohlcv, run_quality_report
from src.transform import transform
from src.database import save_klines, save_quality_results

log = get_logger("main", log_dir=LOGS_DIR, level=LOG_LEVEL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Binance Crypto Data Lake — ETL Pipeline"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Símbolos a procesar. Ej: --symbols BTCUSDT ETHUSDT. Default: todos.",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        help="Intervalo de tiempo. Default: 1h",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida y transforma sin guardar en PostgreSQL.",
    )
    parser.add_argument(
        "--quality-report",
        action="store_true",
        help="Imprime el reporte de calidad de datos en consola.",
    )
    return parser.parse_args()


def run_pipeline(
    symbols: list[str] = None,
    interval: str = "1h",
    dry_run: bool = False,
    quality_report: bool = False,
) -> dict:
    """
    Ejecuta el pipeline ETL completo.

    Args:
        symbols:        Lista de símbolos a procesar.
        interval:       Intervalo de tiempo.
        dry_run:        Si True, no guarda en PostgreSQL.
        quality_report: Si True, imprime reporte de calidad en consola.

    Returns:
        Dict con el resumen de ejecución:
        {
            'processed': ['BTCUSDT', 'ETHUSDT'],
            'failed':    ['SOLUSDT'],
            'total_rows': 43817,
        }
    """
    if symbols is None:
        symbols = SYMBOLS

    # Crear directorios necesarios
    ensure_dirs(LOGS_DIR, EXPORTS_DIR)

    summary = {"processed": [], "failed": [], "total_rows": 0}

    log.info("=" * 60)
    log.info("Pipeline started | symbols=%s | interval=%s | dry_run=%s",
             symbols, interval, dry_run)
    log.info("=" * 60)

    # ── STEP 1: INGESTA ───────────────────────────────────────────────────
    all_data = load_all_symbols(symbols, interval)

    if not all_data:
        log.error("No data loaded. Check that crypto_datalake/processed/ contains Parquet files.")
        return summary

    # ── STEPS 2-4: PROCESAR CADA SÍMBOLO ─────────────────────────────────
    for symbol, df_raw in all_data.items():
        log.info("-" * 40)
        log.info("Processing: %s", symbol)

        try:
            with Timer(f"pipeline {symbol}") as t:

                # STEP 2: Validación
                results = validate_ohlcv(df_raw)
                fails = [r for r in results if r["severity"] == "FAIL"]

                if quality_report:
                    run_quality_report(df_raw)

                if fails:
                    log.warning(
                        "%s has %d FAIL checks — continuing anyway (check data_quality table)",
                        symbol, len(fails)
                    )

                # STEP 3: Transformación
                df_enriched = transform(df_raw)

                # STEP 4: Carga a PostgreSQL (si no es dry-run)
                if not dry_run:
                    save_klines(df_enriched)
                    save_quality_results(results, symbol=symbol, interval=interval)
                else:
                    log.info("DRY RUN — skipping PostgreSQL write for %s", symbol)

            log.info("Done: %s | %d rows | %s", symbol, len(df_enriched), t.elapsed_str)
            summary["processed"].append(symbol)
            summary["total_rows"] += len(df_enriched)

        except Exception as e:
            log.error("FAILED processing %s: %s", symbol, e, exc_info=True)
            summary["failed"].append(symbol)

    # ── RESUMEN FINAL ─────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info(
        "Pipeline complete | processed=%s | failed=%s | total_rows=%d",
        summary["processed"], summary["failed"], summary["total_rows"]
    )
    log.info("=" * 60)

    return summary


if __name__ == "__main__":
    args = parse_args()
    result = run_pipeline(
        symbols=args.symbols,
        interval=args.interval,
        dry_run=args.dry_run,
        quality_report=args.quality_report,
    )
    # Código de salida: 0 si todo OK, 1 si algún símbolo falló
    sys.exit(0 if not result["failed"] else 1)
