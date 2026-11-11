"""Help or Requests: staff ask for help or make a request, and admins answer them on the FAQs page.

Teachers send a message from the Help or Requests button at the bottom of their dashboard. It lands on the admins'
FAQs page (/admin/faqs), where an admin types a reply and sends it in the app, or in the app and by email. The
teacher sees the reply in the same pop-up, with a dot on the button until they open it.

Every Sunday at 12:00 London time the weekly summary goes to HELP_SUMMARY_TO (a Railway variable; ADMIN_EMAIL if it isn't set):
everything sent since the Sunday before, or a line saying nothing was. A thread in the web process checks once a
minute; help_summaries records each week's email, so it goes once even with a restart or a second process, and a
summary missed while the site was down goes out when it comes back, if that is still the same Sunday or Monday.
"""
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, g, jsonify, request

import auth
import emails

KINDS = {"help": "Help", "request": "Request"}
BODY_MAX = 2000
REPLY_MAX = 4000
# Messages one person can send in a day, so a stuck key or a loop can't flood the FAQs page.
DAILY_LIMIT = 20
SUMMARY_HOUR = 12
# How late a missed summary may still go out.
SUMMARY_GRACE = 30 * 3600
CHECK_EVERY = 60

bp = Blueprint("helpdesk", __name__)


def clean(text, limit):
    """Trims each line and drops runs of blank lines, keeping the line breaks people typed."""
    lines = [" ".join(line.split()) for line in str(text or "").replace("\r\n", "\n").split("\n")]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()[:limit]


def own_items(tx, user_id):
    rows = tx.execute("SELECT id, kind, body, created_at, reply, replied_by_name, replied_at, emailed, seen_at "
                      "FROM help_requests WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 50",
                      (user_id,)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "body": r["body"], "at": r["created_at"], "reply": r["reply"],
             "by": r["replied_by_name"], "repliedAt": r["replied_at"], "emailed": bool(r["emailed"]),
             "unread": bool(r["reply"]) and not r["seen_at"]} for r in rows]


@bp.get("/api/help")
def mine():
    with auth.db().transaction() as tx:
        items = own_items(tx, g.user["id"])
    return jsonify(items=items, unread=sum(1 for i in items if i["unread"]),
                   email=current_app.config["MAILER"].enabled)


