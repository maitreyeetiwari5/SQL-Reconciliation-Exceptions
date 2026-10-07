-- 01_schema.sql
-- Three source systems (as extracted) plus the working tables the pipeline writes to.
-- The source tables hold NO hidden answer key; ground truth lives outside the database
-- (ground_truth/*.csv) and is only read by validate_against_ground_truth.py.

DROP TABLE IF EXISTS bank_statement;
CREATE TABLE bank_statement (
    bank_id     TEXT PRIMARY KEY,
    value_date  TEXT NOT NULL,          -- ISO date the bank credited the account
    reference   TEXT,                   -- remittance reference as the bank prints it (may be blank)
    amount      REAL NOT NULL,          -- net of any bank fees
    description TEXT
);

DROP TABLE IF EXISTS ar_subledger;
CREATE TABLE ar_subledger (
    sl_id       TEXT PRIMARY KEY,
    apply_date  TEXT NOT NULL,          -- date the cash was applied to the customer account
    customer_id TEXT,
    reference   TEXT,                   -- invoice reference as AR keys it (may be blank)
    amount      REAL NOT NULL
);

DROP TABLE IF EXISTS gl_entries;
CREATE TABLE gl_entries (
    gl_id        TEXT PRIMARY KEY,
    posting_date TEXT NOT NULL,         -- GL posting date
    reference    TEXT,                  -- GL document text
    amount       REAL NOT NULL,
    account      TEXT                   -- cash clearing account
);

-- Single-row configuration. Every matching rule reads from here, nothing is hardcoded in the SQL.
DROP TABLE IF EXISTS cfg;
CREATE TABLE cfg (
    tol_amount  REAL,      -- max |amount difference| still treated as a match (fees, rounding)
    tol_days    INTEGER,   -- max date spread across systems before a match is flagged as TIMING
    wide_days   INTEGER,   -- how far apart two rows may be and still be linked by reference
    high_amount REAL,      -- exposure at/above this is HIGH severity
    med_amount  REAL,      -- exposure at/above this is MEDIUM severity
    period_end  TEXT       -- reconciliation as-of date, used for aging
);

-- Pairwise links found by the matching tiers (see 03_match_pair.sql).
DROP TABLE IF EXISTS matches;
CREATE TABLE matches (
    pair     TEXT NOT NULL,     -- BANK_SL | SL_GL | BANK_GL
    id_a     TEXT NOT NULL,
    id_b     TEXT NOT NULL,
    tier     TEXT NOT NULL,     -- EXACT_REF | REF_AMOUNT_BREAK | TOLERANCE
    amt_diff REAL,
    day_diff INTEGER
);

-- Rows that had more than one plausible counterpart and were deliberately NOT auto-matched.
DROP TABLE IF EXISTS ambiguous_flags;
CREATE TABLE ambiguous_flags (
    rec_id TEXT PRIMARY KEY
);
