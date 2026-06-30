-- create_tables.sql
-- Ejecutar una vez para crear el schema de la base de datos.
--
-- Uso:
--   psql -U postgres -d crypto_pipeline -f sql/create_tables.sql

-- ── 1. klines ──────────────────────────────────────────────────────────────
-- Almacena los datos OHLCV limpios y enriquecidos con features calculadas.
-- Es la fuente de verdad de todo el pipeline.

CREATE TABLE IF NOT EXISTS klines (
    id                   SERIAL PRIMARY KEY,
    symbol               VARCHAR(20)   NOT NULL,          -- ej. 'BTCUSDT'
    interval             VARCHAR(5)    NOT NULL,           -- ej. '1h'
    open_time_utc        TIMESTAMPTZ   NOT NULL,
    close_time_utc       TIMESTAMPTZ   NOT NULL,
    open                 NUMERIC(20,8) NOT NULL,
    high                 NUMERIC(20,8) NOT NULL,
    low                  NUMERIC(20,8) NOT NULL,
    close                NUMERIC(20,8) NOT NULL,
    volume               NUMERIC(30,8),
    quote_asset_volume   NUMERIC(30,2),
    number_of_trades     INTEGER,
    -- Features calculadas por src/transform.py
    open_to_open_return  NUMERIC(12,8),  -- retorno open[t+1]/open[t] - 1
    log_return           NUMERIC(12,8),  -- log(close/open)
    rolling_vol_24h      NUMERIC(12,8),  -- volatilidad rolling 24 barras
    sma_20               NUMERIC(20,8),
    sma_50               NUMERIC(20,8),
    weekday              SMALLINT,       -- 0=Lun, 1=Mar, ..., 6=Dom
    next_bar_weekday     SMALLINT,       -- weekday de la siguiente barra
    market_regime        VARCHAR(10),    -- 'Bull', 'Bear', 'Lateral'
    -- Auditoría
    loaded_at            TIMESTAMPTZ DEFAULT NOW(),
    -- Restricciones de integridad
    CONSTRAINT klines_positive_prices CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
    CONSTRAINT klines_ohlc_valid      CHECK (high >= low),
    CONSTRAINT klines_unique          UNIQUE (symbol, interval, open_time_utc)
);

-- Índice para queries por símbolo + tiempo (el más común en análisis)
CREATE INDEX IF NOT EXISTS idx_klines_symbol_time
    ON klines (symbol, interval, open_time_utc);

-- Índice para análisis de regímenes
CREATE INDEX IF NOT EXISTS idx_klines_regime
    ON klines (symbol, market_regime, open_time_utc);


-- ── 2. backtest_results ────────────────────────────────────────────────────
-- Almacena los KPIs de cada estrategia por activo.
-- run_date permite comparar resultados de diferentes corridas del pipeline.

CREATE TABLE IF NOT EXISTS backtest_results (
    id             SERIAL PRIMARY KEY,
    run_date       DATE          NOT NULL DEFAULT CURRENT_DATE,
    symbol         VARCHAR(20)   NOT NULL,
    strategy_name  VARCHAR(100)  NOT NULL,  -- ej. 'H2_WEDNESDAY_LONG'
    start_date     DATE,
    end_date       DATE,
    -- KPIs principales
    total_return   NUMERIC(10,6),  -- retorno total del período
    bh_return      NUMERIC(10,6),  -- retorno Buy & Hold del mismo período
    excess_return  NUMERIC(10,6),  -- total_return - bh_return
    sharpe_ratio   NUMERIC(8,4),
    max_drawdown   NUMERIC(8,6),
    cagr           NUMERIC(8,6),
    -- Estadísticas de trades
    total_trades   INTEGER,
    win_rate       NUMERIC(6,4),   -- fracción de trades positivos
    profit_factor  NUMERIC(8,4),   -- gross_profit / gross_loss
    -- Exposición y benchmark
    pos_pct        NUMERIC(6,4),   -- fracción del tiempo en posición
    beat_months    INTEGER,        -- meses en que superó a BH
    total_months   INTEGER,
    beat_pct       NUMERIC(6,4),   -- beat_months / total_months
    -- Validación estadística
    mc_percentile  NUMERIC(6,4),   -- percentil Monte Carlo (0–1)
    -- Auditoría
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_symbol_strategy
    ON backtest_results (symbol, strategy_name, run_date DESC);


-- ── 3. data_quality ────────────────────────────────────────────────────────
-- Log de cada corrida de validación.
-- Permite auditar la calidad de los datos sin reabrir el código.

CREATE TABLE IF NOT EXISTS data_quality (
    id            SERIAL PRIMARY KEY,
    run_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    symbol        VARCHAR(20)   NOT NULL,
    interval      VARCHAR(5)    NOT NULL,
    check_name    VARCHAR(50)   NOT NULL,   -- ej. 'NULL_CHECK', 'OHLC_VALIDITY'
    severity      VARCHAR(10)   NOT NULL,   -- 'PASS', 'WARN', 'FAIL'
    total_rows    INTEGER       NOT NULL,
    passed_rows   INTEGER       NOT NULL,
    failed_rows   INTEGER       GENERATED ALWAYS AS (total_rows - passed_rows) STORED,
    pass_rate     NUMERIC(6,4)  GENERATED ALWAYS AS (
                      CASE WHEN total_rows > 0
                           THEN ROUND(passed_rows::NUMERIC / total_rows, 4)
                           ELSE NULL END
                  ) STORED,
    detail        TEXT                      -- descripción del problema si severity != 'PASS'
);

CREATE INDEX IF NOT EXISTS idx_dq_symbol_run
    ON data_quality (symbol, run_at DESC);
