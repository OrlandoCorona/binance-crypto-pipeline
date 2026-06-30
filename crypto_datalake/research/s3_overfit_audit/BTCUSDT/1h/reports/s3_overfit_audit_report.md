# S3 — Detector de Sobreajuste — BTCUSDT 1h

## Principio rector

Este reporte intenta destruir las estrategias candidatas de S2. No optimiza, no mejora reglas y no cambia parámetros. Las reglas evaluadas permanecen congeladas durante toda la auditoría.

## Dataset

- Filas analizadas: `43,817`
- Inicio UTC: `2021-06-01 00:00:00+00:00`
- Fin UTC: `2026-05-31 23:00:00+00:00`
- Capital inicial por muestra: `10000.0`
- Coste base: comisión `0.001`, slippage `0.0005`
- Walk-forward: train `8760` barras, purge `24`, test `2160`, step `2160`
- Monte Carlo: `500` runs, block size `24` barras

## Estrategias auditadas

### S2_H2_WEDNESDAY_LONG
- Hipótesis: El sesgo observado de miércoles UTC podría ser explotable sin indicadores adicionales.
- Regla congelada: Mantener spot long solo durante velas cuyo open_time pertenece a miércoles UTC; fuera el resto.
- Riesgo que la auditoría intenta explotar: Puede ser selección retrospectiva de un día favorable; sensible a régimen, zona horaria y costes.

### S2_H4_AVOID_THURSDAY
- Hipótesis: Evitar jueves UTC podría filtrar un contexto históricamente negativo del benchmark long-only.
- Regla congelada: Mantener exposición pasiva long excepto durante velas cuyo open_time pertenece a jueves UTC.
- Riesgo que la auditoría intenta explotar: Puede ser un filtro elegido después de mirar el dataset; puede no sostenerse por año, OOS o activos.

## Auditoría de lookahead y data leakage

Las reglas auditadas usan calendario UTC y no consumen retornos futuros ni columnas enriquecidas de Q2. No obstante, el mayor riesgo no es leakage técnico sino selección retrospectiva: los días fueron descubiertos mirando el mismo histórico.

| strategy_id          | uses_price_features   | uses_future_returns   | uses_enriched_q2_features   | uses_calendar_only   | lookahead_detected_by_rule_review   | data_leakage_detected_by_rule_review   | remaining_risk                                                                                                         |
|:---------------------|:----------------------|:----------------------|:----------------------------|:---------------------|:------------------------------------|:---------------------------------------|:-----------------------------------------------------------------------------------------------------------------------|
| S2_H2_WEDNESDAY_LONG | False                 | False                 | False                       | True                 | False                               | False                                  | La regla fue descubierta mirando el mismo histórico; existe sesgo de selección aunque la señal no use precios futuros. |
| S2_H4_AVOID_THURSDAY | False                 | False                 | False                       | True                 | False                               | False                                  | La regla fue descubierta mirando el mismo histórico; existe sesgo de selección aunque la señal no use precios futuros. |


## Resultado final de auditoría

| strategy_id          | audit_status         |   wf_positive_window_rate |   wf_beat_benchmark_window_rate |   wf_median_test_return |   wf_median_excess_vs_benchmark |   year_positive_rate |   year_beat_benchmark_rate |   mc_probability_loss | audit_reasons                                                                                                                                                                                                                                                              |
|:---------------------|:---------------------|--------------------------:|--------------------------------:|------------------------:|--------------------------------:|---------------------:|---------------------------:|----------------------:|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| S2_H2_WEDNESDAY_LONG | INCONCLUSIVE_FRAGILE |                    0.6875 |                           0.375 |               0.0590275 |                     -0.0851267  |             0.666667 |                   0.666667 |                 0.322 | menos de 60% de ventanas walk-forward baten benchmark; mediana de exceso vs benchmark en walk-forward no positiva; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas                            |
| S2_H4_AVOID_THURSDAY | FAILS_OVERFIT_AUDIT  |                    0.5625 |                           0.5   |               0.119696  |                      0.00292365 |             0.666667 |                   0.5      |                 0.222 | menos de 60% de ventanas walk-forward positivas; menos de 60% de ventanas walk-forward baten benchmark; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas; menos de 60% de años baten benchmark |


## Walk-forward base con reglas congeladas

Cada ventana usa un tramo de entrenamiento solo como contexto histórico. No se ajustan parámetros dentro de la ventana. El test posterior se evalúa con la regla fija.

