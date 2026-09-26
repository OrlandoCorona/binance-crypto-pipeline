"""Pruebas herméticas: python -m unittest tests.test_h2_bull_walk_forward -v."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.h2_bull_walk_forward import (
    candidates, diagnostic_warnings, features, fit_threshold, h2_signal,
    ledger, metrics, net_returns, paired_bootstrap, read_protocol,
    report, run_experiment, select_h2, signal_at_next_open, validate_market, windows,
)


def market(n=1000, start="2022-01-01"):
    rng = np.random.default_rng(123)
    price = 30000 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    close = price * np.exp(rng.normal(0, 0.001, n))
    return pd.DataFrame({"open_time_utc": pd.date_range(start, periods=n, freq="h", tz="UTC"),
                         "open": price, "high": np.maximum(price, close) * 1.001,
                         "low": np.minimum(price, close) * 0.999, "close": close,
                         "volume": np.ones(n)})


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_protocol()

    def test_close_signal_executes_only_next_open(self):
        # Salto 100 -> 1000 antes de la compra: la estrategia NO lo gana.
        pos = signal_at_next_open(np.array([1, 0, 0]))[:-1]
        np.testing.assert_array_equal(pos, [0, 1])
        returns, fills = net_returns([100, 1000, 1100], pos, .001, .0005)
        self.assertEqual(returns[0], 0)
        expected = 1.1 * (1 - .0005) * (1 - .001) / ((1 + .0005) * (1 + .001))
        self.assertAlmostEqual(np.prod(1 + returns), expected)
        self.assertEqual(fills, 2)

    def test_roundtrip_costs_and_terminal_liquidation(self):
        expected = .9995 * .999 / (1.0005 * 1.001)
        for position in ([1, 0], [1, 1]):
            ret, fills = net_returns([100, 100, 100], position, .001, .0005)
            self.assertAlmostEqual(np.prod(1 + ret), expected)
            self.assertEqual(fills, 2)
            self.assertAlmostEqual(metrics(ret)["max_drawdown"], expected - 1)

    def test_cash_has_no_costs_or_yield(self):
        ret, fills = net_returns([100, 1000, 1], [0, 0], .001, .0005)
        np.testing.assert_array_equal(ret, [0, 0])
        self.assertEqual(fills, 0)
        self.assertTrue(np.isnan(metrics(ret)["sharpe"]))

    def test_ledger_matches_cash_accounting_multiple_trades(self):
        prices = [100, 120, 115, 90, 95, 110]
        positions = [1, 1, 0, 1, 1]
        ret, fills = net_returns(prices, positions, .001, .0005)
        trades = ledger(prices, pd.date_range("2024-01-01", periods=6, freq="h"), positions, self.cfg)
        # Oráculo: efectivo y unidades, sin usar fórmulas de retornos del motor.
        cash, units = 10000., 0.
        for price, desired in zip(prices, list(positions) + [0]):
            if desired and not units:
                units = cash / (price * 1.0005 * 1.001)
                cash = 0.
            elif not desired and units:
                cash = units * price * .9995 * .999
                units = 0.
        self.assertAlmostEqual(metrics(ret)["final_equity"], cash)
        self.assertAlmostEqual(10000 + trades.pnl.sum(), cash)
        self.assertEqual(fills, 4)
        self.assertEqual(len(trades), 2)
        self.assertTrue(trades.terminal_liquidation.iloc[-1])

    def test_rejects_short_leverage_and_nonfinite(self):
        for position in ([-1], [2], [.5], [np.nan]):
            with self.assertRaises(ValueError):
                net_returns([100, 100], position, .001, .0005)


class CausalityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_protocol()

    def test_bull_requires_4800_bars_and_fixed_band(self):
        df = market(4900)
        df.loc[:, ["open", "high", "low", "close"]] = 100.
        df.loc[4799:, "close"] = 105.
        frame = features(df, self.cfg)
        self.assertFalse(frame.bull.iloc[:4799].any())
        self.assertTrue(frame.bull.iloc[4799])
        self.assertEqual(self.cfg["bull_window_hours"], 200 * 24)

    def test_future_mutation_cannot_change_past_features_or_oos_signals(self):
        raw = market(6000)
        original = features(raw, self.cfg)
        params = next(candidates(self.cfg))
        threshold = fit_threshold(original.iloc[:5000], params)
        changed = raw.copy()
        changed.loc[5500:, ["open", "high", "low", "close"]] *= 10
        modified = features(changed, self.cfg)
        pd.testing.assert_frame_equal(original.iloc[:5500], modified.iloc[:5500])
        self.assertEqual(threshold, fit_threshold(modified.iloc[:5000], params))
        sig_a = h2_signal(original, params, threshold) * original.bull.to_numpy()
        sig_b = h2_signal(modified, params, threshold) * modified.bull.to_numpy()
        # Aun la posición de apertura 5500 solo conoce el cierre 5499.
        np.testing.assert_array_equal(signal_at_next_open(sig_a)[:5501], signal_at_next_open(sig_b)[:5501])

    def test_prefix_matches_full_history(self):
        raw = market(6000)
        pd.testing.assert_frame_equal(features(raw.iloc[:5300], self.cfg), features(raw, self.cfg).iloc[:5300])

    def test_selection_is_train_only_and_ties_deterministic(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["parameter_grid"] = {"lookback": [3, 6], "drop_threshold": [1.0], "vol_window": [24],
                                 "max_vol_quantile": [.5], "avoid_weekend": [False]}
        train = features(market(600), cfg)
        _, _, selected, scores = select_h2(train, cfg)
        self.assertEqual(selected, 0)
        self.assertEqual(len(scores), 2)
        self.assertEqual(scores[0]["net_return_is"], 0)


class DataAndIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_protocol()

    def test_protect_holdout(self):
        with self.assertRaisesRegex(ValueError, "Holdout"):
            validate_market(market(3, "2026-05-31 23:00"), self.cfg)

    def test_gaps_duplicates_unsorted_and_bad_ohlc_rejected(self):
        frame = market(20)
        broken = [frame.drop(3), pd.concat([frame, frame.iloc[:1]]), frame.iloc[::-1]]
        bad_price = frame.copy()
        bad_price.loc[1, "low"] = bad_price.loc[1, "high"] * 2
        broken.append(bad_price)
        for df in broken:
            with self.assertRaises(ValueError):
                validate_market(df, self.cfg)

    def test_diagnostics_and_degenerate_bootstrap(self):
        m = {"sharpe": 4., "total_return": .1, "max_drawdown": -.001, "completed_trades": 2}
        messages = diagnostic_warnings(m, m, self.cfg)
        self.assertTrue(any("Sharpe >3" in s for s in messages))
        self.assertTrue(any("similar" in s for s in messages))
        self.assertTrue(any("drawdown" in s for s in messages))
        cfg = copy.deepcopy(self.cfg)
        cfg["bootstrap"]["runs"] = 10
        intervals = paired_bootstrap(np.zeros(1500), np.zeros(1500), cfg)
        self.assertTrue(all(x["low"] == 0 and x["high"] == 0 for x in intervals))

    def test_walk_forward_end_to_end_and_no_boundary_reset(self):
        raw = market(24 * 365 * 2 + 24 * 200)
        cfg = copy.deepcopy(self.cfg)
        # Reducir búsqueda solo en fixture; protocolo de producción conserva 216 candidatos.
        cfg["parameter_grid"] = {"lookback": [3, 6], "drop_threshold": [.005], "vol_window": [24],
                                 "max_vol_quantile": [.5], "avoid_weekend": [False]}
        cfg["bootstrap"]["runs"] = 10
        result = run_experiment(raw, cfg)
        planned = windows(raw, cfg)
        self.assertGreaterEqual(len(planned), 3)
        self.assertEqual(planned[0].test_start, pd.Timestamp("2024-01-01", tz="UTC"))
        self.assertTrue(planned[-1].partial)
        bh = result["equity_curves"].query("strategy == 'buy_and_hold'")
        self.assertTrue(bh.interval_start.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all())
        self.assertEqual(bh.valuation_time.iloc[-1], raw.open_time_utc.iloc[-1])
        summary = result["summary"].set_index("strategy")
        self.assertEqual(summary.loc["buy_and_hold", "fills"], 2)
        self.assertEqual(summary.loc["buy_and_hold", "completed_trades"], 1)
        first_open = raw.loc[raw.open_time_utc == planned[0].test_start, "open"].iloc[0]
        expected = raw.open.iloc[-1] / first_open * .9995 * .999 / (1.0005 * 1.001) - 1
        self.assertAlmostEqual(summary.loc["buy_and_hold", "total_return"], expected)
        self.assertEqual(len(result["window_metrics"]), len(planned) * 8)
        self.assertIn("INCONCLUSO", report({**result, "bootstrap": {
            **result["bootstrap"], "buy_and_hold": [{"block_hours": 24, "low": -.1, "high": .1}]}}, cfg))
        # Cambiar todo el OOS no cambia selección/umbral del primer IS.
        changed = raw.copy()
        changed.loc[changed.open_time_utc >= planned[0].test_start, ["open", "high", "low", "close"]] *= 2
        rerun = run_experiment(changed, cfg)
        pd.testing.assert_series_equal(result["selection"].iloc[0], rerun["selection"].iloc[0])
        # Ledger y curva concuerdan para las cuatro alternativas.
        for strategy in summary.index:
            pnl = result["trades"].query("strategy == @strategy").pnl.sum()
            self.assertAlmostEqual(cfg["initial_capital"] + pnl, summary.loc[strategy, "final_equity"])
        # Cada posición OOS corresponde al cierre anterior con el ajuste de SU IS.
        enriched = features(raw, cfg).set_index("open_time_utc", drop=False)
        for _, selected in result["selection"].iterrows():
            params = {key: selected[key] for key in cfg["parameter_grid"]}
            h2_curve = result["equity_curves"].loc[
                (result["equity_curves"].strategy == "h2") &
                (result["equity_curves"].window == selected.window)]
            prior = enriched.loc[h2_curve.interval_start - pd.Timedelta(hours=1)]
            np.testing.assert_array_equal(h2_curve.position, h2_signal(prior, params, selected.vol_threshold))

    def test_cli_exports_manifest_report_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic_ONLY.csv"
            market(24 * 365 * 2 + 24 * 5).to_csv(source, index=False)
            output = root / "result"
            command = [sys.executable, "-m", "research.h2_bull_walk_forward",
                       "--input", str(source), "--output", str(output)]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["holdout_evaluated"])
            self.assertEqual(len(manifest["dataset_sha256"]), 64)
            scores = pd.read_csv(output / "candidate_scores.csv")
            self.assertEqual(len(scores), 216)
            self.assertIn("NO histórico intacto", (output / "report.md").read_text(encoding="utf-8"))
            again = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("ya existe", again.stderr)


if __name__ == "__main__":
    unittest.main()
