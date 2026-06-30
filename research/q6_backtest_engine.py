#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Q6 — Motor de Backtest Unificado (Shared Backtest Engine)
==========================================================

Rol: Ingeniero de Software Cuantitativo.

Problema que resuelve:
    Q3, Q4, S2, S3 y S4 reimplementan su propio motor de backtest de forma
    independiente, acumulando divergencias confirmadas en el Code Review:
      - Q3 usa open-to-open; Q4/S2/S3 usan close-to-close.
      - Q4 usaba ddof=0 para Sharpe/Sortino; los demás ddof=1.
      - S3 usa loop Python O(n) para extraer trades; S2/Q3 usan numpy.
      - El bloque de deduplicación varía entre módulos.

    Q6 centraliza toda la lógica de backtest en un módulo importable.
    Todos los scripts futuros (y las próximas correcciones de S2/S3/Q4)
    deben importar desde aquí en vez de reimplementar.

Contenido:
    1. BacktestConfig — configuración única y tipada.
    2. run_backtest_core() — motor vectorizado open-to-open, sin lookahead.
    3. compute_metrics() — Sharpe, Sortino, Calmar, MDD, CAGR, ddof=1 estándar.
    4. extract_trades() — extracción vectorizada con numpy.
    5. block_bootstrap_indices() — vectorizado, reutilizable en Q4/Q5/S4.
    6. walk_forward_windows() — generador de ventanas IS/OOS con purge gap.
    7. load_and_standardize() — cargador único con keep="last" y detección de columnas.
    8. CLI mínima para smoke-test rápido del motor con datos reales.

Principios:
    - Señal en vela t → posición ejecutada desde apertura de t+1.
    - Retorno de mercado: open[t+1] / open[t] - 1 (open-to-open).
    - ddof=1 en todas las desviaciones estándar.
    - keep="last" en deduplicación de timestamps.
    - Sin lookahead: posición = signal.shift(1).

Uso como librería:
    from q6_backtest_engine import BacktestConfig, run_backtest_core, compute_metrics

Uso como smoke-test CLI:
    python q6_backtest_engine.py \
        --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet \
        --output-dir .\crypto_datalake\research\q6_engine_test\BTCUSDT\1h
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import numpy as np
import pandas as pd


# =============================================================================
# 1. CONFIGURACIÓN CENTRAL
# =============================================================================


@dataclass(frozen=True)
class BacktestConfig:
    """Configuración única que usan todos los módulos del pipeline.

    Todos los campos tienen defaults alineados con el estándar del proyecto:
    - initial_capital: 10 000 USD
    - comisión + slippage: 0.001 + 0.0005 (base tier)
    - annualization_factor: 8 760 (cripto 24/7 × 365)
    - train/test/step/purge: walk-forward estándar de S2/S3/S4
    """

    symbol: str = "BTCUSDT"
    interval: str = "1h"
    initial_capital: float = 10_000.0
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    annualization_factor: int = 8_760
    train_bars: int = 24 * 365   # 8 760 barras ≈ 1 año
    test_bars: int = 24 * 90     # 2 160 barras ≈ 3 meses
    step_bars: int = 24 * 90     # avance por ventana
    purge_bars: int = 24         # gap entre IS y OOS (evita leakage de estado)
    random_seed: int = 42
    mc_runs: int = 2_000
    mc_block_size: int = 24      # bloques de 24 barras (≈ 1 día) para bootstrap


# =============================================================================
# 2. CARGADOR ÚNICO
# =============================================================================


