"""
Step 4 - build docs/index.html: a self-contained dashboard (no server, no build step).
Reads the exceptions table and report queries from recon.db, plus the validation outputs,
and injects them into dashboard_template.html as one JSON blob.
"""
import json
import os
import sqlite3

import pandas as pd

from run_reconciliation import DEFAULT_CFG, report, summarize


def sweep_slices(sweep: pd.DataFrame, seeded_total: int) -> dict:
    sweep = sweep.assign(missed=lambda d: (seeded_total - (d["recall"] * seeded_total).round()).astype(int))
    cols = ["missed", "false_exceptions", "is_default"]
    amt = sweep[sweep["tol_days"] == DEFAULT_CFG["tol_days"]].sort_values("tol_amount")
    day = sweep[sweep["tol_amount"] == DEFAULT_CFG["tol_amount"]].sort_values("tol_days")
    return {
        "amount": [dict(tol=float(r.tol_amount), **{c: getattr(r, c) for c in cols}) for r in amt.itertuples()],
        "days": [dict(tol=int(r.tol_days), **{c: getattr(r, c) for c in cols}) for r in day.itertuples()],
    }


def main():
    con = sqlite3.connect("recon.db")
    rep = report(con)
    exc = pd.read_sql_query("SELECT * FROM exceptions", con)
    con.close()

    with open("data/validation_summary.json") as f:
        val = json.load(f)
    sweep = pd.read_csv("data/tolerance_sensitivity.csv")

    payload = {
        "cfg": {"tol_amount": DEFAULT_CFG["tol_amount"], "tol_days": DEFAULT_CFG["tol_days"]},
        "summary": summarize(rep),
        "funnel": rep["funnel"].to_dict("records"),
        "by_type": rep["by_type"].to_dict("records"),
        "aging": rep["aging"].to_dict("records"),
        "validation": {k: val[k] for k in ("seeded_total", "detected_correctly", "recall", "precision", "false_exceptions")},
        "sweep": sweep_slices(sweep, val["seeded_total"]),
        "exceptions": json.loads(exc.to_json(orient="records")),
    }

    with open("dashboard_template.html") as f:
        html = f.read()
    html = html.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":"), default=int))

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w") as f:
        f.write(html)
    print(f"wrote docs/index.html  ({len(html) / 1024:.0f} KB, {len(exc)} exceptions embedded)")


if __name__ == "__main__":
    main()
