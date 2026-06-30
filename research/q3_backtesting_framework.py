"""
Q3 — Framework de Backtesting Profesional para BTCUSDT / cripto spot.

Objetivo:
    Crear un framework reutilizable para evaluar estrategias futuras sin lookahead.

Principios centrales:
    1. Los indicadores solo pueden usar información histórica o presente.
    2. Las señales generadas en la vela t se ejecutan hasta la apertura de la vela t+1.
    3. El backtest descuenta comisiones y slippage.
    4. El capital se compone periodo a periodo.
    5. La validación se divide cronológicamente en in-sample y out-of-sample.

Uso rápido:
    python q3_backtesting_framework.py \
        --input ./crypto_datalake/research/quant_eda/BTCUSDT/1h/BTCUSDT_1h_quant_eda_enriched.parquet \
        --output-dir ./crypto_datalake/research/backtests/BTCUSDT/1h \
        --strategy sma_cross_demo \
        --symbol BTCUSDT \
        --interval 1h

Notas:
    - Este archivo incluye una estrategia DEMO de cruce de medias solo para probar el motor.
    - No representa recomendación financiera ni señal de compra/venta.
    - El objetivo es validar que el framework funcione y quede listo para conectar estrategias futuras.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# =============================================================================
# 1. CONFIGURACIÓN GENERAL
# =============================================================================


@dataclass(frozen=True)
class BacktestConfig:
    """
    Configuración central del backtest.

    frozen=True evita que la configuración cambie accidentalmente durante la ejecución.
    Esto ayuda a trazabilidad y reproducibilidad.
    """

    symbol: str
    interval: str
    initial_capital: float
    commission_rate: float
    slippage_rate: float
    annualization_factor: float
    train_ratio: float
    allow_fractional_position: bool


@dataclass(frozen=True)
class BacktestPaths:
    """
    Rutas de salida del experimento.

    Separar rutas en una clase evita escribir strings sueltos por todo el código.
    """

    output_dir: Path
    tables_dir: Path
    charts_dir: Path
    reports_dir: Path


# =============================================================================
# 2. INTERFAZ PARA ESTRATEGIAS
# =============================================================================


class Strategy(Protocol):
    """
    Contrato mínimo que debe cumplir cualquier estrategia futura.

    Toda estrategia debe implementar:
        - name: nombre único de la estrategia.
        - generate_indicators: agrega columnas de indicadores.
        - generate_signals: genera target_position.

    Regla anti-lookahead:
        generate_signals puede mirar la fila actual porque la señal se ejecutará
        hasta la siguiente apertura, no en la misma vela.
    """

    name: str

    def generate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Recibe datos OHLCV y devuelve datos con indicadores."""
        ...

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Devuelve un DataFrame con una columna target_position entre 0 y 1."""
        ...


class BuyAndHoldStrategy:
    """
    Benchmark buy & hold.

    Esta estrategia mantiene posición 1 durante todo el periodo.
    Sirve como referencia para saber si una estrategia realmente agrega valor.
    """

    name = "buy_and_hold"

    def generate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Buy & Hold no necesita indicadores."""
        result = data.copy()
        return result

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Crea target_position = 1.0 en todas las velas."""
        result = data.copy()
        result["target_position"] = 1.0
        return result


class SmaCrossDemoStrategy:
    """
    Estrategia DEMO para probar el framework.

    Regla de ejemplo:
        - target_position = 1 cuando SMA rápida > SMA lenta.
        - target_position = 0 cuando SMA rápida <= SMA lenta.

    Importante:
        Esta estrategia es solo una plantilla técnica.
        No debe interpretarse como recomendación ni como estrategia validada.
    """

    name = "sma_cross_demo"

    def __init__(self, fast_window: int = 20, slow_window: int = 100) -> None:
        """Guarda ventanas de medias móviles."""
        if fast_window <= 0:
            raise ValueError("fast_window debe ser mayor que cero.")
        if slow_window <= 0:
            raise ValueError("slow_window debe ser mayor que cero.")
        if fast_window >= slow_window:
            raise ValueError("fast_window debe ser menor que slow_window.")
        self.fast_window = fast_window
        self.slow_window = slow_window

    def generate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calcula medias móviles usando solo datos pasados y presentes."""
        result = data.copy()
        result["sma_fast"] = result["close"].rolling(window=self.fast_window, min_periods=self.fast_window).mean()
        result["sma_slow"] = result["close"].rolling(window=self.slow_window, min_periods=self.slow_window).mean()
        return result

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Genera posición objetivo; la ejecución real se hará en la siguiente apertura."""
        result = data.copy()
        result["target_position"] = np.where(result["sma_fast"] > result["sma_slow"], 1.0, 0.0)
        result.loc[result["sma_fast"].isna() | result["sma_slow"].isna(), "target_position"] = 0.0
        return result


class NoTradeStrategy:
    """
    Estrategia de control.

    Nunca toma posición. Sirve para probar costos, métricas y curvas planas.
    """

    name = "no_trade"

    def generate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """No calcula indicadores."""
        return data.copy()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Siempre mantiene target_position = 0."""
        result = data.copy()
        result["target_position"] = 0.0
        return result


