r"""
Q4 — Laboratorio de Investigación Cuantitativa
Autor: ChatGPT para Orlando

Objetivo:
    Evaluar hipótesis de mercado con disciplina cuantitativa, evitando concluir
    que una estrategia "funciona" solo porque un backtest salió bien.

Uso típico en PowerShell:
    python q4_quant_research_lab.py --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet --output-dir .\crypto_datalake\research\q4_lab\BTCUSDT\1h --symbol BTCUSDT --interval 1h

Dependencias:
    pandas, numpy, pyarrow, matplotlib
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from q6_backtest_engine import (
    BacktestConfig as Q6Config,
    run_backtest_core,
    compute_metrics as q6_compute_metrics,
    compute_trade_metrics as q6_compute_trade_metrics,
    extract_trades as q6_extract_trades,
    block_bootstrap_indices,
    monte_carlo_block_bootstrap,
    load_and_standardize,
)

warnings.filterwarnings("ignore", category=RuntimeWarning)


# =============================================================================
# CONFIGURACIÓN GENERAL
# =============================================================================


@dataclass(frozen=True)
class LabConfig:
    """Configuración reproducible del laboratorio."""

    symbol: str
    interval: str
    initial_capital: float = 10_000.0
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    annualization_factor: float = 8760.0
    in_sample_fraction: float = 0.70
    monte_carlo_runs: int = 500
    monte_carlo_block_size: int = 24
    random_seed: int = 42
    max_candidates_for_mc: int = 12
    min_trades_required: int = 30


@dataclass(frozen=True)
class HypothesisSpec:
    """Define una hipótesis investigable."""

    hypothesis_id: str
    name: str
    statement: str
    experiment_design: str
    variables: List[str]
    parameter_grid: Dict[str, List[Any]]
    signal_function_name: str


# =============================================================================
# UTILIDADES DE ARCHIVOS Y DATOS
# =============================================================================


def ensure_dir(path: Path) -> Path:
    """Crea una carpeta si no existe y devuelve el Path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Guarda JSON con indentación para auditoría humana."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)


# load_market_data + standardize_market_data → migradas a Q6 (load_and_standardize).
# Exportadas por compatibilidad con importaciones externas.
def load_market_data(input_path: Path) -> pd.DataFrame:  # pragma: no cover
    return load_and_standardize(input_path)


