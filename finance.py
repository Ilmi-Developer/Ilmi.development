"""Finance: the page a Finance account signs in to.

A Finance account (role "finance", given on Manage users) belongs to one school, like every account. It has its own
page here, its Account, and nothing else: no classes, students, records or Administration pages (server.py keeps it
to FINANCE_PATHS). The page is empty for now. When finance features are added they go on it, built from the same
parts as the rest of Ilmi: the page band (.band), cards (.mod) with their titles, figures (.figs), tables in a
rounded panel (.tablewrap) and pop-ups (.invite), in the shared tokens (src/tokens.css, DESIGN.md).
"""
from flask import Blueprint, abort, g

import auth

bp = Blueprint("finance", __name__)

# Where a Finance account may go. Everything else sends it back to its page (or answers 403 for a form or the API).
FINANCE_PATHS = ("/finance", "/account", "/logout", "/avatar/", "/theme.css", "/theme-photo/", "/developer.css")


@bp.get("/finance")
def page():
    if not g.user or g.user["role"] != "finance":
        abort(403)
    return auth.page("finance.html")