# =============================================================================
# 3. CARGA Y VALIDACIÓN DE DATOS
# =============================================================================


def load_market_data(input_path: Path) -> pd.DataFrame:
    """
    Carga un dataset Parquet o CSV.

    Decisión técnica:
        Permitimos Parquet y CSV porque el Data Lake exporta ambos formatos.
        Parquet es preferible para research por tipos y eficiencia.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")

    suffix = input_path.suffix.lower()

    if suffix == ".parquet":
        data = pd.read_parquet(input_path)
    elif suffix == ".csv":
        data = pd.read_csv(input_path)
    else:
        raise ValueError("Formato no soportado. Usa .parquet o .csv")

    return data


def standardize_market_data(data: pd.DataFrame) -> pd.DataFrame:
    """
    Estandariza columnas mínimas para el motor de backtesting.

    Requisitos mínimos:
        open_time_utc, open, high, low, close, volume.

    El motor ejecuta al precio open de la siguiente vela,
    por eso la columna open es obligatoria.
    """
    result = data.copy()

    # Normalizamos nombres por si vienen con espacios, mayúsculas o diferencias menores.
    result.columns = [str(column).strip().lower() for column in result.columns]

    # Mapeos defensivos para datasets con nombres alternativos.
    rename_map = {
        "open_time": "open_time_utc",
        "timestamp": "open_time_utc",
        "date": "open_time_utc",
        "datetime": "open_time_utc",
    }
    result = result.rename(columns=rename_map)

    # Protección contra columnas duplicadas.
    #
    # Motivo técnico:
    # Algunos datasets enriquecidos pueden conservar una columna original
    # y otra derivada con el mismo nombre lógico, por ejemplo open_time_utc.
    # En pandas, cuando una columna está duplicada, result["open_time_utc"]
    # devuelve un DataFrame en vez de una Series. Después pd.to_datetime
    # intenta ensamblar ese DataFrame como fecha y lanza:
    # "ValueError: cannot assemble with duplicate keys".
    #
    # Decisión:
    # Si hay columnas duplicadas, se colapsan por nombre tomando el primer
    # valor no nulo de izquierda a derecha. Así mantenemos trazabilidad
    # sin eliminar silenciosamente información válida.
    if bool(result.columns.duplicated().any()):
        coalesced_columns = {}
        for column in result.columns.unique():
            same_name_block = result.loc[:, result.columns == column]
            if same_name_block.shape[1] == 1:
                coalesced_columns[column] = same_name_block.iloc[:, 0]
            else:
                coalesced_columns[column] = same_name_block.bfill(axis=1).iloc[:, 0]
        result = pd.DataFrame(coalesced_columns)

    required_columns = ["open_time_utc", "open", "high", "low", "close", "volume"]
    missing_columns = [column for column in required_columns if column not in result.columns]

    if missing_columns:
        raise ValueError(f"Faltan columnas requeridas: {missing_columns}")

    result["open_time_utc"] = pd.to_datetime(result["open_time_utc"], utc=True, errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result.dropna(subset=["open_time_utc", "open", "high", "low", "close", "volume"])
    result = result.sort_values("open_time_utc").drop_duplicates(subset=["open_time_utc"], keep="last")
    result = result.reset_index(drop=True)

    invalid_prices = (result[["open", "high", "low", "close"]] <= 0).any(axis="columns")
    invalid_volume = result["volume"] < 0

    if bool(invalid_prices.any()):
        raise ValueError("Existen precios menores o iguales a cero. Revisa la capa de calidad.")

    if bool(invalid_volume.any()):
        raise ValueError("Existen volúmenes negativos. Revisa la capa de calidad.")

    return result


# =============================================================================
# 4. VALIDACIÓN IN-SAMPLE / OUT-OF-SAMPLE
# =============================================================================


def split_in_sample_out_sample(data: pd.DataFrame, train_ratio: float) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Divide el dataset cronológicamente.

    Decisión técnica:
        Nunca se usa split aleatorio en series temporales financieras.
        El pasado queda in-sample y el futuro queda out-of-sample.
    """
    if not 0.1 <= train_ratio <= 0.9:
        raise ValueError("train_ratio debe estar entre 0.1 y 0.9")

    split_index = int(len(data) * train_ratio)

    in_sample = data.iloc[:split_index].copy()
    out_sample = data.iloc[split_index:].copy()

    return in_sample, out_sample


