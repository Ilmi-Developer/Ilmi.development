"""Staff accounts: sign-in, sessions, invites, password resets and user management.

Each person has their own account at one school. The Office (role "admin") sees
and manages every classroom and every account at its school; teachers see only
the classrooms the Office has assigned to them. Every school has its own
database (schools.py), and one sign-in page serves them all: the email says
which school. The developer's account belongs to no school and can open any.

Sessions are random tokens in a cookie; only their SHA-256 hash is stored. Invite
and reset links work the same way. Passwords are hashed with argon2id.

Admins see every child's record, so their sign-in has a second step when email is
set up: after the password, a 6-digit code is emailed to them, and the session
starts once they type it (LOGIN_CODE_TTL, LOGIN_CODE_FAILS).
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, jsonify

import emails
import schools

MINUTE, HOUR, DAY = 60, 3600, 86400
# A sign-in ends 20 minutes after the dashboard was last open (it checks in while open, even in a background
# tab), so closing the app signs you out: the cookie also ends with the browser. "Stay signed in" keeps the cookie
# for SESSION_STAY instead, ending sooner if the dashboard isn't opened for SESSION_STAY_IDLE.
SESSION_IDLE = 20 * MINUTE
SESSION_SHORT = 12 * HOUR
SESSION_STAY = 30 * DAY
SESSION_STAY_IDLE = 14 * DAY
INVITE_TTL = 72 * HOUR
RESET_TTL = HOUR
LOGIN_CODE_TTL = 10 * MINUTE
# After an admin types their emailed code with "Stay signed in" ticked, that browser is trusted for this long: their
# password alone signs them in there, even after signing out, so the code is asked for about once a month.
TRUSTED_DEVICE_TTL = 30 * DAY
# Wrong codes allowed for one emailed code; after that the admin signs in again for a new one. Across codes, wrong
# ones are limited per email and per IP address within LIMIT_WINDOW, so new codes can't be asked for to keep guessing.
LOGIN_CODE_FAILS = 5
EMAIL_CODE_FAILS, IP_CODE_FAILS = 10, 30
LIMIT_WINDOW = 15 * MINUTE
# Failed sign-ins allowed per email and per IP address within LIMIT_WINDOW.
EMAIL_FAILS, IP_FAILS = 5, 20
# Reset emails allowed per email and per IP address within LIMIT_WINDOW.
EMAIL_RESETS, IP_RESETS = 3, 10
PASSWORD_MIN = 12
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# The roles an account at a school can be given. "admin" is the Office: the person in charge of the school's settings,
# accounts and records. "developer" is not among them, so nobody can be given it: it is what the one account in the
# control database is called (schools.py).
ROLES = ("admin", "head", "teacher")
# A HeadTeacher ("head") sees every classroom and records on any of them (Record, House points, Rankings, Presenter,
# FAQs), but gives no house points and has none of the Administration pages: no accounts, students, imports or classroom changes.
ROLE_NAMES = {"admin": "Office", "head": "HeadTeacher", "teacher": "Teacher", "developer": "Developer"}
SEES_ALL = ("admin", "head")
COMMON_PASSWORDS = {
    "123456789012", "1234567890123", "qwertyuiopas", "passwordpassword", "password1234", "password12345",
    "password123!", "iloveyou1234", "adminadminadmin", "welcome12345", "letmein12345",
    "replace-with-a-long-unique-password",
    "aaaaaaaaaaaa", "abcdefghijkl", "abc123abc123", "qwerty123456", "000000000000", "111111111111",
}

hasher = PasswordHasher()
# Verified against when an email is unknown, so that both cases take as long.
DUMMY_HASH = hasher.hash(secrets.token_hex(16))

bp = Blueprint("auth", __name__)
LONDON = ZoneInfo("Europe/London")
ACTIONS = {
    "login": "Signed in", "login.failed": "Wrong password", "login.blocked": "Sign-in blocked (too many attempts)",
    "logout": "Signed out", "logout.others": "Signed out other devices", "password.changed": "Changed password",
    "password.reset_requested": "Asked for a reset link", "password.reset": "Reset password", "password.reset_on_railway": "Password reset from Railway",
    "invite.accepted": "Accepted invite", "user.invited": "Invited a user", "user.updated": "Changed a user",
    "user.reset_link": "Made a reset link", "user.invite_link": "Made a new invite", "user.disabled": "Turned off a user",
    "user.enabled": "Turned on a user", "class.saved": "Saved classroom", "class.deleted": "Deleted classroom",
    "class.restored": "Restored classroom", "tv.created": "Made a TV screen link",
    "tv.revoked": "Turned off a TV screen link", "tv.settings": "Changed a TV screen's settings",
    "email.test": "Sent a test email", "export.downloaded": "Downloaded the school spreadsheet",
    "help.sent": "Sent a Help or Requests message", "help.replied": "Replied on FAQs",
    "help.deleted": "Deleted a FAQs message", "avatar.changed": "Changed their profile picture",
    "avatar.removed": "Removed their profile picture", "board.created": "Made a memo board link",
    "board.revoked": "Turned off a memo board link", "login.code_sent": "Was emailed a sign-in code",
    "login.code_failed": "Typed a wrong sign-in code",
    "assistant.request": "Submitted a request through the assistant",
    "assistant.request_status": "Changed an assistant request's status",
    "share.code": "Shared a Presenter link", "share.stopped": "Stopped sharing a Presenter link",
    "site.title": "Changed the site title",
    "school.created": "Added a school", "school.renamed": "Renamed a school", "school.opened": "Opened a school",
    "school.office": "Set a school's Office account",
}


@bp.app_template_global("role_name")
def role_name(role):
    return ROLE_NAMES.get(role, "Teacher")


@bp.app_template_filter("when")
def when(timestamp):
    return datetime.fromtimestamp(timestamp, LONDON).strftime("%-d %b %Y, %H:%M")


@bp.app_template_filter("action")
def action_label(action):
    return ACTIONS.get(action, action)


@bp.app_template_filter("fromjson")
def fromjson(value):
    return json.loads(value)


def now():
    return int(time.time())


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def norm_email(value):
    return (value or "").strip().lower()


def password_problem(password, email=""):
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > 256:
        return "Use at most 256 characters."
    if password.lower() in COMMON_PASSWORDS or password.lower() == email or len(set(password)) < 4 \
            or _is_site_name(password):
        return "That password is too easy to guess. Try a few unrelated words together."
    return None


def _is_site_name(password):
    """The school's own name (the site title, kept in the database), give or take numbers, spaces and symbols, is
    an easy guess too. The name isn't written into the code, so it is read from the site title."""
    import site_settings  # imports this module, so not at the top
    try:
        name = re.sub(r"[^a-z]", "", site_settings.title().lower())
    except Exception:  # no database to ask: the other checks still apply
        return False
    return len(name) >= 4 and re.sub(r"[^a-z]", "", password.lower()) in (name, name + "dashboard")