| strategy_id          |   wf_window_count |   wf_positive_window_rate |   wf_beat_benchmark_window_rate |   wf_median_test_return |   wf_mean_test_return |   wf_worst_test_return |   wf_mean_excess_vs_benchmark |   wf_median_excess_vs_benchmark |   wf_worst_drawdown |   wf_mean_test_sharpe |
|:---------------------|------------------:|--------------------------:|--------------------------------:|------------------------:|----------------------:|-----------------------:|------------------------------:|--------------------------------:|--------------------:|----------------------:|
| S2_H2_WEDNESDAY_LONG |                16 |                    0.6875 |                           0.375 |               0.0590275 |             0.016837  |              -0.170095 |                   -0.0807101  |                     -0.0851267  |           -0.227081 |              0.319452 |
| S2_H4_AVOID_THURSDAY |                16 |                    0.5625 |                           0.5   |               0.119696  |             0.0931093 |              -0.379571 |                   -0.00443779 |                      0.00292365 |           -0.423322 |              0.987925 |


## Cost stress

Una hipótesis frágil suele desaparecer al subir costes. Esta tabla resume el walk-forward por escenario.

| scenario   | strategy_id          |   commission |   slippage |   full_total_return |   wf_positive_window_rate |   wf_beat_benchmark_window_rate |   wf_median_test_return |   wf_median_excess_vs_benchmark |   wf_worst_drawdown |
|:-----------|:---------------------|-------------:|-----------:|--------------------:|--------------------------:|--------------------------------:|------------------------:|--------------------------------:|--------------------:|
| base       | S2_H2_WEDNESDAY_LONG |       0.001  |     0.0005 |            0.259894 |                    0.6875 |                          0.375  |               0.0590275 |                     -0.0851267  |           -0.227081 |
| base       | S2_H4_AVOID_THURSDAY |       0.001  |     0.0005 |            1.26987  |                    0.5625 |                          0.5    |               0.119696  |                      0.00292365 |           -0.423322 |
| medium     | S2_H2_WEDNESDAY_LONG |       0.0015 |     0.001  |           -0.253245 |                    0.625  |                          0.3125 |               0.031787  |                     -0.110047   |           -0.239391 |
| medium     | S2_H4_AVOID_THURSDAY |       0.0015 |     0.001  |            0.343836 |                    0.5625 |                          0.125  |               0.0898187 |                     -0.0316555  |           -0.437032 |
| high       | S2_H2_WEDNESDAY_LONG |       0.002  |     0.0015 |           -0.557621 |                    0.5625 |                          0.1875 |               0.0052208 |                     -0.134351   |           -0.251517 |
| high       | S2_H4_AVOID_THURSDAY |       0.002  |     0.0015 |           -0.204825 |                    0.5625 |                          0.125  |               0.0607097 |                     -0.0653453  |           -0.450429 |


## Robustez anual

| strategy_id          |   years |   positive_year_rate |   beat_benchmark_year_rate |   median_year_return |   worst_year_return |
|:---------------------|--------:|---------------------:|---------------------------:|---------------------:|--------------------:|
| S2_H2_WEDNESDAY_LONG |       6 |             0.666667 |                   0.666667 |            0.0797141 |           -0.272454 |
| S2_H4_AVOID_THURSDAY |       6 |             0.666667 |                   0.5      |            0.146017  |           -0.655588 |


## Monte Carlo por bloques

Se remuestrean bloques de retornos de la propia estrategia para estimar fragilidad de secuencia. Una probabilidad alta de pérdida destruye la hipótesis.

| strategy_id          |   observed_total_return |   observed_max_drawdown |   mc_probability_loss |   mc_probability_worse_than_observed_return |   mc_return_p05 |   mc_return_p50 |   mc_return_p95 |   mc_max_drawdown_p05 |   mc_max_drawdown_p50 |   mc_max_drawdown_p95 |   mc_runs |   mc_block_size |
|:---------------------|------------------------:|------------------------:|----------------------:|--------------------------------------------:|----------------:|----------------:|----------------:|----------------------:|----------------------:|----------------------:|----------:|----------------:|
| S2_H2_WEDNESDAY_LONG |                0.259894 |               -0.460102 |                 0.322 |                                       0.484 |       -0.424315 |        0.289334 |         1.91744 |             -0.603513 |             -0.382572 |             -0.236022 |       500 |              24 |
| S2_H4_AVOID_THURSDAY |                1.26987  |               -0.76585  |                 0.222 |                                       0.498 |       -0.591295 |        1.28008  |        14.0186  |             -0.851209 |             -0.647023 |             -0.439173 |       500 |              24 |


## Fragilidad de calendario UTC

Se desplaza artificialmente el calendario UTC. No es optimización: es prueba destructiva. Si la hipótesis solo funciona exactamente con UTC sin desplazamiento, puede ser frágil.