def _parse_datetime_robust(series: pd.Series) -> pd.Series:
    """Parsea una Serie a datetime UTC de forma robusta.

    Maneja todos los formatos que aparecen en exchanges cripto:
      1. Ya es datetime64 → convierte directo.
      2. Es texto legible → pd.to_datetime.
      3. Es numérico → infiere unidad por magnitud (ns/us/ms/s).
         Binance usa ms (13 dígitos), pero otros feeds usan s o ns.
         Filas con unidades mixtas se manejan barra por barra.

    Retorna una Series con dtype datetime64[ns, UTC].
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, utc=True, errors="coerce")

    # Intento texto primero (ISO 8601, RFC 2822, etc.)
    if not pd.api.types.is_numeric_dtype(series):
        parsed_text = pd.to_datetime(series, utc=True, errors="coerce")
        valid_rate = float(parsed_text.notna().mean()) if len(parsed_text) else 0.0
        if valid_rate >= 0.90:
            return parsed_text

    # Numérico: inferir unidad por magnitud absoluta
    num = pd.to_numeric(series, errors="coerce")
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns, UTC]")
    valid = num.notna()
    abs_num = num.abs()
    unit_masks = {
        "ns": valid & (abs_num >= 1e17),
        "us": valid & (abs_num >= 1e14) & (abs_num < 1e17),
        "ms": valid & (abs_num >= 1e11) & (abs_num < 1e14),
        "s":  valid & (abs_num >= 1e8)  & (abs_num < 1e11),
    }
    for unit, mask in unit_masks.items():
        if bool(mask.any()):
            parsed.loc[mask] = pd.to_datetime(
                num.loc[mask], unit=unit, utc=True, errors="coerce"
            )
    # Filas numéricas válidas que no cayeron en ninguna banda → último intento
    leftover = valid & parsed.isna()
    if bool(leftover.any()):
        parsed.loc[leftover] = pd.to_datetime(
            num.loc[leftover], utc=True, errors="coerce"
        )
    return parsed


def _choose_time_column(df: pd.DataFrame) -> Tuple[str, pd.Series]:
    """Detecta la columna temporal más plausible y la parsea con _parse_datetime_robust.

    Busca en orden de prioridad: open_time_utc > timestamp_utc > ... > time.
    Si solo hay un DatetimeIndex, lo usa directamente.

    Criterios de plausibilidad:
      - ≥ 90% de filas válidas.
      - Años dentro del rango [2010, año_actual+2].
      - Bonus de prioridad si el nombre contiene "utc".

    Returns:
        (col_name, parsed_series) — col_name es "index" si se usó el índice.
    """
    priority = [
        "open_time_utc", "timestamp_utc", "datetime_utc", "date_utc",
        "open_datetime_utc", "open_time", "timestamp", "datetime", "date", "time",
    ]
    candidates = [c for c in priority if c in df.columns]

    if not candidates:
        if isinstance(df.index, pd.DatetimeIndex):
            parsed = pd.to_datetime(df.index.to_series(index=df.index), utc=True, errors="coerce")
            return "index", parsed
        raise ValueError(
            "No encontré columna temporal reconocible. "
            "Se buscan: " + ", ".join(priority)
        )

    current_year = pd.Timestamp.utcnow().year
    scored: List[Tuple] = []
    for c in candidates:
        parsed = _parse_datetime_robust(df[c])
        valid_rate = float(parsed.notna().mean()) if len(parsed) else 0.0
        if parsed.notna().any():
            min_year = int(parsed.dropna().dt.year.min())
            max_year = int(parsed.dropna().dt.year.max())
            plausible = (
                valid_rate >= 0.90
                and 2010 <= min_year <= current_year + 2
                and 2010 <= max_year <= current_year + 2
            )
            name_bonus = 1 if "utc" in c else 0
            scored.append((plausible, name_bonus, valid_rate, c, parsed, min_year, max_year))

    if not scored:
        raise ValueError("No pude convertir ninguna columna temporal a datetime UTC.")

    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    plausible, _, valid_rate, col, parsed, min_year, max_year = scored[0]

    if not plausible:
        raise ValueError(
            f"Columna temporal no plausible: col={col!r}, "
            f"valid_rate={valid_rate:.1%}, years={min_year}-{max_year}."
        )
    return col, parsed


def load_and_standardize(input_path: Path) -> pd.DataFrame:
    """Carga Parquet o CSV y estandariza columnas mínimas para el motor Q6.

    Estándares aplicados:
      - Nombres de columna: lower-strip.
      - Timestamp: detectado y parseado con _choose_time_column/_parse_datetime_robust.
        Soporta datetime64, ISO 8601 texto, numérico en ns/us/ms/s (incluso mixto).
        La columna canónica resultante siempre se llama `open_time_utc`.
      - Duplicados de columnas: se colapsan (bfill axis=1).
      - Deduplicación de filas: keep="last" (última corrección prevalece).
      - Precios y volúmenes: to_numeric con coerce.
      - Filas con OHLCV nulos o precios ≤ 0: eliminadas.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(input_path)
    elif suffix == ".csv":
        df = pd.read_csv(input_path)
    else:
        raise ValueError(f"Formato no soportado: {suffix}. Usa .parquet o .csv")

    # Normalizar nombres de columna
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Colapsar columnas duplicadas (patrón defensivo para datasets enriquecidos)
    if df.columns.duplicated().any():
        result_parts: List[pd.Series] = []
        for col in pd.unique(df.columns):
            block = df.loc[:, df.columns == col]
            series = block.iloc[:, 0] if block.shape[1] == 1 else block.bfill(axis=1).iloc[:, 0]
            series.name = col
            result_parts.append(series)
        df = pd.concat(result_parts, axis=1)

    # Parser robusto: detecta y parsea la columna temporal correcta
    time_col, parsed_time = _choose_time_column(df)
    if time_col == "index":
        df = df.reset_index(drop=False)
    if time_col != "open_time_utc" and time_col in df.columns:
        df = df.rename(columns={time_col: "open_time_utc"})
    # Asignar la serie ya parseada (evita segundo pd.to_datetime innecesario)
    if len(parsed_time) == len(df):
        df["open_time_utc"] = parsed_time.to_numpy()
    else:
        df["open_time_utc"] = parsed_time.reindex(df.index).to_numpy()

    required = ["open_time_utc", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas tras estandarización: {missing}")

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["open_time_utc", "open", "high", "low", "close", "volume"])
        .sort_values("open_time_utc")
        .drop_duplicates(subset=["open_time_utc"], keep="last")
        .reset_index(drop=True)
    )

    if (df[["open", "high", "low", "close"]] <= 0).any(axis=None):
        raise ValueError("Existen precios ≤ 0. Revisa la capa de calidad Q1.")

    return df


