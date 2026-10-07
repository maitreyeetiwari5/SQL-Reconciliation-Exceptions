"""
Step 1 - generate three synthetic, internally consistent systems for one quarter of customer cash receipts:

    bank_statement   what the bank credited
    ar_subledger     what AR applied to customer accounts
    gl_entries       what hit the GL cash-clearing account

Every receipt starts as a clean row in all three systems, with the small, realistic noise real
systems produce (different reference formats, a few days of posting lag, occasional bank fees,
a few blank references). Then a known set of breaks is seeded on top, and the answer key is
written to ground_truth/ -- OUTSIDE the database -- so the matching SQL can never see it.

All data is synthetic. Nothing here comes from any employer or real customer.
"""
import os
import sqlite3

import numpy as np
import pandas as pd

SEED = 42
N_RECEIPTS = 1500
N_CUSTOMERS = 60
PERIOD_START = pd.Timestamp("2026-01-02")
PERIOD_END = pd.Timestamp("2026-03-31")

# How many of each break to seed (each lands on a different receipt).
SEEDED = {
    "MISSING_IN_GL": 25,     # bank + AR, GL entry never posted
    "MISSING_IN_SL": 12,     # bank + GL, AR application missing
    "MISSING_IN_BANK": 20,   # AR + GL, no bank credit (e.g. deposit in transit)
    "BANK_ONLY": 8,          # bank credit nobody recorded
    "SL_ONLY": 5,            # AR application with no bank/GL footprint
    "GL_ONLY": 4,            # GL entry with no bank/AR footprint
    "AMOUNT_MISMATCH": 18,   # references tie, one system's amount is wrong
    "TIMING": 20,            # GL posts well after the cash (cut-off style lag)
    "DUPLICATE_GL": 10,      # GL entry posted twice
    "DUPLICATE_SL": 6,       # AR application keyed twice
}

DB_PATH = "recon.db"
TRUTH_DIR = "ground_truth"
DATA_DIR = "data"


def build_receipts(rng: np.random.Generator) -> pd.DataFrame:
    n = N_RECEIPTS
    days = (PERIOD_END - PERIOD_START).days - 8  # leave room for posting lag inside the quarter
    bank_date = PERIOD_START + pd.to_timedelta(rng.integers(0, days, n), unit="D")

    # Amounts: mostly lognormal, ~8% are "round" amounts (retainers, standard fees). Round amounts
    # recur across customers, which is what makes some reference-less rows genuinely ambiguous.
    amt = np.round(np.clip(rng.lognormal(mean=np.log(3200), sigma=0.9, size=n), 120, 48000), 2)
    round_set = np.array([500, 750, 1000, 1500, 2000, 2500, 5000], dtype=float)
    is_round = rng.random(n) < 0.08
    amt[is_round] = rng.choice(round_set, is_round.sum())

    inv = rng.permutation(np.arange(10000, 10000 + n))
    lag_sl = rng.choice([0, 1, 2, 3], n, p=[0.35, 0.35, 0.20, 0.10])
    lag_gl = rng.choice([0, 1, 2, 3], n, p=[0.50, 0.30, 0.15, 0.05])

    df = pd.DataFrame({
        "receipt_id": [f"R{i:05d}" for i in range(n)],
        "customer_id": [f"C{c:03d}" for c in rng.integers(1, N_CUSTOMERS + 1, n)],
        "invoice": inv,
        "amount": amt,
        "bank_date": bank_date,
    })
    df["sl_date"] = df["bank_date"] + pd.to_timedelta(lag_sl, unit="D")
    df["gl_date"] = df["sl_date"] + pd.to_timedelta(lag_gl, unit="D")

    # Bank nets off an occasional wire fee; GL occasionally differs by a cent of rounding.
    fee = np.where(rng.random(n) < 0.12, rng.choice([0.25, 0.35, 0.50, 0.75], n), 0.0)
    df["bank_amount"] = np.round(df["amount"] - fee, 2)
    df["sl_amount"] = df["amount"]
    df["gl_amount"] = np.round(df["amount"] + np.where(rng.random(n) < 0.03, 0.01, 0.0), 2)

    # Each system prints the reference its own way.
    lower = rng.random(n) < 0.10
    df["bank_ref"] = [("inv%d" if lo else "INV%d") % i for i, lo in zip(df["invoice"], lower)]
    df["sl_ref"] = [f"INV-{i}" for i in df["invoice"]]
    df["gl_ref"] = [f"{i}-PMT" for i in df["invoice"]]
    # A few rows arrive with no usable reference at all.
    df.loc[rng.random(n) < 0.03, "bank_ref"] = ""
    df.loc[rng.random(n) < 0.02, "sl_ref"] = ""

    for col in ("bank", "sl", "gl"):
        df[f"in_{col}"] = True
    return df


