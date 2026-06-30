r"""
S2 — Construcción de Estrategias Basadas en Hipótesis
======================================================

Objetivo:
    Convertir hallazgos observables de S1(Q5) en reglas de trading evaluables,
    sin usar indicadores aleatorios ni optimizar parámetros.

Principio metodológico:
    Cada estrategia nace de una hipótesis documentada.
    El backtest no prueba que una estrategia funcione; solo produce evidencia.
    Una regla solo puede quedar como candidata si sobrevive evaluación out-of-sample,
    robustez temporal y Monte Carlo.

Ejecución recomendada en PowerShell:
    python s2_hypothesis_strategy_lab.py --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet --output-dir .\crypto_datalake\research\s2_hypothesis_strategies_fixed\BTCUSDT\1h --symbol BTCUSDT --interval 1h

Nota de versión [fixed_v2]:
    Una versión anterior de este script tenía un bug de leakage temporal:
    `oos_return` se calculaba igual a `full_return` (no usaba el tramo
    out-of-sample real) e `oos_benchmark_return` usaba el Buy & Hold del
    período completo. Esa salida quedó archivada en
    `crypto_datalake/research/_deprecated/s2_hypothesis_strategies_BUGGY_oos_leakage/`.
    Usa siempre `s2_hypothesis_strategies_fixed/` como carpeta de salida.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from q6_backtest_engine import (
    BacktestConfig as Q6Config,
    run_backtest_core,
    compute_metrics as q6_compute_metrics,
    compute_trade_metrics as q6_compute_trade_metrics,
    extract_trades as q6_extract_trades,
    block_bootstrap_indices,
    monte_carlo_block_bootstrap,
)


# =============================================================================
# Configuración
# =============================================================================


@dataclass(frozen=True)
class BacktestConfig:
    """Contiene los supuestos operativos comunes a todos los experimentos."""

    initial_capital: float = 10_000.0
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    annualization_factor: float = 8760.0
    split_ratio: float = 0.70
    monte_carlo_runs: int = 500
    monte_carlo_block_size: int = 24
    random_seed: int = 42
    min_trades_for_candidate: int = 30


@dataclass(frozen=True)
class HypothesisStrategy:
    """Define una estrategia como hipótesis investigable, no como recomendación."""

    strategy_id: str
    name: str
    hypothesis: str
    justification: str
    rule: str
    signal_function: Callable[[pd.DataFrame], pd.Series]


# =============================================================================
# Utilidades de datos
# =============================================================================


def ensure_dir(path: Path) -> Path:
    """Crea una carpeta si no existe y devuelve la misma ruta."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def collapse_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Colapsa columnas duplicadas tomando el primer valor no nulo por fila."""
    if not df.columns.duplicated().any():
        return df.copy()

    pieces: List[pd.Series] = []
    names: List[str] = []
    for name in pd.unique(df.columns):
        cols = df.loc[:, df.columns == name]
        if cols.shape[1] == 1:
            series = cols.iloc[:, 0]
        else:
            series = cols.bfill(axis=1).iloc[:, 0]
        pieces.append(series)
        names.append(str(name))

    result = pd.concat(pieces, axis=1)
    result.columns = names
    return result


