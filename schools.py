"""Many schools on one site, each with a database of its own, and the developer's account.

Every school's records, staff accounts, settings, history and TV links live in that school's own database (see
db.py): nothing is shared between schools, and a query made for one school can't reach another's tables. The first
school keeps the tables the site had before there were schools. The control database holds the list of schools,
which school each sign-in email belongs to, and the developer's own account.

`Schools` stands where the single database used to (app.config["DB"]): `transaction()` opens the database of the
school the current request belongs to. That school is worked out once per request, in auth.load_session, from the
sign-in cookie or from the link's token, both of which start with the school's number ("3.<random>"); a cookie or
link from before there were schools has no number and belongs to the first school.

The developer (DEVELOPER_EMAIL, one account, kept in the control database) signs in like anyone else and lands on
/schools: every school as a tile. There they add a school and set its admin account (the person in charge of that
school's settings): someone new is sent an invite to choose a password, and someone who already has an account at the
school is given the admin role. Opening a school shows it as its admin sees it; the bar across the top switches the
view to HeadTeacher or Teacher and leads back to all schools. Nobody can be given the developer role:
it isn't a role in any school's accounts, only the account for that one email. Inside a school the developer's
work is recorded under a stand-in account (users.developer = 1) that can't sign in and isn't listed on Manage users.
"""
import json
import secrets
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from flask import Blueprint, abort, current_app, g, redirect, request

import auth
from db import DB_ERRORS, SCHEMA, Database

CONTROL = 0  # the control database's number in cookies, links and school_emails
FIRST = 1    # the school whose tables were there before there were schools
VIEWS = ("admin", "head", "teacher")
NAME_MAX = 60  # site_settings.TITLE_MAX: a school's name is its site title
MAX_SCHOOLS = 500
# The school the current request (or background job) is working in; None outside both, which means the first school.
_current = ContextVar("ilmi_school", default=None)

bp = Blueprint("schools", __name__)


