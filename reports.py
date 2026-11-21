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

Teachers' own grades (overrides) are kept on each student in the class record (server.valid_student, "ro"), apart
from the calculated grades, which are never stored: they are worked out again whenever the tab is drawn.
"""
import json
import re
from datetime import date

from flask import Blueprint, current_app, g, jsonify, request

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
    for key, low, high in (("expected", 1, 100), ("test_points", 0, 20), ("pass_points", 0, 20)):
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
