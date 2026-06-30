# Q4 — Laboratorio de Investigación Cuantitativa — BTCUSDT 1h

## Principio rector

Este laboratorio no declara que una estrategia funciona por un solo backtest. Una variante solo puede quedar como candidata si sobrevive validación out-of-sample, revisión de robustez, Monte Carlo, sensibilidad de parámetros y revisión de sobreajuste.

## Configuración

- Capital inicial: `10000.0`
- Comisión: `0.001`
- Slippage: `0.0005`
- Split in-sample: `70.00%`
- Monte Carlo runs: `500`
- Monte Carlo block size: `24` velas

## Hipótesis evaluadas

### H1 — Momentum condicionado por volumen
- Hipótesis: El momentum multi-hora podría tener más valor cuando el volumen confirma un régimen de actividad alta.
- Experimento: Comparar variantes de lookback, umbral de retorno, z-score de volumen, filtro de tendencia y exclusión de fin de semana.
- Variables: `return_Nh, volume_zscore, sma_200, is_weekend`

### H2 — Reversión corta en baja volatilidad
- Hipótesis: La reversión de caídas cortas podría ser más estable en regímenes de baja volatilidad que en alta volatilidad.
- Experimento: Evaluar caídas previas por lookback bajo distintos límites de volatilidad y filtros de fin de semana.
- Variables: `return_Nh, rolling_vol, is_weekend`

### H3 — Ruptura de rango con volumen
- Hipótesis: Las rupturas de máximos previos podrían ser menos ruidosas cuando están acompañadas por expansión de volumen.
- Experimento: Probar ventanas de breakout y volumen, con filtro de tendencia y exclusión opcional de fin de semana.
- Variables: `rolling_high_prev, volume_zscore, sma_trend, is_weekend`

### H4 — Filtro de riesgo para exposición pasiva
- Hipótesis: Un filtro de tendencia podría reducir drawdowns de exposición pasiva, aunque quizá sacrifique retorno.
- Experimento: Comparar exposición long-only con distintas medias móviles y confirmación de retorno agregado.
- Variables: `close_vs_sma, return_Nh, is_weekend`

## Leaderboard seleccionado por desempeño in-sample

La selección por in-sample se usa solo para simular el flujo real de investigación. El out-of-sample no debe usarse para optimizar parámetros.

| hypothesis_id | variant_id | in_sample_strategy_total_return | in_sample_strategy_sharpe | out_of_sample_strategy_total_return | out_of_sample_strategy_sharpe | out_of_sample_strategy_max_drawdown | full_strategy_trade_count | params_json |
|---|---|---|---|---|---|---|---|---|
| H4 | H4_0064 | 0.121642 | 0.262389 | -0.728085 | -3.295297 | -0.733296 | 769.000000 | {"avoid_weekend": true, "confirm_return_window": 72, "min_confirm_return": 0.0, "trend_sma": 200} |
| H4 | H4_0066 | 0.015066 | 0.157151 | -0.645290 | -3.280282 | -0.652222 | 670.000000 | {"avoid_weekend": true, "confirm_return_window": 72, "min_confirm_return": 0.02, "trend_sma": 200} |
| H4 | H4_0065 | -0.137645 | 0.017223 | -0.694409 | -3.474251 | -0.693940 | 805.000000 | {"avoid_weekend": false, "confirm_return_window": 72, "min_confirm_return": 0.02, "trend_sma": 200} |
| H2 | H2_0092 | -0.000880 | -0.024815 | -0.040531 | -1.901967 | -0.043888 | 24.000000 | {"avoid_weekend": true, "drop_threshold": 0.02, "lookback": 6, "max_vol_quantile": 0.33, "vol_window": 24} |
| H2 | H2_0091 | -0.001306 | -0.036087 | -0.044574 | -2.061327 | -0.047918 | 31.000000 | {"avoid_weekend": false, "drop_threshold": 0.02, "lookback": 6, "max_vol_quantile": 0.33, "vol_window": 24} |
| H2 | H2_0049 | -0.028412 | -0.489273 | -0.062548 | -1.522575 | -0.064953 | 54.000000 | {"avoid_weekend": false, "drop_threshold": 0.02, "lookback": 3, "max_vol_quantile": 0.33, "vol_window": 168} |
| H3 | H3_0118 | -0.276841 | -1.140651 | -0.182816 | -3.146193 | -0.187990 | 240.000000 | {"avoid_weekend": true, "breakout_window": 168, "trend_sma": 100, "volume_window": 24, "volume_z": 1.5} |
| H3 | H3_0116 | -0.276841 | -1.140651 | -0.182816 | -3.146193 | -0.187990 | 240.000000 | {"avoid_weekend": true, "breakout_window": 168, "trend_sma": 50, "volume_window": 24, "volume_z": 1.5} |
| H3 | H3_0120 | -0.276841 | -1.140651 | -0.182816 | -3.146193 | -0.187990 | 240.000000 | {"avoid_weekend": true, "breakout_window": 168, "trend_sma": 200, "volume_window": 24, "volume_z": 1.5} |
| H1 | H1_0072 | -0.680960 | -2.125309 | -0.472952 | -4.158677 | -0.474324 | 775.000000 | {"avoid_weekend": true, "lookback": 6, "min_return": 0.01, "use_trend_filter": true, "volume_window": 168, "volume_z": 1.0} |
| H1 | H1_0144 | -0.723515 | -2.280425 | -0.568192 | -4.631704 | -0.582795 | 896.000000 | {"avoid_weekend": true, "lookback": 12, "min_return": 0.01, "use_trend_filter": true, "volume_window": 168, "volume_z": 1.0} |
| H1 | H1_0192 | -0.868203 | -2.340843 | -0.739569 | -5.133002 | -0.748797 | 1431.000000 | {"avoid_weekend": true, "lookback": 24, "min_return": 0.01, "use_trend_filter": true, "volume_window": 168, "volume_z": 0.0} |

