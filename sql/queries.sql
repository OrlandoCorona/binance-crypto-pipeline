-- queries.sql
-- Queries analíticas de ejemplo.
-- Útiles para explorar los datos directamente en psql o DBeaver,
-- y como referencia para construir reportes adicionales.

-- ── 1. Resumen de datos disponibles ────────────────────────────────────────
SELECT
    symbol,
    interval,
    COUNT(*)                             AS total_bars,
    MIN(open_time_utc)::DATE             AS first_date,
    MAX(open_time_utc)::DATE             AS last_date,
    COUNT(*) FILTER (WHERE market_regime = 'Bull')    AS bull_bars,
    COUNT(*) FILTER (WHERE market_regime = 'Bear')    AS bear_bars,
    COUNT(*) FILTER (WHERE market_regime = 'Lateral') AS lateral_bars
FROM klines
GROUP BY symbol, interval
ORDER BY symbol, interval;


-- ── 2. Retorno promedio por día de la semana (todos los símbolos) ───────────
SELECT
    symbol,
    CASE next_bar_weekday
        WHEN 0 THEN '0-Monday'
        WHEN 1 THEN '1-Tuesday'
        WHEN 2 THEN '2-Wednesday'
        WHEN 3 THEN '3-Thursday'
        WHEN 4 THEN '4-Friday'
        WHEN 5 THEN '5-Saturday'
        WHEN 6 THEN '6-Sunday'
    END                                  AS weekday,
    COUNT(*)                             AS bars,
    ROUND(AVG(open_to_open_return)*100,4) AS avg_return_pct,
    ROUND(STDDEV(open_to_open_return)*100,4) AS stddev_pct
FROM klines
WHERE open_to_open_return IS NOT NULL
GROUP BY symbol, next_bar_weekday
ORDER BY symbol, next_bar_weekday;


-- ── 3. Comparativa de estrategias (última corrida) ─────────────────────────
SELECT
    symbol,
    strategy_name,
    total_return_pct,
    sharpe_ratio,
    max_drawdown_pct,
    exposure_pct,
    mc_percentile_pct
FROM vw_strategy_kpi
ORDER BY symbol, mc_percentile_pct DESC;


-- ── 4. Peores días de la semana por régimen (BTCUSDT) ──────────────────────
SELECT
    market_regime,
    weekday_name,
    avg_return_pct,
    bar_count
FROM vw_regime_stats
WHERE symbol = 'BTCUSDT'
ORDER BY market_regime, avg_return_pct ASC;


-- ── 5. Últimas validaciones de calidad de datos ────────────────────────────
SELECT
    symbol,
    check_name,
    severity,
    total_rows,
    total_failed,
    avg_pass_rate_pct,
    last_run
FROM vw_data_quality_summary
ORDER BY
    CASE severity WHEN 'FAIL' THEN 1 WHEN 'WARN' THEN 2 ELSE 3 END,
    symbol,
    check_name;


-- ── 6. Barras con outliers de retorno (para auditoría) ─────────────────────
SELECT
    symbol,
    open_time_utc,
    open,
    close,
    ROUND(open_to_open_return * 100, 4) AS return_pct,
    market_regime
FROM klines
WHERE ABS(open_to_open_return) > 0.10    -- retornos > 10% en 1h
ORDER BY ABS(open_to_open_return) DESC
LIMIT 20;
