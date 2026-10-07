-- 05_reports.sql
-- Reporting queries. run_reconciliation.py reads each block by its "-- name:" tag, so the
-- dashboard and Excel report are fed by SQL rather than by re-computation in Python.

-- name: funnel
-- Rows per system, and how many cleared automatically vs. need a human.
SELECT t.src,
       t.total_rows,
       COALESCE(c.clean_rows, 0)                  AS clean_rows,
       t.total_rows - COALESCE(c.clean_rows, 0)   AS exception_rows
FROM (SELECT src, COUNT(*) AS total_rows FROM src_records GROUP BY src) t
LEFT JOIN (
    SELECT 'BANK' AS src, COUNT(bank_id) AS clean_rows FROM unit_class WHERE unit_status = 'CLEAN'
    UNION ALL
    SELECT 'SL',          COUNT(sl_id)               FROM unit_class WHERE unit_status = 'CLEAN'
    UNION ALL
    SELECT 'GL',          COUNT(gl_id)               FROM unit_class WHERE unit_status = 'CLEAN'
) c ON c.src = t.src
ORDER BY CASE t.src WHEN 'BANK' THEN 1 WHEN 'SL' THEN 2 ELSE 3 END;

-- name: by_type
SELECT exception_type,
       COUNT(*)                                        AS n,
       ROUND(SUM(amount_at_risk), 2)                   AS exposure,
       SUM(CASE WHEN severity = 'HIGH' THEN 1 ELSE 0 END) AS n_high
FROM exceptions
GROUP BY exception_type
ORDER BY n DESC;

-- name: by_severity
SELECT severity, COUNT(*) AS n, ROUND(SUM(amount_at_risk), 2) AS exposure
FROM exceptions
GROUP BY severity
ORDER BY CASE severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END;

-- name: aging
-- Age = days from the earliest date on the item to period end.
SELECT CASE WHEN age_days <= 30 THEN '0-30 days'
            WHEN age_days <= 60 THEN '31-60 days'
            ELSE '61-90+ days' END AS bucket,
       COUNT(*)                      AS n,
       ROUND(SUM(amount_at_risk), 2) AS exposure
FROM exceptions
GROUP BY bucket
ORDER BY MIN(age_days);

-- name: kpis
SELECT (SELECT COUNT(*) FROM unit_class WHERE unit_status = 'CLEAN')       AS clean_units,
       (SELECT COUNT(*) FROM unit_class)                                   AS total_units,
       (SELECT COUNT(*) FROM exceptions)                                   AS exceptions,
       (SELECT ROUND(SUM(amount_at_risk), 2) FROM exceptions)              AS exposure,
       (SELECT COUNT(*) FROM src_records)                                  AS total_rows,
       (SELECT COUNT(*) FROM dup_flags WHERE occurrence > 1)               AS duplicate_rows;
