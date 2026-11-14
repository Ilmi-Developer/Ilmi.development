"""Finance: the pages a Finance account signs in to.

A Finance account (role "finance", given on Manage users) belongs to one school, like every account. It has the
Finance pages here and its Account, and nothing else: no classes, students, records or Administration pages
(server.py keeps it to FINANCE_PATHS). Nobody else has the Finance pages, so the role is the permission: only the
people an admin makes Finance accounts can see or change the school's money, and the developer only when viewing a
school as Finance. Each school's books are in its own database (schools.py), so no school can see another's.

The pages: Overview, Student fees, Income & expenses, Staff payments, Donations & funds, Reports, Budgets and
Settings. The books themselves (the ledger, and every sum the reports show) are in finance_books.py; exports in
finance_export.py. Pages are built from the same parts as the rest of Ilmi: the page band (.band), figures
(.figs), cards (.mod), rounded tables (.tablewrap), "How this works" help (.how) and confirm pop-ups (.confirm),
in the shared tokens (src/tokens.css, DESIGN.md).
"""
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, g, request

import auth
import finance_books as books
import finance_export

bp = Blueprint("finance", __name__)

# Where a Finance account may go. Everything else sends it back to its page (or answers 403 for a form or the API).
FINANCE_PATHS = ("/finance", "/account", "/logout", "/avatar/", "/theme.css", "/theme-photo/", "/developer.css")

SECTIONS = (("/finance", "Overview"), ("/finance/fees", "Student fees"), ("/finance/money", "Income & expenses"),
            ("/finance/staff", "Staff payments"), ("/finance/donations", "Donations & funds"),
            ("/finance/reports", "Reports"), ("/finance/budgets", "Budgets"), ("/finance/settings", "Settings"))

REPORTS = (
    ("income-expenditure", "Income & Expenditure", "Profit & Loss: what came in and what was spent, by category."),
    ("balance-sheet", "Balance sheet", "What the madrasah owns and owes on a day, and its funds."),
    ("cash-flow", "Cash flow", "Money that actually came in and went out, from opening to closing balance."),
    ("budget", "Budget vs actual", "Each budget line against what really happened."),
    ("fees", "Outstanding student fees", "Every fee billed, paid and still owed, by student or by family."),
    ("funds", "Fund movements", "Each fund's balance brought forward, money in, money out and balance now."),
    ("sofa", "Statement of Financial Activities", "For charities: income and spending in restricted and unrestricted funds."),
    ("receipts-payments", "Receipts and payments", "For smaller charities: money received and paid, by category."))
REPORT_NAMES = {k: name for k, name, _ in REPORTS}
CHARITY_REPORTS = ("sofa", "receipts-payments")


def finance_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or g.user["role"] != "finance":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@bp.app_template_filter("gbp")
def gbp_filter(pence, signed=False):
    return books.gbp(pence, signed)


@bp.app_template_filter("nice_day")
def nice_day_filter(value):
    return books.nice_day(value) if value else ""


def today():
    return datetime.now(auth.LONDON).date()


def _render(template, status=200, **context):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
    return auth.page(template, status, sections=SECTIONS, here=request.path, settings=s, today=today(),
                     methods=books.METHODS, **context)


def _period(s):
    p = books.period_from(request.args, s["year_start"], today())
    compare = books.previous(p, s["year_start"])
    return p, compare


def _period_args(p):
    """The query that chooses this period again, for links and exports."""
    if p.kind == "custom":
        return {"period": "custom", "start": p.start.isoformat(), "end": p.end.isoformat()}
    return {"period": p.kind, "on": p.start.isoformat()}


def _shifted(p, s, forward):
    """The period before or after this one, as a query."""
    if forward:
        nxt = books.period_from({"period": p.kind, "on": (p.end + timedelta(days=1)).isoformat()}, s["year_start"], today()) \
            if p.kind != "custom" else books.Period("custom", p.end + timedelta(days=1), p.end + timedelta(days=p.days), "")
        return urlencode(_period_args(nxt))
    return urlencode(_period_args(books.previous(p, s["year_start"])))


