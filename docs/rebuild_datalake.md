# Reconstrucción del data lake

`rebuild_datalake.py` descarga velas del archivo público de Binance, verifica SHA256 y guarda archivos Parquet. Detecta timestamps en milisegundos y microsegundos por valor y reutiliza los ZIP de `crypto_datalake/raw/`. No requiere credenciales.

## Instalación y ejecución

Desde la raíz del repositorio:

```bash
pip install requests pandas pyarrow
python rebuild_datalake.py --symbol BTCUSDT --interval 1h --start 2021-06 --end 2026-05
```

Los archivos se guardan bajo `crypto_datalake/processed/binance/spot/klines/`, organizados por símbolo e intervalo. Los nombres incluyen las fechas inicial y final.
