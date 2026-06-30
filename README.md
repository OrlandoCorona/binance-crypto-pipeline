# Binance Crypto Data Lake Pipeline

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-2.0+-150458?logo=pandas&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-Dashboard-F2C811?logo=powerbi&logoColor=black)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

End-to-end data pipeline that ingests historical cryptocurrency OHLCV data from Binance, validates and stores it in PostgreSQL, and surfaces quantitative trading strategy results in a Power BI executive dashboard.

---

## Key Findings

Backtesting two calendar-based strategies across 4 assets (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT) — last 13 months of out-of-sample data:

| Strategy | Asset | Return | Sharpe | Max DD | Beat BH |
|---|---|---|---|---|---|
| **H4 — Avoid Thursday** | BNBUSDT | +26.0% | 0.72 | -45.3% | 69.2% |
| **H4 — Avoid Thursday** | ETHUSDT | +21.2% | 0.62 | -38.7% | 84.6% |
| **H2 — Wednesday Long** | ETHUSDT | +13.9% | 0.66 | -20.5% | 61.5% |
| **H4 — Avoid Thursday** | SOLUSDT | -15.8% | 0.05 | -56.7% | 69.2% |

**Statistical validation (Monte Carlo, N=5,000 permutations):**
- H2 Wednesday Long → percentile **85.9%** above random same-exposure permutations
- H4 Avoid Thursday → percentile **99.3%** — rank #1 of 7 weekdays on all 4 assets

---

## Architecture

```
Binance API
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  INGESTION  (src/ingestion.py)                      │
│  Reads monthly OHLCV ZIPs / Parquet files           │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  VALIDATION  (src/validation.py)                    │
│  NULL checks, OHLC integrity, gap & outlier flags   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  TRANSFORM  (src/transform.py)                      │
│  Returns (open-to-open), SMA, volatility, regime    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  LOAD  (src/database.py)                            │
│  Writes to PostgreSQL via psycopg2 + pd.to_sql()   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
             ┌──────────────────┐
             │   PostgreSQL 15  │
             └────────┬─────────┘
                      │
                      ▼
             ┌──────────────────┐
             │  Power BI        │
             │  Dashboard       │
             └──────────────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Data source | Binance Vision API | Historical OHLCV klines |
| Raw storage | Apache Parquet | Columnar format, 10× smaller than CSV |
| Processing | Python 3.10, Pandas, NumPy | ETL and feature engineering |
| Database | PostgreSQL 15 | Structured storage and KPI queries |
| Visualization | Power BI | Executive dashboard |
| Research | Jupyter Notebooks | EDA and strategy exploration |
| Version control | Git | Full project history |

---

## Project Structure

```
binance-crypto-datalake/
│
├── data/               # Pipeline outputs (gitignored)
│   ├── raw/
│   ├── processed/
│   ├── exports/        # CSVs ready for Power BI
│   └── reports/
│
├── notebooks/
│   ├── quant_eda.ipynb
│   └── experiments.ipynb
│
├── src/                # Production pipeline
│   ├── ingestion.py
│   ├── validation.py
│   ├── transform.py
│   ├── database.py
│   ├── config.py
│   └── utils.py
│
├── research/           # Quantitative research scripts
│   ├── quant_eda.py
│   ├── q3_backtesting_framework.py
│   ├── q4_quant_research_lab.py
│   ├── q5_pattern_discovery.py
│   ├── q6_backtest_engine.py       ← open-to-open engine
│   ├── s2_hypothesis_strategy_lab.py
│   ├── s3_overfit_audit.py
│   ├── s4_institutional_validation.py
│   └── exposure_benchmark.py       ← Monte Carlo benchmark
│
├── sql/
│   ├── create_tables.sql
│   ├── views.sql
│   └── queries.sql
│
├── dashboard/
│   ├── powerbi/
│   └── screenshots/
│
├── docs/
│   ├── methodology.md
│   ├── architecture.md
│   ├── findings.md
│   └── roadmap.md
│
├── tests/
│   └── test_pipeline.py
│
├── main.py
├── .env.example
├── requirements.txt
└── .gitignore
```

---

## Setup

```bash
# 1. Clone and install
git clone https://github.com/YOUR_USERNAME/binance-crypto-datalake.git
cd binance-crypto-datalake
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your PostgreSQL credentials

# 3. Create database and tables
psql -U postgres -c "CREATE DATABASE crypto_pipeline;"
psql -U postgres -d crypto_pipeline -f sql/create_tables.sql
psql -U postgres -d crypto_pipeline -f sql/views.sql

# 4. Run pipeline
python main.py

# 5. Run tests
pytest tests/ -v
```

---

## Research Methodology

All strategies use an **open-to-open return model** to eliminate lookahead bias:

```
signal[t]  →  position = signal.shift(1)  →  return = open[t+1] / open[t] - 1
```

A signal generated on bar `t` executes at the open of bar `t+1`.
Strategies were validated against an **exposure-matched Monte Carlo benchmark** (N=5,000 permutations) to confirm results exceed random timing of equal duration.

Full methodology: [docs/methodology.md](docs/methodology.md) | Full findings: [docs/findings.md](docs/findings.md)

---

## License

MIT — see [LICENSE](LICENSE)
