# Análisis Exploratorio Cuantitativo — BTCUSDT 1h

## Alcance

Este reporte estudia estadísticamente el comportamiento de mercado antes de diseñar estrategias. No genera señales de compra ni venta.

## Dataset analizado

- Símbolo: `BTCUSDT`
- Intervalo: `1h`
- Filas analizadas: `43,817`
- Inicio UTC: `2021-06-01 00:00:00+00:00`
- Fin UTC: `2026-05-31 23:00:00+00:00`
- Factor de anualización usado: `8760.0`

## 1. Distribución de retornos

Los retornos simples tienen media `0.00003156` y desviación estándar `0.00571445` por vela.

Interpretación cuantitativa: en datos intradía, la media por vela suele ser pequeña frente a la volatilidad. Por eso un investigador normalmente no busca ventaja en la media simple aislada, sino en estructura condicional, régimen, volatilidad, volumen o persistencia.

## 2. Retornos logarítmicos

Los retornos logarítmicos tienen:

- Media: `0.00001523`
- Desviación estándar: `0.00571612`
- Asimetría: `-0.1571`
- Curtosis excedente: `12.2911`

Interpretación cuantitativa: si la curtosis excedente es positiva y elevada, la distribución tiene colas más pesadas que una normal. Eso implica que los eventos extremos son relevantes y que una estrategia debe analizar riesgo de cola, no solo retorno promedio.

## 3. Volatilidad histórica

Se generó una volatilidad rolling con ventana `24` velas.

Interpretación cuantitativa: los periodos de alta volatilidad suelen concentrarse en regímenes. Un investigador puede usar esta información para separar análisis por régimen de volatilidad antes de probar reglas.

## 4. Volatilidad por hora del día UTC

- Hora con mayor volatilidad: `14:00 UTC`, volatilidad `0.00824830`
- Hora con menor volatilidad: `4:00 UTC`, volatilidad `0.00409720`

Interpretación cuantitativa: diferencias por hora pueden sugerir efectos de sesión, liquidez o participación institucional/regional. No son señales por sí mismas; sirven para segmentar el comportamiento del mercado.

## 5. Volatilidad por día de semana UTC

- Día con mayor volatilidad: `Lunes`, volatilidad `0.00650724`
- Día con menor volatilidad: `Sábado`, volatilidad `0.00404110`

Interpretación cuantitativa: si ciertos días concentran mayor volatilidad, conviene evaluar si el fenómeno es estable por año y no solo producto de eventos aislados.

## 6. Drawdowns históricos

El peor drawdown detectado fue de -77.20%, con pico en 2021-11-10 17:00:00+00:00 y valle en 2022-11-21 21:00:00+00:00.

Interpretación cuantitativa: el drawdown muestra cuánto sufrió una posición pasiva desde máximos. Sirve como referencia de riesgo estructural del activo, aunque todavía no evalúa una estrategia.

## 7. Rachas alcistas y bajistas

Resumen de rachas generado en `tables/streak_summary.csv`.

Interpretación cuantitativa: las rachas ayudan a observar si el mercado tiende a alternar dirección rápidamente o si existen tramos persistentes. Por sí solas no prueban ventaja; deben compararse contra pruebas fuera de muestra.

## 8. Persistencia de tendencias

- Probabilidad de continuidad después de vela alcista: `0.4759`
- Probabilidad de continuidad después de vela bajista: `0.4651`
- Persistencia general de dirección: `0.4706`

Interpretación cuantitativa: valores cercanos a 0.50 sugieren poca persistencia direccional simple. Valores claramente superiores o inferiores a 0.50 pueden justificar análisis adicional, pero no bastan para operar.

## 9. Autocorrelación de retornos

La autocorrelación más fuerte en valor absoluto fue:

- Lag: `24`
- Autocorrelación: `-0.016782`

Interpretación cuantitativa: autocorrelaciones pequeñas son comunes en retornos líquidos. Si aparece autocorrelación relevante, debe validarse por subperiodos, con costos y fuera de muestra.

## 10. Correlación entre volumen y volatilidad

La relación monotónica más fuerte por Spearman fue:

- `volume` vs `range_pct`
- Spearman: `0.614770`

Interpretación cuantitativa: una correlación positiva entre volumen y volatilidad puede indicar que el volumen es útil para identificar regímenes de actividad, no necesariamente dirección.

## Anomalías estadísticas candidatas

Total de anomalías candidatas detectadas: `23`.

Revisar `tables/anomalies.csv` para ver eventos extremos, colas pesadas, autocorrelación relevante o relación volumen-volatilidad marcada.

## Conclusión investigativa

Este análisis debe usarse para decidir cómo segmentar el mercado antes de diseñar estrategias. Las conclusiones más útiles no son señales, sino preguntas de investigación:

1. ¿Los retornos tienen colas pesadas y requieren gestión explícita de riesgo extremo?
2. ¿La volatilidad cambia por hora, día o régimen?
3. ¿Existe persistencia direccional o predomina reversión/ruido?
4. ¿El volumen ayuda a explicar volatilidad?
5. ¿Los resultados se mantienen por año o dependen de pocos eventos extremos?

Siguiente paso recomendado: crear un notebook o script de features cuantitativas, separando el dataset por año y por régimen de volatilidad para evitar conclusiones sobreajustadas.