def standardize_market_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Normaliza columnas mínimas necesarias para investigación y backtesting."""
    df = collapse_duplicate_columns(raw)
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    rename_map = {
        "open time": "open_time_utc",
        "open_time": "open_time_utc",
        "timestamp": "open_time_utc",
        "date": "open_time_utc",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    df = collapse_duplicate_columns(df)

    required = ["open_time_utc", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    df["open_time_utc"] = pd.to_datetime(df["open_time_utc"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open_time_utc", "open", "high", "low", "close", "volume"])
    df = df.sort_values("open_time_utc").drop_duplicates("open_time_utc").reset_index(drop=True)

    if "simple_return" not in df.columns:
        df["simple_return"] = df["close"].pct_change().fillna(0.0)
    else:
        df["simple_return"] = pd.to_numeric(df["simple_return"], errors="coerce").fillna(0.0)

    if "log_return" not in df.columns:
        df["log_return"] = np.log(df["close"] / df["close"].shift(1)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    else:
        df["log_return"] = pd.to_numeric(df["log_return"], errors="coerce").fillna(0.0)

    df["hour_utc"] = df["open_time_utc"].dt.hour.astype(int)
    df["day_of_week"] = df["open_time_utc"].dt.dayofweek.astype(int)
    df["day_name"] = df["open_time_utc"].dt.day_name()
    df["is_weekend"] = df["day_of_week"].isin([5, 6])

    df["range_pct"] = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    df["range_pct"] = df["range_pct"].fillna(0.0)

    # FIX: shift(1) antes del rolling para que el cuantil en tiempo t use solo
    # datos hasta t-1. Sin shift(1), range_pct[t] formaba parte de su propio umbral,
    # creando comparación auto-referencial. Coherente con Q5 que usa el mismo patrón.
    df["range_q20_168h"] = df["range_pct"].shift(1).rolling(168, min_periods=168).quantile(0.20)
    df["low_range_contraction_168h"] = df["range_pct"] < df["range_q20_168h"]
    df["low_range_contraction_168h"] = df["low_range_contraction_168h"].fillna(False)

    return df


# =============================================================================
# Señales basadas en hipótesis de S1(Q5)
# =============================================================================


def next_bar_day(df: pd.DataFrame) -> pd.Series:
    """Devuelve el día de semana de la siguiente vela."""
    return df["day_of_week"].shift(-1)


def next_bar_hour(df: pd.DataFrame) -> pd.Series:
    """Devuelve la hora UTC de la siguiente vela."""
    return df["hour_utc"].shift(-1)


def signal_tuesday_24h(df: pd.DataFrame) -> pd.Series:
    """Exposición long durante velas cuyo siguiente periodo pertenece a martes UTC."""
    return (next_bar_day(df) == 1).astype(float).fillna(0.0)


def signal_wednesday_6h_proxy(df: pd.DataFrame) -> pd.Series:
    """Exposición long durante velas cuyo siguiente periodo pertenece a miércoles UTC."""
    return (next_bar_day(df) == 2).astype(float).fillna(0.0)


def signal_hour_21_utc(df: pd.DataFrame) -> pd.Series:
    """Exposición long solo durante la vela que inicia a las 21:00 UTC."""
    return (next_bar_hour(df) == 21).astype(float).fillna(0.0)


def signal_avoid_thursday_buyhold(df: pd.DataFrame) -> pd.Series:
    """Exposición pasiva excepto durante velas cuyo siguiente periodo pertenece a jueves UTC."""
    return (next_bar_day(df) != 3).astype(float).fillna(0.0)


def signal_avoid_low_contraction_buyhold(df: pd.DataFrame) -> pd.Series:
    """Exposición pasiva excepto tras una vela de contracción de rango q20 168h."""
    return (~df["low_range_contraction_168h"]).astype(float).fillna(0.0)


def signal_avoid_thursday_and_low_contraction(df: pd.DataFrame) -> pd.Series:
    """Exposición pasiva filtrando jueves UTC y contracción de rango q20 168h."""
    not_thursday_next = next_bar_day(df) != 3
    not_contraction_now = ~df["low_range_contraction_168h"]
    return (not_thursday_next & not_contraction_now).astype(float).fillna(0.0)


def build_hypothesis_catalog() -> List[HypothesisStrategy]:
    """Construye el catálogo fijo de estrategias nacidas de S1(Q5)."""
    return [
        HypothesisStrategy(
            strategy_id="S2_H1_TUESDAY_24H_LONG",
            name="Exposición temporal en martes UTC",
            hypothesis="El patrón observado de martes con retorno forward 24h positivo podría reflejar un sesgo temporal explotable.",
            justification="S1(Q5) encontró diferencia positiva frente al baseline para martes a 24h. La regla evita indicadores aleatorios y prueba solo el patrón temporal observado.",
            rule="Mantener posición spot long durante velas que pertenecen a martes UTC; estar fuera el resto del tiempo.",
            signal_function=signal_tuesday_24h,
        ),
        HypothesisStrategy(
            strategy_id="S2_H2_WEDNESDAY_LONG",
            name="Exposición temporal en miércoles UTC",
            hypothesis="El patrón observado de miércoles con retorno forward 6h positivo podría reflejar un sesgo temporal intradía o de sesión.",
            justification="S1(Q5) encontró diferencia positiva frente al baseline para miércoles a 6h. Se prueba una regla simple de exposición durante miércoles sin optimizar horarios internos.",
            rule="Mantener posición spot long durante velas que pertenecen a miércoles UTC; estar fuera el resto del tiempo.",
            signal_function=signal_wednesday_6h_proxy,
        ),
        HypothesisStrategy(
            strategy_id="S2_H3_HOUR21_LONG",
            name="Exposición a la hora 21:00 UTC",
            hypothesis="La hora 21:00 UTC mostró retorno forward 1h positivo frente al baseline y podría capturar un micro-patrón horario.",
            justification="S1(Q5) encontró evidencia positiva para la hora 21 UTC, aunque débil/moderada. Se evalúa sin buscar otras horas.",
            rule="Entrar solo en la vela que inicia a las 21:00 UTC y salir en la siguiente vela.",
            signal_function=signal_hour_21_utc,
        ),
        HypothesisStrategy(
            strategy_id="S2_H4_AVOID_THURSDAY",
            name="Buy & Hold filtrando jueves UTC",
            hypothesis="Jueves mostró retornos forward negativos en 1h, 6h y 24h; evitar exposición ese día podría reducir deterioro del benchmark.",
            justification="S1(Q5) encontró evidencia negativa consistente para jueves. En spot long-only se traduce en filtro de riesgo, no en venta corta.",
            rule="Mantener exposición pasiva long excepto durante jueves UTC.",
            signal_function=signal_avoid_thursday_buyhold,
        ),
        HypothesisStrategy(
            strategy_id="S2_H5_AVOID_LOW_RANGE_CONTRACTION",
            name="Buy & Hold filtrando contracción de rango",
            hypothesis="La contracción de rango q20 168h mostró retornos forward negativos; evitar exposición tras esa condición podría mejorar riesgo.",
            justification="S1(Q5) encontró evidencia negativa para contracción de volatilidad a 6h y 24h. Se usa como filtro de riesgo sobre exposición pasiva.",
            rule="Mantener exposición pasiva long excepto justo después de velas con rango inferior al q20 rolling 168h.",
            signal_function=signal_avoid_low_contraction_buyhold,
        ),
        HypothesisStrategy(
            strategy_id="S2_H6_AVOID_THU_AND_CONTRACTION",
            name="Buy & Hold filtrando jueves y contracción",
            hypothesis="Combinar los dos patrones negativos observados podría reducir exposición a contextos desfavorables.",
            justification="Esta regla combina hallazgos negativos de S1(Q5), no parámetros optimizados: jueves negativo y contracción de rango negativa.",
            rule="Mantener exposición pasiva long excepto durante jueves UTC y excepto tras contracción de rango q20 168h.",
            signal_function=signal_avoid_thursday_and_low_contraction,
        ),
    ]


# =============================================================================
# Motor de backtest y métricas — delegado a Q6
# =============================================================================


def _make_q6_config(config: BacktestConfig) -> Q6Config:
    """Convierte S2 BacktestConfig → Q6Config."""
    return Q6Config(
        initial_capital=config.initial_capital,
        commission_rate=config.commission_rate,
        slippage_rate=config.slippage_rate,
        annualization_factor=int(config.annualization_factor),
        mc_runs=config.monte_carlo_runs,
        mc_block_size=config.monte_carlo_block_size,
        random_seed=config.random_seed,
    )


def run_backtest(df: pd.DataFrame, raw_signal: pd.Series, config: BacktestConfig) -> pd.DataFrame:
    """Wrapper: delega a Q6 run_backtest_core (open-to-open, sin lookahead).

    Q6 devuelve columnas: open_time_utc, open, ..., signal_raw, position,
    market_return, strategy_return, equity, drawdown, bh_return, bh_equity, bh_drawdown.
    Para compatibilidad con el resto de S2 se añaden aliases de las columnas previas.
    """
    bt = run_backtest_core(df, raw_signal, _make_q6_config(config))
    # Aliases para compatibilidad con evaluate_samples / yearly_robustness / plotting
    bt = bt.copy()
    bt["benchmark_return"] = bt["bh_return"]
    bt["benchmark_equity"] = bt["bh_equity"]
    bt["benchmark_drawdown"] = bt["bh_drawdown"]
    return bt


def extract_trades(bt: pd.DataFrame, config: BacktestConfig = None) -> pd.DataFrame:
    """Wrapper: delega a Q6 extract_trades."""
    cfg = _make_q6_config(config) if config is not None else _make_q6_config(BacktestConfig())
    return q6_extract_trades(bt, cfg)


def _rebase_slice(bt_slice: pd.DataFrame, col_return: str, initial_capital: float) -> pd.Series:
    """Reconstruye equity desde retornos locales para métricas correctas en sub-tramos."""
    ret = bt_slice[col_return].fillna(0.0)
    return initial_capital * (1.0 + ret).cumprod()


def calculate_metrics(bt: pd.DataFrame, config: BacktestConfig, prefix: str = "") -> Dict[str, float]:
    """Calcula métricas sobre un tramo (con equity re-basada). Delega a Q6."""
    q6_cfg = _make_q6_config(config)
    ret_col = "benchmark_return" if prefix == "benchmark_" else "strategy_return"
    label = "benchmark" if prefix == "benchmark_" else "strategy"

    local_bt = bt.copy().reset_index(drop=True)
    rebased = _rebase_slice(local_bt, ret_col, config.initial_capital)
    local_bt["_equity_rebased"] = rebased.to_numpy()
    m = q6_compute_metrics(local_bt, q6_cfg, return_col=ret_col, equity_col="_equity_rebased", label=label)
    if prefix != "benchmark_":
        local_bt["equity"] = rebased.to_numpy()
        local_bt["drawdown"] = (rebased / rebased.cummax() - 1.0).to_numpy()
        tm = q6_compute_trade_metrics(local_bt, q6_cfg, label=label)
        m.update(tm)
    # Remap prefixed keys to un-prefixed for internal use (Python ≥3.8 compatible)
    prefix_strip = f"{label}_"
    return {(k[len(prefix_strip):] if k.startswith(prefix_strip) else k): v for k, v in m.items()}


def evaluate_samples(bt: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Evalúa full, in-sample y out-of-sample de forma cronológica."""
    split_idx = int(len(bt) * config.split_ratio)
    samples = {
        "full": bt,
        "in_sample": bt.iloc[:split_idx].copy(),
        "out_of_sample": bt.iloc[split_idx:].copy(),
    }

    rows: List[Dict[str, object]] = []
    for sample_name, sample_df in samples.items():
        if sample_df.empty:
            continue
        strategy_metrics = calculate_metrics(sample_df, config)
        benchmark_metrics = calculate_metrics(sample_df, config, prefix="benchmark_")
        row: Dict[str, object] = {"sample": sample_name}
        row.update({f"strategy_{k}": v for k, v in strategy_metrics.items()})
        row.update({f"benchmark_{k}": v for k, v in benchmark_metrics.items()})
        row["excess_total_return_vs_benchmark"] = row["strategy_total_return"] - row["benchmark_total_return"]
        row["excess_sharpe_vs_benchmark"] = row["strategy_sharpe"] - row["benchmark_sharpe"]
        rows.append(row)
    return pd.DataFrame(rows)


