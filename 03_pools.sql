-- 03_pools.sql  (template: run_reconciliation.py fills the __PLACEHOLDERS__)
-- Builds the two candidate pools for one pairing of systems.
--   __SRC_A__ / __SRC_B__   which two systems are being matched
--   __EXCL__                1 = leave out rows already linked to another system (used for the
--                           BANK_GL pass, which only sees what the SL-anchored passes left over)
-- Duplicate postings never enter a pool.

DROP TABLE IF EXISTS pool_a;
CREATE TEMP TABLE pool_a AS
SELECT rec_id, rec_date, ref_norm, amount
FROM src_records
WHERE src = '__SRC_A__'
  AND rec_id NOT IN (SELECT rec_id FROM dup_flags WHERE occurrence > 1)
  AND (__EXCL__ = 0 OR rec_id NOT IN (SELECT id_a FROM matches UNION SELECT id_b FROM matches));

DROP TABLE IF EXISTS pool_b;
CREATE TEMP TABLE pool_b AS
SELECT rec_id, rec_date, ref_norm, amount
FROM src_records
WHERE src = '__SRC_B__'
  AND rec_id NOT IN (SELECT rec_id FROM dup_flags WHERE occurrence > 1)
  AND (__EXCL__ = 0 OR rec_id NOT IN (SELECT id_a FROM matches UNION SELECT id_b FROM matches));

CREATE INDEX idx_pool_a_ref ON pool_a (ref_norm);
CREATE INDEX idx_pool_b_ref ON pool_b (ref_norm);