# =============================================================================
# 3. MOTOR VECTORIZADO — OPEN-TO-OPEN, SIN LOOKAHEAD
# =============================================================================


def run_backtest_core(
    df: pd.DataFrame,
    signal: pd.Series,
    config: BacktestConfig,
) -> pd.DataFrame:
    """Motor de backtest spot long-only sin lookahead.

    Modelo de ejecución (estándar del proyecto, definido en Q3):
        - signal[t]:  calculado con información disponible hasta la vela t.
        - position[t+1]: la señal de t se convierte en posición ejecutada
          al abrir t+1.
        - market_return[t]: open[t+1] / open[t] - 1  (open-to-open).
        - El retorno de la estrategia en la barra t es:
              position[t] * market_return[t] - cost[t]
          donde cost[t] = |position[t] - position[t-1]| * (commission + slippage).

    El DataFrame de salida incluye:
        open_time_utc, open, signal_raw, position, market_return,
        turnover, cost, strategy_return, equity, drawdown,
        bh_return, bh_equity, bh_drawdown.
    """
    result = df[["open_time_utc", "open", "high", "low", "close", "volume"]].copy().reset_index(drop=True)

    # Señal limpia en [0, 1]
    raw = pd.to_numeric(signal, errors="coerce").fillna(0.0).clip(0.0, 1.0)
    raw = raw.reindex(result.index).fillna(0.0)

    # Posición ejecutada: señal de t → posición activa en t+1
    position = raw.shift(1).fillna(0.0)

    # Retorno open-to-open (la estrategia ejecuta al open, no al close)
    next_open = result["open"].shift(-1)
    market_return = (next_open / result["open"]) - 1.0

    # Costos por cambio de posición
    turnover = position.diff().abs().fillna(position.abs())
    cost = turnover * (config.commission_rate + config.slippage_rate)

    # Retorno neto de la estrategia
    strategy_return = position * market_return - cost

    # La última fila no tiene next_open → no puede medir retorno
    valid = market_return.notna()
    result = result.loc[valid].copy()
    position = position.loc[valid]
    market_return = market_return.loc[valid]
    turnover = turnover.loc[valid]
    cost = cost.loc[valid]
    strategy_return = strategy_return.loc[valid]
    raw = raw.loc[valid]

    # Protección: spot sin apalancamiento no puede perder más del 100%
    if (strategy_return <= -1.0).any():
        raise ValueError(
            "Retorno de estrategia ≤ -100% detectado. Revisa costos o posiciones."
        )

    # Equity compuesta
    equity = config.initial_capital * (1.0 + strategy_return).cumprod()
    drawdown = equity / equity.cummax() - 1.0

    # Benchmark Buy & Hold — FRICTIONLESS (decisión explícita del pipeline).
    #
    # El benchmark es position=1 constante con retorno open-to-open puro,
    # SIN costos de comisión ni slippage. Fundamento:
    #   1. Un inversor pasivo compra una vez al inicio y no rebalancea;
    #      su único costo real es el spread del primer trade, insignificante.
    #   2. Usar benchmark con costos favorecería artificialmente a estrategias
    #      activas con mucho turnover (el benchmark se penaliza más).
    #   3. El estándar de la industria (Sharpe, Information Ratio) compara
    #      contra benchmark frictionless.
    #
    # Consecuencia: el benchmark es el techo teórico del mercado; la estrategia
    # debe superar market_return bruto, no market_return degradado por costos.
    # Esto es CONSERVADOR para la estrategia (más difícil de batir).
    bh_return = market_return.copy()
    bh_equity = config.initial_capital * (1.0 + bh_return).cumprod()
    bh_drawdown = bh_equity / bh_equity.cummax() - 1.0

    result = result.copy()
    result["signal_raw"] = raw.to_numpy()
    result["position"] = position.to_numpy()
    result["market_return"] = market_return.to_numpy()
    result["turnover"] = turnover.to_numpy()
    result["cost"] = cost.to_numpy()
    result["strategy_return"] = strategy_return.to_numpy()
    result["equity"] = equity.to_numpy()
    result["drawdown"] = drawdown.to_numpy()
    result["bh_return"] = bh_return.to_numpy()
    result["bh_equity"] = bh_equity.to_numpy()
    result["bh_drawdown"] = bh_drawdown.to_numpy()
    result = result.reset_index(drop=True)

    return result


