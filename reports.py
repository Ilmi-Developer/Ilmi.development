"""Term reports: the settings the Reports tab calculates with, one school-wide value in site_settings ("reports").

The Reports tab (src/page.html, "Reports") works out each pupil's Quran and Memorisation grades for a term from what
is already recorded: Quran homework and level tests, Memo passes and tests, dated warnings, and Journey 2 Jannah
attendance. The rules are those of the school's 2026-27 report spreadsheet; the numbers they use are settings here, so
the admin can change them on the Reports tab without a code change:

- terms: each report's own dates, both days included. A report uses its term's dates, not the dashboard's pickers.
- pct: the percentage grade thresholds for grades 2, 3, 4 and 5 (below the first is grade 1).
- ratio: the class-relative thresholds for performance grades 2, 3, 4 and 5 (warnings run the other way: 4, 3, 2, 1).
- expected: Memorisation targets a pupil is expected to pass in a term (Progress).
- test_points, pass_points: Memorisation participation points for each test and each new target passed.
- lowest: the lowest grade a calculation gives (2); a teacher can still give a 1 by hand.

Teachers' own grades (overrides) are kept on each student in the class record (server.valid_student, "ro"), apart
from the calculated grades, which are never stored: they are worked out again whenever the tab is drawn.
"""
import base64
import hashlib
import io
import json
import re
import threading
import time
from datetime import date

from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request

import auth
import schools
import site_settings

bp = Blueprint("reports", __name__)

KEY = "reports"
TERM_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
NAME_MAX = 30
MAX_TERMS = 6
DEFAULTS = {
    "terms": [{"id": "t1-2026", "name": "Term 1", "from": "2026-09-12", "to": "2026-12-20"},
              {"id": "t2-2027", "name": "Term 2", "from": "2027-01-10", "to": "2027-05-23"}],
    "pct": [30, 50, 70, 90],
    "ratio": [0.7, 0.9, 1.1, 1.3],
    "expected": 8,
    "test_points": 1,
    "pass_points": 5,
    "lowest": 2,
}
# Grade keys a teacher can set by hand, per subject: Quran ("q") and Memorisation ("m"). Keep in step with RPQ and RPM
# in src/page.html. ef: Effort, mb: Manners & Behaviour, hw: Homework, pr: Pronunciation, fl: Fluency,
# cp: Class Participation, pg: Progress.
GRADES = {"q": ("ef", "mb", "hw", "pr", "fl"), "m": ("ef", "mb", "hw", "cp", "pg")}


def _day(text):
    try:
        return date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None


def _ascending(values, kind, low, high):
    return (isinstance(values, list) and len(values) == 4 and all(type(v) in kind and low <= v <= high for v in values)
            and all(a < b for a, b in zip(values, values[1:])))


def clean(value):
    """The settings as sent, checked and tidied, or None when anything is wrong."""
    if not isinstance(value, dict):
        return None
    terms = value.get("terms")
    if not isinstance(terms, list) or not 1 <= len(terms) <= MAX_TERMS:
        return None
    tidy_terms, ids = [], set()
    for term in terms:
        if not isinstance(term, dict):
            return None
        start, end = _day(term.get("from")), _day(term.get("to"))
        name = site_settings.tidy(term.get("name") if isinstance(term.get("name"), str) else "")
        term_id = term.get("id")
        if not isinstance(term_id, str) or not TERM_ID.fullmatch(term_id) or term_id in ids:
            return None
        if not name or len(name) > NAME_MAX or not start or not end or start > end:
            return None
        ids.add(term_id)
        tidy_terms.append({"id": term_id, "name": name, "from": start.isoformat(), "to": end.isoformat()})
    if not _ascending(value.get("pct"), (int,), 1, 100) or not _ascending(value.get("ratio"), (int, float), 0, 5):
        return None
    numbers = {}
    value = dict(value, lowest=value.get("lowest", DEFAULTS["lowest"]))  # settings saved before there was one
    for key, low, high in (("expected", 1, 100), ("test_points", 0, 20), ("pass_points", 0, 20), ("lowest", 1, 5)):
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            return None
        numbers[key] = value[key]
    return {"terms": tidy_terms, "pct": list(value["pct"]), "ratio": [float(r) for r in value["ratio"]], **numbers}