# =============================================================================
# 5. EJECUCIÓN DEL BACKTEST SIN LOOKAHEAD
# =============================================================================


def prepare_strategy_frame(data: pd.DataFrame, strategy: Strategy, config: BacktestConfig) -> pd.DataFrame:
    """
    Calcula indicadores y señales de una estrategia.

    Anti-lookahead:
        La estrategia genera target_position en t usando información hasta t.
        El motor la desplaza una vela para ejecutarla en t+1.
    """
    with_indicators = strategy.generate_indicators(data)
    with_signals = strategy.generate_signals(with_indicators)

    if "target_position" not in with_signals.columns:
        raise ValueError("La estrategia debe generar la columna target_position.")

    result = with_signals.copy()
    result["target_position"] = pd.to_numeric(result["target_position"], errors="coerce").fillna(0.0)

    if config.allow_fractional_position:
        result["target_position"] = result["target_position"].clip(lower=0.0, upper=1.0)
    else:
        allowed_values = set(result["target_position"].dropna().unique().tolist())
        if not allowed_values.issubset({0.0, 1.0}):
            raise ValueError("target_position debe ser 0 o 1 si allow_fractional_position=False.")

    return result


def run_execution_engine(prepared_data: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """
    Ejecuta el backtest vectorizado para spot long-only.

    Modelo de ejecución:
        1. La señal en la vela t se convierte en posición ejecutada en la apertura t+1.
        2. La rentabilidad se mide de open_t a open_t+1.
        3. Los costos se aplican cuando cambia la posición.

    Esto evita lookahead porque no compra/vende en una vela usando el cierre de esa misma vela.
    """
    result = prepared_data.copy()

    result["next_open"] = result["open"].shift(-1)
    result["market_return_open_to_open"] = (result["next_open"] / result["open"]) - 1.0

    # La posición ejecutada en la apertura actual viene de la señal de la vela anterior.
    result["position"] = result["target_position"].shift(1).fillna(0.0)

    # Cambio de posición. En spot long-only, pasar de 0 a 1 o de 1 a 0 genera costos.
    result["position_change"] = result["position"].diff().abs().fillna(result["position"].abs())

    # Costo proporcional: comisión + slippage por el notional cambiado.
    result["trading_cost"] = result["position_change"] * (config.commission_rate + config.slippage_rate)

    # Retorno neto del periodo con capital compuesto.
    result["strategy_return"] = (result["position"] * result["market_return_open_to_open"]) - result["trading_cost"]

    # La última fila no tiene next_open, por lo tanto no puede cerrar periodo de retorno.
    result = result.dropna(subset=["market_return_open_to_open"]).copy()

    # Protección: en spot sin apalancamiento, el retorno neto no debería ser menor a -100%.
    if bool((result["strategy_return"] <= -1.0).any()):
        raise ValueError("Se detectó retorno <= -100%. Revisa costos, posiciones o datos.")

    result["equity"] = config.initial_capital * (1.0 + result["strategy_return"]).cumprod()
    result["buy_hold_return"] = result["market_return_open_to_open"]
    result["buy_hold_equity"] = config.initial_capital * (1.0 + result["buy_hold_return"]).cumprod()

    result["equity_peak"] = result["equity"].cummax()
    result["drawdown"] = (result["equity"] / result["equity_peak"]) - 1.0

    result["buy_hold_peak"] = result["buy_hold_equity"].cummax()
    result["buy_hold_drawdown"] = (result["buy_hold_equity"] / result["buy_hold_peak"]) - 1.0

    return result


# =============================================================================
# 6. MÉTRICAS PROFESIONALES
# =============================================================================


def safe_divide(numerator: float, denominator: float) -> float:
    """Divide evitando errores por cero."""
    if denominator == 0 or math.isnan(denominator):
        return float("nan")
    return numerator / denominator


def compute_cagr(equity: pd.Series, annualization_factor: float) -> float:
    """Calcula CAGR usando número de periodos y factor de anualización."""
    clean = equity.dropna()
    if len(clean) < 2:
        return float("nan")

    start_value = float(clean.iloc[0])
    end_value = float(clean.iloc[-1])

    if start_value <= 0 or end_value <= 0:
        return float("nan")

    periods = len(clean)
    years = periods / annualization_factor

    if years <= 0:
        return float("nan")

    return (end_value / start_value) ** (1.0 / years) - 1.0


def compute_max_drawdown(equity: pd.Series) -> float:
    """Calcula máximo drawdown de una curva de capital."""
    clean = equity.dropna()
    if clean.empty:
        return float("nan")
    peak = clean.cummax()
    drawdown = (clean / peak) - 1.0
    return float(drawdown.min())


def extract_trades(backtest: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """
    Extrae operaciones long-only a partir de cambios de posición.

    Versión optimizada:
        En lugar de recorrer el DataFrame con iterrows(), usa arreglos NumPy.
        Esto evita lentitud innecesaria cuando el dataset crece.

    Regla:
        - Entrada: posición actual > 0 y posición previa == 0.
        - Salida: posición actual == 0 y posición previa > 0.
        - Si queda una operación abierta al final, se cierra al último open disponible
          solo para poder medir la operación en el reporte.

    Limitación explícita:
        Este extractor está pensado para spot long-only con posición binaria o fraccional.
        Si después agregas cortos, derivados, piramidación o fills parciales, conviene
        crear un módulo de ejecución/trades más detallado.
    """
    required = ["open_time_utc", "open", "position"]
    missing = [column for column in required if column not in backtest.columns]
    if missing:
        raise ValueError(f"No se pueden extraer trades. Faltan columnas: {missing}")

    if backtest.empty:
        return pd.DataFrame(
            columns=[
                "entry_time_utc",
                "exit_time_utc",
                "entry_price",
                "exit_price",
                "position_size",
                "gross_return",
                "net_return",
                "bars_held",
                "is_win",
            ]
        )

    times = pd.to_datetime(backtest["open_time_utc"], utc=True, errors="coerce").reset_index(drop=True)
    prices = pd.to_numeric(backtest["open"], errors="coerce").reset_index(drop=True)
    positions = pd.to_numeric(backtest["position"], errors="coerce").fillna(0.0).reset_index(drop=True)

    previous_positions = positions.shift(1).fillna(0.0)

    entry_indices = np.flatnonzero(((positions > 0.0) & (previous_positions <= 0.0)).to_numpy())
    exit_indices = np.flatnonzero(((positions <= 0.0) & (previous_positions > 0.0)).to_numpy())

    rows: List[Dict[str, object]] = []
    exit_pointer = 0
    round_trip_cost = 2.0 * (config.commission_rate + config.slippage_rate)

    for entry_index in entry_indices:
        while exit_pointer < len(exit_indices) and exit_indices[exit_pointer] <= entry_index:
            exit_pointer += 1

        if exit_pointer < len(exit_indices):
            exit_index = int(exit_indices[exit_pointer])
            exit_pointer += 1
        else:
            exit_index = len(backtest) - 1

        entry_time = times.iloc[entry_index]
        exit_time = times.iloc[exit_index]
        entry_price = float(prices.iloc[entry_index])
        exit_price = float(prices.iloc[exit_index])
        entry_position = float(positions.iloc[entry_index])

        if pd.isna(entry_time) or pd.isna(exit_time) or not np.isfinite(entry_price) or not np.isfinite(exit_price):
            continue

        if entry_price <= 0 or exit_price <= 0:
            continue

        gross_return = (exit_price / entry_price) - 1.0
        net_return = gross_return - round_trip_cost
        bars_held = int((pd.Timestamp(exit_time) - pd.Timestamp(entry_time)).total_seconds() / 3600)

        rows.append(
            {
                "entry_time_utc": entry_time,
                "exit_time_utc": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "position_size": entry_position,
                "gross_return": gross_return,
                "net_return": net_return,
                "bars_held": bars_held,
                "is_win": bool(net_return > 0),
            }
        )

    return pd.DataFrame(rows)


def compute_trade_metrics(trades: pd.DataFrame) -> Dict[str, float]:
    """Calcula métricas basadas en operaciones cerradas."""
    if trades.empty:
        return {
            "trade_count": 0.0,
            "win_rate": float("nan"),
            "profit_factor": float("nan"),
            "expectancy": float("nan"),
            "average_trade_return": float("nan"),
            "average_bars_held": float("nan"),
        }

    returns = trades["net_return"].astype(float)
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())

    profit_factor = safe_divide(gross_profit, abs(gross_loss))
    win_rate = float((returns > 0).mean())
    expectancy = float(returns.mean())

    return {
        "trade_count": float(len(trades)),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "average_trade_return": float(returns.mean()),
        "average_bars_held": float(trades["bars_held"].mean()),
    }


def compute_performance_metrics(
    backtest: pd.DataFrame,
    trades: pd.DataFrame,
    config: BacktestConfig,
    equity_column: str = "equity",
    return_column: str = "strategy_return",
) -> Dict[str, float]:
    """Calcula métricas profesionales de performance."""
    returns = backtest[return_column].dropna().astype(float)
    equity = backtest[equity_column].dropna().astype(float)

    if equity.empty or returns.empty:
        return {}

    total_return = (float(equity.iloc[-1]) / config.initial_capital) - 1.0
    cagr = compute_cagr(equity, config.annualization_factor)
    max_drawdown = compute_max_drawdown(equity)

    mean_return = float(returns.mean())
    std_return = float(returns.std(ddof=1))
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else float("nan")

    sharpe = safe_divide(mean_return, std_return) * math.sqrt(config.annualization_factor)
    sortino = safe_divide(mean_return, downside_std) * math.sqrt(config.annualization_factor)
    calmar = safe_divide(cagr, abs(max_drawdown))

    trade_metrics = compute_trade_metrics(trades)

    metrics = {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_drawdown,
        "final_equity": float(equity.iloc[-1]),
        "mean_period_return": mean_return,
        "std_period_return": std_return,
    }
    metrics.update(trade_metrics)
    return metrics


def compute_buy_hold_metrics(backtest: pd.DataFrame, config: BacktestConfig) -> Dict[str, float]:
    """Calcula métricas del benchmark Buy & Hold."""
    pseudo_trades = pd.DataFrame(
        [
            {
                "net_return": (float(backtest["buy_hold_equity"].iloc[-1]) / config.initial_capital) - 1.0,
                "bars_held": len(backtest),
                "is_win": bool(float(backtest["buy_hold_equity"].iloc[-1]) > config.initial_capital),
            }
        ]
    )

    metrics = compute_performance_metrics(
        backtest=backtest,
        trades=pseudo_trades,
        config=config,
        equity_column="buy_hold_equity",
        return_column="buy_hold_return",
    )

    return {f"buy_hold_{key}": value for key, value in metrics.items()}


# =============================================================================
# 7. REPORTES Y GRÁFICOS
# =============================================================================


def create_output_paths(output_dir: Path) -> BacktestPaths:
    """Crea carpetas de salida."""
    tables_dir = output_dir / "tables"
    charts_dir = output_dir / "charts"
    reports_dir = output_dir / "reports"

    tables_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    return BacktestPaths(
        output_dir=output_dir,
        tables_dir=tables_dir,
        charts_dir=charts_dir,
        reports_dir=reports_dir,
    )


def plot_equity_curve(backtest: pd.DataFrame, chart_path: Path, title: str) -> None:
    """Grafica curva de capital de estrategia contra Buy & Hold."""
    plt.figure(figsize=(12, 6))
    plt.plot(backtest["open_time_utc"], backtest["equity"], label="Strategy")
    plt.plot(backtest["open_time_utc"], backtest["buy_hold_equity"], label="Buy & Hold")
    plt.title(title)
    plt.xlabel("Fecha UTC")
    plt.ylabel("Capital")
    plt.legend()
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150)
    plt.close()