def safe_next(target):
    """Only same-site paths, so a sign-in link can't send someone to another website. Browsers drop tabs and line
    breaks from an address and read a backslash as a slash ("/\t/evil.example" goes to evil.example), so a path with
    any of those, a space or another control character is refused too."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target \
            or any(ord(ch) <= 32 or ord(ch) == 127 for ch in target):
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target


def db():
    """The schools (schools.Schools). Its transaction() opens the database of the school this request is working in."""
    return current_app.config["DB"]


def developer_email():
    return norm_email(current_app.config.get("DEVELOPER_EMAIL", ""))


def own():
    """The database the signed-in person's own account is in, and their row there. They differ from db() and g.user
    only for the developer inside a school, whose account, picture and themes stay in the control database."""
    developer = g.get("developer")
    if developer:
        return db().control, developer
    return db().current(), g.user


def audit(tx, action, class_id=None, detail=None, user_id=None):
    if user_id is None and g.get("user"):
        user_id = g.user["id"]
    tx.execute("INSERT INTO audit_log (at, user_id, action, class_id, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
               (now(), user_id, action, class_id, json.dumps(detail) if detail is not None else None,
                request.remote_addr))


def cookie_name():
    return "__Host-safar" if current_app.config["COOKIE_SECURE"] else "safar"


def fresh_cookie_name():
    """Set for a few minutes after signing in, so the dashboard knows this tab has just signed in (greeting, and
    the tab is marked as signed in; a tab opened later without that mark goes back to the sign-in page)."""
    return "__Host-safar-in" if current_app.config["COOKIE_SECURE"] else "safar-in"


def code_cookie_name():
    """Holds the browser's half of an admin's emailed sign-in code (see send_login_code)."""
    return "__Host-safar-code" if current_app.config["COOKIE_SECURE"] else "safar-code"


def pre_cookie_name():
    return "__Host-safar-form" if current_app.config["COOKIE_SECURE"] else "safar-form"


def device_cookie_name():
    """Marks a browser where an admin finished the emailed code with "Stay signed in" (see trusted_device)."""
    return "__Host-safar-device" if current_app.config["COOKIE_SECURE"] else "safar-device"


def trusted_device(tx, user_id):
    """True when this browser holds a trusted-device cookie for this user that hasn't expired."""
    token = request.cookies.get(device_cookie_name(), "")
    if not token or len(token) > 100:
        return False
    return bool(tx.execute("SELECT 1 FROM trusted_devices WHERE token_hash = ? AND user_id = ? AND expires_at > ?",
                           (token_hash(token), user_id, now())).fetchone())


