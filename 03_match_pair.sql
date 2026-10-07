-- 03_match_pair.sql  (template: __PAIR__ is filled in, e.g. BANK_SL / SL_GL / BANK_GL)
-- The same three rules are applied to every pairing of systems, strictest first.
-- A row can be linked at most once per pairing (ranked 1:1 so nothing is double-counted).
--
--   Tier 1  EXACT_REF          same reference, amount within tolerance
--   Tier 2  REF_AMOUNT_BREAK   same reference, amount OUTSIDE tolerance  -> linked, then reported as an amount break
--   Tier 3  TOLERANCE          no usable reference: amount within tolerance AND dates within tol_days,
--                              accepted only when the counterpart is UNIQUE on both sides.
--                              If a row has several plausible partners it is flagged ambiguous, not guessed.

-- ---------------------------------------------------------------- Tier 1
DROP TABLE IF EXISTS cand;
CREATE TEMP TABLE cand AS
SELECT a.rec_id AS id_a, b.rec_id AS id_b,
       ROUND(a.amount - b.amount, 2) AS amt_diff,
       CAST(ABS(julianday(a.rec_date) - julianday(b.rec_date)) AS INTEGER) AS day_diff
FROM pool_a a
JOIN pool_b b ON a.ref_norm = b.ref_norm
WHERE ROUND(ABS(a.amount - b.amount), 2) <= (SELECT tol_amount FROM cfg)
  AND ABS(julianday(a.rec_date) - julianday(b.rec_date)) <= (SELECT wide_days FROM cfg);

INSERT INTO matches (pair, id_a, id_b, tier, amt_diff, day_diff)
SELECT '__PAIR__', id_a, id_b, 'EXACT_REF', amt_diff, day_diff
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id_b ORDER BY ABS(amt_diff), day_diff, id_a) AS rn_b
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY id_a ORDER BY ABS(amt_diff), day_diff, id_b) AS rn_a
        FROM cand
    ) WHERE rn_a = 1
) WHERE rn_b = 1;

-- ---------------------------------------------------------------- Tier 2
DROP TABLE IF EXISTS cand;
CREATE TEMP TABLE cand AS
SELECT a.rec_id AS id_a, b.rec_id AS id_b,
       ROUND(a.amount - b.amount, 2) AS amt_diff,
       CAST(ABS(julianday(a.rec_date) - julianday(b.rec_date)) AS INTEGER) AS day_diff
FROM pool_a a
JOIN pool_b b ON a.ref_norm = b.ref_norm
WHERE ROUND(ABS(a.amount - b.amount), 2) > (SELECT tol_amount FROM cfg)
  AND ABS(julianday(a.rec_date) - julianday(b.rec_date)) <= (SELECT wide_days FROM cfg)
  AND a.rec_id NOT IN (SELECT id_a FROM matches WHERE pair = '__PAIR__')
  AND b.rec_id NOT IN (SELECT id_b FROM matches WHERE pair = '__PAIR__');

INSERT INTO matches (pair, id_a, id_b, tier, amt_diff, day_diff)
SELECT '__PAIR__', id_a, id_b, 'REF_AMOUNT_BREAK', amt_diff, day_diff
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id_b ORDER BY ABS(amt_diff), day_diff, id_a) AS rn_b
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY id_a ORDER BY ABS(amt_diff), day_diff, id_b) AS rn_a
        FROM cand
    ) WHERE rn_a = 1
) WHERE rn_b = 1;

-- ---------------------------------------------------------------- Tier 3
-- Only rows where at least one side has no reference. Two rows that each carry a DIFFERENT
-- valid reference are different invoices, however close their amounts are.
DROP TABLE IF EXISTS cand;
CREATE TEMP TABLE cand AS
SELECT a.rec_id AS id_a, b.rec_id AS id_b,
       ROUND(a.amount - b.amount, 2) AS amt_diff,
       CAST(ABS(julianday(a.rec_date) - julianday(b.rec_date)) AS INTEGER) AS day_diff
FROM pool_a a
JOIN pool_b b
  ON ROUND(ABS(a.amount - b.amount), 2) <= (SELECT tol_amount FROM cfg)
 AND ABS(julianday(a.rec_date) - julianday(b.rec_date)) <= (SELECT tol_days FROM cfg)
WHERE (a.ref_norm IS NULL OR b.ref_norm IS NULL)
  AND a.rec_id NOT IN (SELECT id_a FROM matches WHERE pair = '__PAIR__')
  AND b.rec_id NOT IN (SELECT id_b FROM matches WHERE pair = '__PAIR__');

DROP TABLE IF EXISTS cand_counted;
CREATE TEMP TABLE cand_counted AS
SELECT *, COUNT(*) OVER (PARTITION BY id_a) AS n_a,
          COUNT(*) OVER (PARTITION BY id_b) AS n_b
FROM cand;

INSERT INTO matches (pair, id_a, id_b, tier, amt_diff, day_diff)
SELECT '__PAIR__', id_a, id_b, 'TOLERANCE', amt_diff, day_diff
FROM cand_counted
WHERE n_a = 1 AND n_b = 1;

-- Anything with more than one plausible partner is left for a human.
INSERT OR IGNORE INTO ambiguous_flags (rec_id)
SELECT id_a FROM cand_counted WHERE n_a > 1 OR n_b > 1
UNION
SELECT id_b FROM cand_counted WHERE n_a > 1 OR n_b > 1;