## Banderas de sobreajuste

| hypothesis_id | variant_id | research_status | in_sample_good | oos_bad | low_trade_count | underperforms_benchmark_oos | generalization_ratio_sharpe |
|---|---|---|---|---|---|---|---|
| H1 | H1_0072 | REJECT_OOS_WEAK | 0.000000 | 1.000000 | 0.000000 | 1.000000 | 1.956740 |
| H1 | H1_0144 | REJECT_OOS_WEAK | 0.000000 | 1.000000 | 0.000000 | 1.000000 | 2.031070 |
| H1 | H1_0192 | REJECT_OOS_WEAK | 0.000000 | 1.000000 | 0.000000 | 1.000000 | 2.192800 |
| H2 | H2_0049 | INCONCLUSIVE | 0.000000 | 1.000000 | 0.000000 | 0.000000 | 3.111915 |
| H2 | H2_0091 | INCONCLUSIVE | 0.000000 | 1.000000 | 0.000000 | 0.000000 | 57.121629 |
| H2 | H2_0092 | INCONCLUSIVE_LOW_TRADES | 0.000000 | 1.000000 | 1.000000 | 0.000000 | 76.645094 |
| H3 | H3_0116 | INCONCLUSIVE | 0.000000 | 1.000000 | 0.000000 | 0.000000 | 2.758244 |
| H3 | H3_0118 | INCONCLUSIVE | 0.000000 | 1.000000 | 0.000000 | 0.000000 | 2.758244 |
| H3 | H3_0120 | INCONCLUSIVE | 0.000000 | 1.000000 | 0.000000 | 0.000000 | 2.758244 |
| H4 | H4_0064 | REJECT_OVERFIT_RISK | 1.000000 | 1.000000 | 0.000000 | 1.000000 | -12.558847 |
| H4 | H4_0065 | REJECT_OOS_WEAK | 0.000000 | 1.000000 | 0.000000 | 1.000000 | -201.724186 |
| H4 | H4_0066 | REJECT_OVERFIT_RISK | 1.000000 | 1.000000 | 0.000000 | 1.000000 | -20.873465 |

## Monte Carlo

Monte Carlo usa block bootstrap sobre retornos de estrategia. No prueba causalidad; mide fragilidad de la curva bajo reordenamientos por bloques.

| hypothesis_id | variant_id | mc_total_return_p05 | mc_total_return_p50 | mc_total_return_p95 | mc_max_drawdown_p05 | mc_max_drawdown_p50 | mc_sharpe_p05 | mc_sharpe_p50 | mc_probability_loss | mc_probability_drawdown_worse_50pct |
|---|---|---|---|---|---|---|---|---|---|---|
| H4 | H4_0064 | -0.828824 | -0.720230 | -0.525700 | -0.840682 | -0.740014 | -4.551394 | -3.260357 | 1.000000 | 0.990000 |
| H4 | H4_0066 | -0.755571 | -0.640369 | -0.479340 | -0.763027 | -0.657621 | -4.516030 | -3.256251 | 1.000000 | 0.964000 |
| H4 | H4_0065 | -0.807006 | -0.691580 | -0.533624 | -0.813797 | -0.706483 | -4.792909 | -3.428826 | 1.000000 | 0.982000 |
| H2 | H2_0092 | -0.076534 | -0.038733 | -0.008189 | -0.076705 | -0.040723 | -2.872504 | -1.879317 | 0.992000 | 0.000000 |
| H2 | H2_0091 | -0.075790 | -0.043543 | -0.012146 | -0.077464 | -0.045445 | -3.051141 | -2.042535 | 0.996000 | 0.000000 |
| H2 | H2_0049 | -0.123192 | -0.061548 | -0.000438 | -0.129322 | -0.070787 | -2.735564 | -1.471478 | 0.950000 | 0.000000 |
| H3 | H3_0118 | -0.245670 | -0.181802 | -0.118045 | -0.247869 | -0.186926 | -4.300531 | -3.174011 | 1.000000 | 0.000000 |
| H3 | H3_0116 | -0.256258 | -0.180039 | -0.116769 | -0.260134 | -0.185036 | -4.378680 | -3.136114 | 1.000000 | 0.000000 |
| H3 | H3_0120 | -0.252487 | -0.184594 | -0.118329 | -0.255166 | -0.189245 | -4.451255 | -3.282188 | 1.000000 | 0.000000 |
| H1 | H1_0072 | -0.574374 | -0.465700 | -0.351457 | -0.578186 | -0.474512 | -5.574311 | -4.142808 | 1.000000 | 0.380000 |
| H1 | H1_0144 | -0.660674 | -0.565985 | -0.443857 | -0.663794 | -0.572317 | -6.036900 | -4.640666 | 1.000000 | 0.864000 |
| H1 | H1_0192 | -0.814069 | -0.733611 | -0.633733 | -0.817748 | -0.739557 | -6.492250 | -5.103349 | 1.000000 | 1.000000 |