def trust_device(tx, response, user_id):
    token, t = secrets.token_urlsafe(32), now()
    old = request.cookies.get(device_cookie_name(), "")
    if old and len(old) <= 100:
        tx.execute("DELETE FROM trusted_devices WHERE token_hash = ?", (token_hash(old),))
    tx.execute("INSERT INTO trusted_devices (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (token_hash(token), user_id, t, t + TRUSTED_DEVICE_TTL))
    set_cookie(response, device_cookie_name(), token, TRUSTED_DEVICE_TTL)


def forget_devices(tx, user_id, keep_this_one=False):
    """Stops a user's browsers skipping the emailed code (password changed or reset, account turned off, or signed
    out everywhere else). keep_this_one keeps the browser making the request."""
    mine = request.cookies.get(device_cookie_name(), "") if keep_this_one else ""
    tx.execute("DELETE FROM trusted_devices WHERE user_id = ? AND token_hash <> ?",
               (user_id, token_hash(mine) if mine else ""))


def set_cookie(response, name, value, max_age=None):
    response.set_cookie(name, value, max_age=max_age, path="/", secure=current_app.config["COOKIE_SECURE"],
                        httponly=True, samesite="Lax")


# The admin pages' own addresses (/admin/tv/3/off) carry no token, so they are left as they are.
LINK_TOKEN = re.compile(r"((?<!/admin)/(?:invite|reset|tv|board)/)[^/?#\s\"]+")


def hide_link_tokens(text):
    """Takes the secret token out of invite, reset, TV screen and memo board links, so they can be written to a log."""
    return LINK_TOKEN.sub(r"\1[hidden]", text)


def public_url(path):
    base = current_app.config["PUBLIC_URL"] or request.host_url
    return base.rstrip("/") + path


# --- Sessions -----------------------------------------------------------------

# Links that carry their own token, and with it their school: they work signed out, or signed in anywhere.
LINK_PATHS = ("/invite/", "/reset/", "/tv/", "/present/")
# The developer's own pages. Everywhere else they are inside the school they have opened.
DEVELOPER_HOME = ("/account", "/schools", "/logout", "/theme.css", "/theme-photo/")


NOT_A_LINK = object()


def link_school(path):
    """The school a token link belongs to (the control database for the developer's own reset link); None for a link
    that names no school we have; NOT_A_LINK for any other path."""
    for prefix in LINK_PATHS:
        if path.startswith(prefix):
            school_id = schools.token_school(path[len(prefix):].split("/", 1)[0])[0]
            return school_id if school_id is not None and db().exists(school_id) else None
    return NOT_A_LINK


def developer_home(path):
    return path.startswith(DEVELOPER_HOME)


def load_session():
    """Works out which school this request belongs to, and sets g.user and g.session from the session cookie, if it
    is valid. g.developer is set as well when it is the developer who is signed in: then g.user is either the
    developer themselves (role "developer", on their own pages) or their stand-in in the school they have opened,
    with the role they chose to view it as."""
    g.user = g.session = g.developer = None
    router = db()
    # Signed-out pages belong to no school; the control database stands in (it has no title, records or staff).
    router.set(schools.CONTROL)
    cookie = request.cookies.get(cookie_name()) or ""
    home, token = schools.token_school(cookie) if cookie and len(cookie) <= 100 else (None, "")
    if home is not None and not router.exists(home):
        home = None
    linked = link_school(request.path)
    if linked is not NOT_A_LINK:
        if linked is None:
            return  # no such school: the link is checked against the control database, which has none
        router.set(linked)
        if home != linked:
            return  # signed in at another school, or not at all: the link itself is what counts
    elif home is None or not token:
        return
    else:
        router.set(home)
    t = now()
    with router.transaction() as tx:
        row = tx.execute(
            "SELECT s.token_hash, s.csrf, s.last_seen_at, s.expires_at, s.remember, s.school_id, s.view_as, u.id, u.email, "
            "u.name, u.role, u.must_change, u.disabled, u.developer, u.avatar_at, u.theme FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (token_hash(token),)).fetchone()
        if not row:
            return
        idle, longest = (SESSION_STAY_IDLE, SESSION_STAY) if row["remember"] else (SESSION_IDLE, SESSION_SHORT)
        # Nobody signs in as a stand-in, and only DEVELOPER_EMAIL's account is the developer.
        if (row["disabled"] or row["developer"] or row["expires_at"] <= t or row["last_seen_at"] + idle <= t
                or row["expires_at"] > t + longest
                or (home == schools.CONTROL and (not developer_email() or row["email"] != developer_email()))):
            tx.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            return
        # The dashboard polls every 10 seconds; record activity at most once a minute.
        if t - row["last_seen_at"] >= MINUTE:
            tx.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (t, row["token_hash"]))
    g.session = {"token_hash": row["token_hash"], "csrf": row["csrf"], "remember": bool(row["remember"])}
    g.user = {k: row[k] for k in ("id", "email", "name", "role", "must_change", "avatar_at", "theme")}
    if home != schools.CONTROL:
        return
    opened = row["school_id"] if row["school_id"] and router.exists(row["school_id"]) else None
    g.developer = dict(g.user, school_id=opened, view=row["view_as"] if row["view_as"] in schools.VIEWS else "admin")
    g.user["role"] = "developer"
    if not opened or g.user["must_change"] or developer_home(request.path):
        return
    # Inside the school they opened: its database, and their stand-in there with the role they are viewing it as.
    router.set(opened)
    with router.transaction() as tx:
        stand_in = schools.stand_in(tx, g.developer)
    g.user = {"id": stand_in, "email": g.developer["email"], "name": g.developer["name"], "role": g.developer["view"],
              "must_change": 0, "avatar_at": g.developer["avatar_at"], "theme": g.developer["theme"],
              # As a teacher they have every classroom, where a teacher has the ones the Office ticked.
              "every_class": True}


def sees_all(user=None):
    """The Office and HeadTeachers see and record on every classroom; so does the developer in any view."""
    user = user or g.user
    return user["role"] in SEES_ALL or bool(user.get("every_class"))


def start_session(tx, user_id, remember=False):
    """Starts a session in the current school's database. The cookie's value starts with the school's number, which
    is how the next request finds this database; only the random part's hash is stored."""
    token = secrets.token_urlsafe(32)
    t = now()
    tx.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen_at, expires_at, ip, user_agent, "
               "remember) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (token_hash(token), user_id, secrets.token_urlsafe(32), t, t,
                t + (SESSION_STAY if remember else SESSION_SHORT), request.remote_addr,
                (request.user_agent.string or "")[:200], int(remember)))
    tx.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (t, user_id))
    return f"{db().current_id()}.{token}", (SESSION_STAY if remember else None)


def signed_in_redirect(token, max_age, target):
    response = redirect(safe_next(target))
    set_cookie(response, cookie_name(), token, max_age)
    set_cookie(response, fresh_cookie_name(), "1", 5 * MINUTE)
    response.delete_cookie(pre_cookie_name(), path="/", secure=current_app.config["COOKIE_SECURE"])
    return response


# --- CSRF ---------------------------------------------------------------------

def form_token():
    """CSRF token for the current page's forms."""
    if g.get("session"):
        return g.session["csrf"]
    token = request.cookies.get(pre_cookie_name())
    if not token or len(token) > 100:
        token = secrets.token_urlsafe(32)
    g.new_pre_token = token
    return token


FORM_EXPIRED = "This form has expired. Go back, reload the page and try again."
# Error messages written for people, shown on the error page as they are (see server.ERRORS).
SHOWN_ERRORS = {FORM_EXPIRED}


