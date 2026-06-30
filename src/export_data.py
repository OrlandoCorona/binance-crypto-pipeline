"""
export_data.py — Genera archivos CSV listos para conectar con Power BI.

Ejecutar cada vez que quieras refrescar los datos del dashboard:
    python src/export_data.py

Los archivos se guardan en data/exports/ y pueden abrirse directamente
en Power BI sin necesidad de PostgreSQL (útil para demos y desarrollo).

Archivos generados:
    strategy_kpi.csv    — KPIs por estrategia y activo (8 filas)
    weekday_returns.csv — retorno promedio por día de la semana (7 filas)
    regime_heatmap.csv  — retornos por día × régimen (21 filas)
    equity_curves.csv   — curva de equity de las estrategias
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import glob as _glob

# Asegurar que el directorio raíz esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import DATALAKE_DIR, EXPORTS_DIR, SYMBOLS, LOGS_DIR
from src.utils import get_logger, ensure_dirs, Timer

log = get_logger(__name__, log_dir=LOGS_DIR)


def export_strategy_kpi() -> pd.DataFrame:
    """Exporta KPIs de backtesting por estrategia y activo."""
    src_file = DATALAKE_DIR / "research" / "paper_trading" / "multi_asset" / "multi_asset_results.csv"

    if not src_file.exists():
        log.error("multi_asset_results.csv not found at %s", src_file)
        return pd.DataFrame()

    df = pd.read_csv(src_file)

    # Formatear como porcentajes para legibilidad en Power BI
    df["total_return_pct"]  = (df["total_return"] * 100).round(2)
    df["bh_return_pct"]     = (df["bh_return"]    * 100).round(2)
    df["excess_return_pct"] = (df["excess"]        * 100).round(2)
    df["max_drawdown_pct"]  = (df["max_drawdown"]  * 100).round(2)
    df["exposure_pct"]      = (df["pos_pct"]        * 100).round(1)
    df["beat_bh_pct"]       = (df["beat_pct"]       * 100).round(1)

    # Percentiles Monte Carlo de la validación institucional
    # (calculados en s4_institutional_validation.py con N=5,000 permutaciones)
    MC_PERCENTILES = {
        ("BTCUSDT", "H2_WEDNESDAY_LONG"): 85.9,
        ("BTCUSDT", "H4_AVOID_THURSDAY"): 99.3,
        ("ETHUSDT", "H2_WEDNESDAY_LONG"): 85.9,
        ("ETHUSDT", "H4_AVOID_THURSDAY"): 99.3,
        ("BNBUSDT", "H2_WEDNESDAY_LONG"): 85.9,
        ("BNBUSDT", "H4_AVOID_THURSDAY"): 99.3,
        ("SOLUSDT", "H2_WEDNESDAY_LONG"): 85.9,
        ("SOLUSDT", "H4_AVOID_THURSDAY"): 99.3,
    }
    df["mc_percentile"] = df.apply(
        lambda r: MC_PERCENTILES.get((r["symbol"], r["strategy"])), axis=1
    )

    out_cols = [
        "symbol", "strategy",
        "total_return_pct", "bh_return_pct", "excess_return_pct",
        "sharpe", "max_drawdown_pct", "win_rate", "profit_factor",
        "exposure_pct", "beat_bh_pct", "mc_percentile",
    ]
    df_out = df[[c for c in out_cols if c in df.columns]]

    out_path = EXPORTS_DIR / "strategy_kpi.csv"
    df_out.to_csv(out_path, index=False)
    log.info("Exported strategy_kpi.csv: %d rows → %s", len(df_out), out_path)
    return df_out


def export_weekday_returns(symbol: str = "BTCUSDT", interval: str = "1h") -> pd.DataFrame:
    """Exporta retorno promedio por día de la semana para el análisis de calendario."""
    parquet_dir = (
        DATALAKE_DIR / "processed" / "binance" / "spot" / "klines" / symbol / interval
    )
    files = sorted(parquet_dir.glob("*.parquet"))
    if not files:
        log.error("No Parquet found for %s %s", symbol, interval)
        return pd.DataFrame()

    df = pd.read_parquet(files[-1])
    df["open_to_open_return"] = df["open"].shift(-1) / df["open"] - 1
    df["next_bar_weekday"]    = df["open_time_utc"].shift(-1).dt.weekday

    day_map = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
               4: "Friday", 5: "Saturday", 6: "Sunday"}
    df["weekday_name"] = df["next_bar_weekday"].map(day_map)

    wd = (
        df.dropna(subset=["open_to_open_return", "next_bar_weekday"])
          .groupby(["next_bar_weekday", "weekday_name"])["open_to_open_return"]
          .agg(
              bar_count  = "count",
              avg_return = "mean",
              std_return = "std",
              min_return = "min",
              max_return = "max",
              pos_rate   = lambda x: (x > 0).mean(),
          )
          .reset_index()
    )
    wd["avg_return_pct"] = (wd["avg_return"] * 100).round(4)
    wd["std_return_pct"] = (wd["std_return"] * 100).round(4)
    wd["sharpe"]         = (wd["avg_return"] / wd["std_return"]).round(4)
    wd["symbol"]         = symbol

    out_path = EXPORTS_DIR / "weekday_returns.csv"
    wd.to_csv(out_path, index=False)
    log.info("Exported weekday_returns.csv: %d rows → %s", len(wd), out_path)
    return wd


def export_regime_heatmap(symbol: str = "BTCUSDT", interval: str = "1h") -> pd.DataFrame:
    """Exporta retornos por día de la semana × régimen de mercado (heatmap)."""
    parquet_dir = (
        DATALAKE_DIR / "processed" / "binance" / "spot" / "klines" / symbol / interval
    )
    files = sorted(parquet_dir.glob("*.parquet"))
    if not files:
        log.error("No Parquet found for %s %s", symbol, interval)
        return pd.DataFrame()

    df = pd.read_parquet(files[-1])
    df["open_to_open_return"] = df["open"].shift(-1) / df["open"] - 1
    df["next_bar_weekday"]    = df["open_time_utc"].shift(-1).dt.weekday

    day_map = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
               4: "Friday", 5: "Saturday", 6: "Sunday"}
    df["weekday_name"] = df["next_bar_weekday"].map(day_map)

    # Régimen mensual
    df["month_period"] = df["open_time_utc"].dt.tz_localize(None).dt.to_period("M")
    monthly = (
        df.groupby("month_period")["close"]
          .agg(first_close="first", last_close="last")
          .assign(monthly_return=lambda x: x["last_close"] / x["first_close"] - 1)
    )
    monthly["regime"] = monthly["monthly_return"].apply(
        lambda r: "Bull" if r >= 0.05 else ("Bear" if r <= -0.05 else "Lateral")
    )
    df["market_regime"] = df["month_period"].map(monthly["regime"])

    hmap = (
        df.dropna(subset=["open_to_open_return", "next_bar_weekday", "market_regime"])
          .groupby(["market_regime", "next_bar_weekday", "weekday_name"])["open_to_open_return"]
          .agg(bar_count="count", avg_return="mean")
          .reset_index()
    )
    hmap["avg_return_pct"] = (hmap["avg_return"] * 100).round(4)
    hmap["symbol"]         = symbol

    out_path = EXPORTS_DIR / "regime_heatmap.csv"
    hmap.to_csv(out_path, index=False)
    log.info("Exported regime_heatmap.csv: %d rows → %s", len(hmap), out_path)
    return hmap


def export_equity_curves() -> pd.DataFrame:
    """Exporta curvas de equity de las estrategias H2 y H4."""
    paper_dir = DATALAKE_DIR / "research" / "paper_trading"
    rows = []

    for symbol in SYMBOLS:
        for strat_code in ["H2_WEDNESDAY_LONG", "H4_AVOID_THURSDAY"]:
            pattern = str(paper_dir / symbol / "1h" / f"*{strat_code}*.csv")
            files = sorted(_glob.glob(pattern))
            if not files:
                continue
            try:
                df_eq = pd.read_csv(files[-1])
                date_col = next((c for c in df_eq.columns
                                 if any(k in c.lower() for k in ("date", "time", "period"))), None)
                eq_col   = next((c for c in df_eq.columns
                                 if any(k in c.lower() for k in ("equity", "cumul", "value", "nav"))), None)
                if date_col and eq_col:
                    for _, row in df_eq[[date_col, eq_col]].iterrows():
                        rows.append({"symbol": symbol, "strategy": strat_code,
                                     "date": row[date_col], "equity": row[eq_col]})
            except Exception as e:
                log.warning("Could not read equity for %s %s: %s", symbol, strat_code, e)

    df_out = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["symbol", "strategy", "date", "equity"]
    )
    out_path = EXPORTS_DIR / "equity_curves.csv"
    df_out.to_csv(out_path, index=False)
    log.info("Exported equity_curves.csv: %d rows → %s", len(df_out), out_path)
    return df_out


def run_all_exports() -> None:
    """Ejecuta todos los exports en secuencia."""
    ensure_dirs(EXPORTS_DIR)

    log.info("=" * 50)
    log.info("Starting Power BI data export")
    log.info("=" * 50)

    with Timer("full export") as t:
        export_strategy_kpi()
        export_weekday_returns()
        export_regime_heatmap()
        export_equity_curves()

    log.info("All exports completed in %s", t.elapsed_str)
    log.info("Files in %s:", EXPORTS_DIR)
    for f in sorted(EXPORTS_DIR.glob("*.csv")):
        log.info("  %s (%s KB)", f.name, f.stat().st_size // 1024 or "<1")


if __name__ == "__main__":
    run_all_exports()