def standardize_market_data(df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover
    return df  # Q6 normaliza en load_and_standardize; aquí llega ya normalizado.


# =============================================================================
# FEATURE ENGINEERING PARA EL LABORATORIO
# =============================================================================


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide evitando infinitos cuando el denominador es cero."""
    out = numerator / denominator.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def zscore(series: pd.Series, window: int) -> pd.Series:
    """Calcula z-score rolling sin usar datos futuros."""
    mean = series.rolling(window, min_periods=max(5, window // 3)).mean()
    std = series.rolling(window, min_periods=max(5, window // 3)).std(ddof=0)
    return safe_divide(series - mean, std)


def add_research_features(df: pd.DataFrame) -> pd.DataFrame:
    """Crea variables cuantitativas para hipótesis de régimen, momentum y reversión."""
    result = df.copy()

    result["simple_return_1h"] = result["close"].pct_change()
    result["log_return_1h"] = np.log(result["close"] / result["close"].shift(1))
    result["abs_log_return_1h"] = result["log_return_1h"].abs()
    result["range_pct"] = safe_divide(result["high"] - result["low"], result["close"])
    result["body_pct"] = safe_divide((result["close"] - result["open"]).abs(), result["close"])

    result["hour_utc"] = result["open_time_utc"].dt.hour
    result["day_of_week_utc"] = result["open_time_utc"].dt.dayofweek
    result["is_weekend"] = result["day_of_week_utc"].isin([5, 6]).astype(int)

    for window in [3, 6, 12, 24, 48, 72, 168]:
        result[f"return_{window}h"] = result["close"].pct_change(window)
        result[f"log_return_{window}h"] = np.log(result["close"] / result["close"].shift(window))

    for window in [24, 72, 168, 336]:
        result[f"rolling_vol_{window}h"] = result["log_return_1h"].rolling(
            window, min_periods=max(5, window // 3)
        ).std(ddof=0)
        result[f"volume_zscore_{window}h"] = zscore(np.log1p(result["volume"]), window)
        result[f"range_zscore_{window}h"] = zscore(result["range_pct"], window)
        result[f"rolling_high_prev_{window}h"] = result["high"].rolling(
            window, min_periods=max(5, window // 3)
        ).max().shift(1)
        result[f"rolling_low_prev_{window}h"] = result["low"].rolling(
            window, min_periods=max(5, window // 3)
        ).min().shift(1)

    for window in [20, 50, 100, 200]:
        # FIX: min_periods=window para que SMA-200 no emita valores con solo 66 barras.
        result[f"sma_{window}"] = result["close"].rolling(window, min_periods=window).mean()
        result[f"sma_distance_{window}"] = safe_divide(result["close"] - result[f"sma_{window}"], result[f"sma_{window}"])

    # FIX: régimen calculado con cuantiles rolling históricos (shift(1)) para evitar
    # lookahead bias. pd.qcut global usaba datos futuros al clasificar cada fila.
    _vol_q33 = result["rolling_vol_24h"].shift(1).rolling(168, min_periods=50).quantile(0.33)
    _vol_q67 = result["rolling_vol_24h"].shift(1).rolling(168, min_periods=50).quantile(0.67)
    result["volatility_regime_24h"] = np.where(
        result["rolling_vol_24h"] <= _vol_q33, "low",
        np.where(result["rolling_vol_24h"] <= _vol_q67, "mid", "high")
    )

    _vvol_q33 = result["volume_zscore_168h"].shift(1).rolling(168, min_periods=50).quantile(0.33)
    _vvol_q67 = result["volume_zscore_168h"].shift(1).rolling(168, min_periods=50).quantile(0.67)
    result["volume_regime_168h"] = np.where(
        result["volume_zscore_168h"] <= _vvol_q33, "low",
        np.where(result["volume_zscore_168h"] <= _vvol_q67, "mid", "high")
    )

    result = result.replace([np.inf, -np.inf], np.nan)
    return result


# =============================================================================
# HIPÓTESIS Y GENERADORES DE SEÑALES
# =============================================================================


def signal_momentum_volume(df: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Hipótesis: momentum multi-hora puede ser más informativo cuando el volumen confirma actividad."""
    lookback = int(params["lookback"])
    volume_window = int(params["volume_window"])
    volume_z = float(params["volume_z"])
    min_return = float(params["min_return"])
    use_trend_filter = bool(params["use_trend_filter"])
    avoid_weekend = bool(params["avoid_weekend"])

    signal = df[f"return_{lookback}h"] > min_return
    signal &= df[f"volume_zscore_{volume_window}h"] > volume_z

    if use_trend_filter:
        signal &= df["close"] > df["sma_200"]
    if avoid_weekend:
        signal &= df["is_weekend"] == 0

    return signal.astype(float).fillna(0.0)


def signal_mean_reversion_low_vol(df: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Hipótesis: caídas cortas pueden revertir mejor en regímenes de baja volatilidad."""
    lookback = int(params["lookback"])
    drop_threshold = float(params["drop_threshold"])
    vol_window = int(params["vol_window"])
    max_vol_quantile = float(params["max_vol_quantile"])
    avoid_weekend = bool(params["avoid_weekend"])

    vol_limit = df[f"rolling_vol_{vol_window}h"].quantile(max_vol_quantile)
    signal = df[f"return_{lookback}h"] < -abs(drop_threshold)
    signal &= df[f"rolling_vol_{vol_window}h"] <= vol_limit

    if avoid_weekend:
        signal &= df["is_weekend"] == 0

    return signal.astype(float).fillna(0.0)


def signal_breakout_high_volume(df: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Hipótesis: rupturas de rango solo son investigables cuando hay expansión de volumen."""
    breakout_window = int(params["breakout_window"])
    volume_window = int(params["volume_window"])
    volume_z = float(params["volume_z"])
    trend_sma = int(params["trend_sma"])
    avoid_weekend = bool(params["avoid_weekend"])

    signal = df["close"] > df[f"rolling_high_prev_{breakout_window}h"]
    signal &= df[f"volume_zscore_{volume_window}h"] > volume_z
    signal &= df["close"] > df[f"sma_{trend_sma}"]

    if avoid_weekend:
        signal &= df["is_weekend"] == 0

    return signal.astype(float).fillna(0.0)


def signal_trend_risk_filter(df: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Hipótesis: exposición pasiva filtrada por tendencia puede reducir drawdown estructural."""
    trend_sma = int(params["trend_sma"])
    confirm_return_window = int(params["confirm_return_window"])
    min_confirm_return = float(params["min_confirm_return"])
    avoid_weekend = bool(params["avoid_weekend"])

    signal = df["close"] > df[f"sma_{trend_sma}"]
    signal &= df[f"return_{confirm_return_window}h"] > min_confirm_return

    if avoid_weekend:
        signal &= df["is_weekend"] == 0

    return signal.astype(float).fillna(0.0)


SIGNAL_FUNCTIONS: Dict[str, Callable[[pd.DataFrame, Dict[str, Any]], pd.Series]] = {
    "signal_momentum_volume": signal_momentum_volume,
    "signal_mean_reversion_low_vol": signal_mean_reversion_low_vol,
    "signal_breakout_high_volume": signal_breakout_high_volume,
    "signal_trend_risk_filter": signal_trend_risk_filter,
}


def build_hypotheses() -> List[HypothesisSpec]:
    """Define hipótesis iniciales basadas en Q2: volumen, volatilidad, horarios y baja persistencia."""
    return [
        HypothesisSpec(
            hypothesis_id="H1",
            name="Momentum condicionado por volumen",
            statement="El momentum multi-hora podría tener más valor cuando el volumen confirma un régimen de actividad alta.",
            experiment_design="Comparar variantes de lookback, umbral de retorno, z-score de volumen, filtro de tendencia y exclusión de fin de semana.",
            variables=["return_Nh", "volume_zscore", "sma_200", "is_weekend"],
            parameter_grid={
                "lookback": [6, 12, 24, 48],
                "volume_window": [24, 168],
                "volume_z": [0.0, 0.5, 1.0],
                "min_return": [0.0, 0.005, 0.01],
                "use_trend_filter": [False, True],
                "avoid_weekend": [False, True],
            },
            signal_function_name="signal_momentum_volume",
        ),
        HypothesisSpec(
            hypothesis_id="H2",
            name="Reversión corta en baja volatilidad",
            statement="La reversión de caídas cortas podría ser más estable en regímenes de baja volatilidad que en alta volatilidad.",
            experiment_design="Evaluar caídas previas por lookback bajo distintos límites de volatilidad y filtros de fin de semana.",
            variables=["return_Nh", "rolling_vol", "is_weekend"],
            parameter_grid={
                "lookback": [3, 6, 12, 24],
                "drop_threshold": [0.005, 0.01, 0.02],
                "vol_window": [24, 72, 168],
                "max_vol_quantile": [0.33, 0.50, 0.66],
                "avoid_weekend": [False, True],
            },
            signal_function_name="signal_mean_reversion_low_vol",
        ),
        HypothesisSpec(
            hypothesis_id="H3",
            name="Ruptura de rango con volumen",
            statement="Las rupturas de máximos previos podrían ser menos ruidosas cuando están acompañadas por expansión de volumen.",
            experiment_design="Probar ventanas de breakout y volumen, con filtro de tendencia y exclusión opcional de fin de semana.",
            variables=["rolling_high_prev", "volume_zscore", "sma_trend", "is_weekend"],
            parameter_grid={
                "breakout_window": [24, 72, 168],
                "volume_window": [24, 168],
                "volume_z": [0.0, 0.5, 1.0, 1.5],
                "trend_sma": [50, 100, 200],
                "avoid_weekend": [False, True],
            },
            signal_function_name="signal_breakout_high_volume",
        ),
        HypothesisSpec(
            hypothesis_id="H4",
            name="Filtro de riesgo para exposición pasiva",
            statement="Un filtro de tendencia podría reducir drawdowns de exposición pasiva, aunque quizá sacrifique retorno.",
            experiment_design="Comparar exposición long-only con distintas medias móviles y confirmación de retorno agregado.",
            variables=["close_vs_sma", "return_Nh", "is_weekend"],
            parameter_grid={
                "trend_sma": [50, 100, 200],
                "confirm_return_window": [12, 24, 72, 168],
                "min_confirm_return": [-0.02, 0.0, 0.02],
                "avoid_weekend": [False, True],
            },
            signal_function_name="signal_trend_risk_filter",
        ),
    ]


def expand_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Convierte un dict de listas en todas las combinaciones de parámetros."""
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


# =============================================================================
# MOTOR DE BACKTEST Y MÉTRICAS — delegado a Q6
# =============================================================================


def _make_q6_config(config: LabConfig) -> Q6Config:
    """Convierte LabConfig → Q6Config para usar el motor unificado."""
    return Q6Config(
        initial_capital=config.initial_capital,
        commission_rate=config.commission_rate,
        slippage_rate=config.slippage_rate,
        annualization_factor=int(config.annualization_factor),
        mc_runs=config.monte_carlo_runs,
        mc_block_size=config.monte_carlo_block_size,
        random_seed=config.random_seed,
    )


def run_backtest(df: pd.DataFrame, signal: pd.Series, config: LabConfig) -> pd.DataFrame:
    """Wrapper: delega a Q6 run_backtest_core (open-to-open, sin lookahead)."""
    return run_backtest_core(df, signal, _make_q6_config(config))


def _slice_metrics_q6(
    backtest: pd.DataFrame, q6_cfg: Q6Config, start: int, end: int, label: str = "strategy"
) -> Dict[str, float]:
    """Calcula métricas Q6 sobre un tramo, rebasando equity a initial_capital."""
    sample = backtest.iloc[start:end].copy().reset_index(drop=True)
    if sample.empty:
        return {}
    # Re-base: los retornos del tramo se acumulan desde capital inicial.
    period_ret = sample["strategy_return"].fillna(0.0)
    rebased = q6_cfg.initial_capital * (1.0 + period_ret).cumprod()
    sample["equity"] = rebased.to_numpy()
    sample["drawdown"] = (rebased / rebased.cummax() - 1.0).to_numpy()
    m = q6_compute_metrics(sample, q6_cfg, label=label)
    tm = q6_compute_trade_metrics(sample, q6_cfg, label=label)
    m.update(tm)
    return m


def _slice_bh_metrics_q6(
    backtest: pd.DataFrame, q6_cfg: Q6Config, start: int, end: int, label: str = "buy_hold"
) -> Dict[str, float]:
    """Calcula métricas Buy & Hold sobre un tramo (usa bh_return/bh_equity de Q6)."""
    sample = backtest.iloc[start:end].copy().reset_index(drop=True)
    if sample.empty:
        return {}
    bh_ret = sample["bh_return"].fillna(0.0)
    rebased = q6_cfg.initial_capital * (1.0 + bh_ret).cumprod()
    sample["bh_equity_rebased"] = rebased.to_numpy()
    return q6_compute_metrics(
        sample, q6_cfg, return_col="bh_return", equity_col="bh_equity_rebased", label=label
    )


def slice_metrics(backtest: pd.DataFrame, config: LabConfig, start: int, end: int) -> Dict[str, float]:
    """Compatibilidad: delega a Q6."""
    return _slice_metrics_q6(backtest, _make_q6_config(config), start, end, label="strategy")


def evaluate_backtest(backtest: pd.DataFrame, config: LabConfig) -> Dict[str, Dict[str, float]]:
    """Evalúa full, in-sample y out-of-sample con Q6 (benchmark embebido en backtest)."""
    n = len(backtest)
    split_idx = int(n * config.in_sample_fraction)
    q6_cfg = _make_q6_config(config)
    windows = {
        "full": (0, n),
        "in_sample": (0, split_idx),
        "out_of_sample": (split_idx, n),
    }
    output: Dict[str, Dict[str, float]] = {}
    for name, (start, end) in windows.items():
        strategy_metrics = _slice_metrics_q6(backtest, q6_cfg, start, end, label="strategy")
        benchmark_metrics = _slice_bh_metrics_q6(backtest, q6_cfg, start, end, label="buy_hold")
        merged = {f"strategy_{k}": v for k, v in strategy_metrics.items()}
        merged.update({f"buy_hold_{k}": v for k, v in benchmark_metrics.items()})
        output[name] = merged
    return output


def metrics_to_flat_record(
    hypothesis: HypothesisSpec,
    variant_id: str,
    params: Dict[str, Any],
    metrics_by_sample: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    """Convierte métricas por muestra a una fila plana para CSV."""
    record: Dict[str, Any] = {
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_name": hypothesis.name,
        "variant_id": variant_id,
        "params_json": json.dumps(params, ensure_ascii=False, sort_keys=True),
    }
    for sample_name, metrics in metrics_by_sample.items():
        for key, value in metrics.items():
            record[f"{sample_name}_{key}"] = value
    return record


# =============================================================================
# ROBUSTEZ, MONTE CARLO Y SOBREAJUSTE
# =============================================================================


def yearly_robustness(backtest: pd.DataFrame, config: LabConfig) -> pd.DataFrame:
    """Calcula desempeño por año calendario UTC — delega métricas a Q6."""
    q6_cfg = _make_q6_config(config)
    temp = backtest.copy()
    temp["year"] = temp["open_time_utc"].dt.year
    rows: List[Dict[str, Any]] = []

    for year, group in temp.groupby("year"):
        group = group.reset_index(drop=True)
        ret = group["strategy_return"].fillna(0.0)
        rebased = q6_cfg.initial_capital * (1.0 + ret).cumprod()
        group = group.copy()
        group["equity"] = rebased.to_numpy()
        group["drawdown"] = (rebased / rebased.cummax() - 1.0).to_numpy()
        m = q6_compute_metrics(group, q6_cfg, label="strategy")
        tm = q6_compute_trade_metrics(group, q6_cfg, label="strategy")
        m.update(tm)
        m["year"] = float(year) # type: ignore
        rows.append(m)

    return pd.DataFrame(rows)


def monte_carlo_simulation(
    period_returns: pd.Series,
    config: LabConfig,
    seed_offset: int = 0,
    bh_returns: Optional[pd.Series] = None,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Monte Carlo por block bootstrap — delega a Q6 monte_carlo_block_bootstrap."""
    q6_cfg = _make_q6_config(config)
    q6_cfg_with_offset = Q6Config(
        initial_capital=q6_cfg.initial_capital,
        commission_rate=q6_cfg.commission_rate,
        slippage_rate=q6_cfg.slippage_rate,
        annualization_factor=q6_cfg.annualization_factor,
        mc_runs=q6_cfg.mc_runs,
        mc_block_size=q6_cfg.mc_block_size,
        random_seed=q6_cfg.random_seed + seed_offset,
    )
    s_ret = period_returns.fillna(0.0).to_numpy(dtype=float)
    b_ret = (bh_returns.fillna(0.0).to_numpy(dtype=float) if bh_returns is not None
             else np.zeros_like(s_ret))
    mc, summary = monte_carlo_block_bootstrap(s_ret, b_ret, q6_cfg_with_offset)
    # Remap Q6 summary keys → Q4 keys for backward compatibility
    summary_compat = {
        "mc_total_return_p05": summary["mc_total_return_p05"],
        "mc_total_return_p50": summary["mc_total_return_p50"],
        "mc_total_return_p95": summary["mc_total_return_p95"],
        "mc_max_drawdown_p05": summary["mc_max_drawdown_p05"],
        "mc_max_drawdown_p50": summary["mc_max_drawdown_p50"],
        "mc_sharpe_p05": summary["mc_sharpe_p05"],
        "mc_sharpe_p50": summary["mc_sharpe_p50"],
        "mc_probability_loss": summary["mc_probability_loss"],
        "mc_probability_beat_benchmark": summary["mc_probability_beat_benchmark"],
    }
    # Rename Q6 mc columns to Q4 column names
    mc_compat = mc.rename(columns={
        "strategy_total_return": "total_return",
        "strategy_max_drawdown": "max_drawdown",
        "strategy_sharpe": "sharpe",
    })
    if "total_return" in mc_compat.columns:
        mc_compat["final_equity"] = q6_cfg.initial_capital * (1.0 + mc_compat["total_return"])
    return mc_compat, summary_compat


def detect_overfit(record: Dict[str, Any], config: LabConfig) -> Dict[str, Any]:
    """Crea banderas conservadoras de posible sobreajuste."""
    is_ret = float(record.get("in_sample_strategy_total_return", np.nan))
    oos_ret = float(record.get("out_of_sample_strategy_total_return", np.nan))
    is_sharpe = float(record.get("in_sample_strategy_sharpe", np.nan))
    oos_sharpe = float(record.get("out_of_sample_strategy_sharpe", np.nan))
    full_trades = float(record.get("full_strategy_trade_count", 0.0))
    oos_trades = float(record.get("out_of_sample_strategy_trade_count", 0.0))
    oos_dd = float(record.get("out_of_sample_strategy_max_drawdown", np.nan))
    bh_oos_ret = float(record.get("out_of_sample_buy_hold_total_return", np.nan))

    in_sample_good = bool(is_ret > 0 and is_sharpe > 0)
    oos_bad = bool(oos_ret < 0 or oos_sharpe < 0)
    low_trade_count = bool(full_trades < config.min_trades_required or oos_trades < max(5, config.min_trades_required * 0.25))
    underperforms_benchmark_oos = bool(oos_ret < bh_oos_ret)
    severe_oos_drawdown = bool(oos_dd < -0.50)

    if in_sample_good and oos_bad:
        status = "REJECT_OVERFIT_RISK"
    elif low_trade_count:
        status = "INCONCLUSIVE_LOW_TRADES"
    elif underperforms_benchmark_oos and oos_ret < 0:
        status = "REJECT_OOS_WEAK"
    elif severe_oos_drawdown:
        status = "INCONCLUSIVE_RISK_TOO_HIGH"
    elif oos_ret > 0 and oos_sharpe > 0 and not underperforms_benchmark_oos:
        status = "CANDIDATE_NEEDS_ROBUSTNESS"
    else:
        status = "INCONCLUSIVE"

    generalization_ratio = oos_sharpe / is_sharpe if is_sharpe and not np.isnan(is_sharpe) else np.nan

    return {
        "variant_id": record.get("variant_id"),
        "hypothesis_id": record.get("hypothesis_id"),
        "hypothesis_name": record.get("hypothesis_name"),
        "in_sample_good": in_sample_good,
        "oos_bad": oos_bad,
        "low_trade_count": low_trade_count,
        "underperforms_benchmark_oos": underperforms_benchmark_oos,
        "severe_oos_drawdown": severe_oos_drawdown,
        # FIX: ratio sin sentido si is_sharpe es cercano a cero; usar umbral mínimo.
        "generalization_ratio_sharpe": float(generalization_ratio) if abs(is_sharpe) > 0.1 else float("nan"),
        "research_status": status,
    }


def parameter_sensitivity(all_metrics: pd.DataFrame) -> pd.DataFrame:
    """Resume sensibilidad por hipótesis y parámetro usando métricas out-of-sample."""
    rows: List[Dict[str, Any]] = []
    if all_metrics.empty:
        return pd.DataFrame()

    for hypothesis_id, group in all_metrics.groupby("hypothesis_id"):
        parsed_params = group["params_json"].apply(json.loads)
        param_names = sorted({key for params in parsed_params for key in params.keys()})
        for param_name in param_names:
            values = parsed_params.apply(lambda p: p.get(param_name))
            temp = group.copy()
            temp["param_value"] = values.astype(str).values
            grouped = temp.groupby("param_value")
            for param_value, param_group in grouped:
                rows.append(
                    {
                        "hypothesis_id": hypothesis_id,
                        "param_name": param_name,
                        "param_value": param_value,
                        "variants": int(len(param_group)),
                        "median_oos_total_return": float(param_group["out_of_sample_strategy_total_return"].median()),
                        "median_oos_sharpe": float(param_group["out_of_sample_strategy_sharpe"].median()),
                        "median_oos_max_drawdown": float(param_group["out_of_sample_strategy_max_drawdown"].median()),
                        "positive_oos_rate": float((param_group["out_of_sample_strategy_total_return"] > 0).mean()),
                    }
                )

    return pd.DataFrame(rows)


# =============================================================================
# GRÁFICOS Y REPORTE
# =============================================================================


def plot_equity_curves(output_path: Path, equity_table: pd.DataFrame, title: str) -> None:
    """Grafica curvas de capital de candidatos seleccionados."""
    plt.figure(figsize=(12, 7))
    for column in equity_table.columns:
        if column != "open_time_utc":
            plt.plot(equity_table["open_time_utc"], equity_table[column], label=column)
    plt.title(title)
    plt.xlabel("Fecha UTC")
    plt.ylabel("Capital")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_monte_carlo_histogram(output_path: Path, mc: pd.DataFrame, title: str) -> None:
    """Grafica distribución Monte Carlo de retornos totales."""
    plt.figure(figsize=(10, 6))
    plt.hist(mc["total_return"].dropna(), bins=40)
    plt.title(title)
    plt.xlabel("Retorno total simulado")
    plt.ylabel("Frecuencia")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def format_float(value: Any) -> str:
    """Formato seguro para Markdown."""
    try:
        if pd.isna(value):
            return "nan"
        return f"{float(value):.6f}"
    except Exception:
        return str(value)


def dataframe_to_markdown(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Convierte un DataFrame pequeño a Markdown sin depender de tabulate."""
    if df.empty:
        return "No hay datos."
    temp = df.head(max_rows).copy()
    columns = list(temp.columns)
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---" for _ in columns]) + "|"
    rows = []
    for _, row in temp.iterrows():
        rows.append("| " + " | ".join(format_float(row[col]) for col in columns) + " |")
    return "\n".join([header, sep] + rows)


def generate_report(
    report_path: Path,
    config: LabConfig,
    hypotheses: List[HypothesisSpec],
    leaderboard: pd.DataFrame,
    overfit_flags: pd.DataFrame,
    mc_summary: pd.DataFrame,
    sensitivity: pd.DataFrame,
) -> None:
    """Genera reporte automático del laboratorio."""
    lines: List[str] = []
    lines.append(f"# Q4 — Laboratorio de Investigación Cuantitativa — {config.symbol} {config.interval}")
    lines.append("")
    lines.append("## Principio rector")
    lines.append("")
    lines.append("Este laboratorio no declara que una estrategia funciona por un solo backtest. Una variante solo puede quedar como candidata si sobrevive validación out-of-sample, revisión de robustez, Monte Carlo, sensibilidad de parámetros y revisión de sobreajuste.")
    lines.append("")
    lines.append("## Configuración")
    lines.append("")
    lines.append(f"- Capital inicial: `{config.initial_capital}`")
    lines.append(f"- Comisión: `{config.commission_rate}`")
    lines.append(f"- Slippage: `{config.slippage_rate}`")
    lines.append(f"- Split in-sample: `{config.in_sample_fraction:.2%}`")
    lines.append(f"- Monte Carlo runs: `{config.monte_carlo_runs}`")
    lines.append(f"- Monte Carlo block size: `{config.monte_carlo_block_size}` velas")
    lines.append("")
    lines.append("## Hipótesis evaluadas")
    lines.append("")
    for hyp in hypotheses:
        lines.append(f"### {hyp.hypothesis_id} — {hyp.name}")
        lines.append(f"- Hipótesis: {hyp.statement}")
        lines.append(f"- Experimento: {hyp.experiment_design}")
        lines.append(f"- Variables: `{', '.join(hyp.variables)}`")
        lines.append("")
    lines.append("## Leaderboard seleccionado por desempeño in-sample")
    lines.append("")
    lines.append("La selección por in-sample se usa solo para simular el flujo real de investigación. El out-of-sample no debe usarse para optimizar parámetros.")
    lines.append("")
    selected_cols = [
        "hypothesis_id",
        "variant_id",
        "in_sample_strategy_total_return",
        "in_sample_strategy_sharpe",
        "out_of_sample_strategy_total_return",
        "out_of_sample_strategy_sharpe",
        "out_of_sample_strategy_max_drawdown",
        "full_strategy_trade_count",
        "params_json",
    ]
    lines.append(dataframe_to_markdown(leaderboard[selected_cols], max_rows=15) if not leaderboard.empty else "No hay leaderboard.")
    lines.append("")
    lines.append("## Banderas de sobreajuste")
    lines.append("")
    flag_cols = [
        "hypothesis_id",
        "variant_id",
        "research_status",
        "in_sample_good",
        "oos_bad",
        "low_trade_count",
        "underperforms_benchmark_oos",
        "generalization_ratio_sharpe",
    ]
    lines.append(dataframe_to_markdown(overfit_flags[flag_cols], max_rows=20) if not overfit_flags.empty else "No hay banderas.")
    lines.append("")
    lines.append("## Monte Carlo")
    lines.append("")
    lines.append("Monte Carlo usa block bootstrap sobre retornos de estrategia. No prueba causalidad; mide fragilidad de la curva bajo reordenamientos por bloques.")
    lines.append("")
    lines.append(dataframe_to_markdown(mc_summary, max_rows=20) if not mc_summary.empty else "No se ejecutó Monte Carlo.")
    lines.append("")
    lines.append("## Sensibilidad de parámetros")
    lines.append("")
    lines.append("Una hipótesis robusta no debería depender de un único valor mágico de parámetro. Revisa `tables/sensitivity_summary.csv`.")
    lines.append("")
    lines.append(dataframe_to_markdown(sensitivity.head(25), max_rows=25) if not sensitivity.empty else "No hay sensibilidad.")
    lines.append("")
    lines.append("## Criterio de conclusión")
    lines.append("")
    lines.append("Una variante queda rechazada si gana in-sample pero falla out-of-sample, si tiene pocas operaciones, si depende de un parámetro aislado, si Monte Carlo muestra alta probabilidad de pérdida o si no supera un benchmark razonable. Una variante con buen desempeño solo queda como candidata para más pruebas, nunca como estrategia confirmada.")
    lines.append("")
    lines.append("## Siguiente validación obligatoria")
    lines.append("")
    lines.append("1. Revisar estabilidad por año.")
    lines.append("2. Revisar sensibilidad por parámetro.")
    lines.append("3. Probar costos más altos.")
    lines.append("4. Ejecutar walk-forward real.")
    lines.append("5. Repetir en otros activos líquidos.")
    lines.append("6. Congelar reglas antes de mirar nuevos datos.")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# EJECUCIÓN PRINCIPAL DEL LABORATORIO
# =============================================================================


def select_leaderboard(all_metrics: pd.DataFrame, config: LabConfig) -> pd.DataFrame:
    """Selecciona candidatos usando solo métricas in-sample para evitar optimizar sobre OOS."""
    if all_metrics.empty:
        return all_metrics

    temp = all_metrics.copy()
    temp["selection_score"] = (
        temp["in_sample_strategy_sharpe"].fillna(-999)
        + temp["in_sample_strategy_calmar"].fillna(-999) * 0.25
        + temp["in_sample_strategy_total_return"].fillna(-999) * 0.10
    )
    temp = temp[temp["full_strategy_trade_count"].fillna(0) >= max(5, config.min_trades_required // 2)]
    if temp.empty:
        temp = all_metrics.copy()
        temp["selection_score"] = temp["in_sample_strategy_sharpe"].fillna(-999)

    leaderboard = (
        temp.sort_values(["hypothesis_id", "selection_score"], ascending=[True, False])
        .groupby("hypothesis_id")
        .head(3)
        .sort_values("selection_score", ascending=False)
        .reset_index(drop=True)
    )
    return leaderboard


def run_lab(args: argparse.Namespace) -> None:
    """Orquesta lectura de datos, experimentos, robustez y reportes."""
    input_path = Path(args.input)
    output_dir = ensure_dir(Path(args.output_dir))
    tables_dir = ensure_dir(output_dir / "tables")
    charts_dir = ensure_dir(output_dir / "charts")
    reports_dir = ensure_dir(output_dir / "reports")
    mc_dir = ensure_dir(output_dir / "monte_carlo")

    config = LabConfig(
        symbol=args.symbol,
        interval=args.interval,
        initial_capital=args.initial_capital,
        commission_rate=args.commission_rate,
        slippage_rate=args.slippage_rate,
        annualization_factor=args.annualization_factor,
        in_sample_fraction=args.in_sample_fraction,
        monte_carlo_runs=args.monte_carlo_runs,
        monte_carlo_block_size=args.monte_carlo_block_size,
        random_seed=args.random_seed,
        max_candidates_for_mc=args.max_candidates_for_mc,
        min_trades_required=args.min_trades_required,
    )

    data = load_and_standardize(input_path)   # ← Q6: normaliza OHLCV, keep=last, aliases
    data = add_research_features(data)
    hypotheses = build_hypotheses()

    # FIX: eliminamos backtest_cache global (causaba OOM: ~3.6 GB para 720 variantes).
    # En su lugar guardamos solo params_by_variant para regenerar el backtest de los
    # candidatos del leaderboard. El costo extra es ~12 backtests adicionales al final.
    all_records: List[Dict[str, Any]] = []
    selected_equities = pd.DataFrame({"open_time_utc": data["open_time_utc"]})
    params_by_variant: Dict[str, Dict[str, Any]] = {}
    hypothesis_by_variant: Dict[str, HypothesisSpec] = {}

    for hypothesis in hypotheses:
        signal_fn = SIGNAL_FUNCTIONS[hypothesis.signal_function_name]
        param_combinations = expand_grid(hypothesis.parameter_grid)

        for idx, params in enumerate(param_combinations, start=1):
            variant_id = f"{hypothesis.hypothesis_id}_{idx:04d}"
            signal = signal_fn(data, params)
            backtest = run_backtest(data, signal, config)   # → Q6 run_backtest_core via wrapper
            metrics_by_sample = evaluate_backtest(backtest, config)
            record = metrics_to_flat_record(hypothesis, variant_id, params, metrics_by_sample)
            all_records.append(record)
            params_by_variant[variant_id] = params
            hypothesis_by_variant[variant_id] = hypothesis
            # No guardamos el backtest completo — solo métricas y params.

    all_metrics = pd.DataFrame(all_records)
    all_metrics.to_csv(tables_dir / "all_experiments_metrics.csv", index=False)

    leaderboard = select_leaderboard(all_metrics, config)
    leaderboard.to_csv(tables_dir / "leaderboard_selected_by_in_sample.csv", index=False)

    overfit_rows = [detect_overfit(row.to_dict(), config) for _, row in all_metrics.iterrows()]
    overfit_flags = pd.DataFrame(overfit_rows)
    overfit_flags.to_csv(tables_dir / "overfit_flags.csv", index=False)

    sensitivity = parameter_sensitivity(all_metrics)
    sensitivity.to_csv(tables_dir / "sensitivity_summary.csv", index=False)

    yearly_rows: List[pd.DataFrame] = []
    mc_summary_rows: List[Dict[str, Any]] = []

    for mc_rank, (_, candidate) in enumerate(leaderboard.head(config.max_candidates_for_mc).iterrows(), start=1):
        variant_id = str(candidate["variant_id"])
        hypothesis_id = str(candidate["hypothesis_id"])
        # Regeneramos el backtest solo para los candidatos del leaderboard (máx 12).
        _hyp = hypothesis_by_variant[variant_id]
        _signal_fn = SIGNAL_FUNCTIONS[_hyp.signal_function_name]
        _params = params_by_variant[variant_id]
        backtest = run_backtest(data, _signal_fn(data, _params), config)

        yearly = yearly_robustness(backtest, config)
        yearly.insert(0, "variant_id", variant_id)
        yearly.insert(0, "hypothesis_id", hypothesis_id)
        yearly_rows.append(yearly)

        split_idx = int(len(backtest) * config.in_sample_fraction)
        oos_slice = backtest.iloc[split_idx:]
        oos_returns = oos_slice["strategy_return"]
        oos_bh_returns = oos_slice["bh_return"]
        mc, mc_summary = monte_carlo_simulation(oos_returns, config, seed_offset=mc_rank, bh_returns=oos_bh_returns)
        mc.insert(0, "variant_id", variant_id)
        mc.to_csv(mc_dir / f"{variant_id}_monte_carlo_runs.csv", index=False)

        mc_summary_record = {"hypothesis_id": hypothesis_id, "variant_id": variant_id}
        mc_summary_record.update(mc_summary)
        mc_summary_rows.append(mc_summary_record)

        plot_monte_carlo_histogram(
            charts_dir / f"{variant_id}_monte_carlo_total_return_hist.png",
            mc,
            f"Monte Carlo OOS — {variant_id}",
        )

        selected_equities[variant_id] = backtest["equity"].to_numpy()

    if yearly_rows:
        yearly_table = pd.concat(yearly_rows, ignore_index=True)
    else:
        yearly_table = pd.DataFrame()
    yearly_table.to_csv(tables_dir / "yearly_robustness_selected_candidates.csv", index=False)

    mc_summary = pd.DataFrame(mc_summary_rows)
    mc_summary.to_csv(tables_dir / "monte_carlo_summary.csv", index=False)

    # benchmark equity embebida en el backtest del último candidato (o recalcular)
    _bh_signal = pd.Series(1.0, index=data.index)
    _bh_bt = run_backtest(data, _bh_signal, config)
    selected_equities["buy_and_hold"] = _bh_bt["equity"].to_numpy()
    selected_equities.to_csv(tables_dir / "selected_candidates_equity_curves.csv", index=False)
    plot_equity_curves(charts_dir / "selected_candidates_equity_curves.png", selected_equities, "Q4 candidatos seleccionados vs Buy & Hold")

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "config": asdict(config),
        "row_count": int(len(data)),
        "hypotheses": [asdict(h) for h in hypotheses],
        "total_variants_tested": int(len(all_metrics)),
        "important_note": "Ningún resultado debe considerarse estrategia confirmada. Este laboratorio solo genera evidencia para investigación.",
    }
    write_json(reports_dir / "q4_lab_metadata.json", metadata)

    generate_report(
        reports_dir / "q4_research_lab_report.md",
        config,
        hypotheses,
        leaderboard,
        overfit_flags.merge(leaderboard[["variant_id"]], on="variant_id", how="inner") if not leaderboard.empty else overfit_flags,
        mc_summary,
        sensitivity,
    )

    print("\nLaboratorio Q4 terminado.")
    print(f"Variantes evaluadas: {len(all_metrics)}")
    print(f"Tablas:   {tables_dir}")
    print(f"Gráficos: {charts_dir}")
    print(f"Reporte:  {reports_dir / 'q4_research_lab_report.md'}")
    print("\nRecuerda: ningún resultado queda aprobado sin robustez fuera de muestra, sensibilidad y Monte Carlo.")


def parse_args() -> argparse.Namespace:
    """Argumentos CLI del laboratorio."""
    parser = argparse.ArgumentParser(description="Q4 — Laboratorio de Investigación Cuantitativa")
    parser.add_argument("--input", required=True, help="Ruta al dataset Parquet/CSV enriquecido de Q2/Q3.")
    parser.add_argument("--output-dir", required=True, help="Carpeta de salida del laboratorio Q4.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo evaluado.")
    parser.add_argument("--interval", default="1h", help="Intervalo evaluado.")
    parser.add_argument("--initial-capital", type=float, default=10_000.0, help="Capital inicial.")
    parser.add_argument("--commission-rate", type=float, default=0.001, help="Comisión por notional. 0.001 = 0.10%%.")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="Slippage por notional. 0.0005 = 0.05%%.")
    parser.add_argument("--annualization-factor", type=float, default=8760.0, help="Factor de anualización para 1h.")
    parser.add_argument("--in-sample-fraction", type=float, default=0.70, help="Fracción inicial usada como in-sample.")
    parser.add_argument("--monte-carlo-runs", type=int, default=500, help="Número de simulaciones Monte Carlo.")
    parser.add_argument("--monte-carlo-block-size", type=int, default=24, help="Tamaño de bloque Monte Carlo en velas.")
    parser.add_argument("--random-seed", type=int, default=42, help="Semilla reproducible.")
    parser.add_argument("--max-candidates-for-mc", type=int, default=12, help="Máximo de candidatos para Monte Carlo.")
    parser.add_argument("--min-trades-required", type=int, default=30, help="Mínimo de trades para no marcar baja evidencia.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_lab(args)


if __name__ == "__main__":
    main()
