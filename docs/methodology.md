# Methodology

## Return Model: Open-to-Open

All backtests use an **open-to-open return model** to prevent lookahead bias.

```
signal[t]   →   position = signal.shift(1)   →   bar_return = open[t+1] / open[t] - 1
```

- The signal for bar `t` is computed using only information available at or before
  the close of bar `t`.
- The position is entered at the open of bar `t+1` — not the close.
- This matches realistic execution: you see yesterday's data, place an order at
  market open, and get filled at the opening price.

**Why this matters:** Using `close[t]` as both the signal computation point and
the entry price assumes you can trade at a price you haven't seen yet. This is
lookahead bias and inflates returns in backtests.

---

## Weekday Calendar Features

```python
# Correct: use next bar's weekday to avoid off-by-one
df["next_bar_weekday"] = df["open_time_utc"].shift(-1).dt.weekday

# 0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday
```

The signal is: "on what weekday will the *next* bar open?" This tells us which
day the position will be *active*, not which day the signal was generated.

---

## Exposure-Matched Benchmark

Comparing a strategy with 14% temporal exposure against Buy & Hold 100% is
misleading during a bear market — less exposure trivially outperforms.

**Fractional Buy & Hold:** invest only `pos_pct` of capital at all times.
This gives a fair lower bound.

**Monte Carlo simulation:**
1. Take all 43,817 hourly bars for a symbol.
2. Generate 5,000 random binary masks with the same number of active hours
   as the strategy under test.
3. Compute the total return of each random mask.
4. Report the percentile of the actual strategy's return within this distribution.

A percentile of 99.3% means the strategy beats 99.3% of random strategies
with the same temporal footprint — strong evidence of a genuine signal.

---

## Regime Classification

Monthly returns of the underlying asset are classified as:

| Regime | Condition |
|---|---|
| Bull | Monthly return ≥ +5% |
| Bear | Monthly return ≤ -5% |
| Lateral | Otherwise |

Each hourly bar is tagged with the regime of its calendar month. This allows
analysis of how strategies perform under different market conditions.

---

## Data Source

- **Provider:** Binance Vision (https://data.binance.vision)
- **Format:** Monthly ZIP files containing CSV klines
- **Symbols:** BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT
- **Interval:** 1h (hourly bars)
- **Date range:** June 2021 – May 2026
- **BTCUSDT bars:** 43,817

OHLCV columns used: `open_time_utc`, `open`, `high`, `low`, `close`, `volume`