def check_form_token():
    sent = request.form.get("csrf", "")
    expected = g.session["csrf"] if g.get("session") else request.cookies.get(pre_cookie_name(), "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        abort(400, FORM_EXPIRED)


@bp.after_app_request
def keep_pre_token(response):
    if g.get("new_pre_token") and request.cookies.get(pre_cookie_name()) != g.new_pre_token:
        set_cookie(response, pre_cookie_name(), g.new_pre_token, max_age=DAY)
    return response


def page(template, status=200, **context):
    return render_template(template, user=g.get("user"), csrf=form_token(),
                           mail_enabled=current_app.config["MAILER"].enabled,
                           support_email=current_app.config.get("SUPPORT_EMAIL", ""), **context), status


# --- Access control -----------------------------------------------------------

def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def staff_required(view):
    """Admins and HeadTeachers."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or g.user["role"] not in SEES_ALL:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


# --- Rate limiting --------------------------------------------------------------

@contextmanager
def attempts(tx):
    """Sign-in attempts are counted in the control database, so the limits hold across every school. `tx` is the
    transaction already open, used when that is the control database's own."""
    router = db()
    if router.current_id() == schools.CONTROL:
        yield tx
    else:
        with router.control.transaction() as counted:
            yield counted


def limited(tx, kind, email, email_limit, ip_limit, failures_only=True):
    since = now() - LIMIT_WINDOW
    ok = " AND ok = 0" if failures_only else ""
    with attempts(tx) as counted:
        by_email = counted.execute(f"SELECT COUNT(*) AS n FROM attempts WHERE kind = ? AND email = ? AND at > ?{ok}",
                                   (kind, email, since)).fetchone()["n"]
        by_ip = counted.execute(f"SELECT COUNT(*) AS n FROM attempts WHERE kind = ? AND ip = ? AND at > ?{ok}",
                                (kind, request.remote_addr or "", since)).fetchone()["n"]
    return by_email >= email_limit or by_ip >= ip_limit


def record_attempt(tx, kind, email, ok):
    with attempts(tx) as counted:
        counted.execute("INSERT INTO attempts (kind, email, ip, at, ok) VALUES (?, ?, ?, ?, ?)",
                        (kind, email, request.remote_addr or "", now(), 1 if ok else 0))


# --- Tokens for invite and reset links -------------------------------------------

def issue_link(tx, user_id, purpose):
    """Creates a one-time link and cancels earlier unused ones for the same purpose."""
    t = now()
    tx.execute("UPDATE user_tokens SET used_at = ? WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
               (t, user_id, purpose))
    token = schools.new_token()  # starts with the school's number, so the link finds its school
    tx.execute("INSERT INTO user_tokens VALUES (?, ?, ?, ?, ?, NULL)",
               (token_hash(token), user_id, purpose, t, t + (INVITE_TTL if purpose == "invite" else RESET_TTL)))
    return public_url(f"/{purpose}/{token}")


def send_link(user, purpose, link, by=None, rooms=(), background=False):
    """Emails an invite or reset link. `by` is the admin who sent it, if one did."""
    if purpose == "invite":
        return emails.send("invite", user["email"], background=background, name=user["name"], email=user["email"],
                           role=user.get("role", "teacher"), rooms=list(rooms), by=by, link=link)
    return emails.send("reset", user["email"], background=background, name=user["name"], email=user["email"],
                       by=by, link=link)


def password_changed_notice(user, how):
    """Tells someone their password changed, so they find out if it wasn't them. Sent in the background."""
    emails.send("password_changed", user["email"], background=True, name=user["name"], email=user["email"],
                how=how, at=now())


def find_link(tx, token, purpose):
    row = tx.execute("SELECT t.token_hash, t.expires_at, t.used_at, u.id, u.email, u.name, u.disabled "
                     "FROM user_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ? AND t.purpose = ? "
                     "AND u.developer = 0", (token_hash(token), purpose)).fetchone()
    if not row or row["used_at"] or row["expires_at"] <= now() or row["disabled"]:
        return None
    return row


# --- Sign in and out ----------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    target = safe_next(request.values.get("next"))
    if g.user and request.method in ("GET", "HEAD"):
        return redirect(target)
    if request.method in ("GET", "HEAD"):
        notice = "You've signed out." if request.args.get("out") else None
        # The opening titles play when someone arrives fresh, not after signing out.
        return page("login.html", next=target, notice=notice, intro=not notice)
    check_form_token()
    email = norm_email(request.form.get("email"))[:254]
    password = request.form.get("password", "")[:256]
    remember = request.form.get("remember") == "1"
    # The email says which school's database holds the account; one with no account is checked against none.
    home = db().home_of(email)
    db().set(schools.CONTROL if home is None else home)
    with db().transaction() as tx:
        if limited(tx, "login", email, EMAIL_FAILS, IP_FAILS):
            audit(tx, "login.blocked", detail={"email": email})
            error = "Too many attempts. Wait 15 minutes, then try again."
            return page("login.html", 429, next=target, email=email, remember=remember, error=error)
        user = tx.execute("SELECT id, email, name, role, password_hash, disabled FROM users WHERE email = ? "
                          "AND developer = 0", (email,)).fetchone()
        if user and home == schools.CONTROL and email != developer_email():
            user = None  # only DEVELOPER_EMAIL's account is the developer
        try:
            ok = hasher.verify(user["password_hash"] if user and user["password_hash"] else DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            ok = False
        ok = ok and bool(user) and bool(user["password_hash"]) and not user["disabled"]
        record_attempt(tx, "login", email, ok)
        if not ok:
            audit(tx, "login.failed", detail={"email": email}, user_id=user["id"] if user else None)
    if not ok:
        return page("login.html", 401, next=target, email=email, remember=remember,
                    error="That email and password don't match.")
    if hasher.check_needs_rehash(user["password_hash"]):
        with db().transaction() as tx:
            tx.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hasher.hash(password), user["id"]))
    trusted = False
    if user["role"] == "admin":
        with db().transaction() as tx:
            trusted = trusted_device(tx, user["id"])
        if trusted:
            current_app.logger.info("Admin %s signed in without an emailed code: trusted browser.", user["id"])
        elif not current_app.config.get("LOGIN_CODES"):
            current_app.logger.info("Admin %s signed in without an emailed code: LOGIN_CODES is off.", user["id"])
        elif current_app.config["MAILER"].enabled:
            return send_login_code(dict(user), target, remember)
        else:
            current_app.logger.warning("Admin %s signed in without an emailed code: email isn't set up "
                                       "(RESEND_API_KEY and MAIL_FROM).", user["id"])
    with db().transaction() as tx:
        token, max_age = start_session(tx, user["id"], remember)
        audit(tx, "login", user_id=user["id"], detail={"trusted": True} if trusted else None)
    return signed_in_redirect(token, max_age, target)


