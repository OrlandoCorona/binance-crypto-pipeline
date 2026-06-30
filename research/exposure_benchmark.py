#!/usr/bin/env python3
"""
Benchmark de exposición equivalente.
Compara H2/H4 contra:
  1. BH fraccionado (pos_pct de capital siempre invertido)
  2. Las 7 estrategias de día único / evitar-un-día (exposición idéntica)
  3. Monte Carlo de días aleatorios con la misma exposición
"""

import numpy as np
import pandas as pd
from pathlib import Path

MULTI_DIR = Path("/sessions/vibrant-affectionate-meitner/mnt/binance_crypto_datalake_pipeline_project/crypto_datalake/research/paper_trading/multi_asset")
SYMBOLS  = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
N_MC     = 5000
RNG      = np.random.default_rng(42)
DAY_NAMES = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]

def load_asset(sym):
    df = pd.read_csv(MULTI_DIR / f"{sym}_1h_12m.csv", parse_dates=["open_time_utc"])
    df = df.sort_values("open_time_utc").reset_index(drop=True)
    df["mkt_ret"] = df["open"].shift(-1) / df["open"] - 1
    df["weekday"] = df["open_time_utc"].dt.weekday
    df = df.iloc[:-1].copy()
    return df

def total_ret(pos, mkt):
    return float(np.prod(1.0 + pos * mkt) - 1.0)

def bh_ret(mkt):
    return float(np.prod(1.0 + mkt) - 1.0)

def frac_bh(pct, mkt):
    return float(np.prod(1.0 + pct * mkt) - 1.0)

def pct(x): return f"{x:+.1%}"

SEP = "=" * 78
rows_h2, rows_h4 = [], []

for sym in SYMBOLS:
    df  = load_asset(sym)
    mkt = df["mkt_ret"].values
    wd  = df["weekday"].values
    n   = len(df)
    bh  = bh_ret(mkt)

    # H2: Wednesday Long
    h2_pos = (wd == 2).astype(float)
    h2_ret = total_ret(h2_pos, mkt)
    h2_pct = h2_pos.mean()
    fbh_h2 = frac_bh(h2_pct, mkt)

    day_rets_h2 = {d: total_ret((wd == d).astype(float), mkt) for d in range(7)}
    h2_rank = sorted(day_rets_h2.values(), reverse=True).index(h2_ret) + 1

    h2_n_in = int(h2_pos.sum())
    mc_h2 = np.empty(N_MC)
    for i in range(N_MC):
        p = np.zeros(n); p[RNG.choice(n, h2_n_in, replace=False)] = 1.0
        mc_h2[i] = total_ret(p, mkt)
    mc_pct_h2 = float(np.mean(mc_h2 < h2_ret))

    rows_h2.append(dict(
        symbol=sym, H2_ret=h2_ret, BH100=bh, BHfrac=fbh_h2,
        exc_vs_BH100=h2_ret-bh, exc_vs_BHfrac=h2_ret-fbh_h2,
        pos_pct=h2_pct, day_rank=h2_rank,
        mc_pct=mc_pct_h2, mc_median=float(np.median(mc_h2)),
        mc_p95=float(np.percentile(mc_h2,95)),
        best_day=DAY_NAMES[max(day_rets_h2, key=day_rets_h2.get)],
        **{f"long_{DAY_NAMES[d]}": day_rets_h2[d] for d in range(7)},
    ))

    # H4: Avoid Thursday
    h4_pos = (wd != 3).astype(float)
    h4_ret = total_ret(h4_pos, mkt)
    h4_pct = h4_pos.mean()
    fbh_h4 = frac_bh(h4_pct, mkt)

    avoid_rets = {d: total_ret((wd != d).astype(float), mkt) for d in range(7)}
    h4_rank = sorted(avoid_rets.values(), reverse=True).index(h4_ret) + 1

    h4_n_in = int(h4_pos.sum())
    mc_h4 = np.empty(N_MC)
    for i in range(N_MC):
        p = np.zeros(n); p[RNG.choice(n, h4_n_in, replace=False)] = 1.0
        mc_h4[i] = total_ret(p, mkt)
    mc_pct_h4 = float(np.mean(mc_h4 < h4_ret))

    rows_h4.append(dict(
        symbol=sym, H4_ret=h4_ret, BH100=bh, BHfrac=fbh_h4,
        exc_vs_BH100=h4_ret-bh, exc_vs_BHfrac=h4_ret-fbh_h4,
        pos_pct=h4_pct, day_rank=h4_rank,
        mc_pct=mc_pct_h4, mc_median=float(np.median(mc_h4)),
        mc_p95=float(np.percentile(mc_h4,95)),
        best_avoid_day=DAY_NAMES[max(avoid_rets, key=avoid_rets.get)],
        **{f"avoid_{DAY_NAMES[d]}": avoid_rets[d] for d in range(7)},
    ))

