"""What each school has chosen for itself: its houses, which parts of Ilmi it uses, its menu links and its default look.

Ilmi began as one madrasah's dashboard, so that madrasah's choices were written into the code: three houses called
Makkah, Al-Aqsa and Madinah, house points from counted coins, a Finance account, the hall TV, and two links in the
menu. Each of those is now a choice a school's admin makes in School setup (setup.py), kept as one JSON value in the
school's own site_settings ("config"). A school that has never saved anything gets DEFAULTS, which are exactly how
Ilmi behaved before, so nothing changes for a school until its admin changes it.

    houses_on      whether students belong to houses at all
    houses         the houses' names, in order (2 to 6). A student's record holds their house's name.
    house_points   whether house points are counted (needs houses_on)
    modules        parts of Ilmi a school can leave out: finance, presenter, volunteers, assistant
    links          the links at the foot of the menu: [{"label": ..., "url": ...}], at most MAX_LINKS
    theme          the look new staff accounts start with (a built-in theme's key, themes.py)

get() reads the current school's choices once per request. houses() and house_points_on() are what the rest of the
code asks: with houses off there are no houses, and with no houses there are no house points.
"""
import json
import re
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, has_request_context

import schools
import site_settings
from db import DB_ERRORS

bp = Blueprint("school_config", __name__)

DEFAULT_HOUSES = ("Makkah", "Al-Aqsa", "Madinah")
# The first school's own links, from before links were a choice; every other school starts with none.
FIRST_LINKS = ({"label": "Journey2Jannah", "url": "https://journey2jannah.com"},
               {"label": "FlickLearning", "url": "https://flickpermanentmakeupacademy.com/login/"})
MODULES = {
    "finance": ("Finance", "Fees, income and expenses, staff payments, donations and financial reports, with a "
                           "Finance account for whoever keeps the books."),
    "presenter": ("Presenter and TV screens", "The hall TV display and each class’s Presenter: tops, rankings and "
                                              "celebrations on a big screen."),
    "volunteers": ("Volunteer requests", "A button for teachers to ask the office for a volunteer in their class."),
    "assistant": ("Ask Ilmi", "The built-in assistant that answers staff questions about using Ilmi. It only shows "
                              "when the assistant is switched on for the whole site."),
}
DEFAULTS = {"houses_on": True, "houses": list(DEFAULT_HOUSES), "house_points": True,
            "modules": dict.fromkeys(MODULES, True), "links": [], "theme": "light"}
MIN_HOUSES, MAX_HOUSES, HOUSE_NAME_MAX = 2, 6, 24
MAX_LINKS, LINK_LABEL_MAX, LINK_URL_MAX = 6, 30, 300
# Letters (any script), digits, spaces, hyphens and apostrophes: a house's name is also used as a CSS class.
# A link's host: a real domain name, with an optional port. No sign-in details, and nothing that isn't a name.
HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?::\d{1,5})?")
HOUSE_NAME = re.compile(r"[^\W_][\w '’\-]*", re.UNICODE)


def tidy_houses(names):
    """The names as they will be kept, or raises ValueError saying what is wrong with them."""
    out = []
    for raw in names:
        name = site_settings.tidy(raw)
        if not name:
            continue
        if len(name) > HOUSE_NAME_MAX or not HOUSE_NAME.fullmatch(name):
            raise ValueError(f"“{name[:40]}” can’t be a house name. Use up to {HOUSE_NAME_MAX} letters, numbers, "
                             "spaces, hyphens and apostrophes, starting with a letter or number.")
        if name.casefold() in (n.casefold() for n in out):
            raise ValueError(f"Two houses are both called {name}. Give each house its own name.")
        out.append(name)
    if not MIN_HOUSES <= len(out) <= MAX_HOUSES:
        raise ValueError(f"A school has between {MIN_HOUSES} and {MAX_HOUSES} houses. To have none, turn houses off.")
    return out