# =============================================================================
# 4. MÉTRICAS PROFESIONALES — ddof=1 ESTÁNDAR
# =============================================================================


def _safe_div(a: float, b: float) -> float:
    """División segura; devuelve nan si b es cero o nan."""
    if b == 0.0 or not math.isfinite(b):
        return float("nan")
    return a / b


def compute_metrics(
    backtest: pd.DataFrame,
    config: BacktestConfig,
    return_col: str = "strategy_return",
    equity_col: str = "equity",
    label: str = "strategy",
) -> Dict[str, float]:
    """Calcula métricas profesionales desde una curva de backtest.

    ddof=1 en todas las std (estimador de muestra). Consistente con Q3/S2/S3/S4.

    Retorna un dict con prefijo `label_` en cada clave para facilitar
    la construcción de tablas comparativas IS/OOS/benchmark.
    """
    ret = backtest[return_col].replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    eq = backtest[equity_col].dropna().astype(float)

    if ret.empty or eq.empty:
        return {}

    n = len(ret)
    years = max(n / config.annualization_factor, 1e-12)

    # Retorno y CAGR
    total_return = float(eq.iloc[-1]) / config.initial_capital - 1.0
    cagr = (float(eq.iloc[-1]) / config.initial_capital) ** (1.0 / years) - 1.0 if float(eq.iloc[-1]) > 0 else float("nan")

    # Drawdown máximo
    peak = eq.cummax()
    dd = eq / peak - 1.0
    max_drawdown = float(dd.min())

    # Sharpe (ddof=1)
    mean_ret = float(ret.mean())
    std_ret = float(ret.std(ddof=1))
    sharpe = _safe_div(mean_ret, std_ret) * math.sqrt(config.annualization_factor)

    # Sortino (ddof=1)
    downside = ret[ret < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = _safe_div(mean_ret, downside_std) * math.sqrt(config.annualization_factor)

    # Calmar
    calmar = _safe_div(cagr, abs(max_drawdown)) if max_drawdown < 0 else float("nan")

    prefix = f"{label}_"
    return {
        f"{prefix}total_return": total_return,
        f"{prefix}cagr": cagr,
        f"{prefix}sharpe": sharpe,
        f"{prefix}sortino": sortino,
        f"{prefix}calmar": calmar,
        f"{prefix}max_drawdown": max_drawdown,
        f"{prefix}final_equity": float(eq.iloc[-1]),
        f"{prefix}mean_period_return": mean_ret,
        f"{prefix}std_period_return": std_ret,
        f"{prefix}n_bars": float(n),
        f"{prefix}years": years,
    }


def compute_trade_metrics(
    backtest: pd.DataFrame,
    config: BacktestConfig,
    label: str = "strategy",
) -> Dict[str, float]:
    """Extrae operaciones y calcula métricas de trading vectorizadas.

    Usa numpy puro (sin loop Python por barra) para ser O(trades) en vez de O(n).
    """
    trades = extract_trades(backtest, config)
    prefix = f"{label}_"

    if trades.empty:
        return {
            f"{prefix}trade_count": 0.0,
            f"{prefix}win_rate": float("nan"),
            f"{prefix}profit_factor": float("nan"),
            f"{prefix}expectancy": float("nan"),
            f"{prefix}avg_trade_return": float("nan"),
            f"{prefix}avg_bars_held": float("nan"),
        }

    returns = trades["net_return"].astype(float)
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())

    return {
        f"{prefix}trade_count": float(len(trades)),
        f"{prefix}win_rate": float((returns > 0).mean()),
        f"{prefix}profit_factor": _safe_div(gross_profit, abs(gross_loss)),
        f"{prefix}expectancy": float(returns.mean()),
        f"{prefix}avg_trade_return": float(returns.mean()),
        f"{prefix}avg_bars_held": float(trades["bars_held"].mean()),
    }


