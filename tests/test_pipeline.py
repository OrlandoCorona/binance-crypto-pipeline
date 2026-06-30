"""
test_pipeline.py — Tests básicos del ETL pipeline.

Ejecutar con:
    pytest tests/ -v

Estos tests verifican que las funciones principales del pipeline
producen el output correcto sin necesitar una base de datos real.
"""

import pytest
import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Asegurar que src/ esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Fixtures (datos de prueba) ─────────────────────────────────────────────

@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """DataFrame OHLCV mínimo para pruebas — no necesita archivos reales."""
    dates = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    np.random.seed(42)
    opens  = 40000 + np.cumsum(np.random.randn(100) * 50)
    closes = opens  + np.random.randn(100) * 30
    # Guarantee OHLC integrity: high = max(open,close) + margin; low = min(open,close) - margin
    margin_high = np.random.uniform(10, 100, 100)
    margin_low  = np.random.uniform(10, 100, 100)
    return pd.DataFrame({
        "symbol":        "BTCUSDT",
        "interval":      "1h",
        "open_time_utc":  dates,
        "close_time_utc": dates + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open":           opens,
        "high":           np.maximum(opens, closes) + margin_high,
        "low":            np.minimum(opens, closes) - margin_low,
        "close":          closes,
        "volume":         np.random.uniform(100, 500, 100),
    })


@pytest.fixture
def ohlcv_with_errors(sample_ohlcv) -> pd.DataFrame:
    """DataFrame con errores intencionales para probar la validación."""
    df = sample_ohlcv.copy()
    df.loc[5, "open"] = None          # valor nulo
    df.loc[10, "high"] = -100         # precio negativo
    df.loc[20, "high"] = df.loc[20, "low"] - 1  # high < low (viola OHLC)
    return df


# ── Tests de utilidades ────────────────────────────────────────────────────

class TestUtils:
    def test_get_logger_returns_logger(self):
        from src.utils import get_logger
        log = get_logger("test")
        assert log is not None
        assert log.name == "test"

    def test_get_logger_no_duplicate_handlers(self):
        from src.utils import get_logger
        log1 = get_logger("dedup_test")
        log2 = get_logger("dedup_test")
        assert len(log2.handlers) == len(log1.handlers)

    def test_pct_format(self):
        from src.utils import pct
        assert pct(0.1532) == "15.32%"
        assert pct(0.0, decimals=1) == "0.0%"
        assert pct(-0.05) == "-5.00%"

    def test_timer_measures_elapsed(self):
        import time
        from src.utils import Timer
        with Timer("test") as t:
            time.sleep(0.05)
        assert t.elapsed >= 0.04
        assert "s" in t.elapsed_str

    def test_ensure_dirs_creates_path(self, tmp_path):
        from src.utils import ensure_dirs
        new_dir = tmp_path / "a" / "b" / "c"
        ensure_dirs(new_dir)
        assert new_dir.exists()


# ── Tests de configuración ─────────────────────────────────────────────────

class TestConfig:
    def test_symbols_defined(self):
        from src.config import SYMBOLS
        assert "BTCUSDT" in SYMBOLS
        assert len(SYMBOLS) >= 1

    def test_db_port_is_int(self):
        from src.config import DB_PORT
        assert isinstance(DB_PORT, int)
        assert DB_PORT > 0

    def test_data_dirs_are_paths(self):
        from src.config import RAW_DIR, PROCESSED_DIR, EXPORTS_DIR
        from pathlib import Path
        assert isinstance(RAW_DIR, Path)
        assert isinstance(PROCESSED_DIR, Path)
        assert isinstance(EXPORTS_DIR, Path)

    def test_sigma_threshold_positive(self):
        from src.config import OUTLIER_SIGMA
        assert OUTLIER_SIGMA > 0


# ── Tests de transformación ────────────────────────────────────────────────

class TestTransform:
    def test_add_returns_creates_column(self, sample_ohlcv):
        from src.transform import add_returns
        df = add_returns(sample_ohlcv)
        assert "open_to_open_return" in df.columns
        assert "log_return" in df.columns

    def test_open_to_open_no_lookahead(self, sample_ohlcv):
        """El retorno en t debe usar open[t] y open[t+1], no datos futuros."""
        from src.transform import add_returns
        df = add_returns(sample_ohlcv).reset_index(drop=True)
        # open_to_open_return[i] = open[i+1] / open[i] - 1
        expected = df["open"].shift(-1) / df["open"] - 1
        # La última fila debe ser NaN (no hay t+1)
        assert pd.isna(df["open_to_open_return"].iloc[-1])
        pd.testing.assert_series_equal(
            df["open_to_open_return"].iloc[:-1].round(8),
            expected.iloc[:-1].round(8),
            check_names=False
        )

    def test_add_features_weekday_range(self, sample_ohlcv):
        from src.transform import add_returns, add_features
        df = add_returns(sample_ohlcv)
        df = add_features(df)
        assert "weekday" in df.columns
        assert "next_bar_weekday" in df.columns
        valid = df["weekday"].dropna()
        assert valid.between(0, 6).all()

    def test_add_regime_valid_values(self, sample_ohlcv):
        from src.transform import add_returns, add_features, add_regime
        df = add_returns(sample_ohlcv)
        df = add_features(df)
        df = add_regime(df)
        assert "market_regime" in df.columns
        valid_regimes = {"Bull", "Bear", "Lateral", None}
        actual = set(df["market_regime"].unique()) | {None}
        assert actual.issubset(valid_regimes)


# ── Tests de validación ────────────────────────────────────────────────────

class TestValidation:
    def test_clean_data_passes(self, sample_ohlcv):
        from src.validation import validate_ohlcv
        results = validate_ohlcv(sample_ohlcv)
        fails = [r for r in results if r["severity"] == "FAIL"]
        assert len(fails) == 0, f"Clean data should have no FAILs, got: {fails}"

    def test_null_detected(self, ohlcv_with_errors):
        from src.validation import validate_ohlcv
        results = validate_ohlcv(ohlcv_with_errors)
        null_check = next((r for r in results if r["check_name"] == "NULL_CHECK"), None)
        assert null_check is not None
        assert null_check["failed_rows"] >= 1

    def test_negative_price_detected(self, ohlcv_with_errors):
        from src.validation import validate_ohlcv
        results = validate_ohlcv(ohlcv_with_errors)
        neg_check = next((r for r in results if r["check_name"] == "NEGATIVE_PRICES"), None)
        assert neg_check is not None
        assert neg_check["failed_rows"] >= 1

    def test_ohlc_violation_detected(self, ohlcv_with_errors):
        from src.validation import validate_ohlcv
        results = validate_ohlcv(ohlcv_with_errors)
        ohlc_check = next((r for r in results if r["check_name"] == "OHLC_VALIDITY"), None)
        assert ohlc_check is not None
        assert ohlc_check["failed_rows"] >= 1