def read(tx):
    """The school's report settings: those saved on the Reports tab, or the defaults."""
    try:
        saved = clean(json.loads(site_settings.read(tx, KEY) or "null"))
    except ValueError:
        saved = None
    return saved or json.loads(json.dumps(DEFAULTS))


@bp.post("/api/report-settings")
def save():
    """The admin changes the report settings (the Reports tab's Report settings)."""
    if g.user["role"] != "admin":
        return jsonify(error="Only the admin can change the report settings."), 403
    value = clean(request.get_json(silent=True))
    if value is None:
        return jsonify(error="Check the term dates and numbers: something isn't valid."), 400
    with current_app.config["DB"].transaction() as tx:
        site_settings.write(tx, KEY, json.dumps(value, separators=(",", ":")))
    return jsonify(settings=value)


def valid_overrides(value):
    """A student's teacher grades ("ro"): {term id: {"q" or "m": {grade key: 1 to 5}}}."""
    return isinstance(value, dict) and len(value) <= 20 and all(
        isinstance(term, str) and TERM_ID.fullmatch(term) and isinstance(subjects, dict)
        and all(k in GRADES and isinstance(grades, dict)
                and all(key in GRADES[k] and type(v) is int and 1 <= v <= 5 for key, v in grades.items())
                for k, grades in subjects.items())
        for term, subjects in value.items())


# --- Branding and report emails ---------------------------------------------------------------------------------------
# Term reports go to parents in the school's own colours: its name, logo and brand colour, which the admin sets on the
# Reports page (Report settings). Nothing about a school is written in the code; with none set, a report carries the
# site title and Ilmi's olive. Every report carries a small Ilmi tag in its top right corner.

EMAIL = re.compile(r"[^@\s\"<>,;]{1,64}@[^@\s\"<>,;]{1,190}\.[A-Za-z]{2,24}")
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
DEFAULT_COLOUR = "#4d5a2c"
LOGO_MAX = 3_000_000  # bytes of the uploaded picture, before it is redrawn as a PNG of at most 512 pixels
SEND_MAX = 600  # reports in one send
SEND_GAP = 0.6  # seconds between two emails, under the email service's rate limit
LABELS = {"q": (("ef", "Effort"), ("mb", "Manners & behaviour"), ("hw", "Homework"), ("pr", "Pronunciation"),
                ("fl", "Fluency")),
          "m": (("ef", "Effort"), ("mb", "Manners & behaviour"), ("hw", "Homework"), ("cp", "Class participation"),
                ("pg", "Progress"))}
QAIDAH_BOOK = 13  # Qaidah Book levels 1 to 13, then the Tajweed Book (QBOOK in src/page.html)


def brand(tx):
    """The school's report branding, as the dashboard and the emails use it."""
    version = site_settings.read(tx, "brand_logo_v")
    # The name is only what the admin typed: the site title is the developer's, and stays off the school's pages.
    return {"name": site_settings.read(tx, "brand_name"),
            "colour": site_settings.read(tx, "brand_colour") or DEFAULT_COLOUR,
            "logo": logo_url(version) if version else "",
            "test_to": site_settings.read(tx, "report_test_to") or current_app.config.get("DEVELOPER_EMAIL", "")}


def logo_url(version, absolute=False):
    path = f"/brand/{current_app.config['DB'].current_id()}/logo-{version}.png"
    return auth.public_url(path) if absolute else path


def _ink_on(colour):
    """The type on the colour: white where it reaches 3:1 (the header's words are large), else dark ink."""
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, gr, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    lum = 0.2126 * channel(r) + 0.7152 * channel(gr) + 0.0722 * channel(b)
    return "#ffffff" if 1.05 / (lum + 0.05) >= 3 else "#1c2230"