def tidy_links(pairs):
    """[(label, url)] as they will be kept, or raises ValueError. Rows with neither are dropped."""
    out = []
    for label, url in pairs:
        label, url = site_settings.tidy(label), (url or "").strip()
        if not label and not url:
            continue
        if url and "://" not in url:
            url = "https://" + url
        parts = urlsplit(url)
        if not label or len(label) > LINK_LABEL_MAX:
            raise ValueError(f"Give each link a name of {LINK_LABEL_MAX} characters or fewer.")
        if parts.scheme not in ("https", "http") or not HOST.fullmatch(parts.netloc) or len(url) > LINK_URL_MAX or \
                any(ch.isspace() or ch in "\"'<>" for ch in url):
            raise ValueError(f"“{label}” needs a web address, like https://example.org.")
        out.append({"label": label, "url": url})
    if len(out) > MAX_LINKS:
        raise ValueError(f"The menu holds at most {MAX_LINKS} links.")
    return out


def clean(raw, school_id=None):
    """A stored (or missing) config made whole: every key present and of the right kind."""
    config = json.loads(json.dumps(DEFAULTS))
    if school_id == schools.FIRST:
        config["links"] = [dict(link) for link in FIRST_LINKS]
    if not isinstance(raw, dict):
        return config
    if isinstance(raw.get("houses_on"), bool):
        config["houses_on"] = raw["houses_on"]
    if isinstance(raw.get("house_points"), bool):
        config["house_points"] = raw["house_points"]
    try:
        config["houses"] = tidy_houses(raw["houses"])
    except (KeyError, TypeError, ValueError):
        pass
    if isinstance(raw.get("modules"), dict):
        for key in MODULES:
            if isinstance(raw["modules"].get(key), bool):
                config["modules"][key] = raw["modules"][key]
    if isinstance(raw.get("links"), list):
        try:
            config["links"] = tidy_links([(l.get("label"), l.get("url")) for l in raw["links"] if isinstance(l, dict)])
        except ValueError:
            pass
    if isinstance(raw.get("theme"), str) and re.fullmatch(r"[a-z]{1,20}", raw["theme"]):
        config["theme"] = raw["theme"]
    return config


def read(tx, school_id=None):
    text = site_settings.read(tx, "config")
    try:
        raw = json.loads(text) if text else None
    except ValueError:
        raw = None
    return clean(raw, school_id)


def write(tx, config):
    site_settings.write(tx, "config", json.dumps(config, separators=(",", ":")))


def get():
    """The current school's choices, read once per request. Outside a school (signed out, or the developer's own
    pages) and when the database can't be read, the defaults."""
    router = current_app.config["DB"]
    school_id = router.current_id()
    cached = g.get("school_config")
    if cached and cached[0] == school_id:
        return cached[1]
    if school_id == schools.CONTROL:
        config = clean(None)
    else:
        try:
            with router.transaction() as tx:
                config = read(tx, school_id)
        except DB_ERRORS:
            current_app.logger.warning("Couldn't read the school's setup; using the defaults.")
            return clean(None, school_id)
    g.school_config = (school_id, config)
    return config


def forget():
    g.pop("school_config", None)


def names():
    """Every house name a student's record may hold: the school's houses, whether or not houses are switched on.
    Ilmi's own three outside a request (the tests and the command line)."""
    if not has_request_context():
        return DEFAULT_HOUSES
    return tuple(get()["houses"])


def houses(config=None):
    """The school's houses, in order; none when houses are off."""
    if config is None and not has_request_context():
        return DEFAULT_HOUSES
    config = config or get()
    return tuple(config["houses"]) if config["houses_on"] else ()


def house_points_on(config=None):
    config = config or get()
    return bool(config["houses_on"] and config["house_points"])


def module_on(key, config=None):
    return bool((config or get())["modules"].get(key, True))


@bp.app_template_global("school_cfg")
def template_config():
    """For templates: the choices plus the two answers most pages want."""
    config = get()
    return dict(config, house_names=houses(config), points_on=house_points_on(config))
