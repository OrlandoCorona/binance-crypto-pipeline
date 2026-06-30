# S2 — Estrategias basadas en hipótesis — BTCUSDT 1h

## Principio rector

Este reporte no declara que una estrategia funcione por un backtest. Cada regla nace de un hallazgo observado en S1(Q5), se evalúa con costos, se separa in-sample/out-of-sample y se clasifica de forma conservadora.

## Configuración

- Capital inicial: `10000.0`
- Comisión: `0.001`
- Slippage: `0.0005`
- Split in-sample: `70.00%`
- Monte Carlo runs: `500`
- Monte Carlo block size: `24` velas

## Hipótesis y reglas

### S2_H1_TUESDAY_24H_LONG — Exposición temporal en martes UTC
- Hipótesis: El patrón observado de martes con retorno forward 24h positivo podría reflejar un sesgo temporal explotable.
- Justificación: S1(Q5) encontró diferencia positiva frente al baseline para martes a 24h. La regla evita indicadores aleatorios y prueba solo el patrón temporal observado.
- Regla: Mantener posición spot long durante velas que pertenecen a martes UTC; estar fuera el resto del tiempo.

### S2_H2_WEDNESDAY_LONG — Exposición temporal en miércoles UTC
- Hipótesis: El patrón observado de miércoles con retorno forward 6h positivo podría reflejar un sesgo temporal intradía o de sesión.
- Justificación: S1(Q5) encontró diferencia positiva frente al baseline para miércoles a 6h. Se prueba una regla simple de exposición durante miércoles sin optimizar horarios internos.
- Regla: Mantener posición spot long durante velas que pertenecen a miércoles UTC; estar fuera el resto del tiempo.

### S2_H3_HOUR21_LONG — Exposición a la hora 21:00 UTC
- Hipótesis: La hora 21:00 UTC mostró retorno forward 1h positivo frente al baseline y podría capturar un micro-patrón horario.
- Justificación: S1(Q5) encontró evidencia positiva para la hora 21 UTC, aunque débil/moderada. Se evalúa sin buscar otras horas.
- Regla: Entrar solo en la vela que inicia a las 21:00 UTC y salir en la siguiente vela.

### S2_H4_AVOID_THURSDAY — Buy & Hold filtrando jueves UTC
- Hipótesis: Jueves mostró retornos forward negativos en 1h, 6h y 24h; evitar exposición ese día podría reducir deterioro del benchmark.
- Justificación: S1(Q5) encontró evidencia negativa consistente para jueves. En spot long-only se traduce en filtro de riesgo, no en venta corta.
- Regla: Mantener exposición pasiva long excepto durante jueves UTC.

### S2_H5_AVOID_LOW_RANGE_CONTRACTION — Buy & Hold filtrando contracción de rango
- Hipótesis: La contracción de rango q20 168h mostró retornos forward negativos; evitar exposición tras esa condición podría mejorar riesgo.
- Justificación: S1(Q5) encontró evidencia negativa para contracción de volatilidad a 6h y 24h. Se usa como filtro de riesgo sobre exposición pasiva.
- Regla: Mantener exposición pasiva long excepto justo después de velas con rango inferior al q20 rolling 168h.

### S2_H6_AVOID_THU_AND_CONTRACTION — Buy & Hold filtrando jueves y contracción
- Hipótesis: Combinar los dos patrones negativos observados podría reducir exposición a contextos desfavorables.
- Justificación: Esta regla combina hallazgos negativos de S1(Q5), no parámetros optimizados: jueves negativo y contracción de rango negativa.
- Regla: Mantener exposición pasiva long excepto durante jueves UTC y excepto tras contracción de rango q20 168h.

## Resumen de resultados

| strategy_id | research_status | full_return | oos_return | oos_sharpe | oos_max_drawdown | oos_excess_return_vs_benchmark | trade_count | positive_year_rate | beat_benchmark_year_rate | mc_probability_loss | rejection_reasons |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S2_H2_WEDNESDAY_LONG | CANDIDATE_FOR_FURTHER_VALIDATION | 0.259894 | 0.105263 | 0.472348 | -0.160325 | 0.343751 | 261.000000 | 0.666667 | 0.666667 | 0.322000 | Supera filtros mínimos, pero requiere walk-forward, costos más altos y otros activos. |
| S2_H4_AVOID_THURSDAY | CANDIDATE_FOR_FURTHER_VALIDATION | 1.269871 | 0.073549 | 0.321848 | -0.333243 | 0.312037 | 262.000000 | 0.666667 | 0.500000 | 0.222000 | Supera filtros mínimos, pero requiere walk-forward, costos más altos y otros activos. |
| S2_H1_TUESDAY_24H_LONG | REJECT_OR_REDESIGN | -0.608411 | -0.240926 | -0.898883 | -0.295644 | -0.002438 | 261.000000 | 0.166667 | 0.333333 | 0.970000 | retorno out-of-sample no positivo; Sharpe out-of-sample no positivo; no supera Buy & Hold out-of-sample; Monte Carlo muestra probabilidad de pérdida >= 50%; menos de la mitad de años con retorno positivo |
| S2_H3_HOUR21_LONG | REJECT_OR_REDESIGN | -0.994325 | -0.782625 | -11.015584 | -0.782742 | -0.544137 | 1826.000000 | 0.000000 | 0.000000 | 1.000000 | retorno out-of-sample no positivo; Sharpe out-of-sample no positivo; no supera Buy & Hold out-of-sample; Monte Carlo muestra probabilidad de pérdida >= 50%; menos de la mitad de años con retorno positivo |
| S2_H6_AVOID_THU_AND_CONTRACTION | REJECT_OR_REDESIGN | -0.999989 | -0.955443 | -4.963442 | -0.956830 | -0.716956 | 4458.000000 | 0.000000 | 0.000000 | 1.000000 | retorno out-of-sample no positivo; Sharpe out-of-sample no positivo; no supera Buy & Hold out-of-sample; Monte Carlo muestra probabilidad de pérdida >= 50%; menos de la mitad de años con retorno positivo |
| S2_H5_AVOID_LOW_RANGE_CONTRACTION | REJECT_OR_REDESIGN | -0.999998 | -0.981945 | -5.897736 | -0.982618 | -0.743457 | 4837.000000 | 0.000000 | 0.000000 | 1.000000 | retorno out-of-sample no positivo; Sharpe out-of-sample no positivo; no supera Buy & Hold out-of-sample; Monte Carlo muestra probabilidad de pérdida >= 50%; menos de la mitad de años con retorno positivo |

## Lectura disciplinada

Una estrategia solo puede avanzar si muestra evidencia fuera de muestra, suficientes operaciones, estabilidad temporal y Monte Carlo aceptable. Si una regla no supera Buy & Hold out-of-sample, no debe considerarse candidata aunque parezca atractiva in-sample.

## Archivos generados

- `tables/hypotheses_catalog.csv`
- `tables/strategy_summary.csv`
- `tables/metrics_by_sample.csv`
- `tables/yearly_robustness.csv`
- `tables/monte_carlo_summary.csv`
- `tables/trades.csv`
- `charts/equity_curves.png`
- `charts/drawdowns.png`

## Siguiente validación obligatoria

1. Walk-forward real con reglas congeladas.
2. Costos más altos que el escenario base.
3. Prueba por subperiodos de mercado alcista/bajista.
4. Repetición en otros activos líquidos.
5. Congelar hipótesis antes de mirar nuevos datos.