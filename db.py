"""Database access and schema migrations.

PostgreSQL is used when DATABASE_URL is set; otherwise SQLite files in DATA_DIR,
which is meant for running locally. Queries are written with `?` placeholders and
rewritten for PostgreSQL.

Every school has a database of its own (see schools.py): on PostgreSQL a schema
of its own in the one server, on SQLite a file of its own. The first school keeps
the tables the site had before there were schools. One more database, the control
database, holds the list of schools and the developer's account.

Each migration runs once, in order, inside a transaction, and is recorded in
`schema_migrations`. Add new migrations to the end of MIGRATIONS; never edit one
that has already been deployed.
"""
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DB_ERRORS = (sqlite3.Error, psycopg.Error)


def _types(postgres):
    return {"id": "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT",
            "ref": "BIGINT" if postgres else "INTEGER"}


def _m1_classrooms(db, postgres):
    db.execute("CREATE TABLE IF NOT EXISTS classrooms (id TEXT PRIMARY KEY, json TEXT NOT NULL, revision INTEGER NOT NULL)")
    # The earliest version shipped with fixed classes in a `classes` table.
    if postgres:
        old = db.execute("SELECT to_regclass('classes') AS t").fetchone()["t"]
    else:
        old = db.execute("SELECT name AS t FROM sqlite_master WHERE type = 'table' AND name = 'classes'").fetchone()
    if old:
        db.execute("DROP TABLE classes")
        print("Deleted the classes table left by the earlier version of the dashboard.", flush=True)


def _m2_accounts(db, postgres):
    t = _types(postgres)
    # Times are Unix seconds. Tokens are stored only as SHA-256 hashes.
    db.execute(f"""CREATE TABLE users (
        id {t['id']},
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'teacher')),
        password_hash TEXT,
        must_change INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        last_login_at BIGINT)""")
    db.execute(f"""CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        last_seen_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        ip TEXT,
        user_agent TEXT)""")
    db.execute("CREATE INDEX sessions_user ON sessions (user_id)")
    db.execute(f"""CREATE TABLE user_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK (purpose IN ('invite', 'reset')),
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        used_at BIGINT)""")
    db.execute(f"""CREATE TABLE attempts (
        id {t['id']},
        kind TEXT NOT NULL,
        email TEXT NOT NULL,
        ip TEXT NOT NULL,
        at BIGINT NOT NULL,
        ok INTEGER NOT NULL)""")
    db.execute("CREATE INDEX attempts_email ON attempts (kind, email, at)")
    db.execute("CREATE INDEX attempts_ip ON attempts (kind, ip, at)")
    db.execute(f"""CREATE TABLE classroom_members (
        class_id TEXT NOT NULL,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        PRIMARY KEY (class_id, user_id))""")
    db.execute(f"""CREATE TABLE audit_log (
        id {t['id']},
        at BIGINT NOT NULL,
        user_id {t['ref']},
        action TEXT NOT NULL,
        class_id TEXT,
        detail TEXT,
        ip TEXT)""")
    db.execute("CREATE INDEX audit_log_at ON audit_log (at)")
    db.execute(f"""CREATE TABLE classroom_history (
        id {t['id']},
        class_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        json TEXT,
        saved_by {t['ref']},
        saved_at BIGINT NOT NULL)""")
    db.execute("CREATE INDEX classroom_history_class ON classroom_history (class_id, saved_at)")


def _m3_tv_screens(db, postgres):
    t = _types(postgres)
    # TV screen links (see tv.py). The token is stored only as a SHA-256 hash.
    db.execute(f"""CREATE TABLE tv_screens (
        id {t['id']},
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        created_at BIGINT NOT NULL,
        last_seen_at BIGINT,
        revoked_at BIGINT)""")


def _m4_tv_settings(db, postgres):
    # Per-screen TV settings as JSON, such as the daily Qaidah and Memo time windows (see tv.py).
    db.execute("ALTER TABLE tv_screens ADD COLUMN settings TEXT")