def plot_drawdown(backtest: pd.DataFrame, chart_path: Path, title: str) -> None:
    """Grafica drawdown de estrategia contra Buy & Hold."""
    plt.figure(figsize=(12, 6))
    plt.plot(backtest["open_time_utc"], backtest["drawdown"], label="Strategy Drawdown")
    plt.plot(backtest["open_time_utc"], backtest["buy_hold_drawdown"], label="Buy & Hold Drawdown")
    plt.title(title)
    plt.xlabel("Fecha UTC")
    plt.ylabel("Drawdown")
    plt.legend()
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150)
    plt.close()



def dataframe_to_markdown_table(data: pd.DataFrame) -> str:
    """
    Convierte un DataFrame pequeño a tabla Markdown sin depender de tabulate.

    Motivo técnico:
        pandas.DataFrame.to_markdown() requiere la dependencia opcional tabulate.
        Para que el framework sea más fácil de ejecutar en Windows, evitamos esa
        dependencia adicional.
    """
    if data.empty:
        return "_Sin datos._"

    display = data.copy()
    for column in display.columns:
        display[column] = display[column].map(
            lambda value: f"{value:.6f}" if isinstance(value, float) and np.isfinite(value) else str(value)
        )

    headers = [str(column) for column in display.columns]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---" for _ in headers]) + "|")

    for _, row in display.iterrows():
        lines.append("| " + " | ".join(str(row[column]) for column in display.columns) + " |")

    return "\n".join(lines)


