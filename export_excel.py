"""
Step 5 - reconciliation_report.xlsx: the same results in the format a reviewer actually works in.

  Summary      settings, population cleared vs. flagged, exceptions by type/severity, review progress.
               Every count and dollar total is a formula over the Exceptions sheet, so it updates as
               the analyst works the queue.
  Exceptions   one row per exception, sorted by severity then exposure, with blank columns for the
               analyst's status and notes.
  Sensitivity  the tolerance sweep behind the default settings.

Blue text = a value taken from the SQL run (an input); black = a formula.
"""
import json
import sqlite3

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from run_reconciliation import DEFAULT_CFG, report

FONT = "Arial"
LABEL = {
    "MISSING_IN_GL": "Missing in GL", "MISSING_IN_SL": "Missing in AR", "MISSING_IN_BANK": "Missing in bank",
    "BANK_ONLY": "Bank credit only", "SL_ONLY": "AR application only", "GL_ONLY": "GL entry only",
    "AMOUNT_MISMATCH": "Amount mismatch", "TIMING": "Timing gap", "DUPLICATE_POSTING": "Duplicate posting",
    "AMBIGUOUS_MATCH": "Ambiguous match",
}
SRC = {"BANK": "Bank statement", "SL": "AR subledger", "GL": "General ledger"}

f_base = Font(name=FONT, size=10)
f_bold = Font(name=FONT, size=10, bold=True)
f_title = Font(name=FONT, size=14, bold=True)
f_input = Font(name=FONT, size=10, color="0000FF")
f_note = Font(name=FONT, size=9, italic=True, color="595959")
f_head = Font(name=FONT, size=10, bold=True, color="FFFFFF")
fill_head = PatternFill("solid", fgColor="1D2733")
fill_edit = PatternFill("solid", fgColor="FFF2CC")
fill_default = PatternFill("solid", fgColor="DCEBE7")
thin = Side(style="thin", color="D8DDE4")
b_bottom = Border(bottom=thin)

USD0 = '$#,##0;($#,##0);-'
USD2 = '$#,##0.00;($#,##0.00);-'
PCT = "0.0%"
INT = '#,##0;(#,##0);-'


def style_range(ws, rng, font=None, fmt=None, align=None, fill=None, border=None):
    for row in ws[rng]:
        for c in row:
            if font: c.font = font
            if fmt: c.number_format = fmt
            if align: c.alignment = align
            if fill: c.fill = fill
            if border: c.border = border


def header(ws, row, labels, col=1):
    for i, text in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=text)
        c.font, c.fill = f_head, fill_head
        c.alignment = Alignment(horizontal="left" if i == 0 else "right", vertical="center", wrap_text=True)


