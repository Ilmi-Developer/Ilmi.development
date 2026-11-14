"""Finance report exports: CSV, Excel and PDF, all from one table (table()), so every format shows the same
figures as the report's page. Amounts are pence in the table; CSV and PDF show them as £1,250.00 and Excel keeps
them as numbers in a £ format, so they can be added up there.
"""
import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

import finance_books as books


class Money(int):
    """An amount in pence, in a table cell."""


def _row(cells, style=""):
    return {"cells": list(cells), "style": style}


def table(r):
    """The report r (finance.report_data) as {title, subtitle, columns, rows, notes}."""
    kind, p, d = r["kind"], r["period"], r.get("data")
    compare = r.get("compare")
    out = {"title": r["title"], "subtitle": p.label, "columns": [], "rows": [], "notes": [],
           "made": r["made"].strftime("%-d %b %Y %H:%M")}
    rows = out["rows"]
    if kind == "income-expenditure":
        out["title"] = "Income & Expenditure (Profit & Loss)"
        out["columns"] = ["", p.label] + ([compare.label, "Change"] if compare else [])

        def line(label, now, before, change, style=""):
            cells = [label, Money(now)]
            if compare:
                cells += [Money(before or 0), "" if change is None else f"{change:+.1f}%"]
            rows.append(_row(cells, style))
        rows.append(_row(["Income"], "head"))
        for x in d["income"]:
            line(x["label"], x["total"], x["previous"], x["change"])
            for name, value in x["accounts"]:
                rows.append(_row([f"   {name}", Money(value)] + (["", ""] if compare else []), "indent"))
        line("Total income", d["total_income"], d["prev_income"], d["change_income"], "total")
        rows.append(_row(["Expenditure"], "head"))
        for x in d["spending"]:
            line(x["label"], x["total"], x["previous"], x["change"])
        line("Total expenditure", d["total_spending"], d["prev_spending"], d["change_spending"], "total")
        line("Net surplus" if d["net"] >= 0 else "Net deficit", d["net"], d["prev_net"], d["change_net"], "grand")
        out["notes"].append("A management report from the madrasah's own records, not statutory accounts.")
    elif kind == "balance-sheet":
        out["subtitle"] = f"On {books.nice_day(r['on'])}"
        out["columns"] = ["", "Amount"]
        rows.append(_row(["What the madrasah owns (assets)"], "head"))
        rows += [_row([x["label"], Money(x["amount"])]) for x in d["assets"]]
        rows.append(_row(["Total assets", Money(d["total_assets"])], "total"))
        rows.append(_row(["What the madrasah owes (liabilities)"], "head"))
        rows += [_row([x["label"], Money(x["amount"])]) for x in d["liabilities"]]
        rows.append(_row(["Total liabilities", Money(d["total_liabilities"])], "total"))
        rows.append(_row(["Net assets", Money(d["net_assets"])], "grand"))
        rows.append(_row(["Funds"], "head"))
        rows.append(_row(["Accumulated funds brought forward", Money(d["accumulated"])]))
        rows.append(_row(["Surplus or deficit this financial year", Money(d["current_surplus"])]))
        rows.append(_row(["   of which restricted funds", Money(d["restricted"])], "indent"))
        rows.append(_row(["   of which unrestricted funds", Money(d["unrestricted"])], "indent"))
        rows.append(_row(["Total funds", Money(d["total_funds"])], "grand"))
        out["notes"].append("Assets less liabilities equal total funds." if d["balances"] else
                            f"These don't balance by {books.gbp(d['difference'])}. Contact support: the books need checking.")
        if not d["has_opening"]:
            out["notes"].append("No opening balances are recorded, so money held before the books started isn't included.")
    elif kind == "cash-flow":
        out["columns"] = ["", "Amount"]
        rows.append(_row(["Opening cash and bank balance", Money(d["opening"])], "total"))
        for x in d["lines"]:
            if x["amount"] or x["key"] != "opening":
                rows.append(_row([x["label"], Money(x["amount"])]))
        rows.append(_row(["Closing cash and bank balance", Money(d["closing"])], "grand"))
        rows.append(_row([""]))
        rows.append(_row(["Fees billed in this period", Money(d["fees_billed"])]))
        rows.append(_row(["Fee payments actually received", Money(d["fees_received"])]))
        out["notes"].append("Only money that actually moved. Fees billed but not yet paid aren't cash.")
    elif kind == "budget":
        if not d:
            out["notes"].append("No budget yet.")
        else:
            out["subtitle"] = f"{d['name']}: {books.nice_day(d['start'])} to {books.nice_day(d['end'])}"
            out["columns"] = ["", "Budget", "Actual", "Variance", "Variance %", "Remaining"]
            for kind_, head in (("income", "Income"), ("expense", "Expenditure")):
                rows.append(_row([head], "head"))
                for x in (x for x in d["rows"] if x["kind"] == kind_):
                    rows.append(_row([x["name"], Money(x["budget"]), Money(x["actual"]), Money(x["variance"]),
                                      "" if x["pct"] is None else f"{x['pct']:+.1f}%",
                                      "" if x["remaining"] is None else Money(x["remaining"])],
                                     "alert" if x["attention"] else ""))
    elif kind == "fees":
        if r["by_family"]:
            out["columns"] = ["Family ID", "Students", "Billed", "Paid", "Outstanding", "Overdue"]
            for f in r["families"]:
                rows.append(_row([f["family"], f["students"], Money(f["net"]), Money(f["received"]),
                                  Money(f["outstanding"]), Money(f["overdue"])]))
        else:
            out["columns"] = ["Student", "Family ID", "Class", "Fee", "Billed", "Paid", "Outstanding", "Due", "Status"]
            for i in r["rows"]:
                rows.append(_row([i["student_name"], i["family_id"], i["class_name"], i["description"], Money(i["net"]),
                                  Money(i["received"]), Money(i["outstanding"]), books.nice_day(i["due_day"]),
                                  i["status_label"]], "alert" if i["status"] == "overdue" else ""))
        rows.append(_row(["Total"] + [""] * (len(out["columns"]) - 1), "total"))
        money_cols = [n for n, c in enumerate(out["columns"]) if c in ("Billed", "Paid", "Outstanding", "Overdue")]
        for n in money_cols:
            rows[-1]["cells"][n] = Money(sum(int(x["cells"][n]) for x in rows[:-1] if isinstance(x["cells"][n], int)))
        out["subtitle"] = {"outstanding": "Fees still owed", "overdue": "Overdue fees"}.get(r["show"], "All fees")
    elif kind == "funds":
        out["columns"] = ["Fund", "Type", "Brought forward", "Money in", "Money out", "Balance"]
        for f in d:
            bf = r["before"].get(f["id"], 0) + f["brought_forward"]
            if bf or f["income"] or f["spending"]:
                rows.append(_row([f["name"], "Restricted" if f["restricted"] else "Unrestricted", Money(bf),
                                  Money(f["income"]), Money(f["spending"]), Money(bf + f["income"] - f["spending"])]))
    elif kind == "sofa":
        out["columns"] = ["", "Unrestricted funds", "Restricted funds", "Total"]

        def three(label, x, style=""):
            rows.append(_row([label, Money(x["unrestricted"]), Money(x["restricted"]), Money(x["total"])], style))
        rows.append(_row(["Income from"], "head"))
        for x in d["income"]:
            three(x["label"], x)
        three("Total income", d["total_income"], "total")
        rows.append(_row(["Expenditure on"], "head"))
        for x in d["spending"]:
            three(x["label"], x)
        three("Total expenditure", d["total_spending"], "total")
        three("Net income / (expenditure)", d["net"], "total")
        three("Total funds brought forward", d["brought_forward"])
        three("Total funds carried forward", d["carried_forward"], "grand")
        out["notes"].append("A draft in the SoFA layout from the madrasah's records. Statutory accounts must follow the "
                            "Charities SORP and be independently examined or audited where the law requires it.")
    elif kind == "receipts-payments":
        out["columns"] = ["", "Amount"]
        rows.append(_row(["Receipts"], "head"))
        rows += [_row([k, Money(v)]) for k, v in d["receipts"]]
        rows.append(_row(["Total receipts", Money(d["total_receipts"])], "total"))
        rows.append(_row(["Payments"], "head"))
        rows += [_row([k, Money(v)]) for k, v in d["payments"]]
        rows.append(_row(["Total payments", Money(d["total_payments"])], "total"))
        rows.append(_row(["Cash and bank at the start", Money(d["opening"])]))
        rows.append(_row(["Cash and bank at the end", Money(d["closing"])], "grand"))
        out["notes"].append("A draft receipts and payments account from the madrasah's records, not statutory accounts.")
    return out