def write_markdown_report(
    report_path: Path,
    strategy_name: str,
    config: BacktestConfig,
    metrics: Dict[str, float],
    split_metrics: pd.DataFrame,
) -> None:
    """Genera un reporte Markdown automático."""
    lines: List[str] = []

    lines.append(f"# Backtest profesional — {strategy_name}\n")
    lines.append("## Alcance\n")
    lines.append("Este reporte evalúa una estrategia dentro de un framework spot long-only con cero lookahead operativo.\n")
    lines.append("La señal calculada en la vela `t` se ejecuta hasta la apertura de la vela `t+1`.\n")

    lines.append("## Configuración\n")
    lines.append(f"- Símbolo: `{config.symbol}`\n")
    lines.append(f"- Intervalo: `{config.interval}`\n")
    lines.append(f"- Capital inicial: `{config.initial_capital}`\n")
    lines.append(f"- Comisión por operación: `{config.commission_rate}`\n")
    lines.append(f"- Slippage estimado: `{config.slippage_rate}`\n")
    lines.append(f"- Factor de anualización: `{config.annualization_factor}`\n")
    lines.append(f"- Split in-sample: `{config.train_ratio:.2%}`\n")

    lines.append("## Métricas globales\n")
    lines.append("| Métrica | Valor |\n")
    lines.append("|---|---:|\n")
    for key, value in metrics.items():
        if isinstance(value, float):
            lines.append(f"| {key} | {value:.6f} |\n")
        else:
            lines.append(f"| {key} | {value} |\n")

    lines.append("\n## Métricas In-Sample / Out-of-Sample\n")
    lines.append(dataframe_to_markdown_table(split_metrics))
    lines.append("\n\n## Interpretación técnica\n")
    lines.append("- El resultado global debe compararse contra Buy & Hold, no solo contra cero.\n")
    lines.append("- Si una estrategia gana in-sample pero falla out-of-sample, puede estar sobreajustada.\n")
    lines.append("- Si el Sharpe es positivo pero el Max Drawdown es alto, el riesgo puede no compensar.\n")
    lines.append("- Si el Profit Factor depende de muy pocas operaciones, la evidencia es débil.\n")
    lines.append("- Si el desempeño se concentra en un periodo específico, debe validarse por régimen y por año.\n")

    lines.append("\n## Sesgos que este framework busca reducir\n")
    lines.append("1. Lookahead bias: señales se desplazan una vela antes de ejecutarse.\n")
    lines.append("2. Costos ignorados: se descuentan comisión y slippage.\n")
    lines.append("3. Validación temporal incorrecta: el split es cronológico, no aleatorio.\n")
    lines.append("4. Benchmark ausente: se compara contra Buy & Hold.\n")
    lines.append("5. Métricas incompletas: se reportan retorno, drawdown, Sharpe, Sortino, Calmar, Profit Factor, Expectancy y Win Rate.\n")

    report_path.write_text("".join(lines), encoding="utf-8")