def _m5_house_points(db, postgres):
    t = _types(postgres)
    # House points added by hand for a whole house, not a student (see house_extra.py): n coins of one colour.
    db.execute(f"""CREATE TABLE house_points (
        id {t['id']},
        house TEXT NOT NULL,
        coin TEXT NOT NULL,
        n INTEGER NOT NULL,
        note TEXT NOT NULL,
        given_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        given_at BIGINT NOT NULL)""")
    # The school's points from before the dashboard counted them, as the school gave them on 25 October 2026.
    note, at = "Historical points from before the dashboard", 1792922400000  # milliseconds
    for house, coins in (("Al-Aqsa", (("tb", 4), ("tg", 10))), ("Madinah", (("tb", 7), ("tg", 16))),
                         ("Makkah", (("tr", 1), ("tb", 6), ("tg", 14)))):
        for coin, n in coins:
            db.execute("INSERT INTO house_points (house, coin, n, note, given_at) VALUES (%s, %s, %s, %s, %s)" if postgres else
                       "INSERT INTO house_points (house, coin, n, note, given_at) VALUES (?, ?, ?, ?, ?)", (house, coin, n, note, at))


def _m6_help_requests(db, postgres):
    t = _types(postgres)
    # Help or Requests from staff, and the admins' replies (see helpdesk.py).
    db.execute(f"""CREATE TABLE help_requests (
        id {t['id']},
        kind TEXT NOT NULL CHECK (kind IN ('help', 'request')),
        user_id {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        reply TEXT,
        replied_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        replied_by_name TEXT,
        replied_at BIGINT,
        emailed INTEGER NOT NULL DEFAULT 0,
        seen_at BIGINT)""")
    db.execute("CREATE INDEX help_requests_user ON help_requests (user_id, created_at)")
    db.execute("CREATE INDEX help_requests_created ON help_requests (created_at)")
    # One row per weekly summary email, so each week's is sent once even after a restart.
    db.execute("CREATE TABLE help_summaries (week TEXT PRIMARY KEY, sent_at BIGINT NOT NULL, items INTEGER NOT NULL)")


def _m7_avatars(db, postgres):
    t = _types(postgres)
    # Profile pictures (see avatars.py): one square JPEG per person, re-encoded on the server. users.avatar_at is
    # when it last changed, so pages can link to it with a version that changes with the picture.
    db.execute(f"""CREATE TABLE avatars (
        user_id {t['ref']} PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
        image {"BYTEA" if postgres else "BLOB"} NOT NULL,
        updated_at BIGINT NOT NULL)""")
    db.execute("ALTER TABLE users ADD COLUMN avatar_at BIGINT")


def _m8_tutorial(db, postgres):
    # When a teacher finished or skipped the dashboard tutorial; until then it starts on its own.
    db.execute("ALTER TABLE users ADD COLUMN tour_done BIGINT")


def _m9_tutorial_again(db, postgres):
    # The tutorial now starts for admins too: everyone, old accounts included, sees it once more.
    db.execute("UPDATE users SET tour_done = NULL")


def _m10_board_links(db, postgres):
    t = _types(postgres)
    # Read-only links to the memorisation progress board (the board is gone; the table is left unused). The token is
    # stored only as a SHA-256 hash. scope: "" (whole school), "primary", "secondary" or "class:<classroom id>"; fails: 0 leaves out fails and
    # tester feedback.
    db.execute(f"""CREATE TABLE board_links (
        id {t['id']},
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL DEFAULT '',
        fails INTEGER NOT NULL DEFAULT 1,
        created_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        created_at BIGINT NOT NULL,
        last_seen_at BIGINT,
        revoked_at BIGINT)""")


def _m11_login_codes(db, postgres):
    t = _types(postgres)
    # The emailed code an admin types after their password (see auth.py). token_hash is the SHA-256 hash of the random
    # token in that browser's cookie, code_hash the code's; fails counts wrong codes, and the code stops working at
    # LOGIN_CODE_FAILS.
    db.execute(f"""CREATE TABLE login_codes (
        token_hash TEXT PRIMARY KEY,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        fails INTEGER NOT NULL DEFAULT 0)""")


def _m12_stay_signed_in(db, postgres):
    # 1 for a sign-in made with "Stay signed in" (see auth.py): it lasts SESSION_STAY, not SESSION_SHORT.
    db.execute("ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 0")


