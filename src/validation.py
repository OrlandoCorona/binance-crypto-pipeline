"""
validation.py — Validación de calidad de datos OHLCV.

Responsabilidad: recibir un DataFrame y devolver una lista de resultados
de validación. No modifica los datos, solo los inspecciona.

Funciones principales:
    validate_ohlcv(df)          → list de resultados por check
    run_quality_report(df)      → imprime resumen en consola
"""

import pandas as pd
import numpy as np
from typing import Any

from src.config import OUTLIER_SIGMA, LOG_LEVEL, LOGS_DIR
from src.utils import get_logger

log = get_logger(__name__, log_dir=LOGS_DIR, level=LOG_LEVEL)

# Tipo de retorno de cada check individual
CheckResult = dict[str, Any]
# {check_name, severity, total_rows, passed_rows, failed_rows, detail}


def _make_result(
    check_name: str,
    total: int,
    failed: int,
    detail: str = "",
    warn_threshold: float = 0.001,   # >0.1% de filas fallidas → WARN
    fail_threshold: float = 0.01,    # >1.0% de filas fallidas → FAIL
) -> CheckResult:
    """Construye un resultado de validación estándar."""
    passed = total - failed
    if failed == 0:
        severity = "PASS"
    elif failed / total >= fail_threshold:
        severity = "FAIL"
    else:
        severity = "WARN"

    return {
        "check_name":  check_name,
        "severity":    severity,
        "total_rows":  total,
        "passed_rows": passed,
        "failed_rows": failed,
        "detail":      detail,
    }


# ── Checks individuales ────────────────────────────────────────────────────

def check_nulls(df: pd.DataFrame) -> CheckResult:
    """Detecta valores nulos en columnas OHLCV críticas."""
    price_cols = ["open", "high", "low", "close", "volume", "open_time_utc"]
    null_mask = df[price_cols].isnull().any(axis=1)
    failed = int(null_mask.sum())
    detail = f"Null values in: {df[price_cols].isnull().sum().to_dict()}" if failed else ""
    return _make_result("NULL_CHECK", len(df), failed, detail)


def check_ohlc_integrity(df: pd.DataFrame) -> CheckResult:
    """Verifica que high >= max(open, close) y low <= min(open, close)."""
    bad = (
        (df["high"] < df[["open", "close"]].max(axis=1)) |
        (df["low"]  > df[["open", "close"]].min(axis=1)) |
        (df["high"] < df["low"])
    )
    failed = int(bad.sum())
    return _make_result("OHLC_VALIDITY", len(df), failed,
                        f"{failed} bars violate high >= body >= low")


def check_negative_prices(df: pd.DataFrame) -> CheckResult:
    """Detecta precios o volumen negativos o iguales a cero."""
    bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    failed = int(bad.sum())
    return _make_result("NEGATIVE_PRICES", len(df), failed,
                        f"{failed} bars with price <= 0", fail_threshold=0.0001)


def check_negative_volume(df: pd.DataFrame) -> CheckResult:
    """Detecta volumen negativo."""
    bad = df["volume"] < 0
    failed = int(bad.sum())
    return _make_result("NEGATIVE_VOLUME", len(df), failed,
                        f"{failed} bars with volume < 0", fail_threshold=0.0001)


def check_duplicates(df: pd.DataFrame) -> CheckResult:
    """Detecta timestamps duplicados (misma barra dos veces)."""
    dups = df.duplicated(subset=["open_time_utc"])
    failed = int(dups.sum())
    return _make_result("DUPLICATE_BARS", len(df), failed,
                        f"{failed} duplicate open_time_utc values", fail_threshold=0.0001)


def check_temporal_order(df: pd.DataFrame) -> CheckResult:
    """Verifica que las barras estén en orden cronológico ascendente."""
    timestamps = df["open_time_utc"]
    out_of_order = int((timestamps.diff().dropna() < pd.Timedelta(0)).sum())
    return _make_result("TEMPORAL_ORDER", len(df), out_of_order,
                        f"{out_of_order} bars out of chronological order")


def check_return_outliers(df: pd.DataFrame, sigma: float = OUTLIER_SIGMA) -> CheckResult:
    """Marca retornos extremos (> sigma desviaciones estándar) como outliers."""
    if "open_to_open_return" not in df.columns:
        log.debug("open_to_open_return not present — skipping outlier check")
        return _make_result("RETURN_OUTLIER", len(df), 0, "Column not available — skipped")

    returns = df["open_to_open_return"].dropna()
    if len(returns) < 10:
        return _make_result("RETURN_OUTLIER", len(df), 0, "Not enough data")

    mean = returns.mean()
    std  = returns.std()
    outliers = (returns - mean).abs() > sigma * std
    failed = int(outliers.sum())
    return _make_result("RETURN_OUTLIER", len(df), failed,
                        f"{failed} returns beyond {sigma}σ (mean={mean:.4f}, std={std:.4f})",
                        warn_threshold=0.0, fail_threshold=0.02)


# ── Función principal ──────────────────────────────────────────────────────

def validate_ohlcv(df: pd.DataFrame) -> list[CheckResult]:
    """
    Corre todos los checks de calidad en un DataFrame OHLCV.

    Args:
        df: DataFrame con columnas open, high, low, close, volume, open_time_utc.

    Returns:
        Lista de dicts con el resultado de cada check.

    Example:
        results = validate_ohlcv(df)
        fails = [r for r in results if r['severity'] == 'FAIL']
        print(fails)
    """
    if df.empty:
        log.warning("validate_ohlcv received empty DataFrame")
        return []

    symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "unknown"
    log.info("Running validation on %s (%d rows)", symbol, len(df))

    checks = [
        check_nulls(df),
        check_ohlc_integrity(df),
        check_negative_prices(df),
        check_negative_volume(df),
        check_duplicates(df),
        check_temporal_order(df),
        check_return_outliers(df),
    ]

    # Log un resumen rápido
    for r in checks:
        level = {"PASS": "info", "WARN": "warning", "FAIL": "error"}[r["severity"]]
        getattr(log, level)(
            "[%s] %s — %d/%d passed",
            r["severity"], r["check_name"], r["passed_rows"], r["total_rows"]
        )

    return checks


def run_quality_report(df: pd.DataFrame) -> None:
    """
    Imprime un resumen de calidad de datos en consola.
    Útil para debugging manual o ejecución desde un notebook.

    Example:
        run_quality_report(df)
    """
    results = validate_ohlcv(df)
    symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "DataFrame"

    print(f"\n{'='*55}")
    print(f"  Data Quality Report — {symbol} ({len(df)} rows)")
    print(f"{'='*55}")
    print(f"  {'Check':<25} {'Status':<8} {'Failed':>8} {'Pass%':>8}")
    print(f"  {'-'*51}")

    for r in results:
        pass_pct = 100 * r["passed_rows"] / r["total_rows"] if r["total_rows"] else 0
        icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[r["severity"]]
        print(f"  {icon} {r['check_name']:<23} {r['severity']:<8} {r['failed_rows']:>8} {pass_pct:>7.2f}%")
        if r["detail"] and r["severity"] != "PASS":
            print(f"    ↳ {r['detail']}")

    total_fails = sum(r["failed_rows"] for r in results)
    print(f"{'='*55}")
    print(f"  Total issues found: {total_fails}")
    print(f"{'='*55}\n")