def swap_adjacent_digits(x: float) -> float:
    """Classic keying error: 4563.20 -> 4536.20"""
    s = f"{x:.2f}".replace(".", "")
    for i in range(len(s) - 1):
        if s[i] != s[i + 1]:
            s = s[:i] + s[i + 1] + s[i] + s[i + 2:]
            break
    return float(s[:-2] + "." + s[-2:])


def seed_breaks(df: pd.DataFrame, rng: np.random.Generator):
    order = rng.permutation(len(df))
    cursor = 0
    breaks = []          # (receipt_id, break_type, note)
    dup_rows = []        # extra rows appended later: (system, receipt_id)

    def take(k):
        nonlocal cursor
        ids = order[cursor:cursor + k]
        cursor += k
        return ids

    for i in take(SEEDED["MISSING_IN_GL"]):
        df.loc[i, "in_gl"] = False
        breaks.append((df.loc[i, "receipt_id"], "MISSING_IN_GL", "GL entry removed"))
    for i in take(SEEDED["MISSING_IN_SL"]):
        df.loc[i, "in_sl"] = False
        breaks.append((df.loc[i, "receipt_id"], "MISSING_IN_SL", "AR application removed"))
    for i in take(SEEDED["MISSING_IN_BANK"]):
        df.loc[i, "in_bank"] = False
        breaks.append((df.loc[i, "receipt_id"], "MISSING_IN_BANK", "bank credit removed"))
    for i in take(SEEDED["BANK_ONLY"]):
        df.loc[i, ["in_sl", "in_gl"]] = False
        breaks.append((df.loc[i, "receipt_id"], "BANK_ONLY", "AR and GL removed"))
    for i in take(SEEDED["SL_ONLY"]):
        df.loc[i, ["in_bank", "in_gl"]] = False
        breaks.append((df.loc[i, "receipt_id"], "SL_ONLY", "bank and GL removed"))
    for i in take(SEEDED["GL_ONLY"]):
        df.loc[i, ["in_bank", "in_sl"]] = False
        breaks.append((df.loc[i, "receipt_id"], "GL_ONLY", "bank and AR removed"))

    # Amount mismatches: 6 small ($2.00-$4.50 short-pays) and 12 larger (keying errors / partials).
    # The small ones are deliberate: they are the cases an over-generous tolerance would swallow.
    idx = take(SEEDED["AMOUNT_MISMATCH"])
    for n_, i in enumerate(idx):
        col = "sl_amount" if n_ % 2 == 0 else "gl_amount"
        base = df.loc[i, "amount"]
        if n_ < 6:
            new = round(base - float(rng.choice([2.00, 2.50, 3.00, 3.50, 4.00, 4.50])), 2)
            kind = "small short-pay"
        elif n_ % 3 == 0:
            new = swap_adjacent_digits(base)
            kind = "digit transposition"
            if abs(new - base) < 5:
                new = round(base * 0.9, 2)
                kind = "partial payment"
        else:
            new = round(base * float(rng.choice([0.85, 0.90, 0.95, 1.05, 1.10])), 2)
            kind = "partial / over-application"
        df.loc[i, col] = new
        breaks.append((df.loc[i, "receipt_id"], "AMOUNT_MISMATCH", f"{col} changed ({kind})"))

    for i in take(SEEDED["TIMING"]):
        df.loc[i, "gl_date"] = df.loc[i, "sl_date"] + pd.Timedelta(days=int(rng.integers(9, 29)))
        breaks.append((df.loc[i, "receipt_id"], "TIMING", "GL posting lagged 9-28 days"))

    for i in take(SEEDED["DUPLICATE_GL"]):
        dup_rows.append(("GL", df.loc[i, "receipt_id"]))
    for i in take(SEEDED["DUPLICATE_SL"]):
        dup_rows.append(("SL", df.loc[i, "receipt_id"]))
    return breaks, dup_rows


