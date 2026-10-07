"""
Step 3 - check the engine against the answer key it never saw, then stress the tolerances.

Part 1  Score the default run: for every seeded break, did the engine raise the right exception?
        And did it raise exceptions that are NOT seeded breaks (false alarms)?
Part 2  Sweep amount tolerance x date tolerance. Too tight and normal noise (bank fees, posting lag)
        becomes false exceptions; too loose and real small breaks get absorbed as "matched".
        This is the evidence behind the default settings.

Outputs: data/validation_by_type.csv, data/tolerance_sensitivity.csv, data/validation_summary.json
"""
import json
import sqlite3
import time

import pandas as pd

from run_reconciliation import DEFAULT_CFG, run_pipeline, report, summarize

TOL_AMOUNTS = [0.00, 0.50, 1.00, 2.00, 5.00]
TOL_DAYS = [2, 5, 7, 10]


def load_truth():
    truth = pd.read_csv("ground_truth/truth_map.csv")
    seeded = pd.read_csv("ground_truth/seeded_breaks.csv")
    return truth, seeded


def evaluate(con: sqlite3.Connection, truth: pd.DataFrame, seeded: pd.DataFrame) -> dict:
    rec_to_receipt = dict(zip(truth["rec_id"], truth["receipt_id"]))
    receipt_rows = truth[~truth["is_dup"]].groupby("receipt_id")["rec_id"].apply(list).to_dict()

    units = pd.read_sql_query("SELECT sl_id, bank_id, gl_id, unit_status FROM unit_class", con)
    row_status = {}   # rec_id -> status of the unit it sits in
    unit_receipts = []
    for r in units.itertuples(index=False):
        ids = [x for x in (r.bank_id, r.sl_id, r.gl_id) if isinstance(x, str)]
        for x in ids:
            row_status[x] = r.unit_status
        unit_receipts.append((r.unit_status, {rec_to_receipt[x] for x in ids}))

    exc = pd.read_sql_query("SELECT exception_type, bank_id, sl_id, gl_id FROM exceptions", con)
    # The two copies of a duplicate posting are identical rows, so the engine may flag either one.
    # Score at the level of (system, receipt): "was this receipt's duplicate in this system found?"
    rec_to_src = dict(zip(truth["rec_id"], truth["src"]))
    dup_found = set()
    for r in exc[exc["exception_type"] == "DUPLICATE_POSTING"].itertuples(index=False):
        rid = next(x for x in (r.bank_id, r.sl_id, r.gl_id) if isinstance(x, str))
        dup_found.add((rec_to_src[rid], rec_to_receipt[rid]))

    # ---- recall by seeded break type
    rows = []
    seeded_receipts = set()
    for t, grp in seeded[seeded["break_type"] != "DUPLICATE_POSTING"].groupby("break_type"):
        ok = missed = wrong = 0
        for rid in grp["receipt_id"]:
            seeded_receipts.add(rid)
            statuses = {row_status[x] for x in receipt_rows[rid] if x in row_status}
            if t in statuses:
                ok += 1
            elif statuses <= {"CLEAN"}:
                missed += 1          # swallowed into a clean match
            else:
                wrong += 1           # flagged, but as a different exception type
        rows.append((t, len(grp), ok, missed, wrong))
    dups = seeded[seeded["break_type"] == "DUPLICATE_POSTING"]
    dup_truth = set(zip(dups["src"], dups["receipt_id"]))
    d_ok = len(dup_truth & dup_found)
    rows.append(("DUPLICATE_POSTING", len(dup_truth), d_ok, len(dup_truth) - d_ok, 0))
    by_type = pd.DataFrame(rows, columns=["break_type", "seeded", "detected_correctly", "missed", "misclassified"])
    by_type["recall"] = (by_type["detected_correctly"] / by_type["seeded"]).round(3)

    # ---- false alarms: exception units that involve no seeded receipt at all
    false_units = [(s, r) for s, r in unit_receipts if s != "CLEAN" and not (r & seeded_receipts)]
    false_dups = len(dup_found - dup_truth)
    false_by_type = pd.Series([s for s, _ in false_units]).value_counts().to_dict()
    # ---- wrong pairings: a "clean" unit stitched together from more than one receipt
    mispairs = sum(1 for s, r in unit_receipts if s == "CLEAN" and len(r) > 1)

    seeded_total = int(by_type["seeded"].sum())
    correct = int(by_type["detected_correctly"].sum())
    n_exc = len(exc)
    n_false = len(false_units) + false_dups
    return {
        "seeded_total": seeded_total,
        "detected_correctly": correct,
        "recall": round(correct / seeded_total, 4),
        "exceptions": n_exc,
        "false_exceptions": n_false,
        "false_by_type": false_by_type,
        "precision": round(1 - n_false / n_exc, 4) if n_exc else 1.0,
        "mispaired_clean_units": mispairs,
        "by_type": by_type,
    }


def fresh_copy() -> sqlite3.Connection:
    src = sqlite3.connect("recon.db")
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    return mem


def main():
    truth, seeded = load_truth()

    # ---------------- Part 1: default settings
    con = fresh_copy()
    run_pipeline(con)
    res = evaluate(con, truth, seeded)
    summ = summarize(report(con))
    con.close()

    print(f"Default tolerances: ${DEFAULT_CFG['tol_amount']:.2f} / {DEFAULT_CFG['tol_days']} days\n")
    print(res["by_type"].to_string(index=False))
    print(f"\nrecall    : {res['detected_correctly']}/{res['seeded_total']} seeded breaks caught as the right type ({res['recall']:.1%})")
    print(f"precision : {res['exceptions'] - res['false_exceptions']}/{res['exceptions']} exceptions are real seeded breaks ({res['precision']:.1%})")
    print(f"false alarms by type: {res['false_by_type']}")
    print(f"wrongly paired 'clean' units: {res['mispaired_clean_units']}")
    print(f"auto-match rate (rows cleared without review): {summ['auto_match_rate']:.1%}")
    res["by_type"].to_csv("data/validation_by_type.csv", index=False)

    # ---------------- Part 2: tolerance sweep
    sweep = []
    for ta in TOL_AMOUNTS:
        for td in TOL_DAYS:
            t0 = time.time()
            c = fresh_copy()
            run_pipeline(c, tol_amount=ta, tol_days=td)
            r = evaluate(c, truth, seeded)
            s = summarize(report(c))
            c.close()
            sweep.append({
                "tol_amount": ta, "tol_days": td,
                "auto_match_rate": s["auto_match_rate"],
                "exceptions": r["exceptions"],
                "recall": r["recall"],
                "precision": r["precision"],
                "false_exceptions": r["false_exceptions"],
                "mispaired_clean_units": r["mispaired_clean_units"],
                "is_default": ta == DEFAULT_CFG["tol_amount"] and td == DEFAULT_CFG["tol_days"],
            })
            print(f"  swept ${ta:<4.2f} / {td:>2}d  recall {r['recall']:.3f}  precision {r['precision']:.3f}  ({time.time() - t0:.1f}s)")
    sweep = pd.DataFrame(sweep)
    sweep.to_csv("data/tolerance_sensitivity.csv", index=False)

    with open("data/validation_summary.json", "w") as f:
        json.dump({k: v for k, v in res.items() if k != "by_type"} | {"auto_match_rate": summ["auto_match_rate"]},
                  f, indent=2, default=int)
    print("\nwrote data/validation_by_type.csv, data/tolerance_sensitivity.csv, data/validation_summary.json")


if __name__ == "__main__":
    main()
