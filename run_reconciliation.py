"""
Step 2 - run the SQL reconciliation pipeline against recon.db.

The matching logic lives in sql/*.sql. This file only sequences it:
    02_prepare           unify systems, normalize references, flag duplicate postings
    03_pools + 03_match  match BANK<->SL, SL<->GL, then leftover BANK<->GL, same rules each time
    04_assemble          build one unit per receipt, classify it, write the exceptions table
    05_reports           named queries that feed the dashboard and Excel report

Tolerances come from the cfg table, so the same code can be re-run at different settings
(validate_against_ground_truth.py does exactly that for its sensitivity sweep).
"""
import json
import re
import sqlite3
import sys

import pandas as pd

DEFAULT_CFG = dict(tol_amount=1.00, tol_days=7, wide_days=45,
                   high_amount=5000.0, med_amount=1000.0, period_end="2026-03-31")

# (pair name, system A, system B, exclude already-linked rows?)
PAIRINGS = [
    ("BANK_SL", "BANK", "SL", 0),
    ("SL_GL",   "SL",   "GL", 0),
    ("BANK_GL", "BANK", "GL", 1),   # only sees what the SL-anchored passes left over
]


def read_sql(path: str) -> str:
    with open(path) as f:
        return f.read()


def fill(template: str, **kw) -> str:
    for key, val in kw.items():
        template = template.replace(f"__{key}__", str(val))
    return template


def integrity_checks(con: sqlite3.Connection) -> dict:
    """Control totals. A reconciliation that cannot account for every row is not a reconciliation.
    Raises if any row is linked twice or if any source row is missing from the output."""
    one = lambda q: con.execute(q).fetchone()[0]
    double_a = one("SELECT COUNT(*) FROM (SELECT 1 FROM matches GROUP BY pair, id_a HAVING COUNT(*) > 1)")
    double_b = one("SELECT COUNT(*) FROM (SELECT 1 FROM matches GROUP BY pair, id_b HAVING COUNT(*) > 1)")
    total = one("SELECT COUNT(*) FROM src_records")
    in_units = one("SELECT SUM((bank_id IS NOT NULL) + (sl_id IS NOT NULL) + (gl_id IS NOT NULL)) FROM units")
    dup_rows = one("SELECT COUNT(*) FROM dup_flags WHERE occurrence > 1")
    result = {"rows_linked_twice": double_a + double_b,
              "source_rows": total, "rows_in_units": in_units, "duplicate_rows": dup_rows,
              "unaccounted_rows": total - in_units - dup_rows}
    if result["rows_linked_twice"] or result["unaccounted_rows"]:
        raise AssertionError(f"integrity check failed: {result}")
    return result


def run_pipeline(con: sqlite3.Connection, **overrides) -> None:
    cfg = {**DEFAULT_CFG, **overrides}
    con.execute("DELETE FROM cfg")
    con.execute("INSERT INTO cfg VALUES (?,?,?,?,?,?)",
                (cfg["tol_amount"], cfg["tol_days"], cfg["wide_days"],
                 cfg["high_amount"], cfg["med_amount"], cfg["period_end"]))
    con.commit()

    con.executescript(read_sql("sql/02_prepare.sql"))
    pools, match = read_sql("sql/03_pools.sql"), read_sql("sql/03_match_pair.sql")
    for pair, a, b, excl in PAIRINGS:
        con.executescript(fill(pools, SRC_A=a, SRC_B=b, EXCL=excl))
        con.executescript(fill(match, PAIR=pair))
    con.executescript(read_sql("sql/04_assemble_exceptions.sql"))
    con.commit()
    integrity_checks(con)   # fails loudly; also runs on every sweep iteration


def named_queries(path: str = "sql/05_reports.sql") -> dict:
    """Split 05_reports.sql into {name: statement} using the '-- name:' tags."""
    text = read_sql(path)
    blocks = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.M)
    return {blocks[i]: blocks[i + 1].strip() for i in range(1, len(blocks), 2)}


def report(con: sqlite3.Connection) -> dict:
    return {name: pd.read_sql_query(sql, con) for name, sql in named_queries().items()}


def summarize(rep: dict) -> dict:
    k = rep["kpis"].iloc[0]
    funnel = rep["funnel"]
    total, clean = int(funnel["total_rows"].sum()), int(funnel["clean_rows"].sum())
    return {
        "total_rows": total,
        "auto_matched_rows": clean,
        "auto_match_rate": round(clean / total, 4),
        "units": int(k["total_units"]),
        "clean_units": int(k["clean_units"]),
        "exceptions": int(k["exceptions"]),
        "exposure": float(k["exposure"]),
        "duplicate_rows": int(k["duplicate_rows"]),
    }


def main():
    con = sqlite3.connect("recon.db")
    run_pipeline(con)
    rep = report(con)
    exc = pd.read_sql_query("SELECT * FROM exceptions", con)
    exc.to_csv("data/exceptions.csv", index=False)
    summary = summarize(rep)
    with open("data/summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    chk = integrity_checks(con)
    print(f"integrity checks      : passed (0 rows linked twice, {chk['unaccounted_rows']} unaccounted of {chk['source_rows']:,})")
    print(f"rows across 3 systems : {summary['total_rows']:,}")
    print(f"auto-matched rows     : {summary['auto_matched_rows']:,}  ({summary['auto_match_rate']:.1%})")
    print(f"exceptions raised     : {summary['exceptions']}   (${summary['exposure']:,.2f} exposure)")
    print()
    print(rep["by_type"].to_string(index=False))
    con.close()


if __name__ == "__main__":
    sys.exit(main())