def yearly_robustness(bt: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Calcula robustez por año calendario."""
    working = bt.copy()
    working["year"] = working["open_time_utc"].dt.year
    rows: List[Dict[str, object]] = []
    for year, year_df in working.groupby("year"):
        metrics = calculate_metrics(year_df, config)
        benchmark = calculate_metrics(year_df, config, prefix="benchmark_")
        row: Dict[str, object] = {"year": int(year)}
        row.update({f"strategy_{k}": v for k, v in metrics.items()})
        row.update({f"benchmark_{k}": v for k, v in benchmark.items()})
        row["beats_benchmark_total_return"] = row["strategy_total_return"] > row["benchmark_total_return"]
        row["positive_strategy_return"] = row["strategy_total_return"] > 0
        rows.append(row)
    return pd.DataFrame(rows)


def monte_carlo_summary(bt: pd.DataFrame, config: BacktestConfig) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Monte Carlo por block bootstrap — delega a Q6."""
    q6_cfg = _make_q6_config(config)
    s_ret = bt["strategy_return"].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)
    b_ret = bt["bh_return"].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)
    mc, summary_dict = monte_carlo_block_bootstrap(s_ret, b_ret, q6_cfg)

    # Remap Q6 mc columns to S2 names
    runs = mc.rename(columns={
        "strategy_total_return": "total_return",
        "strategy_max_drawdown": "max_drawdown",
        "strategy_sharpe": "sharpe",
        "mc_run": "run_id",
    })

    summary = pd.DataFrame([{
        "mc_total_return_p05": summary_dict["mc_total_return_p05"],
        "mc_total_return_p50": summary_dict["mc_total_return_p50"],
        "mc_total_return_p95": summary_dict["mc_total_return_p95"],
        "mc_max_drawdown_p05": summary_dict["mc_max_drawdown_p05"],
        "mc_max_drawdown_p50": summary_dict["mc_max_drawdown_p50"],
        "mc_sharpe_p05": summary_dict["mc_sharpe_p05"],
        "mc_sharpe_p50": summary_dict["mc_sharpe_p50"],
        "mc_probability_loss": summary_dict["mc_probability_loss"],
        "mc_probability_drawdown_worse_30pct": float((runs["max_drawdown"] < -0.30).mean()) if "max_drawdown" in runs.columns else float("nan"),
        "mc_probability_drawdown_worse_50pct": float((runs["max_drawdown"] < -0.50).mean()) if "max_drawdown" in runs.columns else float("nan"),
    }])
    return runs, summary