def build():
    con = sqlite3.connect("recon.db")
    rep = report(con)
    exc = pd.read_sql_query("SELECT * FROM exceptions", con)
    con.close()
    with open("data/validation_summary.json") as f:
        val = json.load(f)
    sweep = pd.read_csv("data/tolerance_sensitivity.csv")

    wb = Workbook()
    ws_s = wb.active
    ws_s.title = "Summary"
    ws_e = wb.create_sheet("Exceptions")
    ws_t = wb.create_sheet("Sensitivity")

    # =============================================================== Exceptions
    cols = ["Exception ID", "Type", "Severity", "Found in", "Reference", "Customer", "Bank ID", "AR ID", "GL ID",
            "Bank amount", "AR amount", "GL amount", "Exposure ($)", "Date spread (days)", "Age (days)",
            "Review note", "Analyst status", "Analyst notes"]
    header(ws_e, 1, cols)
    ws_e.row_dimensions[1].height = 30
    n = len(exc)
    last = n + 1
    for i, r in enumerate(exc.itertuples(index=False), start=2):
        vals = [r.exc_id, LABEL[r.exception_type], r.severity.title(), r.systems_present.strip().replace(" ", " + "),
                r.reference or None, r.customer_id, r.bank_id, r.sl_id, r.gl_id,
                r.bank_amount, r.sl_amount, r.gl_amount, r.amount_at_risk, r.date_gap, r.age_days, r.review_note,
                None, None]
        for j, v in enumerate(vals, start=1):
            c = ws_e.cell(row=i, column=j, value=None if pd.isna(v) else v)
            c.font = f_base
            c.border = b_bottom
    style_range(ws_e, f"J2:L{last}", fmt=USD2)
    style_range(ws_e, f"M2:M{last}", fmt=USD2)
    style_range(ws_e, f"N2:O{last}", fmt=INT)
    style_range(ws_e, f"Q2:R{last}", fill=fill_edit)
    ws_e.freeze_panes = "C2"
    ws_e.auto_filter.ref = f"A1:R{last}"
    for col, w in zip("ABCDEFGHIJKLMNOPQR", [11, 18, 10, 14, 11, 10, 10, 10, 10, 13, 13, 13, 14, 11, 9, 70, 14, 34]):
        ws_e.column_dimensions[col].width = w
    ws_e.cell(row=1, column=17).fill = PatternFill("solid", fgColor="7F6000")
    ws_e.cell(row=1, column=18).fill = PatternFill("solid", fgColor="7F6000")
    dv = DataValidation(type="list", formula1='"Open,In review,Resolved,Not an issue"', allow_blank=True)
    ws_e.add_data_validation(dv)
    dv.add(f"Q2:Q{last}")
    for sev, color in (("High", "F4CCCC"), ("Medium", "FCE5CD"), ("Low", "EDEFF2")):
        ws_e.conditional_formatting.add(
            f"C2:C{last}", FormulaRule(formula=[f'$C2="{sev}"'], fill=PatternFill("solid", bgColor=color, fgColor=color)))

    E = lambda col: f"Exceptions!${col}$2:${col}${last}"

    # =============================================================== Summary
    ws = ws_s
    ws["A1"] = "Q1 2026 cash receipts reconciliation: bank, AR subledger and GL"
    ws["A1"].font = f_title
    ws["A2"] = "Synthetic data. Matching was done in SQL (sql/ folder); this workbook reports the result."
    ws["A2"].font = f_note
    ws["A3"] = "Blue = value taken from the SQL run. Black = formula. Yellow cells on the Exceptions sheet (Analyst status / notes) are for the reviewer to fill in."
    ws["A3"].font = f_note

    # -- settings
    r = 5
    ws.cell(row=r, column=1, value="Matching settings used").font = f_bold
    settings = [
        ("Amount tolerance", DEFAULT_CFG["tol_amount"], USD2, "Largest amount difference still treated as a match (bank fees, rounding)."),
        ("Date tolerance (days)", DEFAULT_CFG["tol_days"], "0", "Largest date spread across systems before a match is reported as a timing gap."),
        ("Reference-link window (days)", DEFAULT_CFG["wide_days"], "0", "How far apart two rows with the same reference may be and still be linked."),
        ("High severity from", DEFAULT_CFG["high_amount"], USD0, "Exposure at or above this amount."),
        ("Medium severity from", DEFAULT_CFG["med_amount"], USD0, "Exposure at or above this amount."),
    ]
    for k, (lab, v, fmt, note) in enumerate(settings, start=r + 1):
        ws.cell(row=k, column=1, value=lab).font = f_base
        c = ws.cell(row=k, column=2, value=v); c.font, c.number_format = f_input, fmt
        ws.cell(row=k, column=3, value=note).font = f_note

    # -- population
    r = 12
    ws.cell(row=r, column=1, value="Population: what cleared without review").font = f_bold
    header(ws, r + 1, ["System", "Rows", "Cleared", "For review", "Cleared %"])
    fun = rep["funnel"]
    for k, row in enumerate(fun.itertuples(index=False), start=r + 2):
        ws.cell(row=k, column=1, value=SRC[row.src]).font = f_base
        for col, v in ((2, int(row.total_rows)), (3, int(row.clean_rows))):
            c = ws.cell(row=k, column=col, value=v); c.font, c.number_format = f_input, INT
        c = ws.cell(row=k, column=4, value=f"=B{k}-C{k}"); c.font, c.number_format = f_base, INT
        c = ws.cell(row=k, column=5, value=f"=C{k}/B{k}"); c.font, c.number_format = f_base, PCT
    t = r + 2 + len(fun)
    ws.cell(row=t, column=1, value="All systems").font = f_bold
    for col in "BCD":
        c = ws[f"{col}{t}"]; c.value = f"=SUM({col}{r + 2}:{col}{t - 1})"; c.font, c.number_format = f_bold, INT
    c = ws[f"E{t}"]; c.value = f"=C{t}/B{t}"; c.font, c.number_format = f_bold, PCT
    pop_total_row = t

    # -- by type
    r = t + 3
    ws.cell(row=r, column=1, value="Exceptions by type").font = f_bold
    header(ws, r + 1, ["Type", "Count", "Exposure ($)", "High severity", "Avg age (days)"])
    types = [LABEL[x] for x in rep["by_type"]["exception_type"]]
    first = r + 2
    for k, name in enumerate(types, start=first):
        ws.cell(row=k, column=1, value=name).font = f_base
        c = ws.cell(row=k, column=2, value=f"=COUNTIF({E('B')},A{k})"); c.font, c.number_format = f_base, INT
        c = ws.cell(row=k, column=3, value=f"=SUMIF({E('B')},A{k},{E('M')})"); c.font, c.number_format = f_base, USD0
        c = ws.cell(row=k, column=4, value=f'=COUNTIFS({E("B")},A{k},{E("C")},"High")'); c.font, c.number_format = f_base, INT
        c = ws.cell(row=k, column=5, value=f'=IF(B{k}=0,0,SUMIF({E("B")},A{k},{E("O")})/B{k})'); c.font, c.number_format = f_base, "0"
    lastt = first + len(types) - 1
    tot = lastt + 1
    ws.cell(row=tot, column=1, value="Total").font = f_bold
    for col, fmt in (("B", INT), ("C", USD0), ("D", INT)):
        c = ws[f"{col}{tot}"]; c.value = f"=SUM({col}{first}:{col}{lastt})"; c.font, c.number_format = f_bold, fmt
    c = ws[f"E{tot}"]; c.value = f"=AVERAGE({E('O')})"; c.font, c.number_format = f_bold, "0"
    ws.cell(row=tot + 1, column=1, value="Check: type counts equal rows on Exceptions sheet").font = f_note
    c = ws.cell(row=tot + 1, column=2, value=f'=IF(B{tot}=COUNTA({E("A")}),"OK","CHECK")'); c.font = f_bold
    total_exc_cell = f"B{tot}"
    total_exp_cell = f"C{tot}"

    # -- by severity
    r = tot + 4
    ws.cell(row=r, column=1, value="Exceptions by severity").font = f_bold
    header(ws, r + 1, ["Severity", "Count", "Exposure ($)"])
    for k, sev in enumerate(["High", "Medium", "Low"], start=r + 2):
        ws.cell(row=k, column=1, value=sev).font = f_base
        c = ws.cell(row=k, column=2, value=f"=COUNTIF({E('C')},A{k})"); c.font, c.number_format = f_base, INT
        c = ws.cell(row=k, column=3, value=f"=SUMIF({E('C')},A{k},{E('M')})"); c.font, c.number_format = f_base, USD0

    # -- review progress
    r = r + 6
    ws.cell(row=r, column=1, value="Review progress (driven by the Analyst status column)").font = f_bold
    header(ws, r + 1, ["Status", "Count", "Exposure ($)"])
    statuses = ["Open", "In review", "Resolved", "Not an issue"]
    for k, s in enumerate(statuses, start=r + 2):
        ws.cell(row=k, column=1, value=s).font = f_base
        c = ws.cell(row=k, column=2, value=f"=COUNTIF({E('Q')},A{k})"); c.font, c.number_format = f_base, INT
        c = ws.cell(row=k, column=3, value=f"=SUMIF({E('Q')},A{k},{E('M')})"); c.font, c.number_format = f_base, USD0
    k = r + 2 + len(statuses)
    ws.cell(row=k, column=1, value="Not yet reviewed").font = f_base
    c = ws.cell(row=k, column=2, value=f"={total_exc_cell}-SUM(B{r + 2}:B{k - 1})"); c.font, c.number_format = f_base, INT
    c = ws.cell(row=k, column=3, value=f"={total_exp_cell}-SUM(C{r + 2}:C{k - 1})"); c.font, c.number_format = f_base, USD0
    ws.cell(row=k + 1, column=1,
            value="Example: set Analyst status to Resolved and note 'GL entry reposted' in Analyst notes.").font = f_note

    # -- validation
    r = k + 4
    ws.cell(row=r, column=1, value="Scored against the planted breaks").font = f_bold
    rows = [
        ("Breaks planted in the data", val["seeded_total"], INT, True, "Answer key lives outside the database (ground_truth/)."),
        ("Planted breaks caught as the right type", val["detected_correctly"], INT, True, ""),
        ("Recall", f"=B{r + 2}/B{r + 1}", PCT, False, ""),
        ("Exceptions raised", f"={total_exc_cell}", INT, False, "Linked to the count above."),
        ("Exceptions that were not planted breaks", val["false_exceptions"], INT, True,
         "Both come from one receipt with a blank bank reference and several same-amount candidates."),
        ("Precision", f"=1-B{r + 5}/B{r + 4}", PCT, False, ""),
    ]
    for k2, (lab, v, fmt, is_input, note) in enumerate(rows, start=r + 1):
        ws.cell(row=k2, column=1, value=lab).font = f_base
        c = ws.cell(row=k2, column=2, value=v); c.font, c.number_format = (f_input if is_input else f_base), fmt
        if note: ws.cell(row=k2, column=3, value=note).font = f_note

    ws.column_dimensions["A"].width = 44
    for col in "BCDE":
        ws.column_dimensions[col].width = 15
    ws.column_dimensions["C"].width = 15
    ws.sheet_view.showGridLines = False

    # =============================================================== Sensitivity
    w = ws_t
    w["A1"] = "Tolerance sensitivity: what each setting does to the exception queue"; w["A1"].font = f_title
    w["A2"] = ("Source: validate_against_ground_truth.py re-runs the SQL pipeline at each setting and scores it against the "
               "planted breaks. Values are outputs of that run (blue). Highlighted row = settings used.")
    w["A2"].font = f_note
    header(w, 4, ["Amount tol.", "Date tol. (days)", "Rows cleared", "Exceptions", "Breaks missed", "False alarms",
                  "Recall", "Precision"])
    w.row_dimensions[4].height = 30
    seeded_total = val["seeded_total"]
    for i, r_ in enumerate(sweep.itertuples(index=False), start=5):
        missed = int(round(seeded_total - r_.recall * seeded_total))
        vals = [r_.tol_amount, int(r_.tol_days), r_.auto_match_rate, int(r_.exceptions), missed, int(r_.false_exceptions)]
        fmts = [USD2, "0", PCT, INT, INT, INT]
        for j, (v, fm) in enumerate(zip(vals, fmts), start=1):
            c = w.cell(row=i, column=j, value=v); c.font, c.number_format = f_input, fm
        c = w.cell(row=i, column=7, value=f"=({seeded_total}-E{i})/{seeded_total}"); c.font, c.number_format = f_base, PCT
        c = w.cell(row=i, column=8, value=f"=IF(D{i}=0,1,1-F{i}/D{i})"); c.font, c.number_format = f_base, PCT
        if bool(r_.is_default):
            for j in range(1, 9):
                w.cell(row=i, column=j).fill = fill_default
    lastw = 4 + len(sweep)
    w.cell(row=lastw + 2, column=1, value=(
        "Reading it: at $0.00 every bank fee becomes an amount mismatch; at $5.00 the small short-pays are absorbed. "
        "At 2 days normal posting lag floods the queue; at 10 days genuine timing gaps are absorbed.")).font = f_note
    w.cell(row=lastw + 3, column=1, value=(
        "Caveat: the 7-day knee reflects the synthetic posting lag (at most 6 days). With real data, set the date tolerance "
        "from the observed spread on cleanly matched items.")).font = f_note
    for col, wd in zip("ABCDEFGH", [13, 15, 13, 12, 13, 12, 10, 10]):
        w.column_dimensions[col].width = wd
    w.freeze_panes = "A5"

    wb.save("reconciliation_report.xlsx")
    print(f"wrote reconciliation_report.xlsx ({n} exceptions, {len(sweep)} sensitivity rows)")


if __name__ == "__main__":
    build()