class Schools:
    def __init__(self, database_url, data_dir, testing=False):
        self._args = (database_url, data_dir, testing)
        self.postgres = bool(database_url)
        self._prefix = "ilmi"
        self._dbs = {}
        self._names = {}
        self._lock = threading.Lock()
        self.first = Database(*self._args)
        self.control = None

    # --- Which school ---------------------------------------------------------------------------------------------

    def set(self, school_id):
        _current.set(school_id)

    def reset(self):
        _current.set(None)

    def current_id(self):
        school_id = _current.get()
        return FIRST if school_id is None else school_id

    def current(self):
        return self.get(self.current_id())

    @contextmanager
    def use(self, school_id):
        token = _current.set(school_id)
        try:
            yield self.get(school_id)
        finally:
            _current.reset(token)

    def transaction(self):
        return self.current().transaction()

    def get(self, school_id):
        if school_id == CONTROL:
            return self.control
        if school_id == FIRST:
            return self.first
        with self._lock:
            if school_id not in self._dbs:
                if type(school_id) is not int or school_id < 1:
                    raise KeyError(school_id)
                self._dbs[school_id] = Database(*self._args, schema=f"{self._prefix}_school_{school_id}",
                                                filename=f"school-{school_id}.sqlite3")
            return self._dbs[school_id]

    def exists(self, school_id):
        """Whether there is a school with this number (the control database counts). Schools are never removed, so
        one seen once is remembered."""
        if school_id == CONTROL or school_id in self._names:
            return True
        if type(school_id) is not int or school_id < 1:
            return False
        self.all()
        return school_id in self._names

    def all(self):
        with self.control.transaction() as tx:
            rows = [dict(r) for r in tx.execute("SELECT id, name, created_at FROM schools ORDER BY id").fetchall()]
        self._names = {r["id"]: r["name"] for r in rows}
        return rows

    def name(self, school_id):
        if school_id not in self._names:
            self.all()
        return self._names.get(school_id, "")

    def databases(self):
        """Every database: the control database, then each school's."""
        return [self.control] + [self.get(row["id"]) for row in self.all()]

    # --- Startup --------------------------------------------------------------------------------------------------

    def migrate(self):
        self.first.migrate()  # also waits for a database service that is still starting
        database_url, data_dir, testing = self._args
        schema = None
        if self.postgres:
            with self.first.transaction() as tx:
                base = tx.execute("SELECT current_schema() AS s").fetchone()["s"]
            # On Railway the first school's tables are in "public", and the rest go in ilmi_control, ilmi_school_2...
            # A connection that names another schema (the tests do) keeps everything beside that schema instead.
            self._prefix = "ilmi" if base in (None, "public") else base
            schema = f"{self._prefix}_control"
            if not SCHEMA.fullmatch(schema):
                raise RuntimeError("The database's schema name can't be used for the schools' schemas.")
            with self.first.transaction() as tx:
                tx.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        self.control = Database(database_url, data_dir, testing, schema=schema, filename="control.sqlite3", control=True)
        self.control.migrate()
        with self.first.transaction() as tx:
            row = tx.execute("SELECT value FROM site_settings WHERE key = 'title'").fetchone()
            title = row["value"] if row else ""
        with self.control.transaction() as tx:
            if not tx.execute("SELECT 1 FROM schools").fetchone():
                tx.execute("INSERT INTO schools (name, created_at) VALUES (?, ?)", (title or "First school", auth.now()))
        for row in self.all():
            if row["id"] != FIRST:
                self._prepare(row["id"])

    def _prepare(self, school_id):
        database = self.get(school_id)
        if self.postgres:
            with self.control.transaction() as tx:
                tx.execute(f'CREATE SCHEMA IF NOT EXISTS "{database.schema}"')
        database.migrate()
        return database

    def index_emails(self, developer_email=""):
        """Fills in which school each account belongs to: at startup, for accounts from before there were schools."""
        for row in self.all():
            with self.get(row["id"]).transaction() as tx:
                emails = [r["email"] for r in tx.execute("SELECT email FROM users WHERE developer = 0").fetchall()]
            with self.control.transaction() as tx:
                for email in emails:
                    tx.execute("INSERT INTO school_emails (email, school_id) VALUES (?, ?) ON CONFLICT (email) DO NOTHING",
                               (email, row["id"]))
        if developer_email:
            self.claim(developer_email, CONTROL)

    def ensure_developer(self, email, first_password=""):
        """Makes DEVELOPER_EMAIL's account the one developer account, at startup. If that email already has an
        account at a school, the developer keeps its name, password and picture, and that account becomes their
        stand-in there. Any other account in the control database is turned off: only this email is the developer."""
        email = auth.norm_email(email)
        if email and not auth.EMAIL.fullmatch(email):
            print("DEVELOPER_EMAIL isn't an email address, so there is no developer account.", flush=True)
            email = ""
        with self.control.transaction() as tx:
            for row in tx.execute("SELECT id FROM users WHERE email <> ? AND disabled = 0", (email,)).fetchall():
                tx.execute("UPDATE users SET disabled = 1 WHERE id = ?", (row["id"],))
                for table in ("sessions", "trusted_devices", "login_codes"):
                    tx.execute(f"DELETE FROM {table} WHERE user_id = ?", (row["id"],))
            if not email:
                return
            mine = tx.execute("SELECT id, disabled FROM users WHERE email = ?", (email,)).fetchone()
            if mine and mine["disabled"]:
                tx.execute("UPDATE users SET disabled = 0 WHERE id = ?", (mine["id"],))
        if mine:
            return
        earlier = picture = None
        for row in self.all():
            with self.get(row["id"]).transaction() as tx:
                found = tx.execute("SELECT id, name, password_hash, must_change, theme, avatar_at FROM users "
                                   "WHERE email = ? AND developer = 0", (email,)).fetchone()
                if not found:
                    continue
                if earlier is None or (found["password_hash"] and not earlier["password_hash"]):
                    earlier = dict(found)
                    image = tx.execute("SELECT image FROM avatars WHERE user_id = ?", (found["id"],)).fetchone()
                    picture = bytes(image["image"]) if image else None
                make_stand_in(tx, found["id"])
        t = auth.now()
        password_hash, must_change = (earlier["password_hash"], earlier["must_change"]) if earlier else (None, 0)
        if not password_hash and first_password and first_password != "replace-with-a-long-unique-password":
            password_hash, must_change = auth.hasher.hash(first_password), 1
        theme = earlier["theme"] if earlier and not str(earlier["theme"]).startswith("c") else "light"
        with self.control.transaction() as tx:
            user_id = tx.insert("INSERT INTO users (email, name, role, password_hash, must_change, created_at, theme, "
                                "tour_done) VALUES (?, ?, 'admin', ?, ?, ?, ?, ?)",
                                (email, earlier["name"] if earlier else "Developer", password_hash, must_change, t, theme, t))
            if picture:
                tx.execute("INSERT INTO avatars (user_id, image, updated_at) VALUES (?, ?, ?)", (user_id, picture, t))
                tx.execute("UPDATE users SET avatar_at = ? WHERE id = ?", (t, user_id))
        self.claim(email, CONTROL)
        how = ("with the password it already had" if earlier and earlier["password_hash"] else
               "with DASHBOARD_PASSWORD, to be changed at the first sign-in" if password_hash else
               "with no password yet: use “Forgotten your password?” on the sign-in page to choose one")
        print(f"Created the developer account for {email}, {how}.", flush=True)

    # --- Which school an email belongs to ---------------------------------------------------------------------------

    def home_of(self, email):
        """The school this email's account is at (CONTROL for the developer), or None when it has no account. The
        note of it in school_emails is checked first; an account with no note (or a stale one) is looked for in
        each school and noted, so an account that exists can always sign in."""
        if not email:
            return None
        with self.control.transaction() as tx:
            row = tx.execute("SELECT school_id FROM school_emails WHERE email = ?", (email,)).fetchone()
        if row and self.exists(row["school_id"]) and self._has(row["school_id"], email):
            return row["school_id"]
        for school in [{"id": CONTROL}] + self.all():
            if self._has(school["id"], email):
                self.claim(email, school["id"])
                return school["id"]
        return None

    def _has(self, school_id, email):
        with self.get(school_id).transaction() as tx:
            return tx.execute("SELECT 1 FROM users WHERE email = ? AND developer = 0", (email,)).fetchone() is not None

    def taken_elsewhere(self, email, school_id):
        """True when this email has an account at another school (or is the developer's), so it can't have one here."""
        home = self.home_of(email)
        return home is not None and home != school_id

    def claim(self, email, school_id):
        with self.control.transaction() as tx:
            tx.execute("INSERT INTO school_emails (email, school_id) VALUES (?, ?) "
                       "ON CONFLICT (email) DO UPDATE SET school_id = excluded.school_id", (email, school_id))

    # --- Adding and naming schools ----------------------------------------------------------------------------------

    def create(self, name):
        with self.control.transaction() as tx:
            school_id = tx.insert("INSERT INTO schools (name, created_at) VALUES (?, ?)", (name, auth.now()))
        database = self._prepare(school_id)
        with database.transaction() as tx:
            # A migration adds the first school's historical house points; a new school starts with none.
            tx.execute("DELETE FROM house_points")
            _write_title(tx, name)
        self._names[school_id] = name
        return school_id

    def rename(self, school_id, name, title_too=True):
        with self.control.transaction() as tx:
            tx.execute("UPDATE schools SET name = ? WHERE id = ?", (name, school_id))
        self._names[school_id] = name
        if title_too:
            with self.get(school_id).transaction() as tx:
                _write_title(tx, name)


