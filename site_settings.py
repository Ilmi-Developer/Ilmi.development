"""Settings for a whole school, which its Office chooses on Account.

For now there is one: the site title, usually the school's or madrasah's name. It shows under Ilmi in the top left
corner of every page (the side menu, the sign-in pages and the start page), on the TV and in emails. With no title
set, those places show just Ilmi.
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


def problem(text):
    if len(text) > TITLE_MAX:
        return f"Keep the title to {TITLE_MAX} characters or fewer."
    return None


@bp.post("/account/site-title")
@auth.admin_required
def save_title():
    auth.check_form_token()
    text = tidy(request.form.get("title", ""))
    error = problem(text)
    if error:
        return auth.account_page(400, error=error, title_draft=text)
    with auth.db().transaction() as tx:
        before = read(tx, "title")
        if text != before:
            write(tx, "title", text)
            auth.audit(tx, "site.title", detail={"title": text})
    if text and text != before:
        # The developer's list of schools shows each school by this name.
        auth.db().rename(auth.db().current_id(), text, title_too=False)
    g.site_title = text
    notice = (f"Saved. Your school’s pages now show “{text}” under Ilmi in the top left corner." if text
              else "Saved. Pages now show just Ilmi in the top left corner.")
    return auth.account_page(notice=notice)