## Sensibilidad de parámetros

Una hipótesis robusta no debería depender de un único valor mágico de parámetro. Revisa `tables/sensitivity_summary.csv`.

| hypothesis_id | param_name | param_value | variants | median_oos_total_return | median_oos_sharpe | median_oos_max_drawdown | positive_oos_rate |
|---|---|---|---|---|---|---|---|
| H1 | avoid_weekend | False | 144.000000 | -0.806262 | -6.301823 | -0.810763 | 0.000000 |
| H1 | avoid_weekend | True | 144.000000 | -0.753080 | -5.650270 | -0.756388 | 0.000000 |
| H1 | lookback | 12.000000 | 72.000000 | -0.770088 | -5.749351 | -0.776649 | 0.000000 |
| H1 | lookback | 24.000000 | 72.000000 | -0.778865 | -5.822712 | -0.787293 | 0.000000 |
| H1 | lookback | 48.000000 | 72.000000 | -0.814663 | -6.346294 | -0.819942 | 0.000000 |
| H1 | lookback | 6.000000 | 72.000000 | -0.732899 | -5.677739 | -0.734388 | 0.000000 |
| H1 | min_return | 0.000000 | 96.000000 | -0.837827 | -6.567810 | -0.841729 | 0.000000 |
| H1 | min_return | 0.005000 | 96.000000 | -0.781187 | -5.962795 | -0.789525 | 0.000000 |
| H1 | min_return | 0.010000 | 96.000000 | -0.713082 | -5.400266 | -0.714894 | 0.000000 |
| H1 | use_trend_filter | False | 144.000000 | -0.805253 | -6.112065 | -0.810716 | 0.000000 |
| H1 | use_trend_filter | True | 144.000000 | -0.742782 | -5.795325 | -0.748252 | 0.000000 |
| H1 | volume_window | 168.000000 | 144.000000 | -0.772290 | -5.622384 | -0.778910 | 0.000000 |
| H1 | volume_window | 24.000000 | 144.000000 | -0.779771 | -6.272603 | -0.784362 | 0.000000 |
| H1 | volume_z | 0.000000 | 96.000000 | -0.840726 | -6.288369 | -0.845729 | 0.000000 |
| H1 | volume_z | 0.500000 | 96.000000 | -0.788841 | -6.113052 | -0.795511 | 0.000000 |
| H1 | volume_z | 1.000000 | 96.000000 | -0.668460 | -5.296798 | -0.676434 | 0.000000 |
| H2 | avoid_weekend | False | 108.000000 | -0.588669 | -4.088565 | -0.594425 | 0.000000 |
| H2 | avoid_weekend | True | 108.000000 | -0.497596 | -3.543600 | -0.502610 | 0.000000 |
| H2 | drop_threshold | 0.005000 | 72.000000 | -0.754858 | -6.207466 | -0.757554 | 0.000000 |
| H2 | drop_threshold | 0.010000 | 72.000000 | -0.552796 | -4.272152 | -0.567058 | 0.000000 |
| H2 | drop_threshold | 0.020000 | 72.000000 | -0.218517 | -2.291665 | -0.223805 | 0.000000 |
| H2 | lookback | 12.000000 | 54.000000 | -0.559740 | -4.055146 | -0.569523 | 0.000000 |
| H2 | lookback | 24.000000 | 54.000000 | -0.490740 | -3.047448 | -0.496952 | 0.000000 |
| H2 | lookback | 3.000000 | 54.000000 | -0.514261 | -5.253474 | -0.523197 | 0.000000 |
| H2 | lookback | 6.000000 | 54.000000 | -0.587647 | -5.164859 | -0.592303 | 0.000000 |

## Criterio de conclusión

Una variante queda rechazada si gana in-sample pero falla out-of-sample, si tiene pocas operaciones, si depende de un parámetro aislado, si Monte Carlo muestra alta probabilidad de pérdida o si no supera un benchmark razonable. Una variante con buen desempeño solo queda como candidata para más pruebas, nunca como estrategia confirmada.

## Siguiente validación obligatoria

1. Revisar estabilidad por año.
2. Revisar sensibilidad por parámetro.
3. Probar costos más altos.
4. Ejecutar walk-forward real.
5. Repetir en otros activos líquidos.
6. Congelar reglas antes de mirar nuevos datos.