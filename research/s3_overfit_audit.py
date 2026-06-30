#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
S3 - Detector de Sobreajuste / Walk-Forward con reglas congeladas

Rol: auditor cuantitativo.
Objetivo: intentar destruir hipótesis S2_H2_WEDNESDAY_LONG y S2_H4_AVOID_THURSDAY.

Este script NO optimiza parámetros, NO busca una estrategia mejor y NO cambia reglas.
Evalúa:
- lookahead / data leakage conceptual
- validación walk-forward con reglas congeladas
- robustez por costes
- fragilidad temporal por desplazamiento de calendario UTC
- sesgo de selección mediante controles negativos
- Monte Carlo por bloques

Ejemplo PowerShell:
python s3_overfit_audit.py --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet --output-dir .\crypto_datalake\research\s3_overfit_audit\BTCUSDT\1h --symbol BTCUSDT --interval 1h
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

from q6_backtest_engine import (
    BacktestConfig as Q6Config,
    run_backtest_core,
    compute_metrics as q6_compute_metrics,
    compute_trade_metrics as q6_compute_trade_metrics,
    extract_trades as q6_extract_trades,
    block_bootstrap_indices,
    walk_forward_windows as q6_walk_forward_windows,
    load_and_standardize,
)

warnings.filterwarnings("ignore", category=RuntimeWarning)

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


WEEKDAY_ES = {
    0: "Lunes",
    1: "Martes",
    2: "Miércoles",
    3: "Jueves",
    4: "Viernes",
    5: "Sábado",
    6: "Domingo",
}


@dataclass(frozen=True)
class AuditConfig:
    symbol: str
    interval: str
    initial_capital: float = 10000.0
    base_commission: float = 0.001
    base_slippage: float = 0.0005
    medium_commission: float = 0.0015
    medium_slippage: float = 0.0010
    high_commission: float = 0.0020
    high_slippage: float = 0.0015
    train_bars: int = 24 * 365
    test_bars: int = 24 * 90
    step_bars: int = 24 * 90
    purge_bars: int = 24
    mc_runs: int = 500
    mc_block_size: int = 24
    random_seed: int = 42
    annualization_factor: int = 8760


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    hypothesis: str
    rule: str
    risk_to_destroy: str


STRATEGIES = [
    StrategySpec(
        strategy_id="S2_H2_WEDNESDAY_LONG",
        hypothesis="El sesgo observado de miércoles UTC podría ser explotable sin indicadores adicionales.",
        rule="Mantener spot long solo durante velas cuyo open_time pertenece a miércoles UTC; fuera el resto.",
        risk_to_destroy="Puede ser selección retrospectiva de un día favorable; sensible a régimen, zona horaria y costes.",
    ),
    StrategySpec(
        strategy_id="S2_H4_AVOID_THURSDAY",
        hypothesis="Evitar jueves UTC podría filtrar un contexto históricamente negativo del benchmark long-only.",
        rule="Mantener exposición pasiva long excepto durante velas cuyo open_time pertenece a jueves UTC.",
        risk_to_destroy="Puede ser un filtro elegido después de mirar el dataset; puede no sostenerse por año, OOS o activos.",
    ),
]


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


def read_market_data(path: Path) -> pd.DataFrame:
    """Carga y estandariza datos de mercado usando Q6 + enriquecimiento S3.

    Migrado: load_and_standardize de Q6 maneja OHLCV, deduplicación (keep=last)
    y aliases de timestamp. Aquí se añaden columnas derivadas que S3 necesita.
    """
    out = load_and_standardize(path)  # ← Q6

    if len(out) < 1000:
        raise ValueError("Dataset demasiado pequeño para auditoría walk-forward.")

    # open_time_utc ya viene de Q6; crear alias 'open_time' para compat interna S3
    out["open_time"] = out["open_time_utc"]
    out["period_return"] = out["close"].pct_change().fillna(0.0)
    out["log_return"] = np.log(out["close"]).diff().fillna(0.0)
    out["weekday"] = out["open_time_utc"].dt.weekday
    out["hour"] = out["open_time_utc"].dt.hour
    return out

def signal_for_strategy(df: pd.DataFrame, strategy_id: str, timezone_shift_hours: int = 0) -> pd.Series:
    """Genera señal alineada con Q6: signal[t] se activa cuando la SIGUIENTE barra (t+1)
    cumple la condición del día. Así, tras el shift(1) interno de run_backtest_core,
    la posición queda activa exactamente durante las barras del día objetivo.

    Ejemplo (WEDNESDAY_LONG, horario UTC):
      t = Martes 23:00  → next_bar = Miércoles 00:00 → signal[t]=1 → position[Miér 00:00]=1
      t = Miércoles 22:00 → next_bar = Miércoles 23:00 → signal[t]=1 → position[Miér 23:00]=1
      t = Miércoles 23:00 → next_bar = Jueves 00:00    → signal[t]=0 → position[Juev 00:00]=0
    """
    shifted_time = df["open_time"] + pd.to_timedelta(timezone_shift_hours, unit="h")
    # Weekday de la barra siguiente (look-ahead de 1 barra, que Q6 corrige con shift(1))
    next_bar_weekday = shifted_time.shift(-1).dt.weekday

    if strategy_id == "S2_H2_WEDNESDAY_LONG":
        return next_bar_weekday.eq(2).astype(float).fillna(0.0)
    if strategy_id == "S2_H4_AVOID_THURSDAY":
        return next_bar_weekday.ne(3).astype(float).fillna(0.0)
    raise ValueError(f"Estrategia desconocida: {strategy_id}")


