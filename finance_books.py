"""The school's books: a double-entry ledger, and every finance report worked out from it.

Each thing that happens (a fee billed, a payment, a bill, a correction) is one entry in fin_entries, with lines in
fin_lines that always balance: total debits equal total credits. Money is whole pence. Every report on the Finance
pages is a sum over these lines, so the reports always agree with each other and with the transactions under them.

Nothing is deleted or edited. A correction is a reversing entry: the same lines the other way round, linked to the
original both ways (reverses / reversed_by), so the history keeps both. Every change is also in audit_log.

Each school has a database of its own (schools.py), so one school's books can never be read from another's.

The words on the pages are for people who aren't accountants: "money in", "money out", "fees owed by families".
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import json
import re

# Report lines, in the order the reports show them: (key, label, kind).
GROUPS = (
    ("tuition", "Student tuition fees", "income"), ("donations", "Donations", "income"),
    ("fundraising", "Fundraising", "income"), ("grants", "Grants", "income"), ("other_income", "Other income", "income"),
    ("salaries", "Teacher salaries and wages", "expense"), ("premises", "Rent and premises", "expense"),
    ("utilities", "Utilities", "expense"), ("materials", "Teaching materials", "expense"),
    ("admin", "Administration", "expense"), ("maintenance", "Maintenance", "expense"),
    ("other_expenses", "Other expenses", "expense"),
    ("cash", "Cash and bank balances", "asset"), ("receivables", "Fees owed by families", "asset"),
    ("equipment", "Equipment", "asset"),
    ("payables", "Unpaid bills", "liability"), ("payroll", "Staff pay owed", "liability"),
    ("other_liabilities", "Other amounts owed", "liability"), ("funds", "Funds brought forward", "fund"))
GROUP_LABEL = {k: label for k, label, _ in GROUPS}
INCOME_GROUPS = [k for k, _, kind in GROUPS if kind == "income"]
EXPENSE_GROUPS = [k for k, _, kind in GROUPS if kind == "expense"]

# The system accounts the code posts to, by code (seeded in db._m24_finance_books).
CASH, BANK, RECEIVABLES, EQUIPMENT = "1000", "1010", "1100", "1200"
PAYABLES, PAYROLL, OTHER_OWED, BROUGHT_FORWARD = "2000", "2100", "2200", "3000"
TUITION, DISCOUNTS, DONATIONS = "4000", "4010", "4100"
SALARIES = "5000"

METHODS = (("bank", "Bank transfer"), ("cash", "Cash"), ("card", "Card"), ("cheque", "Cheque"), ("online", "Online"))
METHOD_LABEL = dict(METHODS)
KINDS = {"opening": "Opening balances", "invoice": "Fee billed", "discount": "Discount or waiver",
         "payment": "Fee payment", "refund": "Fee refund", "income": "Money in", "expense": "Money out",
         "bill": "Bill (not paid yet)", "bill_payment": "Bill paid", "staff": "Staff payment",
         "staff_owed": "Staff pay owed", "reversal": "Correction"}

STRUCTURES = (("", "Not set yet"), ("charity", "Registered charity"), ("cio", "Charitable incorporated organisation (CIO)"),
              ("charitable_company", "Charitable company"), ("unincorporated", "Unincorporated association (not a charity)"),
              ("company", "Company (not a charity)"), ("other", "Something else"))
CHARITY_STRUCTURES = ("charity", "cio", "charitable_company")
BASES = (("accruals", "Accruals (income and spending when they're due)"),
         ("receipts", "Receipts and payments (money when it actually moves)"))
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

MAX_PENCE = 10_000_000_00  # £10 million in one entry
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class Problem(ValueError):
    """Something a person entered that can't be recorded; the message is shown to them as it is."""


# --- Money and days --------------------------------------------------------------------------------------------

def gbp(pence, signed=False):
    """£1,250.00; a negative amount is −£1,250.00 (a true minus sign). signed adds + to positive amounts."""
    pence = int(pence or 0)
    text = f"£{abs(pence) // 100:,}.{abs(pence) % 100:02d}"
    if pence < 0:
        return "−" + text
    return ("+" + text) if signed and pence > 0 else text


def pence(text, field="Amount", allow_zero=False):
    """Pence from what a person typed: 1250, 1,250.50, £1,250.5. Refuses negatives and more than two decimals."""
    raw = (text or "").strip().replace(",", "").replace("£", "").replace(" ", "")
    if not raw:
        if allow_zero:
            return 0
        raise Problem(f"Enter the {field.lower()}.")
    if not re.fullmatch(r"\d{1,9}(\.\d{1,2})?", raw):
        raise Problem(f"{field} must be an amount in pounds, like 25 or 1,250.50.")
    whole, _, frac = raw.partition(".")
    value = int(whole) * 100 + int((frac + "00")[:2])
    if value > MAX_PENCE:
        raise Problem(f"{field} is more than £10,000,000.00.")
    if value == 0 and not allow_zero:
        raise Problem(f"{field} must be more than £0.00.")
    return value


def day_of(text, field="Date", default=None):
    raw = (text or "").strip()
    if not raw and default is not None:
        return default
    if not DAY.fullmatch(raw):
        raise Problem(f"Choose the {field.lower()}.")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise Problem(f"Choose the {field.lower()}.") from None


def nice_day(value):
    d = value if isinstance(value, date) else date.fromisoformat(value)
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def change_pct(now, before):
    """The change from before to now as a percentage, or None when there was nothing before to compare with."""
    if not before:
        return None
    return round((now - before) * 100 / abs(before), 1)


# --- Settings and periods -------------------------------------------------------------------------------------

DEFAULTS = {"structure": "", "basis": "accruals", "year_start": "9", "charity_number": "", "legal_name": ""}


def settings(tx):
    out = dict(DEFAULTS)
    for row in tx.execute("SELECT key, value FROM fin_settings").fetchall():
        out[row["key"]] = row["value"]
    out["year_start"] = int(out["year_start"]) if str(out["year_start"]).isdigit() and 1 <= int(out["year_start"]) <= 12 else 9
    out["is_charity"] = out["structure"] in CHARITY_STRUCTURES
    return out


