# Three-Way Cash Reconciliation in SQL

A SQL matching engine that ties customer cash receipts across three systems (bank statement, AR subledger, general ledger), clears what agrees, and turns everything else into a ranked, explained exception queue. Built to show what automating the mechanical part of a reconciliation looks like, and where the engine deliberately stops and hands a case to an analyst.

**[Live dashboard](https://maitreyeetiwari5.github.io/SQL-Reconciliation-Exceptions/)** - filter the exception queue by type and severity, expand any item to see what each system holds, and see the tolerance trade-off behind the settings.

**[Excel report](reconciliation_report.xlsx)** - the same results as a reviewer's workbook: summary (all formulas), exception queue with status columns, and the tolerance sensitivity table.

## Problem

Every customer receipt should appear once in the bank statement, once in the AR subledger and once in the GL. In practice the three disagree: references are formatted differently in each system, posting dates lag, banks net off fees, some receipts never get posted, and some get posted twice. Working through that by hand does not scale, and a single loose rule either buries analysts in false alarms or quietly absorbs real breaks.

The mechanical part (normalize, match, classify, size the exposure) is a good fit for SQL. Deciding *why* something does not tie is not, and the engine does not guess.

## Pipeline

Run in order (or `./run_all.sh`):

| Step | Script | What it does |
| --- | --- | --- |
| 1 | `generate_data.py` | Builds `recon.db`: 4,425 synthetic records across three systems for one quarter, with 128 planted breaks. The answer key is written to `ground_truth/`, outside the database |
| 2 | `run_reconciliation.py` | Runs the SQL in `sql/` (below), then writes `data/exceptions.csv` and `data/summary.json` |
| 3 | `validate_against_ground_truth.py` | Scores the engine against the planted breaks, then re-runs it across 20 tolerance settings |
| 4 | `build_dashboard.py` | `docs/index.html`, a self-contained dashboard (no server, no build step) |
| 5 | `export_excel.py` | `reconciliation_report.xlsx` |

```bash
pip install -r requirements.txt
python generate_data.py
python run_reconciliation.py
python validate_against_ground_truth.py
python build_dashboard.py
python export_excel.py
```

### The SQL

| File | Role |
| --- | --- |
| `01_schema.sql` | Source tables, a one-row `cfg` table holding every tolerance, working tables |
| `02_prepare.sql` | Unifies the three systems, normalizes references (`INV10432` / `INV-10432` / `10432-PMT` all become `10432`), flags duplicate postings with `ROW_NUMBER()` |
| `03_pools.sql`, `03_match_pair.sql` | The matching rules, written once and applied to BANK-to-AR, AR-to-GL, then leftover BANK-to-GL |
| `04_assemble_exceptions.sql` | One unit per receipt, classification, severity, exposure, aging, review notes |
| `05_reports.sql` | Named queries that feed the dashboard and workbook |

## How matching works

Three tiers, strictest first, applied identically to each pair of systems. A row can link at most once per pair (ranked 1:1 with window functions).

| Tier | Rule | Outcome |
| --- | --- | --- |
| 1 | Same reference, amount within tolerance | Matched |
| 2 | Same reference, amount outside tolerance | Linked, reported as an **amount mismatch** |
| 3 | No usable reference: amount and date within tolerance, and the counterpart is **unique** on both sides | Matched. If there is more than one candidate the row is flagged **ambiguous** and left for a human |

A unit is cleared only if all three systems agree on amount and dates sit within the date tolerance. Anything else becomes one of ten exception types: missing in GL / AR / bank, bank-only / AR-only / GL-only, amount mismatch, timing gap, duplicate posting, ambiguous match.

## Key design choices

- **No guessing on ambiguity.** When several rows could be the counterpart (common with round amounts and blank references), the engine matches none of them and says so. A wrong auto-match is worse than a flagged item.
- **Notes describe, they do not explain.** Every exception note states what does not tie ("dates span 14 days, tolerance 7") and ends "pending analyst review". The engine never asserts a cause.
- **Tolerances are data, not code.** They live in `cfg`, so the same SQL re-runs at any setting. That is what makes the sweep possible.
- **Control totals.** Every run asserts that no row is linked twice and every source row is accounted for (`4,425` in, `4,425` out). The run fails if either breaks.
- **Answer key kept out of the database.** The matching SQL cannot see which receipts were tampered with.

## Results

Measured on the synthetic quarter at the default settings ($1.00 amount tolerance, 7 days).

- **94.0%** of 4,425 records cleared without review; **130** exceptions, **$555,310** total exposure
- **128 of 128** planted breaks caught and classified as the right type
- **98.5%** precision: 128 of 130 exceptions are planted breaks. The other two are one receipt with a blank bank reference and a round $5,000 amount, which the engine refused to guess at (an ambiguous match plus its orphaned counterpart)
- **0** wrongly paired "clean" matches; control totals tie

### Why these tolerances

| Amount tolerance (7 days held) | Breaks missed | False alarms |
| --- | --- | --- |
| $0.00 | 4 | 224 |
| $0.50 | 0 | 48 |
| **$1.00** | **0** | **2** |
| $2.00 | 0 | 2 |
| $5.00 | 6 | 2 |

| Date tolerance ($1.00 held) | Breaks missed | False alarms |
| --- | --- | --- |
| 2 days | 0 | 394 |
| 5 days | 0 | 8 |
| **7 days** | **0** | **2** |
| 10 days | 2 | 4 |

At $0.00 every bank fee becomes an amount mismatch. At $5.00 the small short-pays are absorbed as matches. At 2 days normal posting lag floods the queue; at 10 days real timing gaps are absorbed. The full 20-run grid is in `data/tolerance_sensitivity.csv`.

## Honest limits

- **The data is synthetic and the breaks are planted.** 128/128 shows the logic does what it was designed to do against known cases. It says nothing about recall on real-world breaks nobody anticipated.
- **The 7-day knee comes from how the lag was generated.** Posting lag in the generator tops out at 6 days, so 7 is the natural setting. On real data the date tolerance should be set from the observed spread on cleanly matched items.
- **Matching is one-to-one.** Real reconciliations also have one bank deposit covering several invoices, and partial payments. The engine would surface those as exceptions rather than match them. One-to-many matching is the obvious next step.
- **Duplicate detection needs a reference.** A repeat posting with a blank reference cannot be recognised as a repeat.
- **SQLite dialect.** `julianday()` and `printf()` would become `DATEDIFF` / `FORMAT` (or equivalents) on SQL Server or Postgres. The matching logic itself is standard SQL (CTEs, joins, window functions).

## Stack

SQL (SQLite) for all matching, classification and reporting logic. Python (pandas, openpyxl) to sequence the SQL, generate the data, score the results and write the Excel file. Plain HTML/CSS/JS for the web dashboard.

## Note on the data

All figures are synthetic. Nothing comes from any employer, customer or real ledger. The three systems are generated from one set of 1,500 receipts with realistic noise (different reference formats per system, 0-6 days of posting lag, occasional bank fees, a few blank references), then 128 breaks are planted: missing postings in each system, orphaned records, amount errors (including six small short-pays an over-generous tolerance would absorb), timing gaps of 9-28 days, and duplicate postings.
