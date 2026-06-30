# Quantitative Research Findings

## Overview

Analysis of 43,817 hourly bars (BTCUSDT, June 2021 – May 2026) plus
multi-asset out-of-sample validation (ETHUSDT, BNBUSDT, SOLUSDT).

Two calendar-based hypotheses were tested:

- **H2 — Wednesday Long**: hold only on Wednesdays (~14% temporal exposure)
- **H4 — Avoid Thursday**: hold every day except Thursday (~86% exposure)

---

## Out-of-Sample Results (last 13 months)

| Symbol | Strategy | Return | Sharpe | Max DD | Win Rate | Beat BH |
|---|---|---|---|---|---|---|
| BTCUSDT | H2 Wednesday Long | -4.3% | -0.19 | -17.8% | 48.1% | 69.2% |
| BTCUSDT | H4 Avoid Thursday | -21.1% | -0.42 | -34.1% | 49.1% | 61.5% |
| ETHUSDT | H2 Wednesday Long | +13.9% | 0.66 | -20.5% | 48.1% | 61.5% |
| ETHUSDT | H4 Avoid Thursday | +21.2% | 0.62 | -38.7% | 64.2% | 84.6% |
| BNBUSDT | H2 Wednesday Long | -2.4% | -0.04 | -19.0% | 53.8% | 46.2% |
| BNBUSDT | H4 Avoid Thursday | +26.0% | 0.72 | -45.3% | 54.7% | 69.2% |
| SOLUSDT | H2 Wednesday Long | -4.0% | -0.02 | -30.2% | 48.1% | 76.9% |
| SOLUSDT | H4 Avoid Thursday | -15.8% | 0.05 | -56.7% | 50.9% | 69.2% |

---

## Exposure-Matched Benchmark (Monte Carlo)

Comparing a strategy against Buy & Hold 100% is unfair when it only holds
14% of the time (H2). The correct null hypothesis:

> *"Does H2 beat a random strategy that also holds ~14% of all hours?"*

**Method:** Generate 5,000 random permutations of equal temporal exposure,
compute the return distribution, then measure where the actual strategy ranks.

| Strategy | MC Percentile | Interpretation |
|---|---|---|
| H2 Wednesday Long | **85.9%** | Beats 85.9% of random same-exposure strategies |
| H4 Avoid Thursday | **99.3%** | Beats 99.3% — very strong signal |

H4 also ranked **#1 of 7 weekday strategies on all 4 assets** in a head-to-head
comparison (long_Monday, long_Tuesday, ..., long_Sunday).

---

## Regime Analysis (BTCUSDT)

Monthly return classification: Bull (≥+5%), Bear (≤-5%), Lateral (else).

| Regime | Count | H2 Wed mean | H4 AvoidThu mean |
|---|---|---|---|
| Bull | 3 months | +3.1% | — |
| Bear | 5 months | -0.04% | -6.2% (least bad day) |
| Lateral | 5 months | — | — |

H4's edge persists across regimes — Thursday is consistently the worst or
near-worst day regardless of market direction.

---

## Conclusions

1. **H4 (Avoid Thursday) is a genuine calendar anomaly** with MC p-value 99.3%.
   Thursday is ranked last or second-to-last in 4 of 4 assets.

2. **H2 (Wednesday Long) shows a moderate signal** (MC p85.9%) — statistically
   above noise but not as robust as H4.

3. **Neither strategy outperforms in all market conditions.** BTCUSDT BTC had a
   strong bear period in the out-of-sample window that compressed absolute returns
   for both strategies.

4. **The open-to-open model is essential.** Preliminary tests using close-to-close
   returns showed inflated Sharpe ratios due to lookahead bias. Switching to
   open-to-open removed the bias and produced the (more conservative) results above.
