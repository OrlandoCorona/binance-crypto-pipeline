"""
database.py — Operaciones de lectura y escritura en PostgreSQL.

Responsabilidad: conectar a la base de datos y mover datos entre
DataFrames y tablas PostgreSQL. No transforma datos.

Funciones principales:
    get_connection()                → conexión psycopg2
    save_klines(df, conn)           → guarda DataFrame en tabla klines
    save_quality_results(results)   → guarda resultados de validación
    save_backtest_results(df)       → guarda KPIs de backtesting
    load_klines(symbol, conn)       → carga klines desde PostgreSQL
"""

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from sqlalchemy import create_engine
from contextlib import contextmanager
from datetime import date

from src.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, LOG_LEVEL, LOGS_DIR
from src.utils import get_logger, Timer
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert

log = get_logger(__name__, log_dir=LOGS_DIR, level=LOG_LEVEL)

# Columnas de klines que se guardan en PostgreSQL
# (excluye columnas internas de Binance que no añaden valor)
KLINES_COLUMNS = [
    "symbol", "interval", "open_time_utc", "close_time_utc",
    "open", "high", "low", "close", "volume", "quote_asset_volume",
    "number_of_trades", "open_to_open_return", "log_return",
    "rolling_vol_24h", "sma_20", "sma_50",
    "weekday", "next_bar_weekday", "market_regime",
]


# ── Conexión ───────────────────────────────────────────────────────────────

def get_connection() -> psycopg2.extensions.connection:
    """
    Abre y devuelve una conexión psycopg2 a PostgreSQL.

    Usa las variables de entorno definidas en .env (vía src/config.py).
    Lanza un error descriptivo si la conexión falla.

    Example:
        conn = get_connection()
        conn.close()
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
        log.info("Connected to PostgreSQL: %s@%s:%s/%s", DB_USER, DB_HOST, DB_PORT, DB_NAME)
        return conn
    except psycopg2.OperationalError as e:
        log.error(
            "Cannot connect to PostgreSQL at %s:%s/%s — %s\n"
            "Check your .env file and make sure PostgreSQL is running.",
            DB_HOST, DB_PORT, DB_NAME, e
        )
        raise


def _get_engine():
    """SQLAlchemy engine para pd.to_sql() — uso interno."""
    url = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(url)


# ── Escritura ──────────────────────────────────────────────────────────────
def save_klines(df: pd.DataFrame, if_exists: str = "append") -> int:
    """
    Guarda un DataFrame de klines en la tabla PostgreSQL 'klines' usando UPSERT.

    Si ya existe una fila con:
        symbol + interval + open_time_utc

    entonces actualiza sus valores en lugar de fallar por duplicado.

    Args:
        df:         DataFrame con columnas definidas en KLINES_COLUMNS.
        if_exists:  Se conserva por compatibilidad, pero ya no se usa con to_sql().

    Returns:
        Número de filas procesadas.
    """
    if df.empty:
        log.warning("save_klines: received empty DataFrame — nothing saved")
        return 0

    symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "unknown"

    # Seleccionar solo las columnas permitidas que existen en el DataFrame
    cols_present = [c for c in KLINES_COLUMNS if c in df.columns]
    df_to_save = df[cols_present].copy()

    # Convertir NaN/NaT a None para PostgreSQL
    df_to_save = df_to_save.astype(object).where(pd.notna(df_to_save), None)

    conflict_cols = ["symbol", "interval", "open_time_utc"]

    missing_conflict_cols = [c for c in conflict_cols if c not in cols_present]
    if missing_conflict_cols:
        raise ValueError(
            f"Faltan columnas obligatorias para UPSERT: {missing_conflict_cols}"
        )

    update_cols = [c for c in cols_present if c not in conflict_cols]

    columns_sql = ", ".join(cols_present)

    update_sql = ", ".join(
        [f"{col} = EXCLUDED.{col}" for col in update_cols]
    )

    query = f"""
        INSERT INTO klines ({columns_sql})
        VALUES %s
        ON CONFLICT (symbol, interval, open_time_utc)
        DO UPDATE SET
            {update_sql};
    """

    rows = [tuple(row) for row in df_to_save.to_numpy()]

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                with Timer(f"save_klines {symbol}") as t:
                    execute_values(
                        cur,
                        query,
                        rows,
                        page_size=1000,
                    )

            conn.commit()

        log.info(
            "Upserted %d rows for %s to klines in %s",
            len(df_to_save), symbol, t.elapsed_str
        )

        return len(df_to_save)

    except Exception as e:
        log.error("Failed to upsert klines for %s: %s", symbol, e)
        raise


def save_quality_results(results: list[dict], symbol: str, interval: str = "1h") -> None:
    """
    Guarda los resultados de validate_ohlcv() en la tabla data_quality.

    Operación idempotente: borra los checks anteriores del mismo
    symbol+interval antes de insertar los nuevos, en la misma transacción.

    Args:
        results:  Lista de CheckResult de src.validation.validate_ohlcv()
        symbol:   Símbolo al que pertenecen los datos validados.
        interval: Intervalo temporal.

    Example:
        from src.validation import validate_ohlcv
        results = validate_ohlcv(df)
        save_quality_results(results, symbol='BTCUSDT')
    """
    if not results:
        return

    rows = [
        (
            symbol,
            interval,
            r["check_name"],
            r["severity"],
            r["total_rows"],
            r["passed_rows"],
            r.get("detail", ""),
        )
        for r in results
    ]

    insert_query = """
        INSERT INTO data_quality
            (symbol, interval, check_name, severity, total_rows, passed_rows, detail)
        VALUES %s
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM data_quality WHERE symbol = %s AND interval = %s",
                    (symbol, interval),
                )
                execute_values(cur, insert_query, rows)
            conn.commit()
        log.info("Saved %d quality checks for %s", len(rows), symbol)
    except Exception as e:
        log.error("Failed to save quality results for %s: %s", symbol, e)
        raise


