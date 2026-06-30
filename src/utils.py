"""
utils.py — Utilidades compartidas: logging y helpers pequeños.

Este módulo no importa nada del proyecto para evitar dependencias circulares.
Cualquier otro módulo en src/ puede importarlo sin problema.
"""

import logging
import sys
import time
from pathlib import Path
from datetime import datetime


# ── Logging ───────────────────────────────────────────────────────────────────

def get_logger(name: str, log_dir: Path = None, level: str = "INFO") -> logging.Logger:
    """
    Retorna un logger configurado con salida a consola y opcionalmente a archivo.

    Uso:
        import logging
        from src.utils import get_logger

        log = get_logger(__name__)
        log.info("Procesando %s filas para %s", n_rows, symbol)

    Args:
        name:    Nombre del logger (usa __name__ en cada módulo).
        log_dir: Directorio donde guardar el archivo .log. Si es None, solo consola.
        level:   Nivel de logging: "DEBUG", "INFO", "WARNING", "ERROR".

    Returns:
        logging.Logger configurado.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Evitar handlers duplicados si el logger ya fue configurado
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de consola
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Handler de archivo (opcional)
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        module_name = name.split(".")[-1]
        fh = logging.FileHandler(log_dir / f"{date_str}_{module_name}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ── Helpers de sistema de archivos ────────────────────────────────────────────

def ensure_dirs(*paths: Path) -> None:
    """
    Crea uno o más directorios (y sus padres) si no existen.

    Uso:
        ensure_dirs(RAW_DIR, PROCESSED_DIR, EXPORTS_DIR)
    """
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


# ── Timer ─────────────────────────────────────────────────────────────────────

class Timer:
    """
    Context manager para medir el tiempo de ejecución de un bloque.

    Uso:
        with Timer("Carga de datos") as t:
            df = pd.read_parquet(...)
        print(t.elapsed_str)   # "1m 23s"
    """

    def __init__(self, label: str = ""):
        self.label = label
        self.start = None
        self.elapsed = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.elapsed = time.perf_counter() - self.start

    @property
    def elapsed_str(self) -> str:
        m, s = divmod(int(self.elapsed), 60)
        return f"{m}m {s}s" if m else f"{s}s"


# ── Formato de números ────────────────────────────────────────────────────────

def pct(value: float, decimals: int = 2) -> str:
    """Formatea un decimal como porcentaje. Ej: 0.1532 → '15.32%'"""
    return f"{value * 100:.{decimals}f}%"


def fmt_number(value: float, decimals: int = 4) -> str:
    """Formatea un número con separadores de miles. Ej: 12345.6789 → '12,345.6789'"""
    return f"{value:,.{decimals}f}"
