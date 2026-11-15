"""Request Volunteer: a teacher asks for a volunteer from their dashboard, and the admin and HeadTeachers are pinged.

A teacher presses Request Volunteer on their overview and says why they need one. Everyone who sees every classroom
(the admin and HeadTeachers) gets the request as a ping in the corner of their dashboard, which stays until one of
them checks it. Whoever checks it, every other dashboard showing that ping (the other admins and HeadTeachers, and
the teacher who asked) sees it ticked off with who checked it; two seconds later it fades away. The dashboards ask
for the requests every few seconds, so a check reaches the others within one round.
"""
import json

from flask import Blueprint, abort, g, jsonify, request

import auth
import helpdesk

bp = Blueprint("volunteers", __name__)

NOTE_MAX = 500
# Requests one teacher can make in a day, so a stuck button can't flood everyone's dashboard.
DAILY_LIMIT = 10
# A checked request is still sent for this long, so every dashboard polling in that time sees the tick before it goes.
RECENT = 90


def item(row):
    return {"id": row["id"], "name": row["name"], "note": row["note"], "at": row["created_at"],
            "classes": [c for c in (row["classes"] or "").split("\n") if c],
            "checked": row["checked_at"] is not None, "by": row["checked_by_name"], "checkedAt": row["checked_at"],
            "mine": row["user_id"] == g.user["id"],
            "byMe": row["checked_at"] is not None and row["checked_by"] == g.user["id"]}


def visible(tx):
    """What this person's dashboard shows: every open request (and those checked a moment ago) for the admin and
    HeadTeachers; a teacher's own."""
    since = auth.now() - RECENT
    if auth.sees_all():
        rows = tx.execute("SELECT * FROM volunteer_requests WHERE checked_at IS NULL OR checked_at >= ? "
                          "ORDER BY created_at, id", (since,)).fetchall()
    else:
        rows = tx.execute("SELECT * FROM volunteer_requests WHERE user_id = ? AND (checked_at IS NULL OR checked_at >= ?) "
                          "ORDER BY created_at, id", (g.user["id"], since)).fetchall()
    return [item(r) for r in rows]


def answer(tx):
    return jsonify(items=visible(tx), now=auth.now(), check=auth.sees_all(), ask=g.user["role"] == "teacher")


@bp.get("/api/volunteers")
def requests_now():
    if g.user["role"] not in ("admin", "head", "teacher"):
        abort(403)
    with auth.db().transaction() as tx:
        return answer(tx)


@bp.post("/api/volunteers")
def ask():
    if g.user["role"] != "teacher":
        return jsonify(error="Only teachers request a volunteer."), 403
    data = request.get_json(silent=True) or {}
    note = helpdesk.clean(data.get("note"), NOTE_MAX)
    if not note:
        return jsonify(error="Say why you need a volunteer first."), 400
    with auth.db().transaction() as tx:
        today = tx.execute("SELECT COUNT(*) AS n FROM volunteer_requests WHERE user_id = ? AND created_at > ?",
                           (g.user["id"], auth.now() - auth.DAY)).fetchone()["n"]
        if today >= DAILY_LIMIT:
            return jsonify(error="You've asked for a volunteer a lot today. Speak to the office instead."), 429
        classes = []
        for r in tx.execute("SELECT c.id, c.json FROM classroom_members m JOIN classrooms c ON c.id = m.class_id "
                            "WHERE m.user_id = ? ORDER BY c.id", (auth.member_id(),)).fetchall():
            try:
                classes.append(str(json.loads(r["json"]).get("name") or r["id"]))
            except (ValueError, AttributeError):
                classes.append(r["id"])
        made = tx.insert("INSERT INTO volunteer_requests (user_id, name, note, classes, created_at) VALUES (?, ?, ?, ?, ?)",
                         (g.user["id"], g.user["name"], note, "\n".join(classes), auth.now()))
        auth.audit(tx, "volunteer.requested", detail={"request": made})
        return answer(tx)


@bp.post("/api/volunteers/<int:request_id>/check")
def check(request_id):
    if not auth.sees_all():
        return jsonify(error="Only the admin and HeadTeachers check volunteer requests."), 403
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT id, checked_at FROM volunteer_requests WHERE id = ?", (request_id,)).fetchone()
        if not row:
            abort(404)
        # Checked once: whoever gets there first is the one shown; a second check changes nothing.
        if row["checked_at"] is None and tx.execute(
                "UPDATE volunteer_requests SET checked_by = ?, checked_by_name = ?, checked_at = ? "
                "WHERE id = ? AND checked_at IS NULL", (g.user["id"], g.user["name"], auth.now(), request_id)).rowcount:
            auth.audit(tx, "volunteer.checked", detail={"request": request_id})
        return answer(tx)
