"""
02_quant_eda.py

Análisis Exploratorio Cuantitativo para un dataset limpio de BTCUSDT.

Objetivo:
- Estudiar estadísticamente el comportamiento del mercado antes de diseñar estrategias.
- NO genera señales de compra o venta.
- Genera gráficos, tablas y un reporte interpretativo automático.

Uso recomendado desde PowerShell:
.\.venv\Scripts\python.exe 02_quant_eda.py `
  --input .\crypto_datalake\processed\binance\spot\klines\BTCUSDT\1h\BTCUSDT_1h_2021-06_to_2026-05.parquet `
  --output-dir .\crypto_datalake\research\quant_eda\BTCUSDT\1h `
  --symbol BTCUSDT `
  --interval 1h
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EDAConfig:
    """Configuración principal del análisis exploratorio cuantitativo."""

    input_path: Path
    output_dir: Path
    symbol: str
    interval: str
    timezone: str = "UTC"
    autocorr_lags: int = 48
    rolling_vol_window: int = 24
    annualization_factor: Optional[float] = None


REQUIRED_COLUMNS = {
    "open_time_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
}

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


SPANISH_DAY_NAMES = {
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo",
}


def parse_args() -> EDAConfig:
    """Lee parámetros de consola y los transforma en una configuración validada."""

    parser = argparse.ArgumentParser(
        description="Análisis Exploratorio Cuantitativo para datos OHLCV limpios."
    )

    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Ruta al dataset limpio en Parquet o CSV.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Carpeta donde se guardarán gráficos, tablas y reportes.",
    )

    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Símbolo analizado. Ejemplo: BTCUSDT.",
    )

    parser.add_argument(
        "--interval",
        default="1h",
        help="Temporalidad del dataset. Ejemplo: 1h, 4h, 1d.",
    )

    parser.add_argument(
        "--autocorr-lags",
        default=48,
        type=int,
        help="Número de rezagos para autocorrelación de retornos.",
    )

    parser.add_argument(
        "--rolling-vol-window",
        default=24,
        type=int,
        help="Ventana para volatilidad rolling. Para 1h, 24 equivale a 24 horas.",
    )

    parser.add_argument(
        "--annualization-factor",
        default=None,
        type=float,
        help=(
            "Factor de anualización. Para 1h suele usarse 24*365=8760. "
            "Si se omite, se infiere de --interval cuando sea posible."
        ),
    )

    args = parser.parse_args()

    return EDAConfig(
        input_path=args.input,
        output_dir=args.output_dir,
        symbol=args.symbol.upper(),
        interval=args.interval,
        autocorr_lags=args.autocorr_lags,
        rolling_vol_window=args.rolling_vol_window,
        annualization_factor=args.annualization_factor,
    )


def infer_annualization_factor(interval: str) -> Optional[float]:
    """Infiere cuántas velas caben aproximadamente en un año para anualizar volatilidad.

    FIX: añade soporte para "1s" (segundos), "1w" (semanas) y "1mo" (mes). Antes
    esos intervalos devolvían None silenciosamente, dejando annualized_* vacíos.
    """

    normalized = interval.strip().lower()

    # Segundos (p.ej. "1s")
    if normalized.endswith("s") and not normalized.endswith("mo"):
        try:
            seconds = float(normalized[:-1])
            return (365.0 * 24.0 * 3600.0) / seconds
        except ValueError:
            return None

    # Minutos (p.ej. "1m", "5m", "15m")
    if normalized.endswith("m") and not normalized.endswith("mo"):
        try:
            minutes = float(normalized[:-1])
            return (365.0 * 24.0 * 60.0) / minutes
        except ValueError:
            return None

    # Horas (p.ej. "1h", "4h", "12h")
    if normalized.endswith("h"):
        try:
            hours = float(normalized[:-1])
            return (365.0 * 24.0) / hours
        except ValueError:
            return None

    # Días (p.ej. "1d", "3d")
    if normalized.endswith("d"):
        try:
            days = float(normalized[:-1])
            return 365.0 / days
        except ValueError:
            return None

    # Semanas (p.ej. "1w")
    if normalized.endswith("w"):
        try:
            weeks = float(normalized[:-1])
            return 52.1775 / weeks  # semanas por año (365.25/7)
        except ValueError:
            return None

    # Meses (p.ej. "1mo")
    if normalized.endswith("mo"):
        try:
            months = float(normalized[:-2])
            return 12.0 / months
        except ValueError:
            return None

    return None


def ensure_output_dirs(output_dir: Path) -> Dict[str, Path]:
    """Crea la estructura de salida para separar tablas, gráficos y reportes."""

    paths = {
        "root": output_dir,
        "tables": output_dir / "tables",
        "charts": output_dir / "charts",
        "reports": output_dir / "reports",
    }

    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    return paths


def load_dataset(path: Path) -> pd.DataFrame:
    """Carga un dataset Parquet o CSV sin asumir formato oculto."""

    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {path}")

    suffix = path.suffix.lower()

    if suffix == ".parquet":
        df = pd.read_parquet(path)
    elif suffix == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError("Formato no soportado. Usa .parquet o .csv")

    return df


def validate_schema(df: pd.DataFrame) -> None:
    """Valida columnas mínimas para análisis cuantitativo OHLCV."""

    missing = sorted(REQUIRED_COLUMNS.difference(df.columns))

    if missing:
        raise ValueError(
            "El dataset no tiene las columnas mínimas requeridas: " + ", ".join(missing)
        )


def prepare_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos, orden temporal y elimina duplicados por open_time_utc."""

    prepared = df.copy()

    prepared["open_time_utc"] = pd.to_datetime(
        prepared["open_time_utc"], utc=True, errors="coerce"
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]

    for column in numeric_columns:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    prepared = prepared.dropna(subset=["open_time_utc", "open", "high", "low", "close", "volume"])

    # FIX: keep="last" para coherencia con Q3, Q5, S3 (la corrección más reciente prevalece).
    prepared = prepared.sort_values("open_time_utc").drop_duplicates(
        subset=["open_time_utc"], keep="last"
    )

    prepared = prepared.reset_index(drop=True)

    return prepared