# =============================================================================
# 5. EXTRACCIÓN VECTORIZADA DE TRADES
# =============================================================================


def extract_trades(backtest: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Extrae operaciones long-only con numpy (O(trades), no O(n)).

    Usa open_time_utc y open como timestamps y precios de ejecución.
    El round-trip cost es 2 × (commission + slippage) sobre el notional.
    """
    pos = backtest["position"].to_numpy(dtype=float)
    prices = backtest["open"].to_numpy(dtype=float)
    times = backtest["open_time_utc"].to_numpy()

    prev_pos = np.r_[0.0, pos[:-1]]
    entry_idx = np.flatnonzero((pos > 0.0) & (prev_pos <= 0.0))
    exit_idx = np.flatnonzero((pos <= 0.0) & (prev_pos > 0.0))

    if len(entry_idx) == 0:
        return pd.DataFrame(columns=[
            "entry_time", "exit_time", "entry_price", "exit_price",
            "gross_return", "net_return", "bars_held",
        ])

    round_trip_cost = 2.0 * (config.commission_rate + config.slippage_rate)
    rows: List[Dict] = []
    exit_ptr = 0

    for ei in entry_idx:
        # Avanzar el puntero de salidas hasta encontrar la primera salida posterior a ei
        while exit_ptr < len(exit_idx) and exit_idx[exit_ptr] <= ei:
            exit_ptr += 1
        xi = int(exit_idx[exit_ptr]) if exit_ptr < len(exit_idx) else len(pos) - 1

        ep = float(prices[ei])
        xp = float(prices[xi])

        if ep <= 0 or xp <= 0 or not np.isfinite(ep) or not np.isfinite(xp):
            continue

        gross = xp / ep - 1.0
        net = gross - round_trip_cost

        rows.append({
            "entry_time": times[ei],
            "exit_time": times[xi],
            "entry_price": ep,
            "exit_price": xp,
            "gross_return": gross,
            "net_return": net,
            "bars_held": int(xi - ei),
        })

    return pd.DataFrame(rows)


# =============================================================================
# 6. BLOCK BOOTSTRAP VECTORIZADO
# =============================================================================


def block_bootstrap_indices(
    n: int, block_size: int, rng: np.random.Generator
) -> np.ndarray:
    """Genera n índices remuestreados por bloques contiguos (vectorizado).

    Este es el método estándar de bootstrap para el proyecto. Preserva la
    autocorrelación local dentro de cada bloque, reduciendo el sesgo del
    bootstrap iid para series temporales financieras.

    Rendimiento: O(1) calls a numpy vs O(n/block_size) iteraciones Python.
    Equivalente a la implementación de S4; centralizado aquí para reutilización.
    """
    if n == 0:
        return np.array([], dtype=int)
    block_size = max(1, min(block_size, n))
    max_start = n - block_size
    num_blocks = -(-n // block_size)  # ceil(n / block_size)
    starts = rng.integers(0, max_start + 1, size=num_blocks)
    offsets = np.arange(block_size)
    idx = (starts[:, None] + offsets[None, :]).ravel()[:n]
    return idx


def monte_carlo_block_bootstrap(
    strategy_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    config: BacktestConfig,
    seed_offset: int = 0,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Monte Carlo por block bootstrap pareado (paired bootstrap).

    "Pareado" significa que los mismos índices de bloque se aplican tanto a
    retornos de estrategia como a benchmark. Así la comparación en cada run
    es entre la misma secuencia temporal aleatoria, evitando sesgo de selección.

    Equivalente a la implementación de S4; centralizado aquí.
    """
    rng = np.random.default_rng(config.random_seed + seed_offset)
    n = len(strategy_returns)

    rows: List[Dict] = []
    for run in range(config.mc_runs):
        # Mismo índice para estrategia y benchmark (paired)
        idx = block_bootstrap_indices(n, config.mc_block_size, rng)
        s_ret = strategy_returns[idx]
        b_ret = benchmark_returns[idx]

        s_eq = config.initial_capital * np.cumprod(1.0 + s_ret)
        b_eq = config.initial_capital * np.cumprod(1.0 + b_ret)

        s_total = s_eq[-1] / config.initial_capital - 1.0 if len(s_eq) else 0.0
        b_total = b_eq[-1] / config.initial_capital - 1.0 if len(b_eq) else 0.0

        s_dd = float(pd.Series(s_eq).div(pd.Series(s_eq).cummax()).sub(1).min()) if len(s_eq) else 0.0

        s_mean = float(np.mean(s_ret))
        s_std = float(np.std(s_ret, ddof=1))
        s_sharpe = (s_mean / s_std) * math.sqrt(config.annualization_factor) if s_std > 0 else float("nan")

        rows.append({
            "mc_run": run,
            "strategy_total_return": s_total,
            "benchmark_total_return": b_total,
            "beats_benchmark": int(s_total > b_total),
            "strategy_max_drawdown": s_dd,
            "strategy_sharpe": s_sharpe,
        })

    mc = pd.DataFrame(rows)
    summary = {
        "mc_runs": config.mc_runs,
        "mc_block_size": config.mc_block_size,
        "mc_probability_beat_benchmark": float(mc["beats_benchmark"].mean()),
        "mc_probability_loss": float((mc["strategy_total_return"] < 0).mean()),
        "mc_total_return_p05": float(mc["strategy_total_return"].quantile(0.05)),
        "mc_total_return_p50": float(mc["strategy_total_return"].quantile(0.50)),
        "mc_total_return_p95": float(mc["strategy_total_return"].quantile(0.95)),
        "mc_max_drawdown_p05": float(mc["strategy_max_drawdown"].quantile(0.05)),
        "mc_max_drawdown_p50": float(mc["strategy_max_drawdown"].quantile(0.50)),
        "mc_sharpe_p05": float(mc["strategy_sharpe"].quantile(0.05)),
        "mc_sharpe_p50": float(mc["strategy_sharpe"].quantile(0.50)),
    }
    return mc, summary


# =============================================================================
# 7. WALK-FORWARD CON PURGE GAP
# =============================================================================


def walk_forward_windows(
    n: int, config: BacktestConfig
) -> Generator[Tuple[int, int, int, int], None, None]:
    """Genera ventanas (train_start, train_end, test_start, test_end) para walk-forward.

    Incluye purge_bars de separación entre IS y OOS para evitar que el estado
    de los indicadores de la vela final de IS contamine OOS.

    Yields:
        (train_start, train_end, test_start, test_end) — índices enteros.
    """
    start = 0
    while True:
        train_end = start + config.train_bars
        test_start = train_end + config.purge_bars
        test_end = test_start + config.test_bars

        if test_end > n:
            break

        yield start, train_end, test_start, test_end
        start += config.step_bars


def run_walk_forward(
    df: pd.DataFrame,
    signal_series: pd.Series,
    config: BacktestConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Ejecuta walk-forward completo y devuelve (oos_backtest, window_metrics).

    El backtest OOS se construye concatenando solo las ventanas OOS de cada
    ventana del walk-forward. No incluye datos IS.

    `window_metrics` contiene una fila por ventana con métricas IS y OOS.
    """
    n = len(df)
    windows = list(walk_forward_windows(n, config))

    if not windows:
        raise ValueError(
            f"Dataset demasiado pequeño para walk-forward. "
            f"Necesitas al menos {config.train_bars + config.purge_bars + config.test_bars} barras; "
            f"tienes {n}."
        )

    oos_pieces: List[pd.DataFrame] = []
    window_rows: List[Dict] = []

    for win_idx, (ts, te, os, oe) in enumerate(windows, start=1):
        # Ventana IS
        is_df = df.iloc[ts:te].copy().reset_index(drop=True)
        is_sig = signal_series.iloc[ts:te].copy().reset_index(drop=True)
        is_bt = run_backtest_core(is_df, is_sig, config)
        is_m = compute_metrics(is_bt, config, label="is")
        is_tm = compute_trade_metrics(is_bt, config, label="is")

        # Ventana OOS
        oos_df = df.iloc[os:oe].copy().reset_index(drop=True)
        oos_sig = signal_series.iloc[os:oe].copy().reset_index(drop=True)
        oos_bt = run_backtest_core(oos_df, oos_sig, config)
        oos_m = compute_metrics(oos_bt, config, label="oos")
        oos_tm = compute_trade_metrics(oos_bt, config, label="oos")

        oos_pieces.append(oos_bt)

        row: Dict = {
            "window": win_idx,
            "train_start_idx": ts,
            "train_end_idx": te,
            "test_start_idx": os,
            "test_end_idx": oe,
            "train_start_utc": str(df["open_time_utc"].iloc[ts]),
            "train_end_utc": str(df["open_time_utc"].iloc[min(te - 1, n - 1)]),
            "test_start_utc": str(df["open_time_utc"].iloc[os]),
            "test_end_utc": str(df["open_time_utc"].iloc[min(oe - 1, n - 1)]),
        }
        row.update(is_m)
        row.update(is_tm)
        row.update(oos_m)
        row.update(oos_tm)
        window_rows.append(row)

    oos_combined = pd.concat(oos_pieces, ignore_index=True)
    window_metrics = pd.DataFrame(window_rows)
    return oos_combined, window_metrics


# =============================================================================
# 8. SMOKE-TEST CLI
# =============================================================================


class BuyAndHoldSignal:
    """Señal trivial para smoke-test: siempre comprado."""
    name = "buy_and_hold"

    @staticmethod
    def generate(df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


class SmaCrossSignal:
    """Señal de cruce de medias para smoke-test funcional."""
    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 100) -> None:
        self.fast = fast
        self.slow = slow

    def generate(self, df: pd.DataFrame) -> pd.Series:
        fast_ma = df["close"].rolling(self.fast, min_periods=self.fast).mean()
        slow_ma = df["close"].rolling(self.slow, min_periods=self.slow).mean()
        signal = (fast_ma > slow_ma).astype(float)
        signal[fast_ma.isna() | slow_ma.isna()] = 0.0
        return signal


def run_smoke_test(args: argparse.Namespace) -> None:
    """Smoke-test completo del motor Q6."""
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(symbol=args.symbol, interval=args.interval)

    print(f"\nQ6 Engine Smoke-Test")
    print(f"Input: {input_path}")
    print(f"Config: {config.symbol} {config.interval}")

    # Cargar datos
    df = load_and_standardize(input_path)
    print(f"Filas cargadas: {len(df):,} ({df['open_time_utc'].min()} → {df['open_time_utc'].max()})")

    # Test 1: BuyAndHold full backtest
    print("\n[Test 1] Buy & Hold full backtest...")
    bh_signal = BuyAndHoldSignal.generate(df)
    bh_bt = run_backtest_core(df, bh_signal, config)
    bh_m = compute_metrics(bh_bt, config, label="bh")
    print(f"  Total return: {bh_m['bh_total_return']:.2%}")
    print(f"  CAGR: {bh_m['bh_cagr']:.2%}")
    print(f"  Sharpe: {bh_m['bh_sharpe']:.3f}")
    print(f"  Max DD: {bh_m['bh_max_drawdown']:.2%}")

    # Test 2: SMA Cross walk-forward
    print("\n[Test 2] SMA Cross walk-forward...")
    sma = SmaCrossSignal(fast=20, slow=100)
    sma_signal = sma.generate(df)
    oos_bt, wf_metrics = run_walk_forward(df, sma_signal, config)
    print(f"  Ventanas walk-forward generadas: {len(wf_metrics)}")
    if not oos_bt.empty:
        oos_m = compute_metrics(oos_bt, config, label="oos")
        print(f"  OOS total return (concatenado): {oos_m['oos_total_return']:.2%}")
        print(f"  OOS Sharpe: {oos_m['oos_sharpe']:.3f}")

    # Test 3: Monte Carlo pareado
    print("\n[Test 3] Monte Carlo pareado (reducido: 100 runs)...")
    _cfg_fast = BacktestConfig(
        symbol=config.symbol, interval=config.interval,
        mc_runs=100, mc_block_size=24, random_seed=42
    )
    if not oos_bt.empty:
        s_ret = oos_bt["strategy_return"].to_numpy(dtype=float)
        b_ret = oos_bt["bh_return"].to_numpy(dtype=float)
        _, mc_summary = monte_carlo_block_bootstrap(s_ret, b_ret, _cfg_fast)
        print(f"  P(beat benchmark): {mc_summary['mc_probability_beat_benchmark']:.2%}")
        print(f"  P(loss): {mc_summary['mc_probability_loss']:.2%}")
        print(f"  Return P50: {mc_summary['mc_total_return_p50']:.2%}")

    # Guardar resultados
    wf_metrics.to_csv(output_dir / "q6_wf_metrics.csv", index=False)
    bh_bt[["open_time_utc", "equity", "drawdown"]].to_csv(output_dir / "q6_bh_equity.csv", index=False)
    if not oos_bt.empty:
        oos_bt[["open_time_utc", "equity", "drawdown"]].to_csv(output_dir / "q6_oos_equity.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "config": asdict(config),
        "n_rows": int(len(df)),
        "n_wf_windows": int(len(wf_metrics)),
        "execution_model": "signal[t] → position[t+1], open-to-open return",
        "return_model": "open[t+1]/open[t] - 1",
        "ddof": 1,
        "duplicate_handling": "keep=last",
    }
    (output_dir / "q6_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    print(f"\nResultados en: {output_dir}")
    print("Q6 Engine OK — todos los tests pasaron.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Q6 — Motor de Backtest Unificado (smoke-test)")
    parser.add_argument("--input", required=True, help="Dataset Parquet/CSV.")
    parser.add_argument("--output-dir", required=True, help="Carpeta de salida del smoke-test.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_smoke_test(args)


if __name__ == "__main__":
    main()
