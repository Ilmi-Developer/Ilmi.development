"""Ilmi's server: staff sign-in and shared class records, for many schools.

Records are kept in PostgreSQL when DATABASE_URL is set (on Railway, a reference
to the PostgreSQL service's DATABASE_URL). Without it they are kept in SQLite
files in DATA_DIR, which is meant for running locally. See db.py.

Every school has a database of its own, and each request works in the database
of the school its sign-in or link belongs to (schools.py). In the code below,
`database` is all of them: `database.transaction()` opens the current school's.

Each classroom is one row of its school's `classrooms` table. Classrooms are
created from the dashboard (CSV upload) by the school's Office (role "admin").
Teachers see and edit only the classrooms the Office has assigned to them. Every save and delete is recorded in
`classroom_history` and `audit_log`, which are kept for RETENTION_DAYS.
"""
import base64
import json
import logging
import os
import re
import secrets
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, abort, g, jsonify, redirect, request
from markupsafe import escape
from werkzeug.exceptions import HTTPException, InternalServerError
from werkzeug.middleware.proxy_fix import ProxyFix

import assistant
import auth
import avatars
import themes
import cleanup
import set_houses
import site_settings
import export
import house_extra
import quran_coins
import memo_import
import helpdesk
import schools
import tv
from db import DB_ERRORS
from mailer import Mailer

ROOT = Path(__file__).resolve().parent
CLASS_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
DETAILS = ("sid", "fid", "gender", "year", "sy", "ist", "dob")
# Student IDs given by the school, like SE263; new students get the next one.
STUDENT_NUMBER = re.compile(r"SE(\d+)", re.IGNORECASE)
MAX_CLASSES = 500
HOUSES = ("Makkah", "Al-Aqsa", "Madinah")
FEEDBACK_MAX = 300  # keep in step with maxlength on the Record page's note box (src/page.html)
# The focus areas a tester can tick on a Quran or Memo level test, the same list for both; keep in step with
# FOCUS in src/page.html.
FOCUS_TAGS = ("Pronunciation", "Letter Recognition", "Harakah", "Stretched Harakah", "Sukūn", "Shaddah", "Madd Stretch",
              "Special Writing", "Stopping", "Stopping Symbols", "Fluency in One line or verse", "Ghunnah", "Qalqalah",
              "Lām of Allāh", "Rules of Rā", "FM Letters", "Mim Sakinah", "Nūn Sakinah & Tanwin", "Madd")
QAIDAH_LEVELS = 20  # keep in step with QAIDAH in src/page.html; 1 to 13 are the Qaidah Book, 14 to 20 the Tajweed Book
BOOKS = ("q", "q1", "q2", "t", "m1", "m2")  # book passes, the keys of BOOKS in src/page.html
# House tokens: each student's list of award times per token. Keep in step with TOK in src/page.html.
TOKEN_POINTS = {"tg": 5, "tb": 20, "tr": 50}  # green, blue, red (book completion)
# House points given on the Record page, by what for, and the token each one gives (HPR in src/page.html).
HOUSE_POINT_REASONS = {"bk": "tr", "lv": "tb", "hw": "tg", "ec": "tg", "cp": "tg"}
# Reachable without signing in. /welcome is Ilmi's start page, where anyone signed out arrives.
PUBLIC = {"/login", "/login/code", "/forgot", "/healthz", "/logo.png", "/auth.css", "/intro.js", "/code.js", "/favicon.png",
          "/favicon.ico", "/welcome", "/welcome.css", "/welcome.js", "/wash.js"}
# Atkinson Hyperlegible Next stays for the TV display; the dashboard and admin pages use Nunito for text, Fraunces
# (soft) for the wordmark, titles, figures and large italic lines (both made by tools/art/type.py), and Amiri for Arabic:
# the letters of علمي for the name, and the start page's hadith and values in a second subset (see DESIGN.md and
# tools/art/fonts.py).
FONTS = {"nunito.woff2", "fraunces.woff2", "fraunces-italic.woff2", "atkinson-next-latin.woff2",
         "atkinson-next-latin-ext.woff2", "amiri-ilmi.woff2", "amiri-text.woff2"}
# The watercolour washes and paper texture (static/art), public like the fonts.
ART = {"paper.webp", "wash-band.webp", "wash-cloud.webp", "wash-low.webp", "wash-mist.webp", "wash-page.webp", "wash-panel.webp",
       "wash-rail.webp", "wash-soft.webp", "splat-a.webp", "splat-b.webp", "splat-c.webp"}
# The scripts and styles of the signed-in pages: they need a sign-in but belong to no school, so the developer's own
# pages can load them before any school is open.
SHELL_FILES = {"/developer.css", "/menu.js", "/dock.js", "/warm-tooltip.js", "/avatar.js", "/theme.js"}
# /tv/<token> is a TV screen link; tv.py checks the token.
PUBLIC_PREFIXES = ("/invite/", "/reset/", "/tv/", "/present/", "/fonts/", "/art/")


def coins(state):
    """Every house coin in a class, counted by colour and the time it was given."""
    return Counter((k, t) for s in state.get("S", []) for k in TOKEN_POINTS for t in s.get(k, []))


