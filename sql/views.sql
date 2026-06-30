-- views.sql
-- Vistas analíticas listas para conectar con Power BI.
-- Ejecutar después de create_tables.sql.
--
-- Uso:
--   psql -U postgres -d crypto_pipeline -f sql/views.sql

-- ── 1. vw_strategy_kpi ─────────────────────────────────────────────────────
-- Vista principal para el dashboard: un renglón por estrategia + activo.
-- Power BI la usa para las tarjetas de KPIs y la tabla comparativa.

CREATE OR REPLACE VIEW vw_strategy_kpi AS
SELECT
    symbol,
    strategy_name,
    run_date,
    ROUND(total_return   * 100, 2) AS total_return_pct,
    ROUND(bh_return      * 100, 2) AS bh_return_pct,
    ROUND(excess_return  * 100, 2) AS excess_return_pct,
    ROUND(sharpe_ratio,   2)       AS sharpe_ratio,
    ROUND(max_drawdown   * 100, 2) AS max_drawdown_pct,
    ROUND(cagr           * 100, 2) AS cagr_pct,
    win_rate,
    profit_factor,
    total_trades,
    ROUND(pos_pct        * 100, 2) AS exposure_pct,
    ROUND(beat_pct       * 100, 2) AS beat_bh_months_pct,
    ROUND(mc_percentile  * 100, 2) AS mc_percentile_pct
FROM backtest_results
-- Solo la corrida más reciente por estrategia+símbolo
WHERE run_date = (
    SELECT MAX(run_date)
    FROM backtest_results AS br2
    WHERE br2.symbol = backtest_results.symbol
      AND br2.strategy_name = backtest_results.strategy_name
);


-- ── 2. vw_daily_returns ────────────────────────────────────────────────────
-- Retornos diarios agregados desde las barras horarias.
-- Power BI la usa para la curva de equity.

CREATE OR REPLACE VIEW vw_daily_returns AS
SELECT
    symbol,
    interval,
    DATE(open_time_utc)                      AS trade_date,
    SUM(open_to_open_return)                 AS daily_return,
    AVG(rolling_vol_24h)                     AS avg_volatility,
    MAX(high)                                AS day_high,
    MIN(low)                                 AS day_low,
    MAX(market_regime)                       AS market_regime,
    COUNT(*)                                 AS bars_count
FROM klines
WHERE open_to_open_return IS NOT NULL
GROUP BY symbol, interval, DATE(open_time_utc)
ORDER BY symbol, interval, trade_date;


-- ── 3. vw_regime_stats ─────────────────────────────────────────────────────
-- Estadísticas de retornos por día de la semana y régimen de mercado.
-- Útil para el heatmap de régimen en Power BI.

CREATE OR REPLACE VIEW vw_regime_stats AS
SELECT
    symbol,
    market_regime,
    next_bar_weekday,
    CASE next_bar_weekday
        WHEN 0 THEN 'Monday'
        WHEN 1 THEN 'Tuesday'
        WHEN 2 THEN 'Wednesday'
        WHEN 3 THEN 'Thursday'
        WHEN 4 THEN 'Friday'
        WHEN 5 THEN 'Saturday'
        WHEN 6 THEN 'Sunday'
    END                                      AS weekday_name,
    COUNT(*)                                 AS bar_count,
    ROUND(AVG(open_to_open_return) * 100, 4) AS avg_return_pct,
    ROUND(STDDEV(open_to_open_return) * 100, 4) AS stddev_return_pct,
    ROUND(MIN(open_to_open_return) * 100, 4) AS min_return_pct,
    ROUND(MAX(open_to_open_return) * 100, 4) AS max_return_pct
FROM klines
WHERE open_to_open_return IS NOT NULL
  AND market_regime IS NOT NULL
  AND next_bar_weekday IS NOT NULL
GROUP BY symbol, market_regime, next_bar_weekday
ORDER BY symbol, market_regime, next_bar_weekday;


-- ── 4. vw_data_quality_summary ─────────────────────────────────────────────
-- Resumen de calidad de datos para la página de monitoreo en Power BI.

CREATE OR REPLACE VIEW vw_data_quality_summary AS
SELECT
    symbol,
    check_name,
    severity,
    MAX(run_at)                              AS last_run,
    SUM(total_rows)                          AS total_rows,
    SUM(failed_rows)                         AS total_failed,
    ROUND(AVG(pass_rate) * 100, 2)           AS avg_pass_rate_pct
FROM data_quality
GROUP BY symbol, check_name, severity
ORDER BY symbol, check_name;