def to_systems(df: pd.DataFrame, dup_rows, rng: np.random.Generator):
    """Explode the receipt table into the three system extracts, assigning ids by date so row
    order carries no information about which receipt a row belongs to."""
    iso = lambda s: s.dt.strftime("%Y-%m-%d")

    bank = df[df["in_bank"]][["receipt_id", "bank_date", "bank_ref", "bank_amount"]].copy()
    bank.columns = ["receipt_id", "date", "reference", "amount"]
    sl = df[df["in_sl"]][["receipt_id", "sl_date", "customer_id", "sl_ref", "sl_amount"]].copy()
    sl.columns = ["receipt_id", "date", "customer_id", "reference", "amount"]
    gl = df[df["in_gl"]][["receipt_id", "gl_date", "gl_ref", "gl_amount"]].copy()
    gl.columns = ["receipt_id", "date", "reference", "amount"]

    bank["is_dup"], sl["is_dup"], gl["is_dup"] = False, False, False

    # Duplicate postings: same reference and amount re-posted 0-2 days later.
    by_id = df.set_index("receipt_id")
    extra_sl, extra_gl = [], []
    for system, rid in dup_rows:
        if system == "GL":
            r = gl[gl["receipt_id"] == rid].iloc[0].copy()
            r["date"] = r["date"] + pd.Timedelta(days=int(rng.integers(0, 3)))
            r["is_dup"] = True
            extra_gl.append(r)
        else:
            r = sl[sl["receipt_id"] == rid].iloc[0].copy()
            r["date"] = r["date"] + pd.Timedelta(days=int(rng.integers(0, 3)))
            r["is_dup"] = True
            extra_sl.append(r)
    if extra_gl:
        gl = pd.concat([gl, pd.DataFrame(extra_gl)], ignore_index=True)
    if extra_sl:
        sl = pd.concat([sl, pd.DataFrame(extra_sl)], ignore_index=True)

    out = {}
    for name, frame, prefix in (("bank", bank, "B"), ("sl", sl, "S"), ("gl", gl, "G")):
        frame = frame.assign(_tie=rng.random(len(frame))).sort_values(["date", "_tie"]).drop(columns="_tie")
        frame = frame.reset_index(drop=True)
        frame["rec_id"] = [f"{prefix}{i + 1:06d}" for i in range(len(frame))]
        out[name] = frame
    return out


def main():
    rng = np.random.default_rng(SEED)
    os.makedirs(TRUTH_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    receipts = build_receipts(rng)
    breaks, dup_rows = seed_breaks(receipts, rng)
    sys_ = to_systems(receipts, dup_rows, rng)
    bank, sl, gl = sys_["bank"], sys_["sl"], sys_["gl"]

    # ---- ground truth (kept OUTSIDE recon.db)
    truth_map = pd.concat([
        bank.assign(src="BANK")[["src", "rec_id", "receipt_id", "is_dup"]],
        sl.assign(src="SL")[["src", "rec_id", "receipt_id", "is_dup"]],
        gl.assign(src="GL")[["src", "rec_id", "receipt_id", "is_dup"]],
    ])
    truth_map.to_csv(f"{TRUTH_DIR}/truth_map.csv", index=False)

    seeded = pd.DataFrame(breaks, columns=["receipt_id", "break_type", "note"])
    dups = truth_map[truth_map["is_dup"]].assign(
        break_type=lambda d: "DUPLICATE_POSTING", note=lambda d: "second posting of the same receipt")
    seeded = pd.concat([seeded, dups[["receipt_id", "break_type", "note", "src", "rec_id"]]], ignore_index=True)
    seeded.to_csv(f"{TRUTH_DIR}/seeded_breaks.csv", index=False)

    # ---- source extracts + database
    iso = lambda s: pd.to_datetime(s).dt.strftime("%Y-%m-%d")
    bank_out = pd.DataFrame({"bank_id": bank["rec_id"], "value_date": iso(bank["date"]),
                             "reference": bank["reference"], "amount": bank["amount"],
                             "description": "CUSTOMER PAYMENT"})
    sl_out = pd.DataFrame({"sl_id": sl["rec_id"], "apply_date": iso(sl["date"]),
                           "customer_id": sl["customer_id"], "reference": sl["reference"],
                           "amount": sl["amount"]})
    gl_out = pd.DataFrame({"gl_id": gl["rec_id"], "posting_date": iso(gl["date"]),
                           "reference": gl["reference"], "amount": gl["amount"],
                           "account": "1010-CASH-CLEARING"})
    bank_out.to_csv(f"{DATA_DIR}/bank_statement.csv", index=False)
    sl_out.to_csv(f"{DATA_DIR}/ar_subledger.csv", index=False)
    gl_out.to_csv(f"{DATA_DIR}/gl_entries.csv", index=False)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    with open("sql/01_schema.sql") as f:
        con.executescript(f.read())
    bank_out.to_sql("bank_statement", con, if_exists="append", index=False)
    sl_out.to_sql("ar_subledger", con, if_exists="append", index=False)
    gl_out.to_sql("gl_entries", con, if_exists="append", index=False)
    con.commit()
    con.close()

    print(f"receipts generated : {N_RECEIPTS}")
    print(f"bank / AR / GL rows: {len(bank_out)} / {len(sl_out)} / {len(gl_out)}")
    print(f"seeded breaks      : {len(seeded)} ({seeded['break_type'].value_counts().to_dict()})")
    print(f"database           : {DB_PATH}   (answer key in {TRUTH_DIR}/, not in the database)")


if __name__ == "__main__":
    main()