def valid_state(state):
    """Validate the structure consumed by the dashboard before replacing a class."""
    if not isinstance(state, dict):
        return False
    if not isinstance(state.get("name"), str) or not state["name"].strip():
        return False
    try:
        date.fromisoformat(state["fcd"])
    except (KeyError, TypeError, ValueError):
        return False
    levels = state.get("L")
    if not isinstance(levels, list) or not levels or not all(type(n) is int and n > 0 for n in levels):
        return False
    # Numbered Juz items that follow the book (198 An-Naba onwards). Older records have none.
    juz_items = state.get("Q", 0)
    if type(juz_items) is not int or juz_items < 0:
        return False
    students = state.get("S")
    if not isinstance(students, list):
        return False
    # Ex students ("X"): removed from the class but kept, with the day they left ("left"). Nothing else counts them.
    former = state.get("X", [])
    if not isinstance(former, list):
        return False
    if not all(valid_student(s, levels, juz_items) for s in students):
        return False
    if not all(valid_student(s, levels, juz_items) and isinstance(s.get("left"), str)
               and DAY.fullmatch(s["left"]) for s in former):
        return False
    return True


def valid_student(student, levels, juz_items):
    """Validate one student record of a class."""
    if not isinstance(student, dict) or not isinstance(student.get("name"), str):
        return False
    if not all(type(student.get(k)) is int and student[k] >= 0 for k in ("target", "passes", "merits", "warn")):
        return False
    if not 1 <= student["target"] <= sum(levels) + juz_items + 1 or student["warn"] > 3:
        return False
    for key in ("log", "lt", "ml"):
        if not isinstance(student.get(key), list) or not all(type(n) in (int, float) and n >= 0 for n in student[key]):
            return False
    # Fails are dated like passes but never change the target or the pass count.
    fails = student.get("fl", [])
    if not isinstance(fails, list) or not all(type(n) in (int, float) and n >= 0 for n in fails):
        return False
    if not isinstance(student.get("lx"), list) or not all(isinstance(x, str) for x in student["lx"]):
        return False
    # Details from the CSV upload or the student's profile: Student ID, Family ID, gender, madrasah year group,
    # school year group, Islamic Studies teacher and date of birth.
    if not all(isinstance(student.get(k, ""), str) for k in DETAILS):
        return False
    if student.get("dob", "") and not DAY.fullmatch(student["dob"]):
        return False
    # Details changed by hand on the profile ("ed"), which a later CSV upload leaves alone, and the earlier
    # Student IDs and names it had ("xid", "xn"), so the upload still recognises the student.
    if not isinstance(student.get("ed", []), list) or not all(k in DETAILS + ("name", "house", "class") for k in student.get("ed", [])):
        return False
    if not all(isinstance(student.get(k, []), list) and all(isinstance(x, str) for x in student.get(k, [])) for k in ("xid", "xn")):
        return False
    # Qaidah level, set on the Record page: 0 until set, then 1 to QAIDAH_LEVELS.
    qaidah = student.get("qa", 0)
    if type(qaidah) is not int or not 0 <= qaidah <= QAIDAH_LEVELS:
        return False
    # Level tests from the Record page: day, Quran ("q") or Memo ("m"), level or target, 0 to 5 score (none for one
    # imported from a sheet without scores), passed, for a pass, when its blue token was given, and for a surah
    # chosen by item number, the surah as written, the tester's note ("fb", optional) and the focus areas they ticked
    # for the student to work on ("fa", optional, from FOCUS_TAGS, each once).
    tests = student.get("tests", [])
    if not isinstance(tests, list) or not all(
            isinstance(x, dict) and isinstance(x.get("d"), str) and DAY.fullmatch(x["d"]) and x.get("k") in ("q", "m")
            and type(x.get("n")) is int and x["n"] >= 1 and type(x.get("sc", 0)) is int and 0 <= x.get("sc", 0) <= 5
            and type(x.get("ok")) is bool and type(x.get("pq", 0)) is int and 0 <= x.get("pq", 0) <= QAIDAH_LEVELS
            and type(x.get("tk", 0)) in (int, float) and x.get("tk", 0) >= 0
            and isinstance(x.get("x", ""), str) and len(x.get("x", "")) <= 80
            and isinstance(x.get("fb", ""), str) and len(x.get("fb", "")) <= FEEDBACK_MAX
            and isinstance(x.get("fa", []), list) and all(f in FOCUS_TAGS for f in x.get("fa", []))
            and len(set(x.get("fa", []))) == len(x.get("fa", []))
            for x in tests):
        return False
    # Star of the week: the Monday of each week the student was chosen.
    stars = student.get("sw", [])
    if not isinstance(stars, list) or not all(isinstance(d, str) and DAY.fullmatch(d) for d in stars):
        return False
    # House points: the student's house (from the CSV) and the times each token was given.
    if student.get("house", "") not in ("",) + HOUSES:
        return False
    for key in TOKEN_POINTS:
        tokens = student.get(key, [])
        if not isinstance(tokens, list) or not all(type(n) in (int, float) and n >= 0 for n in tokens):
            return False
    # Book passes from the Record page: when ("t") and which book ("b", one of BOOKS).
    # They give no house points.
    books = student.get("bp", [])
    if not isinstance(books, list) or not all(
            isinstance(x, dict) and type(x.get("t")) in (int, float) and x["t"] >= 0 and x.get("b") in BOOKS for x in books):
        return False
    # House points from the Record page: when ("t", the time its token was given) and what for ("r").
    given = student.get("hp", [])
    if not isinstance(given, list) or not all(
            isinstance(x, dict) and type(x.get("t")) in (int, float) and x["t"] >= 0 and x.get("r") in HOUSE_POINT_REASONS
            for x in given):
        return False
    if len(student["log"]) != len(student["lt"]) or len(student["log"]) != len(student["lx"]):
        return False
    # The Quran tab scores out of 5, starting at 0; -1 marks the student absent that day.
    if not isinstance(student.get("q"), dict) or not all(type(v) is int and -1 <= v <= 5 for v in student["q"].values()):
        return False
    # The Memo tab's day tracker: "t" had a memorisation test, "l" went down for a level test. Absence lives in "q".
    marks = student.get("mt", {})
    if not isinstance(marks, dict) or not all(isinstance(d, str) and DAY.fullmatch(d) and v in ("t", "l")
                                               for d, v in marks.items()):
        return False
    return True