def signal_weekday_long(df: pd.DataFrame, weekday_num: int, timezone_shift_hours: int = 0) -> pd.Series:
    """Señal long para un día de la semana, usando next_bar_weekday (alineada con Q6)."""
    shifted_time = df["open_time"] + pd.to_timedelta(timezone_shift_hours, unit="h")
    next_bar_weekday = shifted_time.shift(-1).dt.weekday
    return next_bar_weekday.eq(weekday_num).astype(float).fillna(0.0)


def signal_avoid_weekday(df: pd.DataFrame, weekday_num: int, timezone_shift_hours: int = 0) -> pd.Series:
    """Señal de evitar un día de la semana, usando next_bar_weekday (alineada con Q6)."""
    shifted_time = df["open_time"] + pd.to_timedelta(timezone_shift_hours, unit="h")
    next_bar_weekday = shifted_time.shift(-1).dt.weekday
    return next_bar_weekday.ne(weekday_num).astype(float).fillna(0.0)


def _make_q6_config(config: AuditConfig, commission: float, slippage: float) -> Q6Config:
    """Convierte AuditConfig + costos → Q6Config."""
    return Q6Config(
        initial_capital=config.initial_capital,
        commission_rate=commission,
        slippage_rate=slippage,
        annualization_factor=int(config.annualization_factor),
        mc_runs=config.mc_runs,
        mc_block_size=config.mc_block_size,
        random_seed=config.random_seed,
        train_bars=config.train_bars,
        test_bars=config.test_bars,
        step_bars=config.step_bars,
        purge_bars=config.purge_bars,
    )


def _rebase_equity(returns: pd.Series, initial_capital: float) -> pd.Series:
    """Reconstruye equity desde retornos locales."""
    return initial_capital * (1.0 + returns.fillna(0.0)).cumprod()


def evaluate_sample(
    df: pd.DataFrame,
    position: pd.Series,
    commission: float,
    slippage: float,
    initial_capital: float,
    annualization_factor: int,
) -> Dict[str, float]:
    """Delega a Q6: ejecuta backtest open-to-open, calcula métricas con ddof=1."""
    # S3 trabaja con df que tiene columnas open_time_utc, open, high, low, close, volume
    q6_cfg = Q6Config(
        initial_capital=initial_capital,
        annualization_factor=int(annualization_factor),
        commission_rate=commission,
        slippage_rate=slippage,
    )
    bt = run_backtest_core(df, position, q6_cfg)
    # Re-base para métricas locales correctas
    s_ret = bt["strategy_return"]
    b_ret = bt["bh_return"]
    s_eq = _rebase_equity(s_ret, initial_capital)
    b_eq = _rebase_equity(b_ret, initial_capital)
    bt_s = bt.copy()
    bt_s["equity"] = s_eq.to_numpy()
    bt_s["drawdown"] = (s_eq / s_eq.cummax() - 1.0).to_numpy()
    bt_s["bh_equity_local"] = b_eq.to_numpy()

    s_m = q6_compute_metrics(bt_s, q6_cfg, label="strategy")
    s_tm = q6_compute_trade_metrics(bt_s, q6_cfg, label="strategy")
    b_m = q6_compute_metrics(bt_s, q6_cfg, return_col="bh_return", equity_col="bh_equity_local", label="benchmark")

    out: Dict[str, float] = {}
    out.update(s_m)
    out.update(s_tm)
    out.update(b_m)
    out["excess_total_return_vs_benchmark"] = out["strategy_total_return"] - out["benchmark_total_return"]
    out["excess_sharpe_vs_benchmark"] = out["strategy_sharpe"] - out["benchmark_sharpe"]
    return out


def generate_walk_forward_windows(n: int, train_bars: int, test_bars: int, step_bars: int, purge_bars: int) -> pd.DataFrame:
    """Genera ventanas walk-forward — delega a Q6 q6_walk_forward_windows."""
    _cfg = Q6Config(train_bars=train_bars, test_bars=test_bars, step_bars=step_bars, purge_bars=purge_bars)
    rows = []
    window_id = 1
    for ts, te, os, oe in q6_walk_forward_windows(n, _cfg):
        rows.append({
            "window_id": window_id,
            "train_start_idx": ts,
            "train_end_idx_exclusive": te,
            "purge_start_idx": te,
            "purge_end_idx_exclusive": os,
            "test_start_idx": os,
            "test_end_idx_exclusive": oe,
            "train_bars": te - ts,
            "purge_bars": os - te,
            "test_bars": oe - os,
        })
        window_id += 1
    return pd.DataFrame(rows)