def _m13_presenter_shares(db, postgres):
    t = _types(postgres)
    # No longer used: sharing a Presenter by link and code was removed (the Presenter now opens by its own link, see
    # _m14_presenter_links). A class's Presenter shared by link (the site's address/ustdtariq). The link stays the same for the class;
    # its 4-character code changes every day and when the teacher asks for a new one, and stops working after
    # SHARE_FAILS wrong tries. Each tab that typed the right code holds a random view token (stored only as a SHA-256
    # hash) until the tab is closed or the teacher stops sharing.
    db.execute(f"""CREATE TABLE presenter_shares (
        class_id TEXT PRIMARY KEY REFERENCES classrooms (id) ON DELETE CASCADE,
        slug TEXT NOT NULL UNIQUE,
        code TEXT,
        code_expires_at BIGINT NOT NULL DEFAULT 0,
        fails INTEGER NOT NULL DEFAULT 0,
        created_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        created_at BIGINT NOT NULL)""")
    db.execute("""CREATE TABLE presenter_views (
        token_hash TEXT PRIMARY KEY,
        class_id TEXT NOT NULL REFERENCES presenter_shares (class_id) ON DELETE CASCADE,
        created_at BIGINT NOT NULL,
        last_seen_at BIGINT NOT NULL)""")


def _m14_presenter_links(db, postgres):
    t = _types(postgres)
    # The teacher's Presenter, opened like a TV screen's link (see tv.py): each press of Presenter makes a new random
    # link (stored only as a SHA-256 hash) that the first tab to open it claims. That tab holds its own key (tab_hash)
    # in sessionStorage, so the link works there until the tab is closed, and nowhere else.
    db.execute(f"""CREATE TABLE presenter_links (
        token_hash TEXT PRIMARY KEY,
        class_id TEXT NOT NULL REFERENCES classrooms (id) ON DELETE CASCADE,
        created_by {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at BIGINT NOT NULL,
        tab_hash TEXT,
        last_seen_at BIGINT)""")


def _m15_trusted_devices(db, postgres):
    t = _types(postgres)
    # A browser where an admin typed their emailed code with "Stay signed in" ticked (see auth.py). For
    # TRUSTED_DEVICE_TTL its password alone signs that admin in, even after signing out. token_hash is the SHA-256
    # hash of the random token in that browser's cookie.
    db.execute(f"""CREATE TABLE trusted_devices (
        token_hash TEXT PRIMARY KEY,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL)""")


def _m16_head_teachers(db, postgres):
    # A third role, "head" (shown as HeadTeacher): sees every classroom, Record, House points, Rankings, Presenter and
    # FAQs, but not the Administration pages (see auth.py). The users table's CHECK on role must allow it.
    allowed = "CHECK (role IN ('admin', 'head', 'teacher'))"
    if postgres:
        names = [r["conname"] for r in db.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'users'::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) LIKE '%role%'").fetchall()]
        for name in names:
            db.execute(f'ALTER TABLE users DROP CONSTRAINT "{name}"')
        db.execute(f"ALTER TABLE users ADD CONSTRAINT users_role_check {allowed}")
        return
    # SQLite can't change a CHECK in place. Rebuilding the table would cascade-delete sessions and memberships, so the
    # stored CREATE TABLE text is edited instead, as SQLite documents for loosening a CHECK (existing rows still pass).
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
    new = re.sub(r"CHECK \(role IN \([^)]*\)\)", allowed, sql)
    if new == sql:
        return
    version = db.execute("PRAGMA schema_version").fetchone()[0]
    db.execute("PRAGMA writable_schema = ON")
    db.execute("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = 'users'", (new,))
    db.execute(f"PRAGMA schema_version = {version + 1}")
    db.execute("PRAGMA writable_schema = OFF")


def _m17_dark_mode(db, postgres):
    # Each person's look, chosen on Account: "light" (the default) or "dark". The sign-in pages are always light.
    db.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'light'")


