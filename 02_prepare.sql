-- 02_prepare.sql
-- Put the three systems on one footing so the matching logic is written once.
--   * src_records: one row per record per system, with a normalized reference key
--   * dup_flags:   repeat postings (same system + same reference + same amount) found with a window function

DROP TABLE IF EXISTS src_records;
CREATE TABLE src_records AS
SELECT 'BANK' AS src, bank_id AS rec_id, value_date AS rec_date,
       reference AS raw_ref,
       -- Reference formats differ by system: INV10432 / INV-10432 / 10432-PMT.
       -- Strip the decoration so all three reduce to the bare invoice number.
       NULLIF(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(TRIM(reference)), 'INV', ''), '-', ''), 'PMT', ''), ' ', ''), '') AS ref_norm,
       amount, NULL AS customer_id
FROM bank_statement
UNION ALL
SELECT 'SL', sl_id, apply_date, reference,
       NULLIF(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(TRIM(reference)), 'INV', ''), '-', ''), 'PMT', ''), ' ', ''), ''),
       amount, customer_id
FROM ar_subledger
UNION ALL
SELECT 'GL', gl_id, posting_date, reference,
       NULLIF(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(TRIM(reference)), 'INV', ''), '-', ''), 'PMT', ''), ' ', ''), ''),
       amount, NULL
FROM gl_entries;

CREATE INDEX idx_src_ref ON src_records (src, ref_norm);
CREATE INDEX idx_src_id  ON src_records (rec_id);

-- A reference should appear once per system. The 2nd, 3rd... occurrence is a duplicate posting.
-- Duplicates are pulled out of the matching pool so they cannot steal the real record's counterpart.
DROP TABLE IF EXISTS dup_flags;
CREATE TABLE dup_flags AS
SELECT src, rec_id, ref_norm, amount,
       ROW_NUMBER() OVER w AS occurrence,
       FIRST_VALUE(rec_id) OVER w AS first_rec_id
FROM src_records
WHERE ref_norm IS NOT NULL
WINDOW w AS (PARTITION BY src, ref_norm, ROUND(amount, 2) ORDER BY rec_date, rec_id);

CREATE INDEX idx_dup_id ON dup_flags (rec_id);

DROP TABLE IF EXISTS ambiguous_flags;
CREATE TABLE ambiguous_flags (rec_id TEXT PRIMARY KEY);

DELETE FROM matches;