def walk_forward_audit(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float) -> pd.DataFrame:
    windows = generate_walk_forward_windows(
        len(df), config.train_bars, config.test_bars, config.step_bars, config.purge_bars
    )
    rows: List[Dict[str, float]] = []
    if windows.empty:
        raise ValueError("No hay suficientes datos para las ventanas walk-forward configuradas.")

    for _, w in windows.iterrows():
        train_df = df.iloc[int(w.train_start_idx) : int(w.train_end_idx_exclusive)].copy().reset_index(drop=True)
        test_df = df.iloc[int(w.test_start_idx) : int(w.test_end_idx_exclusive)].copy().reset_index(drop=True)
        for spec in STRATEGIES:
            train_pos = signal_for_strategy(train_df, spec.strategy_id)
            test_pos = signal_for_strategy(test_df, spec.strategy_id)
            train_m = evaluate_sample(
                train_df, train_pos, commission, slippage, config.initial_capital, config.annualization_factor
            )
            test_m = evaluate_sample(
                test_df, test_pos, commission, slippage, config.initial_capital, config.annualization_factor
            )
            base = {
                "strategy_id": spec.strategy_id,
                "window_id": int(w.window_id),
                "train_start": train_df["open_time"].iloc[0].isoformat(),
                "train_end": train_df["open_time"].iloc[-1].isoformat(),
                "test_start": test_df["open_time"].iloc[0].isoformat(),
                "test_end": test_df["open_time"].iloc[-1].isoformat(),
                "commission": commission,
                "slippage": slippage,
                "rules_frozen": True,
                "fit_parameters_in_window": 0,
            }
            for prefix, metrics in [("train", train_m), ("test", test_m)]:
                for k, v in metrics.items():
                    base[f"{prefix}_{k}"] = v
            rows.append(base)
    return pd.DataFrame(rows)


