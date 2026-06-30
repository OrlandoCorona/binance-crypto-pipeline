# Resultados deprecados

## s2_hypothesis_strategies_BUGGY_oos_leakage/

Generado por una versión anterior de `s2_hypothesis_strategy_lab.py` (antes del
fix marcado como `[fixed_v2]` en el script actual).

**Bug confirmado:** en `strategy_summary.csv`, la columna `oos_return`
(out-of-sample) era idéntica a `full_return` para todas las hipótesis
(ej. `S2_H4_AVOID_THURSDAY`: ambas = `1.2623830522049855`). Esto significa que
el split out-of-sample no se estaba evaluando de forma independiente: el
resultado "fuera de muestra" en realidad filtraba el desempeño de la muestra
completa (incluyendo in-sample), un caso de data leakage temporal. También
`oos_benchmark_return` usaba el retorno de Buy & Hold del período completo
(`0.9491...`) en vez del benchmark correspondiente al tramo OOS real.

Esto inflaba artificialmente la calificación de varias hipótesis (ej.
`S2_H4_AVOID_THURSDAY` aparecía como `CANDIDATE_FOR_FURTHER_VALIDATION` con
señales más favorables de lo real).

**Versión correcta:** `crypto_datalake/research/s2_hypothesis_strategies_fixed/`,
producida por la versión actual del script (`oos_return` se calcula sobre el
tramo `bt.iloc[split_idx:]` real, distinto de `full_return`).

Esta carpeta se conserva solo como evidencia de auditoría interna (Q4/S3
exigen poder rastrear por qué cambió un resultado). No usar estos números para
ninguna conclusión de investigación.
