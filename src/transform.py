"""
transform.py — Feature engineering y enriquecimiento de datos OHLCV.

Responsabilidad: recibir un DataFrame validado y agregar columnas calculadas.
No lee ni escribe archivos. Las funciones son puras (entrada → salida).

Funciones principales:
    add_returns(df)     → agrega open_to_open_return, log_return
    add_features(df)    → agrega SMA, volatilidad, weekday
    add_regime(df)      → agrega market_regime (Bull/Bear/Lateral)
    transform(df)       → aplica todo en secuencia (convenience function)
"""

import pandas as pd
import numpy as np

from src.config import REGIME_BULL_THRESHOLD, REGIME_BEAR_THRESHOLD, LOG_LEVEL, LOGS_DIR
from src.utils import get_logger, Timer

log = get_logger(__name__, log_dir=LOGS_DIR, level=LOG_LEVEL)


def add_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega retornos calculados con el modelo open-to-open.

    open_to_open_return[t] = open[t+1] / open[t] - 1

    Esto refleja el retorno real de una posición abierta al open de t
    y cerrada al open de t+1. La última fila siempre es NaN porque
    no existe el open[t+1].

    log_return[t] = log(close[t] / open[t]) — retorno intra-barra.

    Args:
        df: DataFrame con columna 'open' y 'close'.

    Returns:
        DataFrame con columnas adicionales:
            open_to_open_return, log_return
    """
    df = df.copy()

    # open-to-open: return[t] depende de open[t+1] → shift(-1)
    next_open = df["open"].shift(-1)
    df["open_to_open_return"] = next_open / df["open"] - 1

    # log return intra-barra
    df["log_return"] = np.log(df["close"] / df["open"])

    log.debug("add_returns: added open_to_open_return and log_return")
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega features técnicas y de calendario.

    Features de precio:
        sma_20            — media móvil simple 20 barras
        sma_50            — media móvil simple 50 barras
        rolling_vol_24h   — desviación estándar de retornos (ventana 24h)

    Features de calendario:
        weekday           — día de la semana de open_time_utc (0=Lun, 6=Dom)
        next_bar_weekday  — día de la semana de la siguiente barra

    next_bar_weekday es crítico para las estrategias de calendario:
    nos dice en qué día del semana va a estar *activa* una posición,
    no en qué día se generó la señal.

    Args:
        df: DataFrame con columnas 'close', 'open_to_open_return', 'open_time_utc'.

    Returns:
        DataFrame con columnas adicionales.
    """
    df = df.copy()

    # Indicadores técnicos
    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_50"] = df["close"].rolling(50).mean()

    if "open_to_open_return" in df.columns:
        df["rolling_vol_24h"] = df["open_to_open_return"].rolling(24).std()

    # Features de calendario
    df["weekday"]          = df["open_time_utc"].dt.weekday  # 0=Lun, 6=Dom
    df["next_bar_weekday"] = df["open_time_utc"].shift(-1).dt.weekday

    log.debug("add_features: added SMA, volatility, and weekday features")
    return df


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clasifica cada barra en un régimen de mercado mensual.

    El régimen se calcula con el retorno mensual del activo:
        Bull   → retorno mensual >= +5%  (config.REGIME_BULL_THRESHOLD)
        Bear   → retorno mensual <= -5%  (config.REGIME_BEAR_THRESHOLD)
        Lateral → cualquier otro caso

    Cada barra hereda el régimen del mes al que pertenece.

    Args:
        df: DataFrame con 'open_time_utc' y 'close'.

    Returns:
        DataFrame con columna 'market_regime' (str o None).
    """
    df = df.copy()

    if df.empty or "close" not in df.columns:
        log.warning("add_regime: DataFrame empty or missing 'close'")
        df["market_regime"] = None
        return df

    # Retorno mensual = (último close del mes / primer close del mes) - 1
    df_temp = df[["open_time_utc", "close"]].copy()
    df_temp["month"] = df_temp["open_time_utc"].dt.to_period("M")

    monthly = (
        df_temp.groupby("month")["close"]
        .agg(first_close="first", last_close="last")
        .assign(monthly_return=lambda x: x["last_close"] / x["first_close"] - 1)
    )

    def classify(ret: float) -> str:
        if ret >= REGIME_BULL_THRESHOLD:
            return "Bull"
        elif ret <= REGIME_BEAR_THRESHOLD:
            return "Bear"
        return "Lateral"

    monthly["market_regime"] = monthly["monthly_return"].apply(classify)

    # Hacer merge sin timezone en el período
    df_temp["month"] = df_temp["open_time_utc"].dt.tz_localize(None).dt.to_period("M")
    monthly_map = monthly["market_regime"].to_dict()
    df["market_regime"] = df_temp["month"].map(monthly_map)

    counts = df["market_regime"].value_counts().to_dict()
    log.info("add_regime: Bull=%s, Bear=%s, Lateral=%s bars",
             counts.get("Bull", 0), counts.get("Bear", 0), counts.get("Lateral", 0))

    return df


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica toda la cadena de transformaciones en secuencia.

    Equivale a:
        df = add_returns(df)
        df = add_features(df)
        df = add_regime(df)

    Args:
        df: DataFrame OHLCV validado.

    Returns:
        DataFrame enriquecido listo para cargar a PostgreSQL.

    Example:
        from src.ingestion import load_symbol
        from src.transform import transform

        df = load_symbol('BTCUSDT')
        df_enriched = transform(df)
        print(df_enriched.columns.tolist())
    """
    symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "unknown"
    log.info("Transforming %s (%d rows)", symbol, len(df))

    with Timer(f"transform {symbol}") as t:
        df = add_returns(df)
        df = add_features(df)
        df = add_regime(df)

    log.info("Transform complete for %s in %s — %d features added",
             symbol, t.elapsed_str,
             df.shape[1] - 8)  # 8 columnas originales aprox.

    return df
