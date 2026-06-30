"""
Q5 / S1 — Descubrimiento de Patrones Cuantitativos
====================================================

Objetivo
--------
Analizar patrones observables en datos históricos de BTCUSDT sin optimizar
parámetros ni declarar que una estrategia funciona por un solo backtest.

Este script NO genera señales operativas ni recomienda comprar/vender.
Produce evidencia descriptiva y condicional sobre:

1. Comportamiento por hora UTC.
2. Comportamiento por día de semana UTC.
3. Expansión de volatilidad.
4. Contracción de volatilidad.
5. Momentum.
6. Reversión a la media.

Principios anti-sobreajuste
---------------------------
- No hace búsqueda de parámetros.
- Usa ventanas fijas predefinidas.
- Usa umbrales cuantílicos rolling calculados solo con pasado.
- Reporta frecuencia, estadística y evidencia, incluso si no hay ventaja.
- Compara retornos forward condicionales contra la línea base del mercado.

Ejemplo de ejecución PowerShell
-------------------------------
python q5_pattern_discovery.py `
  --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet `
  --output-dir .\crypto_datalake\research\q5_patterns\BTCUSDT\1h `
  --symbol BTCUSDT `
  --interval 1h
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuración general del laboratorio
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternConfig:
    """Parámetros fijos del laboratorio.

    Importante: estos valores no son optimizados por el script. Son reglas
    predefinidas para observar el mercado de forma disciplinada.
    """

    symbol: str
    interval: str
    forward_horizons: Tuple[int, ...] = (1, 6, 24)
    momentum_windows: Tuple[int, ...] = (3, 6, 24)
    rolling_context_window: int = 168
    short_vol_window: int = 24
    long_vol_window: int = 168
    low_quantile: float = 0.20
    high_quantile: float = 0.80
    bootstrap_runs: int = 500
    min_samples_for_evidence: int = 100
    random_seed: int = 42


DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_NAME_ES = {
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo",
}


# ---------------------------------------------------------------------------
# Utilidades de E/S y normalización
# ---------------------------------------------------------------------------


def ensure_dirs(output_dir: Path) -> Dict[str, Path]:
    """Crea la estructura de salida y devuelve sus rutas."""

    tables_dir = output_dir / "tables"
    charts_dir = output_dir / "charts"
    reports_dir = output_dir / "reports"

    for path in (tables_dir, charts_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)

    return {"tables": tables_dir, "charts": charts_dir, "reports": reports_dir}


def read_market_data(input_path: Path) -> pd.DataFrame:
    """Lee un archivo Parquet o CSV según su extensión."""

    suffix = input_path.suffix.lower()

    if suffix == ".parquet":
        return pd.read_parquet(input_path)

    if suffix == ".csv":
        return pd.read_csv(input_path)

    raise ValueError(f"Formato no soportado: {input_path.suffix}. Usa .parquet o .csv")


def collapse_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Colapsa columnas duplicadas tomando el primer valor no nulo por fila.

    Esto protege contra datasets enriquecidos que hayan guardado la misma columna
    más de una vez, por ejemplo `open_time_utc` duplicada.
    """

    if not df.columns.duplicated().any():
        return df.copy()

    result_parts: List[pd.Series] = []

    for col in pd.unique(df.columns):
        block = df.loc[:, df.columns == col]

        if block.shape[1] == 1:
            series = block.iloc[:, 0]
        else:
            series = block.bfill(axis=1).iloc[:, 0]

        series.name = col
        result_parts.append(series)

    return pd.concat(result_parts, axis=1)


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza nombres de columnas y valida campos mínimos.

    El laboratorio requiere timestamp UTC, OHLC y close. Volume mejora el análisis,
    pero si no existe se rellena con NaN para no inventar datos.
    """

    result = df.copy()
    result.columns = [str(c).strip().lower() for c in result.columns]
    result = collapse_duplicate_columns(result)

    rename_map = {
        "open time": "open_time_utc",
        "open_time": "open_time_utc",
        "timestamp": "open_time_utc",
        "datetime": "open_time_utc",
        "date": "open_time_utc",
        "quote asset volume": "quote_asset_volume",
        "number of trades": "number_of_trades",
    }

    result = result.rename(columns={k: v for k, v in rename_map.items() if k in result.columns})
    result = collapse_duplicate_columns(result)

    required = ["open_time_utc", "open", "high", "low", "close"]
    missing = [col for col in required if col not in result.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    if "volume" not in result.columns:
        result["volume"] = np.nan

    result["open_time_utc"] = pd.to_datetime(result["open_time_utc"], utc=True, errors="coerce")

    numeric_cols = ["open", "high", "low", "close", "volume", "quote_asset_volume", "number_of_trades"]
    for col in numeric_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.dropna(subset=["open_time_utc", "open", "high", "low", "close"])
    result = result.sort_values("open_time_utc").drop_duplicates("open_time_utc", keep="last")
    result = result.reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# Feature engineering descriptivo, sin optimización
# ---------------------------------------------------------------------------


def prepare_features(df: pd.DataFrame, config: PatternConfig) -> pd.DataFrame:
    """Crea variables observables y retornos forward para análisis de patrones.

    Todas las variables de condición usan información disponible hasta la vela
    actual o ventanas rolling desplazadas con `.shift(1)` cuando se calculan
    umbrales históricos. Los retornos forward solo se usan para medir qué pasó
    después, no para construir la condición.
    """

    result = df.copy()

    result["simple_return"] = result["close"].pct_change()
    result["log_return"] = np.log(result["close"] / result["close"].shift(1))
    result["abs_log_return"] = result["log_return"].abs()
    result["range_pct"] = (result["high"] - result["low"]) / result["close"].replace(0, np.nan)
    result["body_pct"] = (result["close"] - result["open"]).abs() / result["open"].replace(0, np.nan)

    result["hour_utc"] = result["open_time_utc"].dt.hour
    result["day_of_week"] = result["open_time_utc"].dt.day_name()
    result["day_of_week_num"] = result["open_time_utc"].dt.dayofweek
    result["is_weekend"] = result["day_of_week"].isin(["Saturday", "Sunday"])

    # Retornos forward fijos. No se usan para crear señales, solo para evaluar
    # qué ocurrió después de cada patrón.
    for horizon in config.forward_horizons:
        result[f"fwd_log_return_{horizon}h"] = np.log(result["close"].shift(-horizon) / result["close"])
        result[f"fwd_simple_return_{horizon}h"] = result["close"].shift(-horizon) / result["close"] - 1.0

    # Ventanas de volatilidad observada hasta el pasado inmediato.
    result["rolling_vol_24h_past"] = result["log_return"].shift(1).rolling(config.short_vol_window).std()
    result["rolling_vol_168h_past"] = result["log_return"].shift(1).rolling(config.long_vol_window).std()
    result["rolling_range_median_168h_past"] = result["range_pct"].shift(1).rolling(config.rolling_context_window).median()
    result["range_q20_168h_past"] = result["range_pct"].shift(1).rolling(config.rolling_context_window).quantile(config.low_quantile)
    result["range_q80_168h_past"] = result["range_pct"].shift(1).rolling(config.rolling_context_window).quantile(config.high_quantile)

    # Expansión/contracción de volatilidad observada contra contexto reciente.
    result["vol_expansion_event"] = result["range_pct"] > result["range_q80_168h_past"]
    result["vol_contraction_event"] = result["range_pct"] < result["range_q20_168h_past"]

    # Volumen relativo. Se usa rolling pasado para no comparar contra información futura.
    volume_past_mean = result["volume"].shift(1).rolling(config.rolling_context_window).mean()
    volume_past_std = result["volume"].shift(1).rolling(config.rolling_context_window).std()
    result["volume_zscore_168h_past"] = (result["volume"] - volume_past_mean) / volume_past_std.replace(0, np.nan)
    result["high_volume_event"] = result["volume_zscore_168h_past"] > 1.0
    result["low_volume_event"] = result["volume_zscore_168h_past"] < -1.0

    # Momentum fijo a distintos horizontes. Umbrales cuantílicos rolling calculados
    # con pasado. No se elige el mejor horizonte: se reportan todos.
    for window in config.momentum_windows:
        col = f"past_log_return_{window}h"
        result[col] = np.log(result["close"] / result["close"].shift(window))
        result[f"{col}_q20_past"] = result[col].shift(1).rolling(config.rolling_context_window).quantile(config.low_quantile)
        result[f"{col}_q80_past"] = result[col].shift(1).rolling(config.rolling_context_window).quantile(config.high_quantile)
        result[f"momentum_up_{window}h_event"] = result[col] > result[f"{col}_q80_past"]
        result[f"momentum_down_{window}h_event"] = result[col] < result[f"{col}_q20_past"]

    return result


# ---------------------------------------------------------------------------
# Estadística condicional
# ---------------------------------------------------------------------------


def safe_float(value: object) -> Optional[float]:
    """Convierte un valor numérico a float o None si no es finito."""

    try:
        output = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(output) or math.isinf(output):
        return None

    return output


def _block_bootstrap_idx(n: int, block_size: int, rng: np.random.Generator) -> np.ndarray:
    """Vectorized block bootstrap: genera n índices remuestreados por bloques."""
    block_size = max(1, min(block_size, n))
    max_start = n - block_size
    num_blocks = -(-n // block_size)  # ceil
    starts = rng.integers(0, max_start + 1, size=num_blocks)
    offsets = np.arange(block_size)
    idx = (starts[:, None] + offsets[None, :]).ravel()[:n]
    return idx


def bootstrap_mean_diff_ci(
    conditional_returns: pd.Series,
    baseline_returns: pd.Series,
    runs: int,
    seed: int,
    block_size: int = 24,
) -> Tuple[Optional[float], Optional[float]]:
    """Calcula intervalo bootstrap por bloques para diferencia de medias.

    FIX: reemplaza bootstrap iid por block bootstrap (bloques de 24 velas).
    Los retornos horarios de BTC tienen autocorrelación; el bootstrap iid
    subestima la varianza del estimador → intervalos demasiado estrechos →
    se declaraba POSITIVE_STATISTICAL_EDGE con más frecuencia de la real.

    Diferencia medida:
        media(retornos condicionales) - media(retornos base)

    Si hay pocos datos válidos, devuelve None.
    """

    cond = conditional_returns.dropna().to_numpy(dtype=float)
    base = baseline_returns.dropna().to_numpy(dtype=float)

    if len(cond) < 10 or len(base) < 10:
        return None, None

    rng = np.random.default_rng(seed)
    diffs = np.empty(runs, dtype=float)

    for i in range(runs):
        cond_idx = _block_bootstrap_idx(len(cond), block_size, rng)
        base_idx = _block_bootstrap_idx(len(base), block_size, rng)
        diffs[i] = float(np.nanmean(cond[cond_idx]) - np.nanmean(base[base_idx]))

    low, high = np.nanpercentile(diffs, [2.5, 97.5])
    return float(low), float(high)


def classify_evidence(
    sample_count: int,
    mean_diff: Optional[float],
    ci_low: Optional[float],
    ci_high: Optional[float],
    config: PatternConfig,
) -> str:
    """Clasifica evidencia de ventaja estadística de forma conservadora."""

    if sample_count < config.min_samples_for_evidence:
        return "INSUFFICIENT_FREQUENCY"

    if mean_diff is None or ci_low is None or ci_high is None:
        return "NO_STATISTICAL_EDGE"

    if ci_low > 0:
        return "POSITIVE_STATISTICAL_EDGE"

    if ci_high < 0:
        return "NEGATIVE_STATISTICAL_EDGE"

    return "NO_STATISTICAL_EDGE"


def conditional_return_stats(
    df: pd.DataFrame,
    condition: pd.Series,
    condition_name: str,
    category: str,
    horizon: int,
    config: PatternConfig,
) -> Dict[str, object]:
    """Calcula estadísticas de retorno forward bajo una condición."""

    ret_col = f"fwd_log_return_{horizon}h"
    valid = df[ret_col].notna()
    cond = condition.fillna(False) & valid
    baseline = df.loc[valid, ret_col]
    sample = df.loc[cond, ret_col]

    sample_count = int(sample.shape[0])
    baseline_count = int(baseline.shape[0])
    frequency = sample_count / baseline_count if baseline_count else np.nan

    sample_mean = safe_float(sample.mean())
    baseline_mean = safe_float(baseline.mean())
    mean_diff = None if sample_mean is None or baseline_mean is None else sample_mean - baseline_mean

    ci_low, ci_high = bootstrap_mean_diff_ci(
        sample,
        baseline,
        runs=config.bootstrap_runs,
        seed=config.random_seed + horizon + len(condition_name),
    )

    evidence = classify_evidence(sample_count, mean_diff, ci_low, ci_high, config)

    return {
        "category": category,
        "condition_name": condition_name,
        "forward_horizon_h": horizon,
        "sample_count": sample_count,
        "baseline_count": baseline_count,
        "frequency": safe_float(frequency),
        "sample_mean_fwd_log_return": sample_mean,
        "sample_median_fwd_log_return": safe_float(sample.median()),
        "sample_std_fwd_log_return": safe_float(sample.std()),
        "sample_positive_rate": safe_float((sample > 0).mean()),
        "baseline_mean_fwd_log_return": baseline_mean,
        "baseline_positive_rate": safe_float((baseline > 0).mean()),
        "mean_diff_vs_baseline": safe_float(mean_diff),
        "bootstrap_mean_diff_ci_low": safe_float(ci_low),
        "bootstrap_mean_diff_ci_high": safe_float(ci_high),
        "evidence_status": evidence,
    }


def build_pattern_summary(df: pd.DataFrame, config: PatternConfig) -> pd.DataFrame:
    """Construye una tabla unificada de patrones condicionales."""

    rows: List[Dict[str, object]] = []

    # Hora UTC como condición individual por hora.
    for hour in range(24):
        cond = df["hour_utc"] == hour
        for horizon in config.forward_horizons:
            rows.append(conditional_return_stats(df, cond, f"hour_utc_{hour:02d}", "hour_behavior", horizon, config))

    # Día de semana.
    for day in DAY_ORDER:
        cond = df["day_of_week"] == day
        for horizon in config.forward_horizons:
            rows.append(conditional_return_stats(df, cond, f"day_{DAY_NAME_ES[day]}", "day_behavior", horizon, config))

    # Volatilidad/volumen.
    event_conditions = {
        "vol_expansion_range_gt_q80_168h": df["vol_expansion_event"],
        "vol_contraction_range_lt_q20_168h": df["vol_contraction_event"],
        "high_volume_zscore_gt_1_168h": df["high_volume_event"],
        "low_volume_zscore_lt_minus_1_168h": df["low_volume_event"],
        "vol_expansion_and_high_volume": df["vol_expansion_event"] & df["high_volume_event"],
        "vol_contraction_and_low_volume": df["vol_contraction_event"] & df["low_volume_event"],
    }

    for name, cond in event_conditions.items():
        for horizon in config.forward_horizons:
            rows.append(conditional_return_stats(df, cond, name, "volatility_volume", horizon, config))

    # Momentum y reversión usan las mismas condiciones, pero se reportan en
    # categorías distintas para facilitar lectura de continuación vs reversión.
    for window in config.momentum_windows:
        up_cond = df[f"momentum_up_{window}h_event"]
        down_cond = df[f"momentum_down_{window}h_event"]

        for horizon in config.forward_horizons:
            rows.append(conditional_return_stats(df, up_cond, f"momentum_up_{window}h", "momentum", horizon, config))
            rows.append(conditional_return_stats(df, down_cond, f"momentum_down_{window}h", "momentum", horizon, config))
            rows.append(conditional_return_stats(df, up_cond, f"after_extreme_rally_{window}h", "mean_reversion", horizon, config))
            rows.append(conditional_return_stats(df, down_cond, f"after_extreme_drop_{window}h", "mean_reversion", horizon, config))

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tablas específicas por bloque de análisis
# ---------------------------------------------------------------------------


def summarize_base_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Resumen estadístico general de retornos."""

    rows = []
    for col in ["simple_return", "log_return", "abs_log_return", "range_pct", "volume"]:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        rows.append(
            {
                "metric": col,
                "count": int(s.shape[0]),
                "mean": safe_float(s.mean()),
                "median": safe_float(s.median()),
                "std": safe_float(s.std()),
                "min": safe_float(s.min()),
                "q01": safe_float(s.quantile(0.01)),
                "q05": safe_float(s.quantile(0.05)),
                "q95": safe_float(s.quantile(0.95)),
                "q99": safe_float(s.quantile(0.99)),
                "max": safe_float(s.max()),
                "skew": safe_float(s.skew()),
                "excess_kurtosis": safe_float(s.kurtosis()),
            }
        )
    return pd.DataFrame(rows)