def _write_title(tx, name):
    tx.execute("INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES ('title', ?, ?, NULL) "
               "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
               "updated_by = NULL", (name, auth.now()))


def make_stand_in(tx, user_id):
    """Turns a school's account into the developer's stand-in there: it keeps its history, and can no longer sign in."""
    tx.execute("UPDATE users SET developer = 1, role = 'admin', password_hash = NULL, must_change = 0, disabled = 0 "
               "WHERE id = ?", (user_id,))
    tx.execute("DELETE FROM classroom_members WHERE user_id = ?", (user_id,))
    for table in ("sessions", "trusted_devices", "login_codes"):
        tx.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    tx.execute("UPDATE user_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (auth.now(), user_id))


def stand_in(tx, developer):
    """The id of the developer's stand-in account in this school, made on their first visit."""
    row = tx.execute("SELECT id, name FROM users WHERE developer = 1 ORDER BY id LIMIT 1").fetchone()
    if row:
        if row["name"] != developer["name"]:
            tx.execute("UPDATE users SET name = ? WHERE id = ?", (developer["name"], row["id"]))
        return row["id"]
    earlier = tx.execute("SELECT id FROM users WHERE email = ?", (developer["email"],)).fetchone()
    if earlier:
        make_stand_in(tx, earlier["id"])
        return earlier["id"]
    t = auth.now()
    # tour_done: the tutorial doesn't start in every school the developer opens; Account can still start it.
    return tx.insert("INSERT INTO users (email, name, role, created_at, developer, tour_done) VALUES (?, ?, 'admin', ?, 1, ?)",
                     (developer["email"], developer["name"], t, t))


def token_school(token):
    """The school a cookie or link token names ("3.<random>"), and the token's own part. One without a number is
    from before there were schools: the first school's."""
    head, dot, rest = (token or "").partition(".")
    if not dot:
        return FIRST, token
    if not (head.isascii() and head.isdigit()) or len(head) > 6:
        return None, rest
    return int(head), rest


def new_token(nbytes=32):
    """A random token for a link or cookie, starting with the current school's number."""
    return f"{router().current_id()}.{secrets.token_urlsafe(nbytes)}"


def router():
    return current_app.config["DB"]


# --- The developer's pages ------------------------------------------------------------------------------------------

def developer_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.get("developer") or not g.user or g.user["role"] != "developer":
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.app_template_global("developer_bar")
def developer_bar():
    """What the developer's bar shows on this page, or None for everyone else: the school they have opened, the view
    they are looking at it as, and whether this page is inside that school."""
    developer = g.get("developer")
    if not developer:
        return None
    school_id = developer["school_id"]
    inside = bool(g.user and g.user["role"] != "developer")
    return {"school": router().name(school_id) if school_id else "", "view": developer["view"], "inside": inside,
            "teacher": developer.get("teacher"), "teacher_name": developer.get("teacher_name", ""),
            "teachers": teachers() if inside else [],
            "views": [(v, auth.ROLE_NAMES[v]) for v in VIEWS]}


def teachers():
    """The open school's teachers, for the developer's Teacher pop-up: each one's id, name and classes."""
    if "dev_teachers" not in g:
        with router().transaction() as tx:
            people = tx.execute("SELECT id, name FROM users WHERE role = 'teacher' AND disabled = 0 AND developer = 0 "
                                "ORDER BY name").fetchall()
            rooms = {r["id"]: r["json"] for r in tx.execute("SELECT id, json FROM classrooms").fetchall()}
            member = tx.execute("SELECT class_id, user_id FROM classroom_members").fetchall()
        import tv  # the dashboard's own way of naming a class: "Yr3-A-QM-Br Ahmed" is "Yr3A Ustadh Ahmed"
        names = {}
        for class_id, text in rooms.items():
            try:
                names[class_id] = tv.class_label(json.loads(text).get("name") or class_id)
            except ValueError:
                names[class_id] = class_id
        mine = {}
        for row in member:
            if row["class_id"] in names:
                mine.setdefault(row["user_id"], []).append(names[row["class_id"]])
        g.dev_teachers = [{"id": p["id"], "name": p["name"], "classes": sorted(mine.get(p["id"], []))} for p in people]
    return g.dev_teachers


def summary(school):
    """A school's tile: its classes, students and staff, and who its admin is."""
    info = dict(school, classes=0, students=0, staff=0, office=[], invited=[], offices=[], ok=True)
    try:
        with router().get(school["id"]).transaction() as tx:
            rooms = tx.execute("SELECT json FROM classrooms").fetchall()
            people = tx.execute("SELECT name, email, role, password_hash IS NOT NULL AS active FROM users "
                                "WHERE developer = 0 AND disabled = 0 ORDER BY name").fetchall()
    except DB_ERRORS:
        current_app.logger.exception("Couldn't read school %s for its tile.", school["id"])
        info["ok"] = False
        return info
    info["classes"] = len(rooms)
    for room in rooms:
        try:
            info["students"] += len(json.loads(room["json"]).get("S", []))
        except ValueError:
            pass
    info["staff"] = len(people)
    # Its admin accounts: those who have signed in (chosen a password), then those invited and still to accept.
    info["offices"] = sorted(({"name": p["name"], "email": p["email"], "active": bool(p["active"])}
                              for p in people if p["role"] == "admin"), key=lambda o: not o["active"])
    info["office"] = [o["name"] for o in info["offices"] if o["active"]]
    info["invited"] = [o["name"] for o in info["offices"] if not o["active"]]
    return info


def schools_page(status=200, **context):
    tiles = [summary(row) for row in router().all()]
    context.setdefault("manage_open", None)
    # A problem with a pop-up's form is said inside that pop-up, which stays open, and not behind it as well.
    if context.get("error") and (context.get("new_open") or context["manage_open"]):
        context["pop_error"] = context.pop("error")
    return auth.page("schools.html", status, schools=tiles, name_max=NAME_MAX, invite_hours=auth.INVITE_TTL // auth.HOUR,
                     opened=g.developer["school_id"], **context)


def clean_name():
    import site_settings  # it imports auth, which imports this module
    return site_settings.tidy(request.form.get("name", ""))


# --- A school's admin account ----------------------------------------------------------------------------------

def office_fields():
    """The Admin's name and email as typed on Add a school or Manage."""
    return (" ".join(request.form.get("office_name", "").split())[:80],
            auth.norm_email(request.form.get("office_email"))[:254])


def office_problem(name, email, school_id=None):
    """Why this person can't be made the admin of this school (None: a school about to be added), or None."""
    if not name or not auth.EMAIL.fullmatch(email):
        return "Enter the Admin’s name and a valid email address."
    if email == auth.developer_email():
        return "That is your own developer email. The admin needs an email of its own."
    home = router().home_of(email)
    if home is not None and home != school_id:
        # One account for each email across every school, since the email is what finds the school at sign-in.
        return f"{email} already has an account at {router().name(home) or 'another school'}. Use another email address."
    if home is not None:
        with router().get(home).transaction() as tx:
            if tx.execute("SELECT disabled FROM users WHERE email = ? AND developer = 0", (email,)).fetchone()["disabled"]:
                return f"{email}’s account at this school is turned off. Open the school and turn it back on from Manage users first."
    return None


def set_office(school_id, name, email):
    """Makes this person an admin account of the school, working in that school's own database. Someone new gets
    an account and an invite link to choose their password; someone who already has an account there is given the
    Admin role (and a fresh invite if they never accepted theirs). Returns what happened: who, whether they were
    already at the school, whether an email went out, and the link to pass on when it didn't."""
    every, link = router(), None
    # The invite email carries the school's own title, not the one this page read for itself.
    g.pop("site_title", None)
    with every.use(school_id):
        with every.transaction() as tx:
            by = stand_in(tx, g.developer)  # the school's Activity records it under the developer's name
            row = tx.execute("SELECT id, name, role, password_hash IS NOT NULL AS active FROM users "
                             "WHERE email = ? AND developer = 0", (email,)).fetchone()
            if row:
                user_id, who, active, already = row["id"], row["name"], bool(row["active"]), True
                if row["role"] != "admin":
                    tx.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user_id,))
                    # The admin sees every classroom, so it isn't assigned any.
                    tx.execute("DELETE FROM classroom_members WHERE user_id = ?", (user_id,))
                    auth.audit(tx, "user.updated", detail={"user": user_id, "role": "admin", "classes": []}, user_id=by)
            else:
                user_id = tx.insert("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, 'admin', ?)",
                                    (email, name, auth.now()))
                every.claim(email, school_id)
                auth.audit(tx, "user.invited", detail={"email": email, "role": "admin", "user": user_id}, user_id=by)
                who, active, already = name, False, False
            if not active:
                link = auth.issue_link(tx, user_id, "invite")
                if already:
                    auth.audit(tx, "user.invite_link", detail={"user": user_id}, user_id=by)
        sent = bool(link) and auth.send_link({"name": who, "email": email, "role": "admin"}, "invite", link,
                                             by=g.developer["name"])
    g.pop("site_title", None)
    return {"name": who, "email": email, "already": already, "active": active, "sent": sent,
            "link": None if sent else link}


