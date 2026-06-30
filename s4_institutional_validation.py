#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
S4 — Validación institucional (Director de Investigación / mesa de fondo)

Rol: la última puerta antes de considerar capital real. Simula el tipo de
revisión que haría un comité de riesgo de un fondo antes de asignar capital
a una regla de trading: distintos regímenes de mercado, costos de ejecución
de tamaño institucional, Monte Carlo con probabilidad de ruina y un stress
test directo sobre los peores drawdowns históricos del benchmark.

Regla de oro de este módulo (heredada de Q4):
    NUNCA se aprueba una estrategia solo porque esta capa salió bien.
    Si S3 (auditoría anti-overfitting) ya marcó la estrategia como
    `FAILS_OVERFIT_AUDIT` o `INCONCLUSIVE_FRAGILE`, S4 no puede revertir
    ese veredicto. S4 igual calcula y reporta todas las métricas
    institucionales por transparencia y para que el módulo sea reutilizable
    con hipótesis futuras que sí superen S2/S3 limpiamente.

Este script NO optimiza parámetros, NO cambia reglas y NO mira los datos
para "mejorar" la estrategia. Las reglas se leen congeladas desde
`frozen_strategy_definitions.csv` (generado por S3) y se ejecutan tal cual.

Motor de backtest: importado desde Q6 (open-to-open, sin lookahead, ddof=1).
El motor close-to-close original fue eliminado en la migración a Q6.

Ejemplo PowerShell:
    python s4_institutional_validation.py --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet --s3-dir .\crypto_datalake\research\s3_overfit_audit\BTCUSDT\1h --output-dir .\crypto_datalake\research\s4_institutional_validation\BTCUSDT\1h --symbol BTCUSDT --interval 1h
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None

from q6_backtest_engine import (
    BacktestConfig as Q6Config,
    run_backtest_core,
    compute_metrics as q6_compute_metrics,
    compute_trade_metrics as q6_compute_trade_metrics,
    block_bootstrap_indices,
    walk_forward_windows as q6_walk_forward_windows,
    load_and_standardize,
)


# =============================================================================
# Configuración
# =============================================================================


@dataclass(frozen=True)
class InstitutionalConfig:
    symbol: str
    interval: str
    initial_capital: float = 10_000.0
    annualization_factor: int = 8760

    # Costos. La intuición institucional es contraria a la retail: comisión
    # negociada más baja, pero slippage/impacto de mercado más alto por el
    # tamaño de las órdenes, especialmente bajo estrés.
    best_execution_commission: float = 0.0004
    best_execution_slippage: float = 0.0006
    realistic_commission: float = 0.0008
    realistic_slippage: float = 0.0012
    stress_commission: float = 0.0015
    stress_slippage: float = 0.0030

    # Walk-forward (mismas ventanas que S3 para comparabilidad directa).
    train_bars: int = 24 * 365
    test_bars: int = 24 * 90
    step_bars: int = 24 * 90
    purge_bars: int = 24

    # Régimen de mercado.
    trend_window_bars: int = 24 * 200
    vol_window_bars: int = 24 * 30
    vol_low_quantile: float = 0.33
    vol_high_quantile: float = 0.67
    trend_band: float = 0.02  # +/-2% alrededor de la SMA de tendencia = RANGE

    # Monte Carlo institucional.
    mc_runs: int = 2000
    mc_block_size: int = 24
    random_seed: int = 42
    ruin_drawdown_threshold: float = -0.50

    # Stress test de crisis.
    crisis_top_n: int = 3
    min_crisis_depth: float = -0.15  # solo cuenta como "crisis" si dd <= -15%

    # Criterios mínimos de aprobación institucional (solo aplican si la
    # estrategia ya superó el gate de S3).
    min_regime_beat_rate: float = 0.50
    min_stress_beat_rate: float = 0.50
    max_probability_ruin: float = 0.05
    min_crisis_nonworse_rate: float = 0.60


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    hypothesis: str
    rule: str
    risk_to_destroy: str


# =============================================================================
# Adaptador Q6Config ← InstitutionalConfig
# =============================================================================


def _make_q6_config(config: InstitutionalConfig, commission: float, slippage: float) -> Q6Config:
    """Adapta InstitutionalConfig al Q6Config para cada escenario de costos."""
    return Q6Config(
        symbol=config.symbol,
        interval=config.interval,
        initial_capital=config.initial_capital,
        commission_rate=commission,
        slippage_rate=slippage,
        annualization_factor=config.annualization_factor,
        train_bars=config.train_bars,
        test_bars=config.test_bars,
        step_bars=config.step_bars,
        purge_bars=config.purge_bars,
        random_seed=config.random_seed,
        mc_runs=config.mc_runs,
        mc_block_size=config.mc_block_size,
    )


# =============================================================================
# Carga de datos (delega a Q6 load_and_standardize; agrega columnas extras)
# =============================================================================


def read_market_data(path: Path) -> pd.DataFrame:
    """Carga y estandariza datos de mercado usando el loader centralizado de Q6.

    Añade columnas auxiliares usadas por el análisis de régimen de S4:
    period_return, log_return, weekday, hour.
    La columna `open_time_utc` es canónica (de Q6). No se usa `open_time` como
    nombre de columna principal — las funciones de S4 referencia `open_time_utc`.
    """
    out = load_and_standardize(path)

    if len(out) < 2000:
        raise ValueError("Dataset demasiado pequeño para validación institucional con regímenes y walk-forward.")

    min_year = int(out["open_time_utc"].dt.year.min())
    max_year = int(out["open_time_utc"].dt.year.max())
    if min_year < 2010 or max_year < 2010:
        raise ValueError(f"Fechas inválidas detectadas: rango {min_year}-{max_year}.")

    out["period_return"] = out["close"].pct_change().fillna(0.0)
    out["log_return"] = np.log(out["close"]).diff().fillna(0.0)
    out["weekday"] = out["open_time_utc"].dt.weekday
    out["hour"] = out["open_time_utc"].dt.hour
    return out


