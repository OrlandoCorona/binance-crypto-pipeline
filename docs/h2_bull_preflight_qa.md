# QA y pre-flight H2 + Bull — revisión del 25 de septiembre de 2026

**NO listo — hay que investigar y resolver siete horas ausentes del train.**

Esta revisión sustituye el reporte anterior: los archivos ya están disponibles.
No se ejecutó el experimento económico con datos reales ni se modificaron motor,
protocolo, pruebas, script de reconstrucción o valores del dataset.

## Tarea 1 — separación completada

Se detectaron dos Parquet leyendo solo timestamps. El único que cruza el corte,
y por tanto el origen elegido automáticamente, es:

`crypto_datalake/processed/binance/spot/klines/BTCUSDT/1h/BTCUSDT_1h_2021-06-01_to_2026-09-25.parquet`

El archivo preexistente terminado en `2026-05-31.parquet` quedó intacto.

| Archivo | Filas | Inicio UTC | Fin UTC |
|---|---:|---|---|
| Original | 46.625 | 2021-06-01 00:00 | 2026-09-25 23:00 |
| Train creado | 43.817 | 2021-06-01 00:00 | 2026-05-31 23:00 |
| Holdout creado | 2.808 | 2026-06-01 00:00 | 2026-09-25 23:00 |

Salidas:

- `crypto_datalake/processed/binance/spot/klines/BTCUSDT/1h/BTCUSDT_1h_train_2021-06_to_2026-05.parquet`
- `data/holdout_locked/BTCUSDT_1h_holdout_2026-06_to_2026-09.parquet`

**PASA:** 43.817 + 2.808 = 46.625; esquema, metadatos y orden conservados.
Filtrado estable sin ordenar, imputar, convertir tipos ni eliminar duplicados.
Se verificó la igualdad de la copia train y de los timestamps del holdout.
Los nuevos Parquet se escribieron sin estadísticas por columna.

**PASA:** SHA-256 del original idéntico antes y después:
`85f78c9e627ce4303ec80892659942a2d1dc9b6a3f0befed353c7df70dd0c514`.

## Tarea 2 — integridad del train

| Control | Resultado | Evidencia |
|---|---|---|
| Esquema exacto y orden | PASA | symbol, interval, open_time_utc, open, high, low, close, volume |
| Datetime UTC | PASA | datetime64[ms, UTC]; 0 timestamps nulos |
| Estrictamente creciente, sin duplicados | PASA | 0 duplicados |
| Intervalos exactamente de 1h, sin huecos | **FALLA** | Faltan 7 velas, en 3 huecos |
| OHLCV sin nulos/negativos, precios >0, high>=low | PASA | 0 infracciones; todos los valores son finitos |
| Open/close dentro de low/high | PASA | 0 infracciones |
| Identidad del train | PASA | BTCUSDT / 1h |
| Rango solicitado | PASA | 2021-06-01 00:00 a 2026-05-31 23:00 UTC |
| Conteo frente a ~43.817 | PASA | 43.817; diferencia 0 |

El calendario completo exige **43.824** horas. Coincidir con la referencia de
43.817 no demuestra continuidad: faltan siete horas.

| Fecha UTC | Horas ausentes | Cantidad | Salto observado |
|---|---|---:|---|
| 2021-08-13 | 02:00, 03:00, 04:00, 05:00 | 4 | 01:00 → 06:00, 5h |
| 2021-09-29 | 07:00, 08:00 | 2 | 06:00 → 09:00, 3h |
| 2023-03-24 | 13:00 | 1 | 12:00 → 14:00, 2h |

No se determinó todavía si faltan en la fuente o en la descarga. No se corrigieron
en silencio. El motor exige 1h continua en `validate_market`, líneas 57–58:
este train sería rechazado. Se constató leyendo código, sin ejecutar el backtest.

## Tarea 3 — higiene del holdout

| Control | Resultado | Evidencia |
|---|---|---|
| max(train) < corte | PASA | 2026-05-31 23:00 UTC |
| min(holdout) >= corte | PASA | 2026-06-01 00:00 UTC |
| Carpeta holdout_locked existe y solo contiene el holdout | PASA | Un único archivo con el nombre solicitado |
| Holdout sin análisis económico ni modificación de valores | PASA | Solo copia técnica, esquema/orden, conteo y fechas |

Separar requiere leer y serializar las filas: esa fue la única manipulación
técnica. No se calcularon estadísticas OHLCV, indicadores, señales, gráficos o
rendimientos del holdout, ni se usó para seleccionar parámetros. No sería preciso
decir que nunca se leyó para copiarlo. La carpeta proporciona separación lógica;
no se instalaron restricciones de acceso del sistema operativo.

## Tarea 4 — código y pruebas

Referencias a `research/h2_bull_walk_forward.py`:

| Control | Resultado | Evidencia |
|---|---|---|
| Cuantil solo IS, congelado OOS | PASA | `run_experiment` 282–283 recorta train; `select_h2` 217 pasa train a `fit_threshold`; líneas 101–105 calculan su cuantil; línea 300 reutiliza threshold en test |
| H2 reversión corta en baja volatilidad | PASA | `h2_signal` 108–113: caída previa y volatilidad <= umbral; no regla Wednesday Long |
| Bull SMA4800h ×1,02, causal y calentamiento completo | PASA | Valores bloqueados en 30; `features` 90 usa rolling no centrado y min_periods=window |
| Cierre t → apertura t+1 | PASA | `signal_at_next_open` 120; alineación OOS 299–305; cubierto por pruebas |
| Comisión 0,10% + slippage 0,05%, cada lado, incluido buy-and-hold | PASA | Valores en 31; `net_returns` 140–145; mismo motor para todas las alternativas en 313–315 |
| Rechaza fechas >=2026-06-01 | PASA | `validate_market` 59–60, invocado antes de features en 278 |
| Pruebas unitarias sin cambios | PASA | 14 pasan; 0 fallos; 0 errores; 0 omitidas |

Comando ejecutado con el intérprete disponible:

```text
C:/Users/menes/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -m unittest tests.test_h2_bull_walk_forward -v
Ran 14 tests in 7.675s
OK
```

Los tests ejecutan simulaciones sobre datos **sintéticos** exclusivamente.

Observación adicional: el texto fijo del reporte, línea 394, dice «No se ha
descargado ni evaluado el holdout». La primera parte está desactualizada: el
holdout está descargado y separado, aunque no evaluado. No se modificó el código;
queda señalado para corregir después.

## Evidencia y conclusión

- Evidencia estructurada: `data/reports/h2_bull_data_qa.json`.
- Script QA: `data/reports/split_and_verify_h2_data.py`; no importa estrategias y se niega a sobrescribir salidas.
- PyArrow se instaló localmente en `data/qa_runtime` para efectuar la copia.
- No se ejecutaron backtests con datos reales ni se alteraron las pruebas.

**NO listo — investigar las siete horas ausentes y acordar su tratamiento antes
de satisfacer la exigencia de continuidad del motor.** No se deben inventar velas
ni relajar el validador sin una decisión explícita. La ejecución del experimento
económico sigue pendiente de autorización.