def _form(key, default=""):
    return request.form.get(key, default).strip()


def _done(view, notice=None, error=None, status=None):
    return view(status=status or (400 if error else 200), notice=notice, error=error)


def _record(action, do, view, notice):
    """Runs one change in a transaction with its audit entry. A Problem is shown on the page, and nothing is saved."""
    auth.check_form_token()
    try:
        with auth.db().transaction() as tx:
            detail = do(tx)
            auth.audit(tx, action, detail=detail)
    except books.Problem as problem:
        return _done(view, error=str(problem))
    return _done(view, notice=notice(detail) if callable(notice) else notice)


def _who():
    return {"user_id": g.user["id"], "at": auth.now()}


# --- Overview ---------------------------------------------------------------------------------------------------

@bp.get("/finance")
@finance_required
def page(status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        p, before = _period(s)
        data = books.overview(tx, p, today())
        # A period still running is compared with the same stretch of the one before, not all of it.
        if p.start <= today() < p.end:
            upto = min(before.end, before.start + (today() - p.start))
            before = books.Period("custom", before.start, upto, f"this point in {before.label}")
        prev = books.income_expenditure(tx, before)
    return _render("finance.html", status, notice=notice, error=error, period=p, data=data, before=before, prev=prev,
                   change=books.change_pct,
                   q=urlencode(_period_args(p)), prev_q=_shifted(p, s, False), next_q=_shifted(p, s, True))


# --- Student fees -----------------------------------------------------------------------------------------------

@bp.get("/finance/fees")
@finance_required
def fees(status=200, notice=None, error=None):
    # Fees still owed first: that is what needs doing. "all" shows every fee, newest first.
    show = request.args.get("show", "outstanding")
    family = request.args.get("family", "").strip()
    with auth.db().transaction() as tx:
        rows = books.invoices(tx, today(), status=show if show in ("outstanding", "overdue") else None,
                              family=family or None)
        everyone = books.invoices(tx, today())
        kids = books.students(tx)
    rows.sort(key=lambda i: (i["status"] != "overdue", i["due_day"]) if show != "all" else (i["due_day"], i["id"]),
              reverse=show == "all")
    classes = sorted({(k["class_id"], k["class_name"]) for k in kids}, key=lambda c: c[1].lower())
    return _render("finance_fees.html", status, notice=notice, error=error, rows=rows, show=show, family=family,
                   by_family=request.args.get("view") == "family", families=books.family_totals(rows),
                   students=kids, classes=classes,
                   totals={"net": sum(i["net"] for i in everyone), "received": sum(i["received"] for i in everyone),
                           "outstanding": sum(max(i["outstanding"], 0) for i in everyone),
                           "overdue": sum(max(i["outstanding"], 0) for i in everyone if i["status"] == "overdue")})


def _bill_fields():
    amount = books.pence(_form("amount"), "Fee")
    off = books.pence(_form("discount"), "Discount", allow_zero=True)
    issued = books.day_of(_form("issued"), "day billed", default=today())
    due = books.day_of(_form("due"), "due date", default=issued)
    raw = _form("instalments") or "1"
    if not raw.isdigit():
        raise books.Problem("Instalments must be a whole number from 1 to 12.")
    return amount, off, issued, due, int(raw)


@bp.post("/finance/fees/bill")
@finance_required
def bill():
    def do(tx):
        key = _form("student")
        student = next((s for s in books.students(tx) if s["key"] == key), None)
        if not student:
            raise books.Problem("Choose a student.")
        amount, off, issued, due, n = _bill_fields()
        ids = books.bill_student(tx, student, _form("description"), amount, off, issued, due, n, **_who())
        return {"student": student["name"], "amount": amount, "discount": off, "instalments": n, "invoices": ids}
    return _record("finance.billed", do, fees, lambda d: f"Billed {d['student']} {books.gbp(d['amount'] - d['discount'])}.")


@bp.post("/finance/fees/bill-class")
@finance_required
def bill_class():
    def do(tx):
        class_id = _form("class")
        kids = [s for s in books.students(tx) if s["class_id"] == class_id]
        if not kids:
            raise books.Problem("Choose a class with students in it.")
        amount, off, issued, due, n = _bill_fields()
        ids = []
        for student in kids:
            ids += books.bill_student(tx, student, _form("description"), amount, off, issued, due, n, **_who())
        return {"class": kids[0]["class_name"], "students": len(kids), "amount": amount, "discount": off, "invoices": ids}
    return _record("finance.billed", do, fees,
                   lambda d: f"Billed {d['students']} students in {d['class']} {books.gbp(d['amount'] - d['discount'])} each.")


@bp.get("/finance/fees/<int:invoice_id>")
@finance_required
def fee(invoice_id, status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        inv = books.invoice(tx, invoice_id, today())
        if not inv:
            abort(404)
        history = books.invoice_history(tx, invoice_id)
    return _render("finance_fee.html", status, notice=notice, error=error, inv=inv, history=history, kinds=books.KINDS)


def _fee_view(invoice_id):
    return lambda **kw: fee(invoice_id, **kw)


@bp.post("/finance/fees/<int:invoice_id>/pay")
@finance_required
def pay(invoice_id):
    def do(tx):
        amount = books.pence(_form("amount"))
        books.receive_payment(tx, invoice_id, amount, books.day_of(_form("day"), "day paid", default=today()),
                              _form("method"), memo=_form("memo"), **_who())
        return {"invoice": invoice_id, "amount": amount, "method": _form("method")}
    return _record("finance.payment", do, _fee_view(invoice_id), lambda d: f"Recorded a payment of {books.gbp(d['amount'])}.")


@bp.post("/finance/fees/<int:invoice_id>/discount")
@finance_required
def discount(invoice_id):
    def do(tx):
        amount = books.pence(_form("amount"))
        books.discount(tx, invoice_id, amount, books.day_of(_form("day"), "date", default=today()), _form("reason"), **_who())
        return {"invoice": invoice_id, "amount": amount, "reason": _form("reason")}
    return _record("finance.discount", do, _fee_view(invoice_id), lambda d: f"Took {books.gbp(d['amount'])} off this fee.")


@bp.post("/finance/fees/<int:invoice_id>/refund")
@finance_required
def refund(invoice_id):
    def do(tx):
        amount = books.pence(_form("amount"))
        books.refund(tx, invoice_id, amount, books.day_of(_form("day"), "date", default=today()), _form("method"),
                     _form("reason"), **_who())
        return {"invoice": invoice_id, "amount": amount, "reason": _form("reason")}
    return _record("finance.refund", do, _fee_view(invoice_id), lambda d: f"Recorded a refund of {books.gbp(d['amount'])}.")


# --- Corrections ------------------------------------------------------------------------------------------------

BACK = {"fees": "/finance/fees", "money": "/finance/money", "staff": "/finance/staff", "donations": "/finance/donations",
        "settings": "/finance/settings"}


@bp.post("/finance/entries/<int:entry_id>/reverse")
@finance_required
def reverse(entry_id):
    back = _form("back")
    if back.startswith("fee:") and back[4:].isdigit():
        view = _fee_view(int(back[4:]))
    else:
        view = {"fees": fees, "money": money, "staff": staff, "donations": donations, "settings": settings_page}.get(back, page)

    def do(tx):
        original = books.entry(tx, entry_id)
        when = books.day_of(_form("day"), "date of the correction",
                            default=date.fromisoformat(original["day"]) if original else today())
        new = books.reverse(tx, entry_id, when, _form("reason"), **_who())
        return {"entry": entry_id, "correction": new, "reason": _form("reason")}
    return _record("finance.corrected", do, view, "Corrected. The original and the correction are both kept in the history.")


# --- Income & expenses ------------------------------------------------------------------------------------------

def _list_period(s):
    p, _ = _period(s)
    return p


@bp.get("/finance/money")
@finance_required
def money(status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        p = _list_period(s)
        rows = books.transactions(tx, p.start, p.end, kinds=("income", "expense", "bill", "bill_payment", "reversal"))
        rows = [r for r in rows if r["kind"] != "reversal" or set(r["groups"]) - {"cash", "receivables", "salaries", "payroll"}]
        data = {"in_accounts": [a for a in books.accounts(tx, ("income",)) if a["grp"] != "tuition"],
                "out_accounts": [a for a in books.accounts(tx, ("expense", "asset")) if a["kind"] == "expense" or a["grp"] == "equipment"],
                "funds": books.funds(tx), "bills": [b for b in books.unpaid_bills(tx) if b["kind"] == "bill"]}
    return _render("finance_money.html", status, notice=notice, error=error, rows=rows, period=p, **data,
                   q=urlencode(_period_args(p)), prev_q=_shifted(p, s, False), next_q=_shifted(p, s, True))


@bp.post("/finance/money/in")
@finance_required
def money_in():
    def do(tx):
        amount = books.pence(_form("amount"))
        books.money_in(tx, _form("account"), _form("fund"), amount, books.day_of(_form("day"), "date", default=today()),
                       _form("method"), _form("party"), _form("memo"), **_who())
        return {"amount": amount, "account": _form("account")}
    return _record("finance.money_in", do, money, lambda d: f"Recorded {books.gbp(d['amount'])} coming in.")


@bp.post("/finance/money/out")
@finance_required
def money_out():
    def do(tx):
        amount = books.pence(_form("amount"))
        paid = _form("paid", "yes") != "no"
        books.money_out(tx, _form("account"), _form("fund"), amount, books.day_of(_form("day"), "date", default=today()),
                        paid, _form("method"), _form("party"), _form("memo"), **_who())
        return {"amount": amount, "account": _form("account"), "paid": paid}
    return _record("finance.money_out", do, money,
                   lambda d: f"Recorded {books.gbp(d['amount'])} going out." if d["paid"] else f"Recorded a bill for {books.gbp(d['amount'])}.")


@bp.post("/finance/bills/<int:bill_id>/pay")
@finance_required
def pay_bill(bill_id):
    view = staff if _form("back") == "staff" else money

    def do(tx):
        amount = books.pence(_form("amount"))
        books.pay_bill(tx, bill_id, amount, books.day_of(_form("day"), "date", default=today()), _form("method"), **_who())
        return {"bill": bill_id, "amount": amount}
    return _record("finance.bill_paid", do, view, lambda d: f"Paid {books.gbp(d['amount'])}.")


# --- Staff payments ---------------------------------------------------------------------------------------------

@bp.get("/finance/staff")
@finance_required
def staff(status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        p = _list_period(s)
        rows = books.transactions(tx, p.start, p.end, groups=("salaries", "payroll"))
        names = [r["name"] for r in tx.execute("SELECT name FROM users WHERE disabled = 0 AND developer = 0 AND role IN "
                                                "('admin', 'head', 'teacher') ORDER BY name").fetchall()]
        owed = [b for b in books.unpaid_bills(tx) if b["kind"] == "staff_owed"]
        f = books.funds(tx)
    people = {}
    for r in rows:
        if r["kind"] in ("staff", "staff_owed") or (r["kind"] == "reversal" and "salaries" in r["groups"]):
            people[r["party"]] = people.get(r["party"], 0) - r["effect"]
    return _render("finance_staff.html", status, notice=notice, error=error, rows=rows, period=p, names=names,
                   owed=owed, funds=f, people=sorted(people.items(), key=lambda x: -x[1]),
                   q=urlencode(_period_args(p)), prev_q=_shifted(p, s, False), next_q=_shifted(p, s, True))


@bp.post("/finance/staff/pay")
@finance_required
def staff_pay():
    def do(tx):
        amount = books.pence(_form("amount"))
        paid = _form("paid", "yes") != "no"
        salaries = books.account_by_code(tx, books.SALARIES)["id"]
        books.money_out(tx, salaries, _form("fund"), amount, books.day_of(_form("day"), "date", default=today()),
                        paid, _form("method"), _form("party"), _form("memo") or "Salary", staff=True, **_who())
        return {"amount": amount, "party": _form("party"), "paid": paid}
    return _record("finance.staff", do, staff,
                   lambda d: f"Recorded {books.gbp(d['amount'])} for {d['party']}." if d["paid"] else f"Recorded {books.gbp(d['amount'])} owed to {d['party']}.")


# --- Donations & funds ------------------------------------------------------------------------------------------

@bp.get("/finance/donations")
@finance_required
def donations(status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        p = _list_period(s)
        rows = books.transactions(tx, p.start, p.end, groups=("donations", "fundraising", "grants"))
        balances = books.fund_balances(tx, today())
        gift = [a for a in books.accounts(tx, ("income",)) if a["grp"] in ("donations", "fundraising", "grants")]
    return _render("finance_donations.html", status, notice=notice, error=error, rows=rows, period=p, funds=balances,
                   gift_accounts=gift, q=urlencode(_period_args(p)), prev_q=_shifted(p, s, False), next_q=_shifted(p, s, True))


@bp.post("/finance/donations")
@finance_required
def donate():
    def do(tx):
        amount = books.pence(_form("amount"))
        books.money_in(tx, _form("account"), _form("fund"), amount, books.day_of(_form("day"), "date", default=today()),
                       _form("method"), _form("party"), _form("memo"), **_who())
        return {"amount": amount, "fund": _form("fund")}
    return _record("finance.donation", do, donations, lambda d: f"Recorded a gift of {books.gbp(d['amount'])}.")


@bp.post("/finance/funds")
@finance_required
def add_fund():
    def do(tx):
        fid = books.add_fund(tx, _form("name"), _form("restricted") == "1", _form("purpose"), auth.now())
        return {"fund": fid, "name": _form("name"), "restricted": _form("restricted") == "1"}
    return _record("finance.fund", do, donations, lambda d: f"Added the fund {d['name']}.")


# --- Reports ----------------------------------------------------------------------------------------------------

@bp.get("/finance/reports")
@finance_required
def reports():
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        has = books.has_entries(tx)
    shown = [r for r in REPORTS if r[0] not in CHARITY_REPORTS or s["is_charity"]]
    return _render("finance_reports.html", reports=shown, has_entries=has)


def report_data(kind, tx, s):
    """Everything a report shows, for its page and its exports."""
    p, compare = _period(s)
    compare_on = request.args.get("compare", "1") != "0"
    out = {"kind": kind, "title": REPORT_NAMES[kind], "period": p, "compare": compare if compare_on else None,
           "settings": s, "made": datetime.now(auth.LONDON)}
    if kind == "income-expenditure":
        out["data"] = books.income_expenditure(tx, p, compare if compare_on else None)
        out["detail"] = {k: books.transactions(tx, p.start, p.end, groups=(k,))
                         for k in books.INCOME_GROUPS + books.EXPENSE_GROUPS}
    elif kind == "balance-sheet":
        on = books.day_of(request.args.get("on", ""), default=min(today(), p.end)) if request.args.get("period") != "custom" else p.end
        out["on"] = on
        out["data"] = books.balance_sheet(tx, on, s["year_start"])
        out["data_before"] = books.balance_sheet(tx, compare.end, s["year_start"]) if compare_on else None
    elif kind == "cash-flow":
        out["data"] = books.cash_flow(tx, p)
    elif kind == "budget":
        all_budgets = books.budgets(tx)
        chosen = request.args.get("budget") or (str(all_budgets[0]["id"]) if all_budgets else "")
        out["budgets"], out["data"] = all_budgets, books.budget_vs_actual(tx, chosen) if chosen else None
    elif kind == "fees":
        show = request.args.get("show", "")
        rows = books.invoices(tx, today(), status=show if show in ("outstanding", "overdue") else None)
        out["show"], out["rows"], out["families"] = show, rows, books.family_totals(rows)
        out["by_family"] = request.args.get("view") == "family"
    elif kind == "funds":
        out["data"] = books.fund_balances(tx, p.end, p.start)
        out["before"] = {f["id"]: f["balance"] for f in books.fund_balances(tx, p.start - timedelta(days=1))}
    elif kind == "sofa":
        out["data"] = books.sofa(tx, p)
    elif kind == "receipts-payments":
        out["data"] = books.receipts_payments(tx, p)
    return out


@bp.get("/finance/reports/<kind>")
@finance_required
def report(kind):
    if kind not in REPORT_NAMES:
        abort(404)
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        if kind in CHARITY_REPORTS and not s["is_charity"]:
            abort(404)
        r = report_data(kind, tx, s)
        problems = books.check(tx)
        has = books.has_entries(tx)
    p = r["period"]
    q = dict(request.args)
    return _render("finance_report.html", r=r, problems=problems, has_entries=has, q=urlencode(q),
                   prev_q=_shifted(p, s, False), next_q=_shifted(p, s, True), args=request.args,
                   charity=kind in CHARITY_REPORTS)


@bp.get("/finance/reports/<kind>.<fmt>")
@finance_required
def report_export(kind, fmt):
    if kind not in REPORT_NAMES or fmt not in ("csv", "xlsx", "pdf"):
        abort(404)
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        if kind in CHARITY_REPORTS and not s["is_charity"]:
            abort(404)
        r = report_data(kind, tx, s)
        auth.audit(tx, "finance.exported", detail={"report": kind, "format": fmt, "start": r["period"].start.isoformat(),
                                                    "end": r["period"].end.isoformat()})
    sheet = finance_export.table(r)
    name = f"{kind}-{r['period'].start.isoformat()}-to-{r['period'].end.isoformat()}"
    if fmt == "csv":
        body, mime = finance_export.to_csv(sheet), "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        body, mime = finance_export.to_xlsx(sheet), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        school = s["legal_name"] or auth.db().name(auth.db().current_id()) or ""
        body, mime = finance_export.to_pdf(sheet, school), "application/pdf"
    return Response(body, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"',
                                                  "Cache-Control": "no-store"})


# --- Budgets ----------------------------------------------------------------------------------------------------

def _budget_amounts(tx):
    out = {}
    for a in books.accounts(tx, ("income", "expense")):
        out[a["id"]] = books.pence(_form(f"a{a['id']}"), a["name"], allow_zero=True)
    return out


@bp.get("/finance/budgets")
@finance_required
def budgets(status=200, notice=None, error=None, budget_id=None):
    with auth.db().transaction() as tx:
        s = books.settings(tx)
        all_budgets = books.budgets(tx)
        chosen = budget_id or request.args.get("budget") or (str(all_budgets[0]["id"]) if all_budgets else None)
        data = books.budget_vs_actual(tx, chosen) if chosen else None
        accts = books.accounts(tx, ("income", "expense"))
    year = books.year_period(today(), s["year_start"])
    return _render("finance_budgets.html", status, notice=notice, error=error, budgets=all_budgets, data=data,
                   accounts=accts, year=year)


@bp.post("/finance/budgets")
@finance_required
def add_budget():
    def do(tx):
        start = books.day_of(_form("start"), "budget's start")
        end = books.day_of(_form("end"), "budget's end")
        bid = books.add_budget(tx, _form("name"), start, end, _budget_amounts(tx), **_who())
        return {"budget": bid, "name": _form("name")}
    return _record("finance.budget", do, budgets, lambda d: f"Saved the budget {d['name']}.")


@bp.post("/finance/budgets/<int:budget_id>")
@finance_required
def save_budget(budget_id):
    def do(tx):
        if not tx.execute("SELECT 1 FROM fin_budgets WHERE id = ?", (budget_id,)).fetchone():
            raise books.Problem("That budget doesn't exist.")
        books.save_budget_lines(tx, budget_id, _budget_amounts(tx))
        return {"budget": budget_id}
    return _record("finance.budget", do, lambda **kw: budgets(budget_id=str(budget_id), **kw), "Saved the budget.")


# --- Settings ---------------------------------------------------------------------------------------------------

@bp.get("/finance/settings")
@finance_required
def settings_page(status=200, notice=None, error=None):
    with auth.db().transaction() as tx:
        recs = books.reconciliations(tx)
        opening = books.transactions(tx, kinds=("opening",))
        balances = {code: books.balance_of(tx, books.account_by_code(tx, code)["id"], today()) for code in (books.BANK, books.CASH)}
        f = [x for x in books.funds(tx) if x["restricted"]]
    return _render("finance_settings.html", status, notice=notice, error=error, structures=books.STRUCTURES,
                   bases=books.BASES, months=books.MONTHS, recs=recs, opening=opening, balances=balances,
                   restricted_funds=f, codes={"cash": books.CASH, "bank": books.BANK})


@bp.post("/finance/settings")
@finance_required
def save_settings():
    def do(tx):
        values = {"structure": _form("structure"), "basis": _form("basis"), "year_start": _form("year_start"),
                  "charity_number": " ".join(_form("charity_number").split())[:20],
                  "legal_name": " ".join(_form("legal_name").split())[:120]}
        if values["structure"] not in dict(books.STRUCTURES):
            raise books.Problem("Choose how the madrasah is set up.")
        if values["basis"] not in dict(books.BASES):
            raise books.Problem("Choose how the accounts are kept.")
        if not values["year_start"].isdigit() or not 1 <= int(values["year_start"]) <= 12:
            raise books.Problem("Choose the month the financial year starts.")
        books.save_settings(tx, values, g.user["id"], auth.now())
        return values
    return _record("finance.settings", do, settings_page, "Saved.")


@bp.post("/finance/opening")
@finance_required
def opening():
    def do(tx):
        amounts = {code: books.pence(_form("o" + code), name, allow_zero=True) for code, name in (
            (books.CASH, "Cash in hand"), (books.BANK, "Bank"), (books.RECEIVABLES, "Fees owed"),
            (books.EQUIPMENT, "Equipment"), (books.PAYABLES, "Unpaid bills"), (books.PAYROLL, "Staff pay owed"),
            (books.OTHER_OWED, "Other amounts owed"))}
        restricted = {f["id"]: books.pence(_form(f"r{f['id']}"), f["name"], allow_zero=True)
                      for f in books.funds(tx) if f["restricted"]}
        day = books.day_of(_form("day"), "date of the opening balances")
        books.opening_balances(tx, day, amounts, restricted, **_who())
        return {"day": day.isoformat(), **{k: v for k, v in amounts.items() if v}}
    return _record("finance.opening", do, settings_page, "Saved the opening balances.")


@bp.post("/finance/reconcile")
@finance_required
def reconcile():
    def do(tx):
        code = _form("account")
        if code not in (books.BANK, books.CASH):
            raise books.Problem("Choose the bank account or cash.")
        raw = _form("balance")
        negative = raw.startswith("-") or raw.startswith("−")
        balance = books.pence(raw.lstrip("-−"), "Statement balance", allow_zero=True) * (-1 if negative else 1)
        day = books.day_of(_form("day"), "statement date")
        if day > today():
            raise books.Problem("The statement date can't be in the future.")
        ledger = books.reconcile(tx, code, day, balance, _form("note"), **_who())
        return {"account": code, "day": day.isoformat(), "statement": balance, "ledger": ledger}

    def notice(d):
        diff = d["statement"] - d["ledger"]
        return ("The statement matches the books." if not diff else
                f"The statement is {books.gbp(abs(diff))} {'more' if diff > 0 else 'less'} than the books. "
                "Check for money in or out that hasn't been recorded yet.")
    return _record("finance.reconciled", do, settings_page, notice)


# --- The print button -----------------------------------------------------------------------------------------

PRINT_JS = (Path(__file__).resolve().parent / "static" / "finance.js")


@bp.get("/finance/finance.js")
@finance_required
def finance_js():
    return Response(PRINT_JS.read_text(encoding="utf-8"), mimetype="text/javascript")
