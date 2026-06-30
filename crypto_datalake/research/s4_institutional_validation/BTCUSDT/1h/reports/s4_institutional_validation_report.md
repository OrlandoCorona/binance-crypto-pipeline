# S4 — Validación institucional — BTCUSDT 1h

## Principio rector

Esta capa simula la revisión de un comité de riesgo de fondo. **No puede aprobar lo que S3 ya rechazó.** Si una estrategia llega aquí habiendo fallado la auditoría anti-overfitting, se documentan todas las métricas institucionales por transparencia, pero el veredicto final hereda el rechazo de S3.

## Motor de backtest

S4 usa el motor unificado Q6 (open-to-open, señal[t] → posición ejecutada en apertura de t+1, benchmark frictionless Buy & Hold, ddof=1 en todas las métricas). El motor close-to-close propio fue eliminado en la migración a Q6.

## Dataset

- Filas analizadas: `43,817`
- Inicio UTC: `2021-06-01 00:00:00+00:00`
- Fin UTC: `2026-05-31 23:00:00+00:00`
- Costos institucionales: best_execution=(0.0004, 0.0006), realistic=(0.0008, 0.0012), stress=(0.0015, 0.003)
- Monte Carlo: `2000` runs, bloque `24` barras, ruina si drawdown <= `-50%`
- Régimen: ventana tendencia `4800` barras, ventana volatilidad `720` barras, banda RANGE `±2%`

## Veredicto institucional final

| strategy_id          | s3_status            | institutional_status   | reasons                                                                                                                                                                                                                                                                                                                                                                                                                               |
|:---------------------|:---------------------|:-----------------------|:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| S2_H2_WEDNESDAY_LONG | INCONCLUSIVE_FRAGILE | REJECTED_AT_S3_GATE    | S3 ya clasificó esta estrategia como 'INCONCLUSIVE_FRAGILE'. S4 no puede aprobar lo que S3 rechazó, sin importar el resultado institucional. Motivos de S3: menos de 60% de ventanas walk-forward baten benchmark; mediana de exceso vs benchmark en walk-forward no positiva; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas                           |
| S2_H4_AVOID_THURSDAY | FAILS_OVERFIT_AUDIT  | REJECTED_AT_S3_GATE    | S3 ya clasificó esta estrategia como 'FAILS_OVERFIT_AUDIT'. S4 no puede aprobar lo que S3 rechazó, sin importar el resultado institucional. Motivos de S3: menos de 60% de ventanas walk-forward positivas; menos de 60% de ventanas walk-forward baten benchmark; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas; menos de 60% de años baten benchmark |


## Gate de entrada: estado en S3

| strategy_id          | audit_status         | audit_reasons                                                                                                                                                                                                                                                              |
|:---------------------|:---------------------|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| S2_H2_WEDNESDAY_LONG | INCONCLUSIVE_FRAGILE | menos de 60% de ventanas walk-forward baten benchmark; mediana de exceso vs benchmark en walk-forward no positiva; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas                            |
| S2_H4_AVOID_THURSDAY | FAILS_OVERFIT_AUDIT  | menos de 60% de ventanas walk-forward positivas; menos de 60% de ventanas walk-forward baten benchmark; cost stress medium: bate benchmark en menos de 50% de ventanas; cost stress high: bate benchmark en menos de 50% de ventanas; menos de 60% de años baten benchmark |


## Desempeño por régimen de mercado (walk-forward, costos realistas)

El régimen se calcula con tendencia (precio vs SMA de 200 días) y volatilidad realizada (terciles), y se usa **solo para etiquetar y segmentar resultados**, nunca para decidir cuándo entra o sale la regla. La regla original permanece congelada en todo momento.