def add_return_and_volatility_features(df: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    """Calcula retornos, retornos logarítmicos y proxies de volatilidad."""

    enriched = df.copy()

    enriched["simple_return"] = enriched["close"].pct_change()

    enriched["log_return"] = np.log(enriched["close"] / enriched["close"].shift(1))

    enriched["abs_log_return"] = enriched["log_return"].abs()

    enriched["squared_log_return"] = enriched["log_return"] ** 2

    enriched["range_pct"] = (enriched["high"] - enriched["low"]) / enriched["close"]

    enriched["body_pct"] = (enriched["close"] - enriched["open"]).abs() / enriched["open"]

    enriched["upper_wick_pct"] = (
        enriched["high"] - enriched[["open", "close"]].max(axis="columns")
    ) / enriched["open"]

    enriched["lower_wick_pct"] = (
        enriched[["open", "close"]].min(axis="columns") - enriched["low"]
    ) / enriched["open"]

    enriched["volume_change_pct"] = enriched["volume"].pct_change()

    enriched["rolling_volatility"] = enriched["log_return"].rolling(rolling_window).std()

    enriched["rolling_mean_return"] = enriched["log_return"].rolling(rolling_window).mean()

    enriched["hour_utc"] = enriched["open_time_utc"].dt.hour

    enriched["day_of_week"] = enriched["open_time_utc"].dt.day_name()

    enriched["date_utc"] = enriched["open_time_utc"].dt.date.astype(str)

    return enriched


def compute_summary_statistics(df: pd.DataFrame, annualization_factor: Optional[float]) -> pd.DataFrame:
    """Genera estadísticas descriptivas de retornos simples, logarítmicos y volumen."""

    rows: List[Dict[str, object]] = []

    for column in ["simple_return", "log_return", "abs_log_return", "range_pct", "volume"]:
        series = df[column].replace([np.inf, -np.inf], np.nan).dropna()

        row: Dict[str, object] = {
            "metric": column,
            "count": int(series.count()),
            "mean": float(series.mean()),
            "median": float(series.median()),
            "std": float(series.std()),
            "min": float(series.min()),
            "max": float(series.max()),
            "skew": float(series.skew()),
            "excess_kurtosis": float(series.kurt()),
        }

        if column in {"simple_return", "log_return"} and annualization_factor:
            row["annualized_mean_approx"] = float(series.mean() * annualization_factor)
            row["annualized_volatility_approx"] = float(series.std() * math.sqrt(annualization_factor))
        else:
            row["annualized_mean_approx"] = None
            row["annualized_volatility_approx"] = None

        rows.append(row)

    return pd.DataFrame(rows)


def compute_return_quantiles(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula cuantiles para estudiar colas y eventos extremos."""

    quantiles = [0.001, 0.005, 0.01, 0.025, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.975, 0.99, 0.995, 0.999]

    result = pd.DataFrame({
        "quantile": quantiles,
        "simple_return": df["simple_return"].quantile(quantiles).to_numpy(),
        "log_return": df["log_return"].quantile(quantiles).to_numpy(),
        "abs_log_return": df["abs_log_return"].quantile(quantiles).to_numpy(),
        "range_pct": df["range_pct"].quantile(quantiles).to_numpy(),
    })

    return result


def compute_volatility_by_hour(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa volatilidad por hora UTC del día."""

    grouped = (
        df.groupby("hour_utc")
        .agg(
            observations=("log_return", "count"),
            mean_log_return=("log_return", "mean"),
            volatility_std=("log_return", "std"),
            mean_abs_return=("abs_log_return", "mean"),
            mean_range_pct=("range_pct", "mean"),
            median_volume=("volume", "median"),
        )
        .reset_index()
    )

    return grouped


def compute_volatility_by_day(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa volatilidad por día de semana en UTC."""

    grouped = (
        df.groupby("day_of_week")
        .agg(
            observations=("log_return", "count"),
            mean_log_return=("log_return", "mean"),
            volatility_std=("log_return", "std"),
            mean_abs_return=("abs_log_return", "mean"),
            mean_range_pct=("range_pct", "mean"),
            median_volume=("volume", "median"),
        )
        .reindex(DAY_ORDER)
        .reset_index()
    )

    grouped["day_of_week_es"] = grouped["day_of_week"].map(SPANISH_DAY_NAMES)

    return grouped


def compute_drawdown_series(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula drawdown histórico usando el precio de cierre como curva base."""

    result = df[["open_time_utc", "close"]].copy()

    result["running_max_close"] = result["close"].cummax()

    result["drawdown"] = (result["close"] / result["running_max_close"]) - 1.0

    return result


def compute_drawdown_events(drawdown_df: pd.DataFrame) -> pd.DataFrame:
    """Detecta eventos de drawdown desde un máximo histórico hasta recuperación."""

    events: List[Dict[str, object]] = []

    in_drawdown = False
    peak_time = None
    trough_time = None
    recovery_time = None
    trough_drawdown = 0.0
    peak_price = None
    trough_price = None

    for row in drawdown_df.itertuples(index=False):
        time = row.open_time_utc
        close = float(row.close)
        drawdown = float(row.drawdown)

        if drawdown < 0 and not in_drawdown:
            in_drawdown = True
            peak_idx = drawdown_df.index[drawdown_df["open_time_utc"] == time][0] - 1
            if peak_idx >= 0:
                peak_row = drawdown_df.iloc[peak_idx]
                peak_time = peak_row["open_time_utc"]
                peak_price = float(peak_row["close"])
            else:
                peak_time = time
                peak_price = close
            trough_time = time
            trough_price = close
            trough_drawdown = drawdown

        elif drawdown < 0 and in_drawdown:
            if drawdown < trough_drawdown:
                trough_drawdown = drawdown
                trough_time = time
                trough_price = close

        elif drawdown == 0 and in_drawdown:
            recovery_time = time
            duration_hours = (recovery_time - peak_time).total_seconds() / 3600 if peak_time is not None else None
            underwater_hours = (recovery_time - trough_time).total_seconds() / 3600 if trough_time is not None else None

            events.append(
                {
                    "peak_time_utc": peak_time,
                    "trough_time_utc": trough_time,
                    "recovery_time_utc": recovery_time,
                    "peak_close": peak_price,
                    "trough_close": trough_price,
                    "max_drawdown": trough_drawdown,
                    "duration_hours_peak_to_recovery": duration_hours,
                    "duration_hours_trough_to_recovery": underwater_hours,
                    "recovered": True,
                }
            )

            in_drawdown = False
            peak_time = None
            trough_time = None
            recovery_time = None
            trough_drawdown = 0.0
            peak_price = None
            trough_price = None

    if in_drawdown:
        last_time = drawdown_df["open_time_utc"].iloc[-1]
        events.append(
            {
                "peak_time_utc": peak_time,
                "trough_time_utc": trough_time,
                "recovery_time_utc": None,
                "peak_close": peak_price,
                "trough_close": trough_price,
                "max_drawdown": trough_drawdown,
                "duration_hours_peak_to_recovery": (last_time - peak_time).total_seconds() / 3600 if peak_time is not None else None,
                "duration_hours_trough_to_recovery": None,
                "recovered": False,
            }
        )

    if not events:
        return pd.DataFrame(
            columns=[
                "peak_time_utc",
                "trough_time_utc",
                "recovery_time_utc",
                "peak_close",
                "trough_close",
                "max_drawdown",
                "duration_hours_peak_to_recovery",
                "duration_hours_trough_to_recovery",
                "recovered",
            ]
        )

    return pd.DataFrame(events).sort_values("max_drawdown").reset_index(drop=True)


def compute_streaks(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Calcula rachas consecutivas alcistas y bajistas con base en log_return."""

    returns = df[["open_time_utc", "log_return"]].dropna().copy()

    returns["direction"] = np.where(
        returns["log_return"] > 0,
        "up",
        np.where(returns["log_return"] < 0, "down", "flat"),
    )

    returns["streak_group"] = (returns["direction"] != returns["direction"].shift()).cumsum()

    streaks = (
        returns.groupby(["streak_group", "direction"])
        .agg(
            start_time_utc=("open_time_utc", "first"),
            end_time_utc=("open_time_utc", "last"),
            length_bars=("log_return", "count"),
            cumulative_log_return=("log_return", "sum"),
        )
        .reset_index()
        .drop(columns=["streak_group"])
    )

    streaks = streaks[streaks["direction"].isin(["up", "down"])].reset_index(drop=True)

    summary = (
        streaks.groupby("direction")
        .agg(
            streak_count=("length_bars", "count"),
            mean_length=("length_bars", "mean"),
            median_length=("length_bars", "median"),
            max_length=("length_bars", "max"),
            mean_cumulative_log_return=("cumulative_log_return", "mean"),
            max_abs_cumulative_log_return=("cumulative_log_return", lambda s: float(s.abs().max())),
        )
        .reset_index()
    )

    return streaks, summary


def compute_trend_persistence(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula persistencia de dirección entre una vela y la siguiente."""

    returns = df[["log_return"]].dropna().copy()

    returns["direction"] = np.where(
        returns["log_return"] > 0,
        "up",
        np.where(returns["log_return"] < 0, "down", "flat"),
    )

    returns = returns[returns["direction"].isin(["up", "down"])]

    returns["next_direction"] = returns["direction"].shift(-1)

    returns = returns.dropna(subset=["next_direction"])

    matrix = pd.crosstab(
        returns["direction"],
        returns["next_direction"],
        normalize="index",
    )

    matrix = matrix.reindex(index=["up", "down"], columns=["up", "down"]).fillna(0.0)

    rows = [
        {
            "current_direction": current,
            "prob_next_up": float(matrix.loc[current, "up"]),
            "prob_next_down": float(matrix.loc[current, "down"]),
            "persistence_probability": float(matrix.loc[current, current]),
        }
        for current in ["up", "down"]
    ]

    same_direction_rate = float((returns["direction"] == returns["next_direction"]).mean())

    rows.append(
        {
            "current_direction": "overall",
            "prob_next_up": None,
            "prob_next_down": None,
            "persistence_probability": same_direction_rate,
        }
    )

    return pd.DataFrame(rows)


def compute_autocorrelation(df: pd.DataFrame, max_lag: int) -> pd.DataFrame:
    """Calcula autocorrelación de retornos logarítmicos para varios rezagos."""

    returns = df["log_return"].replace([np.inf, -np.inf], np.nan).dropna()

    rows = []

    for lag in range(1, max_lag + 1):
        rows.append(
            {
                "lag": lag,
                "autocorrelation": float(returns.autocorr(lag=lag)),
            }
        )

    return pd.DataFrame(rows)


def compute_volume_volatility_correlation(df: pd.DataFrame) -> pd.DataFrame:
    """Evalúa correlación entre volumen y proxies de volatilidad."""

    temp = df[["volume", "abs_log_return", "range_pct", "rolling_volatility"]].copy()

    temp["log1p_volume"] = np.log1p(temp["volume"])

    pairs = [
        ("volume", "abs_log_return"),
        ("volume", "range_pct"),
        ("volume", "rolling_volatility"),
        ("log1p_volume", "abs_log_return"),
        ("log1p_volume", "range_pct"),
        ("log1p_volume", "rolling_volatility"),
    ]

    rows = []

    for left, right in pairs:
        clean = temp[[left, right]].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "x": left,
                "y": right,
                "pearson_correlation": float(clean[left].corr(clean[right], method="pearson")),
                "spearman_correlation": float(clean[left].corr(clean[right], method="spearman")),
                "observations": int(len(clean)),
            }
        )

    return pd.DataFrame(rows)


def detect_statistical_anomalies(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    autocorr: pd.DataFrame,
    volume_corr: pd.DataFrame,
    drawdown_events: pd.DataFrame,
) -> pd.DataFrame:
    """Detecta anomalías candidatas mediante umbrales heurísticos documentados."""

    anomalies: List[Dict[str, object]] = []

    log_stats = summary.loc[summary["metric"] == "log_return"].iloc[0]

    if abs(float(log_stats["skew"])) > 1.0:
        anomalies.append(
            {
                "type": "return_distribution",
                "metric": "skew",
                "value": float(log_stats["skew"]),
                "threshold": "abs(skew) > 1.0",
                "interpretation": "Asimetría fuerte en retornos logarítmicos; revisar eventos extremos direccionales.",
            }
        )

    if float(log_stats["excess_kurtosis"]) > 3.0:
        anomalies.append(
            {
                "type": "return_distribution",
                "metric": "excess_kurtosis",
                "value": float(log_stats["excess_kurtosis"]),
                "threshold": "excess_kurtosis > 3.0",
                "interpretation": "Colas pesadas; los retornos extremos aparecen más de lo esperado bajo normalidad.",
            }
        )

    strongest_autocorr = autocorr.reindex(autocorr["autocorrelation"].abs().sort_values(ascending=False).index).head(1)

    if not strongest_autocorr.empty:
        value = float(strongest_autocorr["autocorrelation"].iloc[0])
        lag = int(strongest_autocorr["lag"].iloc[0])
        if abs(value) > 0.05:
            anomalies.append(
                {
                    "type": "return_autocorrelation",
                    "metric": f"lag_{lag}",
                    "value": value,
                    "threshold": "abs(autocorrelation) > 0.05",
                    "interpretation": "Posible dependencia temporal débil; requiere prueba fuera de muestra antes de usarla.",
                }
            )

    strongest_volume_corr = volume_corr.reindex(volume_corr["spearman_correlation"].abs().sort_values(ascending=False).index).head(1)

    if not strongest_volume_corr.empty:
        value = float(strongest_volume_corr["spearman_correlation"].iloc[0])
        if abs(value) > 0.30:
            anomalies.append(
                {
                    "type": "volume_volatility_relationship",
                    "metric": f"{strongest_volume_corr['x'].iloc[0]} vs {strongest_volume_corr['y'].iloc[0]}",
                    "value": value,
                    "threshold": "abs(spearman_correlation) > 0.30",
                    "interpretation": "Relación monotónica relevante entre volumen y volatilidad; puede ser útil para análisis de régimen.",
                }
            )

    if not drawdown_events.empty:
        worst_dd = float(drawdown_events["max_drawdown"].min())
        if worst_dd < -0.50:
            anomalies.append(
                {
                    "type": "drawdown",
                    "metric": "max_drawdown",
                    "value": worst_dd,
                    "threshold": "max_drawdown < -50%",
                    "interpretation": "Caída histórica extrema; cualquier backtest debe medir exposición a drawdowns profundos.",
                }
            )

    extreme_returns = df[["open_time_utc", "log_return", "abs_log_return", "volume", "range_pct"]].dropna().copy()

    cutoff = extreme_returns["abs_log_return"].quantile(0.999)

    top_extremes = extreme_returns[extreme_returns["abs_log_return"] >= cutoff].sort_values("abs_log_return", ascending=False).head(20)

    for row in top_extremes.itertuples(index=False):
        anomalies.append(
            {
                "type": "extreme_bar",
                "metric": "abs_log_return_top_0_1pct",
                "value": float(row.abs_log_return),
                "threshold": "abs_log_return >= quantile(99.9%)",
                "timestamp_utc": str(row.open_time_utc),
                "interpretation": "Vela extrema detectada; revisar si coincide con eventos de mercado o microestructura.",
            }
        )

    return pd.DataFrame(anomalies)


def save_table(df: pd.DataFrame, path: Path) -> None:
    """Guarda una tabla en CSV usando codificación UTF-8."""

    df.to_csv(path, index=False, encoding="utf-8")


def save_json(payload: Dict[str, object], path: Path) -> None:
    """Guarda un diccionario como JSON legible y reproducible."""

    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False, default=str)


def plot_close_price(df: pd.DataFrame, path: Path, symbol: str) -> None:
    """Grafica precio de cierre."""

    plt.figure(figsize=(14, 6))
    plt.plot(df["open_time_utc"], df["close"])
    plt.title(f"{symbol} - Precio de cierre")
    plt.xlabel("Tiempo UTC")
    plt.ylabel("Close")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_histogram(series: pd.Series, path: Path, title: str, xlabel: str, bins: int = 150) -> None:
    """Grafica histograma de una serie numérica."""

    clean = series.replace([np.inf, -np.inf], np.nan).dropna()

    plt.figure(figsize=(12, 6))
    plt.hist(clean, bins=bins)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Frecuencia")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_rolling_volatility(df: pd.DataFrame, path: Path, window: int) -> None:
    """Grafica volatilidad rolling."""

    plt.figure(figsize=(14, 6))
    plt.plot(df["open_time_utc"], df["rolling_volatility"])
    plt.title(f"Volatilidad rolling de retornos logarítmicos - ventana {window}")
    plt.xlabel("Tiempo UTC")
    plt.ylabel("Volatilidad rolling")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_bar(df: pd.DataFrame, x: str, y: str, path: Path, title: str, xlabel: str, ylabel: str) -> None:
    """Grafica barras sin fijar colores para mantener compatibilidad simple."""

    plt.figure(figsize=(12, 6))
    plt.bar(df[x].astype(str), df[y])
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_drawdown(drawdown_df: pd.DataFrame, path: Path) -> None:
    """Grafica drawdown histórico."""

    plt.figure(figsize=(14, 6))
    plt.plot(drawdown_df["open_time_utc"], drawdown_df["drawdown"])
    plt.title("Drawdown histórico basado en precio de cierre")
    plt.xlabel("Tiempo UTC")
    plt.ylabel("Drawdown")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_streak_distribution(streaks: pd.DataFrame, path: Path) -> None:
    """Grafica distribución de longitud de rachas alcistas y bajistas."""

    plt.figure(figsize=(12, 6))

    for direction in ["up", "down"]:
        data = streaks.loc[streaks["direction"] == direction, "length_bars"]
        plt.hist(data, bins=range(1, int(streaks["length_bars"].max()) + 2), alpha=0.5, label=direction)

    plt.title("Distribución de rachas alcistas y bajistas")
    plt.xlabel("Longitud de racha en velas")
    plt.ylabel("Frecuencia")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_autocorrelation(autocorr: pd.DataFrame, path: Path) -> None:
    """Grafica autocorrelación por rezago."""

    plt.figure(figsize=(12, 6))
    plt.bar(autocorr["lag"], autocorr["autocorrelation"])
    plt.axhline(0, linewidth=1)
    plt.title("Autocorrelación de retornos logarítmicos")
    plt.xlabel("Rezago")
    plt.ylabel("Autocorrelación")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_volume_vs_volatility(df: pd.DataFrame, path: Path) -> None:
    """Grafica relación entre volumen y volatilidad proxy usando abs(log_return)."""

    sample = df[["volume", "abs_log_return"]].replace([np.inf, -np.inf], np.nan).dropna()

    if len(sample) > 10000:
        sample = sample.sample(10000, random_state=42)

    plt.figure(figsize=(10, 6))
    plt.scatter(np.log1p(sample["volume"]), sample["abs_log_return"], s=6, alpha=0.35)
    plt.title("Relación entre volumen y volatilidad absoluta")
    plt.xlabel("log(1 + volume)")
    plt.ylabel("abs(log_return)")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def build_markdown_report(
    config: EDAConfig,
    df: pd.DataFrame,
    summary: pd.DataFrame,
    vol_hour: pd.DataFrame,
    vol_day: pd.DataFrame,
    drawdown_events: pd.DataFrame,
    streak_summary: pd.DataFrame,
    persistence: pd.DataFrame,
    autocorr: pd.DataFrame,
    volume_corr: pd.DataFrame,
    anomalies: pd.DataFrame,
    annualization_factor: Optional[float],
) -> str:
    """Construye un reporte Markdown con interpretación cuantitativa automática."""

    log_stats = summary.loc[summary["metric"] == "log_return"].iloc[0]
    simple_stats = summary.loc[summary["metric"] == "simple_return"].iloc[0]

    max_hour_row = vol_hour.loc[vol_hour["volatility_std"].idxmax()]
    min_hour_row = vol_hour.loc[vol_hour["volatility_std"].idxmin()]

    max_day_row = vol_day.loc[vol_day["volatility_std"].idxmax()]
    min_day_row = vol_day.loc[vol_day["volatility_std"].idxmin()]

    if drawdown_events.empty:
        worst_drawdown_text = "No se detectaron eventos de drawdown con la lógica aplicada."
    else:
        worst = drawdown_events.iloc[0]
        worst_drawdown_text = (
            f"El peor drawdown detectado fue de {float(worst['max_drawdown']):.2%}, "
            f"con pico en {worst['peak_time_utc']} y valle en {worst['trough_time_utc']}."
        )

    strongest_autocorr = autocorr.reindex(autocorr["autocorrelation"].abs().sort_values(ascending=False).index).head(1).iloc[0]

    strongest_volume_corr = volume_corr.reindex(volume_corr["spearman_correlation"].abs().sort_values(ascending=False).index).head(1).iloc[0]

    up_persistence = persistence.loc[persistence["current_direction"] == "up", "persistence_probability"].iloc[0]
    down_persistence = persistence.loc[persistence["current_direction"] == "down", "persistence_probability"].iloc[0]
    overall_persistence = persistence.loc[persistence["current_direction"] == "overall", "persistence_probability"].iloc[0]

    report = f"""# Análisis Exploratorio Cuantitativo — {config.symbol} {config.interval}

## Alcance

Este reporte estudia estadísticamente el comportamiento de mercado antes de diseñar estrategias. No genera señales de compra ni venta.

## Dataset analizado

- Símbolo: `{config.symbol}`
- Intervalo: `{config.interval}`
- Filas analizadas: `{len(df):,}`
- Inicio UTC: `{df['open_time_utc'].min()}`
- Fin UTC: `{df['open_time_utc'].max()}`
- Factor de anualización usado: `{annualization_factor}`

## 1. Distribución de retornos

Los retornos simples tienen media `{float(simple_stats['mean']):.8f}` y desviación estándar `{float(simple_stats['std']):.8f}` por vela.

Interpretación cuantitativa: en datos intradía, la media por vela suele ser pequeña frente a la volatilidad. Por eso un investigador normalmente no busca ventaja en la media simple aislada, sino en estructura condicional, régimen, volatilidad, volumen o persistencia.

## 2. Retornos logarítmicos

Los retornos logarítmicos tienen:

- Media: `{float(log_stats['mean']):.8f}`
- Desviación estándar: `{float(log_stats['std']):.8f}`
- Asimetría: `{float(log_stats['skew']):.4f}`
- Curtosis excedente: `{float(log_stats['excess_kurtosis']):.4f}`

Interpretación cuantitativa: si la curtosis excedente es positiva y elevada, la distribución tiene colas más pesadas que una normal. Eso implica que los eventos extremos son relevantes y que una estrategia debe analizar riesgo de cola, no solo retorno promedio.

## 3. Volatilidad histórica

Se generó una volatilidad rolling con ventana `{config.rolling_vol_window}` velas.

Interpretación cuantitativa: los periodos de alta volatilidad suelen concentrarse en regímenes. Un investigador puede usar esta información para separar análisis por régimen de volatilidad antes de probar reglas.

## 4. Volatilidad por hora del día UTC

- Hora con mayor volatilidad: `{int(max_hour_row['hour_utc'])}:00 UTC`, volatilidad `{float(max_hour_row['volatility_std']):.8f}`
- Hora con menor volatilidad: `{int(min_hour_row['hour_utc'])}:00 UTC`, volatilidad `{float(min_hour_row['volatility_std']):.8f}`

Interpretación cuantitativa: diferencias por hora pueden sugerir efectos de sesión, liquidez o participación institucional/regional. No son señales por sí mismas; sirven para segmentar el comportamiento del mercado.

## 5. Volatilidad por día de semana UTC

- Día con mayor volatilidad: `{SPANISH_DAY_NAMES.get(str(max_day_row['day_of_week']), str(max_day_row['day_of_week']))}`, volatilidad `{float(max_day_row['volatility_std']):.8f}`
- Día con menor volatilidad: `{SPANISH_DAY_NAMES.get(str(min_day_row['day_of_week']), str(min_day_row['day_of_week']))}`, volatilidad `{float(min_day_row['volatility_std']):.8f}`

Interpretación cuantitativa: si ciertos días concentran mayor volatilidad, conviene evaluar si el fenómeno es estable por año y no solo producto de eventos aislados.

## 6. Drawdowns históricos

{worst_drawdown_text}

Interpretación cuantitativa: el drawdown muestra cuánto sufrió una posición pasiva desde máximos. Sirve como referencia de riesgo estructural del activo, aunque todavía no evalúa una estrategia.

## 7. Rachas alcistas y bajistas

Resumen de rachas generado en `tables/streak_summary.csv`.

Interpretación cuantitativa: las rachas ayudan a observar si el mercado tiende a alternar dirección rápidamente o si existen tramos persistentes. Por sí solas no prueban ventaja; deben compararse contra pruebas fuera de muestra.

## 8. Persistencia de tendencias

- Probabilidad de continuidad después de vela alcista: `{float(up_persistence):.4f}`
- Probabilidad de continuidad después de vela bajista: `{float(down_persistence):.4f}`
- Persistencia general de dirección: `{float(overall_persistence):.4f}`

Interpretación cuantitativa: valores cercanos a 0.50 sugieren poca persistencia direccional simple. Valores claramente superiores o inferiores a 0.50 pueden justificar análisis adicional, pero no bastan para operar.

## 9. Autocorrelación de retornos

La autocorrelación más fuerte en valor absoluto fue:

- Lag: `{int(strongest_autocorr['lag'])}`
- Autocorrelación: `{float(strongest_autocorr['autocorrelation']):.6f}`

Interpretación cuantitativa: autocorrelaciones pequeñas son comunes en retornos líquidos. Si aparece autocorrelación relevante, debe validarse por subperiodos, con costos y fuera de muestra.

## 10. Correlación entre volumen y volatilidad

La relación monotónica más fuerte por Spearman fue:

- `{strongest_volume_corr['x']}` vs `{strongest_volume_corr['y']}`
- Spearman: `{float(strongest_volume_corr['spearman_correlation']):.6f}`

Interpretación cuantitativa: una correlación positiva entre volumen y volatilidad puede indicar que el volumen es útil para identificar regímenes de actividad, no necesariamente dirección.

## Anomalías estadísticas candidatas

Total de anomalías candidatas detectadas: `{len(anomalies)}`.

Revisar `tables/anomalies.csv` para ver eventos extremos, colas pesadas, autocorrelación relevante o relación volumen-volatilidad marcada.

## Conclusión investigativa

Este análisis debe usarse para decidir cómo segmentar el mercado antes de diseñar estrategias. Las conclusiones más útiles no son señales, sino preguntas de investigación:

1. ¿Los retornos tienen colas pesadas y requieren gestión explícita de riesgo extremo?
2. ¿La volatilidad cambia por hora, día o régimen?
3. ¿Existe persistencia direccional o predomina reversión/ruido?
4. ¿El volumen ayuda a explicar volatilidad?
5. ¿Los resultados se mantienen por año o dependen de pocos eventos extremos?

Siguiente paso recomendado: crear un notebook o script de features cuantitativas, separando el dataset por año y por régimen de volatilidad para evitar conclusiones sobreajustadas.
"""

    return report


def main() -> None:
    """Orquesta todo el análisis exploratorio cuantitativo."""

    config = parse_args()

    paths = ensure_output_dirs(config.output_dir)

    annualization_factor = config.annualization_factor or infer_annualization_factor(config.interval)

    raw_df = load_dataset(config.input_path)

    validate_schema(raw_df)

    df = prepare_dataset(raw_df)

    df = add_return_and_volatility_features(df, config.rolling_vol_window)

    summary = compute_summary_statistics(df, annualization_factor)
    quantiles = compute_return_quantiles(df)
    vol_hour = compute_volatility_by_hour(df)
    vol_day = compute_volatility_by_day(df)
    drawdown_series = compute_drawdown_series(df)
    drawdown_events = compute_drawdown_events(drawdown_series)
    streaks, streak_summary = compute_streaks(df)
    persistence = compute_trend_persistence(df)
    autocorr = compute_autocorrelation(df, config.autocorr_lags)
    volume_corr = compute_volume_volatility_correlation(df)
    anomalies = detect_statistical_anomalies(df, summary, autocorr, volume_corr, drawdown_events)

    save_table(summary, paths["tables"] / "summary_statistics.csv")
    save_table(quantiles, paths["tables"] / "return_quantiles.csv")
    save_table(vol_hour, paths["tables"] / "volatility_by_hour_utc.csv")
    save_table(vol_day, paths["tables"] / "volatility_by_day_of_week_utc.csv")
    save_table(drawdown_series, paths["tables"] / "drawdown_series.csv")
    save_table(drawdown_events, paths["tables"] / "drawdown_events.csv")
    save_table(streaks, paths["tables"] / "streaks.csv")
    save_table(streak_summary, paths["tables"] / "streak_summary.csv")
    save_table(persistence, paths["tables"] / "trend_persistence.csv")
    save_table(autocorr, paths["tables"] / "autocorrelation_returns.csv")
    save_table(volume_corr, paths["tables"] / "volume_volatility_correlation.csv")
    save_table(anomalies, paths["tables"] / "anomalies.csv")

    enriched_output = paths["root"] / f"{config.symbol}_{config.interval}_quant_eda_enriched.parquet"
    df.to_parquet(enriched_output, index=False)

    plot_close_price(df, paths["charts"] / "01_close_price.png", config.symbol)
    plot_histogram(df["simple_return"], paths["charts"] / "02_simple_return_distribution.png", "Distribución de retornos simples", "simple_return")
    plot_histogram(df["log_return"], paths["charts"] / "03_log_return_distribution.png", "Distribución de retornos logarítmicos", "log_return")
    plot_rolling_volatility(df, paths["charts"] / "04_rolling_volatility.png", config.rolling_vol_window)
    plot_bar(vol_hour, "hour_utc", "volatility_std", paths["charts"] / "05_volatility_by_hour_utc.png", "Volatilidad por hora UTC", "Hora UTC", "Std log_return")
    plot_bar(vol_day, "day_of_week_es", "volatility_std", paths["charts"] / "06_volatility_by_day_utc.png", "Volatilidad por día de semana UTC", "Día", "Std log_return")
    plot_drawdown(drawdown_series, paths["charts"] / "07_drawdown_history.png")
    plot_streak_distribution(streaks, paths["charts"] / "08_streak_distribution.png")
    plot_autocorrelation(autocorr, paths["charts"] / "09_return_autocorrelation.png")
    plot_volume_vs_volatility(df, paths["charts"] / "10_volume_vs_volatility.png")

    report_md = build_markdown_report(
        config=config,
        df=df,
        summary=summary,
        vol_hour=vol_hour,
        vol_day=vol_day,
        drawdown_events=drawdown_events,
        streak_summary=streak_summary,
        persistence=persistence,
        autocorr=autocorr,
        volume_corr=volume_corr,
        anomalies=anomalies,
        annualization_factor=annualization_factor,
    )

    report_path = paths["reports"] / "quant_eda_report.md"

    report_path.write_text(report_md, encoding="utf-8")

    metadata = {
        "symbol": config.symbol,
        "interval": config.interval,
        "input_path": str(config.input_path),
        "output_dir": str(config.output_dir),
        "row_count": int(len(df)),
        "min_open_time_utc": str(df["open_time_utc"].min()),
        "max_open_time_utc": str(df["open_time_utc"].max()),
        "annualization_factor": annualization_factor,
        "rolling_vol_window": config.rolling_vol_window,
        "autocorr_lags": config.autocorr_lags,
        "generated_files": {
            "enriched_dataset": str(enriched_output),
            "tables_dir": str(paths["tables"]),
            "charts_dir": str(paths["charts"]),
            "markdown_report": str(report_path),
        },
        "note": "Este análisis no genera señales de compra o venta; solo estudia propiedades estadísticas del mercado.",
    }

    save_json(metadata, paths["reports"] / "quant_eda_metadata.json")

    print("\nAnálisis Exploratorio Cuantitativo terminado.")
    print(f"Tablas:   {paths['tables']}")
    print(f"Gráficos: {paths['charts']}")
    print(f"Reporte:  {report_path}")
    print(f"Dataset enriquecido: {enriched_output}")


if __name__ == "__main__":
    main()