def save_settings(tx, values, user_id, at):
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        if tx.execute("SELECT 1 FROM fin_settings WHERE key = ?", (key,)).fetchone():
            tx.execute("UPDATE fin_settings SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?",
                       (str(value), at, user_id, key))
        else:
            tx.execute("INSERT INTO fin_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                       (key, str(value), at, user_id))


def _add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


@dataclass(frozen=True)
class Period:
    kind: str  # month, term, year or custom
    start: date
    end: date
    label: str

    @property
    def days(self):
        return (self.end - self.start).days + 1


def month_period(d):
    start = date(d.year, d.month, 1)
    return Period("month", start, _add_months(start, 1) - timedelta(days=1), f"{MONTHS[d.month - 1]} {d.year}")


def year_period(d, year_start):
    first = date(d.year if d.month >= year_start else d.year - 1, year_start, 1)
    end = _add_months(first, 12) - timedelta(days=1)
    label = f"{first.year} to {end.year}" if first.year != end.year else str(first.year)
    return Period("year", first, end, label)


# A year's three terms, as months from its start: Autumn September to December, Spring January to March and
# Summer April to August when the year starts in September.
TERMS = (("Autumn term", 0, 4), ("Spring term", 4, 7), ("Summer term", 7, 12))


def term_period(d, year_start):
    year = year_period(d, year_start)
    for name, a, b in TERMS:
        start, after = _add_months(year.start, a), _add_months(year.start, b)
        if start <= d < after:
            return Period("term", start, after - timedelta(days=1), f"{name} {year.label}")
    raise AssertionError("unreachable")


def previous(period, year_start):
    """The period before this one, of the same kind (or, for a custom range, the same number of days)."""
    before = period.start - timedelta(days=1)
    if period.kind == "month":
        return month_period(before)
    if period.kind == "term":
        return term_period(before, year_start)
    if period.kind == "year":
        return year_period(before, year_start)
    start = period.start - timedelta(days=period.days)
    return Period("custom", start, before, f"{nice_day(start)} to {nice_day(before)}")


def period_from(args, year_start, today):
    """The period a page's ?period= (month, term, year or custom), ?on= and ?start= / ?end= choose."""
    kind = args.get("period", "year")
    try:
        on = day_of(args.get("on", ""), default=today)
    except Problem:
        on = today
    if kind == "custom":
        try:
            start, end = day_of(args.get("start", ""), "start date"), day_of(args.get("end", ""), "end date")
        except Problem:
            return year_period(on, year_start)
        if end < start:
            start, end = end, start
        if (end - start).days > 3700:
            start = end - timedelta(days=3700)
        return Period("custom", start, end, f"{nice_day(start)} to {nice_day(end)}")
    if kind == "month":
        return month_period(on)
    if kind == "term":
        return term_period(on, year_start)
    return year_period(on, year_start)


def months_between(start, end):
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(month_period(d))
        d = _add_months(d, 1)
    return out


# --- Accounts, funds and students -----------------------------------------------------------------------------

def accounts(tx, kinds=None, include_archived=False):
    rows = [dict(r) for r in tx.execute("SELECT * FROM fin_accounts ORDER BY code").fetchall()]
    return [r for r in rows if (kinds is None or r["kind"] in kinds) and (include_archived or not r["archived"])]


def account_by_code(tx, code):
    row = tx.execute("SELECT * FROM fin_accounts WHERE code = ?", (code,)).fetchone()
    if not row:
        raise RuntimeError(f"The finance account {code} is missing.")
    return dict(row)


def account(tx, account_id, kinds):
    row = tx.execute("SELECT * FROM fin_accounts WHERE id = ?", (account_id,)).fetchone() if str(account_id).isdigit() else None
    if not row or row["kind"] not in kinds or row["archived"]:
        raise Problem("Choose what the money is for.")
    return dict(row)


def funds(tx, include_archived=False):
    rows = [dict(r) for r in tx.execute("SELECT * FROM fin_funds ORDER BY system DESC, restricted, name").fetchall()]
    return [r for r in rows if include_archived or not r["archived"]]


def general_fund(tx):
    return dict(tx.execute("SELECT * FROM fin_funds WHERE system = 1 ORDER BY id LIMIT 1").fetchone())


def fund(tx, fund_id):
    if not fund_id:
        return general_fund(tx)
    row = tx.execute("SELECT * FROM fin_funds WHERE id = ?", (fund_id,)).fetchone() if str(fund_id).isdigit() else None
    if not row or row["archived"]:
        raise Problem("Choose a fund.")
    return dict(row)


def add_fund(tx, name, restricted, purpose, at):
    name = " ".join((name or "").split())[:80]
    if not name:
        raise Problem("Give the fund a name.")
    if tx.execute("SELECT 1 FROM fin_funds WHERE LOWER(name) = LOWER(?)", (name,)).fetchone():
        raise Problem("There is already a fund with that name.")
    return tx.insert("INSERT INTO fin_funds (name, restricted, purpose, created_at) VALUES (?, ?, ?, ?)",
                     (name, 1 if restricted else 0, " ".join((purpose or "").split())[:200], at))


def students(tx):
    """Every current student, from the classes: {key, name, sid, fid, class_name}. key is the Student ID when there
    is one, otherwise the class and name, so a student without an ID can still be billed."""
    out = []
    for row in tx.execute("SELECT id, json FROM classrooms").fetchall():
        try:
            state = json.loads(row["json"])
        except (TypeError, ValueError):
            continue
        for s in state.get("S", []):
            name = (s.get("name") or "").strip()
            if not name:
                continue
            sid = (s.get("sid") or "").strip()
            out.append({"key": f"id:{sid}" if sid else f"cl:{row['id']}:{name}", "name": name, "sid": sid,
                        "fid": (s.get("fid") or "").strip(), "class_id": row["id"], "class_name": state.get("name", "")})
    return sorted(out, key=lambda s: (s["class_name"].lower(), s["name"].lower()))


# --- Posting --------------------------------------------------------------------------------------------------

def post(tx, day, kind, lines, *, user_id, at, memo="", party="", method="", invoice_id=None, settles=None,
         reverses=None, reason=""):
    """Records one entry. lines: (account_id, fund_id, debit, credit). Refuses one that doesn't balance."""
    lines = [(a, f, int(d), int(c)) for a, f, d, c in lines if d or c]
    if not lines:
        raise Problem("There is nothing to record.")
    if any(d < 0 or c < 0 or (d and c) for _, _, d, c in lines):
        raise RuntimeError("A ledger line must be a positive debit or a positive credit.")
    if sum(d for _, _, d, _ in lines) != sum(c for _, _, _, c in lines):
        raise RuntimeError("An entry's debits and credits must balance.")
    entry = tx.insert(
        "INSERT INTO fin_entries (day, kind, memo, party, method, invoice_id, settles, reverses, reason, created_by, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (day.isoformat(), kind, memo[:200], party[:120], method, invoice_id, settles, reverses, reason[:300], user_id, at))
    for a, f, d, c in lines:
        tx.execute("INSERT INTO fin_lines (entry_id, account_id, fund_id, debit, credit) VALUES (?, ?, ?, ?, ?)",
                   (entry, a, f, d, c))
    return entry


def _money_account(tx, method):
    return account_by_code(tx, CASH if method == "cash" else BANK)


def _method(value):
    if value not in METHOD_LABEL:
        raise Problem("Choose how the money was paid.")
    return value


def entry(tx, entry_id):
    row = tx.execute("SELECT * FROM fin_entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def entry_lines(tx, entry_id):
    return [dict(r) for r in tx.execute(
        "SELECT l.*, a.name AS account, a.code, a.kind AS account_kind, a.grp, f.name AS fund FROM fin_lines l "
        "JOIN fin_accounts a ON a.id = l.account_id JOIN fin_funds f ON f.id = l.fund_id WHERE l.entry_id = ? "
        "ORDER BY l.id", (entry_id,)).fetchall()]


def reverse(tx, entry_id, day, reason, *, user_id, at):
    """Corrects an entry by recording its opposite. The original stays, marked as reversed."""
    original = entry(tx, entry_id)
    reason = " ".join((reason or "").split())
    if not original:
        raise Problem("That entry doesn't exist.")
    if original["kind"] == "reversal":
        raise Problem("A correction can't itself be reversed. Record the entry again instead.")
    if original["reversed_by"]:
        raise Problem("That entry has already been reversed.")
    if not reason:
        raise Problem("Say why the entry is being corrected. It is kept with the correction.")
    if original["kind"] == "invoice":
        inv = invoice(tx, original["invoice_id"])
        if inv and (inv["paid"] or inv["discounts"]):
            raise Problem("This fee has payments or discounts against it. Correct those first.")
    if original["kind"] in ("bill", "staff_owed") and bill_paid(tx, entry_id):
        raise Problem("This bill has been paid. Correct the payment first.")
    lines = [(l["account_id"], l["fund_id"], l["credit"], l["debit"]) for l in entry_lines(tx, entry_id)]
    new = post(tx, day, "reversal", lines, user_id=user_id, at=at, memo=f"Correction of: {original['memo'] or KINDS.get(original['kind'], '')}",
               party=original["party"], method=original["method"], invoice_id=original["invoice_id"],
               settles=original["settles"], reverses=entry_id, reason=reason)
    tx.execute("UPDATE fin_entries SET reversed_by = ? WHERE id = ?", (new, entry_id))
    return new


# --- Fees -------------------------------------------------------------------------------------------------------

def bill_student(tx, student, description, amount, discount, issued, due, instalments, *, user_id, at):
    """Bills a student a fee, optionally split into monthly instalments, less any discount. Returns invoice ids."""
    description = " ".join((description or "").split())[:120]
    if not description:
        raise Problem("Say what the fee is for, like Autumn term fees.")
    if discount > amount:
        raise Problem("The discount can't be more than the fee.")
    if not 1 <= instalments <= 12:
        raise Problem("Instalments must be from 1 to 12.")
    if due < issued:
        raise Problem("The due date can't be before the day the fee is billed.")
    receivable, tuition, discounts = (account_by_code(tx, c)["id"] for c in (RECEIVABLES, TUITION, DISCOUNTS))
    general = general_fund(tx)["id"]
    ids = []
    # Spread the pence so the instalments add up exactly to the fee.
    for i in range(instalments):
        part = amount // instalments + (1 if i < amount % instalments else 0)
        off = discount // instalments + (1 if i < discount % instalments else 0)
        due_i = due if i == 0 else _add_months(date(due.year, due.month, 1), i).replace(day=min(due.day, 28))
        label = description if instalments == 1 else f"{description} ({i + 1} of {instalments})"
        inv = tx.insert("INSERT INTO fin_invoices (student_sid, student_name, class_name, family_id, description, "
                        "issued_day, due_day, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (student["sid"], student["name"], student["class_name"], student["fid"], label,
                         issued.isoformat(), due_i.isoformat(), user_id, at))
        post(tx, issued, "invoice", [(receivable, general, part, 0), (tuition, general, 0, part)], user_id=user_id,
             at=at, memo=label, party=student["name"], invoice_id=inv)
        if off:
            post(tx, issued, "discount", [(discounts, general, off, 0), (receivable, general, 0, off)], user_id=user_id,
                 at=at, memo=f"Discount: {label}", party=student["name"], invoice_id=inv)
        ids.append(inv)
    return ids


def _invoice_sums(tx, where="", params=()):
    """{invoice_id: {billed, discounts, paid, refunded}} from the ledger: what each fee's entries did to the fees
    families owe. billed and discounts are net of corrections; paid is payments less corrected payments."""
    rows = tx.execute(
        "SELECT e.invoice_id, e.kind, e.reverses, l.debit, l.credit FROM fin_lines l JOIN fin_entries e ON e.id = l.entry_id "
        "JOIN fin_accounts a ON a.id = l.account_id WHERE a.code = ? AND e.invoice_id IS NOT NULL" + where,
        (RECEIVABLES,) + tuple(params)).fetchall()
    kinds = {r["id"]: r["kind"] for r in tx.execute("SELECT id, kind FROM fin_entries WHERE invoice_id IS NOT NULL").fetchall()}
    out = defaultdict(lambda: {"billed": 0, "discounts": 0, "paid": 0, "refunded": 0})
    for r in rows:
        kind = r["kind"]
        if kind == "reversal":
            kind = kinds.get(r["reverses"], "")
        net = r["debit"] - r["credit"]
        s = out[r["invoice_id"]]
        if kind == "invoice":
            s["billed"] += net
        elif kind == "discount":
            s["discounts"] -= net
        elif kind == "payment":
            s["paid"] -= net
        elif kind == "refund":
            s["refunded"] += net
    return out


def _status(inv, today):
    owed = inv["outstanding"]
    if inv["net"] <= 0 and inv["billed"] > 0:
        return "waived", "Waived", "grey"
    if inv["billed"] <= 0:
        return "cancelled", "Cancelled", "grey"
    if owed <= 0:
        return "paid", "Paid", "green"
    if inv["due_day"] < today.isoformat():
        return "overdue", "Overdue", "red"
    if inv["paid"] - inv["refunded"] > 0:
        return "part", "Part paid", "amber"
    return "unpaid", "Not paid yet", "blue"


def invoices(tx, today, status=None, family=None, student=None):
    sums = _invoice_sums(tx)
    out = []
    for r in tx.execute("SELECT * FROM fin_invoices ORDER BY due_day, id").fetchall():
        inv = dict(r)
        inv.update(sums[inv["id"]])
        inv["net"] = inv["billed"] - inv["discounts"]
        inv["received"] = inv["paid"] - inv["refunded"]
        inv["outstanding"] = inv["net"] - inv["received"]
        inv["status"], inv["status_label"], inv["tone"] = _status(inv, today)
        if family and inv["family_id"] != family:
            continue
        if student and inv["student_name"] != student:
            continue
        if status == "outstanding" and inv["outstanding"] <= 0:
            continue
        if status == "overdue" and inv["status"] != "overdue":
            continue
        out.append(inv)
    return out


def invoice(tx, invoice_id, today=None):
    row = tx.execute("SELECT id FROM fin_invoices WHERE id = ?", (invoice_id,)).fetchone() if str(invoice_id).isdigit() else None
    if not row:
        return None
    return next(i for i in invoices(tx, today or date.today()) if i["id"] == row["id"])


def invoice_history(tx, invoice_id):
    return [dict(r) for r in tx.execute(
        "SELECT e.*, u.name AS by_name, (SELECT SUM(l.debit) FROM fin_lines l WHERE l.entry_id = e.id) AS amount "
        "FROM fin_entries e LEFT JOIN users u ON u.id = e.created_by WHERE e.invoice_id = ? ORDER BY e.day, e.id", (invoice_id,)).fetchall()]


def receive_payment(tx, invoice_id, amount, day, method, *, user_id, at, memo=""):
    inv = invoice(tx, invoice_id, day)
    if not inv:
        raise Problem("That fee doesn't exist.")
    if amount > inv["outstanding"]:
        raise Problem(f"That's more than the {gbp(inv['outstanding'])} still owed on this fee.")
    money = _money_account(tx, _method(method))["id"]
    receivable, general = account_by_code(tx, RECEIVABLES)["id"], general_fund(tx)["id"]
    return post(tx, day, "payment", [(money, general, amount, 0), (receivable, general, 0, amount)], user_id=user_id,
                at=at, memo=memo or f"Payment: {inv['description']}", party=inv["student_name"], method=method,
                invoice_id=inv["id"])


def discount(tx, invoice_id, amount, day, reason, *, user_id, at):
    inv = invoice(tx, invoice_id, day)
    reason = " ".join((reason or "").split())
    if not inv:
        raise Problem("That fee doesn't exist.")
    if not reason:
        raise Problem("Say why the discount or waiver is given.")
    if amount > inv["outstanding"]:
        raise Problem(f"That's more than the {gbp(inv['outstanding'])} still owed on this fee.")
    discounts, receivable = account_by_code(tx, DISCOUNTS)["id"], account_by_code(tx, RECEIVABLES)["id"]
    general = general_fund(tx)["id"]
    return post(tx, day, "discount", [(discounts, general, amount, 0), (receivable, general, 0, amount)], user_id=user_id,
                at=at, memo=f"Discount: {inv['description']}", party=inv["student_name"], invoice_id=inv["id"], reason=reason)


def refund(tx, invoice_id, amount, day, method, reason, *, user_id, at):
    """Money paid back to a family. The fee is owed again unless a discount or waiver is also recorded."""
    inv = invoice(tx, invoice_id, day)
    reason = " ".join((reason or "").split())
    if not inv:
        raise Problem("That fee doesn't exist.")
    if not reason:
        raise Problem("Say why the money is being paid back.")
    if amount > inv["received"]:
        raise Problem(f"Only {gbp(inv['received'])} has been paid on this fee.")
    money = _money_account(tx, _method(method))["id"]
    receivable, general = account_by_code(tx, RECEIVABLES)["id"], general_fund(tx)["id"]
    return post(tx, day, "refund", [(receivable, general, amount, 0), (money, general, 0, amount)], user_id=user_id,
                at=at, memo=f"Refund: {inv['description']}", party=inv["student_name"], method=method,
                invoice_id=inv["id"], reason=reason)


# --- Money in and out -----------------------------------------------------------------------------------------

def money_in(tx, account_id, fund_id, amount, day, method, party, memo, *, user_id, at):
    """Donations, fundraising, grants and other income, received now. (Tuition comes in through fees.)"""
    acct = account(tx, account_id, ("income",))
    if acct["grp"] == "tuition":
        raise Problem("Record tuition fees on Student fees, so they count against what each family owes.")
    f = fund(tx, fund_id)
    money = _money_account(tx, _method(method))["id"]
    return post(tx, day, "income", [(money, f["id"], amount, 0), (acct["id"], f["id"], 0, amount)], user_id=user_id,
                at=at, memo=" ".join((memo or "").split()) or acct["name"], party=" ".join((party or "").split()), method=method)


def money_out(tx, account_id, fund_id, amount, day, paid, method, party, memo, *, user_id, at, staff=False):
    """Spending: paid now (from cash or bank) or a bill or staff pay still owed. Equipment is an asset, not spending."""
    acct = account(tx, account_id, ("expense", "asset"))
    if acct["kind"] == "asset" and acct["grp"] != "equipment":
        raise Problem("Choose what the money is for.")
    f = fund(tx, fund_id)
    party = " ".join((party or "").split())
    if staff and not party:
        raise Problem("Enter the staff member's name.")
    memo = " ".join((memo or "").split()) or acct["name"]
    if paid:
        money = _money_account(tx, _method(method))["id"]
        return post(tx, day, "staff" if staff else "expense", [(acct["id"], f["id"], amount, 0), (money, f["id"], 0, amount)],
                    user_id=user_id, at=at, memo=memo, party=party, method=method)
    owed = account_by_code(tx, PAYROLL if staff else PAYABLES)["id"]
    return post(tx, day, "staff_owed" if staff else "bill", [(acct["id"], f["id"], amount, 0), (owed, f["id"], 0, amount)],
                user_id=user_id, at=at, memo=memo, party=party)


def bill_paid(tx, bill_id):
    return sum(r["debit"] - r["credit"] for r in tx.execute(
        "SELECT l.debit, l.credit FROM fin_lines l JOIN fin_entries e ON e.id = l.entry_id JOIN fin_accounts a "
        "ON a.id = l.account_id WHERE e.settles = ? AND a.kind = 'liability'", (bill_id,)).fetchall())


def unpaid_bills(tx):
    out = []
    for r in tx.execute("SELECT e.*, l.credit AS amount, l.fund_id, a.code FROM fin_entries e JOIN fin_lines l ON l.entry_id = e.id "
                        "JOIN fin_accounts a ON a.id = l.account_id WHERE e.kind IN ('bill', 'staff_owed') AND "
                        "e.reversed_by IS NULL AND a.kind = 'liability' ORDER BY e.day, e.id").fetchall():
        b = dict(r)
        b["paid"] = bill_paid(tx, b["id"])
        b["owed"] = b["amount"] - b["paid"]
        if b["owed"] > 0:
            out.append(b)
    return out


def pay_bill(tx, bill_id, amount, day, method, *, user_id, at):
    bill = next((b for b in unpaid_bills(tx) if b["id"] == bill_id), None)
    if not bill:
        raise Problem("That bill has already been paid, or doesn't exist.")
    if amount > bill["owed"]:
        raise Problem(f"That's more than the {gbp(bill['owed'])} still owed.")
    owed = account_by_code(tx, bill["code"])["id"]
    money = _money_account(tx, _method(method))["id"]
    return post(tx, day, "bill_payment", [(owed, bill["fund_id"], amount, 0), (money, bill["fund_id"], 0, amount)],
                user_id=user_id, at=at, memo=f"Paid: {bill['memo']}", party=bill["party"], method=method, settles=bill_id)


def opening_balances(tx, day, amounts, restricted, *, user_id, at):
    """What the madrasah had and owed on the day it started keeping its books in Ilmi. amounts: {code: pence} for
    cash, bank, fees owed, equipment, unpaid bills, staff pay owed and other amounts owed. restricted: {fund_id:
    pence} of the funds that are restricted. The funds brought forward are what it had less what it owed: that is
    what funds are, not a balancing figure."""
    if tx.execute("SELECT 1 FROM fin_entries WHERE kind = 'opening' AND reversed_by IS NULL").fetchone():
        raise Problem("Opening balances are already recorded. Correct them under Settings first to enter them again.")
    assets = sum(amounts.get(c, 0) for c in (CASH, BANK, RECEIVABLES, EQUIPMENT))
    owed = sum(amounts.get(c, 0) for c in (PAYABLES, PAYROLL, OTHER_OWED))
    net = assets - owed
    held = sum(restricted.values())
    if not assets and not owed:
        raise Problem("Enter at least one balance.")
    if held > net:
        raise Problem(f"Restricted funds ({gbp(held)}) can't be more than what the madrasah has after what it owes ({gbp(net)}).")
    general = general_fund(tx)["id"]
    lines = []
    for code in (CASH, BANK, RECEIVABLES, EQUIPMENT):
        if amounts.get(code):
            lines.append((account_by_code(tx, code)["id"], general, amounts[code], 0))
    for code in (PAYABLES, PAYROLL, OTHER_OWED):
        if amounts.get(code):
            lines.append((account_by_code(tx, code)["id"], general, 0, amounts[code]))
    bf = account_by_code(tx, BROUGHT_FORWARD)["id"]
    for fund_id, value in restricted.items():
        if value:
            lines.append((bf, fund_id, 0, value))
    rest = net - held
    lines.append((bf, general, 0, rest) if rest >= 0 else (bf, general, -rest, 0))
    return post(tx, day, "opening", lines, user_id=user_id, at=at, memo="Opening balances")


def reconcile(tx, code, statement_day, statement_balance, note, *, user_id, at):
    acct = account_by_code(tx, code)
    ledger = balance_of(tx, acct["id"], statement_day)
    tx.insert("INSERT INTO fin_reconciliations (account_id, statement_day, statement_balance, ledger_balance, note, "
              "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (acct["id"], statement_day.isoformat(), statement_balance, ledger, " ".join((note or "").split())[:200], user_id, at))
    return ledger


def reconciliations(tx):
    return [dict(r) for r in tx.execute(
        "SELECT r.*, a.name AS account, a.code, u.name AS by_name FROM fin_reconciliations r JOIN fin_accounts a ON "
        "a.id = r.account_id LEFT JOIN users u ON u.id = r.created_by ORDER BY r.statement_day DESC, r.id DESC").fetchall()]


def add_budget(tx, name, start, end, amounts, *, user_id, at):
    name = " ".join((name or "").split())[:80]
    if not name:
        raise Problem("Give the budget a name, like 2026 to 2027.")
    if end < start:
        raise Problem("The budget's end can't be before its start.")
    if not any(amounts.values()):
        raise Problem("Enter an amount for at least one line.")
    budget = tx.insert("INSERT INTO fin_budgets (name, start_day, end_day, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                       (name, start.isoformat(), end.isoformat(), user_id, at))
    save_budget_lines(tx, budget, amounts)
    return budget


def save_budget_lines(tx, budget, amounts):
    for account_id, value in amounts.items():
        tx.execute("DELETE FROM fin_budget_lines WHERE budget_id = ? AND account_id = ?", (budget, account_id))
        if value:
            tx.execute("INSERT INTO fin_budget_lines (budget_id, account_id, amount) VALUES (?, ?, ?)", (budget, account_id, value))


def budgets(tx):
    return [dict(r) for r in tx.execute("SELECT * FROM fin_budgets ORDER BY start_day DESC, id DESC").fetchall()]


# --- Sums -----------------------------------------------------------------------------------------------------

def _sums(tx, start=None, end=None, by_fund=False):
    """{(account_id, fund_id or None): (debit, credit)} over entries dated start..end (inclusive; None is open)."""
    where, params = [], []
    if start:
        where.append("e.day >= ?"), params.append(start.isoformat())
    if end:
        where.append("e.day <= ?"), params.append(end.isoformat())
    fund_col = "l.fund_id" if by_fund else "NULL"
    rows = tx.execute(
        f"SELECT l.account_id, {fund_col} AS fund_id, SUM(l.debit) AS d, SUM(l.credit) AS c FROM fin_lines l "
        "JOIN fin_entries e ON e.id = l.entry_id" + (" WHERE " + " AND ".join(where) if where else "") +
        f" GROUP BY l.account_id{', l.fund_id' if by_fund else ''}", tuple(params)).fetchall()
    return {(r["account_id"], r["fund_id"]): (int(r["d"] or 0), int(r["c"] or 0)) for r in rows}


def _natural(kind, d, c):
    """An account's balance the way people read it: assets and spending as debits, the rest as credits."""
    return d - c if kind in ("asset", "expense") else c - d


def balance_of(tx, account_id, upto):
    row = tx.execute("SELECT a.kind, SUM(l.debit) AS d, SUM(l.credit) AS c FROM fin_lines l JOIN fin_entries e ON "
                     "e.id = l.entry_id JOIN fin_accounts a ON a.id = l.account_id WHERE l.account_id = ? AND e.day <= ? "
                     "GROUP BY a.kind", (account_id, upto.isoformat())).fetchone()
    return _natural(row["kind"], int(row["d"] or 0), int(row["c"] or 0)) if row else 0


def has_entries(tx):
    return bool(tx.execute("SELECT 1 FROM fin_entries LIMIT 1").fetchone())


def has_opening(tx):
    return bool(tx.execute("SELECT 1 FROM fin_entries WHERE kind = 'opening' AND reversed_by IS NULL").fetchone())


def transactions(tx, start=None, end=None, groups=None, kinds=None, limit=None, party_like=None):
    """Entries in a period, newest first, each with its amount (the sum of its debits) and the report lines it
    touched. groups: only entries with a line on these report lines."""
    where, params = [], []
    if start:
        where.append("e.day >= ?"), params.append(start.isoformat())
    if end:
        where.append("e.day <= ?"), params.append(end.isoformat())
    if kinds:
        where.append("e.kind IN (" + ", ".join("?" * len(kinds)) + ")"), params.extend(kinds)
    rows = [dict(r) for r in tx.execute(
        "SELECT e.*, u.name AS by_name FROM fin_entries e LEFT JOIN users u ON u.id = e.created_by" +
        (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.day DESC, e.id DESC", tuple(params)).fetchall()]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    lines = defaultdict(list)
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for l in tx.execute("SELECT l.entry_id, l.debit, l.credit, a.grp, a.kind, a.name, f.name AS fund, f.restricted "
                            "FROM fin_lines l JOIN fin_accounts a ON a.id = l.account_id JOIN fin_funds f ON f.id = l.fund_id "
                            "WHERE l.entry_id IN (" + ", ".join("?" * len(part)) + ")", tuple(part)).fetchall():
            lines[l["entry_id"]].append(dict(l))
    out = []
    for r in rows:
        ls = lines[r["id"]]
        r["amount"] = sum(l["debit"] for l in ls)
        r["groups"] = sorted({l["grp"] for l in ls})
        r["kind_label"] = KINDS.get(r["kind"], r["kind"])
        r["method_label"] = METHOD_LABEL.get(r["method"], "")
        r["fund"] = next((l["fund"] for l in ls if l["kind"] in ("income", "expense")), ls[0]["fund"] if ls else "")
        r["what"] = next((l["name"] for l in ls if l["kind"] in ("income", "expense")) or
                         (l["name"] for l in ls if l["grp"] == "equipment"), r["kind_label"])
        # The effect on profit: income lines as positive, spending as negative (for the transaction lists).
        r["effect"] = sum((l["credit"] - l["debit"]) for l in ls if l["kind"] in ("income", "expense"))
        r["cash"] = sum((l["debit"] - l["credit"]) for l in ls if l["grp"] == "cash")
        if groups and not set(r["groups"]) & set(groups):
            continue
        if party_like and party_like.lower() not in (r["party"] or "").lower():
            continue
        out.append(r)
    return out[:limit] if limit else out


# --- Reports --------------------------------------------------------------------------------------------------

def _group_totals(tx, start, end):
    """{group: {total, accounts: [(name, amount)]}} for income and spending in a period, each in its natural sign."""
    accts = {a["id"]: a for a in accounts(tx, include_archived=True)}
    out = {k: {"total": 0, "accounts": defaultdict(int)} for k in INCOME_GROUPS + EXPENSE_GROUPS}
    for (account_id, _), (d, c) in _sums(tx, start, end).items():
        a = accts[account_id]
        if a["kind"] in ("income", "expense"):
            value = _natural(a["kind"], d, c)
            out[a["grp"]]["total"] += value
            out[a["grp"]]["accounts"][a["name"]] += value
    return out


def income_expenditure(tx, period, compare=None):
    now = _group_totals(tx, period.start, period.end)
    before = _group_totals(tx, compare.start, compare.end) if compare else None

    def section(keys):
        rows = []
        for k in keys:
            total = now[k]["total"]
            prev = before[k]["total"] if before else None
            detail = [(n, v) for n, v in now[k]["accounts"].items() if v]
            rows.append({"key": k, "label": GROUP_LABEL[k], "total": total, "previous": prev,
                         "change": change_pct(total, prev) if before else None, "accounts": detail if len(detail) > 1 else []})
        return rows

    income, spending = section(INCOME_GROUPS), section(EXPENSE_GROUPS)
    ti, ts = sum(r["total"] for r in income), sum(r["total"] for r in spending)
    pi = sum(r["previous"] for r in income) if before else None
    ps = sum(r["previous"] for r in spending) if before else None
    return {"income": income, "spending": spending, "total_income": ti, "total_spending": ts, "net": ti - ts,
            "prev_income": pi, "prev_spending": ps, "prev_net": (pi - ps) if before else None,
            "change_income": change_pct(ti, pi) if before else None, "change_spending": change_pct(ts, ps) if before else None,
            "change_net": change_pct(ti - ts, pi - ps) if before else None}


def fund_balances(tx, upto, start=None):
    """Each fund's balance on a day (or its movement start..upto): what was brought forward, came in and was spent."""
    accts = {a["id"]: a for a in accounts(tx, include_archived=True)}
    out = {f["id"]: {**f, "brought_forward": 0, "income": 0, "spending": 0} for f in funds(tx, include_archived=True)}
    for (account_id, fund_id), (d, c) in _sums(tx, start, upto, by_fund=True).items():
        a = accts[account_id]
        if a["kind"] == "fund":
            out[fund_id]["brought_forward"] += c - d
        elif a["kind"] == "income":
            out[fund_id]["income"] += c - d
        elif a["kind"] == "expense":
            out[fund_id]["spending"] += d - c
    for f in out.values():
        f["balance"] = f["brought_forward"] + f["income"] - f["spending"]
    return list(out.values())


def balance_sheet(tx, on, year_start):
    """What the madrasah owns and owes on a day, and its funds. Worked out from the ledger alone; it balances
    because every entry does. If it ever doesn't, the report says so instead of hiding the difference."""
    accts = {a["id"]: a for a in accounts(tx, include_archived=True)}
    sums = _sums(tx, None, on)
    lines = defaultdict(int)
    for (account_id, _), (d, c) in sums.items():
        a = accts[account_id]
        if a["kind"] in ("asset", "liability"):
            lines[(a["kind"], a["grp"], a["name"])] += _natural(a["kind"], d, c)

    def section(kind):
        rows = [{"group": g, "label": n, "amount": v} for (k, g, n), v in sorted(lines.items(), key=lambda x: x[0][2]) if k == kind and v]
        return rows, sum(r["amount"] for r in rows)

    assets, total_assets = section("asset")
    liabilities, total_liabilities = section("liability")
    year = year_period(on, year_start)
    before_year = fund_balances(tx, year.start - timedelta(days=1))
    this_year = fund_balances(tx, on, year.start)
    funds_rows = []
    for f0, f1 in zip(before_year, this_year):
        bf = f0["balance"] + f1["brought_forward"]  # opening balances entered this year count as brought forward
        surplus = f1["income"] - f1["spending"]
        if bf or surplus:
            funds_rows.append({"name": f0["name"], "restricted": f0["restricted"], "brought_forward": bf,
                               "surplus": surplus, "balance": bf + surplus})
    total_funds = sum(f["balance"] for f in funds_rows)
    net = total_assets - total_liabilities
    return {"on": on, "year": year, "assets": assets, "total_assets": total_assets, "liabilities": liabilities,
            "total_liabilities": total_liabilities, "net_assets": net, "funds": funds_rows,
            "accumulated": sum(f["brought_forward"] for f in funds_rows),
            "current_surplus": sum(f["surplus"] for f in funds_rows),
            "restricted": sum(f["balance"] for f in funds_rows if f["restricted"]),
            "unrestricted": sum(f["balance"] for f in funds_rows if not f["restricted"]),
            "total_funds": total_funds, "balances": net == total_funds, "difference": net - total_funds,
            "has_opening": has_opening(tx), "has_entries": has_entries(tx)}


CASH_LINES = (("tuition", "Tuition payments received"), ("donations", "Donations received"),
              ("other_in", "Other money received"), ("refunds", "Fee refunds paid"), ("staff", "Staff payments"),
              ("operating", "Operating expenses"), ("equipment", "Equipment purchases"),
              ("opening", "Balances brought in when the books started"))


def _cash_line(entry):
    """Which cash-flow line an entry's cash belongs to, from what else it touched."""
    groups = set(entry["groups"]) - {"cash"}
    kind = entry["kind"]
    if kind == "opening":
        return "opening"
    if "receivables" in groups:
        return "tuition" if entry["cash"] >= 0 and kind != "refund" else "refunds"
    if "equipment" in groups:
        return "equipment"
    if groups & {"salaries", "payroll"}:
        return "staff"
    if "donations" in groups:
        return "donations"
    if groups & set(INCOME_GROUPS):
        return "other_in"
    return "operating"


def _effective(tx, e, cache):
    """What an entry's cash was really for: a correction counts as the entry it corrects (with its own sign), and a
    bill payment as the bill's spending. Returns (kind, groups)."""
    kind, groups = e["kind"], set(e["groups"])
    if kind == "reversal" and e["reverses"]:
        original = cache.get(e["reverses"]) or entry(tx, e["reverses"])
        cache[e["reverses"]] = original
        kind = original["kind"] if original else kind
    if kind == "bill_payment" and e["settles"]:
        key = ("lines", e["settles"])
        if key not in cache:
            cache[key] = {l["grp"] for l in entry_lines(tx, e["settles"])}
        groups = (groups | cache[key]) - {"payables", "payroll"} | ({"payroll"} if "payroll" in groups else set())
    return kind, groups


def cash_flow(tx, period):
    cash_ids = [a["id"] for a in accounts(tx, include_archived=True) if a["grp"] == "cash"]
    opening = sum(balance_of(tx, a, period.start - timedelta(days=1)) for a in cash_ids)
    lines = {k: 0 for k, _ in CASH_LINES}
    cache = {}
    entries = transactions(tx, period.start, period.end)
    for e in entries:
        if e["cash"]:
            kind, groups = _effective(tx, e, cache)
            lines[_cash_line({"kind": kind, "groups": groups, "cash": e["cash"] if kind == e["kind"] else -e["cash"]})] += e["cash"]
    closing = opening + sum(lines.values())
    months = []
    for m in months_between(period.start, period.end):
        ins = outs = 0
        for e in entries:
            if e["kind"] == "opening" or not (m.start.isoformat() <= e["day"] <= m.end.isoformat()):
                continue
            if e["cash"] > 0:
                ins += e["cash"]
            else:
                outs -= e["cash"]
        months.append({"label": m.label[:3], "full": m.label, "in": ins, "out": outs})
    # Fees billed in the period against fees actually received in it: billing isn't money in the bank.
    billed = sum(_natural("asset", d, c) for (a, _), (d, c) in _sums_for(tx, period, ("invoice",), RECEIVABLES).items())
    return {"opening": opening, "lines": [{"key": k, "label": label, "amount": lines[k]} for k, label in CASH_LINES],
            "received": sum(v for k, v in lines.items() if k in ("tuition", "donations", "other_in")),
            "paid": -sum(v for k, v in lines.items() if k in ("refunds", "staff", "operating", "equipment")),
            "closing": closing, "months": months, "fees_billed": billed, "fees_received": lines["tuition"]}


def _sums_for(tx, period, kinds, code):
    rows = tx.execute("SELECT l.account_id, SUM(l.debit) AS d, SUM(l.credit) AS c FROM fin_lines l JOIN fin_entries e ON "
                      "e.id = l.entry_id JOIN fin_accounts a ON a.id = l.account_id WHERE a.code = ? AND e.day >= ? AND "
                      "e.day <= ? AND e.kind IN (" + ", ".join("?" * len(kinds)) + ") GROUP BY l.account_id",
                      (code, period.start.isoformat(), period.end.isoformat()) + tuple(kinds)).fetchall()
    return {(r["account_id"], None): (int(r["d"] or 0), int(r["c"] or 0)) for r in rows}


def budget_vs_actual(tx, budget_id):
    row = tx.execute("SELECT * FROM fin_budgets WHERE id = ?", (budget_id,)).fetchone() if str(budget_id).isdigit() else None
    if not row:
        return None
    b = dict(row)
    start, end = date.fromisoformat(b["start_day"]), date.fromisoformat(b["end_day"])
    planned = {r["account_id"]: r["amount"] for r in tx.execute(
        "SELECT account_id, amount FROM fin_budget_lines WHERE budget_id = ?", (b["id"],)).fetchall()}
    actual = defaultdict(int)
    accts = {a["id"]: a for a in accounts(tx, include_archived=True)}
    for (account_id, _), (d, c) in _sums(tx, start, end).items():
        if accts[account_id]["kind"] in ("income", "expense"):
            actual[account_id] += _natural(accts[account_id]["kind"], d, c)
    rows = []
    for a in accounts(tx, ("income", "expense"), include_archived=True):
        plan, act = planned.get(a["id"], 0), actual.get(a["id"], 0)
        if not plan and not act:
            continue
        variance = act - plan
        pct = change_pct(act, plan)
        # Spending over budget, or income short of it, is what needs attention; 10% either way is significant.
        bad = (variance > 0) if a["kind"] == "expense" else (variance < 0)
        rows.append({"account_id": a["id"], "name": a["name"], "kind": a["kind"], "budget": plan, "actual": act,
                     "variance": variance, "pct": pct, "remaining": plan - act if a["kind"] == "expense" else None,
                     "over": a["kind"] == "expense" and act > plan, "attention": bad and (plan == 0 or abs(pct or 0) >= 10)})
    totals = {k: {"budget": sum(r["budget"] for r in rows if r["kind"] == k),
                  "actual": sum(r["actual"] for r in rows if r["kind"] == k)} for k in ("income", "expense")}
    today = date.today()
    elapsed = 100 if today >= end else 0 if today < start else round(((today - start).days + 1) * 100 / ((end - start).days + 1))
    return {**b, "start": start, "end": end, "rows": rows, "totals": totals, "planned": planned, "elapsed": elapsed}


def overview(tx, period, today):
    ie = income_expenditure(tx, period)
    fees = invoices(tx, today)
    in_period = [i for i in fees if period.start.isoformat() <= i["issued_day"] <= period.end.isoformat()]
    net_billed = sum(i["net"] for i in in_period)
    collected = sum(min(i["received"], i["net"]) for i in in_period)
    cash_ids = {a["code"]: a for a in accounts(tx, include_archived=True) if a["grp"] == "cash"}
    cash = sum(balance_of(tx, a["id"], today) for a in cash_ids.values())
    recs = reconciliations(tx)
    last_bank = next((r for r in recs if r["code"] == BANK), None)
    # The trend is the 12 months up to the end of the period (or today, if that's sooner), whatever period is chosen.
    last = min(period.end, today)
    months = []
    for m in months_between(_add_months(date(last.year, last.month, 1), -11), last):
        t = income_expenditure(tx, m)
        months.append({"label": m.label[:3], "full": m.label, "in": t["total_income"], "out": t["total_spending"]})
    # The dashboard's other cards: where money came from and went, what needs attention, funds and the budget.
    bills = unpaid_bills(tx)
    owed_fees = [i for i in fees if i["outstanding"] > 0]
    overdue = [i for i in owed_fees if i["status"] == "overdue"]
    statuses = {k: 0 for k in ("paid", "part", "unpaid", "overdue")}
    for i in in_period:
        if i["status"] in statuses:
            statuses[i["status"]] += 1
    live = [b for b in budgets(tx) if b["start_day"] <= period.end.isoformat() and b["end_day"] >= period.start.isoformat()]
    budget = budget_vs_actual(tx, live[0]["id"]) if live else None
    return {"income": ie["total_income"], "spending": ie["total_spending"], "net": ie["net"],
            "sources": sorted(({"label": x["label"], "amount": x["total"]} for x in ie["income"] if x["total"] > 0),
                              key=lambda x: -x["amount"]),
            "uses": sorted(({"label": x["label"], "amount": x["total"]} for x in ie["spending"] if x["total"] > 0),
                           key=lambda x: -x["amount"]),
            "overdue_count": len(overdue), "overdue_families": len({i["family_id"] or i["student_name"] for i in overdue}),
            "owed_count": len(owed_fees), "statuses": statuses, "collected": collected,
            "bills": sum(b["owed"] for b in bills if b["kind"] == "bill"), "bills_count": sum(1 for b in bills if b["kind"] == "bill"),
            "pay_owed": sum(b["owed"] for b in bills if b["kind"] == "staff_owed"),
            "pay_owed_count": sum(1 for b in bills if b["kind"] == "staff_owed"),
            "funds": [f for f in fund_balances(tx, min(today, period.end)) if f["balance"] or not f["archived"]],
            "budget": budget, "over_budget": sum(1 for r in budget["rows"] if r["over"]) if budget else 0,
            "outstanding": sum(i["outstanding"] for i in fees if i["outstanding"] > 0),
            "overdue": sum(i["outstanding"] for i in fees if i["status"] == "overdue"),
            "collection": round(collected * 100 / net_billed) if net_billed > 0 else None, "billed": net_billed,
            "cash": cash, "last_bank": last_bank, "months": months,
            "recent": transactions(tx, limit=8), "has_entries": has_entries(tx), "has_opening": has_opening(tx)}


def sofa(tx, period):
    """The Statement of Financial Activities layout: income and spending in unrestricted and restricted columns,
    the net movement, and the funds brought and carried forward."""
    accts = {a["id"]: a for a in accounts(tx, include_archived=True)}
    fund_rows = {f["id"]: f for f in funds(tx, include_archived=True)}
    cols = ("unrestricted", "restricted")
    rows = {k: dict.fromkeys(cols, 0) for k in INCOME_GROUPS + EXPENSE_GROUPS}
    for (account_id, fund_id), (d, c) in _sums(tx, period.start, period.end, by_fund=True).items():
        a = accts[account_id]
        if a["kind"] in ("income", "expense"):
            col = "restricted" if fund_rows[fund_id]["restricted"] else "unrestricted"
            rows[a["grp"]][col] += _natural(a["kind"], d, c)
    before = fund_balances(tx, period.start - timedelta(days=1))
    opened = fund_balances(tx, period.end, period.start)
    bf = dict.fromkeys(cols, 0)
    for f0, f1 in zip(before, opened):
        bf["restricted" if f0["restricted"] else "unrestricted"] += f0["balance"] + f1["brought_forward"]

    def total(keys, col):
        return sum(rows[k][col] for k in keys)

    income = {c: total(INCOME_GROUPS, c) for c in cols}
    spending = {c: total(EXPENSE_GROUPS, c) for c in cols}
    net = {c: income[c] - spending[c] for c in cols}
    return {"income": [{"label": GROUP_LABEL[k], **rows[k], "total": sum(rows[k].values())} for k in INCOME_GROUPS],
            "spending": [{"label": GROUP_LABEL[k], **rows[k], "total": sum(rows[k].values())} for k in EXPENSE_GROUPS],
            "total_income": {**income, "total": sum(income.values())},
            "total_spending": {**spending, "total": sum(spending.values())},
            "net": {**net, "total": sum(net.values())}, "brought_forward": {**bf, "total": sum(bf.values())},
            "carried_forward": {c: bf[c] + net[c] for c in cols} | {"total": sum(bf.values()) + sum(net.values())},
            "funds": [f for f in fund_balances(tx, period.end, period.start)],
            "funds_before": {f["id"]: f["balance"] for f in before}}


def receipts_payments(tx, period):
    """Receipts and payments accounts: only money that actually moved, by what it was for, and the cash at each end.
    A correction reduces the line of the entry it corrects."""
    cf = cash_flow(tx, period)
    receipts, payments = defaultdict(int), defaultdict(int)
    cache = {}
    for e in transactions(tx, period.start, period.end):
        if not e["cash"] or e["kind"] == "opening":
            continue
        kind, groups = _effective(tx, e, cache)
        groups -= {"cash"}
        if "receivables" in groups:
            label = "Fee refunds" if kind == "refund" else GROUP_LABEL["tuition"]
        elif "payroll" in groups:
            label = GROUP_LABEL["salaries"]
        else:
            label = next((GROUP_LABEL[k] for k, _, _ in GROUPS if k in groups), "Other")
        incoming = (e["cash"] > 0) == (kind == e["kind"])
        (receipts if incoming else payments)[label] += e["cash"] if incoming else -e["cash"]
    receipts = {k: v for k, v in receipts.items() if v}
    payments = {k: v for k, v in payments.items() if v}
    return {"receipts": sorted(receipts.items()), "payments": sorted(payments.items()),
            "total_receipts": sum(receipts.values()), "total_payments": sum(payments.values()),
            "opening": cf["opening"] + next(l["amount"] for l in cf["lines"] if l["key"] == "opening"),
            "closing": cf["closing"]}


def family_totals(rows):
    out = defaultdict(lambda: {"students": set(), "net": 0, "received": 0, "outstanding": 0, "overdue": 0})
    for i in rows:
        f = out[i["family_id"] or f"(no Family ID) {i['student_name']}"]
        f["students"].add(i["student_name"])
        f["net"] += i["net"]
        f["received"] += i["received"]
        f["outstanding"] += max(i["outstanding"], 0)
        f["overdue"] += max(i["outstanding"], 0) if i["status"] == "overdue" else 0
    return sorted(({"family": k, **v, "students": ", ".join(sorted(v["students"]))} for k, v in out.items()),
                  key=lambda f: -f["outstanding"])


def check(tx):
    """Every entry balances and the whole ledger nets to zero. Returns a list of problems (empty when all is well)."""
    problems = []
    for r in tx.execute("SELECT entry_id, SUM(debit) AS d, SUM(credit) AS c FROM fin_lines GROUP BY entry_id").fetchall():
        if int(r["d"] or 0) != int(r["c"] or 0):
            problems.append(f"Entry {r['entry_id']} doesn't balance.")
    return problems