def office_notice(result, school, added=False):
    """What set_office did, for the top of the schools page; `added` when the school itself was just added."""
    name, email = result["name"], result["email"]
    if added:
        return (f"Added {school}. {name} has been emailed an invite at {email} to be its admin." if result["sent"] else
                f"Added {school}. {name} is its admin: send them the link below to choose a password.")
    if result["active"]:
        return f"{name} is now an admin account at {school}. They sign in with the password they already have."
    if result["sent"]:
        return f"{name} has been emailed an invite at {email}. They become {school}’s admin once they choose a password."
    return f"{name} now has an admin account at {school}. Send them the link below to choose a password."


def set_view(school_id, view):
    with router().control.transaction() as tx:
        tx.execute("UPDATE sessions SET school_id = ?, view_as = ? WHERE token_hash = ?",
                   (school_id, view, g.session["token_hash"]))


@bp.get("/schools")
@developer_required
def index():
    return schools_page()


@bp.post("/schools/new")
@developer_required
def new():
    auth.check_form_token()
    name = clean_name()
    office_name, office_email = office_fields()
    drafts = {"new_open": True, "name_draft": name, "office_name_draft": office_name, "office_email_draft": office_email}
    if not name or len(name) > NAME_MAX:
        return schools_page(400, error=f"Give the school a name of {NAME_MAX} characters or fewer.", **drafts)
    if len(router().all()) >= MAX_SCHOOLS:
        return schools_page(400, error=f"Ilmi holds at most {MAX_SCHOOLS} schools.", **drafts)
    # The admin account is checked before the school is made, so a mistyped email doesn't leave a school behind.
    with_office = bool(office_name or office_email)
    if with_office:
        error = office_problem(office_name, office_email)
        if error:
            return schools_page(400, error=error, **drafts)
    school_id = router().create(name)
    with router().control.transaction() as tx:
        auth.audit(tx, "school.created", detail={"school": school_id, "name": name})
    if not with_office:
        return schools_page(notice=f"Added {name}. It has no admin account yet: set one from Manage on its tile.")
    result = set_office(school_id, office_name, office_email)
    with router().control.transaction() as tx:
        auth.audit(tx, "school.office", detail={"school": school_id, "email": office_email})
    return schools_page(notice=office_notice(result, name, added=True), link=result["link"], link_for=result["name"])