def encode_state(state):
    return json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def record_history(db, class_id, revision, encoded, user_id, via=None, change=None):
    """Adds a history entry, or updates this person's latest one if it is recent. A save made through the
    assistant ("via") always gets its own entry and audit line, so each one can be found and restored."""
    t = int(time.time())
    last = None if via else db.execute("SELECT id, saved_by, saved_at, json FROM classroom_history WHERE class_id = ? "
                                       "ORDER BY id DESC LIMIT 1", (class_id,)).fetchone()
    if last and last["json"] is not None and last["saved_by"] == user_id and t - last["saved_at"] < HISTORY_COALESCE:
        db.execute("UPDATE classroom_history SET revision = ?, json = ? WHERE id = ?", (revision, encoded, last["id"]))
        return
    db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
               (class_id, revision, encoded, user_id, t))
    detail = {"revision": revision}
    if via:
        detail.update(via=via, change=change or "")
    auth.audit(db, "class.saved", class_id, detail, user_id=user_id)


def update_classroom(db, class_id, state, revision, user_id, via=None, change=None):
    """The one way an existing classroom's record is replaced, by the dashboard and the assistant alike: the record
    must pass valid_state and still be at `revision`, and the save goes into Class history and Activity. Returns the
    new revision, or None when the classroom changed (or went) since `revision`."""
    if not valid_state(state):
        raise ValueError("The classroom record isn't valid.")
    encoded = encode_state(state)
    cursor = db.execute("UPDATE classrooms SET json = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                        (encoded, class_id, revision))
    if not cursor.rowcount:
        return None
    record_history(db, class_id, revision + 1, encoded, user_id, via, change)
    return revision + 1


# The dashboard, TV and board pages each carry their script inline. Only <script> elements the server sent run: each
# response marks its own with a new random nonce (see private_responses), so text that ever slipped into the page as
# HTML could not run as a script.
DASHBOARD_CSP = ("default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                 "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
                 "object-src 'none'")
PAGE_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'")
# The sign-in page also runs one script of ours, /intro.js, which focuses the email field after the opening; the
# signed-in admin and account pages run /menu.js, which folds and unfolds the menu's groups, and /dock.js and
# /warm-tooltip.js, the menu's dock. All of them, and the start page, run /wash.js, which tells the wash page turn
# where it starts (it reads and writes one sessionStorage entry, the last press's position, and nothing else).
LOGIN_CSP = PAGE_CSP + "; script-src 'self'"
# The emailed sign-in code page (sent from POST /login and POST /login/code) runs /code.js, the animated code boxes,
# which sends the finished code with fetch so the boxes can show whether it was right before the page moves on.
CODE_CSP = LOGIN_CSP + "; connect-src 'self'"
# The Account page runs /avatar.js, the profile picture cropper, which shows the chosen photo from a blob: address
# and sends the cropped picture with fetch.
ACCOUNT_CSP = PAGE_CSP.replace("img-src 'self'", "img-src 'self' blob:") + "; script-src 'self'; connect-src 'self'"
# Email previews are styled inline, as emails must be; they have no scripts or forms.
EMAIL_PREVIEW_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'none'; "
                     "frame-ancestors 'none'; base-uri 'none'")
# A classroom saved again by the same person within this time updates its latest
# history entry instead of adding one, so that history doesn't grow with every click.
HISTORY_COALESCE = 15 * 60
# What an error page says. Messages the app gives on purpose (auth.SHOWN_ERRORS) are shown instead; nothing from an unexpected error (a stack trace, a path, a database message) ever reaches the browser.
ERRORS = {400: ("Something went wrong", "The request couldn't be used. Go back, reload the page and try again."),
          401: ("Sign in again", "Your session has ended. Sign in again."),
          403: ("Not allowed", "Your account can't open this page."),
          404: ("Page not found", "There's nothing at this address."),
          405: ("Not allowed", "This page can't be used that way."),
          413: ("Too large", "That was too large to send. Files must be under 2 MB."),
          429: ("Too many requests", "Wait a few minutes, then try again."),
          500: ("Something went wrong", "The dashboard hit a problem. Try again in a minute; if it keeps happening, "
                                        "tell the office.")}


class RailwayProxy:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        real_ip = environ.get("HTTP_X_REAL_IP", "").strip()
        if real_ip:
            environ["REMOTE_ADDR"] = real_ip[:64]
        return self.wsgi_app(environ, start_response)


# Opening the site in a new tab or window always starts at the sign-in page. A tab that has just signed in is
# marked in sessionStorage (which lasts for that tab only, through refreshes); a dashboard loaded in a tab without
# the mark signs out and goes to the sign-in page. If sessionStorage is blocked, the 20-minute idle limit still applies.
# A "Stay signed in" sign-in (ME.stay) is meant to carry over to new tabs, so it is left alone.
TAB_GUARD = ("(function(){try{var k='mysafar.tab';if(ME.fresh){document.documentElement.classList.add('fresh');"
             "sessionStorage.setItem(k,'1');return}"
             "if(ME.stay||sessionStorage.getItem(k))return;window.SIGNING_OUT=true;"
             "document.documentElement.style.visibility='hidden';var f=new FormData();f.append('csrf',ME.csrf);"
             "fetch('/logout',{method:'POST',body:f,credentials:'same-origin'}).then(go,go);"
             "function go(){location.replace('/login')}}catch(e){}})();")


