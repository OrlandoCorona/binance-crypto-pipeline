# Protocolo H2 + Bull, versión 1

Decisiones fijadas antes de ejecutar resultados nuevos, 25 de septiembre de 2026.
Este documento y `research/h2_bull_protocol.json` describen el experimento nuevo;
no validan conclusiones de los informes antiguos sobre estrategias de calendario.

## Hipótesis y criterios

H2 es reversión corta en baja volatilidad, **no** Wednesday Long. Se conserva la
cuadrícula original Q4 de 216 combinaciones. Se selecciona por mayor retorno neto
IS de H2 **sin filtro**; empate: menos ejecuciones, luego orden fijo de cuadrícula.
La misma variante y umbral de volatilidad se usan en H2 y H2+Bull durante OOS.
No se selecciona retrospectivamente la variante que mejor combine con Bull.

Bull queda fijado: cierre > SMA de **4.800 barras horarias (200 días)** × 1,02.
No se prueban otras medias ni bandas dentro de este experimento. Se requiere el
calentamiento completo. Una eventual modificación crea otra hipótesis y debe
registrarse como otro intento, nunca ocultar el resultado de esta versión.

Se compra cuando H2 y Bull son verdaderos; se vende cuando cualquiera deja de
serlo. Se utiliza todo el capital disponible, sin deuda; efectivo en USDT con
rendimiento cero. No hay posiciones cortas. Señales calculadas al cierre de t,
ejecución en apertura de t+1, con slippage adverso de 0,05% y comisión de 0,10%
en cada lado, incluidas compras y ventas de buy-and-hold. Precio de apertura más
slippage es una aproximación de ejecución, no garantía de fill en mercado vivo.

El retorno H2 usa caída acumulada de 3/6/12/24 horas; volatilidad de log-retornos
con std poblacional y ventanas 24/72/168 h, siguiendo Q4. El umbral es el cuantil
0,33/0,50/0,66 calculado **solo con IS**, congelado en el OOS siguiente. La opción
de evitar fin de semana corresponde al día UTC del cierre que emite la señal,
igual que la función Q4 existente. No se añade lógica de duración de posiciones.

## Validación

- IS rolling de 24 meses, OOS de 3 meses, avance de 3 meses. Primer IS desde el
  primer inicio de mes completo disponible. Última ventana parcial identificada.
- IS permite ajuste retrospectivo: sus resultados están sesgados por selección.
  Se informa junto con OOS, nunca como evidencia independiente.
- Ningún retorno que termine dentro de OOS se usa para rankear IS. El historial
  anterior a IS puede calentar indicadores causales, pero no contribuir al retorno IS.
- Cada recalibración usa datos anteriores a su apertura OOS. La señal del último
  cierre IS puede ejecutarse en la primera apertura OOS. Se presupone que el
  cálculo está disponible entonces; paper trading debe medir la latencia real.
- OOS concatena posiciones y capital, sin liquidaciones artificiales entre folds.
  La nueva variante determina el objetivo en el cambio de ventana y cualquier
  operación resultante paga costes. Liquidación final en última apertura disponible.
- Las cuatro alternativas comparten calendario OOS: buy-and-hold, H2, H2+Bull y
  Bull solo. La última vela aporta su apertura terminal, no un retorno inventado
  ni una apertura de junio. El drawdown usa capital inicial como primer máximo.
- No se filtran candidatos sin operaciones a posteriori; pueden ganar IS si todos
  los que operan pierden. Se advierte la falta de operaciones como limitación.

## Qué significa ganar

Rentabilidad y riesgo se juzgan por separado. Se compara retorno neto compuesto
OOS con buy-and-hold, H2 y Bull solo; se reportan CAGR, Sharpe, drawdown, Calmar,
exposición, operaciones, profit factor y win rate globales. Profit factor sin
pérdidas y ratios sin denominador quedan indefinidos, no como números perfectos.
Calmar depende tanto del retorno como del drawdown: no es riesgo puro.

Menor drawdown observado es una mejora histórica de riesgo válida, incluso si
el retorno queda por debajo de buy-and-hold; no demuestra que persistirá. No se
presupone que el filtro mejore ninguno de los dos objetivos.

Bootstrap pareado por bloques circulares de 24/168/720 h, 1.000 réplicas por
tamaño y semilla 42. IC percentil 95% del exceso anualizado de log-retorno. Se
remuestrean las mismas horas en ambas alternativas. Si todos los IC están por
encima de cero: ventaja histórica exploratoria; si todos están debajo: desventaja
histórica exploratoria. Si cruzan cero, discrepan o falta muestra: **inconcluso**.
Estos IC no corrigen la búsqueda histórica de hipótesis, no reentrenan estrategias
y no garantizan cobertura nominal bajo no estacionariedad. No son prueba definitiva.

Diagnósticos heurísticos predeclarados, no reglas de aceptación: Sharpe >3,
drawdown absoluto <1% con retorno positivo, diferencia absoluta de Sharpe IS/OOS
≤0,1 y menos de 30 operaciones cerradas OOS por ventana. Son motivos para revisar
ejecución, exposición, calibración, tamaño de muestra y solapamiento; no prueban
por sí mismos fraude estadístico ni ausencia de valor. Se guardan en configuración.

## Separación del holdout

Todo el histórico hasta mayo de 2026 ya fue usado para investigación. El nuevo
walk-forward es exploratorio, aunque cada ajuste respete IS/OOS. La utilidad del
walk-forward no borra la contaminación por decisiones de investigación previas.

El ejecutable **rechaza cualquier fila desde 2026-06-01 00:00 UTC** y no tiene
descarga de red. Junio–septiembre de 2026 queda reservado. El 25 de septiembre,
septiembre aún no está completo: no se debe presentar como mes íntegro ni incluir
velas abiertas. Antes de abrir ese holdout debe congelarse también su protocolo
de evaluación (modelo congelado o recalibraciones programadas), código y dataset
de entrenamiento. No volver a ajustar reglas tras verlo y llamarlo intacto.
Paper trading futuro es la evaluación prospectiva definitiva, todavía pendiente.

## Ejecución reproducible

CSV con encabezado o Parquet con `open_time_utc` (datetime/ISO UTC), `open`, `high`,
`low`, `close`, `volume`. El input debe ser BTCUSDT spot 1h; si no contiene columnas
`symbol`/`interval`, el operador debe verificar su procedencia. Se rechazan huecos,
duplicados, orden incorrecto y OHLCV inválido en lugar de corregirlos silenciosamente.

```powershell
python -m unittest tests.test_h2_bull_walk_forward -v
python -m research.h2_bull_walk_forward --input "C:\ruta\BTCUSDT_1h_hasta_mayo2026.parquet" --output data/reports/h2_bull_v1_run01
```

La salida debe ser un directorio nuevo. Genera:

- `manifest.json`: configuración, SHA-256 de dataset, código y protocolo, versiones.
- `selection.csv`: parámetros y umbral por ventana, fechas y tramos parciales.
- `candidate_scores.csv`: todos los candidatos IS, no solo el ganador.
- `window_metrics.csv`: métricas IS/OOS y alertas por ventana.
- `summary.csv`, `equity_curves.csv`, `trades.csv`: métricas agregadas, curva continua
  y operaciones completas (pueden atravesar ventanas).
- `bootstrap.json` y `report.md`: incertidumbre, veredicto y límites en español.

Las tablas son propias del experimento; todavía no sustituyen el modelo de Power BI.
El motor Q6 y los resultados existentes permanecen disponibles para comparación.
Pruebas sintéticas verifican causalidad y contabilidad, **no rentabilidad económica**.
