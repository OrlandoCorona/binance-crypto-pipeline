# Architecture

## Data Flow

```
┌──────────────────────────────────────────────────┐
│  DATA SOURCES                                    │
│                                                  │
│  Binance Vision API                              │
│  https://data.binance.vision/data/spot/          │
│  monthly/klines/{SYMBOL}/{INTERVAL}/             │
└─────────────────────────┬────────────────────────┘
                          │  ZIP → CSV (monthly files)
                          ▼
┌──────────────────────────────────────────────────┐
│  RAW LAYER  (data/raw/)                          │
│                                                  │
│  BTCUSDT-1h-2021-06.zip                          │
│  BTCUSDT-1h-2021-07.zip  ...                     │
│  ETHUSDT-1h-2021-06.zip  ...                     │
└─────────────────────────┬────────────────────────┘
                          │  src/ingestion.py
                          ▼
┌──────────────────────────────────────────────────┐
│  VALIDATION  (src/validation.py)                 │
│                                                  │
│  • NULL / missing values                         │
│  • OHLC integrity: high >= max(open,close)       │
│  • Negative prices or volume                     │
│  • Duplicate timestamps                          │
│  • Temporal ordering                             │
│  • Time gaps (missing hours)                     │
│  • Return outliers (> 4 sigma)                   │
│                                                  │
│  Results written to: PostgreSQL > data_quality   │
└─────────────────────────┬────────────────────────┘
                          │  src/transform.py
                          ▼
┌──────────────────────────────────────────────────┐
│  PROCESSED LAYER  (data/processed/)              │
│                                                  │
│  Parquet files with enriched features:           │
│  • open_to_open_return                           │
│  • log_return                                    │
│  • rolling_volatility_24h                        │
│  • sma_20, sma_50                                │
│  • weekday, next_bar_weekday                     │
│  • market_regime (Bull / Bear / Lateral)         │
└─────────────────────────┬────────────────────────┘
                          │  src/database.py
                          ▼
┌──────────────────────────────────────────────────┐
│  PostgreSQL  (crypto_pipeline database)          │
│                                                  │
│  Tables:                                         │
│  • klines           → clean OHLCV + features     │
│  • backtest_results → strategy performance KPIs  │
│  • data_quality     → validation run logs        │
│                                                  │
│  Views (sql/views.sql):                          │
│  • vw_strategy_kpi  → KPIs per strategy/asset    │
│  • vw_daily_returns → daily return series        │
│  • vw_regime_stats  → performance by regime      │
└─────────────────────────┬────────────────────────┘
                          │  Power BI DirectQuery
                          ▼
┌──────────────────────────────────────────────────┐
│  Power BI Dashboard                              │
│                                                  │
│  • KPI cards: Sharpe, Max DD, CAGR               │
│  • Equity curve line chart                       │
│  • Strategy comparison bar chart                 │
│  • Asset comparison matrix                       │
│  • Regime heatmap                                │
│  • Data quality summary                          │
└──────────────────────────────────────────────────┘
```

## Module Responsibilities

| File | Responsibility | Key functions |
|---|---|---|
| `src/ingestion.py` | Load raw data | `load_symbol()`, `load_all_symbols()` |
| `src/validation.py` | Data quality | `validate_ohlcv()`, `run_quality_report()` |
| `src/transform.py` | Feature engineering | `add_returns()`, `add_features()`, `add_regime()` |
| `src/database.py` | PostgreSQL I/O | `get_connection()`, `save_klines()`, `load_klines()` |
| `src/config.py` | Central config | Paths, symbols, DB params |
| `src/utils.py` | Shared helpers | `get_logger()`, `Timer`, `ensure_dirs()` |
| `main.py` | Entry point | Orchestrates the full pipeline |

## Design Decisions

**Why Parquet?** Columnar storage reads only the columns you need. A 43K-row
OHLCV file in Parquet is ~2 MB vs ~18 MB as CSV and loads 5× faster with Pandas.

**Why pd.to_sql() instead of COPY?** `pd.to_sql()` is readable, debuggable, and
sufficient for datasets of this size (~50K rows). COPY FROM STDIN is faster for
millions of rows but harder to maintain and explain in Junior interviews.

**Why no ORM (SQLAlchemy models)?** Keeping SQL visible in `.sql` files means
the database schema is readable by anyone — you don't need to know Python to
understand the data model. This is also more common in Analytics/BI roles.