@bp.post("/schools/<int:school_id>/office")
@developer_required
def office(school_id):
    """Sets a school's admin account: the person in charge of that school's settings, accounts and records."""
    auth.check_form_token()
    if not router().exists(school_id) or school_id == CONTROL:
        abort(404)
    name, email = office_fields()
    error = office_problem(name, email, school_id)
    if error:
        return schools_page(400, error=error, manage_open=school_id, office_name_draft=name, office_email_draft=email)
    result = set_office(school_id, name, email)
    with router().control.transaction() as tx:
        auth.audit(tx, "school.office", detail={"school": school_id, "email": email})
    return schools_page(notice=office_notice(result, router().name(school_id)), link=result["link"],
                        link_for=result["name"])


@bp.post("/schools/<int:school_id>/rename")
@developer_required
def rename(school_id):
    auth.check_form_token()
    if not router().exists(school_id) or school_id == CONTROL:
        abort(404)
    name = clean_name()
    if not name or len(name) > NAME_MAX:
        return schools_page(400, error=f"Give the school a name of {NAME_MAX} characters or fewer.",
                            manage_open=school_id, name_draft=name)
    router().rename(school_id, name)
    with router().control.transaction() as tx:
        auth.audit(tx, "school.renamed", detail={"school": school_id, "name": name})
    return schools_page(notice=f"Renamed to {name}. Its pages show the new name under Ilmi.")