| strategy_id          | regime       |   window_count |   positive_window_rate |   beat_benchmark_window_rate |   median_test_return |   median_excess_vs_benchmark |   worst_test_return |
|:---------------------|:-------------|---------------:|-----------------------:|-----------------------------:|---------------------:|-----------------------------:|--------------------:|
| S2_H2_WEDNESDAY_LONG | BEAR_HIGHVOL |              2 |               0.5      |                     1        |           -0.0540613 |                   0.201802   |          -0.163503  |
| S2_H2_WEDNESDAY_LONG | BEAR_LOWVOL  |              2 |               1        |                     0        |            0.0983617 |                  -0.331655   |           0.0983233 |
| S2_H2_WEDNESDAY_LONG | BEAR_MEDVOL  |              2 |               0.5      |                     0.5      |           -0.0193726 |                   0.0475043  |          -0.0896443 |
| S2_H2_WEDNESDAY_LONG | BULL_HIGHVOL |              1 |               1        |                     0        |            0.162694  |                  -0.187538   |           0.162694  |
| S2_H2_WEDNESDAY_LONG | BULL_LOWVOL  |              6 |               0.666667 |                     0.166667 |            0.0199364 |                  -0.0976649  |          -0.180828  |
| S2_H2_WEDNESDAY_LONG | BULL_MEDVOL  |              3 |               0.666667 |                     0.333333 |            0.0477785 |                  -0.012286   |          -0.121249  |
| S2_H4_AVOID_THURSDAY | BEAR_HIGHVOL |              2 |               0        |                     0        |           -0.351659  |                  -0.0957965  |          -0.387296  |
| S2_H4_AVOID_THURSDAY | BEAR_LOWVOL  |              2 |               1        |                     0.5      |            0.435809  |                   0.00579218 |           0.421058  |
| S2_H4_AVOID_THURSDAY | BEAR_MEDVOL  |              2 |               0.5      |                     1        |            0.0568529 |                   0.12373    |          -0.153068  |
| S2_H4_AVOID_THURSDAY | BULL_HIGHVOL |              1 |               1        |                     0        |            0.239406  |                  -0.110825   |           0.239406  |
| S2_H4_AVOID_THURSDAY | BULL_LOWVOL  |              6 |               0.5      |                     0.5      |            0.0590746 |                  -0.0258483  |          -0.191263  |
| S2_H4_AVOID_THURSDAY | BULL_MEDVOL  |              3 |               0.666667 |                     0.333333 |            0.0730142 |                  -0.0250208  |          -0.0243021 |


## Costos de ejecución institucional

`best_execution` asume acceso preferencial; `realistic` es el escenario base esperado; `stress` simula impacto de mercado elevado por tamaño de orden en condiciones adversas.

| scenario       | strategy_id          |   commission |   slippage |   window_count |   positive_window_rate |   beat_benchmark_window_rate |   median_test_return |   median_excess_vs_benchmark |
|:---------------|:---------------------|-------------:|-----------:|---------------:|-----------------------:|-----------------------------:|---------------------:|-----------------------------:|
| best_execution | S2_H2_WEDNESDAY_LONG |       0.0004 |     0.0006 |             16 |                 0.6875 |                       0.375  |            0.0729058 |                   -0.0724306 |
| best_execution | S2_H4_AVOID_THURSDAY |       0.0004 |     0.0006 |             16 |                 0.625  |                       0.5625 |            0.134929  |                    0.0168827 |
| realistic      | S2_H2_WEDNESDAY_LONG |       0.0008 |     0.0012 |             16 |                 0.6875 |                       0.3125 |            0.0453219 |                   -0.0976649 |
| realistic      | S2_H4_AVOID_THURSDAY |       0.0008 |     0.0012 |             16 |                 0.5625 |                       0.4375 |            0.10466   |                   -0.0144785 |
| stress         | S2_H2_WEDNESDAY_LONG |       0.0015 |     0.003  |             16 |                 0.25   |                       0.1875 |           -0.020687  |                   -0.158052  |
| stress         | S2_H4_AVOID_THURSDAY |       0.0015 |     0.003  |             16 |                 0.5625 |                       0.0625 |            0.0323501 |                   -0.0981679 |


## Monte Carlo institucional (bootstrap pareado por bloques, Q6)

Cada trayectoria remuestrea los mismos índices de bloque para la estrategia y el benchmark, de forma que `mc_probability_beat_benchmark` mide ventaja real ante secuencias alternativas de mercado, no solo signo del retorno.

| strategy_id          |   observed_total_return |   observed_max_drawdown |   mc_runs |   mc_probability_loss |   mc_probability_ruin |   mc_probability_beat_benchmark |   mc_return_p05 |   mc_return_p50 |   mc_return_p95 |   mc_max_drawdown_p05 |   mc_max_drawdown_p50 |
|:---------------------|------------------------:|------------------------:|----------:|----------------------:|----------------------:|--------------------------------:|----------------:|----------------:|----------------:|----------------------:|----------------------:|
| S2_H2_WEDNESDAY_LONG |              -0.0299713 |               -0.514013 |      2000 |                 0.51  |                0.3315 |                          0.254  |       -0.552634 |      -0.0202535 |         1.16035 |             -0.678596 |             -0.435583 |
| S2_H4_AVOID_THURSDAY |               0.746635  |               -0.778183 |      2000 |                 0.311 |                0.8855 |                          0.3965 |       -0.706005 |       0.740134  |         9.44916 |             -0.872642 |             -0.65008  |