def _m18_assistant(db, postgres):
    t = _types(postgres)
    # The staff assistant (see assistant.py). One row per question asked (question set) or preview confirmed
    # (question NULL): who, their role, when, the tools it used, and whether a request was submitted or a change
    # confirmed. The assistant's answers are never stored.
    db.execute(f"""CREATE TABLE assistant_log (
        id {t['id']},
        user_id {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        role TEXT NOT NULL,
        at BIGINT NOT NULL,
        question TEXT,
        tools TEXT NOT NULL DEFAULT '[]',
        request_submitted INTEGER NOT NULL DEFAULT 0,
        change_confirmed INTEGER NOT NULL DEFAULT 0)""")
    db.execute("CREATE INDEX assistant_log_user ON assistant_log (user_id, at)")
    # Requests staff submit through the assistant, which admins mark done or declined on /admin/requests.
    db.execute(f"""CREATE TABLE assistant_requests (
        id {t['id']},
        user_id {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        category TEXT NOT NULL CHECK (category IN ('feature', 'change', 'problem', 'other')),
        summary TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done', 'declined')),
        updated_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL,
        updated_at BIGINT)""")
    db.execute("CREATE INDEX assistant_requests_user ON assistant_requests (user_id, created_at)")
    # A change an admin's assistant has previewed, waiting for the admin to press Confirm. The token in the
    # admin's chat panel is stored only as a SHA-256 hash, works once, for its own admin, until expires_at.
    db.execute(f"""CREATE TABLE assistant_pending (
        token_hash TEXT PRIMARY KEY,
        user_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        used_at BIGINT,
        kind TEXT NOT NULL,
        params TEXT NOT NULL,
        summary TEXT NOT NULL)""")


def _m19_assistant_tokens(db, postgres):
    # The tokens each question used (see assistant.DAILY_TOKENS), for the teachers' daily allowance.
    db.execute("ALTER TABLE assistant_log ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0")


def _m20_themes(db, postgres):
    t = _types(postgres)
    blob = "BYTEA" if postgres else "BLOB"
    # Themes people make on Account (see themes.py). users.theme names a built-in theme ("light", "dark", "pink"...)
    # or one of these as "c<id>". json is the theme's colours and photo settings; derived is what themes.py worked
    # out from them (light or dark cards, the menu colour and the picker's tile), kept so pages needn't redo it.
    db.execute(f"""CREATE TABLE themes (
        id {t['id']},
        owner_id {t['ref']} NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        json TEXT NOT NULL,
        derived TEXT NOT NULL DEFAULT '{{}}',
        shared INTEGER NOT NULL DEFAULT 0,
        photo_at BIGINT NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL)""")
    # A theme's background photo: the upload re-encoded (for changing it again), a small copy for working out
    # the shade text needs, and the picture pages show (cropped, adjusted and shaded).
    db.execute(f"""CREATE TABLE theme_photos (
        theme_id {t['ref']} PRIMARY KEY REFERENCES themes (id) ON DELETE CASCADE,
        original {blob} NOT NULL,
        sample {blob} NOT NULL,
        shown {blob} NOT NULL)""")


def _m21_site_settings(db, postgres):
    # Settings for the whole site that admins choose on Account (site_settings.py), one row each. "title" is the
    # name shown under Ilmi in the top left corner of every page; with no row, pages show just Ilmi.
    t = _types(postgres)
    db.execute(f"""CREATE TABLE site_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at BIGINT NOT NULL,
        updated_by {t['ref']} REFERENCES users (id) ON DELETE SET NULL)""")


def _m22_schools(db, postgres):
    # Many schools (see schools.py). users.developer marks the developer's stand-in in a school: the row their work
    # there is recorded under, which nobody can sign in to, edit or see on Manage users. On the developer's own
    # sign-in (kept in the control database), sessions.school_id is the school they have opened and sessions.view_as
    # the role they are looking at it as ("admin", "head" or "teacher").
    db.execute("ALTER TABLE users ADD COLUMN developer INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE sessions ADD COLUMN school_id BIGINT")
    db.execute("ALTER TABLE sessions ADD COLUMN view_as TEXT")


def _c1_schools(db, postgres):
    t = _types(postgres)
    # The schools. A school's id names its database: the first school keeps the site's original tables, the
    # others get a schema (PostgreSQL) or a file (SQLite) of their own, made when the school is added.
    db.execute(f"""CREATE TABLE schools (
        id {t['id']},
        name TEXT NOT NULL,
        created_at BIGINT NOT NULL)""")
    # Which school each sign-in email belongs to (0: the developer), so one sign-in page serves every school and
    # an email has one account across all of them.
    db.execute("""CREATE TABLE school_emails (
        email TEXT PRIMARY KEY,
        school_id BIGINT NOT NULL)""")


