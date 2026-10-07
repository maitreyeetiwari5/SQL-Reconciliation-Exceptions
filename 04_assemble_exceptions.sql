-- 04_assemble_exceptions.sql
-- Turn the pairwise links into one row per reconciliation "unit" (a receipt as seen by
-- bank / AR subledger / GL), classify each unit, and write the exceptions table.
--
-- A unit is CLEAN only when all three systems agree on amount (within tolerance) and dates sit
-- within tol_days of each other. Everything else becomes an exception with an objective note.
-- The notes describe WHAT does not tie; they never assert WHY, that is left to the analyst.

-- ---------------------------------------------------------------- 1. units
DROP TABLE IF EXISTS units;
CREATE TABLE units AS
-- (a) units anchored on an AR subledger row
SELECT s.rec_id AS sl_id, bs.id_a AS bank_id, sg.id_b AS gl_id,
       bs.tier AS tier_bs, sg.tier AS tier_sg
FROM src_records s
LEFT JOIN matches bs ON bs.pair = 'BANK_SL' AND bs.id_b = s.rec_id
LEFT JOIN matches sg ON sg.pair = 'SL_GL'   AND sg.id_a = s.rec_id
WHERE s.src = 'SL'
  AND s.rec_id NOT IN (SELECT rec_id FROM dup_flags WHERE occurrence > 1)
UNION ALL
-- (b) bank + GL linked directly, no AR subledger record
SELECT NULL, bg.id_a, bg.id_b, NULL, NULL
FROM matches bg
WHERE bg.pair = 'BANK_GL'
UNION ALL
-- (c) bank rows linked to nothing
SELECT NULL, b.rec_id, NULL, NULL, NULL
FROM src_records b
WHERE b.src = 'BANK'
  AND b.rec_id NOT IN (SELECT rec_id FROM dup_flags WHERE occurrence > 1)
  AND b.rec_id NOT IN (SELECT id_a FROM matches WHERE pair IN ('BANK_SL', 'BANK_GL'))
UNION ALL
-- (d) GL rows linked to nothing
SELECT NULL, NULL, g.rec_id, NULL, NULL
FROM src_records g
WHERE g.src = 'GL'
  AND g.rec_id NOT IN (SELECT rec_id FROM dup_flags WHERE occurrence > 1)
  AND g.rec_id NOT IN (SELECT id_b FROM matches WHERE pair IN ('SL_GL', 'BANK_GL'));

-- ---------------------------------------------------------------- 2. unit detail
DROP TABLE IF EXISTS unit_detail;
CREATE TABLE unit_detail AS
SELECT u.sl_id, u.bank_id, u.gl_id, u.tier_bs, u.tier_sg,
       b.rec_date AS bank_date, b.amount AS bank_amount, b.ref_norm AS bank_ref,
       s.rec_date AS sl_date,   s.amount AS sl_amount,   s.ref_norm AS sl_ref, s.customer_id,
       g.rec_date AS gl_date,   g.amount AS gl_amount,   g.ref_norm AS gl_ref,
       (u.bank_id IS NOT NULL) + (u.sl_id IS NOT NULL) + (u.gl_id IS NOT NULL) AS n_present
FROM units u
LEFT JOIN src_records b ON b.rec_id = u.bank_id
LEFT JOIN src_records s ON s.rec_id = u.sl_id
LEFT JOIN src_records g ON g.rec_id = u.gl_id;

-- Date spread and amount spread across whichever systems are present.
-- (SQLite's multi-argument MAX/MIN return NULL if any argument is NULL, so absent systems are
--  filled with a value from a present system, which cannot change the max or min.)
DROP TABLE IF EXISTS unit_class;
CREATE TABLE unit_class AS
WITH j AS (
    SELECT d.*,
           julianday(bank_date) AS jb, julianday(sl_date) AS js, julianday(gl_date) AS jg,
           COALESCE(julianday(bank_date), julianday(sl_date), julianday(gl_date)) AS anyd,
           COALESCE(bank_amount, sl_amount, gl_amount) AS anya
    FROM unit_detail d
), k AS (
    SELECT j.*,
           CAST(MAX(COALESCE(jb, anyd), COALESCE(js, anyd), COALESCE(jg, anyd))
              - MIN(COALESCE(jb, anyd), COALESCE(js, anyd), COALESCE(jg, anyd)) AS INTEGER) AS date_gap,
           MIN(COALESCE(jb, anyd), COALESCE(js, anyd), COALESCE(jg, anyd)) AS earliest,
           ROUND(MAX(COALESCE(bank_amount, anya), COALESCE(sl_amount, anya), COALESCE(gl_amount, anya))
               - MIN(COALESCE(bank_amount, anya), COALESCE(sl_amount, anya), COALESCE(gl_amount, anya)), 2) AS amt_gap
    FROM j
)
SELECT k.*,
       CASE
           WHEN n_present = 3 AND (tier_bs = 'REF_AMOUNT_BREAK' OR tier_sg = 'REF_AMOUNT_BREAK') THEN 'AMOUNT_MISMATCH'
           WHEN n_present = 3 AND date_gap > (SELECT tol_days FROM cfg)                          THEN 'TIMING'
           WHEN n_present = 3                                                                    THEN 'CLEAN'
           WHEN n_present = 2 AND bank_id IS NULL                                                THEN 'MISSING_IN_BANK'
           WHEN n_present = 2 AND sl_id   IS NULL                                                THEN 'MISSING_IN_SL'
           WHEN n_present = 2 AND gl_id   IS NULL                                                THEN 'MISSING_IN_GL'
           WHEN COALESCE(bank_id, sl_id, gl_id) IN (SELECT rec_id FROM ambiguous_flags)          THEN 'AMBIGUOUS_MATCH'
           WHEN bank_id IS NOT NULL                                                              THEN 'BANK_ONLY'
           WHEN sl_id   IS NOT NULL                                                              THEN 'SL_ONLY'
           ELSE                                                                                       'GL_ONLY'
       END AS unit_status
