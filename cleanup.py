"""One-off: clear every student's progress records for a fresh start.

Removes Quran scores, memo passes (with the pass count) and fails, level tests,
merits, warnings and missed breaks, house tokens (so house points go to 0) and
stars of the week. Students, their details and houses, classes, accounts and TV
screens stay. Every student goes back to the first memo target and no Qaidah level.

An admin opens /admin/clear-test-data to see exactly what would go and download a
backup of every class first; nothing changes until they confirm. The version of
each class from just before the clear is kept in Class history, so it can be
restored from there. Remove this file once it has been used.
"""
import json
import time

from flask import Blueprint, Response, current_app, g, request

import auth

TOKENS = ("tg", "tb", "tr")
KINDS = (("q", "Quran scores"), ("passes", "Memo passes"), ("fails", "Memo fails"), ("tests", "Level tests and book passes"),
         ("merits", "Merits"), ("warnings", "Warnings and missed breaks"), ("tokens", "House tokens"),
         ("stars", "Stars of the week"), ("targets", "Memo targets reset"), ("levels", "Qaidah levels reset"))

bp = Blueprint("cleanup", __name__)


def clear_student(s):
    """Removes this student's progress records, in place. Returns how many of each were removed."""
    n = {"q": len(s.get("q", {})), "passes": max(s.get("passes", 0), len(s.get("log", []))), "fails": len(s.get("fl", [])),
         "tests": len(s.get("tests", [])) + len(s.get("bp", [])), "merits": max(s.get("merits", 0), len(s.get("ml", []))),
         "warnings": 1 if s.get("warn") else 0, "tokens": sum(len(s.get(k, [])) for k in TOKENS),
         "stars": len(s.get("sw", [])), "targets": 1 if s.get("target", 1) > 1 else 0, "levels": 1 if s.get("qa") else 0}
    s.update(q={}, passes=0, log=[], lt=[], lx=[], hit=None, fl=[], tests=[], bp=[], hp=[], merits=0, ml=[], warn=0, sw=[], target=1, qa=0,
             **{k: [] for k in TOKENS})
    s.pop("mp", None)
    return n


def plan(db):
    """What clearing would change: [(class_id, revision, name, cleared state, counts per kind)]."""
    out = []
    for row in db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall():
        state = json.loads(row["json"])
        counts = dict.fromkeys((k for k, _ in KINDS), 0)
        for s in state.get("S", []):
            for k, v in clear_student(s).items():
                counts[k] += v
        out.append((row["id"], row["revision"], state.get("name", row["id"]), state, counts))
    return out


def _page(**context):
    with current_app.config["DB"].transaction() as db:
        rows = plan(db)
    classes = [{"name": name, "counts": counts} for _, _, name, _, counts in rows if any(counts.values())]
    totals = {k: sum(c["counts"][k] for c in classes) for k, _ in KINDS}
    return auth.page("admin_clear.html", kinds=KINDS, classes=classes, totals=totals, **context)


@bp.get("/admin/clear-test-data")
@auth.admin_required
def preview():
    return _page()


@bp.get("/admin/clear-test-data/backup.json")
@auth.admin_required
def backup():
    with current_app.config["DB"].transaction() as db:
        rows = db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall()
    body = json.dumps({"saved_at": int(time.time()),
                       "classrooms": {r["id"]: {"revision": r["revision"], "state": json.loads(r["json"])} for r in rows}},
                      ensure_ascii=False, indent=1)
    stamp = time.strftime("%Y-%m-%d-%H%M", time.gmtime())
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="ilmi-backup-{stamp}.json"'})


@bp.post("/admin/clear-test-data")
@auth.admin_required
def clear():
    auth.check_form_token()
    if request.form.get("confirm", "").strip().lower() != "clear":
        return _page(error="Type CLEAR in the box to confirm.")
    t = int(time.time())
    with current_app.config["DB"].transaction() as db:
        cleared = 0
        for class_id, revision, _, state, counts in plan(db):
            if not any(counts.values()):
                continue
            current = db.execute("SELECT json FROM classrooms WHERE id = ?", (class_id,)).fetchone()["json"]
            encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            # Keep the version from just before the clear in Class history, then the cleared one.
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision, current, g.user["id"], t))
            db.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (encoded, revision + 1, class_id))
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision + 1, encoded, g.user["id"], t))
            auth.audit(db, "class.progress_cleared", class_id, counts)
            cleared += 1
    return _page(done=cleared)