def classify_research_status(metrics_by_sample: pd.DataFrame, yearly: pd.DataFrame, mc: pd.DataFrame, config: BacktestConfig) -> Tuple[str, str]:
    """Clasifica una estrategia de forma conservadora."""
    oos = metrics_by_sample.loc[metrics_by_sample["sample"] == "out_of_sample"].iloc[0]
    full = metrics_by_sample.loc[metrics_by_sample["sample"] == "full"].iloc[0]

    oos_return = float(oos["strategy_total_return"])
    oos_sharpe = float(oos["strategy_sharpe"]) if not pd.isna(oos["strategy_sharpe"]) else -999.0
    oos_excess = float(oos["excess_total_return_vs_benchmark"])
    trades = float(full["strategy_trade_count"])
    positive_year_rate = float(yearly["positive_strategy_return"].mean()) if not yearly.empty else 0.0
    beat_year_rate = float(yearly["beats_benchmark_total_return"].mean()) if not yearly.empty else 0.0
    mc_loss_prob = float(mc.iloc[0].get("mc_probability_loss", 1.0)) if not mc.empty else 1.0

    reasons: List[str] = []
    if trades < config.min_trades_for_candidate:
        reasons.append("pocas operaciones")
    if oos_return <= 0:
        reasons.append("retorno out-of-sample no positivo")
    if oos_sharpe <= 0:
        reasons.append("Sharpe out-of-sample no positivo")
    if oos_excess <= 0:
        reasons.append("no supera Buy & Hold out-of-sample")
    if mc_loss_prob >= 0.50:
        reasons.append("Monte Carlo muestra probabilidad de pérdida >= 50%")
    if positive_year_rate < 0.50:
        reasons.append("menos de la mitad de años con retorno positivo")

    if not reasons and beat_year_rate >= 0.50:
        return "CANDIDATE_FOR_FURTHER_VALIDATION", "Supera filtros mínimos, pero requiere walk-forward, costos más altos y otros activos."
    if oos_return > 0 and oos_sharpe > 0 and trades >= config.min_trades_for_candidate:
        return "INCONCLUSIVE_NEEDS_ROBUSTNESS", "; ".join(reasons) if reasons else "Tiene evidencia parcial, pero no suficiente."
    return "REJECT_OR_REDESIGN", "; ".join(reasons)


