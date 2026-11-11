"""One-off admin page: remove the house coins given from a student's name menu on the Quran tab.

That menu no longer gives coins. A coin is one time in tg, tb or tr. It came from the Record page when an entry in
the student's hp has the same time and that reason's coin, or from a passed level test when the test's tk is that
time (a blue coin); any other coin was given from the Quran tab, as the House points page in src/page.html counts
it (hpAll). The page lists who has them first; removing them needs DELETE typed, keeps the version of each class from
just before in Class history and leaves every other coin as it is. It can be removed once it has been used.
"""
import json
import time

from flask import Blueprint, current_app, g, request

import auth

# Keep in step with TOKEN_POINTS and HOUSE_POINT_REASONS in server.py (server.py imports this module).
TOKEN_POINTS = {"tg": 5, "tb": 20, "tr": 50}
HOUSE_POINT_REASONS = {"bk": "tr", "lv": "tb", "hw": "tg", "ec": "tg", "cp": "tg"}

bp = Blueprint("quran_coins", __name__)


def quran_coins(s):
    """The times of this student's coins that came from the Quran tab, as {field: [times]}."""
    other = {}
    for x in s.get("hp", []):
        field = HOUSE_POINT_REASONS.get(x.get("r"))
        if field:
            other.setdefault(field, []).append(x.get("t"))
    for t in s.get("tests", []):
        if t.get("ok") and t.get("tk"):
            other.setdefault("tb", []).append(t["tk"])
    out = {}
    for field in TOKEN_POINTS:
        rest = list(other.get(field, []))
        for t in s.get(field, []):
            if t in rest:
                rest.remove(t)
            else:
                out.setdefault(field, []).append(t)
    return out


def plan(db):
    """[(class_id, revision, class name, state with the coins removed, [student rows])]."""
    out = []
    for row in db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall():
        state = json.loads(row["json"])
        students = []
        for s in state.get("S", []):
            found = quran_coins(s)
            if not found:
                continue
            counts = {k: len(found.get(k, [])) for k in TOKEN_POINTS}
            students.append({"name": s.get("name", ""), "sid": s.get("sid", ""), "house": s.get("house", ""), "counts": counts,
                             "points": sum(n * TOKEN_POINTS[k] for k, n in counts.items()),
                             "last": max(t for times in found.values() for t in times)})
            for field, times in found.items():
                kept = list(s.get(field, []))
                for t in times:
                    kept.remove(t)
                s[field] = kept
        if students:
            out.append((row["id"], row["revision"], state.get("name", row["id"]), state, students))
    return out


def _page(**context):
    with current_app.config["DB"].transaction() as db:
        rows = plan(db)
    students = [dict(st, cls=name, when=time.strftime("%d %b %Y", time.gmtime(st["last"] / 1000)) if st["last"] > 1e11 else "")
                for _, _, name, _, sts in rows for st in sts]
    students.sort(key=lambda x: (-x["points"], x["name"].lower()))
    houses = {}
    for st in students:
        houses[st["house"] or "No house"] = houses.get(st["house"] or "No house", 0) + st["points"]
    return auth.page("admin_quran_coins.html", students=students, houses=houses,
                     total=sum(st["points"] for st in students), **context)


@bp.get("/admin/remove-quran-coins")
@auth.admin_required
def preview():
    return _page()


@bp.post("/admin/remove-quran-coins")
@auth.admin_required
def remove():
    auth.check_form_token()
    if request.form.get("confirm", "").strip().lower() != "delete":
        return _page(error="Type DELETE in the box to confirm.")
    t = int(time.time())
    removed = 0
    with current_app.config["DB"].transaction() as db:
        for class_id, revision, _, state, students in plan(db):
            current = db.execute("SELECT json FROM classrooms WHERE id = ?", (class_id,)).fetchone()["json"]
            encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            # Keep the version from just before in Class history, then the one without the coins.
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision, current, g.user["id"], t))
            db.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (encoded, revision + 1, class_id))
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision + 1, encoded, g.user["id"], t))
            coins = {k: sum(st["counts"][k] for st in students) for k in TOKEN_POINTS}
            auth.audit(db, "class.quran_coins_removed", class_id, coins)
            removed += sum(coins.values())
    return _page(done=removed)