def script_json(value):
    """JSON that is safe inside a <script> element."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def hide_link_tokens(record):
    """Flask logs the path of a request that fails; an invite or reset link's token stays out of it."""
    record.msg, record.args = auth.hide_link_tokens(record.getMessage()), None
    return True


def create_app(test_config=None):
    app = Flask(__name__, static_folder=None, template_folder=str(ROOT / "templates"))
    app.logger.addFilter(hide_link_tokens)
    app.config.update(
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        DATA_DIR=os.environ.get("DATA_DIR", str(ROOT / ".local-data")),
        ADMIN_EMAIL=os.environ.get("ADMIN_EMAIL", ""),
        # The one developer account: it sees every school and can add schools (schools.py). Nobody can be given
        # this role from the site; it belongs to this email alone. Without it there is no developer.
        DEVELOPER_EMAIL=os.environ.get("DEVELOPER_EMAIL", "").strip(),
        DASHBOARD_PASSWORD=os.environ.get("DASHBOARD_PASSWORD", ""),
        ADMIN_RESET_PASSWORD=os.environ.get("ADMIN_RESET_PASSWORD", ""),
        COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") != "0",
        # The site's address in invite and reset links. Railway's own RAILWAY_PUBLIC_DOMAIN can name a
        # domain that no longer exists, so it isn't used; without PUBLIC_URL the request's address is.
        PUBLIC_URL=os.environ.get("PUBLIC_URL", ""),
        TRUST_PROXY=bool(os.environ.get("RAILWAY_ENVIRONMENT_ID")),
        RESEND_API_KEY=os.environ.get("RESEND_API_KEY", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", ""),
        MAIL_REDIRECT_TO=os.environ.get("MAIL_REDIRECT_TO", ""),
        # Admins' emailed sign-in code (it also needs RESEND_API_KEY and MAIL_FROM). On unless LOGIN_CODES=off,
        # for a time when email can't be sent.
        LOGIN_CODES=os.environ.get("LOGIN_CODES", "on").strip().lower() not in ("0", "off", "false", "no"),
        # Where replies to the dashboard's emails go. Defaults to SUPPORT_EMAIL.
        MAIL_REPLY_TO=os.environ.get("MAIL_REPLY_TO", "").strip(),
        RETENTION_DAYS=int(os.environ.get("RETENTION_DAYS", "14")),
        # Shown on the password pages so staff can contact the school office directly.
        SUPPORT_EMAIL=os.environ.get("SUPPORT_EMAIL", "").strip(),
        # Who gets the weekly Help or Requests summary on Sundays at 12:00 (see helpdesk.py).
        HELP_SUMMARY_TO=os.environ.get("HELP_SUMMARY_TO", "").strip(),
        # The staff assistant, Ask Ilmi (see assistant.py): off unless ASSISTANT_ENABLED is on. Then the Anthropic API key,
        # the model, and how long its log is kept.
        ASSISTANT_ENABLED=os.environ.get("ASSISTANT_ENABLED", "").strip().lower() in ("1", "on", "true", "yes"),
        ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        ASSISTANT_MODEL=os.environ.get("ASSISTANT_MODEL", "").strip(),
        # Gemma on Cloudflare Workers AI, free up to a daily allowance: the account ID and an API token with
        # Workers AI Read permission. When both are set the assistant uses Cloudflare (ASSISTANT_PROVIDER can override).
        CLOUDFLARE_ACCOUNT_ID=os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(),
        CLOUDFLARE_API_TOKEN=os.environ.get("CLOUDFLARE_API_TOKEN", "").strip(),
        # Google's Gemini API key (free tier); when set, the assistant uses Gemini unless ASSISTANT_PROVIDER says otherwise.
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", "").strip(),
        ASSISTANT_PROVIDER=os.environ.get("ASSISTANT_PROVIDER", "").strip(),
        ASSISTANT_LOG_DAYS=int(os.environ.get("ASSISTANT_LOG_DAYS", "90")),
        # Each teacher's and HeadTeacher's daily token allowance for the assistant; 0 turns it off.
        ASSISTANT_DAILY_TOKENS=int(os.environ.get("ASSISTANT_DAILY_TOKENS", str(assistant.DAILY_TOKENS))),
    )
    if test_config:
        app.config.update(test_config)
    if app.config["TRUST_PROXY"]:
        # Railway's edge proxy sets X-Forwarded-Proto and X-Forwarded-Host to the public request,
        # and X-Real-IP to the client's address (used for sign-in limits and the activity log).
        app.wsgi_app = RailwayProxy(ProxyFix(app.wsgi_app, x_proto=1, x_host=1))
    database = schools.Schools(app.config["DATABASE_URL"].strip(), app.config["DATA_DIR"], app.config.get("TESTING"))
    database.migrate()
    database.ensure_developer(app.config["DEVELOPER_EMAIL"], app.config["DASHBOARD_PASSWORD"])
    auth.bootstrap_admin(database, app.config["ADMIN_EMAIL"], app.config["DASHBOARD_PASSWORD"],
                         developer=app.config["DEVELOPER_EMAIL"])
    database.index_emails(auth.norm_email(app.config["DEVELOPER_EMAIL"]))
    auth.reset_admin(database, app.config["ADMIN_EMAIL"], app.config["ADMIN_RESET_PASSWORD"])
    app.config["DB"] = database
    if not app.config.get("TESTING"):
        with database.transaction() as db:
            memo_import.log_summary(db, "at startup")
    app.config.setdefault("MAILER", Mailer(app.config["RESEND_API_KEY"].strip(), app.config["MAIL_FROM"].strip(),
                                           app.config["MAIL_REDIRECT_TO"].strip(),
                                           app.config["MAIL_REPLY_TO"] or app.config["SUPPORT_EMAIL"]))
    app.register_blueprint(auth.bp)
    app.register_blueprint(avatars.bp)
    app.register_blueprint(themes.bp)
    app.register_blueprint(site_settings.bp)
    app.register_blueprint(schools.bp)
    app.register_blueprint(tv.bp)
    app.register_blueprint(cleanup.bp)
    app.register_blueprint(set_houses.bp)
    app.register_blueprint(export.bp)
    app.register_blueprint(house_extra.bp)
    app.register_blueprint(quran_coins.bp)
    app.register_blueprint(memo_import.bp)
    app.register_blueprint(helpdesk.bp)
    app.register_blueprint(assistant.bp)
    if not app.config.get("TESTING"):
        helpdesk.start_summary_thread(app)
    pruned = {"at": 0}

    @app.errorhandler(HTTPException)
    def http_error(error):
        """JSON for the dashboard's API calls, a plain page otherwise, in the site's own words."""
        code = error.code or 500
        title, message = ERRORS.get(code, ERRORS[500 if code >= 500 else 400])
        own = error.description if error.description in auth.SHOWN_ERRORS else None
        if wants_json():
            response = jsonify(error=own or message)
            response.status_code = code
        else:
            response = app.make_response(auth.page("error.html", code, title=title, message=own or message))
        # Such as Allow on a 405.
        for name, value in error.get_headers():
            if name.lower() != "content-type":
                response.headers[name] = value
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        # Logged in full for Railway's logs; the browser only gets the generic message.
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        return http_error(InternalServerError())

    def wants_json():
        return request.path.startswith("/api/")

    @app.teardown_request
    def leave_school(error=None):
        # Outside a request nothing is left pointing at the last request's school.
        database.reset()

    @app.before_request
    def require_login():
        database.reset()
        if request.path == "/healthz":
            return None
        # No school yet: auth.load_session works out which one from the sign-in cookie or the link.
        database.set(schools.CONTROL)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Writes only from this site's own pages.
            origin = request.headers.get("Origin")
            own = {request.host_url.rstrip("/"), app.config["PUBLIC_URL"].rstrip("/")}
            if (origin and origin not in own) or request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
        auth.load_session()
        if time.time() - pruned["at"] > 3600:
            pruned["at"] = time.time()
            for each in database.databases():
                auth.prune(each, app.config["RETENTION_DAYS"])
                assistant.prune(each, app.config["ASSISTANT_LOG_DAYS"])
        public = request.path in PUBLIC or request.path.startswith(PUBLIC_PREFIXES)
        if not g.user and not public:
            if wants_json() or request.method != "GET":
                return jsonify(error="Your session has ended. Sign in again."), 401
            target = request.full_path.rstrip("?")
            # The site's own address opens the start page; any other page asks to sign in and comes back after.
            return redirect("/welcome" if target == "/" else "/login?next=" + quote(target, safe="/"))
        if g.user and g.user["must_change"] and not public and request.path not in ("/account", "/account/password", "/logout"):
            if wants_json():
                return jsonify(error="Choose a new password before continuing."), 403
            return redirect("/account")
        # The developer with no school open has their own pages and nothing else: every other page is a school's.
        if g.user and g.user["role"] == "developer" and not public and not auth.developer_home(request.path) \
                and request.path not in SHELL_FILES:
            if wants_json() or request.method != "GET":
                return jsonify(error="Open a school first."), 403
            return redirect("/schools")
        if wants_json() and request.method not in ("GET", "HEAD", "OPTIONS"):
            # JSON plus this custom header require a CORS preflight cross-origin.
            # No CORS access is granted by this server.
            if not request.is_json or request.headers.get("X-Dashboard-Request") != "1":
                abort(403)

    @app.after_request
    def private_responses(response):
        # The font files are public and never change; everything else is private.
        # The logo is public too, and email apps fetch it for every email they show.
        # A profile picture sets its own (private) caching, as its address changes with the picture.
        if not (request.path.startswith(("/avatar/", "/theme-photo/", "/theme.css")) and response.status_code == 200):
            response.headers["Cache-Control"] = "public, max-age=604800" if (request.path.startswith(("/fonts/", "/art/"))
            or request.path in ("/logo.png", "/favicon.png", "/favicon.ico")) and response.status_code == 200 else "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        dashboard = request.path in ("/", "/tv") or (request.path.startswith(("/tv/", "/present/"))
                                                        and response.mimetype == "text/html" and response.status_code == 200)
        if dashboard:
            nonce = secrets.token_urlsafe(16)
            if response.mimetype == "text/html" and not response.direct_passthrough:
                response.set_data(response.get_data().replace(b"<script", f'<script nonce="{nonce}"'.encode()))
        response.headers["Content-Security-Policy"] = DASHBOARD_CSP.format(nonce=nonce) if dashboard else \
            EMAIL_PREVIEW_CSP if request.path.startswith("/admin/email/preview/") else \
            ACCOUNT_CSP if request.path.startswith("/account") else \
            CODE_CSP if request.path in ("/login", "/login/code") else \
            LOGIN_CSP if request.path.startswith(("/admin/", "/schools")) or request.path == "/welcome" else PAGE_CSP
        if app.config["COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/healthz")
    def health():
        try:
            with database.first.transaction() as db:
                db.execute("SELECT COUNT(*) FROM classrooms").fetchone()
            return jsonify(status="ok")
        except DB_ERRORS:
            return jsonify(status="unavailable"), 503

    logo = {}

    @app.get("/logo.png")
    def logo_png():
        if "png" not in logo:
            logo["png"] = base64.b64decode((ROOT / "src" / "logo.b64").read_text().strip())
        response = Response(logo["png"], mimetype="image/png")
        return response

    # The browser tab icon: the logo on a white disc, so it reads as a circle on light and dark tabs alike.
    @app.get("/favicon.png")
    @app.get("/favicon.ico")
    def favicon():
        return Response((ROOT / "static" / "favicon.png").read_bytes(), mimetype="image/png")

    @app.get("/auth.css")
    def auth_css():
        # The shared shell and tokens (also inlined in the dashboard), then the admin page styles.
        css = (ROOT / "src" / "tokens.css").read_text(encoding="utf-8") + "\n" + \
            (ROOT / "static" / "auth.css").read_text(encoding="utf-8")
        return Response(css, mimetype="text/css")

    # The developer's bar across the top of a school's pages (schools.py); nobody else's pages link to it.
    @app.get("/developer.css")
    def developer_css():
        return Response((ROOT / "static" / "developer.css").read_text(encoding="utf-8"), mimetype="text/css")

    @app.get("/code.js")
    def code_js():
        return Response((ROOT / "static" / "code.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    # Where the wash page turn starts from (static/wash.js; the turn itself is CSS, in src/tokens.css).
    @app.get("/wash.js")
    def wash_js():
        return Response((ROOT / "static" / "wash.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/intro.js")
    def intro_js():
        return Response((ROOT / "static" / "intro.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/avatar.js")
    def avatar_js():
        return Response((ROOT / "static" / "avatar.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/theme.js")
    def theme_js():
        return Response((ROOT / "static" / "theme.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/assistant.js")
    def assistant_js():
        if not assistant.enabled(app):
            abort(404)
        return Response((ROOT / "static" / "assistant.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/assistant.css")
    def assistant_css():
        if not assistant.enabled(app):
            abort(404)
        return Response((ROOT / "static" / "assistant.css").read_text(encoding="utf-8"), mimetype="text/css")

    @app.get("/menu.js")
    def menu_js():
        return Response((ROOT / "static" / "menu.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    # The dock that replaces the side menu once signed in, and its tooltips (static/dock.js, static/warm-tooltip.js).
    @app.get("/dock.js")
    def dock_js():
        return Response((ROOT / "static" / "dock.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/warm-tooltip.js")
    def warm_tooltip_js():
        return Response((ROOT / "static" / "warm-tooltip.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/fonts/<name>")
    def font(name):
        if name not in FONTS:
            abort(404)
        return Response((ROOT / "static" / "fonts" / name).read_bytes(), mimetype="font/woff2")

    @app.get("/art/<name>")
    def art(name):
        if name not in ART:
            abort(404)
        return Response((ROOT / "static" / "art" / name).read_bytes(), mimetype="image/webp")

    # The start page: what Ilmi is, for anyone who arrives signed out, with the way in to sign in. It runs one
    # script of ours, /welcome.js, which reveals each part as it scrolls into view; without it everything shows.
    @app.get("/welcome")
    def welcome():
        return auth.page("welcome.html")

    @app.get("/welcome.css")
    def welcome_css():
        css = (ROOT / "src" / "tokens.css").read_text(encoding="utf-8") + "\n" + \
            (ROOT / "static" / "welcome.css").read_text(encoding="utf-8")
        return Response(css, mimetype="text/css")

    @app.get("/welcome.js")
    def welcome_js():
        return Response((ROOT / "static" / "welcome.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    @app.get("/")
    def dashboard():
        fresh = bool(request.cookies.get(auth.fresh_cookie_name()))
        # Anyone's first visit starts the tutorial (the "tour" in page.html); Account can start it again.
        with database.transaction() as tx:
            toured = tx.execute("SELECT tour_done FROM users WHERE id = ?", (g.user["id"],)).fetchone()["tour_done"]
        me = {"name": g.user["name"], "email": g.user["email"], "role": g.user["role"], "csrf": g.session["csrf"],
              "fresh": fresh, "stay": g.session["remember"], "initial": avatars.initial(g.user["name"]),
              "avatar": avatars.url(g.user["id"], g.user["avatar_at"]),
              "tour": not toured, "assistant": assistant.enabled(app), "site": site_settings.title()}
        # The developer's bar: the school they have opened and the view they chose (railway-storage.js draws it).
        bar = schools.developer_bar()
        if bar:
            me["dev"] = {"school": bar["school"], "view": bar["view"], "views": bar["views"]}
        html = (ROOT / "Master_Dashboard_standalone.html").read_text(encoding="utf-8")
        # The site title under Ilmi in the menu's top left corner (site_settings.py); with none, just Ilmi.
        if me["site"]:
            html = html.replace('<small class="org" hidden></small>', f'<small class="org">{escape(me["site"])}</small>', 1)
        html = html.replace("<script>", f"<script>window.RAILWAY=true;window.ME={script_json(me)};{TAB_GUARD}</script>\n<script>", 1)
        look = themes.current(g.user)
        if bar:
            html = html.replace("</style>", '</style>\n<link rel="stylesheet" href="/developer.css">', 1)
        # has-dock: the menu is the dock (static/dock.js), so the side menu never shows first.
        html = html.replace("<html lang=\"en\">", f"<html lang=\"en\" class=\"has-dock\"{themes.html_attrs(look)}>", 1)
        html = html.replace('<meta name="theme-color" content="#4b552f">', f'<meta name="theme-color" content="{look["menu"]}">', 1)
        if look["css"]:
            html = html.replace("</style>", f'</style>\n<link rel="stylesheet" href="{look["css"]}" id="themecss">', 1)
        html = html.replace("<header>", "<header inert>").replace("<main>", "<main inert>")
        # In the head, so the page knows where its wash starts before it is first drawn (static/wash.js).
        html = html.replace("</style></head>", '</style><script src="/wash.js"></script></head>', 1)
        extra = '<link rel="stylesheet" href="/assistant.css"><script src="/assistant.js"></script>' if assistant.enabled(app) else ""
        html = html.replace("</body>", '<script src="/railway-storage.js"></script><script src="/menu.js"></script>'
                            '<script src="/warm-tooltip.js"></script><script src="/dock.js"></script>'
                            + extra + '</body>')
        response = Response(html, mimetype="text/html")
        if fresh:
            response.delete_cookie(auth.fresh_cookie_name(), path="/", secure=app.config["COOKIE_SECURE"])
        return response

    @app.post("/api/tour")
    def tour_done():
        # Finished or skipped: it doesn't start on its own again.
        with database.transaction() as tx:
            tx.execute("UPDATE users SET tour_done = ? WHERE id = ?", (int(time.time()), g.user["id"]))
        return jsonify(ok=True)

    @app.get("/Master_Dashboard_standalone.html")
    def old_dashboard_link():
        return redirect("/")

    @app.get("/railway-storage.js")
    def storage_script():
        return Response((ROOT / "railway-storage.js").read_text(encoding="utf-8"), mimetype="text/javascript")

    def is_admin():
        return g.user["role"] == "admin"

    def sees_all():
        """The Office and HeadTeachers see and record on every classroom (and the developer, in any view)."""
        return auth.sees_all()

    def can_edit(db, class_id):
        return sees_all() or db.execute("SELECT 1 FROM classroom_members WHERE class_id = ? AND user_id = ?",
                                        (class_id, g.user["id"])).fetchone() is not None

    @app.get("/api/classes")
    def read_classes():
        with database.transaction() as db:
            if sees_all():
                rows = db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall()
            else:
                rows = db.execute("SELECT c.id, c.json, c.revision FROM classrooms c JOIN classroom_members m "
                                  "ON m.class_id = c.id WHERE m.user_id = ? ORDER BY c.id", (g.user["id"],)).fetchall()
            return jsonify({row["id"]: {"json": row["json"], "revision": row["revision"]} for row in rows})

    @app.get("/api/houses")
    def house_totals():
        """Whole-school house points. Only totals, so teachers can see them too."""
        with database.transaction() as db:
            rows = db.execute("SELECT json FROM classrooms").fetchall()
            # Points added by hand for a whole house count too.
            totals = house_extra.totals(db)
        for row in rows:
            for student in json.loads(row["json"]).get("S", []):
                if student.get("house") in totals:
                    totals[student["house"]] += sum(len(student.get(k, [])) * v for k, v in TOKEN_POINTS.items())
        return jsonify(totals)

    @app.get("/api/last-sid")
    def last_student_number():
        """The highest SE Student ID in any classroom, so a new student gets the next one. Teachers only load
        their own classrooms, so the browser can't work this out by itself."""
        last = 0
        with database.transaction() as db:
            rows = db.execute("SELECT json FROM classrooms").fetchall()
        for row in rows:
            state = json.loads(row["json"])
            # Ex students keep their Student IDs, so a new student never gets one of theirs.
            for student in state.get("S", []) + state.get("X", []):
                match = STUDENT_NUMBER.fullmatch(str(student.get("sid", "")).strip())
                if match:
                    last = max(last, int(match[1]))
        return jsonify(last=last)

    @app.put("/api/classes/<class_id>")
    def write_class(class_id):
        """Revision 0 creates a classroom; otherwise it must match the stored revision."""
        document = request.get_json()
        if not CLASS_ID.fullmatch(class_id) or not isinstance(document, dict) or not isinstance(document.get("json"), str) \
                or type(document.get("revision")) is not int or document["revision"] < 0:
            abort(400)
        try:
            state = json.loads(document["json"])
            if not valid_state(state):
                abort(400)
            encoded = encode_state(state)
        except (TypeError, ValueError):
            abort(400)
        with database.transaction() as db:
            if not can_edit(db, class_id) or (document["revision"] == 0 and not is_admin()):
                return jsonify(error="Your account can't change this classroom."), 403
            if document["revision"] == 0:
                exists = db.execute("SELECT 1 FROM classrooms WHERE id = ?", (class_id,)).fetchone()
                if not exists and db.execute("SELECT COUNT(*) AS n FROM classrooms").fetchone()["n"] >= MAX_CLASSES:
                    return jsonify(error=f"The dashboard holds at most {MAX_CLASSES} classrooms."), 400
                cursor = db.execute("INSERT INTO classrooms VALUES (?, ?, 1) ON CONFLICT (id) DO NOTHING",
                                    (class_id, encoded))
                if cursor.rowcount:
                    record_history(db, class_id, 1, encoded, g.user["id"])
                    return jsonify(revision=1)
            else:
                # Only admins add or remove students; a teacher's save must keep the class's students.
                if not is_admin():
                    current = db.execute("SELECT json FROM classrooms WHERE id = ? AND revision = ?",
                                         (class_id, document["revision"])).fetchone()
                    if current and any(len(json.loads(current["json"]).get(k, [])) != len(state.get(k, [])) for k in ("S", "X")):
                        return jsonify(error="Only the Office can add or remove students."), 403
                    # House points are given by admins only (on the Record page and Counted coins), not by HeadTeachers or teachers.
                    if current and coins(state) - coins(json.loads(current["json"])):
                        return jsonify(error="Only the Office can give house points."), 403
                saved = update_classroom(db, class_id, state, document["revision"], g.user["id"])
                if saved:
                    return jsonify(revision=saved)
            current = db.execute("SELECT json, revision FROM classrooms WHERE id = ?", (class_id,)).fetchone()
            if current is None:
                abort(404)
            # A lost response may cause an identical write to be retried.
            if json.loads(current["json"]) == state:
                return jsonify(revision=current["revision"])
            return jsonify(error="This class changed in another browser. Reload before editing again."), 409

    @app.delete("/api/classes/<class_id>")
    def delete_class(class_id):
        """Deletes a classroom if it is still at the revision the browser last saw."""
        document = request.get_json()
        if not CLASS_ID.fullmatch(class_id) or not isinstance(document, dict) or type(document.get("revision")) is not int:
            abort(400)
        if not is_admin():
            return jsonify(error="Only the Office can delete a classroom."), 403
        with database.transaction() as db:
            cursor = db.execute("DELETE FROM classrooms WHERE id = ? AND revision = ?", (class_id, document["revision"]))
            if cursor.rowcount:
                db.execute("DELETE FROM classroom_members WHERE class_id = ?", (class_id,))
                # A history entry without a record marks the deletion; the entries before it can restore it.
                db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, NULL, ?, ?)",
                           (class_id, document["revision"], g.user["id"], int(time.time())))
                auth.audit(db, "class.deleted", class_id, {"revision": document["revision"]})
                return jsonify(deleted=True)
            # Deleting a classroom that is already gone succeeds, so a retried request is harmless.
            if db.execute("SELECT 1 FROM classrooms WHERE id = ?", (class_id,)).fetchone() is None:
                return jsonify(deleted=True)
            return jsonify(error="This class changed in another browser. Reload before deleting it."), 409

    @app.get("/admin/history")
    @auth.admin_required
    def history():
        with database.transaction() as db:
            current = {row["id"] for row in db.execute("SELECT id FROM classrooms").fetchall()}
            rows = db.execute("SELECT h.class_id, h.json, h.saved_at, u.name FROM classroom_history h "
                              "LEFT JOIN users u ON u.id = h.saved_by ORDER BY h.id DESC").fetchall()
        classes = {}
        for row in rows:
            entry = classes.setdefault(row["class_id"], {"id": row["class_id"], "name": None, "count": 0,
                                                         "last": row["saved_at"], "by": row["name"],
                                                         "deleted": row["class_id"] not in current})
            entry["count"] += 1
            if entry["name"] is None and row["json"]:
                entry["name"] = json.loads(row["json"]).get("name")
        return auth.page("admin_history.html", classes=sorted(classes.values(), key=lambda c: -c["last"]))

    def class_history(class_id):
        with database.transaction() as db:
            rows = db.execute("SELECT h.id, h.revision, h.json, h.saved_at, u.name FROM classroom_history h "
                              "LEFT JOIN users u ON u.id = h.saved_by WHERE h.class_id = ? ORDER BY h.id DESC",
                              (class_id,)).fetchall()
            exists = db.execute("SELECT 1 FROM classrooms WHERE id = ?", (class_id,)).fetchone() is not None
        entries = []
        for row in rows:
            state = json.loads(row["json"]) if row["json"] else None
            entries.append({"id": row["id"], "saved_at": row["saved_at"], "by": row["name"],
                            "name": state.get("name") if state else None,
                            "students": len(state.get("S", [])) if state else None})
        return entries, exists

    @app.get("/admin/history/<class_id>")
    @auth.admin_required
    def history_of(class_id):
        entries, exists = class_history(class_id)
        if not entries:
            abort(404)
        return auth.page("admin_class_history.html", class_id=class_id, entries=entries, exists=exists)

    @app.post("/admin/history/<class_id>/restore")
    @auth.admin_required
    def restore(class_id):
        """Saves an earlier version as the classroom's newest one, or brings back a deleted classroom."""
        auth.check_form_token()
        with database.transaction() as db:
            row = db.execute("SELECT json FROM classroom_history WHERE id = ? AND class_id = ? AND json IS NOT NULL",
                             (request.form.get("entry", type=int), class_id)).fetchone()
            if not row:
                abort(404)
            current = db.execute("SELECT revision FROM classrooms WHERE id = ?", (class_id,)).fetchone()
            if current:
                revision = current["revision"] + 1
                db.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (row["json"], revision, class_id))
            else:
                revision = 1
                db.execute("INSERT INTO classrooms VALUES (?, ?, 1)", (class_id, row["json"]))
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision, row["json"], g.user["id"], int(time.time())))
            auth.audit(db, "class.restored", class_id, {"entry": request.form.get("entry", type=int)})
        entries, exists = class_history(class_id)
        return auth.page("admin_class_history.html", class_id=class_id, entries=entries, exists=exists,
                         notice="Restored. Open dashboards will show this version within a few seconds.")

    return app