MIGRATIONS = [_m1_classrooms, _m2_accounts, _m3_tv_screens, _m4_tv_settings, _m5_house_points, _m6_help_requests,
              _m7_avatars, _m8_tutorial, _m9_tutorial_again, _m10_board_links, _m11_login_codes, _m12_stay_signed_in,
              _m13_presenter_shares, _m14_presenter_links, _m15_trusted_devices, _m16_head_teachers, _m17_dark_mode,
              _m18_assistant, _m19_assistant_tokens, _m20_themes, _m21_site_settings, _m22_schools]
# Run after MIGRATIONS on the control database only, and recorded in its `control_migrations`.
CONTROL_MIGRATIONS = [_c1_schools]
SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class Database:
    """One database: a school's, or the control database (control=True).

    `schema` is the PostgreSQL schema its tables live in; None means wherever the connection puts them, which is
    where the first school's tables have always been. `filename` is its SQLite file in DATA_DIR."""

    def __init__(self, database_url, data_dir, testing=False, schema=None, filename="dashboard.sqlite3", control=False):
        self.postgres = bool(database_url)
        self.schema, self.control = schema, control
        if schema is not None and not SCHEMA.fullmatch(schema):
            raise ValueError("Not a schema name.")
        if self.postgres:
            def connect():
                db = psycopg.connect(database_url, connect_timeout=10, row_factory=dict_row)
                if schema:
                    try:
                        # Part of the transaction every query then runs in. Only this schema: a table missing
                        # here must never be found in another school's.
                        db.execute(f'SET search_path TO "{schema}"')
                    except BaseException:
                        db.close()
                        raise
                return db
            self._connect = connect
        else:
            data_dir = Path(data_dir).resolve()
            # Fail rather than silently save student records on Railway's temporary disk.
            if os.environ.get("RAILWAY_ENVIRONMENT_ID") and not testing:
                mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                if not mount or (Path(mount).resolve() != data_dir and Path(mount).resolve() not in data_dir.parents):
                    raise RuntimeError("Set DATABASE_URL to your PostgreSQL service's DATABASE_URL "
                                       "(or attach a Railway volume at DATA_DIR).")
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / filename

            def connect():
                db = sqlite3.connect(path, timeout=10)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
                return db
            self._connect = connect

    def sql(self, query):
        return query.replace("?", "%s") if self.postgres else query

    @contextmanager
    def transaction(self):
        db = self._connect()
        try:
            yield _Connection(db, self)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        # The database service can still be starting when the app boots.
        for attempt in range(10):
            try:
                return self._migrate()
            except psycopg.OperationalError:
                if attempt == 9:
                    raise
                time.sleep(3)

    def _migrate(self):
        with self.transaction() as db:
            if self.postgres:
                # Only one process applies migrations at a time.
                db.execute("SELECT pg_advisory_xact_lock(726354)")
            else:
                db.execute("PRAGMA journal_mode=WAL")
            self._apply(db, "schema_migrations", MIGRATIONS, "database")
            if self.control:
                self._apply(db, "control_migrations", CONTROL_MIGRATIONS, "control")

    def _apply(self, db, table, migrations, label):
        db.execute(f"CREATE TABLE IF NOT EXISTS {table} (version INTEGER PRIMARY KEY, applied_at BIGINT NOT NULL)")
        done = {row["version"] for row in db.execute(f"SELECT version FROM {table}").fetchall()}
        where = f" ({self.schema})" if self.schema else ""
        for version, migration in enumerate(migrations, 1):
            if version not in done:
                migration(db.raw, self.postgres)
                db.execute(f"INSERT INTO {table} VALUES (?, ?)", (version, int(time.time())))
                print(f"Applied {label} migration {version}{where}: {migration.__name__.split('_', 2)[-1]}.", flush=True)


class _Connection:
    """Wraps a connection so that queries can use `?` placeholders on both databases."""

    def __init__(self, raw, database):
        self.raw = raw
        self._database = database

    def execute(self, query, params=()):
        return self.raw.execute(self._database.sql(query), params)

    def insert(self, query, params=()):
        """Runs an INSERT and returns the new row's id."""
        if self._database.postgres:
            return self.raw.execute(self._database.sql(query) + " RETURNING id", params).fetchone()["id"]
        return self.raw.execute(query, params).lastrowid