| strategy_id          |   return_shift_0 |   median_return_other_shifts |   positive_other_shift_rate |   best_shift_return |   worst_shift_return |
|:---------------------|-----------------:|-----------------------------:|----------------------------:|--------------------:|---------------------:|
| S2_H2_WEDNESDAY_LONG |         0.259894 |                     0.201314 |                       0.625 |            0.588755 |            -0.410774 |
| S2_H4_AVOID_THURSDAY |         1.26987  |                     1.17164  |                       0.875 |            2.261    |            -0.308519 |


## Sesgo de selección: controles negativos

Se comparan las reglas originales contra reglas del mismo tipo en otros días. Si muchas alternativas similares funcionan igual o mejor, el hallazgo puede ser selección retrospectiva.

| control_family   | tested_condition   | is_original_rule   |   strategy_total_return |   strategy_sharpe |   strategy_max_drawdown |   excess_total_return_vs_benchmark |   strategy_trade_count |
|:-----------------|:-------------------|:-------------------|------------------------:|------------------:|------------------------:|-----------------------------------:|-----------------------:|
| weekday_long     | long_Lunes         | False              |              -0.324415  |        -0.224824  |               -0.566142 |                          -1.3093   |                    260 |
| weekday_long     | long_Martes        | False              |              -0.608411  |        -0.739294  |               -0.634939 |                          -1.5933   |                    261 |
| weekday_long     | long_Miércoles     | True               |               0.259894  |         0.318025  |               -0.460102 |                          -0.724993 |                    261 |
| weekday_long     | long_Jueves        | False              |              -0.820435  |        -1.53707   |               -0.830826 |                          -1.80532  |                    261 |
| weekday_long     | long_Viernes       | False              |              -0.564021  |        -0.671334  |               -0.660838 |                          -1.54891  |                    261 |
| weekday_long     | long_Sábado        | False              |              -0.574096  |        -1.11992   |               -0.596325 |                          -1.55898  |                    261 |
| weekday_long     | long_Domingo       | False              |              -0.266434  |        -0.306327  |               -0.457863 |                          -1.25132  |                    261 |
| avoid_weekday    | avoid_Lunes        | False              |              -0.39332   |         0.0349672 |               -0.797392 |                          -1.37821  |                    261 |
| avoid_weekday    | avoid_Martes       | False              |               0.0437499 |         0.261481  |               -0.795185 |                          -0.941137 |                    261 |
| avoid_weekday    | avoid_Miércoles    | False              |              -0.676426  |        -0.221295  |               -0.754437 |                          -1.66131  |                    262 |
| avoid_weekday    | avoid_Jueves       | True               |               1.26987   |         0.579089  |               -0.76585  |                           0.284984 |                    262 |
| avoid_weekday    | avoid_Viernes      | False              |              -0.06523   |         0.21805   |               -0.675303 |                          -1.05012  |                    262 |
| avoid_weekday    | avoid_Sábado       | False              |              -0.042896  |         0.240895  |               -0.794712 |                          -1.02778  |                    262 |
| avoid_weekday    | avoid_Domingo      | False              |              -0.442884  |         0.026604  |               -0.793157 |                          -1.42777  |                    261 |


## Riesgos encontrados

- **Sesgo de selección:** S1 descubrió miércoles/jueves en el mismo histórico que ahora se audita.
- **Riesgo de calendario:** las reglas dependen de día UTC; un cambio de zona horaria o régimen puede destruir el efecto.
- **No hay prueba multi-activo:** BTCUSDT 1h no basta para generalizar.
- **No hay datos futuros realmente no vistos:** walk-forward reduce el riesgo, pero no reemplaza una validación futura congelada.
- **Costes y ejecución:** se modelan comisiones/slippage simples; no hay spread dinámico, liquidez intrabar ni latencia.
- **Capacidad de inferencia limitada:** patrones temporales pueden ser artefactos de ciclos de mercado específicos.

## Conclusión disciplinada

Ninguna estrategia sobrevive de forma limpia a la auditoría S3.
La siguiente validación obligatoria, si alguna regla sobrevive, es prueba multi-activo y luego forward paper trading con reglas congeladas.

## Archivos generados

- `tables/audit_verdict.csv`
- `tables/lookahead_leakage_audit.csv`
- `tables/walk_forward_windows.csv`
- `tables/walk_forward_results_base.csv`
- `tables/walk_forward_aggregate_base.csv`
- `tables/cost_stress_summary.csv`
- `tables/yearly_robustness.csv`
- `tables/monte_carlo_summary.csv`
- `tables/timezone_fragility.csv`
- `tables/selection_bias_controls.csv`
- `charts/*.png`
- `reports/s3_overfit_audit_report.md`