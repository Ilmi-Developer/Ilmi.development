"""Settings for a whole school, one row each in its site_settings table.

"title" is the school's name. The developer sets it when adding or renaming a school (schools.py), and it is shown
only on the developer's school tiles and bar: every page, screen and email of the school itself says just Ilmi.
It is also one of the easy guesses a password may not be (auth.password_problem).
"""
import re

from flask import Blueprint, current_app, g, request

import auth
import schools
from db import DB_ERRORS

bp = Blueprint("site", __name__)

TITLE_MAX = 60
SPACES = re.compile(r"\s+")


def tidy(text):
    """One line of plain text: line breaks and runs of spaces become one space, and invisible characters go."""
    text = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return SPACES.sub(" ", text).strip()


def read(tx, key):
    row = tx.execute("SELECT value FROM site_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def write(tx, key, value):
    if value:
        tx.execute("INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                   "updated_by = excluded.updated_by", (key, value, auth.now(), g.user["id"] if g.get("user") else None))
    else:
        tx.execute("DELETE FROM site_settings WHERE key = ?", (key,))


@bp.app_template_global("site_title")
def title():
    """The current school's site title, or "" when none is set. Read once per request; a page still renders (with
    just Ilmi) when the database can't be reached, such as the error page shown then.

    Signed out, a page belongs to no school and shows just Ilmi: the start page, the sign-in pages and the rest are
    Ilmi's own and never carry a school's name, however many schools there are."""
    if "site_title" not in g:
        try:
            router = current_app.config["DB"]
            database = None if router.current_id() == schools.CONTROL else router.current()
            if database is None:
                g.site_title = ""
            else:
                with database.transaction() as tx:
                    g.site_title = read(tx, "title")
        except DB_ERRORS:
            current_app.logger.warning("Couldn't read the site title; showing just Ilmi.")
            return ""
    return g.site_title