df_h2 = pd.DataFrame(rows_h2)
df_h4 = pd.DataFrame(rows_h4)

# ── PRINT ──────────────────────────────────────────────────────────────────────
print(SEP)
print("  BENCHMARK DE EXPOSICIÓN EQUIVALENTE (Jun-2025 → Jun-2026, 12 meses)")
print(f"  H2 = Wednesday Long (~14%)   |   H4 = Avoid Thursday (~86%)")
print(SEP)

# ── H2 tabla principal ─────────────────────────────────────────────────────────
print("\n── H2 WEDNESDAY LONG ─────────────────────────────────────────────────────")
print(f"  {'Activo':<10} {'H2':>8}  {'BH-100%':>8}  {'BH-frac':>9}  {'vs BH100':>9}  {'vs BHfrac':>10}  {'Rk/7':>5}  {'MC%ile':>7}")
for r in rows_h2:
    print(f"  {r['symbol']:<10} {pct(r['H2_ret']):>8}  {pct(r['BH100']):>8}  {pct(r['BHfrac']):>9}  "
          f"{pct(r['exc_vs_BH100']):>9}  {pct(r['exc_vs_BHfrac']):>10}  "
          f"{'#'+str(r['day_rank']):>5}  {r['mc_pct']:>6.1%}")

print(f"\n  Retornos por día (Long ese día todo el año):")
print(f"  {'Activo':<10}", "  ".join(f"{d:>7}" for d in DAY_NAMES))
for r in rows_h2:
    vals = [r[f"long_{d}"] for d in DAY_NAMES]
    line = "  ".join(f"{pct(v):>7}" for v in vals)
    print(f"  {r['symbol']:<10} {line}   ← mejor: {r['best_day']}")

print(f"\n  Monte Carlo (N={N_MC}, misma # barras, timing aleatorio):")
print(f"  {'Activo':<10} {'H2':>8}  {'MC med':>8}  {'MC p95':>8}  {'H2 supera X% de randoms':>25}")
for r in rows_h2:
    print(f"  {r['symbol']:<10} {pct(r['H2_ret']):>8}  {pct(r['mc_median']):>8}  {pct(r['mc_p95']):>8}  {r['mc_pct']:>24.1%}")

# ── H4 tabla principal ─────────────────────────────────────────────────────────
print("\n── H4 AVOID THURSDAY ─────────────────────────────────────────────────────")
print(f"  {'Activo':<10} {'H4':>8}  {'BH-100%':>8}  {'BH-frac':>9}  {'vs BH100':>9}  {'vs BHfrac':>10}  {'Rk/7':>5}  {'MC%ile':>7}")
for r in rows_h4:
    print(f"  {r['symbol']:<10} {pct(r['H4_ret']):>8}  {pct(r['BH100']):>8}  {pct(r['BHfrac']):>9}  "
          f"{pct(r['exc_vs_BH100']):>9}  {pct(r['exc_vs_BHfrac']):>10}  "
          f"{'#'+str(r['day_rank']):>5}  {r['mc_pct']:>6.1%}")