@bp.post("/api/help")
def send():
    data = request.get_json(silent=True) or {}
    kind, body = data.get("kind"), clean(data.get("body"), BODY_MAX)
    if kind not in KINDS:
        return jsonify(error="Choose Help or Request."), 400
    if not body:
        return jsonify(error="Type your message first."), 400
    with auth.db().transaction() as tx:
        sent_today = tx.execute("SELECT COUNT(*) AS n FROM help_requests WHERE user_id = ? AND created_at > ?",
                                (g.user["id"], auth.now() - auth.DAY)).fetchone()["n"]
        if sent_today >= DAILY_LIMIT:
            return jsonify(error="You've sent a lot of messages today. Try again tomorrow, or speak to the office."), 429
        item = tx.insert("INSERT INTO help_requests (kind, user_id, name, email, body, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (kind, g.user["id"], g.user["name"], g.user["email"], body, auth.now()))
        auth.audit(tx, "help.sent", detail={"item": item, "kind": kind})
        items = own_items(tx, g.user["id"])
    return jsonify(items=items, unread=sum(1 for i in items if i["unread"]),
                   email=current_app.config["MAILER"].enabled)


@bp.post("/api/help/seen")
def seen():
    with auth.db().transaction() as tx:
        tx.execute("UPDATE help_requests SET seen_at = ? WHERE user_id = ? AND reply IS NOT NULL AND seen_at IS NULL",
                   (auth.now(), g.user["id"]))
    return jsonify(ok=True)


# --- The FAQs page --------------------------------------------------------------

def faqs_page(status=200, **context):
    with auth.db().transaction() as tx:
        rows = tx.execute("SELECT * FROM help_requests ORDER BY created_at DESC, id DESC LIMIT 300").fetchall()
    waiting = [r for r in rows if not r["reply"]]
    answered = [r for r in rows if r["reply"]]
    return auth.page("admin_faqs.html", status, waiting=waiting, answered=answered, kinds=KINDS,
                     summary_to=summary_to(), **context)


@bp.get("/admin/faqs")
@auth.staff_required
def admin_faqs():
    return faqs_page()


@bp.post("/admin/faqs/<int:item_id>/reply")
@auth.staff_required
def admin_reply(item_id):
    auth.check_form_token()
    reply = clean(request.form.get("reply"), REPLY_MAX)
    by_email = request.form.get("send") == "email" and current_app.config["MAILER"].enabled
    if not reply:
        return faqs_page(400, error="Type a reply before sending it.")
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT * FROM help_requests WHERE id = ?", (item_id,)).fetchone()
        if not row:
            abort(404)
        # Answering again replaces the earlier reply and shows it to the teacher as new.
        tx.execute("UPDATE help_requests SET reply = ?, replied_by = ?, replied_by_name = ?, replied_at = ?, "
                   "emailed = ?, seen_at = NULL WHERE id = ?",
                   (reply, g.user["id"], g.user["name"], auth.now(), 1 if by_email else 0, item_id))
        auth.audit(tx, "help.replied", detail={"item": item_id, "email": by_email})
    sent = by_email and emails.send("help_reply", row["email"], name=row["name"], kind=KINDS[row["kind"]],
                                    body=row["body"], asked_at=row["created_at"], reply=reply, by=g.user["name"])
    if by_email and not sent:
        with auth.db().transaction() as tx:
            tx.execute("UPDATE help_requests SET emailed = 0 WHERE id = ?", (item_id,))
        return faqs_page(502, error=f"Your reply is saved and shows in {row['name']}'s app, but the email couldn't be "
                                    "sent. The site's service logs on Railway say why.")
    how = " in the app and by email" if sent else " in the app"
    return faqs_page(notice=f"Reply sent to {row['name']}{how}.")


@bp.post("/admin/faqs/<int:item_id>/delete")
@auth.admin_required
def admin_delete(item_id):
    auth.check_form_token()
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT name, kind FROM help_requests WHERE id = ?", (item_id,)).fetchone()
        if not row:
            abort(404)
        tx.execute("DELETE FROM help_requests WHERE id = ?", (item_id,))
        auth.audit(tx, "help.deleted", detail={"item": item_id})
    return faqs_page(notice=f"Deleted {row['name']}'s {KINDS[row['kind']].lower()} message.")


# --- The weekly summary ---------------------------------------------------------

def summary_to():
    return current_app.config.get("HELP_SUMMARY_TO") or current_app.config.get("ADMIN_EMAIL", "")


def last_summary_time(now=None):
    """The latest Sunday 12:00 London time at or before `now` (a Unix time), as an aware datetime."""
    here = datetime.fromtimestamp(now if now is not None else time.time(), auth.LONDON)
    sunday = here.date() - timedelta(days=(here.weekday() + 1) % 7)
    due = datetime(sunday.year, sunday.month, sunday.day, SUMMARY_HOUR, tzinfo=auth.LONDON)
    if due > here:
        due = datetime.combine(sunday - timedelta(days=7), due.timetz())
    return due


def summary_items(tx, start, end):
    return tx.execute("SELECT * FROM help_requests WHERE created_at >= ? AND created_at < ? ORDER BY created_at, id",
                      (start, end)).fetchall()


def send_summary_if_due(now=None):
    """Sends this week's summary if it's due and hasn't gone yet. Returns True when one was sent. Needs an app
    context."""
    if not current_app.config["MAILER"].enabled or not summary_to():
        return False
    now = now if now is not None else time.time()
    due = last_summary_time(now)
    end = int(due.timestamp())
    if now - end > SUMMARY_GRACE:
        return False
    week = due.date().isoformat()
    start = int((due - timedelta(days=7)).timestamp())
    with auth.db().transaction() as tx:
        if tx.execute("SELECT 1 FROM help_summaries WHERE week = ?", (week,)).fetchone():
            return False
        rows = summary_items(tx, start, end)
        # Claims the week first: a second process that gets here at the same moment inserts nothing.
        claimed = tx.execute("INSERT INTO help_summaries (week, sent_at, items) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                             (week, int(now), len(rows))).rowcount
    if not claimed:
        return False
    sent = emails.send("help_summary", summary_to(), **summary_context(rows, start, end))
    if not sent:
        # Let the next check try again.
        with auth.db().transaction() as tx:
            tx.execute("DELETE FROM help_summaries WHERE week = ?", (week,))
        return False
    print(f"Sent the Help or Requests summary for the week to {week} ({len(rows)} messages).", flush=True)
    return True


def summary_context(rows, start, end):
    items = [{"kind": KINDS[r["kind"]], "name": r["name"], "email": r["email"], "body": r["body"],
              "at": r["created_at"], "reply": r["reply"], "by": r["replied_by_name"]} for r in rows]
    return {"name": "Admin", "items": items, "start": start, "end": end,
            "helps": sum(1 for i in items if i["kind"] == "Help"),
            "requests": sum(1 for i in items if i["kind"] == "Request"),
            "waiting": sum(1 for i in items if not i["reply"]), "faqs": auth.public_url("/admin/faqs")}


def start_summary_thread(app):
    """Checks once a minute, from a background thread, whether the weekly summary is due."""
    def loop():
        warned = False
        while True:
            time.sleep(CHECK_EVERY)
            try:
                # The email's links need the site's address, which comes from the PUBLIC_URL Railway variable.
                with app.test_request_context(base_url=app.config["PUBLIC_URL"] or "http://localhost"):
                    if not app.config["MAILER"].enabled:
                        if not warned:
                            print("The weekly Help or Requests summary can't be emailed until RESEND_API_KEY and "
                                  "MAIL_FROM are set.", flush=True)
                            warned = True
                        continue
                    send_summary_if_due()
            except Exception as error:  # Keep checking: one failed check mustn't stop next week's email.
                print(f"The Help or Requests summary check failed: {error!r}", flush=True)
    threading.Thread(target=loop, name="help-summary", daemon=True).start()
