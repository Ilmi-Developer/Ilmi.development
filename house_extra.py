"""House points added by hand for a whole house rather than a student: the physical house coins the admin counts
(teachers hand coins to students, who put them in the box at the office), and the school's points from before the
dashboard counted them. Since house points moved to physical coins this is the only place new points come from. Each entry is n coins of one colour, given_at in milliseconds like the dashboard's times (stored in house_points, see db.py). They count in
the house totals everywhere (the dashboard, the TV) and show on the dashboard's House points page as their own
source. Admins add and remove them on /admin/house-points.
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


def _page(status=200, **context):
    with auth.db().transaction() as tx:
        rows = entries(tx)[::-1]  # newest first
    points = {k: v for k, _, v in COINS}
    names = {k: name for k, name, _ in COINS}
    for r in rows:
        r["points"], r["colour"] = r["n"] * points.get(r["coin"], 0), names.get(r["coin"], r["coin"])
        r["added"] = datetime.fromtimestamp(r["given_at"] / 1000, auth.LONDON).strftime("%-d %b %Y")
    sums = {h: sum(r["points"] for r in rows if r["house"] == h) for h in HOUSES}
    today = datetime.now(auth.LONDON).date().isoformat()
    return auth.page("admin_house_points.html", status, rows=rows, sums=sums, houses=HOUSES, coins=COINS, today=today, **context)


@bp.get("/admin/house-points")
@auth.admin_required
def admin_page():
    return _page()


@bp.post("/admin/house-points")
@auth.admin_required
def add():
    auth.check_form_token()
    house, note = request.form.get("house", ""), " ".join(request.form.get("note", "").split())[:120]
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
    # The day the coins were counted, today unless an earlier day is chosen; it dates the points on the House points page.
    today = datetime.now(auth.LONDON).date()
    try:
        counted = date.fromisoformat(request.form.get("day", "").strip() or today.isoformat())
    except ValueError:
        return _page(400, error="Choose the day the coins were counted.")
    if counted > today:
        return _page(400, error="The day the coins were counted can't be in the future.")
    given_at = auth.now() * 1000 if counted == today else \
        int(datetime.combine(counted, time(12), auth.LONDON).timestamp() * 1000)
    with auth.db().transaction() as tx:
        for k, n in counts.items():
            if n:
                tx.insert("INSERT INTO house_points (house, coin, n, note, given_by, given_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (house, k, n, note or "Coins counted", g.user["id"], given_at))
        auth.audit(tx, "house_points.added", detail={"house": house, **counts, "note": note})
    points = sum(n * v for (k, _, v) in COINS for kk, n in counts.items() if kk == k)
    return _page(notice=f"Added {points} points to {house}.")


@bp.post("/admin/house-points/<int:entry_id>/delete")
@auth.admin_required
def delete(entry_id):
    auth.check_form_token()
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT house, coin, n, note FROM house_points WHERE id = ?", (entry_id,)).fetchone()
        if row:
            tx.execute("DELETE FROM house_points WHERE id = ?", (entry_id,))
            auth.audit(tx, "house_points.removed", detail=dict(row))
    return _page(notice="Removed." if row else "That entry was already removed.")


@bp.get("/api/house-extra")
@auth.staff_required
def api():
    with auth.db().transaction() as tx:
        return jsonify([{k: e[k] for k in ("id", "house", "coin", "n", "note", "given_at")} for e in entries(tx)])
