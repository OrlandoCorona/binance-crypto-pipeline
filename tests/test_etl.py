"""
Integration-style tests for the Silver ETL transform.
These tests operate on in-memory data (no network, no DB) — fast and hermetic.
"""

import numpy as np
import pandas as pd
import pytest
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from crypto_pipeline.etl.silver import _cast_columns, _add_derived_columns, SILVER_SCHEMA
from crypto_pipeline.etl.gold import build_monthly_returns, build_daily_ohlcv


def _make_silver_df(n: int = 200, freq: str = "h") -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    opens  = np.random.uniform(40_000, 60_000, n)
    highs  = opens + np.random.uniform(0, 500, n)
    lows   = opens - np.random.uniform(0, 500, n)
    closes = np.clip(opens + np.random.uniform(-250, 250, n), lows, highs)
    df = pd.DataFrame({
        "open_time_utc":  times,
        "close_time_utc": times + pd.Timedelta(hours=1),
        "open":   opens.astype(str),   # intentionally wrong type → test cast
        "high":   highs.astype(str),
        "low":    lows.astype(str),
        "close":  closes.astype(str),
        "volume": np.random.uniform(1, 50, n),
        "quote_volume": np.random.uniform(50_000, 200_000, n),
        "trade_count": np.random.randint(100, 1000, n),
        "taker_buy_volume": np.random.uniform(0.5, 25, n),
        "taker_buy_quote_volume": np.random.uniform(25_000, 100_000, n),
    })
    return df


class TestSilverCast:

    def test_numeric_columns_cast(self):
        df = _make_silver_df(50)
        result = _cast_columns(df)
        for col in ("open", "high", "low", "close"):
            assert result[col].dtype == "float64", f"{col} should be float64"

    def test_timestamps_are_utc(self):
        df = _make_silver_df(50)
        result = _cast_columns(df)
        assert str(result["open_time_utc"].dt.tz) == "UTC"

    def test_bad_numeric_coerced_to_nan(self):
        df = _make_silver_df(10)
        df.loc[3, "open"] = "NOT_A_NUMBER"
        result = _cast_columns(df)
        assert pd.isna(result.loc[3, "open"])


class TestDerivedColumns:

    def test_bar_return_sign(self):
        df = _make_silver_df(100)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        # bar_return sign matches close vs open direction
        bull_bars = df[df["close"] > df["open"]]
        assert (bull_bars["bar_return"] > 0).all()

    def test_log_return_finite(self):
        df = _make_silver_df(100)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        assert np.isfinite(df["log_return"]).all()

    def test_range_pct_non_negative(self):
        df = _make_silver_df(100)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        assert (df["range_pct"] >= 0).all()

    def test_weekday_in_range(self):
        df = _make_silver_df(100)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        assert df["weekday"].between(0, 6).all()

    def test_year_month_format(self):
        df = _make_silver_df(100)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        assert df["year_month"].str.match(r"\d{4}-\d{2}").all()


class TestGoldTransforms:

    def _make_parquet(self, n=500) -> Path:
        df = _make_silver_df(n)
        df = _cast_columns(df)
        df = _add_derived_columns(df)
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            df.to_parquet(f.name, index=False)
            return Path(f.name)

    def test_monthly_returns_row_count(self):
        parquet = self._make_parquet(500)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = build_monthly_returns(parquet, "BTCUSDT", Path(tmpdir))
            result = pd.read_parquet(out)
            assert len(result) >= 1
            assert "monthly_return" in result.columns

    def test_daily_ohlcv_has_required_columns(self):
        parquet = self._make_parquet(500)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = build_daily_ohlcv(parquet, "BTCUSDT", Path(tmpdir))
            result = pd.read_parquet(out)
            for col in ("open_price", "close_price", "daily_return", "date_id"):
                assert col in result.columns, f"Missing: {col}"

    def test_daily_ohlcv_date_id_is_int(self):
        parquet = self._make_parquet(200)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = build_daily_ohlcv(parquet, "BTCUSDT", Path(tmpdir))
            result = pd.read_parquet(out)
            assert result["date_id"].dtype in ("int64", "int32")