@bp.post("/schools/<int:school_id>/open")
@developer_required
def open_school(school_id):
    """Opens a school: the developer sees it as its admin does until they choose another view."""
    auth.check_form_token()
    if not router().exists(school_id) or school_id == CONTROL:
        abort(404)
    set_view(school_id, "admin")
    with router().control.transaction() as tx:
        auth.audit(tx, "school.opened", detail={"school": school_id})
    return redirect("/", code=303)


@bp.post("/schools/view")
@developer_required
def choose_view():
    """Admin, HeadTeacher or one teacher: how the developer sees the school they have opened. The Teacher view is
    always one teacher's, chosen in the bar's pop-up, so it shows exactly their classes."""
    auth.check_form_token()
    view = request.form.get("view")
    school_id = g.developer["school_id"]
    if view not in VIEWS or not school_id:
        abort(400)
    if view == "teacher":
        teacher = request.form.get("teacher", "")
        with router().use(school_id), router().transaction() as tx:
            ok = teacher.isdigit() and tx.execute("SELECT 1 FROM users WHERE id = ? AND role = 'teacher' AND disabled = 0 "
                                                  "AND developer = 0", (int(teacher),)).fetchone()
        if not ok:
            abort(400)
        view = f"teacher:{int(teacher)}"
    set_view(school_id, view)
    return redirect("/", code=303)