# =============================================================================
# 8. ORQUESTACIÓN
# =============================================================================


def evaluate_strategy_on_dataset(
    data: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """Ejecuta estrategia, motor, trades y métricas sobre un dataset."""
    prepared = prepare_strategy_frame(data, strategy, config)
    backtest = run_execution_engine(prepared, config)
    trades = extract_trades(backtest, config)
    metrics = compute_performance_metrics(backtest, trades, config)
    metrics.update(compute_buy_hold_metrics(backtest, config))
    return backtest, trades, metrics


def build_strategy(strategy_name: str, fast_window: int, slow_window: int) -> Strategy:
    """Construye una estrategia a partir de su nombre."""
    normalized = strategy_name.strip().lower()

    if normalized == "buy_and_hold":
        return BuyAndHoldStrategy()

    if normalized == "sma_cross_demo":
        return SmaCrossDemoStrategy(fast_window=fast_window, slow_window=slow_window)

    if normalized == "no_trade":
        return NoTradeStrategy()

    raise ValueError(f"Estrategia no reconocida: {strategy_name}")


def run_full_backtest(args: argparse.Namespace) -> None:
    """Ejecuta el flujo completo del framework."""
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    paths = create_output_paths(output_dir)

    config = BacktestConfig(
        symbol=args.symbol,
        interval=args.interval,
        initial_capital=args.initial_capital,
        commission_rate=args.commission_rate,
        slippage_rate=args.slippage_rate,
        annualization_factor=args.annualization_factor,
        train_ratio=args.train_ratio,
        allow_fractional_position=args.allow_fractional_position,
    )

    strategy = build_strategy(args.strategy, args.fast_window, args.slow_window)

    raw_data = load_market_data(input_path)
    data = standardize_market_data(raw_data)

    in_sample, out_sample = split_in_sample_out_sample(data, config.train_ratio)

    full_backtest, full_trades, full_metrics = evaluate_strategy_on_dataset(data, strategy, config)
    in_backtest, in_trades, in_metrics = evaluate_strategy_on_dataset(in_sample, strategy, config)
    out_backtest, out_trades, out_metrics = evaluate_strategy_on_dataset(out_sample, strategy, config)

    split_metrics = pd.DataFrame(
        [
            {"sample": "full", **full_metrics},
            {"sample": "in_sample", **in_metrics},
            {"sample": "out_of_sample", **out_metrics},
        ]
    )

    full_backtest.to_csv(paths.tables_dir / f"{strategy.name}_backtest_timeseries.csv", index=False)
    full_trades.to_csv(paths.tables_dir / f"{strategy.name}_trades.csv", index=False)
    split_metrics.to_csv(paths.tables_dir / f"{strategy.name}_metrics_by_sample.csv", index=False)

    metadata = {
        "symbol": config.symbol,
        "interval": config.interval,
        "strategy": strategy.name,
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "rows_input": int(len(data)),
        "rows_full_backtest": int(len(full_backtest)),
        "rows_in_sample": int(len(in_sample)),
        "rows_out_sample": int(len(out_sample)),
        "initial_capital": config.initial_capital,
        "commission_rate": config.commission_rate,
        "slippage_rate": config.slippage_rate,
        "annualization_factor": config.annualization_factor,
        "train_ratio": config.train_ratio,
        "execution_model": "signal at candle t, execution at next candle open t+1, open-to-open returns",
        "spot_only": True,
        "long_only": True,
        "lookahead_control": "target_position is shifted by 1 bar before execution",
    }

    (paths.reports_dir / f"{strategy.name}_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    plot_equity_curve(
        full_backtest,
        paths.charts_dir / f"{strategy.name}_equity_curve.png",
        title=f"Equity Curve — {strategy.name} vs Buy & Hold",
    )

    plot_drawdown(
        full_backtest,
        paths.charts_dir / f"{strategy.name}_drawdown.png",
        title=f"Drawdown — {strategy.name} vs Buy & Hold",
    )

    write_markdown_report(
        report_path=paths.reports_dir / f"{strategy.name}_backtest_report.md",
        strategy_name=strategy.name,
        config=config,
        metrics=full_metrics,
        split_metrics=split_metrics,
    )

    print("\nBacktest terminado.")
    print(f"Estrategia: {strategy.name}")
    print(f"Tablas:     {paths.tables_dir}")
    print(f"Gráficos:   {paths.charts_dir}")
    print(f"Reportes:   {paths.reports_dir}")


# =============================================================================
# 9. CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Define argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(description="Q3 — Framework profesional de backtesting spot long-only.")

    parser.add_argument("--input", required=True, help="Ruta del dataset Parquet o CSV.")
    parser.add_argument("--output-dir", required=True, help="Carpeta de salida del backtest.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo analizado.")
    parser.add_argument("--interval", default="1h", help="Temporalidad analizada.")
    parser.add_argument("--strategy", default="sma_cross_demo", choices=["sma_cross_demo", "buy_and_hold", "no_trade"], help="Estrategia a evaluar.")

    parser.add_argument("--initial-capital", type=float, default=10000.0, help="Capital inicial.")
    parser.add_argument("--commission-rate", type=float, default=0.001, help="Comisión por notional operado. 0.001 = 0.10%%.")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="Slippage estimado por notional operado. 0.0005 = 0.05%%.")
    parser.add_argument("--annualization-factor", type=float, default=8760.0, help="Periodos por año para 1h en cripto.")
    parser.add_argument("--train-ratio", type=float, default=0.70, help="Porcentaje cronológico para in-sample.")
    parser.add_argument("--allow-fractional-position", action="store_true", help="Permite target_position fraccional entre 0 y 1.")

    parser.add_argument("--fast-window", type=int, default=20, help="Ventana rápida para estrategia demo SMA.")
    parser.add_argument("--slow-window", type=int, default=100, help="Ventana lenta para estrategia demo SMA.")

    return parser.parse_args()


def main() -> None:
    """Punto de entrada del script."""
    args = parse_args()
    run_full_backtest(args)


if __name__ == "__main__":
    main()