print(f"\n  Retornos por estrategia 'evitar-día' (out un día, in los otros 6):")
print(f"  {'Activo':<10}", "  ".join(f"{'ev'+d:>8}" for d in DAY_NAMES))
for r in rows_h4:
    vals = [r[f"avoid_{d}"] for d in DAY_NAMES]
    line = "  ".join(f"{pct(v):>8}" for v in vals)
    print(f"  {r['symbol']:<10} {line}   ← mejor: ev {r['best_avoid_day']}")

print(f"\n  Monte Carlo (N={N_MC}):")
print(f"  {'Activo':<10} {'H4':>8}  {'MC med':>8}  {'MC p95':>8}  {'H4 supera X% de randoms':>25}")
for r in rows_h4:
    print(f"  {r['symbol']:<10} {pct(r['H4_ret']):>8}  {pct(r['mc_median']):>8}  {pct(r['mc_p95']):>8}  {r['mc_pct']:>24.1%}")

# ── VEREDICTO ─────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  VEREDICTO AGREGADO")
print(SEP)
for label, rows, ret_key in [("H2 WEDNESDAY LONG", rows_h2, "H2_ret"), ("H4 AVOID THURSDAY", rows_h4, "H4_ret")]:
    n_beat_frac = sum(r["exc_vs_BHfrac"] > 0 for r in rows)
    mean_exc    = np.mean([r["exc_vs_BHfrac"] for r in rows])
    n_beat_mc   = sum(r["mc_pct"] > 0.5 for r in rows)
    med_mc_pct  = np.median([r["mc_pct"] for r in rows])
    n_best_day  = sum(r["day_rank"] == 1 for r in rows)
    print(f"\n  {label}")
    print(f"    vs BH fraccionado : {n_beat_frac}/4 activos positivos | exceso medio: {mean_exc:+.1%}")
    print(f"    vs MC aleatorio   : {n_beat_mc}/4 activos > p50 random | MC percentil mediano: {med_mc_pct:.1%}")
    print(f"    Mejor día de los 7: {n_best_day}/4 activos donde el día elegido es rank #1")
    if n_beat_frac >= 3 and med_mc_pct > 0.70:
        verdict = "SEÑAL GENUINA — el timing de calendario agrega valor MÁS ALLÁ de la reducción de exposición"
    elif n_beat_frac >= 2 and med_mc_pct > 0.50:
        verdict = "SEÑAL PARCIAL — evidencia débil de timing genuino"
    else:
        verdict = "ARTEFACTO DE EXPOSICIÓN — el exceso vs BH-100% se explica por menor tiempo en mercado (bear market)"
    print(f"    ──► {verdict}")

print(f"""
{SEP}
  NOTA METODOLÓGICA CLAVE
{SEP}
  Comparar contra BH-100% en un bear market (BTC -40%, ETH -31%, SOL -51%) es
  incorrecto para estrategias de baja exposición. El benchmark correcto es:

    BH fraccionado : siempre invertir el mismo % que la estrategia (~14% / ~86%)
    Monte Carlo    : invertir ese mismo % de barras, pero con timing ALEATORIO

  Si la estrategia no supera estos benchmarks, el exceso vs BH-100% es puramente
  un artefacto de estar "menos en el mercado" durante una caída.
{SEP}
""")

# ── CSV ──────────────────────────────────────────────────────────────────────
out = Path("/sessions/vibrant-affectionate-meitner/mnt/binance_crypto_datalake_pipeline_project/crypto_datalake/research/paper_trading/multi_asset")
pd.concat([df_h2.assign(strategy="H2"), df_h4.assign(strategy="H4")]).to_csv(
    out / "exposure_matched_benchmark.csv", index=False, float_format="%.6f")
print("  CSV guardado: exposure_matched_benchmark.csv")