def code_hash(code):
    return token_hash("login-code:" + code)


def send_login_code(user, target, remember=False):
    """Emails an admin a 6-digit code and asks for it. The browser keeps a random token that the code belongs to, so
    the code only works in the browser that typed the password."""
    browser, code, t = schools.new_token(), f"{secrets.randbelow(10 ** 6):06d}", now()
    with db().transaction() as tx:
        if limited(tx, "code", user["email"], EMAIL_CODE_FAILS, IP_CODE_FAILS):
            audit(tx, "login.blocked", detail={"email": user["email"]}, user_id=user["id"])
            return page("login.html", 429, next=target, email=user["email"], remember=remember,
                        error="Too many wrong sign-in codes. Wait 15 minutes, then try again.")
        # Only the latest code works.
        tx.execute("DELETE FROM login_codes WHERE user_id = ? OR expires_at < ?", (user["id"], t))
        tx.execute("INSERT INTO login_codes (token_hash, user_id, code_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                   (token_hash(browser), user["id"], code_hash(code), t, t + LOGIN_CODE_TTL))
        audit(tx, "login.code_sent", user_id=user["id"])
    if not emails.send("login_code", user["email"], name=user["name"], email=user["email"], code=code, at=t):
        with db().transaction() as tx:
            tx.execute("DELETE FROM login_codes WHERE token_hash = ?", (token_hash(browser),))
        return page("login.html", 502, next=target, email=user["email"], remember=remember,
                    error="Your sign-in code couldn't be emailed. Try again in a minute, or ask another admin.")
    response = current_app.make_response(page("login_code.html", next=target, email=user["email"],
                                               remember=remember, minutes=LOGIN_CODE_TTL // MINUTE))
    set_cookie(response, code_cookie_name(), browser, LOGIN_CODE_TTL)
    return response


@bp.post("/login/code")
def login_code():
    check_form_token()
    target = safe_next(request.form.get("next"))
    remember = request.form.get("remember") == "1"
    browser = request.cookies.get(code_cookie_name(), "")
    typed = "".join(ch for ch in request.form.get("code", "") if ch.isdigit())[:6]
    expired = "That code has expired or was replaced by a newer one. Sign in again for a new code."
    home = schools.token_school(browser)[0]
    if not browser or len(browser) > 100 or home is None or not db().exists(home):
        return page("login.html", 400, next=target, error=expired)
    db().set(home)  # the code's cookie starts with its school's number, like a link
    t = now()
    with db().transaction() as tx:
        row = tx.execute("SELECT c.token_hash, c.code_hash, c.expires_at, c.fails, u.id, u.email, u.disabled "
                         "FROM login_codes c JOIN users u ON u.id = c.user_id WHERE c.token_hash = ?",
                         (token_hash(browser),)).fetchone()
        if not row or row["expires_at"] <= t or row["fails"] >= LOGIN_CODE_FAILS or row["disabled"]:
            return page("login.html", 400, next=target, error=expired)
        if len(typed) != 6 or not hmac.compare_digest(code_hash(typed), row["code_hash"]):
            tx.execute("UPDATE login_codes SET fails = fails + 1 WHERE token_hash = ?", (row["token_hash"],))
            record_attempt(tx, "code", row["email"], False)
            audit(tx, "login.code_failed", user_id=row["id"])
            left = LOGIN_CODE_FAILS - row["fails"] - 1
            if not left:
                error = "That code isn't right, and it has now stopped working. Sign in again for a new code."
                return page("login.html", 401, next=target, email=row["email"], error=error)
            return page("login_code.html", 401, next=target, email=row["email"], remember=remember,
                        minutes=LOGIN_CODE_TTL // MINUTE, error="That code isn't right. Check the latest email and try again.")
        tx.execute("DELETE FROM login_codes WHERE token_hash = ?", (row["token_hash"],))
        token, max_age = start_session(tx, row["id"], remember)
        audit(tx, "login", user_id=row["id"])
        response = signed_in_redirect(token, max_age, target)
        # "Stay signed in" also means no emailed code in this browser for TRUSTED_DEVICE_TTL.
        if remember:
            trust_device(tx, response, row["id"])
    response.delete_cookie(code_cookie_name(), path="/", secure=current_app.config["COOKIE_SECURE"])
    return response


@bp.post("/logout")
def logout():
    if g.session:
        check_form_token()
        with db().transaction() as tx:
            tx.execute("DELETE FROM sessions WHERE token_hash = ?", (g.session["token_hash"],))
            audit(tx, "logout")
    response = redirect("/login?out=1")
    response.delete_cookie(cookie_name(), path="/", secure=current_app.config["COOKIE_SECURE"])
    response.delete_cookie(fresh_cookie_name(), path="/", secure=current_app.config["COOKIE_SECURE"])
    return response


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return page("forgot.html")
    check_form_token()
    email = norm_email(request.form.get("email"))[:254]
    home = db().home_of(email)
    db().set(schools.CONTROL if home is None else home)
    with db().transaction() as tx:
        blocked = limited(tx, "reset", email, EMAIL_RESETS, IP_RESETS, failures_only=False)
        record_attempt(tx, "reset", email, False)
        user = tx.execute("SELECT id, email, name, role, password_hash, disabled FROM users WHERE email = ? "
                          "AND developer = 0", (email,)).fetchone()
        if user and home == schools.CONTROL and email != developer_email():
            user = None
        link = None
        if not blocked and user and not user["disabled"] and current_app.config["MAILER"].enabled:
            # Someone who never accepted their invite gets a fresh invite instead.
            purpose = "reset" if user["password_hash"] else "invite"
            link = issue_link(tx, user["id"], purpose)
            audit(tx, "password.reset_requested", user_id=user["id"])
    if link:
        # Sent from another thread, so the page takes as long whether or not the email has an account.
        send_link(dict(user), purpose, link, background=True)
    # The same answer whether or not the email has an account.
    return page("forgot.html", sent=True)


def set_password_page(token, purpose):
    with db().transaction() as tx:
        row = find_link(tx, token, purpose)
    if not row:
        return page("link_expired.html", 410, purpose=purpose)
    if request.method == "GET":
        return page("set_password.html", purpose=purpose, name=row["name"], email=row["email"])
    check_form_token()
    password = request.form.get("password", "")
    error = password_problem(password, row["email"])
    if not error and password != request.form.get("confirm", ""):
        error = "The two passwords don't match."
    if error:
        return page("set_password.html", 400, purpose=purpose, name=row["name"], email=row["email"], error=error)
    with db().transaction() as tx:
        # Mark the link used first, so two submissions can't both succeed.
        used = tx.execute("UPDATE user_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                          (now(), row["token_hash"]))
        if not used.rowcount:
            return page("link_expired.html", 410, purpose=purpose)
        tx.execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?", (hasher.hash(password), row["id"]))
        tx.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        forget_devices(tx, row["id"])
        audit(tx, "invite.accepted" if purpose == "invite" else "password.reset", user_id=row["id"])
        session_token, max_age = start_session(tx, row["id"])
    if purpose == "reset":
        password_changed_notice(row, "reset")
    return signed_in_redirect(session_token, max_age, "/")


@bp.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    return set_password_page(token, "invite")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    return set_password_page(token, "reset")


# --- Your account ---------------------------------------------------------------

def other_sessions(tx):
    return tx.execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND token_hash <> ? AND expires_at > ?",
                      (g.user["id"], g.session["token_hash"], now())).fetchone()["n"]


def account_page(status=200, **context):
    if "others" not in context:
        with db().transaction() as tx:
            context["others"] = other_sessions(tx)
    return page("account.html", status, **context)


@bp.get("/account")
def account():
    return account_page()


@bp.post("/account/password")
def change_password():
    check_form_token()
    current, password = request.form.get("current", ""), request.form.get("password", "")
    with db().transaction() as tx:
        stored = tx.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()["password_hash"]
        others = other_sessions(tx)
    try:
        ok = hasher.verify(stored, current)
    except (VerificationError, InvalidHashError):
        ok = False
    error = None if ok else "Your current password isn't right."
    error = error or password_problem(password, g.user["email"])
    if not error and password != request.form.get("confirm", ""):
        error = "The two new passwords don't match."
    if not error and password == current:
        error = "Choose a password different from your current one."
    if error:
        return page("account.html", 400, others=others, error=error)
    with db().transaction() as tx:
        tx.execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?", (hasher.hash(password), g.user["id"]))
        # Any other device signed in with the old password is signed out.
        tx.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (g.user["id"], g.session["token_hash"]))
        forget_devices(tx, g.user["id"], keep_this_one=True)
        audit(tx, "password.changed")
    password_changed_notice(g.user, "changed")
    g.user["must_change"] = 0
    return page("account.html", others=0, notice="Password changed. You've been signed out on your other devices.")


@bp.post("/account/signout-others")
def sign_out_others():
    check_form_token()
    with db().transaction() as tx:
        tx.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (g.user["id"], g.session["token_hash"]))
        forget_devices(tx, g.user["id"], keep_this_one=True)
        audit(tx, "logout.others")
    return page("account.html", others=0, notice="You've been signed out on all your other devices.")


# --- Manage users (admins) ---------------------------------------------------------

def classrooms(tx):
    rows = tx.execute("SELECT id, json FROM classrooms").fetchall()
    names = {}
    for row in rows:
        try:
            names[row["id"]] = json.loads(row["json"]).get("name") or row["id"]
        except ValueError:
            names[row["id"]] = row["id"]
    return sorted(names.items(), key=lambda item: item[1].lower())


def users_page(status=200, **context):
    with db().transaction() as tx:
        users = [dict(row) for row in tx.execute(
            "SELECT id, email, name, role, disabled, password_hash IS NOT NULL AS active, last_login_at, avatar_at FROM users "
            "WHERE developer = 0 ORDER BY disabled, role, name").fetchall()]
        members = {}
        for row in tx.execute("SELECT class_id, user_id FROM classroom_members").fetchall():
            members.setdefault(row["user_id"], set()).add(row["class_id"])
        rooms = classrooms(tx)
    for user in users:
        user["classes"] = members.get(user["id"], set())
    mailer = current_app.config["MAILER"]
    return page("admin_users.html", status, users=users, rooms=rooms, mail_from=getattr(mailer, "sender_address", ""),
                mail_test_mode=bool(getattr(mailer, "redirect_to", "")), email_previews=EMAIL_PREVIEWS, **context)


EMAIL_PREVIEWS = (("invite", "Invite"), ("reset", "Password reset"), ("password_changed", "Password changed"),
                  ("login_code", "Office sign-in code"),
                  ("test", "Test email"), ("help_reply", "FAQs reply"), ("help_summary", "Weekly Help or Requests summary"))


def chosen_classes(tx):
    known = {c for c, _ in classrooms(tx)}
    return sorted(set(request.form.getlist("classes")) & known)


def set_classes(tx, user_id, role, classes):
    tx.execute("DELETE FROM classroom_members WHERE user_id = ?", (user_id,))
    # The Office and HeadTeachers see every classroom, so they are not assigned any.
    if role == "teacher":
        for class_id in classes:
            tx.execute("INSERT INTO classroom_members VALUES (?, ?)", (class_id, user_id))


def room_names(tx, user_id):
    """The names of the classrooms a teacher is assigned to, for their invite email."""
    mine = {row["class_id"] for row in tx.execute("SELECT class_id FROM classroom_members WHERE user_id = ?",
                                                  (user_id,)).fetchall()}
    return [name for class_id, name in classrooms(tx) if class_id in mine]


def user_by_id(tx, user_id):
    # The developer's stand-in isn't an account anyone manages.
    row = tx.execute("SELECT id, email, name, role, disabled, password_hash IS NOT NULL AS active FROM users WHERE id = ? "
                     "AND developer = 0", (user_id,)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def active_admins(tx):
    return tx.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0 AND developer = 0").fetchone()["n"]


@bp.get("/admin/users")
@admin_required
def admin_users():
    return users_page()


@bp.post("/admin/users/invite")
@admin_required
def admin_invite():
    check_form_token()
    name = " ".join(request.form.get("name", "").split())[:80]
    email = norm_email(request.form.get("email"))[:254]
    role = request.form.get("role")
    if not name or not EMAIL.fullmatch(email) or role not in ROLES:
        return users_page(400, error="Enter a name, a valid email address and a role.", invite_open=True)
    with db().transaction() as tx:
        if tx.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            return users_page(400, error=f"{email} already has an account.", invite_open=True)
        # One account for each email across every school, since the email is what finds the school at sign-in.
        if db().taken_elsewhere(email, db().current_id()):
            return users_page(400, error=f"{email} already has an Ilmi account elsewhere, so it can't be invited here. "
                                         "Use another email address.", invite_open=True)
        user_id = tx.insert("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, ?, ?)",
                            (email, name, role, now()))
        db().claim(email, db().current_id())
        set_classes(tx, user_id, role, chosen_classes(tx))
        rooms = room_names(tx, user_id) if role == "teacher" else []
        link = issue_link(tx, user_id, "invite")
        audit(tx, "user.invited", detail={"email": email, "role": role, "user": user_id})
    sent = send_link({"name": name, "email": email, "role": role}, "invite", link, by=g.user["name"], rooms=rooms)
    return users_page(notice=f"Invite {'emailed to' if sent else 'created for'} {email}.",
                      link=None if sent else link, link_for=name)


@bp.post("/admin/users/<int:user_id>/update")
@admin_required
def admin_update(user_id):
    check_form_token()
    name = " ".join(request.form.get("name", "").split())[:80]
    role = request.form.get("role")
    if not name or role not in ROLES:
        return users_page(400, error="Enter a name and a role.")
    with db().transaction() as tx:
        user = user_by_id(tx, user_id)
        if user["role"] == "admin" and role != "admin" and active_admins(tx) <= 1 and not user["disabled"]:
            return users_page(400, error="Keep at least one Office account. Give someone else the Office role first.")
        classes = chosen_classes(tx)
        tx.execute("UPDATE users SET name = ?, role = ? WHERE id = ?", (name, role, user_id))
        set_classes(tx, user_id, role, classes)
        audit(tx, "user.updated", detail={"user": user_id, "role": role, "classes": classes})
    return users_page(notice=f"Saved {name}.")


@bp.post("/admin/users/<int:user_id>/link")
@admin_required
def admin_link(user_id):
    """A new reset link, or a new invite for someone who never set a password."""
    check_form_token()
    with db().transaction() as tx:
        user = user_by_id(tx, user_id)
        if user["disabled"]:
            return users_page(400, error="Turn this account back on first.")
        purpose = "reset" if user["active"] else "invite"
        rooms = room_names(tx, user_id) if user["role"] == "teacher" else []
        link = issue_link(tx, user_id, purpose)
        audit(tx, f"user.{purpose}_link", detail={"user": user_id})
    sent = send_link(user, purpose, link, by=g.user["name"], rooms=rooms)
    what = "Password reset link" if purpose == "reset" else "New invite"
    return users_page(notice=f"{what} {'emailed to' if sent else 'created for'} {user['email']}.",
                      link=None if sent else link, link_for=user["name"])


@bp.post("/admin/users/<int:user_id>/access")
@admin_required
def admin_access(user_id):
    check_form_token()
    disable = request.form.get("disable") == "1"
    with db().transaction() as tx:
        user = user_by_id(tx, user_id)
        if disable and user_id == g.user["id"]:
            return users_page(400, error="You can't turn off your own account.")
        if disable and user["role"] == "admin" and active_admins(tx) <= 1:
            return users_page(400, error="Keep at least one Office account.")
        tx.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disable else 0, user_id))
        if disable:
            tx.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            tx.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,))
            tx.execute("UPDATE user_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now(), user_id))
        audit(tx, "user.disabled" if disable else "user.enabled", detail={"user": user_id})
    return users_page(notice=f"{user['name']} can {'no longer' if disable else 'now'} sign in.")


@bp.post("/admin/email/test")
@admin_required
def admin_email_test():
    """Sends the signed-in Office account a test email, to check that email is set up."""
    check_form_token()
    if not current_app.config["MAILER"].enabled:
        return users_page(400, error="Email isn't set up yet. Add RESEND_API_KEY and MAIL_FROM on Railway.")
    sent = emails.send("test", g.user["email"], name=g.user["name"], email=g.user["email"], at=now())
    with db().transaction() as tx:
        audit(tx, "email.test", detail={"sent": sent})
    if not sent:
        return users_page(502, error="The test email couldn't be sent. The site's service logs on Railway say why.")
    return users_page(notice=f"Test email sent to {g.user['email']}. It should arrive within a minute.")


@bp.get("/admin/email/preview/<template>")
@admin_required
def admin_email_preview(template):
    """Shows an email with made-up details, as staff would see it. ?text=1 shows the plain-text part."""
    if template not in emails.PREVIEWS:
        abort(404)
    subject, text, html = emails.render(template, **emails.PREVIEWS[template]())
    if request.args.get("text"):
        return current_app.response_class(f"Subject: {subject}\n\n{text}", mimetype="text/plain")
    return html


@bp.get("/admin/activity")
@admin_required
def admin_activity():
    with db().transaction() as tx:
        rows = tx.execute("SELECT a.at, a.action, a.class_id, a.detail, a.ip, u.name, u.email FROM audit_log a "
                          "LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT 300").fetchall()
    return page("admin_activity.html", rows=rows)


# --- Startup ------------------------------------------------------------------

def bootstrap_admin(router, email, password, developer=""):
    """Creates the first school's first Office account from ADMIN_EMAIL and DASHBOARD_PASSWORD when it has no accounts
    yet. With a developer account (DEVELOPER_EMAIL) there needn't be one: the developer opens the school and invites
    its Office."""
    email, developer = norm_email(email), norm_email(developer)
    with router.first.transaction() as tx:
        if tx.execute("SELECT COUNT(*) AS n FROM users WHERE developer = 0").fetchone()["n"]:
            return
        if developer and (not email or email == developer):
            return
        if not EMAIL.fullmatch(email) or not password or password == "replace-with-a-long-unique-password":
            raise RuntimeError("There are no accounts yet. Set ADMIN_EMAIL and DASHBOARD_PASSWORD to create the "
                               "first Office account; they will choose a new password when they first sign in. "
                               "Or set DEVELOPER_EMAIL to create the developer account, which can add schools "
                               "and invite each one's Office.")
        if router.taken_elsewhere(email, schools.FIRST):
            return
        tx.insert("INSERT INTO users (email, name, role, password_hash, must_change, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                  (email, "Office", "admin", hasher.hash(password), now()))
        router.claim(email, schools.FIRST)
        print(f"Created the first Office account for {email}.", flush=True)


def reset_admin(router, email, password):
    """For an Office account or the developer locked out while email isn't set up, so no reset link can reach them.
    With ADMIN_RESET_PASSWORD set on Railway, the next start gives ADMIN_EMAIL's account that password (to be changed
    at the next sign-in), turns it back on with its Office role, clears its failed sign-ins and signs it out
    everywhere. The account is found at whichever school it belongs to; DEVELOPER_EMAIL's is the developer account.

    Each value is used once: a fingerprint of it is kept in site_settings, so later restarts with the same value
    change nothing. Remove the variable from Railway once the account is signed in."""
    if not password:
        return
    email = norm_email(email)
    if not EMAIL.fullmatch(email):
        print("ADMIN_RESET_PASSWORD is set but ADMIN_EMAIL isn't an email address; nothing was reset.", flush=True)
        return
    if len(password) < PASSWORD_MIN:
        print(f"ADMIN_RESET_PASSWORD must be at least {PASSWORD_MIN} characters; nothing was reset.", flush=True)
        return
    mark = hashlib.sha256(f"{email}\n{password}".encode()).hexdigest()
    home = router.home_of(email)
    with router.get(schools.FIRST if home is None else home).transaction() as tx:
        done = tx.execute("SELECT value FROM site_settings WHERE key = 'admin_reset'").fetchone()
        if done and done["value"] == mark:
            return
        user = tx.execute("SELECT id FROM users WHERE email = ? AND developer = 0", (email,)).fetchone()
        if not user:
            print(f"ADMIN_RESET_PASSWORD is set but there is no account for {email}; nothing was reset.", flush=True)
            return
        t = now()
        tx.execute("UPDATE users SET password_hash = ?, must_change = 1, disabled = 0, role = 'admin' WHERE id = ?",
                   (hasher.hash(password), user["id"]))
        for table in ("sessions", "trusted_devices", "login_codes"):
            tx.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
        tx.execute("INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES ('admin_reset', ?, ?, NULL) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (mark, t))
        tx.execute("INSERT INTO audit_log (at, user_id, action, class_id, detail, ip) VALUES (?, ?, ?, NULL, NULL, '')",
                   (t, user["id"], "password.reset_on_railway"))
    # Failed sign-ins are counted in the control database, for every school.
    with router.control.transaction() as tx:
        tx.execute("DELETE FROM attempts WHERE email = ?", (email,))
    print(f"Reset the password for {email} from ADMIN_RESET_PASSWORD and cleared its sign-in block. "
          "Remove ADMIN_RESET_PASSWORD from Railway once you have signed in.", flush=True)


def prune(database, retention_days):
    """Removes records past their retention period, and expired sessions and links."""
    t = now()
    cutoff = t - retention_days * DAY
    with database.transaction() as tx:
        tx.execute("DELETE FROM audit_log WHERE at < ?", (cutoff,))
        tx.execute("DELETE FROM classroom_history WHERE saved_at < ?", (cutoff,))
        tx.execute("DELETE FROM attempts WHERE at < ?", (t - DAY,))
        # Also ends any 30-day sign-ins left from the old "keep me signed in" option, which had no remember mark.
        tx.execute("DELETE FROM sessions WHERE expires_at < ? OR (remember = 0 AND (last_seen_at < ? OR expires_at > ?)) "
                   "OR (remember = 1 AND (last_seen_at < ? OR expires_at > ?))",
                   (t, t - SESSION_IDLE, t + SESSION_SHORT, t - SESSION_STAY_IDLE, t + SESSION_STAY))
        tx.execute("DELETE FROM user_tokens WHERE expires_at < ?", (t - DAY,))
        tx.execute("DELETE FROM login_codes WHERE expires_at < ?", (t,))
        tx.execute("DELETE FROM trusted_devices WHERE expires_at < ?", (t,))
        # Presenter links never opened, or whose tab has gone quiet (closed); tv.py has the times.
        tx.execute("DELETE FROM presenter_links WHERE (tab_hash IS NULL AND created_at < ?) OR last_seen_at < ?",
                   (t - 10 * MINUTE, t - 12 * HOUR))