def load_frozen_strategies(s3_dir: Path) -> List[StrategySpec]:
    """Lee las reglas congeladas que S3 evaluó. S4 nunca redefine reglas."""
    path = s3_dir / "tables" / "frozen_strategy_definitions.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No encontré {path}. S4 necesita las definiciones congeladas que produjo S3 "
            "(`frozen_strategy_definitions.csv`). Corre s3_overfit_audit.py primero."
        )
    df = pd.read_csv(path)
    specs = [
        StrategySpec(
            strategy_id=str(r["strategy_id"]),
            hypothesis=str(r["hypothesis"]),
            rule=str(r["rule"]),
            risk_to_destroy=str(r["risk_to_destroy"]),
        )
        for _, r in df.iterrows()
    ]
    return specs


def load_s3_verdict(s3_dir: Path) -> pd.DataFrame:
    path = s3_dir / "tables" / "audit_verdict.csv"
    if not path.exists():
        raise FileNotFoundError(f"No encontré {path}. S4 exige el veredicto de S3 como gate de entrada.")
    return pd.read_csv(path)


# =============================================================================
# Señales (las mismas reglas congeladas que evaluó S3; cero parámetros nuevos)
# Nota: usan `open_time_utc` (columna canónica del pipeline Q6).
# =============================================================================


def _signal_wednesday_long(df: pd.DataFrame) -> pd.Series:
    # next_bar_weekday: signal[t] = 1 cuando la barra t+1 es miércoles.
    # Q6 hace position = signal.shift(1), así position[t+1] = signal[t].
    # Resultado: posición activa exactamente durante las 24h del miércoles.
    next_bar_weekday = df["open_time_utc"].shift(-1).dt.weekday
    return next_bar_weekday.eq(2).astype(float).fillna(0.0)


def _signal_avoid_thursday(df: pd.DataFrame) -> pd.Series:
    next_bar_weekday = df["open_time_utc"].shift(-1).dt.weekday
    return next_bar_weekday.ne(3).astype(float).fillna(0.0)


SIGNAL_REGISTRY: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "S2_H2_WEDNESDAY_LONG": _signal_wednesday_long,
    "S2_H4_AVOID_THURSDAY": _signal_avoid_thursday,
}


def signal_for_strategy(df: pd.DataFrame, strategy_id: str) -> pd.Series:
    fn = SIGNAL_REGISTRY.get(strategy_id)
    if fn is None:
        raise ValueError(
            f"No tengo una implementación de señal registrada para '{strategy_id}'. "
            "S4 solo evalúa reglas que ya tiene codificadas explícitamente; no inventa "
            "lógica nueva a partir del texto de la hipótesis."
        )
    return fn(df)


def resolve_evaluable_strategies(specs: List[StrategySpec]) -> Tuple[List[StrategySpec], List[str]]:
    evaluable = [s for s in specs if s.strategy_id in SIGNAL_REGISTRY]
    skipped = [s.strategy_id for s in specs if s.strategy_id not in SIGNAL_REGISTRY]
    return evaluable, skipped


# =============================================================================
# Régimen de mercado (solo para segmentar/reportar; las reglas no lo usan)
# =============================================================================