def save_backtest_results(df_results: pd.DataFrame) -> None:
    """
    Guarda KPIs de backtesting en la tabla backtest_results.

    Args:
        df_results: DataFrame con las columnas de backtest_results
                    (ver sql/create_tables.sql).

    Example:
        import pandas as pd
        row = pd.DataFrame([{
            'symbol': 'BTCUSDT',
            'strategy_name': 'H2_WEDNESDAY_LONG',
            'total_return': 0.139,
            'sharpe_ratio': 0.66,
            ...
        }])
        save_backtest_results(row)
    """
    if df_results.empty:
        log.warning("save_backtest_results: empty DataFrame")
        return

    if "run_date" not in df_results.columns:
        df_results = df_results.copy()
        df_results["run_date"] = date.today()

    try:
        engine = _get_engine()
        df_results.to_sql("backtest_results", con=engine, if_exists="append", index=False)
        log.info("Saved %d backtest result rows", len(df_results))
    except Exception as e:
        log.error("Failed to save backtest results: %s", e)
        raise


# ── Lectura ────────────────────────────────────────────────────────────────

def load_klines(
    symbol: str,
    interval: str = "1h",
    start_date: str = None,
    end_date: str = None,
) -> pd.DataFrame:
    """
    Carga klines desde PostgreSQL para un símbolo y rango de fechas.

    Args:
        symbol:     Símbolo, ej. 'BTCUSDT'
        interval:   Intervalo, ej. '1h'
        start_date: Fecha inicio 'YYYY-MM-DD' (opcional)
        end_date:   Fecha fin   'YYYY-MM-DD' (opcional)

    Returns:
        DataFrame con los datos de klines.

    Example:
        df = load_klines('BTCUSDT', start_date='2024-01-01')
        print(df.shape)
    """
    conditions = ["symbol = %(symbol)s", "interval = %(interval)s"]
    params: dict = {"symbol": symbol, "interval": interval}

    if start_date:
        conditions.append("open_time_utc >= %(start_date)s")
        params["start_date"] = start_date
    if end_date:
        conditions.append("open_time_utc <= %(end_date)s")
        params["end_date"] = end_date

    query = f"""
        SELECT *
        FROM klines
        WHERE {' AND '.join(conditions)}
        ORDER BY open_time_utc ASC
    """

    try:
        engine = _get_engine()
        with Timer(f"load_klines {symbol}") as t:
            df = pd.read_sql(query, con=engine, params=params)
        log.info("Loaded %d rows for %s from PostgreSQL in %s", len(df), symbol, t.elapsed_str)
        return df
    except Exception as e:
        log.error("Failed to load klines for %s: %s", symbol, e)
        raise
