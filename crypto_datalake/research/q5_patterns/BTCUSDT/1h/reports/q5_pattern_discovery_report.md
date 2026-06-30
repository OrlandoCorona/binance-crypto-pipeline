# S1(Q5) — Descubrimiento de Patrones — BTCUSDT 1h

## Principio rector

Este reporte identifica patrones observables. No optimiza parámetros, no genera señales de compra/venta y no declara que una estrategia funcione. La evidencia se reporta incluso cuando no existe ventaja estadística.

## Dataset

- Filas analizadas: `43,817`
- Inicio UTC: `2021-06-01 00:00:00+00:00`
- Fin UTC: `2026-05-31 23:00:00+00:00`
- Horizontes forward evaluados: `(1, 6, 24)` horas
- Ventanas de momentum observadas: `(3, 6, 24)` horas
- Umbrales rolling fijos: q20 / q80 con ventana `168` velas

## 1. Distribución base de retornos

- Media log-return por vela: `0.000015`
- Desviación estándar log-return: `0.005716`
- Asimetría: `-0.157116`
- Curtosis excedente: `12.291136`

Interpretación: esta sección es la línea base. Cualquier patrón debe compararse contra el comportamiento promedio del activo, no contra cero de forma aislada.

## 2. Comportamiento por hora UTC

- Hora con mayor volatilidad: `14:00 UTC`, volatilidad `0.008248`
- Hora con menor volatilidad: `04:00 UTC`, volatilidad `0.004097`

Tabla completa: `tables/hourly_behavior.csv`.

## 3. Comportamiento por día de semana UTC

- Día con mayor volatilidad: `Lunes`, volatilidad `0.006507`
- Día con menor volatilidad: `Sábado`, volatilidad `0.004041`

Tabla completa: `tables/day_behavior.csv`.

## 4. Resumen de evidencia estadística

- Patrones con evidencia positiva vs baseline: `3`
- Patrones con evidencia negativa vs baseline: `5`
- Patrones sin ventaja estadística clara: `139`
- Patrones con frecuencia insuficiente: `0`

Conclusión conservadora: existen patrones con diferencia positiva frente a la línea base, pero deben tratarse solo como hipótesis para validación posterior. No son estrategias confirmadas.

## 5. Evidencia destacada

| category | condition_name | forward_horizon_h | sample_count | frequency | sample_mean_fwd_log_return | baseline_mean_fwd_log_return | mean_diff_vs_baseline | bootstrap_mean_diff_ci_low | bootstrap_mean_diff_ci_high | evidence_status |
|---|---|---|---|---|---|---|---|---|---|---|
| day_behavior | day_Jueves | 1 | 6264 | 0.142961 | -0.000207 | 0.000015 | -0.000222 | -0.000415 | -0.000013 | NEGATIVE_STATISTICAL_EDGE |
| day_behavior | day_Miércoles | 6 | 6262 | 0.142932 | 0.000858 | 0.000094 | 0.000764 | 0.000266 | 0.001287 | POSITIVE_STATISTICAL_EDGE |
| day_behavior | day_Jueves | 6 | 6264 | 0.142978 | -0.000884 | 0.000094 | -0.000978 | -0.001469 | -0.000501 | NEGATIVE_STATISTICAL_EDGE |
| day_behavior | day_Martes | 24 | 6264 | 0.143037 | 0.002325 | 0.000385 | 0.001940 | 0.000944 | 0.002920 | POSITIVE_STATISTICAL_EDGE |
| day_behavior | day_Jueves | 24 | 6264 | 0.143037 | -0.002249 | 0.000385 | -0.002634 | -0.003568 | -0.001582 | NEGATIVE_STATISTICAL_EDGE |
| hour_behavior | hour_utc_21 | 1 | 1826 | 0.041674 | 0.000386 | 0.000015 | 0.000371 | 0.000012 | 0.000755 | POSITIVE_STATISTICAL_EDGE |
| volatility_volume | vol_contraction_range_lt_q20_168h | 6 | 9466 | 0.216064 | -0.000210 | 0.000094 | -0.000304 | -0.000677 | -0.000001 | NEGATIVE_STATISTICAL_EDGE |
| volatility_volume | vol_contraction_range_lt_q20_168h | 24 | 9458 | 0.215971 | -0.000551 | 0.000385 | -0.000936 | -0.001647 | -0.000241 | NEGATIVE_STATISTICAL_EDGE |

## 6. Expansión y contracción de volatilidad

Se compara el retorno forward después de velas con rango superior al percentil 80 rolling y rango inferior al percentil 20 rolling. Los umbrales usan solo información pasada.

Tabla completa: `tables/volatility_volume_patterns.csv`.

## 7. Momentum

Se observan retornos acumulados pasados de 3h, 6h y 24h contra cuantiles rolling. No se selecciona el mejor parámetro; se reportan todos.

Tabla completa: `tables/momentum_patterns.csv`.

## 8. Reversión a la media

Se evalúa qué ocurre después de rallies y caídas extremas según cuantiles rolling. Una diferencia positiva después de caída extrema puede sugerir rebote; una diferencia negativa después de rally extremo puede sugerir reversión bajista.

Tabla completa: `tables/mean_reversion_patterns.csv`.

## 9. Lectura disciplinada

Un patrón observable no es una estrategia. Para avanzar, cualquier hallazgo debe pasar por: validación fuera de muestra, walk-forward, costos más altos, comparación contra buy & hold, Monte Carlo y prueba en otros activos líquidos.

## Archivos generados

- `tables/base_return_summary.csv`
- `tables/hourly_behavior.csv`
- `tables/day_behavior.csv`
- `tables/pattern_findings_summary.csv`
- `tables/volatility_volume_patterns.csv`
- `tables/momentum_patterns.csv`
- `tables/mean_reversion_patterns.csv`
- `charts/*.png`