def hourly_behavior(df: pd.DataFrame) -> pd.DataFrame:
    """Estadísticas descriptivas por hora UTC."""

    grouped = df.groupby("hour_utc", observed=True)
    out = grouped.agg(
        candles=("open_time_utc", "count"),
        mean_log_return=("log_return", "mean"),
        median_log_return=("log_return", "median"),
        volatility=("log_return", "std"),
        mean_abs_log_return=("abs_log_return", "mean"),
        mean_range_pct=("range_pct", "mean"),
        median_volume=("volume", "median"),
        positive_rate=("log_return", lambda x: float((x > 0).mean())),
        mean_fwd_1h=("fwd_log_return_1h", "mean"),
        positive_fwd_1h_rate=("fwd_log_return_1h", lambda x: float((x > 0).mean())),
    ).reset_index()
    return out.sort_values("hour_utc")


def day_behavior(df: pd.DataFrame) -> pd.DataFrame:
    """Estadísticas descriptivas por día de semana UTC."""

    grouped = df.groupby("day_of_week", observed=True)
    out = grouped.agg(
        candles=("open_time_utc", "count"),
        mean_log_return=("log_return", "mean"),
        median_log_return=("log_return", "median"),
        volatility=("log_return", "std"),
        mean_abs_log_return=("abs_log_return", "mean"),
        mean_range_pct=("range_pct", "mean"),
        median_volume=("volume", "median"),
        positive_rate=("log_return", lambda x: float((x > 0).mean())),
        mean_fwd_1h=("fwd_log_return_1h", "mean"),
        positive_fwd_1h_rate=("fwd_log_return_1h", lambda x: float((x > 0).mean())),
    ).reset_index()

    out["day_order"] = out["day_of_week"].map({d: i for i, d in enumerate(DAY_ORDER)})
    out["day_of_week_es"] = out["day_of_week"].map(DAY_NAME_ES)
    return out.sort_values("day_order").drop(columns=["day_order"])