def aggregate_walk_forward(wf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in wf.groupby("strategy_id"):
        test_returns = g["test_strategy_total_return"].astype(float)
        excess = g["test_excess_total_return_vs_benchmark"].astype(float)
        test_dd = g["test_strategy_max_drawdown"].astype(float)
        rows.append(
            {
                "strategy_id": sid,
                "wf_window_count": len(g),
                "wf_positive_window_rate": float((test_returns > 0).mean()),
                "wf_beat_benchmark_window_rate": float((excess > 0).mean()),
                "wf_median_test_return": float(test_returns.median()),
                "wf_mean_test_return": float(test_returns.mean()),
                "wf_worst_test_return": float(test_returns.min()),
                "wf_mean_excess_vs_benchmark": float(excess.mean()),
                "wf_median_excess_vs_benchmark": float(excess.median()),
                "wf_worst_drawdown": float(test_dd.min()),
                "wf_mean_test_sharpe": float(g["test_strategy_sharpe"].astype(float).mean()),
            }
        )
    return pd.DataFrame(rows)


def full_metrics_by_strategy(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float) -> pd.DataFrame:
    rows = []
    for spec in STRATEGIES:
        pos = signal_for_strategy(df, spec.strategy_id)
        m = evaluate_sample(df, pos, commission, slippage, config.initial_capital, config.annualization_factor)
        rows.append({"strategy_id": spec.strategy_id, "commission": commission, "slippage": slippage, **m})
    return pd.DataFrame(rows)


def cost_stress(df: pd.DataFrame, config: AuditConfig) -> pd.DataFrame:
    scenarios = [
        ("base", config.base_commission, config.base_slippage),
        ("medium", config.medium_commission, config.medium_slippage),
        ("high", config.high_commission, config.high_slippage),
    ]
    rows = []
    for scenario, comm, slip in scenarios:
        wf = walk_forward_audit(df, config, comm, slip)
        agg = aggregate_walk_forward(wf)
        full = full_metrics_by_strategy(df, config, comm, slip)
        for sid in [s.strategy_id for s in STRATEGIES]:
            a = agg[agg.strategy_id == sid].iloc[0].to_dict()
            f = full[full.strategy_id == sid].iloc[0].to_dict()
            rows.append(
                {
                    "scenario": scenario,
                    "strategy_id": sid,
                    "commission": comm,
                    "slippage": slip,
                    "full_total_return": f["strategy_total_return"],
                    "full_sharpe": f["strategy_sharpe"],
                    "full_max_drawdown": f["strategy_max_drawdown"],
                    "full_excess_return_vs_benchmark": f["excess_total_return_vs_benchmark"],
                    **{k: v for k, v in a.items() if k != "strategy_id"},
                }
            )
    return pd.DataFrame(rows)


def yearly_robustness(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float) -> pd.DataFrame:
    rows = []
    df2 = df.copy()
    df2["year"] = df2["open_time_utc"].dt.year
    for year, ydf in df2.groupby("year"):
        if len(ydf) < 100:
            continue
        ydf = ydf.reset_index(drop=True)
        for spec in STRATEGIES:
            pos = signal_for_strategy(ydf, spec.strategy_id)
            m = evaluate_sample(ydf, pos, commission, slippage, config.initial_capital, config.annualization_factor)
            rows.append({"strategy_id": spec.strategy_id, "year": int(year), **m})
    return pd.DataFrame(rows)


def selection_bias_controls(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float) -> pd.DataFrame:
    rows = []
    for wd in range(7):
        pos = signal_weekday_long(df, wd)
        m = evaluate_sample(df, pos, commission, slippage, config.initial_capital, config.annualization_factor)
        rows.append(
            {
                "control_family": "weekday_long",
                "tested_condition": f"long_{WEEKDAY_ES[wd]}",
                "weekday_num": wd,
                "is_original_rule": wd == 2,
                **m,
            }
        )
    for wd in range(7):
        pos = signal_avoid_weekday(df, wd)
        m = evaluate_sample(df, pos, commission, slippage, config.initial_capital, config.annualization_factor)
        rows.append(
            {
                "control_family": "avoid_weekday",
                "tested_condition": f"avoid_{WEEKDAY_ES[wd]}",
                "weekday_num": wd,
                "is_original_rule": wd == 3,
                **m,
            }
        )
    return pd.DataFrame(rows)


def timezone_fragility(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float) -> pd.DataFrame:
    shifts = [-12, -6, -3, -1, 0, 1, 3, 6, 12]
    rows = []
    for sid in [s.strategy_id for s in STRATEGIES]:
        for h in shifts:
            pos = signal_for_strategy(df, sid, timezone_shift_hours=h)
            m = evaluate_sample(df, pos, commission, slippage, config.initial_capital, config.annualization_factor)
            rows.append({"strategy_id": sid, "timezone_shift_hours": h, **m})
    return pd.DataFrame(rows)


def monte_carlo_audit(df: pd.DataFrame, config: AuditConfig, commission: float, slippage: float, dirs: Dict[str, Path]) -> pd.DataFrame:
    """Monte Carlo por block bootstrap — delega a Q6 block_bootstrap_indices."""
    rng = np.random.default_rng(config.random_seed)
    q6_cfg = _make_q6_config(config, commission, slippage)
    rows = []
    run_tables = []

    for spec in STRATEGIES:
        pos = signal_for_strategy(df, spec.strategy_id)
        bt = run_backtest_core(df, pos, q6_cfg)
        s_ret = bt["strategy_return"].fillna(0.0).to_numpy(dtype=float)
        b_ret = bt["bh_return"].fillna(0.0).to_numpy(dtype=float)

        s_eq = config.initial_capital * np.cumprod(1.0 + s_ret)
        observed_total_return = float(s_eq[-1] / config.initial_capital - 1.0)
        s_eq_series = pd.Series(s_eq)
        observed_mdd = float((s_eq_series / s_eq_series.cummax() - 1.0).min())

        mc_returns, mc_mdds = [], []
        n = len(s_ret)
        for i in range(config.mc_runs):
            # FIX: block_bootstrap_indices (vectorizado) vs iid rng.choice
            idx = block_bootstrap_indices(n, config.mc_block_size, rng)
            rb = s_ret[idx]
            eq = config.initial_capital * np.cumprod(1.0 + rb)
            eq_s = pd.Series(eq)
            total_ret = float(eq[-1] / config.initial_capital - 1.0)
            mdd = float((eq_s / eq_s.cummax() - 1.0).min())
            mc_returns.append(total_ret)
            mc_mdds.append(mdd)
            run_tables.append({"strategy_id": spec.strategy_id, "run": i + 1,
                                "mc_total_return": total_ret, "mc_max_drawdown": mdd})

        arr = np.array(mc_returns, dtype=float)
        dd = np.array(mc_mdds, dtype=float)
        rows.append({
            "strategy_id": spec.strategy_id,
            "observed_total_return": observed_total_return,
            "observed_max_drawdown": observed_mdd,
            "mc_probability_loss": float((arr < 0).mean()),
            "mc_probability_worse_than_observed_return": float((arr < observed_total_return).mean()),
            "mc_return_p05": float(np.nanpercentile(arr, 5)),
            "mc_return_p50": float(np.nanpercentile(arr, 50)),
            "mc_return_p95": float(np.nanpercentile(arr, 95)),
            "mc_max_drawdown_p05": float(np.nanpercentile(dd, 5)),
            "mc_max_drawdown_p50": float(np.nanpercentile(dd, 50)),
            "mc_max_drawdown_p95": float(np.nanpercentile(dd, 95)),
            "mc_runs": config.mc_runs,
            "mc_block_size": config.mc_block_size,
        })

    pd.DataFrame(run_tables).to_csv(dirs["monte_carlo"] / "monte_carlo_runs.csv", index=False)
    return pd.DataFrame(rows)


def lookahead_leakage_audit(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for spec in STRATEGIES:
        rows.append(
            {
                "strategy_id": spec.strategy_id,
                "uses_price_features": False,
                "uses_future_returns": False,
                "uses_enriched_q2_features": False,
                "uses_calendar_only": True,
                "lookahead_detected_by_rule_review": False,
                "data_leakage_detected_by_rule_review": False,
                "remaining_risk": "La regla fue descubierta mirando el mismo histórico; existe sesgo de selección aunque la señal no use precios futuros.",
            }
        )
    return pd.DataFrame(rows)


def classify_audit(
    wf_agg_base: pd.DataFrame,
    cost_df: pd.DataFrame,
    year_df: pd.DataFrame,
    mc_df: pd.DataFrame,
    tz_df: pd.DataFrame,
    controls_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for spec in STRATEGIES:
        sid = spec.strategy_id
        reasons = []
        status = "SURVIVES_INITIAL_AUDIT_BUT_NOT_APPROVED"

        wf = wf_agg_base[wf_agg_base.strategy_id == sid].iloc[0]
        mc = mc_df[mc_df.strategy_id == sid].iloc[0]
        yr = year_df[year_df.strategy_id == sid].copy()
        cst = cost_df[cost_df.strategy_id == sid].copy()
        tz = tz_df[tz_df.strategy_id == sid].copy()

        wf_pos = float(wf["wf_positive_window_rate"])
        wf_beat = float(wf["wf_beat_benchmark_window_rate"])
        wf_med = float(wf["wf_median_test_return"])
        wf_excess = float(wf["wf_median_excess_vs_benchmark"])
        prob_loss = float(mc["mc_probability_loss"])

        if wf_pos < 0.60:
            reasons.append("menos de 60% de ventanas walk-forward positivas")
        if wf_beat < 0.60:
            reasons.append("menos de 60% de ventanas walk-forward baten benchmark")
        if wf_med <= 0:
            reasons.append("mediana de retorno test walk-forward no positiva")
        if wf_excess <= 0:
            reasons.append("mediana de exceso vs benchmark en walk-forward no positiva")
        if prob_loss >= 0.40:
            reasons.append("Monte Carlo muestra probabilidad de pérdida >= 40%")

        # Cost stress: medio y alto deben mantenerse positivos en mediana WF para sobrevivir con fuerza.
        for scenario in ["medium", "high"]:
            row = cst[cst.scenario == scenario]
            if not row.empty and float(row.iloc[0]["wf_median_test_return"]) <= 0:
                reasons.append(f"cost stress {scenario}: mediana WF no positiva")
            if not row.empty and float(row.iloc[0]["wf_beat_benchmark_window_rate"]) < 0.50:
                reasons.append(f"cost stress {scenario}: bate benchmark en menos de 50% de ventanas")

        # Robustez anual.
        if not yr.empty:
            yr_pos = float((yr["strategy_total_return"].astype(float) > 0).mean())
            yr_beat = float((yr["excess_total_return_vs_benchmark"].astype(float) > 0).mean())
            if yr_pos < 0.60:
                reasons.append("menos de 60% de años positivos")
            if yr_beat < 0.60:
                reasons.append("menos de 60% de años baten benchmark")
        else:
            yr_pos = float("nan")
            yr_beat = float("nan")
            reasons.append("no hay suficientes años para robustez anual")

        # Fragilidad temporal: si shift 0 es muy superior a mediana de shifts alternos, sospecha.
        tz0 = tz[tz.timezone_shift_hours == 0]
        tznon = tz[tz.timezone_shift_hours != 0]
        fragility_ratio = float("nan")
        if not tz0.empty and not tznon.empty:
            zero_ret = float(tz0.iloc[0]["strategy_total_return"])
            alt_median = float(tznon["strategy_total_return"].median())
            fragility_ratio = zero_ret - alt_median
            alt_pos_rate = float((tznon["strategy_total_return"].astype(float) > 0).mean())
            if zero_ret > 0 and alt_pos_rate < 0.50:
                reasons.append("fragilidad de calendario: la mayoría de shifts horarios alternos no son positivos")

        # Sesgo de selección: revisar ranking contra controles.
        if sid == "S2_H2_WEDNESDAY_LONG":
            family = controls_df[controls_df.control_family == "weekday_long"].copy()
            family["rank_return_desc"] = family["strategy_total_return"].rank(ascending=False, method="min")
            orig = family[family.is_original_rule == True]
            if not orig.empty and int(orig.iloc[0]["rank_return_desc"]) > 2:
                reasons.append("sesgo de selección: miércoles no está entre los 2 mejores controles weekday_long")
        if sid == "S2_H4_AVOID_THURSDAY":
            family = controls_df[controls_df.control_family == "avoid_weekday"].copy()
            family["rank_return_desc"] = family["strategy_total_return"].rank(ascending=False, method="min")
            orig = family[family.is_original_rule == True]
            if not orig.empty and int(orig.iloc[0]["rank_return_desc"]) > 2:
                reasons.append("sesgo de selección: evitar jueves no está entre los 2 mejores controles avoid_weekday")

        if reasons:
            # Si falla puntos centrales, queda destruida o inconclusa.
            severe_terms = [
                "mediana de retorno test walk-forward no positiva",
                "menos de 60% de ventanas walk-forward positivas",
                "Monte Carlo muestra probabilidad de pérdida >= 40%",
                "cost stress high: mediana WF no positiva",
            ]
            if any(term in reasons for term in severe_terms):
                status = "FAILS_OVERFIT_AUDIT"
            else:
                status = "INCONCLUSIVE_FRAGILE"
        else:
            status = "SURVIVES_INITIAL_AUDIT_BUT_NOT_APPROVED"
            reasons.append("No se logró destruir con las pruebas S3, pero falta multi-activo y datos futuros.")

        rows.append(
            {
                "strategy_id": sid,
                "audit_status": status,
                "wf_positive_window_rate": wf_pos,
                "wf_beat_benchmark_window_rate": wf_beat,
                "wf_median_test_return": wf_med,
                "wf_median_excess_vs_benchmark": wf_excess,
                "year_positive_rate": yr_pos,
                "year_beat_benchmark_rate": yr_beat,
                "mc_probability_loss": prob_loss,
                "timezone_fragility_return_gap_vs_alt_median": fragility_ratio,
                "audit_reasons": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows)


def plot_equity_curves(df: pd.DataFrame, config: AuditConfig, dirs: Dict[str, Path]) -> None:
    if plt is None:
        return
    plt.figure(figsize=(12, 6))
    q6_cfg = _make_q6_config(config, config.base_commission, config.base_slippage)
    for spec in STRATEGIES:
        pos = signal_for_strategy(df, spec.strategy_id)
        bt = run_backtest_core(df, pos, q6_cfg)
        plt.plot(bt["open_time_utc"], bt["equity"], label=spec.strategy_id)
    # Buy & Hold embebido en el primer backtest
    _bh_bt = run_backtest_core(df, pd.Series(1.0, index=df.index), q6_cfg)
    plt.plot(_bh_bt["open_time_utc"], _bh_bt["equity"], label="BENCHMARK_BUY_AND_HOLD")
    plt.title("S3 Audit - Equity curves, base costs")
    plt.xlabel("UTC")
    plt.ylabel("Equity")
    plt.legend()
    plt.tight_layout()
    plt.savefig(dirs["charts"] / "equity_curves_base_costs.png", dpi=140)
    plt.close()


def plot_walk_forward(wf_agg: pd.DataFrame, wf: pd.DataFrame, dirs: Dict[str, Path]) -> None:
    if plt is None:
        return
    for sid, g in wf.groupby("strategy_id"):
        plt.figure(figsize=(12, 5))
        plt.bar(g["window_id"].astype(str), g["test_strategy_total_return"].astype(float))
        plt.axhline(0, linewidth=1)
        plt.title(f"Walk-forward test returns - {sid}")
        plt.xlabel("Window")
        plt.ylabel("Test total return")
        plt.tight_layout()
        plt.savefig(dirs["charts"] / f"walk_forward_test_returns_{sid}.png", dpi=140)
        plt.close()


def safe_fmt(x: object, pct: bool = False, nd: int = 4) -> str:
    try:
        v = float(x)
        if math.isnan(v):
            return "nan"
        return f"{v:.{nd}%}" if pct else f"{v:.{nd}f}"
    except Exception:
        return str(x)


def write_report(
    dirs: Dict[str, Path],
    config: AuditConfig,
    df: pd.DataFrame,
    full_base: pd.DataFrame,
    wf_base: pd.DataFrame,
    wf_agg_base: pd.DataFrame,
    cost_df: pd.DataFrame,
    year_df: pd.DataFrame,
    mc_df: pd.DataFrame,
    tz_df: pd.DataFrame,
    controls_df: pd.DataFrame,
    audit_df: pd.DataFrame,
    leakage_df: pd.DataFrame,
) -> None:
    report = []
    report.append(f"# S3 — Detector de Sobreajuste — {config.symbol} {config.interval}\n")
    report.append("## Principio rector\n")
    report.append(
        "Este reporte intenta destruir las estrategias candidatas de S2. No optimiza, no mejora reglas y no cambia parámetros. "
        "Las reglas evaluadas permanecen congeladas durante toda la auditoría.\n"
    )
    report.append("## Dataset\n")
    report.append(f"- Filas analizadas: `{len(df):,}`")
    report.append(f"- Inicio UTC: `{df['open_time_utc'].iloc[0]}`")
    report.append(f"- Fin UTC: `{df['open_time_utc'].iloc[-1]}`")
    report.append(f"- Capital inicial por muestra: `{config.initial_capital}`")
    report.append(f"- Coste base: comisión `{config.base_commission}`, slippage `{config.base_slippage}`")
    report.append(f"- Walk-forward: train `{config.train_bars}` barras, purge `{config.purge_bars}`, test `{config.test_bars}`, step `{config.step_bars}`")
    report.append(f"- Monte Carlo: `{config.mc_runs}` runs, block size `{config.mc_block_size}` barras\n")

    report.append("## Estrategias auditadas\n")
    for spec in STRATEGIES:
        report.append(f"### {spec.strategy_id}")
        report.append(f"- Hipótesis: {spec.hypothesis}")
        report.append(f"- Regla congelada: {spec.rule}")
        report.append(f"- Riesgo que la auditoría intenta explotar: {spec.risk_to_destroy}\n")

    report.append("## Auditoría de lookahead y data leakage\n")
    report.append(
        "Las reglas auditadas usan calendario UTC y no consumen retornos futuros ni columnas enriquecidas de Q2. "
        "No obstante, el mayor riesgo no es leakage técnico sino selección retrospectiva: los días fueron descubiertos mirando el mismo histórico.\n"
    )
    report.append(leakage_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Resultado final de auditoría\n")
    cols = [
        "strategy_id",
        "audit_status",
        "wf_positive_window_rate",
        "wf_beat_benchmark_window_rate",
        "wf_median_test_return",
        "wf_median_excess_vs_benchmark",
        "year_positive_rate",
        "year_beat_benchmark_rate",
        "mc_probability_loss",
        "audit_reasons",
    ]
    report.append(audit_df[cols].to_markdown(index=False))
    report.append("\n")

    report.append("## Walk-forward base con reglas congeladas\n")
    report.append(
        "Cada ventana usa un tramo de entrenamiento solo como contexto histórico. No se ajustan parámetros dentro de la ventana. "
        "El test posterior se evalúa con la regla fija.\n"
    )
    report.append(wf_agg_base.to_markdown(index=False))
    report.append("\n")

    report.append("## Cost stress\n")
    report.append(
        "Una hipótesis frágil suele desaparecer al subir costes. Esta tabla resume el walk-forward por escenario.\n"
    )
    cost_cols = [
        "scenario",
        "strategy_id",
        "commission",
        "slippage",
        "full_total_return",
        "wf_positive_window_rate",
        "wf_beat_benchmark_window_rate",
        "wf_median_test_return",
        "wf_median_excess_vs_benchmark",
        "wf_worst_drawdown",
    ]
    report.append(cost_df[cost_cols].to_markdown(index=False))
    report.append("\n")

    report.append("## Robustez anual\n")
    yr_summary = []
    for sid, g in year_df.groupby("strategy_id"):
        yr_summary.append(
            {
                "strategy_id": sid,
                "years": len(g),
                "positive_year_rate": float((g["strategy_total_return"].astype(float) > 0).mean()),
                "beat_benchmark_year_rate": float((g["excess_total_return_vs_benchmark"].astype(float) > 0).mean()),
                "median_year_return": float(g["strategy_total_return"].astype(float).median()),
                "worst_year_return": float(g["strategy_total_return"].astype(float).min()),
            }
        )
    yr_summary_df = pd.DataFrame(yr_summary)
    report.append(yr_summary_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Monte Carlo por bloques\n")
    report.append(
        "Se remuestrean bloques de retornos de la propia estrategia para estimar fragilidad de secuencia. "
        "Una probabilidad alta de pérdida destruye la hipótesis.\n"
    )
    report.append(mc_df.to_markdown(index=False))
    report.append("\n")

    report.append("## Fragilidad de calendario UTC\n")
    report.append(
        "Se desplaza artificialmente el calendario UTC. No es optimización: es prueba destructiva. "
        "Si la hipótesis solo funciona exactamente con UTC sin desplazamiento, puede ser frágil.\n"
    )
    tz_summary = []
    for sid, g in tz_df.groupby("strategy_id"):
        zero = g[g.timezone_shift_hours == 0]
        non = g[g.timezone_shift_hours != 0]
        tz_summary.append(
            {
                "strategy_id": sid,
                "return_shift_0": float(zero.iloc[0]["strategy_total_return"]) if not zero.empty else np.nan,
                "median_return_other_shifts": float(non["strategy_total_return"].median()) if not non.empty else np.nan,
                "positive_other_shift_rate": float((non["strategy_total_return"].astype(float) > 0).mean()) if not non.empty else np.nan,
                "best_shift_return": float(g["strategy_total_return"].max()),
                "worst_shift_return": float(g["strategy_total_return"].min()),
            }
        )
    report.append(pd.DataFrame(tz_summary).to_markdown(index=False))
    report.append("\n")

    report.append("## Sesgo de selección: controles negativos\n")
    report.append(
        "Se comparan las reglas originales contra reglas del mismo tipo en otros días. "
        "Si muchas alternativas similares funcionan igual o mejor, el hallazgo puede ser selección retrospectiva.\n"
    )
    control_summary = controls_df[[
        "control_family",
        "tested_condition",
        "is_original_rule",
        "strategy_total_return",
        "strategy_sharpe",
        "strategy_max_drawdown",
        "excess_total_return_vs_benchmark",
        "strategy_trade_count",
    ]].copy()
    report.append(control_summary.to_markdown(index=False))
    report.append("\n")

    report.append("## Riesgos encontrados\n")
    report.append("- **Sesgo de selección:** S1 descubrió miércoles/jueves en el mismo histórico que ahora se audita.")
    report.append("- **Riesgo de calendario:** las reglas dependen de día UTC; un cambio de zona horaria o régimen puede destruir el efecto.")
    report.append("- **No hay prueba multi-activo:** BTCUSDT 1h no basta para generalizar.")
    report.append("- **No hay datos futuros realmente no vistos:** walk-forward reduce el riesgo, pero no reemplaza una validación futura congelada.")
    report.append("- **Costes y ejecución:** se modelan comisiones/slippage simples; no hay spread dinámico, liquidez intrabar ni latencia.")
    report.append("- **Capacidad de inferencia limitada:** patrones temporales pueden ser artefactos de ciclos de mercado específicos.\n")

    report.append("## Conclusión disciplinada\n")
    if (audit_df["audit_status"] == "SURVIVES_INITIAL_AUDIT_BUT_NOT_APPROVED").any():
        survivors = audit_df.loc[audit_df["audit_status"] == "SURVIVES_INITIAL_AUDIT_BUT_NOT_APPROVED", "strategy_id"].tolist()
        report.append(
            "Sobreviven de forma preliminar, pero **no quedan aprobadas para operar**: " + ", ".join(survivors) + "."
        )
    else:
        report.append("Ninguna estrategia sobrevive de forma limpia a la auditoría S3.")
    report.append(
        "La siguiente validación obligatoria, si alguna regla sobrevive, es prueba multi-activo y luego forward paper trading con reglas congeladas.\n"
    )

    report.append("## Archivos generados\n")
    for name in [
        "audit_verdict.csv",
        "lookahead_leakage_audit.csv",
        "walk_forward_windows.csv",
        "walk_forward_results_base.csv",
        "walk_forward_aggregate_base.csv",
        "cost_stress_summary.csv",
        "yearly_robustness.csv",
        "monte_carlo_summary.csv",
        "timezone_fragility.csv",
        "selection_bias_controls.csv",
    ]:
        report.append(f"- `tables/{name}`")
    report.append("- `charts/*.png`")
    report.append("- `reports/s3_overfit_audit_report.md`")

    (dirs["reports"] / "s3_overfit_audit_report.md").write_text("\n".join(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="S3 Detector de Sobreajuste para estrategias S2 con walk-forward congelado.")
    parser.add_argument("--input", required=True, help="Ruta al dataset .parquet o .csv con BTCUSDT 1h.")
    parser.add_argument("--output-dir", required=True, help="Directorio de salida.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument("--commission", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--medium-commission", type=float, default=0.0015)
    parser.add_argument("--medium-slippage", type=float, default=0.0010)
    parser.add_argument("--high-commission", type=float, default=0.0020)
    parser.add_argument("--high-slippage", type=float, default=0.0015)
    parser.add_argument("--train-bars", type=int, default=24 * 365)
    parser.add_argument("--test-bars", type=int, default=24 * 90)
    parser.add_argument("--step-bars", type=int, default=24 * 90)
    parser.add_argument("--purge-bars", type=int, default=24)
    parser.add_argument("--mc-runs", type=int, default=500)
    parser.add_argument("--mc-block-size", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    config = AuditConfig(
        symbol=args.symbol,
        interval=args.interval,
        initial_capital=args.initial_capital,
        base_commission=args.commission,
        base_slippage=args.slippage,
        medium_commission=args.medium_commission,
        medium_slippage=args.medium_slippage,
        high_commission=args.high_commission,
        high_slippage=args.high_slippage,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        purge_bars=args.purge_bars,
        mc_runs=args.mc_runs,
        mc_block_size=args.mc_block_size,
        random_seed=args.seed,
    )

    output_dir = Path(args.output_dir)
    dirs = ensure_dirs(output_dir)
    df = read_market_data(Path(args.input))

    specs_df = pd.DataFrame([asdict(s) for s in STRATEGIES])
    specs_df.to_csv(dirs["tables"] / "frozen_strategy_definitions.csv", index=False)

    leakage_df = lookahead_leakage_audit(df)
    leakage_df.to_csv(dirs["tables"] / "lookahead_leakage_audit.csv", index=False)

    full_base = full_metrics_by_strategy(df, config, config.base_commission, config.base_slippage)
    full_base.to_csv(dirs["tables"] / "full_sample_metrics_base.csv", index=False)

    windows = generate_walk_forward_windows(
        len(df), config.train_bars, config.test_bars, config.step_bars, config.purge_bars
    )
    # Añadir fechas a las ventanas para lectura.
    if not windows.empty:
        windows = windows.copy()
        windows["train_start_time"] = windows["train_start_idx"].map(lambda i: df["open_time_utc"].iloc[int(i)].isoformat())
        windows["train_end_time"] = windows["train_end_idx_exclusive"].map(lambda i: df["open_time_utc"].iloc[int(i) - 1].isoformat())
        windows["test_start_time"] = windows["test_start_idx"].map(lambda i: df["open_time_utc"].iloc[int(i)].isoformat())
        windows["test_end_time"] = windows["test_end_idx_exclusive"].map(lambda i: df["open_time_utc"].iloc[int(i) - 1].isoformat())
    windows.to_csv(dirs["tables"] / "walk_forward_windows.csv", index=False)

    wf_base = walk_forward_audit(df, config, config.base_commission, config.base_slippage)
    wf_base.to_csv(dirs["tables"] / "walk_forward_results_base.csv", index=False)
    wf_agg_base = aggregate_walk_forward(wf_base)
    wf_agg_base.to_csv(dirs["tables"] / "walk_forward_aggregate_base.csv", index=False)

    cost_df = cost_stress(df, config)
    cost_df.to_csv(dirs["tables"] / "cost_stress_summary.csv", index=False)

    year_df = yearly_robustness(df, config, config.base_commission, config.base_slippage)
    year_df.to_csv(dirs["tables"] / "yearly_robustness.csv", index=False)

    mc_df = monte_carlo_audit(df, config, config.base_commission, config.base_slippage, dirs)
    mc_df.to_csv(dirs["tables"] / "monte_carlo_summary.csv", index=False)

    tz_df = timezone_fragility(df, config, config.base_commission, config.base_slippage)
    tz_df.to_csv(dirs["tables"] / "timezone_fragility.csv", index=False)

    controls_df = selection_bias_controls(df, config, config.base_commission, config.base_slippage)
    controls_df.to_csv(dirs["tables"] / "selection_bias_controls.csv", index=False)

    audit_df = classify_audit(wf_agg_base, cost_df, year_df, mc_df, tz_df, controls_df)
    audit_df.to_csv(dirs["tables"] / "audit_verdict.csv", index=False)

    metadata = {
        "config": asdict(config),
        "input": str(args.input),
        "output_dir": str(output_dir),
        "rows": int(len(df)),
        "start_utc": df["open_time_utc"].iloc[0].isoformat(),
        "end_utc": df["open_time_utc"].iloc[-1].isoformat(),
        "rules_frozen": True,
        "optimized_parameters": 0,
        "strategies": [asdict(s) for s in STRATEGIES],
    }
    (dirs["reports"] / "s3_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    plot_equity_curves(df, config, dirs)
    plot_walk_forward(wf_agg_base, wf_base, dirs)

    write_report(
        dirs=dirs,
        config=config,
        df=df,
        full_base=full_base,
        wf_base=wf_base,
        wf_agg_base=wf_agg_base,
        cost_df=cost_df,
        year_df=year_df,
        mc_df=mc_df,
        tz_df=tz_df,
        controls_df=controls_df,
        audit_df=audit_df,
        leakage_df=leakage_df,
    )

    print("S3 Detector de Sobreajuste terminado.")
    print(f"Tablas:   {dirs['tables']}")
    print(f"Gráficos: {dirs['charts']}")
    print(f"Reporte:  {dirs['reports'] / 's3_overfit_audit_report.md'}")
    print("Recuerda: sobrevivir S3 no aprueba una estrategia; solo permite pasar a multi-activo / forward test.")


if __name__ == "__main__":
    main()
