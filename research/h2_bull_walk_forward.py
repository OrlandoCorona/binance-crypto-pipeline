"""H2 reversión + Bull: protocolo causal, spot, evaluación histórica exploratoria.

Ejecutar desde la raíz: python -m research.h2_bull_walk_forward --help
Este módulo nunca descarga datos ni evalúa el holdout junio-septiembre de 2026.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_PROTOCOL = Path(__file__).with_name("h2_bull_protocol.json")
HOUR = pd.Timedelta(hours=1)
ANNUAL_HOURS = 365 * 24
STRATEGIES = ("buy_and_hold", "h2", "h2_bull", "bull_only")


def read_protocol(path: Path = DEFAULT_PROTOCOL) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    # Una modificación requiere otro protocolo explícito, no optimizar el filtro.
    locked = {
        "symbol": "BTCUSDT", "interval": "1h", "train_months": 24,
        "test_months": 3, "bull_window_hours": 4800, "bull_band": 0.02,
        "commission": 0.001, "slippage": 0.0005, "cash_yield": 0.0,
        "selection": "net_return_then_fewer_fills_then_grid_order",
        "development_end_exclusive": "2026-06-01T00:00:00Z",
        "holdout_end_exclusive": "2026-10-01T00:00:00Z",
    }
    for key, value in locked.items():
        if cfg.get(key) != value:
            raise ValueError(f"El protocolo acordado exige {key}={value!r}")
    if cfg["initial_capital"] <= 0:
        raise ValueError("El capital debe ser positivo")
    return cfg


def validate_market(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Rechaza problemas; no imputa, deduplica ni ordena silenciosamente."""
    needed = ["open_time_utc", "open", "high", "low", "close", "volume"]
    missing = set(needed) - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {sorted(missing)}")
    out = df[needed].copy().reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(out.open_time_utc):
        raise ValueError("open_time_utc debe ser datetime o ISO UTC, no epoch ambiguo")
    out["open_time_utc"] = pd.to_datetime(out.open_time_utc, utc=True, errors="raise")
    t = out.open_time_utc
    if len(out) < 2 or t.isna().any() or t.duplicated().any():
        raise ValueError("Timestamps nulos/duplicados o datos insuficientes")
    if not t.diff().iloc[1:].eq(HOUR).all() or not t.eq(t.dt.floor("h")).all():
        raise ValueError("Se exige una serie ordenada, horaria UTC y sin huecos")
    if (t >= pd.Timestamp(cfg["development_end_exclusive"])).any():
        raise ValueError("Holdout protegido: se rechazan filas desde junio de 2026")
    if (t + HOUR > pd.Timestamp.now(tz="UTC")).any():
        raise ValueError("Hay velas que todavía no han cerrado")
    for col in needed[1:]:
        out[col] = pd.to_numeric(out[col], errors="raise")
    values = out[needed[1:]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV contiene valores no finitos")
    if (out[["open", "high", "low", "close"]] <= 0).any().any() or (out.volume < 0).any():
        raise ValueError("Precios no positivos o volumen negativo")
    if ((out.high < out[["open", "close", "low"]].max(axis=1)) |
            (out.low > out[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC inconsistente")
    for col, expected in (("symbol", cfg["symbol"]), ("interval", cfg["interval"])):
        if col in df and not df[col].eq(expected).all():
            raise ValueError(f"El archivo mezcla o no corresponde a {col}={expected}")
    return out


def features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Solo pasado y cierre actual. 200 días = 4.800 horas, no 200 horas."""
    out = df.copy()
    log_return = np.log(out.close / out.close.shift(1))
    for lag in cfg["parameter_grid"]["lookback"]:
        out[f"return_{lag}"] = out.close.pct_change(lag, fill_method=None)
    for window in cfg["parameter_grid"]["vol_window"]:
        # Conserva la definición de volatilidad de Q4.
        out[f"vol_{window}"] = log_return.rolling(
            window, min_periods=max(5, window // 3)).std(ddof=0)
    window = cfg["bull_window_hours"]
    out["bull"] = out.close > out.close.rolling(window, min_periods=window).mean() * (1 + cfg["bull_band"])
    out["weekend"] = out.open_time_utc.dt.dayofweek >= 5
    return out


def candidates(cfg: dict):
    grid = cfg["parameter_grid"]
    for values in itertools.product(*grid.values()):
        yield dict(zip(grid, values))


def fit_threshold(train: pd.DataFrame, params: dict) -> float:
    value = float(train[f"vol_{params['vol_window']}"].quantile(params["max_vol_quantile"]))
    if not np.isfinite(value):
        raise ValueError("IS sin datos suficientes para calibrar volatilidad")
    return value


def h2_signal(frame: pd.DataFrame, params: dict, threshold: float) -> np.ndarray:
    result = ((frame[f"return_{params['lookback']}"] < -abs(params["drop_threshold"])) &
              (frame[f"vol_{params['vol_window']}"] <= threshold))
    if params["avoid_weekend"]:
        result &= ~frame.weekend
    return result.to_numpy(dtype=np.int8)


def signal_at_next_open(close_signal: np.ndarray, previous_close_signal: int = 0) -> np.ndarray:
    raw = np.asarray(close_signal)
    if not np.isin(raw, [0, 1]).all() or previous_close_signal not in (0, 1):
        raise ValueError("Las señales deben ser 0 o 1")
    return np.r_[previous_close_signal, raw[:-1]].astype(np.int8)


def net_returns(opens, positions, commission, slippage, liquidate=True):
    """Posiciones ejecutadas en cada apertura; última apertura solo valora/liquida.

    Compra: cantidad = efectivo / (open * (1+s) * (1+c)).
    Venta: efectivo = cantidad * open * (1-s) * (1-c).
    Los costes son sobre notional; nunca se financia una comisión con deuda.
    """
    opens = np.asarray(opens, dtype=float)
    positions = np.asarray(positions)
    if len(opens) < 2 or len(positions) != len(opens) - 1:
        raise ValueError("Se necesita una apertura terminal adicional")
    if not np.isfinite(opens).all() or (opens <= 0).any() or not np.isin(positions, [0, 1]).all():
        raise ValueError("Precios inválidos o exposición fuera de {0,1}")
    if not (0 <= commission < 1 and 0 <= slippage < 1):
        raise ValueError("Costes inválidos")
    changes = np.diff(np.r_[0, positions])
    factors = np.ones(len(positions))
    factors[changes == 1] = 1 / ((1 + slippage) * (1 + commission))
    factors[changes == -1] = (1 - slippage) * (1 - commission)
    gross = np.where(positions == 1, opens[1:] / opens[:-1], 1.0)
    returns = gross * factors - 1
    if liquidate and positions[-1]:
        returns[-1] = (1 + returns[-1]) * (1 - slippage) * (1 - commission) - 1
    fills = int(np.abs(changes).sum() + (bool(positions[-1]) and liquidate))
    return returns, fills


def metrics(returns, capital=10000.0) -> dict:
    r = np.asarray(returns, dtype=float)
    if not len(r) or not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("Retornos inválidos")
    equity = capital * np.cumprod(1 + r)
    peaks = np.maximum.accumulate(np.r_[capital, equity])[1:]
    dd = float(np.min(equity / peaks - 1))
    cagr = float(np.expm1(np.log1p(r).sum() * ANNUAL_HOURS / len(r)))
    std = r.std(ddof=1) if len(r) > 1 else 0
    sharpe = float(r.mean() / std * np.sqrt(ANNUAL_HOURS)) if std > 0 else np.nan
    return {"total_return": float(equity[-1] / capital - 1), "cagr": cagr,
            "sharpe": sharpe, "max_drawdown": dd,
            "calmar": cagr / abs(dd) if dd < 0 else np.nan,
            "final_equity": float(equity[-1]), "hours": len(r)}


def ledger(opens, times, positions, cfg) -> pd.DataFrame:
    changes = np.diff(np.r_[0, positions, 0])
    entries = np.flatnonzero(changes == 1)
    exits = np.flatnonzero(changes == -1)
    buy = np.asarray(opens)[entries] * (1 + cfg["slippage"])
    sell = np.asarray(opens)[exits] * (1 - cfg["slippage"])
    net = sell * (1 - cfg["commission"]) / (buy * (1 + cfg["commission"])) - 1
    before = cfg["initial_capital"] * np.r_[1.0, np.cumprod(1 + net)[:-1]] if len(net) else np.array([])
    return pd.DataFrame({"entry_time": np.asarray(times)[entries], "exit_time": np.asarray(times)[exits],
                         "entry_fill": buy, "exit_fill": sell, "net_return": net, "pnl": before * net,
                         "terminal_liquidation": exits == len(opens) - 1})


def trade_metrics(trades: pd.DataFrame) -> dict:
    pnl = trades.pnl.to_numpy()
    losses = -pnl[pnl < 0].sum()
    return {"completed_trades": len(pnl), "win_rate": float((pnl > 0).mean()) if len(pnl) else np.nan,
            "profit_factor": float(pnl[pnl > 0].sum() / losses) if losses > 0 else np.nan}


@dataclass(frozen=True)
class Window:
    train_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp  # apertura terminal, sin leer close de esta barra
    partial: bool


def windows(df: pd.DataFrame, cfg: dict) -> list[Window]:
    # Primer mes completo de datos; calentamiento causal queda dentro de IS.
    first, last = df.open_time_utc.iloc[[0, -1]]
    start = first.normalize().replace(day=1)
    if start < first:
        start += pd.DateOffset(months=1)
    result = []
    test = start + pd.DateOffset(months=cfg["train_months"])
    while test < last:
        planned_end = test + pd.DateOffset(months=cfg["test_months"])
        end = min(planned_end, last)
        result.append(Window(test - pd.DateOffset(months=cfg["train_months"]), test, end, end < planned_end))
        test = planned_end
    if not result:
        raise ValueError("Se requieren 24 meses IS y al menos un intervalo OOS")
    return result


def select_h2(train: pd.DataFrame, cfg: dict):
    """Recibe exclusivamente IS. Rankea H2 sin Bull para aislar efecto del filtro."""
    best = None
    rows = []
    for index, params in enumerate(candidates(cfg)):
        threshold = fit_threshold(train, params)
        signal = h2_signal(train, params, threshold)
        position = signal_at_next_open(signal)[:-1]
        ret, fills = net_returns(train.open, position, cfg["commission"], cfg["slippage"])
        total = float(np.expm1(np.log1p(ret).sum()))
        key = (total, -fills, -index)
        rows.append({"candidate": index, **params, "vol_threshold": threshold,
                     "net_return_is": total, "fills_is": fills})
        if best is None or key > best[0]:
            best = (key, params, threshold, index)
    if best is None:
        raise ValueError("La cuadrícula H2 está vacía")
    return best[1], best[2], best[3], rows


def diagnostic_warnings(is_metrics: dict, oos_metrics: dict, cfg: dict) -> list[str]:
    d = cfg["diagnostics"]
    warnings = []
    for label, m in (("IS", is_metrics), ("OOS", oos_metrics)):
        if m["sharpe"] > d["sharpe_upper"]:
            warnings.append(f"{label}: Sharpe >3; revisar ejecución, fuga de información y selección múltiple")
        if m["total_return"] > 0 and abs(m["max_drawdown"]) < d["small_drawdown_abs"]:
            warnings.append(f"{label}: drawdown <1% con ganancia; revisar exposición, muestra y costes")
    a, b = is_metrics["sharpe"], oos_metrics["sharpe"]
    if np.isfinite(a) and np.isfinite(b) and abs(a - b) <= d["similar_sharpe_abs_difference"]:
        warnings.append("Sharpe IS/OOS muy similar; revisar solapamiento y calibración. No prueba un error")
    if oos_metrics.get("completed_trades", 0) < d["few_completed_trades"]:
        warnings.append("Pocas operaciones OOS: incertidumbre alta; no equivale a ausencia de ventaja")
    return warnings


def paired_bootstrap(a, b, cfg: dict) -> list[dict]:
    """IC exploratorio de exceso anualizado de log-retorno, bloques circulares pareados.

    No corrige selección histórica, no reentrena H2 y no certifica ventaja futura.
    """
    delta = np.log1p(np.asarray(a)) - np.log1p(np.asarray(b))
    result = []
    settings = cfg["bootstrap"]
    for block in settings["block_hours"]:
        if len(delta) < 2 * block:
            result.append({"block_hours": block, "low": None, "high": None, "status": "muestra_insuficiente"})
            continue
        rng = np.random.default_rng(settings["seed"])
        extended = np.r_[delta, delta[:block]]
        prefix = np.r_[0., np.cumsum(extended)]
        full, tail = divmod(len(delta), block)
        samples = np.empty(settings["runs"])
        for i in range(settings["runs"]):
            starts = rng.integers(0, len(delta), size=full)
            total = (prefix[starts + block] - prefix[starts]).sum()
            if tail:
                start = rng.integers(0, len(delta))
                total += prefix[start + tail] - prefix[start]
            samples[i] = total / len(delta) * ANNUAL_HOURS
        low, high = np.quantile(samples, [0.025, 0.975])
        result.append({"block_hours": block, "low": float(low), "high": float(high), "status": "exploratorio"})
    return result


def run_experiment(raw: pd.DataFrame, cfg: dict) -> dict:
    frame = features(validate_market(raw, cfg), cfg)
    all_windows = windows(frame, cfg)
    selection_rows, score_rows, is_rows, oos_parts = [], [], [], []
    for number, w in enumerate(all_windows):
        train = frame.loc[(frame.open_time_utc >= w.train_start) & (frame.open_time_utc < w.test_start)].copy()
        params, threshold, selected, scores = select_h2(train, cfg)
        selection_rows.append({"window": number, "train_start": w.train_start, "test_start": w.test_start,
                               "test_end": w.test_end, "partial": w.partial, "candidate": selected,
                               "vol_threshold": threshold, **params})
        score_rows.extend({"window": number, **score} for score in scores)
        raw_is = h2_signal(train, params, threshold)
        bull_is = train.bull.to_numpy(dtype=np.int8)
        for strategy, sig in zip(STRATEGIES, (np.ones(len(train)), raw_is, raw_is * bull_is, bull_is)):
            # BH compra en primera apertura, estrategias necesitan cierre anterior.
            pos = np.ones(len(train) - 1, dtype=np.int8) if strategy == "buy_and_hold" else signal_at_next_open(sig)[:-1]
            r, fills = net_returns(train.open, pos, cfg["commission"], cfg["slippage"])
            trades = ledger(train.open.to_numpy(), train.open_time_utc.to_numpy(), pos, cfg)
            is_rows.append({"window": number, "strategy": strategy, "split": "IS",
                            **metrics(r, cfg["initial_capital"]), **trade_metrics(trades),
                            "exposure": float(pos.mean()), "fills": fills})
        # Incluye cierre inmediatamente anterior para primera decisión OOS.
        test = frame.loc[(frame.open_time_utc >= w.test_start - HOUR) & (frame.open_time_utc <= w.test_end)].copy()
        raw_test = h2_signal(test, params, threshold)
        bull_test = test.bull.to_numpy(dtype=np.int8)
        part = test.iloc[1:-1][["open_time_utc", "open"]].copy()
        part["window"] = number
        for strategy, sig in zip(STRATEGIES, (np.ones(len(test)), raw_test, raw_test * bull_test, bull_test)):
            part[strategy] = np.asarray(sig[:-2], dtype=np.int8)
        oos_parts.append(part)
    # Simulación única: no liquida/recompra artificialmente entre ventanas OOS.
    scheduled = pd.concat(oos_parts, ignore_index=True)
    endpoint = frame.loc[frame.open_time_utc == all_windows[-1].test_end].iloc[0]
    opens = np.r_[scheduled.open.to_numpy(), endpoint.open]
    times = list(scheduled.open_time_utc) + [endpoint.open_time_utc]
    curves, trade_tables, totals, oos_rows = [], [], [], []
    for strategy in STRATEGIES:
        pos = scheduled[strategy].to_numpy(dtype=np.int8)
        ret, fills = net_returns(opens, pos, cfg["commission"], cfg["slippage"])
        eq = cfg["initial_capital"] * np.cumprod(1 + ret)
        curve = pd.DataFrame({"interval_start": times[:-1], "valuation_time": times[1:],
                              "window": scheduled.window, "strategy": strategy, "position": pos,
                              "net_return": ret, "equity": eq,
                              "drawdown": eq / np.maximum.accumulate(np.r_[cfg["initial_capital"], eq])[1:] - 1})
        trades = ledger(opens, times, pos, cfg)
        trades["strategy"] = strategy
        curves.append(curve)
        trade_tables.append(trades)
        totals.append({"strategy": strategy, "split": "OOS_concat",
                       **metrics(ret, cfg["initial_capital"]), **trade_metrics(trades),
                       "exposure": float(pos.mean()), "fills": fills})
        for number, group in curve.groupby("window", sort=True):
            # Conteo por cierre; una operación puede empezar en la ventana anterior.
            start, end = group.interval_start.iloc[0], group.valuation_time.iloc[-1]
            closed = trades.loc[(trades.exit_time >= start) & (trades.exit_time < end)]
            if number == len(all_windows) - 1:
                closed = trades.loc[(trades.exit_time >= start) & (trades.exit_time <= end)]
            m = {"window": int(number), "strategy": strategy, "split": "OOS",
                 **metrics(group.net_return, cfg["initial_capital"]),
                 "completed_trades": len(closed), "exposure": float(group.position.mean())}
            reference = next(row for row in is_rows if row["window"] == number and row["strategy"] == strategy)
            m["warnings"] = " | ".join(diagnostic_warnings(reference, m, cfg))
            oos_rows.append(m)
    curve = pd.concat(curves, ignore_index=True)
    returns = curve.pivot(index="interval_start", columns="strategy", values="net_return")
    intervals = {other: paired_bootstrap(returns.h2_bull, returns[other], cfg)
                 for other in ("buy_and_hold", "h2", "bull_only")}
    return {"selection": pd.DataFrame(selection_rows), "candidate_scores": pd.DataFrame(score_rows),
            "window_metrics": pd.DataFrame(is_rows + oos_rows), "summary": pd.DataFrame(totals),
            "equity_curves": curve, "trades": pd.concat(trade_tables, ignore_index=True),
            "bootstrap": intervals}


def report(results: dict, cfg: dict) -> str:
    summary = results["summary"].set_index("strategy")
    target, benchmark = summary.loc["h2_bull"], summary.loc["buy_and_hold"]
    intervals = results["bootstrap"]["buy_and_hold"]
    positive = all(item["low"] is not None and item["low"] > 0 for item in intervals)
    negative = all(item["high"] is not None and item["high"] < 0 for item in intervals)
    verdict = "INCONCLUSO: la incertidumbre no permite distinguir una ventaja consistente."
    if positive:
        verdict = "Ventaja histórica exploratoria en retorno; pendiente de holdout y paper, sin confirmación independiente."
    elif negative:
        verdict = "Desventaja histórica exploratoria en retorno frente a buy-and-hold."
    risk = "menor" if target.max_drawdown > benchmark.max_drawdown else "igual o mayor"
    lines = ["# H2 + Bull — investigación histórica", "", verdict, "",
             "Todo el histórico hasta mayo de 2026 fue explorado previamente. OOS significa separación temporal de cada ajuste; NO histórico intacto.",
             "El bootstrap es descriptivo: no corrige selección de hipótesis ni recalibra modelos dentro de cada remuestreo.", "",
             f"Retorno H2+Bull: {target.total_return:.2%}; buy-and-hold: {benchmark.total_return:.2%}.",
             f"Drawdown observado: {risk} magnitud que buy-and-hold. Esto es distinto del resultado de rentabilidad.",
             "Calmar combina rentabilidad y drawdown: no constituye por sí solo una medida pura de riesgo.", "",
             "| Estrategia | Retorno OOS | CAGR | Sharpe | Max DD | Calmar | Operaciones | Exposición |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in summary.iterrows():
        lines.append(f"| {name} | {row.total_return:.2%} | {row.cagr:.2%} | {row.sharpe:.2f} | {row.max_drawdown:.2%} | {row.calmar:.2f} | {int(row.completed_trades)} | {row.exposure:.2%} |")
    lines += ["", "## Incertidumbre: IC 95% del exceso anualizado de log-retorno", ""]
    for other, items in results["bootstrap"].items():
        for item in items:
            bounds = "muestra insuficiente" if item["low"] is None else f"[{item['low']:.4f}, {item['high']:.4f}]"
            lines.append(f"- H2+Bull frente a {other}; bloques {item['block_hours']} h: {bounds}.")
    lines += ["", "## Advertencias por ventana", ""]
    warned = results["window_metrics"].dropna(subset=["warnings"])
    warned = warned.loc[warned.warnings.ne("")]
    for _, row in warned.iterrows():
        lines.append(f"- Ventana {row.window}, {row.strategy}: {row.warnings}.")
    if warned.empty:
        lines.append("Sin alertas heurísticas; esto no demuestra ausencia de errores ni sobreajuste.")
    lines += ["", "## Interpretación y límites", "",
              "- Bull fijo: SMA de 4.800 horas y banda +2%; calentamiento completo. No se optimiza con los resultados.",
              "- H2 seleccionada por retorno neto IS; desempate por menos ejecuciones y orden fijo de cuadrícula.",
              "- IS es ajuste retrospectivo (incluido su cuantil de volatilidad); no una simulación independiente de entrenamiento online.",
              "- Comisión 0,10% y slippage 0,05% por lado, también en buy-and-hold; BTC spot o USDT sin rendimiento.",
              "- Curva OOS conserva posiciones entre ventanas; liquidación solo en apertura terminal común. Coste terminal asignado al último intervalo.",
              "- La última apertura disponible es el límite de valoración: no se utiliza junio para completar el retorno de mayo.",
              "- Ventanas finales parciales están identificadas en selection.csv; CAGR/Sharpe cortos pueden ser inestables.",
              "- final_equity por ventana normaliza a capital inicial para comparación; equity_curves.csv contiene el capital continuo real de la simulación.",
              "- No hay stop-loss, take-profit ni circuit breaker en esta fase. El paper trading todavía no está implementado.",
              "- No se ha descargado ni evaluado el holdout. No cambiar reglas después de observarlo."]
    return "\n".join(lines) + "\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="CSV/Parquet BTCUSDT 1h, exclusivamente anterior a junio 2026")
    parser.add_argument("--output", type=Path, required=True, help="Directorio nuevo; no sobrescribe experimentos")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    args = parser.parse_args()
    protocol_hash = sha256(args.protocol)
    code_hash = sha256(Path(__file__))
    cfg = read_protocol(args.protocol)
    if args.output.exists():
        parser.error("El directorio de salida ya existe; utiliza uno nuevo")
    dataset_hash = sha256(args.input)
    if args.input.suffix.lower() == ".parquet":
        raw = pd.read_parquet(args.input)
    elif args.input.suffix.lower() == ".csv":
        raw = pd.read_csv(args.input)
    else:
        parser.error("Formato esperado: .csv o .parquet")
    results = run_experiment(raw, cfg)
    if (sha256(args.input) != dataset_hash or sha256(args.protocol) != protocol_hash or
            sha256(Path(__file__)) != code_hash):
        raise RuntimeError("Dataset, código o protocolo cambiaron durante la ejecución; repetir con versiones estables")
    args.output.mkdir(parents=True, exist_ok=False)
    for name, result in results.items():
        if isinstance(result, pd.DataFrame):
            result.to_csv(args.output / f"{name}.csv", index=False)
        else:
            (args.output / f"{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    manifest = {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "protocol": cfg,
                "protocol_sha256": protocol_hash, "code_sha256": code_hash,
                "dataset_path": str(args.input.resolve()), "dataset_sha256": dataset_hash,
                "rows": len(raw), "numpy": np.__version__, "pandas": pd.__version__,
                "historical_oos_is_independent_holdout": False, "holdout_evaluated": False}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.output / "report.md").write_text(report(results, cfg), encoding="utf-8")
    print(f"Reporte generado: {(args.output / 'report.md').resolve()}")


if __name__ == "__main__":
    main()
