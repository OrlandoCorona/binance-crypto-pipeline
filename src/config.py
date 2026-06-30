"""
config.py — Configuración centralizada del proyecto.

Todas las rutas, símbolos y parámetros viven aquí.
Si necesitas cambiar algo, lo cambias en un solo lugar.
"""

import os
from pathlib import Path

# ── Rutas del proyecto ────────────────────────────────────────────────────────

ROOT_DIR      = Path(__file__).parent.parent   # raíz del proyecto
DATA_DIR      = ROOT_DIR / "data"
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORTS_DIR   = DATA_DIR / "exports"           # CSVs listos para Power BI
REPORTS_DIR   = DATA_DIR / "reports"           # reportes generados
LOGS_DIR      = ROOT_DIR / "logs"

# Data lake original (ZIPs y Parquets de Binance ya procesados)
DATALAKE_DIR  = ROOT_DIR / "crypto_datalake"

# ── Símbolos e intervalos ─────────────────────────────────────────────────────

SYMBOLS   = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
INTERVALS = ["1h"]

# URL base para descarga de datos históricos de Binance
BINANCE_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

# ── Base de datos PostgreSQL ──────────────────────────────────────────────────
# Los valores por defecto sirven para desarrollo local.
# En producción, usar variables de entorno definidas en .env

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "crypto_pipeline")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

# ── Parámetros del pipeline ───────────────────────────────────────────────────

LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

# Umbral para marcar un outlier en returns (número de desviaciones estándar)
OUTLIER_SIGMA = 4.0

# Umbrales de régimen de mercado (retorno mensual del activo)
REGIME_BULL_THRESHOLD  =  0.05   # >= +5% → Bull
REGIME_BEAR_THRESHOLD  = -0.05   # <= -5% → Bear
