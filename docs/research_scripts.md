# Scripts de investigación

Comandos de ejecución desde la raíz del repositorio, con el entorno virtual activo.

## quant_eda.py

Análisis exploratorio cuantitativo del dataset limpio. No genera señales de compra o venta; produce gráficos, tablas y un reporte interpretativo.

```powershell
python research\quant_eda.py `
  --input .\crypto_datalake\processed\binance\spot\klines\BTCUSDT\1h\BTCUSDT_1h_2021-06_to_2026-05.parquet `
  --output-dir .\crypto_datalake\research\quant_eda\BTCUSDT\1h `
  --symbol BTCUSDT `
  --interval 1h
```

## q5_pattern_discovery.py

Descubrimiento de patrones por hora, día de la semana, volatilidad, momentum y reversión a la media, con ventanas fijas y sin búsqueda de parámetros.

```powershell
python research\q5_pattern_discovery.py `
  --input .\crypto_datalake\research\quant_eda\BTCUSDT\1h\BTCUSDT_1h_quant_eda_enriched.parquet `
  --output-dir .\crypto_datalake\research\q5_patterns\BTCUSDT\1h `
  --symbol BTCUSDT `
  --interval 1h
```
