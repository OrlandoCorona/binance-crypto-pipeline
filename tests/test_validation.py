"""
Unit tests for OHLCVValidator.

Why test the validator specifically?
  Data quality is the first thing that breaks in production.
  Demonstrating that the validator itself is correct — via unit tests with
  adversarial inputs — shows the same discipline interviewers expect.
"""

import numpy as np
import pandas as pd
import pytest

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from crypto_pipeline.validation.ohlcv_validator import OHLCVValidator, Severity


def _make_df(n: int = 100, start: str = "2024-01-01", freq: str = "h") -> pd.DataFrame:
    """Generate clean synthetic OHLCV data."""
    times = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    opens  = np.random.uniform(30_000, 70_000, n)
    highs  = opens + np.random.uniform(0, 1_000, n)
    lows   = opens - np.random.uniform(0, 1_000, n)
    closes = opens + np.random.uniform(-500, 500, n)
    closes = np.clip(closes, lows, highs)
    return pd.DataFrame({
        "open_time_utc": times,
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": np.random.uniform(1, 100, n),
    })


class TestOHLCVValidator:

    def test_clean_data_passes_all(self):
        df = _make_df(200)
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        assert not report.has_failures()
        assert not report.has_warnings()

    def test_null_detection(self):
        df = _make_df(50)
        df.loc[5, "close"] = np.nan
        df.loc[10, "open"] = np.nan
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        null_check = next(r for r in report.results if r.check_name == "NULL_CHECK")
        assert null_check.rows_failed > 0

    def test_ohlc_invalidity_detected(self):
        df = _make_df(50)
        # Make high < low on row 3 → invalid
        df.loc[3, "high"] = df.loc[3, "low"] - 100
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        ohlc_check = next(r for r in report.results if r.check_name == "OHLC_VALIDITY")
        assert ohlc_check.rows_failed >= 1

    def test_negative_price_detected(self):
        df = _make_df(50)
        df.loc[7, "open"] = -1.0
        report = OHLCVValidator(df, "BTCUSDT", "1h", fail_threshold=0.001).run_all()
        neg = next(r for r in report.results if r.check_name == "NEGATIVE_PRICES")
        assert neg.rows_failed >= 1
        assert neg.status == Severity.FAIL

    def test_duplicate_detection(self):
        df = _make_df(50)
        df_dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
        report = OHLCVValidator(df_dup, "BTCUSDT", "1h").run_all()
        dup = next(r for r in report.results if r.check_name == "DUPLICATE_BARS")
        assert dup.rows_failed == 1

    def test_temporal_order_detection(self):
        df = _make_df(50)
        # Swap rows 10 and 11 → out-of-order timestamp
        df.iloc[[10, 11]] = df.iloc[[11, 10]].values
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        order = next(r for r in report.results if r.check_name == "TEMPORAL_ORDER")
        assert order.rows_failed >= 1

    def test_time_gap_detection(self):
        df = _make_df(100)
        # Drop rows 40-45 → creates a 6-hour gap
        df = df.drop(index=list(range(40, 46))).reset_index(drop=True)
        report = OHLCVValidator(df, "BTCUSDT", "1h", expected_freq="h").run_all()
        gap = next(r for r in report.results if r.check_name == "TIME_GAPS")
        assert gap.rows_failed >= 6

    def test_summary_format(self):
        df = _make_df(50)
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        summary = report.summary()
        assert "BTCUSDT" in summary
        assert "PASS" in summary or "WARN" in summary

    def test_json_serialisable(self):
        import json
        df = _make_df(50)
        report = OHLCVValidator(df, "BTCUSDT", "1h").run_all()
        # should not raise
        json.loads(report.to_json())


class TestValidationReport:

    def test_has_failures_false_on_clean(self):
        df = _make_df(100)
        report = OHLCVValidator(df, "ETHUSDT", "1h").run_all()
        assert not report.has_failures()

    def test_has_failures_true_on_negatives(self):
        df = _make_df(10)
        df["open"] = -1
        report = OHLCVValidator(df, "ETHUSDT", "1h", fail_threshold=0.001).run_all()
        assert report.has_failures()
