"""House points added by hand for a whole house rather than a student: the physical house coins the admin counts
(teachers hand coins to students, who put them in the box at the office), and the school's points from before the
dashboard counted them. Since house points moved to physical coins this is the only place new points come from. Each entry is n coins of one colour, given_at in milliseconds like the dashboard's times (stored in house_points, see db.py). They count in
the house totals everywhere (the dashboard, the TV) and show on the dashboard's House points page as their own
source. Admins add and remove them on /admin/house-points, either as coins of each colour for one house or as a
points total for any of the houses in one go. A total is stored as the fewest coins that make it (every coin is a
multiple of 5 points, so a total must be too), so everything that reads the coins counts it unchanged. One
submission for one house is one row on the page (its coins share the house, time, admin and note) and is removed
as a whole.
"""
from datetime import date, datetime, time

from flask import Blueprint, g, jsonify, request

import auth

HOUSES = ("Makkah", "Al-Aqsa", "Madinah")  # HOUSES in server.py
COINS = (("tg", "Green", 5), ("tb", "Blue", 20), ("tr", "Red", 50))  # TOKEN_POINTS in server.py

bp = Blueprint("house_extra", __name__)


def entries(tx):
    return [dict(r) for r in tx.execute(
        "SELECT p.id, p.house, p.coin, p.n, p.note, p.given_at, u.name AS given_by FROM house_points p "
        "LEFT JOIN users u ON u.id = p.given_by ORDER BY p.given_at, p.id").fetchall()]


def totals(tx):
    """{house: points} from the entries."""
    points = {k: v for k, _, v in COINS}
    out = dict.fromkeys(HOUSES, 0)
    for e in entries(tx):
        if e["house"] in out and e["coin"] in points:
            out[e["house"]] += e["n"] * points[e["coin"]]
    return out


MAX_TOTAL = 100000  # points in one house's total at a time


def as_coins(points):
    """The fewest coins that make this many points, as {coin: n}, biggest first. points is a multiple of 5."""
    out = {}
    for k, _, v in sorted(COINS, key=lambda c: -c[2]):
        if points >= v:
            out[k], points = points // v, points % v
    return out


def _group_key(e):
    return (e["house"], e["given_at"], e["given_by"], e["note"])


def groups(tx):
    """The entries as the page lists them, newest first: the coins one submission added for one house together."""
    points = {k: v for k, _, v in COINS}
    names = {k: name for k, name, _ in COINS}
    out = {}
    for e in entries(tx):
        grp = out.setdefault(_group_key(e), {"id": e["id"], "house": e["house"], "note": e["note"],
                                              "given_by": e["given_by"], "given_at": e["given_at"], "coins": {}, "points": 0})
        grp["coins"][e["coin"]] = grp["coins"].get(e["coin"], 0) + e["n"]
        grp["points"] += e["n"] * points.get(e["coin"], 0)
    rows = sorted(out.values(), key=lambda r: (r["given_at"], r["id"]), reverse=True)
    for r in rows:
        r["coin_text"] = ", ".join(f"{r['coins'][k]} {names[k].lower()}" for k, _, _ in reversed(COINS) if r["coins"].get(k))
        r["added"] = datetime.fromtimestamp(r["given_at"] / 1000, auth.LONDON).strftime("%-d %b %Y")
    return rows


def _page(status=200, **context):
    with auth.db().transaction() as tx:
        rows = groups(tx)
    sums = {h: sum(r["points"] for r in rows if r["house"] == h) for h in HOUSES}
    today = datetime.now(auth.LONDON).date().isoformat()
    return auth.page("admin_house_points.html", status, rows=rows, sums=sums, houses=HOUSES, coins=COINS, today=today, **context)


def _counted_at():
    """The time to date the points with, from the form's day counted (today unless an earlier day is chosen), or an
    error message. It dates the points on the House points page."""
    today = datetime.now(auth.LONDON).date()
    try:
        counted = date.fromisoformat(request.form.get("day", "").strip() or today.isoformat())
    except ValueError:
        return None, "Choose the day the points were counted."
    if counted > today:
        return None, "The day the points were counted can't be in the future."
    return (auth.now() * 1000 if counted == today else
            int(datetime.combine(counted, time(12), auth.LONDON).timestamp() * 1000)), None