# =============================================================================
# Reportes y gráficos
# =============================================================================


def plot_equity_curves(all_equity: pd.DataFrame, output_path: Path) -> None:
    """Grafica curvas de capital de todas las estrategias y benchmark."""
    plt.figure(figsize=(12, 7))
    for col in all_equity.columns:
        if col != "open_time_utc":
            plt.plot(all_equity["open_time_utc"], all_equity[col], label=col, linewidth=1.1)
    plt.title("S2 — Curvas de capital por hipótesis")
    plt.xlabel("Fecha UTC")
    plt.ylabel("Capital")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=140)
    plt.close()


def plot_drawdowns(all_dd: pd.DataFrame, output_path: Path) -> None:
    """Grafica drawdowns de todas las estrategias y benchmark."""
    plt.figure(figsize=(12, 7))
    for col in all_dd.columns:
        if col != "open_time_utc":
            plt.plot(all_dd["open_time_utc"], all_dd[col], label=col, linewidth=1.1)
    plt.title("S2 — Drawdowns por hipótesis")
    plt.xlabel("Fecha UTC")
    plt.ylabel("Drawdown")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=140)
    plt.close()


def format_float(value: object) -> str:
    """Formatea números para Markdown."""
    if value is None or pd.isna(value):
        return "nan"
    try:
        return f"{float(value):.6f}"
    except Exception:
        return str(value)