FROM k;

-- ---------------------------------------------------------------- 3. exceptions
DROP TABLE IF EXISTS exceptions_raw;
CREATE TABLE exceptions_raw AS
SELECT unit_status AS exception_type,
       CASE WHEN bank_id IS NOT NULL THEN 'BANK ' ELSE '' END ||
       CASE WHEN sl_id   IS NOT NULL THEN 'SL '   ELSE '' END ||
       CASE WHEN gl_id   IS NOT NULL THEN 'GL'    ELSE '' END AS systems_present,
       bank_id, sl_id, gl_id,
       COALESCE(sl_ref, gl_ref, bank_ref) AS reference,
       customer_id, bank_amount, sl_amount, gl_amount,
       CASE WHEN unit_status = 'AMOUNT_MISMATCH' THEN amt_gap
            ELSE ROUND(ABS(COALESCE(sl_amount, gl_amount, bank_amount)), 2) END AS amount_at_risk,
       date_gap,
       CAST(julianday((SELECT period_end FROM cfg)) - earliest AS INTEGER) AS age_days,
       CASE unit_status
           WHEN 'MISSING_IN_GL'   THEN 'In bank and AR subledger; no GL entry within tolerance. Pending analyst review.'
           WHEN 'MISSING_IN_SL'   THEN 'In bank and GL; no AR subledger application found. Pending analyst review.'
           WHEN 'MISSING_IN_BANK' THEN 'In AR subledger and GL; no matching bank credit found. Pending analyst review.'
           WHEN 'BANK_ONLY'       THEN 'Bank credit with no matching AR or GL record. Pending analyst review.'
           WHEN 'SL_ONLY'         THEN 'AR application with no matching bank credit or GL entry. Pending analyst review.'
           WHEN 'GL_ONLY'         THEN 'GL entry with no matching bank credit or AR application. Pending analyst review.'
           WHEN 'AMBIGUOUS_MATCH' THEN 'Several possible counterparts on amount and date; deliberately not auto-matched. Pending analyst review.'
           WHEN 'AMOUNT_MISMATCH' THEN printf('Reference ties across all three systems but amounts differ by $%.2f (tolerance $%.2f). Pending analyst review.',
                                              amt_gap, (SELECT tol_amount FROM cfg))
           WHEN 'TIMING'          THEN printf('Ties across all three systems but dates span %d days (tolerance %d). Pending analyst review.',
                                              date_gap, (SELECT tol_days FROM cfg))
       END AS review_note
FROM unit_class
WHERE unit_status <> 'CLEAN'
UNION ALL
-- Duplicate postings: the repeat copy of a reference already booked once in the same system.
SELECT 'DUPLICATE_POSTING',
       d.src,
       CASE WHEN d.src = 'BANK' THEN d.rec_id END,
       CASE WHEN d.src = 'SL'   THEN d.rec_id END,
       CASE WHEN d.src = 'GL'   THEN d.rec_id END,
       d.ref_norm,
       r.customer_id,
       CASE WHEN d.src = 'BANK' THEN d.amount END,
       CASE WHEN d.src = 'SL'   THEN d.amount END,
       CASE WHEN d.src = 'GL'   THEN d.amount END,
       ROUND(ABS(d.amount), 2),
       CAST(julianday(r.rec_date) - julianday(f.rec_date) AS INTEGER),
       CAST(julianday((SELECT period_end FROM cfg)) - julianday(r.rec_date) AS INTEGER),
       printf('Same reference and amount already posted once in %s (%s). Pending analyst review.', d.src, d.first_rec_id)
FROM dup_flags d
JOIN src_records r ON r.rec_id = d.rec_id
JOIN src_records f ON f.rec_id = d.first_rec_id
WHERE d.occurrence > 1;

DROP TABLE IF EXISTS exceptions;
CREATE TABLE exceptions AS
WITH sev AS (
    SELECT x.*,
           CASE WHEN amount_at_risk >= (SELECT high_amount FROM cfg) THEN 'HIGH'
                WHEN amount_at_risk >= (SELECT med_amount  FROM cfg) THEN 'MEDIUM'
                ELSE 'LOW' END AS severity
    FROM exceptions_raw x
)
SELECT 'E' || printf('%04d', ROW_NUMBER() OVER (
           ORDER BY CASE severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                    amount_at_risk DESC, exception_type)) AS exc_id,
       exception_type, severity, systems_present,
       bank_id, sl_id, gl_id, reference, customer_id,
       bank_amount, sl_amount, gl_amount, amount_at_risk, date_gap, age_days, review_note
FROM sev;

CREATE INDEX idx_exc_type ON exceptions (exception_type);
