# Power BI Dashboard — Setup Guide

## Opción A — Conectar con CSV (recomendado para empezar)

Esta opción funciona sin PostgreSQL. Los datos están en `data/exports/`.

### Paso 1: Actualizar los datos de exportación

```bash
python src/export_data.py
```

Esto genera o actualiza los 4 archivos CSV en `data/exports/`.

### Paso 2: Abrir Power BI Desktop

1. Abrir **Power BI Desktop**
2. `Obtener datos` → `Texto/CSV`
3. Navegar a la carpeta `data/exports/` del proyecto

### Paso 3: Cargar los 4 archivos

Importar en este orden:

| Archivo | Descripción | Filas aprox. |
|---|---|---|
| `strategy_kpi.csv` | KPIs por estrategia y activo | 8 |
| `weekday_returns.csv` | Retorno promedio por día | 7 |
| `regime_heatmap.csv` | Retornos por día × régimen | 21 |
| `equity_curves.csv` | Curvas de equity en el tiempo | 1,000+ |

### Paso 4: Verificar tipos de dato

En Power Query, verificar que:
- `total_return_pct`, `sharpe_ratio`, etc. → **Número decimal**
- `symbol`, `strategy`, `weekday_name` → **Texto**
- `date` en equity_curves → **Fecha/Hora**

---

## Opción B — Conectar con PostgreSQL (versión final)

### Prerequisitos

1. PostgreSQL 15 instalado y corriendo en localhost:5432
2. Base de datos creada y datos cargados:

```bash
# 1. Crear la base de datos
psql -U postgres -c "CREATE DATABASE crypto_pipeline;"

# 2. Crear tablas y vistas
psql -U postgres -d crypto_pipeline -f sql/create_tables.sql
psql -U postgres -d crypto_pipeline -f sql/views.sql

# 3. Cargar datos
python main.py

# Verificar que los datos cargaron
psql -U postgres -d crypto_pipeline -c "SELECT symbol, COUNT(*) FROM klines GROUP BY symbol;"
```

### Conectar Power BI a PostgreSQL

1. En Power BI Desktop: `Obtener datos` → `Base de datos` → **PostgreSQL**
2. Servidor: `localhost`
3. Base de datos: `crypto_pipeline`
4. Modo de conectividad: **Importar** (para dashboard estático) o **DirectQuery** (datos en vivo)
5. Ingresar usuario y contraseña de PostgreSQL

### Tablas y vistas a importar

Importar estas vistas (ya están optimizadas para Power BI):

| Vista | Uso en dashboard |
|---|---|
| `vw_strategy_kpi` | Tarjetas KPI, tabla comparativa |
| `vw_daily_returns` | Curva de equity, línea de tiempo |
| `vw_regime_stats` | Heatmap de régimen |
| `vw_data_quality_summary` | Página de monitoreo de datos |

---

## Estructura del Dashboard (4 páginas)

### Página 1 — Resumen Ejecutivo

```
┌─────────────────────────────────────────────────────────────────┐
│  KPI Cards: Mejor Sharpe | Mejor Retorno | MC Percentile       │
├──────────────────────┬──────────────────────────────────────────┤
│  Tabla comparativa   │  Gráfico de barras:                      │
│  symbol | strategy   │  Retorno total % por activo              │
│  return | sharpe     │  (agrupado por estrategia)               │
│  MC%    | beat_bh%   │                                          │
└──────────────────────┴──────────────────────────────────────────┘
```

**Medidas DAX útiles:**
```dax
Mejor Sharpe = MAX(strategy_kpi[sharpe_ratio])

Beat BH Promedio = AVERAGE(strategy_kpi[beat_bh_pct])

MC Percentile H4 =
    CALCULATE(
        MAX(strategy_kpi[mc_percentile]),
        strategy_kpi[strategy] = "H4_AVOID_THURSDAY"
    )
```

---

### Página 2 — Análisis de Calendario

```
┌─────────────────────────────────────────────────────────────────┐
│  Gráfico de barras: Retorno promedio por día de la semana       │
│  (verde = positivo, rojo = negativo)                            │
│  Filtro: symbol                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Matriz: Heatmap régimen × día de la semana                     │
│  Filas: Bull / Bear / Lateral                                   │
│  Columnas: Mon / Tue / Wed / Thu / Fri / Sat / Sun              │
│  Valores: avg_return_pct (formato condicional por color)        │
└─────────────────────────────────────────────────────────────────┘
```

**Formato condicional para el heatmap:**
- Escala de color: Rojo (-) → Blanco (0) → Verde (+)
- Mínimo: -0.15% | Máximo: +0.15%

---

### Página 3 — Curvas de Equity

```
┌─────────────────────────────────────────────────────────────────┐
│  Gráfico de líneas: equity vs tiempo                            │
│  Eje X: date | Eje Y: equity                                    │
│  Leyenda: strategy                                              │
│  Filtros: symbol, strategy                                      │
├─────────────────────────────────────────────────────────────────┤
│  KPI Cards: Max Drawdown | CAGR | Sharpe Ratio                  │
└─────────────────────────────────────────────────────────────────┘
```

---

### Página 4 — Calidad de Datos

```
┌─────────────────────────────────────────────────────────────────┐
│  Tabla: check_name | severity | total_rows | failed | pass%     │
│  Colores: PASS=verde, WARN=amarillo, FAIL=rojo                  │
├─────────────────────────────────────────────────────────────────┤
│  KPI: Total Rows Processed | Last Run Date | Pass Rate %        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tips para la entrevista

**Pregunta:** "¿Por qué usaste Power BI y no Tableau o Looker?"
**Respuesta:** "En el stack de Microsoft (PostgreSQL en Azure, Python), Power BI es la opción más natural. Tiene conector nativo a PostgreSQL, DirectQuery para datos en vivo, y las organizaciones con Office 365 ya tienen licencias incluidas."

**Pregunta:** "¿Qué es DirectQuery vs Import Mode?"
**Respuesta:** "Import Mode carga una copia de los datos en memoria de Power BI — más rápido pero datos estáticos. DirectQuery ejecuta queries en tiempo real contra la base de datos — datos siempre frescos pero más lento. Para este proyecto uso Import Mode porque los datos se actualizan una vez al mes."

**Pregunta:** "¿Cómo actualizarías el dashboard automáticamente?"
**Respuesta:** "Con un script programado que corra `python main.py` mensualmente cuando Binance publica nuevos datos históricos, y luego un refresh programado en Power BI Service."