def _note():
    return " ".join(request.form.get("note", "").split())[:120]


@bp.get("/admin/house-points")
@auth.admin_required
def admin_page():
    return _page()


@bp.post("/admin/house-points")
@auth.admin_required
def add():
    auth.check_form_token()
    house, note = request.form.get("house", ""), _note()
    if house not in HOUSES:
        return _page(400, error="Choose a house.")
    counts = {}
    for k, name, _ in COINS:
        raw = request.form.get(k, "").strip() or "0"
        if not raw.isdigit() or int(raw) > 10000:
            return _page(400, error=f"{name} coins must be a whole number from 0 to 10000.")
        counts[k] = int(raw)
    if not any(counts.values()):
        return _page(400, error="Enter how many coins of at least one colour.")
    given_at, error = _counted_at()
    if error:
        return _page(400, error=error)
    with auth.db().transaction() as tx:
        for k, n in counts.items():
            if n:
                tx.insert("INSERT INTO house_points (house, coin, n, note, given_by, given_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (house, k, n, note or "Coins counted", g.user["id"], given_at))
        auth.audit(tx, "house_points.added", detail={"house": house, **counts, "note": note})
    points = sum(n * v for (k, _, v) in COINS for kk, n in counts.items() if kk == k)
    return _page(notice=f"Added {points} points to {house}.")


@bp.post("/admin/house-points/totals")
@auth.admin_required
def add_totals():
    """A points total for any of the houses in one go, such as a whole term's count."""
    auth.check_form_token()
    note, totals = _note(), {}
    for h in HOUSES:
        raw = request.form.get("pts-" + h, "").replace(",", "").strip() or "0"
        if not raw.isdigit() or int(raw) > MAX_TOTAL:
            return _page(400, error=f"{h}'s points must be a whole number from 0 to {MAX_TOTAL:,}.")
        if int(raw) % 5:
            return _page(400, error=f"{h}'s points must be a multiple of 5, as every house coin is.")
        if int(raw):
            totals[h] = int(raw)
    if not totals:
        return _page(400, error="Enter a points total for at least one house.")
    given_at, error = _counted_at()
    if error:
        return _page(400, error=error)
    with auth.db().transaction() as tx:
        for h, points in totals.items():
            for k, n in as_coins(points).items():
                tx.insert("INSERT INTO house_points (house, coin, n, note, given_by, given_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (h, k, n, note or "Points total", g.user["id"], given_at))
        auth.audit(tx, "house_points.total_added", detail={"totals": totals, "note": note})
    return _page(notice="Added " + ", ".join(f"{p} points to {h}" for h, p in totals.items()) + ".")


@bp.post("/admin/house-points/<int:entry_id>/delete")
@auth.admin_required
def delete(entry_id):
    auth.check_form_token()
    # The entry and the other coins added with it for the same house (one row on the page).
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT house, given_at, given_by, note FROM house_points WHERE id = ?", (entry_id,)).fetchone()
        if row:
            by = "given_by IS NULL" if row["given_by"] is None else "given_by = ?"
            same = [dict(r) for r in tx.execute(
                f"SELECT id, coin, n FROM house_points WHERE house = ? AND given_at = ? AND note = ? AND {by}",
                (row["house"], row["given_at"], row["note"]) + (() if row["given_by"] is None else (row["given_by"],))
            ).fetchall()]
            for e in same:
                tx.execute("DELETE FROM house_points WHERE id = ?", (e["id"],))
            auth.audit(tx, "house_points.removed", detail={"house": row["house"], "note": row["note"],
                                                           **{e["coin"]: e["n"] for e in same}})
    return _page(notice="Removed." if row else "That entry was already removed.")


@bp.get("/api/house-extra")
@auth.staff_required
def api():
    with auth.db().transaction() as tx:
        return jsonify([{k: e[k] for k in ("id", "house", "coin", "n", "note", "given_at")} for e in entries(tx)])