def volatility_event_table(pattern_summary: pd.DataFrame) -> pd.DataFrame:
    """Extrae resultados de expansión/contracción/volumen."""

    return pattern_summary.loc[pattern_summary["category"] == "volatility_volume"].copy()


def momentum_table(pattern_summary: pd.DataFrame) -> pd.DataFrame:
    """Extrae resultados de momentum."""

    return pattern_summary.loc[pattern_summary["category"] == "momentum"].copy()


def mean_reversion_table(pattern_summary: pd.DataFrame) -> pd.DataFrame:
    """Extrae resultados de reversión a la media."""

    return pattern_summary.loc[pattern_summary["category"] == "mean_reversion"].copy()


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------


def save_bar_chart(df: pd.DataFrame, x: str, y: str, title: str, xlabel: str, ylabel: str, path: Path) -> None:
    """Guarda una gráfica de barras simple."""

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(df[x].astype(str), df[y])
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def save_line_chart(df: pd.DataFrame, x: str, y: str, title: str, xlabel: str, ylabel: str, path: Path) -> None:
    """Guarda una gráfica de línea."""

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df[x], df[y])
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def create_charts(df: pd.DataFrame, tables: Dict[str, pd.DataFrame], charts_dir: Path) -> None:
    """Genera gráficos principales del laboratorio."""

    save_bar_chart(
        tables["hourly_behavior"],
        x="hour_utc",
        y="volatility",
        title="Volatilidad por hora UTC",
        xlabel="Hora UTC",
        ylabel="Desviación estándar log-return",
        path=charts_dir / "01_volatility_by_hour_utc.png",
    )

    save_bar_chart(
        tables["hourly_behavior"],
        x="hour_utc",
        y="mean_fwd_1h",
        title="Retorno forward 1h promedio por hora UTC",
        xlabel="Hora UTC",
        ylabel="Media fwd log-return 1h",
        path=charts_dir / "02_forward_return_by_hour_utc.png",
    )

    save_bar_chart(
        tables["day_behavior"],
        x="day_of_week_es",
        y="volatility",
        title="Volatilidad por día de semana UTC",
        xlabel="Día",
        ylabel="Desviación estándar log-return",
        path=charts_dir / "03_volatility_by_day_utc.png",
    )

    save_bar_chart(
        tables["day_behavior"],
        x="day_of_week_es",
        y="mean_fwd_1h",
        title="Retorno forward 1h promedio por día UTC",
        xlabel="Día",
        ylabel="Media fwd log-return 1h",
        path=charts_dir / "04_forward_return_by_day_utc.png",
    )

    rolling = df[["open_time_utc", "rolling_vol_24h_past", "rolling_vol_168h_past"]].dropna().copy()
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(rolling["open_time_utc"], rolling["rolling_vol_24h_past"], label="Vol 24h pasada")
    ax.plot(rolling["open_time_utc"], rolling["rolling_vol_168h_past"], label="Vol 168h pasada")
    ax.set_title("Volatilidad rolling observada")
    ax.set_xlabel("Tiempo UTC")
    ax.set_ylabel("Std log-return")
    ax.legend()
    fig.tight_layout()
    fig.savefig(charts_dir / "05_rolling_volatility.png", dpi=150)
    plt.close(fig)

    # Gráficos de resumen condicional por categoría. Se filtra horizonte 1h para
    # evitar saturar las imágenes.
    for category, filename, title in [
        ("volatility_volume", "06_volatility_volume_patterns_fwd_1h.png", "Patrones de volatilidad/volumen — fwd 1h"),
        ("momentum", "07_momentum_patterns_fwd_1h.png", "Patrones de momentum — fwd 1h"),
        ("mean_reversion", "08_mean_reversion_patterns_fwd_1h.png", "Patrones de reversión — fwd 1h"),
    ]:
        subset = tables["pattern_findings_summary"]
        subset = subset[(subset["category"] == category) & (subset["forward_horizon_h"] == 1)].copy()
        if subset.empty:
            continue
        subset = subset.sort_values("mean_diff_vs_baseline")
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.bar(subset["condition_name"], subset["mean_diff_vs_baseline"])
        ax.axhline(0, linewidth=1)
        ax.set_title(title)
        ax.set_xlabel("Condición")
        ax.set_ylabel("Diferencia media vs baseline")
        ax.tick_params(axis="x", rotation=90)
        fig.tight_layout()
        fig.savefig(charts_dir / filename, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Reporte Markdown
# ---------------------------------------------------------------------------


def fmt(value: object, digits: int = 6) -> str:
    """Formatea números para reporte."""

    val = safe_float(value)
    if val is None:
        return "nan"
    return f"{val:.{digits}f}"


def top_evidence_rows(pattern_summary: pd.DataFrame) -> pd.DataFrame:
    """Selecciona filas con evidencia estadística positiva o negativa."""

    mask = pattern_summary["evidence_status"].isin(["POSITIVE_STATISTICAL_EDGE", "NEGATIVE_STATISTICAL_EDGE"])
    cols = [
        "category",
        "condition_name",
        "forward_horizon_h",
        "sample_count",
        "frequency",
        "sample_mean_fwd_log_return",
        "baseline_mean_fwd_log_return",
        "mean_diff_vs_baseline",
        "bootstrap_mean_diff_ci_low",
        "bootstrap_mean_diff_ci_high",
        "evidence_status",
    ]
    return pattern_summary.loc[mask, cols].sort_values(["category", "forward_horizon_h", "mean_diff_vs_baseline"], ascending=[True, True, False])


def dataframe_to_markdown(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Convierte DataFrame a tabla Markdown sin depender de tabulate."""

    if df.empty:
        return "No hay filas para mostrar."

    display = df.head(max_rows).copy()
    cols = list(display.columns)

    def cell(x: object) -> str:
        if isinstance(x, float):
            if math.isnan(x) or math.isinf(x):
                return "nan"
            return f"{x:.6f}"
        return str(x)

    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---" for _ in cols]) + "|")
    for _, row in display.iterrows():
        lines.append("| " + " | ".join(cell(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_report(
    df: pd.DataFrame,
    tables: Dict[str, pd.DataFrame],
    config: PatternConfig,
    reports_dir: Path,
) -> None:
    """Escribe el reporte Markdown principal."""

    base = tables["base_return_summary"]
    hour = tables["hourly_behavior"]
    day = tables["day_behavior"]
    pattern_summary = tables["pattern_findings_summary"]
    evidence = top_evidence_rows(pattern_summary)

    most_volatile_hour = hour.sort_values("volatility", ascending=False).iloc[0]
    least_volatile_hour = hour.sort_values("volatility", ascending=True).iloc[0]
    most_volatile_day = day.sort_values("volatility", ascending=False).iloc[0]
    least_volatile_day = day.sort_values("volatility", ascending=True).iloc[0]

    positive_edges = int((pattern_summary["evidence_status"] == "POSITIVE_STATISTICAL_EDGE").sum())
    negative_edges = int((pattern_summary["evidence_status"] == "NEGATIVE_STATISTICAL_EDGE").sum())
    no_edges = int((pattern_summary["evidence_status"] == "NO_STATISTICAL_EDGE").sum())
    insufficient = int((pattern_summary["evidence_status"] == "INSUFFICIENT_FREQUENCY").sum())

    log_row = base.loc[base["metric"] == "log_return"].iloc[0]

    lines: List[str] = []
    lines.append(f"# S1(Q5) — Descubrimiento de Patrones — {config.symbol} {config.interval}")
    lines.append("")
    lines.append("## Principio rector")
    lines.append("")
    lines.append("Este reporte identifica patrones observables. No optimiza parámetros, no genera señales de compra/venta y no declara que una estrategia funcione. La evidencia se reporta incluso cuando no existe ventaja estadística.")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- Filas analizadas: `{len(df):,}`")
    lines.append(f"- Inicio UTC: `{df['open_time_utc'].min()}`")
    lines.append(f"- Fin UTC: `{df['open_time_utc'].max()}`")
    lines.append(f"- Horizontes forward evaluados: `{config.forward_horizons}` horas")
    lines.append(f"- Ventanas de momentum observadas: `{config.momentum_windows}` horas")
    lines.append(f"- Umbrales rolling fijos: q{int(config.low_quantile*100)} / q{int(config.high_quantile*100)} con ventana `{config.rolling_context_window}` velas")
    lines.append("")
    lines.append("## 1. Distribución base de retornos")
    lines.append("")
    lines.append(f"- Media log-return por vela: `{fmt(log_row['mean'])}`")
    lines.append(f"- Desviación estándar log-return: `{fmt(log_row['std'])}`")
    lines.append(f"- Asimetría: `{fmt(log_row['skew'])}`")
    lines.append(f"- Curtosis excedente: `{fmt(log_row['excess_kurtosis'])}`")
    lines.append("")
    lines.append("Interpretación: esta sección es la línea base. Cualquier patrón debe compararse contra el comportamiento promedio del activo, no contra cero de forma aislada.")
    lines.append("")
    lines.append("## 2. Comportamiento por hora UTC")
    lines.append("")
    lines.append(f"- Hora con mayor volatilidad: `{int(most_volatile_hour['hour_utc']):02d}:00 UTC`, volatilidad `{fmt(most_volatile_hour['volatility'])}`")
    lines.append(f"- Hora con menor volatilidad: `{int(least_volatile_hour['hour_utc']):02d}:00 UTC`, volatilidad `{fmt(least_volatile_hour['volatility'])}`")
    lines.append("")
    lines.append("Tabla completa: `tables/hourly_behavior.csv`.")
    lines.append("")
    lines.append("## 3. Comportamiento por día de semana UTC")
    lines.append("")
    lines.append(f"- Día con mayor volatilidad: `{most_volatile_day['day_of_week_es']}`, volatilidad `{fmt(most_volatile_day['volatility'])}`")
    lines.append(f"- Día con menor volatilidad: `{least_volatile_day['day_of_week_es']}`, volatilidad `{fmt(least_volatile_day['volatility'])}`")
    lines.append("")
    lines.append("Tabla completa: `tables/day_behavior.csv`.")
    lines.append("")
    lines.append("## 4. Resumen de evidencia estadística")
    lines.append("")
    lines.append(f"- Patrones con evidencia positiva vs baseline: `{positive_edges}`")
    lines.append(f"- Patrones con evidencia negativa vs baseline: `{negative_edges}`")
    lines.append(f"- Patrones sin ventaja estadística clara: `{no_edges}`")
    lines.append(f"- Patrones con frecuencia insuficiente: `{insufficient}`")
    lines.append("")
    if positive_edges == 0:
        lines.append("Conclusión conservadora: bajo estas reglas fijas, no se detectó ventaja estadística positiva robusta frente a la línea base en las condiciones evaluadas.")
    else:
        lines.append("Conclusión conservadora: existen patrones con diferencia positiva frente a la línea base, pero deben tratarse solo como hipótesis para validación posterior. No son estrategias confirmadas.")
    lines.append("")
    lines.append("## 5. Evidencia destacada")
    lines.append("")
    lines.append(dataframe_to_markdown(evidence, max_rows=25))
    lines.append("")
    lines.append("## 6. Expansión y contracción de volatilidad")
    lines.append("")
    lines.append("Se compara el retorno forward después de velas con rango superior al percentil 80 rolling y rango inferior al percentil 20 rolling. Los umbrales usan solo información pasada.")
    lines.append("")
    lines.append("Tabla completa: `tables/volatility_volume_patterns.csv`.")
    lines.append("")
    lines.append("## 7. Momentum")
    lines.append("")
    lines.append("Se observan retornos acumulados pasados de 3h, 6h y 24h contra cuantiles rolling. No se selecciona el mejor parámetro; se reportan todos.")
    lines.append("")
    lines.append("Tabla completa: `tables/momentum_patterns.csv`.")
    lines.append("")
    lines.append("## 8. Reversión a la media")
    lines.append("")
    lines.append("Se evalúa qué ocurre después de rallies y caídas extremas según cuantiles rolling. Una diferencia positiva después de caída extrema puede sugerir rebote; una diferencia negativa después de rally extremo puede sugerir reversión bajista.")
    lines.append("")
    lines.append("Tabla completa: `tables/mean_reversion_patterns.csv`.")
    lines.append("")
    lines.append("## 9. Lectura disciplinada")
    lines.append("")
    lines.append("Un patrón observable no es una estrategia. Para avanzar, cualquier hallazgo debe pasar por: validación fuera de muestra, walk-forward, costos más altos, comparación contra buy & hold, Monte Carlo y prueba en otros activos líquidos.")
    lines.append("")
    lines.append("## Archivos generados")
    lines.append("")
    lines.append("- `tables/base_return_summary.csv`")
    lines.append("- `tables/hourly_behavior.csv`")
    lines.append("- `tables/day_behavior.csv`")
    lines.append("- `tables/pattern_findings_summary.csv`")
    lines.append("- `tables/volatility_volume_patterns.csv`")
    lines.append("- `tables/momentum_patterns.csv`")
    lines.append("- `tables/mean_reversion_patterns.csv`")
    lines.append("- `charts/*.png`")

    (reports_dir / "q5_pattern_discovery_report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def write_metadata(config: PatternConfig, input_path: Path, output_dir: Path, reports_dir: Path) -> None:
    """Guarda metadata de ejecución."""

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "config": asdict(config),
        "methodology": {
            "optimization": "No parameter optimization. Fixed observational windows and fixed rolling quantiles.",
            "forward_returns": "Forward returns are used only for evidence measurement, not for condition construction.",
            "rolling_thresholds": "Rolling quantile thresholds are shifted by one candle to use only past information.",
            "evidence_rule": "Bootstrap confidence interval for mean difference versus baseline. Conservative classification.",
        },
    }

    (reports_dir / "q5_pattern_discovery_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------


def run_pattern_discovery(args: argparse.Namespace) -> None:
    """Ejecuta el laboratorio S1(Q5)."""

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    paths = ensure_dirs(output_dir)

    config = PatternConfig(
        symbol=args.symbol,
        interval=args.interval,
        bootstrap_runs=args.bootstrap_runs,
        min_samples_for_evidence=args.min_samples,
    )

    raw = read_market_data(input_path)
    data = standardize_columns(raw)
    data = prepare_features(data, config)

    pattern_summary = build_pattern_summary(data, config)

    tables = {
        "base_return_summary": summarize_base_returns(data),
        "hourly_behavior": hourly_behavior(data),
        "day_behavior": day_behavior(data),
        "pattern_findings_summary": pattern_summary,
        "volatility_volume_patterns": volatility_event_table(pattern_summary),
        "momentum_patterns": momentum_table(pattern_summary),
        "mean_reversion_patterns": mean_reversion_table(pattern_summary),
    }

    for name, table in tables.items():
        table.to_csv(paths["tables"] / f"{name}.csv", index=False)

    data.to_parquet(output_dir / f"{config.symbol}_{config.interval}_q5_pattern_features.parquet", index=False)

    create_charts(data, tables, paths["charts"])
    write_report(data, tables, config, paths["reports"])
    write_metadata(config, input_path, output_dir, paths["reports"])

    print("\nS1(Q5) Descubrimiento de Patrones terminado.")
    print(f"Tablas:   {paths['tables']}")
    print(f"Gráficos: {paths['charts']}")
    print(f"Reporte:  {paths['reports'] / 'q5_pattern_discovery_report.md'}")
    print("\nRecuerda: un patrón observable no es una estrategia; debe validarse fuera de muestra.")


def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos."""

    parser = argparse.ArgumentParser(
        description="S1(Q5) — Descubrimiento de patrones observables sin optimización."
    )
    parser.add_argument("--input", required=True, help="Ruta del dataset Parquet/CSV enriquecido.")
    parser.add_argument("--output-dir", required=True, help="Carpeta donde se guardarán tablas, gráficos y reporte.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo analizado.")
    parser.add_argument("--interval", default="1h", help="Intervalo temporal analizado.")
    parser.add_argument("--bootstrap-runs", type=int, default=500, help="Número de remuestreos bootstrap por patrón.")
    parser.add_argument("--min-samples", type=int, default=100, help="Frecuencia mínima para considerar evidencia estadística.")
    return parser.parse_args()


def main() -> None:
    """Punto de entrada."""

    args = parse_args()
    run_pattern_discovery(args)


if __name__ == "__main__":
    main()