def add_regime_columns(df: pd.DataFrame, config: InstitutionalConfig) -> pd.DataFrame:
    out = df.copy()
    min_periods_trend = max(50, config.trend_window_bars // 4)
    min_periods_vol = max(50, config.vol_window_bars // 4)

    sma_trend = out["close"].rolling(config.trend_window_bars, min_periods=min_periods_trend).mean()
    price_vs_sma = out["close"] / sma_trend - 1.0

    realized_vol = out["log_return"].rolling(config.vol_window_bars, min_periods=min_periods_vol).std() * math.sqrt(
        config.annualization_factor
    )

    valid_vol = realized_vol.dropna()
    if len(valid_vol) >= 50:
        vol_low_thr = float(valid_vol.quantile(config.vol_low_quantile))
        vol_high_thr = float(valid_vol.quantile(config.vol_high_quantile))
    else:
        vol_low_thr, vol_high_thr = float("nan"), float("nan")

    trend_regime = pd.Series("WARMUP", index=out.index, dtype=object)
    has_trend = price_vs_sma.notna()
    trend_regime.loc[has_trend & (price_vs_sma > config.trend_band)] = "BULL"
    trend_regime.loc[has_trend & (price_vs_sma < -config.trend_band)] = "BEAR"
    trend_regime.loc[has_trend & (price_vs_sma >= -config.trend_band) & (price_vs_sma <= config.trend_band)] = "RANGE"

    vol_regime = pd.Series("WARMUP", index=out.index, dtype=object)
    has_vol = realized_vol.notna() & np.isfinite(vol_low_thr) & np.isfinite(vol_high_thr)
    vol_regime.loc[has_vol & (realized_vol <= vol_low_thr)] = "LOWVOL"
    vol_regime.loc[has_vol & (realized_vol > vol_low_thr) & (realized_vol < vol_high_thr)] = "MEDVOL"
    vol_regime.loc[has_vol & (realized_vol >= vol_high_thr)] = "HIGHVOL"

    out["sma_trend"] = sma_trend
    out["realized_vol"] = realized_vol
    out["trend_regime"] = trend_regime
    out["vol_regime"] = vol_regime
    out["regime_label"] = np.where(
        (trend_regime == "WARMUP") | (vol_regime == "WARMUP"),
        "WARMUP",
        trend_regime.astype(str) + "_" + vol_regime.astype(str),
    )
    out["vol_low_threshold"] = vol_low_thr
    out["vol_high_threshold"] = vol_high_thr
    return out


def dominant_regime(df_slice: pd.DataFrame) -> str:
    labels = df_slice["regime_label"]
    labels = labels[labels != "WARMUP"]
    if labels.empty:
        return "WARMUP"
    return str(labels.mode().iloc[0])


# =============================================================================
# Motor de backtest — delega a Q6 run_backtest_core (open-to-open, ddof=1)
# =============================================================================


def evaluate_sample(
    df: pd.DataFrame,
    position: pd.Series,
    q6_cfg: Q6Config,
) -> Dict[str, float]:
    """Evalúa una ventana de backtest usando el motor Q6.

    Modelo: open-to-open, posición ejecutada en la apertura del bar siguiente,
    ddof=1 en todas las métricas, benchmark frictionless (Buy & Hold).
    El motor close-to-close de S4 fue eliminado en la migración a Q6.
    """
    bt = run_backtest_core(df, position, q6_cfg)
    s_m = q6_compute_metrics(bt, q6_cfg, return_col="strategy_return", equity_col="equity", label="strategy")
    b_m = q6_compute_metrics(bt, q6_cfg, return_col="bh_return", equity_col="bh_equity", label="benchmark")
    out: Dict[str, float] = {**s_m, **b_m}
    out["excess_total_return_vs_benchmark"] = (
        out.get("strategy_total_return", 0.0) - out.get("benchmark_total_return", 0.0)
    )
    out["excess_sharpe_vs_benchmark"] = (
        out.get("strategy_sharpe", float("nan")) - out.get("benchmark_sharpe", float("nan"))
    )
    return out


def generate_walk_forward_windows(
    n: int, train_bars: int, test_bars: int, step_bars: int, purge_bars: int
) -> pd.DataFrame:
    """Genera ventanas walk-forward usando el generador de Q6.

    Devuelve un DataFrame con el mismo esquema que S4 usaba internamente,
    para mantener compatibilidad con walk_forward_by_regime() y main().
    """
    _q6_cfg = Q6Config(
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        purge_bars=purge_bars,
    )
    rows = []
    for win_id, (ts, te, os, oe) in enumerate(q6_walk_forward_windows(n, _q6_cfg), start=1):
        rows.append(
            {
                "window_id": win_id,
                "train_start_idx": ts,
                "train_end_idx_exclusive": te,
                "test_start_idx": os,
                "test_end_idx_exclusive": oe,
            }
        )
    return pd.DataFrame(rows)


# =============================================================================
# Walk-forward institucional por régimen y por escala de costos
# =============================================================================


def walk_forward_by_regime(
    df: pd.DataFrame,
    strategies: List[StrategySpec],
    windows: pd.DataFrame,
    commission: float,
    slippage: float,
    config: InstitutionalConfig,
) -> pd.DataFrame:
    q6_cfg = _make_q6_config(config, commission, slippage)
    rows: List[Dict] = []
    for _, w in windows.iterrows():
        test_df = df.iloc[int(w.test_start_idx) : int(w.test_end_idx_exclusive)].copy().reset_index(drop=True)
        regime = dominant_regime(test_df)
        for spec in strategies:
            test_pos = signal_for_strategy(test_df, spec.strategy_id)
            m = evaluate_sample(test_df, test_pos, q6_cfg)
            rows.append(
                {
                    "strategy_id": spec.strategy_id,
                    "window_id": int(w.window_id),
                    "test_start": test_df["open_time_utc"].iloc[0].isoformat(),
                    "test_end": test_df["open_time_utc"].iloc[-1].isoformat(),
                    "dominant_regime": regime,
                    "commission": commission,
                    "slippage": slippage,
                    **m,
                }
            )
    return pd.DataFrame(rows)


def aggregate_by_regime(wf_regime: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (sid, regime), g in wf_regime.groupby(["strategy_id", "dominant_regime"]):
        rows.append(
            {
                "strategy_id": sid,
                "regime": regime,
                "window_count": len(g),
                "positive_window_rate": float((g["strategy_total_return"].astype(float) > 0).mean()),
                "beat_benchmark_window_rate": float((g["excess_total_return_vs_benchmark"].astype(float) > 0).mean()),
                "median_test_return": float(g["strategy_total_return"].astype(float).median()),
                "median_excess_vs_benchmark": float(g["excess_total_return_vs_benchmark"].astype(float).median()),
                "worst_test_return": float(g["strategy_total_return"].astype(float).min()),
            }
        )
    return pd.DataFrame(rows)


def institutional_cost_stress(
    df: pd.DataFrame,
    strategies: List[StrategySpec],
    windows: pd.DataFrame,
    config: InstitutionalConfig,
) -> pd.DataFrame:
    scenarios = [
        ("best_execution", config.best_execution_commission, config.best_execution_slippage),
        ("realistic", config.realistic_commission, config.realistic_slippage),
        ("stress", config.stress_commission, config.stress_slippage),
    ]
    rows = []
    for scenario, comm, slip in scenarios:
        wf = walk_forward_by_regime(df, strategies, windows, comm, slip, config)
        for sid, g in wf.groupby("strategy_id"):
            rows.append(
                {
                    "scenario": scenario,
                    "strategy_id": sid,
                    "commission": comm,
                    "slippage": slip,
                    "window_count": len(g),
                    "positive_window_rate": float((g["strategy_total_return"].astype(float) > 0).mean()),
                    "beat_benchmark_window_rate": float((g["excess_total_return_vs_benchmark"].astype(float) > 0).mean()),
                    "median_test_return": float(g["strategy_total_return"].astype(float).median()),
                    "median_excess_vs_benchmark": float(g["excess_total_return_vs_benchmark"].astype(float).median()),
                }
            )
    return pd.DataFrame(rows)


# =============================================================================
# Monte Carlo institucional (Q6 block_bootstrap_indices, bootstrap pareado)
# =============================================================================


def monte_carlo_institutional(
    df: pd.DataFrame,
    strategies: List[StrategySpec],
    config: InstitutionalConfig,
    commission: float,
    slippage: float,
    dirs: Dict[str, Path],
) -> pd.DataFrame:
    """Monte Carlo por bootstrap de bloques pareado usando Q6.

    "Pareado" = los mismos índices de bloque se aplican a estrategia y benchmark,
    de modo que cada run compara la misma secuencia temporal aleatoria.
    `mc_probability_ruin` se calcula sobre los drawdowns de cada run versus
    `config.ruin_drawdown_threshold`.
    """
    rng = np.random.default_rng(config.random_seed)
    q6_cfg = _make_q6_config(config, commission, slippage)

    rows = []
    run_tables = []

    for spec in strategies:
        pos = signal_for_strategy(df, spec.strategy_id).reset_index(drop=True)
        bt = run_backtest_core(df, pos, q6_cfg)
        strat_r = bt["strategy_return"].to_numpy(dtype=float)
        bench_r = bt["bh_return"].to_numpy(dtype=float)
        n = len(strat_r)

        observed_total_return = float(bt["equity"].iloc[-1] / q6_cfg.initial_capital - 1.0)
        observed_mdd = float((bt["equity"] / bt["equity"].cummax() - 1.0).min())

        mc_returns, mc_mdds, mc_beats_bench = [], [], []
        for i in range(config.mc_runs):
            idx = block_bootstrap_indices(n, config.mc_block_size, rng)
            rb_strat = strat_r[idx]
            rb_bench = bench_r[idx]
            eq_strat = q6_cfg.initial_capital * np.cumprod(1.0 + rb_strat)
            total_strat = float(eq_strat[-1] / q6_cfg.initial_capital - 1.0)
            total_bench = float(np.prod(1.0 + rb_bench) - 1.0)
            # Drawdown del run
            eq_s = pd.Series(eq_strat)
            mdd = float((eq_s / eq_s.cummax() - 1.0).min())
            mc_returns.append(total_strat)
            mc_mdds.append(mdd)
            mc_beats_bench.append(total_strat > total_bench)
            run_tables.append(
                {
                    "strategy_id": spec.strategy_id,
                    "run": i + 1,
                    "mc_total_return": total_strat,
                    "mc_max_drawdown": mdd,
                    "mc_benchmark_total_return": total_bench,
                    "mc_beats_benchmark": total_strat > total_bench,
                }
            )

        arr = np.array(mc_returns, dtype=float)
        dd = np.array(mc_mdds, dtype=float)
        beats = np.array(mc_beats_bench, dtype=bool)
        rows.append(
            {
                "strategy_id": spec.strategy_id,
                "observed_total_return": observed_total_return,
                "observed_max_drawdown": observed_mdd,
                "mc_runs": config.mc_runs,
                "mc_probability_loss": float((arr < 0).mean()),
                "mc_probability_ruin": float((dd <= config.ruin_drawdown_threshold).mean()),
                "mc_probability_beat_benchmark": float(beats.mean()),
                "mc_return_p05": float(np.nanpercentile(arr, 5)),
                "mc_return_p50": float(np.nanpercentile(arr, 50)),
                "mc_return_p95": float(np.nanpercentile(arr, 95)),
                "mc_max_drawdown_p05": float(np.nanpercentile(dd, 5)),
                "mc_max_drawdown_p50": float(np.nanpercentile(dd, 50)),
            }
        )

    runs_df = pd.DataFrame(run_tables)
    runs_df.to_csv(dirs["monte_carlo"] / "monte_carlo_institutional_runs.csv", index=False)
    return pd.DataFrame(rows)


# =============================================================================
# Stress test sobre los peores drawdowns históricos del benchmark
# =============================================================================


def find_drawdown_episodes(equity: pd.Series, open_time: pd.Series, min_depth: float) -> pd.DataFrame:
    eq = equity.reset_index(drop=True)
    t = open_time.reset_index(drop=True)
    peak = eq.cummax()
    dd = eq / peak - 1.0
    is_under = dd < 0

    episodes = []
    start_idx = None
    for i in range(len(eq)):
        if is_under.iloc[i] and start_idx is None:
            start_idx = i
        ended = (not is_under.iloc[i]) or (i == len(eq) - 1)
        if ended and start_idx is not None:
            end_idx = i if not is_under.iloc[i] else i
            segment = dd.iloc[start_idx : end_idx + 1]
            depth = float(segment.min())
            trough_idx = start_idx + int(segment.values.argmin())
            episodes.append(
                {
                    "start_idx": start_idx,
                    "trough_idx": trough_idx,
                    "end_idx": end_idx,
                    "depth": depth,
                    "start_time": t.iloc[start_idx],
                    "trough_time": t.iloc[trough_idx],
                    "end_time": t.iloc[end_idx],
                }
            )
            start_idx = None
    out = pd.DataFrame(episodes)
    if out.empty:
        return out
    return out[out["depth"] <= min_depth].sort_values("depth").reset_index(drop=True)


def crisis_stress_test(
    df: pd.DataFrame,
    strategies: List[StrategySpec],
    config: InstitutionalConfig,
    commission: float,
    slippage: float,
) -> pd.DataFrame:
    """Stress test sobre los peores drawdowns históricos del benchmark usando Q6.

    Encuentra los N episodios de mayor drawdown en la curva Buy & Hold y
    mide cómo se comportó la estrategia congelada durante esos mismos períodos.
    """
    q6_cfg = _make_q6_config(config, commission, slippage)

    # Buy & Hold sobre el histórico completo para identificar episodios de crisis
    bh_pos = pd.Series(1.0, index=df.index)
    bh_bt = run_backtest_core(df, bh_pos, q6_cfg)
    episodes = find_drawdown_episodes(bh_bt["bh_equity"], bh_bt["open_time_utc"], config.min_crisis_depth)
    episodes = episodes.head(config.crisis_top_n)

    rows = []
    for rank, (_, ep) in enumerate(episodes.iterrows(), start=1):
        seg = df.iloc[int(ep.start_idx) : int(ep.end_idx) + 1].copy().reset_index(drop=True)

        # Métricas del benchmark en este segmento
        bh_seg_pos = pd.Series(1.0, index=seg.index)
        seg_bh_bt = run_backtest_core(seg, bh_seg_pos, q6_cfg)
        bench_total = float(seg_bh_bt["bh_equity"].iloc[-1] / q6_cfg.initial_capital - 1.0)
        bench_mdd = float((seg_bh_bt["bh_equity"] / seg_bh_bt["bh_equity"].cummax() - 1.0).min())

        for spec in strategies:
            pos = signal_for_strategy(seg, spec.strategy_id)
            seg_bt = run_backtest_core(seg, pos, q6_cfg)
            strat_total = float(seg_bt["equity"].iloc[-1] / q6_cfg.initial_capital - 1.0)
            strat_mdd = float((seg_bt["equity"] / seg_bt["equity"].cummax() - 1.0).min())
            rows.append(
                {
                    "crisis_rank": rank,
                    "strategy_id": spec.strategy_id,
                    "start_time": ep.start_time.isoformat(),
                    "trough_time": ep.trough_time.isoformat(),
                    "end_time": ep.end_time.isoformat(),
                    "benchmark_drawdown_depth": float(ep.depth),
                    "benchmark_total_return_in_episode": bench_total,
                    "benchmark_max_drawdown_in_episode": bench_mdd,
                    "strategy_total_return_in_episode": strat_total,
                    "strategy_max_drawdown_in_episode": strat_mdd,
                    "excess_return_in_episode": strat_total - bench_total,
                    "strategy_nonworse_than_benchmark": strat_total >= bench_total,
                }
            )
    return pd.DataFrame(rows)


# =============================================================================
# Clasificación final institucional (respeta el gate de S3 siempre)
# =============================================================================


def classify_institutional(
    specs: List[StrategySpec],
    s3_verdict: pd.DataFrame,
    regime_agg: pd.DataFrame,
    cost_df: pd.DataFrame,
    mc_df: pd.DataFrame,
    crisis_df: pd.DataFrame,
    config: InstitutionalConfig,
) -> pd.DataFrame:
    rows = []
    s3_pass_statuses = {"SURVIVES_INITIAL_AUDIT_BUT_NOT_APPROVED"}

    for spec in specs:
        sid = spec.strategy_id
        s3_row = s3_verdict[s3_verdict["strategy_id"] == sid]
        if s3_row.empty:
            rows.append(
                {
                    "strategy_id": sid,
                    "s3_status": "NO_S3_AUDIT_FOUND",
                    "institutional_status": "REJECTED_NO_S3_AUDIT",
                    "reasons": "No existe veredicto S3 para esta estrategia; no se evalúa para capital institucional.",
                }
            )
            continue

        s3_status = str(s3_row.iloc[0]["audit_status"])
        s3_reasons = str(s3_row.iloc[0].get("audit_reasons", ""))

        if s3_status not in s3_pass_statuses:
            rows.append(
                {
                    "strategy_id": sid,
                    "s3_status": s3_status,
                    "institutional_status": "REJECTED_AT_S3_GATE",
                    "reasons": (
                        f"S3 ya clasificó esta estrategia como '{s3_status}'. S4 no puede aprobar lo que S3 rechazó, "
                        f"sin importar el resultado institucional. Motivos de S3: {s3_reasons}"
                    ),
                }
            )
            continue

        # Solo si pasó el gate de S3 se evalúan criterios institucionales reales.
        reasons: List[str] = []

        reg = regime_agg[regime_agg.strategy_id == sid]
        reg = reg[reg.regime != "WARMUP"]
        if reg.empty:
            reasons.append("sin ventanas walk-forward suficientes por régimen")
        else:
            weak_regimes = reg[reg["beat_benchmark_window_rate"] < config.min_regime_beat_rate]
            if not weak_regimes.empty:
                reasons.append(
                    "no bate al benchmark de forma consistente en los regímenes: "
                    + ", ".join(weak_regimes["regime"].tolist())
                )

        stress_row = cost_df[(cost_df.strategy_id == sid) & (cost_df.scenario == "stress")]
        if stress_row.empty or float(stress_row.iloc[0]["beat_benchmark_window_rate"]) < config.min_stress_beat_rate:
            reasons.append("no sobrevive al escenario de costos institucionales de estrés")
        if stress_row.empty or float(stress_row.iloc[0]["median_test_return"]) <= 0:
            reasons.append("mediana de retorno walk-forward no positiva bajo costos de estrés")

        mc_row = mc_df[mc_df.strategy_id == sid]
        if mc_row.empty or float(mc_row.iloc[0]["mc_probability_ruin"]) > config.max_probability_ruin:
            reasons.append(f"probabilidad de ruina (drawdown <= {config.ruin_drawdown_threshold:.0%}) supera el máximo aceptado")

        crisis_rows = crisis_df[crisis_df.strategy_id == sid]
        if crisis_rows.empty:
            reasons.append("no hay episodios de crisis suficientes en el histórico para stress test")
        else:
            nonworse_rate = float(crisis_rows["strategy_nonworse_than_benchmark"].mean())
            if nonworse_rate < config.min_crisis_nonworse_rate:
                reasons.append("se comporta peor que el benchmark en la mayoría de las peores crisis históricas")

        status = "APPROVED_FOR_PAPER_TRADING_PENDING_MULTIASSET" if not reasons else "REJECTED_INSTITUTIONAL_CRITERIA"
        if not reasons:
            reasons.append(
                "Supera criterios institucionales mínimos sobre BTCUSDT 1h. Pendiente: prueba multi-activo, "
                "forward paper trading con reglas congeladas y aprobación de comité de riesgo humano."
            )

        rows.append(
            {
                "strategy_id": sid,
                "s3_status": s3_status,
                "institutional_status": status,
                "reasons": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows)


# =============================================================================
# Gráficos
# =============================================================================


def plot_regime_equity(
    df: pd.DataFrame,
    strategies: List[StrategySpec],
    config: InstitutionalConfig,
    dirs: Dict[str, Path],
) -> None:
    if plt is None:
        return
    q6_cfg = _make_q6_config(config, config.realistic_commission, config.realistic_slippage)
    fig, ax = plt.subplots(figsize=(13, 6))
    for spec in strategies:
        pos = signal_for_strategy(df, spec.strategy_id)
        bt = run_backtest_core(df, pos, q6_cfg)
        ax.plot(bt["open_time_utc"], bt["equity"], label=spec.strategy_id)

    # Benchmark frictionless Buy & Hold (embedded en Q6 bt output)
    bh_pos = pd.Series(1.0, index=df.index)
    bh_bt = run_backtest_core(df, bh_pos, q6_cfg)
    ax.plot(bh_bt["open_time_utc"], bh_bt["bh_equity"], label="BENCHMARK_BUY_AND_HOLD", color="black", linewidth=1.5)

    bear_mask = (df["trend_regime"] == "BEAR").to_numpy()
    if bear_mask.any():
        ax.fill_between(
            df["open_time_utc"], ax.get_ylim()[0], ax.get_ylim()[1],
            where=bear_mask, color="red", alpha=0.05, label="BEAR",
        )
    ax.set_title("S4 — Equity bajo costos institucionales realistas (sombreado = régimen BEAR)")
    ax.set_xlabel("UTC")
    ax.set_ylabel("Equity")
    ax.legend()
    fig.tight_layout()
    fig.savefig(dirs["charts"] / "institutional_equity_by_regime.png", dpi=140)
    plt.close(fig)


def plot_crisis_stress(crisis_df: pd.DataFrame, dirs: Dict[str, Path]) -> None:
    if plt is None or crisis_df.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    width = 0.35
    crises = sorted(crisis_df["crisis_rank"].unique())
    strategies = sorted(crisis_df["strategy_id"].unique())
    x = np.arange(len(crises))
    for i, sid in enumerate(strategies):
        vals = [
            float(crisis_df[(crisis_df.crisis_rank == c) & (crisis_df.strategy_id == sid)]["excess_return_in_episode"].iloc[0])
            if not crisis_df[(crisis_df.crisis_rank == c) & (crisis_df.strategy_id == sid)].empty
            else np.nan
            for c in crises
        ]
        ax.bar(x + i * width, vals, width=width, label=sid)
    ax.axhline(0, color="black", linewidth=1)
    ax.set_xticks(x + width / 2)
    ax.set_xticklabels([f"Crisis #{c}" for c in crises])
    ax.set_ylabel("Exceso de retorno vs benchmark en el episodio")
    ax.set_title("S4 — Stress test: exceso vs benchmark en las peores crisis históricas")
    ax.legend()
    fig.tight_layout()
    fig.savefig(dirs["charts"] / "crisis_stress_excess_return.png", dpi=140)
    plt.close(fig)


# =============================================================================
# Reporte
# =============================================================================


def write_report(
    dirs: Dict[str, Path],
    config: InstitutionalConfig,
    df: pd.DataFrame,
    specs: List[StrategySpec],
    skipped_ids: List[str],
    s3_verdict: pd.DataFrame,
    regime_agg: pd.DataFrame,
    cost_df: pd.DataFrame,
    mc_df: pd.DataFrame,
    crisis_df: pd.DataFrame,
    verdict_df: pd.DataFrame,
) -> None:
    report = []
    report.append(f"# S4 — Validación institucional — {config.symbol} {config.interval}\n")
    report.append("## Principio rector\n")
    report.append(
        "Esta capa simula la revisión de un comité de riesgo de fondo. **No puede aprobar lo que S3 ya rechazó.** "
        "Si una estrategia llega aquí habiendo fallado la auditoría anti-overfitting, se documentan todas las métricas "
        "institucionales por transparencia, pero el veredicto final hereda el rechazo de S3.\n"
    )
    report.append("## Motor de backtest\n")
    report.append(
        "S4 usa el motor unificado Q6 (open-to-open, señal[t] → posición ejecutada en apertura de t+1, "
        "benchmark frictionless Buy & Hold, ddof=1 en todas las métricas). "
        "El motor close-to-close propio fue eliminado en la migración a Q6.\n"
    )
    report.append("## Dataset\n")
    report.append(f"- Filas analizadas: `{len(df):,}`")
    report.append(f"- Inicio UTC: `{df['open_time_utc'].iloc[0]}`")
    report.append(f"- Fin UTC: `{df['open_time_utc'].iloc[-1]}`")
    report.append(
        f"- Costos institucionales: best_execution=({config.best_execution_commission}, {config.best_execution_slippage}), "
        f"realistic=({config.realistic_commission}, {config.realistic_slippage}), "
        f"stress=({config.stress_commission}, {config.stress_slippage})"
    )
    report.append(f"- Monte Carlo: `{config.mc_runs}` runs, bloque `{config.mc_block_size}` barras, ruina si drawdown <= `{config.ruin_drawdown_threshold:.0%}`")
    report.append(
        f"- Régimen: ventana tendencia `{config.trend_window_bars}` barras, ventana volatilidad `{config.vol_window_bars}` barras, "
        f"banda RANGE `±{config.trend_band:.0%}`\n"
    )

    if skipped_ids:
        report.append("## Estrategias congeladas omitidas\n")
        report.append(
            "S4 solo evalúa reglas que tiene codificadas explícitamente. Las siguientes estrategias estaban en "
            "`frozen_strategy_definitions.csv` pero no tienen señal registrada en este script y se omiten: "
            + ", ".join(skipped_ids) + ".\n"
        )

    report.append("## Veredicto institucional final\n")
    report.append(verdict_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Gate de entrada: estado en S3\n")
    report.append(s3_verdict[["strategy_id", "audit_status", "audit_reasons"]].to_markdown(index=False))
    report.append("\n")

    report.append("## Desempeño por régimen de mercado (walk-forward, costos realistas)\n")
    report.append(
        "El régimen se calcula con tendencia (precio vs SMA de 200 días) y volatilidad realizada (terciles), "
        "y se usa **solo para etiquetar y segmentar resultados**, nunca para decidir cuándo entra o sale la regla. "
        "La regla original permanece congelada en todo momento.\n"
    )
    report.append(regime_agg.to_markdown(index=False))
    report.append("\n")

    report.append("## Costos de ejecución institucional\n")
    report.append(
        "`best_execution` asume acceso preferencial; `realistic` es el escenario base esperado; `stress` simula "
        "impacto de mercado elevado por tamaño de orden en condiciones adversas.\n"
    )
    report.append(cost_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Monte Carlo institucional (bootstrap pareado por bloques, Q6)\n")
    report.append(
        "Cada trayectoria remuestrea los mismos índices de bloque para la estrategia y el benchmark, de forma que "
        "`mc_probability_beat_benchmark` mide ventaja real ante secuencias alternativas de mercado, no solo signo del retorno.\n"
    )
    report.append(mc_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Stress test: peores crisis históricas del benchmark\n")
    if crisis_df.empty:
        report.append("No se identificaron episodios de drawdown del benchmark suficientemente profundos en este histórico.\n")
    else:
        report.append(
            "Se identifican los peores drawdowns históricos de Buy & Hold y se mide cómo se habría comportado la regla "
            "congelada exactamente durante esos episodios, sin re-optimizar nada.\n"
        )
        report.append(
            crisis_df[
                [
                    "crisis_rank", "strategy_id", "start_time", "trough_time", "end_time",
                    "benchmark_drawdown_depth", "benchmark_total_return_in_episode",
                    "strategy_total_return_in_episode", "excess_return_in_episode",
                ]
            ].to_markdown(index=False)
        )
    report.append("\n")

    report.append("## Riesgos y limitaciones\n")
    report.append("- **El gate de S3 es vinculante:** ninguna métrica institucional puede revertir un rechazo previo.")
    report.append("- **Un solo activo:** BTCUSDT 1h no permite generalizar a un régimen multi-activo o multi-exchange.")
    report.append("- **Régimen es descriptivo:** los umbrales de volatilidad se calculan sobre toda la muestra; sirven para reportar, no para operar.")
    report.append("- **Pocas crisis históricas:** con ~5 años de datos hay pocos episodios de crisis severa; el stress test tiene poca muestra.")
    report.append("- **Costos modelados de forma simple:** no hay libro de órdenes, profundidad real ni latencia de ejecución.\n")

    report.append("## Conclusión disciplinada\n")
    approved = verdict_df.loc[verdict_df["institutional_status"].str.startswith("APPROVED"), "strategy_id"].tolist()
    if approved:
        report.append(
            "Aprobadas para la siguiente etapa (paper trading multi-activo), nunca para capital real directo: "
            + ", ".join(approved) + "."
        )
    else:
        report.append(
            "Ninguna estrategia evaluada queda aprobada para asignación de capital institucional. "
            "Esto es consistente con el resultado de S3: ambas hipótesis de calendario fallaron la auditoría "
            "anti-overfitting antes de llegar a esta capa, y S4 respeta esa conclusión en lugar de buscar una "
            "forma de revertirla. El laboratorio funcionó como debía: protegió el capital al no aceptar una "
            "estrategia solo porque una métrica aislada se viera bien.\n"
        )

    report.append("## Archivos generados\n")
    for name in [
        "institutional_verdict.csv",
        "regime_breakdown.csv",
        "institutional_cost_stress.csv",
        "monte_carlo_institutional_summary.csv",
        "crisis_stress_test.csv",
        "walk_forward_by_regime_detail.csv",
    ]:
        report.append(f"- `tables/{name}`")
    report.append("- `charts/*.png`")
    report.append("- `reports/s4_institutional_validation_report.md`")

    (dirs["reports"] / "s4_institutional_validation_report.md").write_text("\n".join(report), encoding="utf-8")


# =============================================================================
# Main
# =============================================================================


def ensure_dirs(output_dir: Path) -> Dict[str, Path]:
    dirs = {
        "root": output_dir,
        "tables": output_dir / "tables",
        "charts": output_dir / "charts",
        "reports": output_dir / "reports",
        "monte_carlo": output_dir / "monte_carlo",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="S4 Validación institucional sobre reglas congeladas de S3.")
    parser.add_argument("--input", required=True, help="Ruta al dataset .parquet o .csv con BTCUSDT 1h.")
    parser.add_argument("--s3-dir", required=True, help="Directorio de salida de S3 (contiene tables/frozen_strategy_definitions.csv y audit_verdict.csv).")
    parser.add_argument("--output-dir", required=True, help="Directorio de salida de S4.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--train-bars", type=int, default=24 * 365)
    parser.add_argument("--test-bars", type=int, default=24 * 90)
    parser.add_argument("--step-bars", type=int, default=24 * 90)
    parser.add_argument("--purge-bars", type=int, default=24)
    parser.add_argument("--mc-runs", type=int, default=2000)
    parser.add_argument("--mc-block-size", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--crisis-top-n", type=int, default=3)
    args = parser.parse_args()

    config = InstitutionalConfig(
        symbol=args.symbol,
        interval=args.interval,
        initial_capital=args.initial_capital,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        purge_bars=args.purge_bars,
        mc_runs=args.mc_runs,
        mc_block_size=args.mc_block_size,
        random_seed=args.seed,
        crisis_top_n=args.crisis_top_n,
    )

    s3_dir = Path(args.s3_dir)
    output_dir = Path(args.output_dir)
    dirs = ensure_dirs(output_dir)

    df_raw = read_market_data(Path(args.input))
    df = add_regime_columns(df_raw, config)

    all_specs = load_frozen_strategies(s3_dir)
    specs, skipped_ids = resolve_evaluable_strategies(all_specs)
    if not specs:
        raise ValueError("No hay estrategias evaluables: ninguna coincide con SIGNAL_REGISTRY de S4.")

    s3_verdict = load_s3_verdict(s3_dir)

    windows = generate_walk_forward_windows(len(df), config.train_bars, config.test_bars, config.step_bars, config.purge_bars)
    if windows.empty:
        raise ValueError("No hay suficientes datos para generar ventanas walk-forward institucionales.")

    wf_regime_detail = walk_forward_by_regime(
        df, specs, windows, config.realistic_commission, config.realistic_slippage, config
    )
    wf_regime_detail.to_csv(dirs["tables"] / "walk_forward_by_regime_detail.csv", index=False)

    regime_agg = aggregate_by_regime(wf_regime_detail)
    regime_agg.to_csv(dirs["tables"] / "regime_breakdown.csv", index=False)

    cost_df = institutional_cost_stress(df, specs, windows, config)
    cost_df.to_csv(dirs["tables"] / "institutional_cost_stress.csv", index=False)

    mc_df = monte_carlo_institutional(df, specs, config, config.realistic_commission, config.realistic_slippage, dirs)
    mc_df.to_csv(dirs["tables"] / "monte_carlo_institutional_summary.csv", index=False)

    crisis_df = crisis_stress_test(df, specs, config, config.realistic_commission, config.realistic_slippage)
    crisis_df.to_csv(dirs["tables"] / "crisis_stress_test.csv", index=False)

    verdict_df = classify_institutional(specs, s3_verdict, regime_agg, cost_df, mc_df, crisis_df, config)
    verdict_df.to_csv(dirs["tables"] / "institutional_verdict.csv", index=False)

    metadata = {
        "config": asdict(config),
        "input": str(args.input),
        "s3_dir": str(s3_dir),
        "output_dir": str(output_dir),
        "rows": int(len(df)),
        "start_utc": df["open_time_utc"].iloc[0].isoformat(),
        "end_utc": df["open_time_utc"].iloc[-1].isoformat(),
        "rules_frozen": True,
        "optimized_parameters": 0,
        "s3_gate_enforced": True,
        "backtest_engine": "Q6 (open-to-open, signal[t]->pos[t+1])",
        "strategies_evaluated": [s.strategy_id for s in specs],
        "strategies_skipped_no_signal_impl": skipped_ids,
    }
    (dirs["reports"] / "s4_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    plot_regime_equity(df, specs, config, dirs)
    plot_crisis_stress(crisis_df, dirs)

    write_report(
        dirs=dirs,
        config=config,
        df=df,
        specs=specs,
        skipped_ids=skipped_ids,
        s3_verdict=s3_verdict,
        regime_agg=regime_agg,
        cost_df=cost_df,
        mc_df=mc_df,
        crisis_df=crisis_df,
        verdict_df=verdict_df,
    )

    print("S4 Validación institucional terminada.")
    print(f"Tablas:   {dirs['tables']}")
    print(f"Gráficos: {dirs['charts']}")
    print(f"Reporte:  {dirs['reports'] / 's4_institutional_validation_report.md'}")
    print("Recuerda: el veredicto institucional nunca puede ser más permisivo que el veredicto de S3.")


if __name__ == "__main__":
    main()