def _dominant(image):
    """The logo's main colour: its most common colour that is neither near white, near black nor grey."""
    small = image.convert("RGBA").resize((64, 64))
    counts = {}
    for r, gr, b, a in small.getdata():
        if a < 128 or max(r, gr, b) - min(r, gr, b) < 40 or min(r, gr, b) > 225 or max(r, gr, b) < 35:
            continue
        key = (r // 8 * 8, gr // 8 * 8, b // 8 * 8)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    r, gr, b = max(counts, key=counts.get)
    return f"#{r:02x}{gr:02x}{b:02x}"


@bp.post("/api/report-branding")
def save_brand():
    """The admin sets the school's name, logo and colour for the reports sent to parents."""
    if g.user["role"] != "admin":
        return jsonify(error="Only the admin can change the school's branding."), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    name = site_settings.tidy(body.get("name") if isinstance(body.get("name"), str) else "")
    colour = body.get("colour") if isinstance(body.get("colour"), str) else ""
    test_to = (body.get("test_to") if isinstance(body.get("test_to"), str) else "").strip()
    if len(name) > 80 or (colour and not COLOUR.fullmatch(colour)) or (test_to and not EMAIL.fullmatch(test_to)):
        return jsonify(error="Check the name, colour and test address."), 400
    logo = None
    if isinstance(body.get("logo"), str) and body["logo"]:
        try:
            from PIL import Image, UnidentifiedImageError
            raw = base64.b64decode(body["logo"].split(",", 1)[-1], validate=True)
            if len(raw) > LOGO_MAX:
                return jsonify(error="That picture is too big. Choose one under 3 MB."), 400
            image = Image.open(io.BytesIO(raw))
            image.load()
            image = image.convert("RGBA")
            image.thumbnail((512, 512))
            out = io.BytesIO()
            image.save(out, "PNG", optimize=True)
            logo = out.getvalue()
        except (ValueError, OSError, Image.DecompressionBombError, UnidentifiedImageError):
            return jsonify(error="That file isn't a picture Ilmi can read. Use a PNG or JPEG."), 400
        if not colour:
            colour = _dominant(image)
    with current_app.config["DB"].transaction() as tx:
        site_settings.write(tx, "brand_name", name)
        site_settings.write(tx, "brand_colour", colour)
        site_settings.write(tx, "report_test_to", test_to)
        if logo is not None:
            site_settings.write(tx, "brand_logo", base64.b64encode(logo).decode())
            site_settings.write(tx, "brand_logo_v", hashlib.sha256(logo).hexdigest()[:12])
        elif body.get("remove_logo") is True:
            site_settings.write(tx, "brand_logo", "")
            site_settings.write(tx, "brand_logo_v", "")
        return jsonify(brand=brand(tx))


@bp.get("/brand/<int:school_id>/logo-<version>.png")
def brand_logo(school_id, version):
    """The school's logo, public so that email apps can show it. Its address changes with the picture."""
    router = current_app.config["DB"]
    if school_id == schools.CONTROL or not router.exists(school_id):
        abort(404)
    with router.use(school_id) as database:
        with database.transaction() as tx:
            data, current = site_settings.read(tx, "brand_logo"), site_settings.read(tx, "brand_logo_v")
    if not data or version != current:
        abort(404)
    response = Response(base64.b64decode(data), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def _grade(value):
    return value if type(value) is int and 1 <= value <= 5 else None


def clean_report(value):
    """One pupil's term report as the Reports page sends it, checked, or None."""
    from server import FOCUS_TAGS
    if not isinstance(value, dict) or not isinstance(value.get("class_id"), str) or type(value.get("i")) is not int:
        return None
    if not isinstance(value.get("name"), str) or not isinstance(value.get("label", ""), str) or len(value.get("label", "")) > 80:
        return None
    grades = {}
    for k, keys in GRADES.items():
        given = value.get(k)
        if not isinstance(given, dict) or any(x not in keys for x in given):
            return None
        grades[k] = {key: _grade(given.get(key)) for key in keys}
    att = value.get("att")
    if att is not None and not (isinstance(att, dict) and type(att.get("p")) is int and type(att.get("n")) is int
                                and 0 <= att["p"] <= att["n"] <= 400 and att["n"] > 0):
        return None
    memo = value.get("memo")
    if memo is not None and not (isinstance(memo, dict) and type(memo.get("passed")) is int and 0 <= memo["passed"] <= 500
                                 and isinstance(memo.get("now"), str) and len(memo["now"]) <= 60):
        return None
    focus = value.get("focus")
    if focus is not None and not (isinstance(focus, dict) and focus.get("f") in FOCUS_TAGS and type(focus.get("n")) is int):
        return None
    qa = value.get("qa", 0)
    if type(qa) is not int or not 0 <= qa <= 20:
        return None
    return {"class_id": value["class_id"], "i": value["i"], "name": value["name"], "label": value.get("label", ""),
            "grades": grades, "att": att, "memo": memo, "focus": focus, "qa": qa, "act": value.get("act") is not False}


def _day_text(day, year=True):
    d = date.fromisoformat(day)
    return f"{d.day} {d.strftime('%B')}" + (f" {d.year}" if year else "")


def render_report(report, term, school):
    """(subject, text, html) for one pupil's report."""
    colour = school["colour"]
    first = report["name"].split(" ")[0] or report["name"]
    sections = []
    for k, title in (("q", "Quran"), ("m", "Memorisation")):
        rows = [{"label": label, "grade": report["grades"][k][key], "lead": key == "ef"} for key, label in LABELS[k]]
        notes = []
        if k == "q":
            # The Quran level and targets passed are figures at the top, so the notes don't repeat them.
            if report["focus"]:
                notes.append(("Focus for next term", report["focus"]["f"]))
        elif report["memo"]:
            notes.append(("Working on", report["memo"]["now"]))
        sections.append({"title": title, "rows": rows, "notes": notes})
    att = report["att"]
    context = {"school": school["name"] or "Your school", "colour": colour, "ink": _ink_on(colour),
               "logo": school["logo_abs"],
               "ilmi": auth.public_url("/logo.png?v=4"), "site": auth.public_url("/"),
               "pupil": report["name"], "first": first, "label": report["label"], "term": term["name"],
               "dates": f"{_day_text(term['from'], term['from'][:4] != term['to'][:4])} to {_day_text(term['to'])}",
               "attendance": f"{round(100 * att['p'] / att['n'])}%" if att else "", "att": att,
               "level": report["qa"] or "", "book": ("Tajweed Book" if report["qa"] > QAIDAH_BOOK else "Qaidah Book") if report["qa"] else "",
               "passed": report["memo"]["passed"] if report["memo"] else "",
               "sections": sections, "act": report["act"],
               "support_email": current_app.config.get("SUPPORT_EMAIL", "")}
    subject = f"{first}'s {term['name']} report from {context['school']}"
    return (subject, render_template("email/report.txt", **context).strip() + "\n",
            render_template("email/report.html", **context))


def _school_for_email(tx):
    school = brand(tx)
    school["name"] = school["name"] or site_settings.read(tx, "title")
    version = site_settings.read(tx, "brand_logo_v")
    school["logo_abs"] = logo_url(version, absolute=True) if version else ""
    return school


def _term(tx, term_id):
    return next((t for t in read(tx)["terms"] if t["id"] == term_id), None)


@bp.post("/api/report-emails/preview")
def preview_report():
    """The email one pupil's report would be, for the admin to look at before sending."""
    if g.user["role"] != "admin":
        return jsonify(error="Only the admin can send reports."), 403
    body = request.get_json(silent=True) or {}
    report = clean_report(body.get("report"))
    with current_app.config["DB"].transaction() as tx:
        term = _term(tx, body.get("term_id"))
        if report is None or term is None:
            abort(400)
        subject, text, html = render_report(report, term, _school_for_email(tx))
    return jsonify(subject=subject, html=html)


def _deliver(app, school_id, queue, from_name):
    """Sends the queued reports one after another, and records how each went."""
    mailer, router = app.config["MAILER"], app.config["DB"]
    for row_id, to, subject, text, html in queue:
        ok = mailer.send(to, subject, text, html=html, tag="term_report", from_name=from_name)
        with router.use(school_id) as database:
            with database.transaction() as tx:
                tx.execute("UPDATE report_emails SET status = ? WHERE id = ?", ("sent" if ok else "failed", row_id))
        if not app.testing:
            time.sleep(SEND_GAP)


@bp.post("/api/report-emails")
def send_reports():
    """Emails each pupil's term report: to the test address, or to the parent's address on the pupil's details.
    The server finds every address itself, so a report only ever goes to the test address or that pupil's parent."""
    if g.user["role"] != "admin":
        return jsonify(error="Only the admin can send reports."), 403
    mailer = current_app.config["MAILER"]
    if not mailer.enabled:
        return jsonify(error="Email isn't set up on this site yet (RESEND_API_KEY and MAIL_FROM)."), 503
    body = request.get_json(silent=True) or {}
    mode = body.get("to")
    given = body.get("reports")
    if mode not in ("test", "parents") or not isinstance(given, list) or not 1 <= len(given) <= SEND_MAX:
        abort(400)
    reports = [clean_report(r) for r in given]
    if any(r is None for r in reports):
        abort(400)
    router = current_app.config["DB"]
    queue, skipped = [], []
    with router.transaction() as tx:
        term = _term(tx, body.get("term_id"))
        if term is None:
            abort(400)
        school = _school_for_email(tx)
        test_to = school["test_to"]
        if mode == "test" and not EMAIL.fullmatch(test_to or ""):
            return jsonify(error="Set a test address in Report settings first."), 400
        classes = {}
        for report in reports:
            if report["class_id"] not in classes:
                row = tx.execute("SELECT json FROM classrooms WHERE id = ?", (report["class_id"],)).fetchone()
                classes[report["class_id"]] = json.loads(row["json"]).get("S", []) if row else None
            pupils = classes[report["class_id"]]
            pupil = pupils[report["i"]] if pupils is not None and 0 <= report["i"] < len(pupils) else None
            if pupil is None or pupil.get("name") != report["name"]:
                skipped.append({"name": report["name"], "why": "not found in their class any more"})
                continue
            to = test_to if mode == "test" else pupil.get("pe", "")
            if not to:
                skipped.append({"name": report["name"], "why": "no parent email address"})
                continue
            subject, text, html = render_report(report, term, school)
            row_id = tx.insert("INSERT INTO report_emails (sent_at, term_id, class_id, student, recipient, mode, status, "
                               "sent_by) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                               (auth.now(), term["id"], report["class_id"], report["name"], to, mode, g.user["id"]))
            queue.append((row_id, to, subject, text, html))
    app = current_app._get_current_object()
    if queue:
        if app.testing:
            _deliver(app, router.current_id(), queue, school["name"])
        else:
            threading.Thread(target=_deliver, args=(app, router.current_id(), queue, school["name"]), daemon=True).start()
    return jsonify(queued=len(queue), skipped=skipped)


@bp.get("/api/report-emails")
def sent_reports():
    """The latest report emails, newest first, with how each went."""
    if g.user["role"] != "admin":
        return jsonify(error="Only the admin can send reports."), 403
    with current_app.config["DB"].transaction() as tx:
        rows = tx.execute("SELECT r.sent_at, r.term_id, r.student, r.recipient, r.mode, r.status, u.name AS by_name "
                          "FROM report_emails r LEFT JOIN users u ON u.id = r.sent_by ORDER BY r.id DESC LIMIT 100").fetchall()
    return jsonify(sent=[dict(r) for r in rows])