def _text(cell):
    return books.gbp(cell) if isinstance(cell, Money) else str(cell)


def to_csv(t):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([t["title"]])
    w.writerow([t["subtitle"]])
    w.writerow([])
    if t["columns"]:
        w.writerow(t["columns"])
    for r in t["rows"]:
        # Amounts as plain numbers (1250.00) so a spreadsheet can add them up; a cell starting with = + - @ is
        # quoted so a spreadsheet can't read it as a formula.
        w.writerow([f"{int(c) / 100:.2f}" if isinstance(c, Money) else _safe(str(c)) for c in r["cells"]])
    for n in t["notes"]:
        w.writerow([])
        w.writerow([_safe(n)])
    return "﻿" + buf.getvalue()


def _safe(text):
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_xlsx(t):
    wb = Workbook()
    ws = wb.active
    ws.title = t["title"][:31]
    ws.append([t["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([t["subtitle"]])
    ws.append([])
    if t["columns"]:
        ws.append(t["columns"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="EDE6D6")
    for r in t["rows"]:
        ws.append([int(c) / 100 if isinstance(c, Money) else _safe(str(c)) for c in r["cells"]])
        for n, c in enumerate(r["cells"]):
            cell = ws.cell(row=ws.max_row, column=n + 1)
            if isinstance(c, Money):
                cell.number_format = '"£"#,##0.00;[Red]-"£"#,##0.00'
                cell.alignment = Alignment(horizontal="right")
            if r["style"] in ("head", "total", "grand"):
                cell.font = Font(bold=True)
    for n in t["notes"]:
        ws.append([])
        ws.append([n])
    ws.column_dimensions["A"].width = 44
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(t, school=""):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    wide = len(t["columns"]) > 4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4) if wide else A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=t["title"], author=school or "Ilmi")
    styles = getSampleStyleSheet()
    olive, line, sunk = colors.HexColor("#4d5a2c"), colors.HexColor("#cfc5ae"), colors.HexColor("#ede6d6")
    story = [Paragraph(school or "", styles["Normal"]), Paragraph(t["title"], styles["Title"]),
             Paragraph(t["subtitle"], styles["Normal"]), Spacer(1, 6 * mm)]
    data = ([t["columns"]] if t["columns"] else []) + [[_text(c) for c in r["cells"]] for r in t["rows"]]
    if data:
        width = max(len(r) for r in data)
        data = [r + [""] * (width - len(r)) for r in data]
        table = Table(data, repeatRows=1 if t["columns"] else 0, hAlign="LEFT")
        style = [("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.25, line), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        if t["columns"]:
            style += [("BACKGROUND", (0, 0), (-1, 0), sunk), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9)]
        start = 1 if t["columns"] else 0
        for n, r in enumerate(t["rows"], start):
            if r["style"] in ("head", "total", "grand"):
                style.append(("FONT", (0, n), (-1, n), "Helvetica-Bold", 9))
            if r["style"] == "grand":
                style += [("LINEABOVE", (0, n), (-1, n), 1, olive), ("TEXTCOLOR", (0, n), (-1, n), olive)]
            if r["style"] == "alert":
                style.append(("TEXTCOLOR", (0, n), (-1, n), colors.HexColor("#8a2a1c")))
        table.setStyle(TableStyle(style))
        story.append(table)
    for n in t["notes"]:
        story += [Spacer(1, 4 * mm), Paragraph(n, styles["Italic"])]
    story += [Spacer(1, 6 * mm), Paragraph(f"Made in Ilmi on {t['made']}", styles["Normal"])]
    doc.build(story)
    return buf.getvalue()
