# Backtest profesional — sma_cross_demo
## Alcance
Este reporte evalúa una estrategia dentro de un framework spot long-only con cero lookahead operativo.
La señal calculada en la vela `t` se ejecuta hasta la apertura de la vela `t+1`.
## Configuración
- Símbolo: `BTCUSDT`
- Intervalo: `1h`
- Capital inicial: `10000.0`
- Comisión por operación: `0.001`
- Slippage estimado: `0.0005`
- Factor de anualización: `8760.0`
- Split in-sample: `70.00%`
## Métricas globales
| Métrica | Valor |
|---|---:|
| total_return | -0.218014 |
| cagr | -0.047977 |
| sharpe | 0.043550 |
| sortino | 0.040942 |
| calmar | -0.071065 |
| max_drawdown | -0.675113 |
| final_equity | 7819.859739 |
| mean_period_return | 0.000002 |
| std_period_return | 0.003847 |
| trade_count | 304.000000 |
| win_rate | 0.273026 |
| profit_factor | 1.038358 |
| expectancy | 0.000611 |
| average_trade_return | 0.000611 |
| average_bars_held | 73.378289 |
| buy_hold_total_return | 0.984887 |
| buy_hold_cagr | 0.143577 |
| buy_hold_sharpe | 0.523779 |
| buy_hold_sortino | 0.669564 |
| buy_hold_calmar | 0.185979 |
| buy_hold_max_drawdown | -0.772008 |
| buy_hold_final_equity | 19848.869190 |
| buy_hold_mean_period_return | 0.000032 |
| buy_hold_std_period_return | 0.005715 |
| buy_hold_trade_count | 1.000000 |
| buy_hold_win_rate | 1.000000 |
| buy_hold_profit_factor | nan |
| buy_hold_expectancy | 0.984887 |
| buy_hold_average_trade_return | 0.984887 |
| buy_hold_average_bars_held | 43816.000000 |

## Métricas In-Sample / Out-of-Sample
| sample | total_return | cagr | sharpe | sortino | calmar | max_drawdown | final_equity | mean_period_return | std_period_return | trade_count | win_rate | profit_factor | expectancy | average_trade_return | average_bars_held | buy_hold_total_return | buy_hold_cagr | buy_hold_sharpe | buy_hold_sortino | buy_hold_calmar | buy_hold_max_drawdown | buy_hold_final_equity | buy_hold_mean_period_return | buy_hold_std_period_return | buy_hold_trade_count | buy_hold_win_rate | buy_hold_profit_factor | buy_hold_expectancy | buy_hold_average_trade_return | buy_hold_average_bars_held |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full | -0.218014 | -0.047977 | 0.043550 | 0.040942 | -0.071065 | -0.675113 | 7819.859739 | 0.000002 | 0.003847 | 304.000000 | 0.273026 | 1.038358 | 0.000611 | 0.000611 | 73.378289 | 0.984887 | 0.143577 | 0.523779 | 0.669564 | 0.185979 | -0.772008 | 19848.869190 | 0.000032 | 0.005715 | 1.000000 | 1.000000 | nan | 0.984887 | 0.984887 | 43816.000000 |
| in_sample | 0.382714 | 0.096974 | 0.433358 | 0.410499 | 0.143640 | -0.675113 | 13827.144995 | 0.000019 | 0.004079 | 211.000000 | 0.279621 | 1.204418 | 0.003412 | 0.003412 | 74.298578 | 1.595175 | 0.307660 | 0.764128 | 0.978543 | 0.398519 | -0.772008 | 25951.749378 | 0.000049 | 0.006053 | 1.000000 | 1.000000 | nan | 1.595175 | 1.595175 | 30670.000000 |
| out_of_sample | -0.431496 | -0.313641 | -1.092792 | -1.005120 | -0.650934 | -0.481832 | 5685.041343 | -0.000038 | 0.003232 | 93.000000 | 0.258065 | 0.597873 | -0.005709 | -0.005709 | 70.655914 | -0.238488 | -0.163376 | -0.174683 | -0.223630 | -0.326206 | -0.500838 | 7615.123139 | -0.000009 | 0.004836 | 1.000000 | 0.000000 | 0.000000 | -0.238488 | -0.238488 | 13145.000000 |

## Interpretación técnica
- El resultado global debe compararse contra Buy & Hold, no solo contra cero.
- Si una estrategia gana in-sample pero falla out-of-sample, puede estar sobreajustada.
- Si el Sharpe es positivo pero el Max Drawdown es alto, el riesgo puede no compensar.
- Si el Profit Factor depende de muy pocas operaciones, la evidencia es débil.
- Si el desempeño se concentra en un periodo específico, debe validarse por régimen y por año.

## Sesgos que este framework busca reducir
1. Lookahead bias: señales se desplazan una vela antes de ejecutarse.
2. Costos ignorados: se descuentan comisión y slippage.
3. Validación temporal incorrecta: el split es cronológico, no aleatorio.
4. Benchmark ausente: se compara contra Buy & Hold.
5. Métricas incompletas: se reportan retorno, drawdown, Sharpe, Sortino, Calmar, Profit Factor, Expectancy y Win Rate.
