# Roadmap

## ✅ Completed

### Data Infrastructure
- [x] Binance Vision data ingestion (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT — 1h, 2021–2026)
- [x] Raw data stored as Parquet (columnar, compressed)
- [x] PostgreSQL schema with klines, backtest_results, data_quality tables
- [x] ETL pipeline: ingestion → validation → transform → load

### Data Quality
- [x] 7 OHLCV validation checks (NULL, OHLC integrity, negatives, duplicates, gaps, outliers)
- [x] Validation results logged to `data_quality` table

### Quantitative Research
- [x] Open-to-open return model (no lookahead bias)
- [x] EDA on BTCUSDT 1h (seasonality, volatility, autocorrelation)
- [x] Hypothesis H2: Wednesday Long
- [x] Hypothesis H4: Avoid Thursday
- [x] Walk-forward validation (S3 overfit audit)
- [x] Institutional-grade validation (S4 — Monte Carlo, regime analysis)
- [x] Exposure-matched benchmark (5,000 Monte Carlo permutations)
- [x] Multi-asset out-of-sample validation (4 assets, 13 months)

### Visualization
- [x] Power BI dashboard connected to PostgreSQL

---

## 🔄 In Progress

- [ ] `src/ingestion.py` — production-grade ingestion module
- [ ] `src/validation.py` — modular validation pipeline
- [ ] `src/transform.py` — feature engineering module
- [ ] `src/database.py` — PostgreSQL read/write module
- [ ] `main.py` — pipeline orchestration entry point
- [ ] `tests/test_pipeline.py` — unit tests

---

## 🗓️ Planned

### Short term
- [ ] Add 15m and 4h intervals for multi-timeframe analysis
- [ ] Automate monthly data refresh (scheduled script)
- [ ] Export final strategy signals to CSV for Power BI integration
- [ ] Dashboard screenshots in `dashboard/screenshots/`

### Medium term
- [ ] Extend to 10+ symbols (top-20 crypto by market cap)
- [ ] Add additional hypotheses: H5 (end-of-month effect), H6 (halving cycles)
- [ ] Implement simple position sizing (Kelly criterion, fixed fractional)
- [ ] Jupyter notebook with interactive EDA charts

### Long term
- [ ] REST API endpoint to serve strategy signals
- [ ] Automated report generation (PDF via Python)
- [ ] Live paper trading simulation with real-time Binance WebSocket data