## Stress test: peores crisis históricas del benchmark

Se identifican los peores drawdowns históricos de Buy & Hold y se mide cómo se habría comportado la regla congelada exactamente durante esos episodios, sin re-optimizar nada.

|   crisis_rank | strategy_id          | start_time                | trough_time               | end_time                  |   benchmark_drawdown_depth |   benchmark_total_return_in_episode |   strategy_total_return_in_episode |   excess_return_in_episode |
|--------------:|:---------------------|:--------------------------|:--------------------------|:--------------------------|---------------------------:|------------------------------------:|-----------------------------------:|---------------------------:|
|             1 | S2_H2_WEDNESDAY_LONG | 2021-11-10T18:00:00+00:00 | 2022-11-21T21:00:00+00:00 | 2024-03-05T14:00:00+00:00 |                  -0.772008 |                         -0.0126043  |                         -0.334644  |                 -0.32204   |
|             1 | S2_H4_AVOID_THURSDAY | 2021-11-10T18:00:00+00:00 | 2022-11-21T21:00:00+00:00 | 2024-03-05T14:00:00+00:00 |                  -0.772008 |                         -0.0126043  |                         -0.0467265 |                 -0.0341222 |
|             2 | S2_H2_WEDNESDAY_LONG | 2025-10-06T19:00:00+00:00 | 2026-02-24T13:00:00+00:00 | 2026-05-31T22:00:00+00:00 |                  -0.500838 |                         -0.413603   |                         -0.127427  |                  0.286177  |
|             2 | S2_H4_AVOID_THURSDAY | 2025-10-06T19:00:00+00:00 | 2026-02-24T13:00:00+00:00 | 2026-05-31T22:00:00+00:00 |                  -0.500838 |                         -0.413603   |                         -0.206583  |                  0.20702   |
|             3 | S2_H2_WEDNESDAY_LONG | 2024-03-14T07:00:00+00:00 | 2024-08-05T12:00:00+00:00 | 2024-11-06T03:00:00+00:00 |                  -0.323107 |                         -0.00679965 |                         -0.162772  |                 -0.155972  |
|             3 | S2_H4_AVOID_THURSDAY | 2024-03-14T07:00:00+00:00 | 2024-08-05T12:00:00+00:00 | 2024-11-06T03:00:00+00:00 |                  -0.323107 |                         -0.00679965 |                         -0.204349  |                 -0.197549  |


## Riesgos y limitaciones

- **El gate de S3 es vinculante:** ninguna métrica institucional puede revertir un rechazo previo.
- **Un solo activo:** BTCUSDT 1h no permite generalizar a un régimen multi-activo o multi-exchange.
- **Régimen es descriptivo:** los umbrales de volatilidad se calculan sobre toda la muestra; sirven para reportar, no para operar.
- **Pocas crisis históricas:** con ~5 años de datos hay pocos episodios de crisis severa; el stress test tiene poca muestra.
- **Costos modelados de forma simple:** no hay libro de órdenes, profundidad real ni latencia de ejecución.

## Conclusión disciplinada

Ninguna estrategia evaluada queda aprobada para asignación de capital institucional. Esto es consistente con el resultado de S3: ambas hipótesis de calendario fallaron la auditoría anti-overfitting antes de llegar a esta capa, y S4 respeta esa conclusión en lugar de buscar una forma de revertirla. El laboratorio funcionó como debía: protegió el capital al no aceptar una estrategia solo porque una métrica aislada se viera bien.

## Archivos generados

- `tables/institutional_verdict.csv`
- `tables/regime_breakdown.csv`
- `tables/institutional_cost_stress.csv`
- `tables/monte_carlo_institutional_summary.csv`
- `tables/crisis_stress_test.csv`
- `tables/walk_forward_by_regime_detail.csv`
- `charts/*.png`
- `reports/s4_institutional_validation_report.md`