def dataframe_to_markdown(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Convierte un DataFrame a tabla Markdown sin depender de tabulate."""
    if df.empty:
        return "_Sin filas._"
    display = df.head(max_rows).copy()
    headers = list(display.columns)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in display.iterrows():
        values = [format_float(row[h]) if isinstance(row[h], (float, int, np.floating, np.integer)) or pd.isna(row[h]) else str(row[h]) for h in headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_report(
    output_path: Path,
    symbol: str,
    interval: str,
    config: BacktestConfig,
    hypotheses: List[HypothesisStrategy],
    summary: pd.DataFrame,
    catalog_df: pd.DataFrame,
) -> None:
    """Genera reporte Markdown del laboratorio S2."""
    lines: List[str] = []
    lines.append(f"# S2 — Estrategias basadas en hipótesis — {symbol} {interval}")
    lines.append("")
    lines.append("## Principio rector")
    lines.append("")
    lines.append("Este reporte no declara que una estrategia funcione por un backtest. Cada regla nace de un hallazgo observado en S1(Q5), se evalúa con costos, se separa in-sample/out-of-sample y se clasifica de forma conservadora.")
    lines.append("")
    lines.append("## Configuración")
    lines.append("")
    lines.append(f"- Capital inicial: `{config.initial_capital}`")
    lines.append(f"- Comisión: `{config.commission_rate}`")
    lines.append(f"- Slippage: `{config.slippage_rate}`")
    lines.append(f"- Split in-sample: `{config.split_ratio:.2%}`")
    lines.append(f"- Monte Carlo runs: `{config.monte_carlo_runs}`")
    lines.append(f"- Monte Carlo block size: `{config.monte_carlo_block_size}` velas")
    lines.append("")
    lines.append("## Hipótesis y reglas")
    lines.append("")
    for h in hypotheses:
        lines.append(f"### {h.strategy_id} — {h.name}")
        lines.append(f"- Hipótesis: {h.hypothesis}")
        lines.append(f"- Justificación: {h.justification}")
        lines.append(f"- Regla: {h.rule}")
        lines.append("")
    lines.append("## Resumen de resultados")
    lines.append("")
    cols = [
        "strategy_id",
        "research_status",
        "full_return",
        "oos_return",
        "oos_sharpe",
        "oos_max_drawdown",
        "oos_excess_return_vs_benchmark",
        "trade_count",
        "positive_year_rate",
        "beat_benchmark_year_rate",
        "mc_probability_loss",
        "rejection_reasons",
    ]
    lines.append(dataframe_to_markdown(summary[cols], max_rows=50))
    lines.append("")
    lines.append("## Lectura disciplinada")
    lines.append("")
    lines.append("Una estrategia solo puede avanzar si muestra evidencia fuera de muestra, suficientes operaciones, estabilidad temporal y Monte Carlo aceptable. Si una regla no supera Buy & Hold out-of-sample, no debe considerarse candidata aunque parezca atractiva in-sample.")
    lines.append("")
    lines.append("## Archivos generados")
    lines.append("")
    lines.append("- `tables/hypotheses_catalog.csv`")
    lines.append("- `tables/strategy_summary.csv`")
    lines.append("- `tables/metrics_by_sample.csv`")
    lines.append("- `tables/yearly_robustness.csv`")
    lines.append("- `tables/monte_carlo_summary.csv`")
    lines.append("- `tables/trades.csv`")
    lines.append("- `charts/equity_curves.png`")
    lines.append("- `charts/drawdowns.png`")
    lines.append("")
    lines.append("## Siguiente validación obligatoria")
    lines.append("")
    lines.append("1. Walk-forward real con reglas congeladas.")
    lines.append("2. Costos más altos que el escenario base.")
    lines.append("3. Prueba por subperiodos de mercado alcista/bajista.")
    lines.append("4. Repetición en otros activos líquidos.")
    lines.append("5. Congelar hipótesis antes de mirar nuevos datos.")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# Orquestación
# =============================================================================


def run_lab(args: argparse.Namespace) -> None:
    """Ejecuta el laboratorio completo S2."""
    input_path = Path(args.input)
    output_dir = ensure_dir(Path(args.output_dir))
    tables_dir = ensure_dir(output_dir / "tables")
    charts_dir = ensure_dir(output_dir / "charts")
    reports_dir = ensure_dir(output_dir / "reports")
    mc_dir = ensure_dir(output_dir / "monte_carlo")

    config = BacktestConfig(
        initial_capital=args.initial_capital,
        commission_rate=args.commission_rate,
        slippage_rate=args.slippage_rate,
        annualization_factor=args.annualization_factor,
        split_ratio=args.split_ratio,
        monte_carlo_runs=args.monte_carlo_runs,
        monte_carlo_block_size=args.monte_carlo_block_size,
        random_seed=args.random_seed,
        min_trades_for_candidate=args.min_trades_for_candidate,
    )

    raw = pd.read_parquet(input_path)
    data = standardize_market_data(raw)
    hypotheses = build_hypothesis_catalog()

    catalog_rows = [
        {
            "strategy_id": h.strategy_id,
            "name": h.name,
            "hypothesis": h.hypothesis,
            "justification": h.justification,
            "rule": h.rule,
        }
        for h in hypotheses
    ]
    catalog_df = pd.DataFrame(catalog_rows)
    catalog_df.to_csv(tables_dir / "hypotheses_catalog.csv", index=False, encoding="utf-8")

    all_metrics: List[pd.DataFrame] = []
    all_yearly: List[pd.DataFrame] = []
    all_mc_summary: List[pd.DataFrame] = []
    all_trades: List[pd.DataFrame] = []
    summary_rows: List[Dict[str, object]] = []

    # Q6 elimina la última barra (sin next_open para calcular market_return),
    # por lo que bt tiene n-1 filas. Inicializar con iloc[:-1] para alinear longitudes.
    equity_curves = pd.DataFrame({"open_time_utc": data["open_time_utc"].iloc[:-1].reset_index(drop=True)})
    drawdown_curves = pd.DataFrame({"open_time_utc": data["open_time_utc"].iloc[:-1].reset_index(drop=True)})
    benchmark_added = False

    for h in hypotheses:
        signal = h.signal_function(data)
        bt = run_backtest(data, signal, config)

        metrics = evaluate_samples(bt, config)
        metrics.insert(0, "strategy_id", h.strategy_id)
        all_metrics.append(metrics)

        yearly = yearly_robustness(bt, config)
        yearly.insert(0, "strategy_id", h.strategy_id)
        all_yearly.append(yearly)

        trades = extract_trades(bt, config)
        if not trades.empty:
            trades.insert(0, "strategy_id", h.strategy_id)
        all_trades.append(trades)

        mc_runs, mc_summary = monte_carlo_summary(bt, config)
        mc_runs.to_csv(mc_dir / f"{h.strategy_id}_monte_carlo_runs.csv", index=False, encoding="utf-8")
        mc_summary.insert(0, "strategy_id", h.strategy_id)
        all_mc_summary.append(mc_summary)

        status, reasons = classify_research_status(metrics, yearly, mc_summary, config)
        full = metrics.loc[metrics["sample"] == "full"].iloc[0]
        oos = metrics.loc[metrics["sample"] == "out_of_sample"].iloc[0]
        summary_rows.append(
            {
                "strategy_id": h.strategy_id,
                "research_status": status,
                "full_return": full["strategy_total_return"],
                "full_sharpe": full["strategy_sharpe"],
                "full_max_drawdown": full["strategy_max_drawdown"],
                "oos_return": oos["strategy_total_return"],
                "oos_sharpe": oos["strategy_sharpe"],
                "oos_max_drawdown": oos["strategy_max_drawdown"],
                "oos_benchmark_return": oos["benchmark_total_return"],
                "oos_excess_return_vs_benchmark": oos["excess_total_return_vs_benchmark"],
                "trade_count": full["strategy_trade_count"],
                "positive_year_rate": yearly["positive_strategy_return"].mean() if not yearly.empty else np.nan,
                "beat_benchmark_year_rate": yearly["beats_benchmark_total_return"].mean() if not yearly.empty else np.nan,
                "mc_probability_loss": mc_summary.iloc[0].get("mc_probability_loss", np.nan),
                "mc_total_return_p50": mc_summary.iloc[0].get("mc_total_return_p50", np.nan),
                "mc_max_drawdown_p50": mc_summary.iloc[0].get("mc_max_drawdown_p50", np.nan),
                "rejection_reasons": reasons,
            }
        )

        equity_curves[h.strategy_id] = bt["equity"].to_numpy()
        drawdown_curves[h.strategy_id] = bt["drawdown"].to_numpy()
        if not benchmark_added:
            equity_curves["BUY_AND_HOLD_BENCHMARK"] = bt["benchmark_equity"].to_numpy()
            drawdown_curves["BUY_AND_HOLD_BENCHMARK"] = bt["benchmark_drawdown"].to_numpy()
            benchmark_added = True

    metrics_df = pd.concat(all_metrics, ignore_index=True)
    yearly_df = pd.concat(all_yearly, ignore_index=True)
    mc_summary_df = pd.concat(all_mc_summary, ignore_index=True)
    trades_df = pd.concat([t for t in all_trades if not t.empty], ignore_index=True) if any(not t.empty for t in all_trades) else pd.DataFrame()
    summary_df = pd.DataFrame(summary_rows).sort_values(["research_status", "oos_excess_return_vs_benchmark"], ascending=[True, False])

    summary_df.to_csv(tables_dir / "strategy_summary.csv", index=False, encoding="utf-8")
    metrics_df.to_csv(tables_dir / "metrics_by_sample.csv", index=False, encoding="utf-8")
    yearly_df.to_csv(tables_dir / "yearly_robustness.csv", index=False, encoding="utf-8")
    mc_summary_df.to_csv(tables_dir / "monte_carlo_summary.csv", index=False, encoding="utf-8")
    trades_df.to_csv(tables_dir / "trades.csv", index=False, encoding="utf-8")
    equity_curves.to_csv(tables_dir / "equity_curves.csv", index=False, encoding="utf-8")
    drawdown_curves.to_csv(tables_dir / "drawdown_curves.csv", index=False, encoding="utf-8")

    plot_equity_curves(equity_curves, charts_dir / "equity_curves.png")
    plot_drawdowns(drawdown_curves, charts_dir / "drawdowns.png")

    metadata = {
        "symbol": args.symbol,
        "interval": args.interval,
        "input": str(input_path),
        "rows": int(len(data)),
        "start_utc": str(data["open_time_utc"].min()),
        "end_utc": str(data["open_time_utc"].max()),
        "config": config.__dict__,
        "strategies_evaluated": [h.strategy_id for h in hypotheses],
    }
    (reports_dir / "s2_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(reports_dir / "s2_hypothesis_strategy_report.md", args.symbol, args.interval, config, hypotheses, summary_df, catalog_df)

    print("\nS2 — Laboratorio de estrategias basadas en hipótesis terminado. [fixed_v2]")
    print(f"Estrategias evaluadas: {len(hypotheses)}")
    print(f"Tablas:   {tables_dir}")
    print(f"Gráficos: {charts_dir}")
    print(f"Reporte:  {reports_dir / 's2_hypothesis_strategy_report.md'}")
    print("\nRecuerda: una estrategia solo avanza si demuestra robustez, no solo retorno.")


def parse_args() -> argparse.Namespace:
    """Lee argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(description="S2 — Construcción de estrategias basadas en hipótesis.")
    parser.add_argument("--input", required=True, help="Parquet enriquecido de Q2/Q5.")
    parser.add_argument("--output-dir", required=True, help="Carpeta de salida.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo analizado.")
    parser.add_argument("--interval", default="1h", help="Intervalo analizado.")
    parser.add_argument("--initial-capital", type=float, default=10_000.0, help="Capital inicial.")
    parser.add_argument("--commission-rate", type=float, default=0.001, help="Comisión por notional operado. 0.001 = 0.10%%.")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="Slippage por notional operado. 0.0005 = 0.05%%.")
    parser.add_argument("--annualization-factor", type=float, default=8760.0, help="Periodos por año para velas 1h.")
    parser.add_argument("--split-ratio", type=float, default=0.70, help="Porcentaje cronológico para in-sample.")
    parser.add_argument("--monte-carlo-runs", type=int, default=500, help="Cantidad de corridas Monte Carlo.")
    parser.add_argument("--monte-carlo-block-size", type=int, default=24, help="Tamaño de bloque Monte Carlo en velas.")
    parser.add_argument("--random-seed", type=int, default=42, help="Semilla aleatoria reproducible.")
    parser.add_argument("--min-trades-for-candidate", type=int, default=30, help="Mínimo de operaciones para considerar candidata.")
    return parser.parse_args()


def main() -> None:
    """Punto de entrada."""
    args = parse_args()
    run_lab(args)


if __name__ == "__main__":
    main()
