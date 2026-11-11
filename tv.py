"""The TV dashboard: a read-only screen for the admin area.

A TV can't sign in like staff, so an admin makes it a screen link on the TV
screens page. The link holds a long random token (stored only as a SHA-256 hash)
and shows the dashboard and nothing else: whole-school totals and short names
(first name and surname initial), never student records, and no way to change
anything. An admin can turn a link off at any time. Signed-in staff can open
the same dashboard at /tv.
"""
import json
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, Response, abort, g, jsonify, redirect, request
from markupsafe import escape

import auth
import house_extra
import site_settings

ROOT = Path(__file__).resolve().parent
HOUSES = ("Makkah", "Al-Aqsa", "Madinah")
# Keep in step with TOKEN_POINTS in server.py and TOK in src/page.html.
TOKEN_POINTS = {"tg": 5, "tb": 20, "tr": 50}  # green, blue, red (book completion)
YEAR = re.compile(r"(?:year|yr)\s*(\d+)", re.I)
SEEN_EVERY = 300  # record a screen's last visit at most every 5 minutes

bp = Blueprint("tv", __name__)


def short_name(name):
    """'Aisha Khan' -> 'Aisha K.'"""
    words = name.split()
    if not words:
        return ""
    return words[0] + (" " + words[-1][0].upper() + "." if len(words) > 1 else "")


# A teacher's title in a classroom name ("Yr3-A-QM-Br Ahmed", "Year 3A Quran - Ustadh Tariq").
TITLE = re.compile(r"^(?:brother|sister|br|sr|ust\w*|shaykh|shaikh|sheikh|sh|mufti|maulana|moulana|mr|mrs|ms|miss)\.?\s+", re.I)
GROUP = re.compile(r"\b(?:year|yr)\s*(\d+(?:\s*/\s*\d+)?)", re.I)


# Titles for women teachers; every other title (Br, Ustadh, Ustd...) is written as Ustadh.
FEMALE = re.compile(r"^(?:sister|sr|ustadhah?|ustdh|ustazah?|ustada|mrs|ms|miss)\.?$", re.I)


def teacher_parts(text):
    """The teacher in a classroom name: 'Yr3-A-QM-Br Ahmed' -> ('Ustadh', 'Ahmed'). None if there isn't one."""
    parts = [p.strip() for p in (text or "").split("-")]
    for i, part in enumerate(parts):
        title = TITLE.match(part)
        if title:
            name = TITLE.sub("", "-".join(parts[i:])).split()
            if not name:
                return None
            # A title can also trail the name after a slash: "Ustadha Ruqayyah/Sr".
            first, _, after = name[0].partition("/")
            female = FEMALE.match(title.group(0).strip()) or FEMALE.match(after.strip())
            return ("Ustadha" if female else "Ustadh"), first[:1].upper() + first[1:]
    # No title: a last part that is a name, not the year, the class letter or a subject code (QM, AI),
    # perhaps with its title after a slash ("Ruqayyah/Sr").
    last, _, after = (re.sub(r"^[A-Z]\s+", "", parts[-1]) if len(parts) > 1 else "").partition("/")
    if len(last) >= 3 and not GROUP.search(last) and not last.isupper() and last.isalpha():
        return ("Ustadha" if FEMALE.match(after.strip()) else "Ustadh"), last[:1].upper() + last[1:]
    return None


def teacher_of(text):
    """The teacher with their title in full: 'Yr3-A-QM-Br Ahmed' -> 'Ustadh Ahmed', 'Yr4-B-QM-Sr Zainab' -> 'Ustadha Zainab'."""
    parts = teacher_parts(text)
    return " ".join(parts) if parts else ""


def class_label(class_name):
    """How the TV names a class: year group, class letter and teacher. 'Yr3-A-QM-Br Ahmed' -> 'Yr3A Ustadh Ahmed'."""
    group = GROUP.search(class_name or "")
    if group:
        letter = re.match(r"\s*-?\s*([A-Z])\b", class_name[group.end():])
        year = "Yr" + re.sub(r"\s+", "", group.group(1)) + (letter.group(1) if letter else "")
    else:
        year = "Reception" if re.search(r"\breception\b", class_name or "", re.I) else ""
    label = " ".join(p for p in (year, teacher_of(class_name)) if p)
    return label or class_name


def school_stage(student, class_name):
    """'primary' for Reception to Year 6, 'secondary' for Year 7 onwards, None if unknown.

    The madrasah year group decides, then the classroom name (Yr7-B-...), then the school year group."""
    for text in (student.get("year", ""), class_name, student.get("sy", "")):
        if re.search(r"\breception\b", text or "", re.I):
            return "primary"
        match = YEAR.search(text or "")
        if match:
            return "primary" if int(match.group(1)) <= 6 else "secondary"
    return None


def natural(text):
    """Sorts Yr3 before Yr11."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text.lower())]


def since(times, start):
    return sum(1 for t in times if t >= start)


def between(times, start, end):
    return sum(1 for t in times if start <= t < end)


def podium(entries, key="n", places=3):
    """The top three different totals (or all of them, with places=None), each with everyone on it: ties share a step."""
    steps = []
    for entry in sorted((e for e in entries if e[key]), key=lambda e: (-e[key], natural(e.get("name") or e["class"]))):
        if steps and steps[-1]["n"] == entry[key]:
            steps[-1]["who"].append(entry)
        elif places is None or len(steps) < places:
            steps.append({"place": len(steps) + 1, "n": entry[key], "who": [entry]})
        else:
            break
    return steps


def class_stage(class_name, stages):
    """A class's stage: from its name (Yr7-B, 9/10), otherwise what most of its students are."""
    stage = school_stage({}, class_name)
    if stage or not stages:
        return stage
    return max(("primary", "secondary"), key=stages.count)


def period(now):
    """This month so far: its start in milliseconds, its first day and today ("2026-10-06"), and its finished weeks.

    The dashboard records times in milliseconds, and level tests, Quran scores and stars by day. Stars of the week
    run from the week the month starts in up to the end of last week."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first = day.replace(day=1)
    monday = day - timedelta(days=day.weekday())
    weeks, w = [], first - timedelta(days=first.weekday())
    while w < monday:
        weeks.append(w)
        w += timedelta(days=7)
    return first.timestamp() * 1000, first.strftime("%Y-%m-%d"), day.strftime("%Y-%m-%d"), weeks


def levels_passed(student, month_key, today_key):
    return sum(1 for t in student.get("tests") or [] if t.get("k") == "q" and t.get("ok")
               and month_key <= str(t.get("d", "")) <= today_key)


def last_month(now):
    """Last calendar month: its name, its start and end in milliseconds, and its first and last days ("2026-09-30")."""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (first - timedelta(days=1)).replace(day=1)
    end = first - timedelta(days=1)
    return start.strftime("%B"), start.timestamp() * 1000, first.timestamp() * 1000, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def week_star(students, week):
    """A class's star of the week: the teacher's pick from the Quran tab, otherwise the most passes and merits.

    students is a list of (record, short name, house); the answer is (short name, house) or None."""
    key, start = week.strftime("%Y-%m-%d"), week.timestamp() * 1000
    end = (week + timedelta(days=7)).timestamp() * 1000
    star = next(((n, h) for s, n, h in students if key in (s.get("sw") or [])), None)
    if not star:
        best = max(((between(s.get("log", []), start, end) + between(s.get("ml", []), start, end),
                     between(s.get("log", []), start, end), n, h) for s, n, h in students), default=None)
        star = best and best[0] and (best[2], best[3])
    return star or None


def summary(rows, now=None, teachers=(), extra=None):
    """Everything the TV shows, worked out from the classroom records. extra: house points added by hand for a whole
    house (house_extra.py), which count in the house totals."""
    now = now or datetime.now(auth.LONDON)
    month, month_key, today_key, _ = period(now)
    # The TV shows one star per class on one page: each class's star for last week (the last full week).
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_week = day - timedelta(days=day.weekday() + 7)
    houses = {h: (extra or {}).get(h, 0) for h in HOUSES}
    books, stars, memo, qaidah = [], [], [], []
    # Last month's winners, for the slide that looks back: Qaidah levels, each stage's top memorisation class, and
    # the house that earned the most points that month.
    past_name, past_start, past_end, past_first, past_last = last_month(now)
    past = {"qaidah": [], "primary": [], "secondary": [], "houses": dict.fromkeys(HOUSES, 0)}
    # Every teacher named in the classrooms (Quran and Islamic Studies) as well as those with an account.
    # A title from a classroom name wins; teachers known only by their account are Ustadh unless the name says otherwise.
    names = {}
    for t in teachers:
        title, first = TITLE.match(t), (TITLE.sub("", t).split() or [""])[0]
        if first:
            names.setdefault(first.lower(), ("Ustadha" if title and FEMALE.match(title.group(0).strip()) else None,
                                             first[:1].upper() + first[1:]))
    tops = {kind: {"primary": [], "secondary": []} for kind in ("memo", "qaidah")}
    for row in rows:
        state = json.loads(row["json"])
        class_name = state.get("name") or row["id"]
        label = class_label(class_name)
        for name in [class_name] + [s.get("ist", "") for s in state.get("S", [])]:
            parts = teacher_parts(name)
            if parts and not (names.get(parts[1].lower()) or (None,))[0]:
                names[parts[1].lower()] = parts
        students, stages, class_memo, class_qaidah, class_past = [], [], 0, 0, 0
        for s in state.get("S", []):
            name = short_name(s.get("name", ""))
            if not name:
                continue
            house = s.get("house", "")
            if house in houses:
                houses[house] += sum(len(s.get(k, [])) * v for k, v in TOKEN_POINTS.items())
                past["houses"][house] += sum(between(s.get(k, []), past_start, past_end) * v for k, v in TOKEN_POINTS.items())
            past_levels = levels_passed(s, past_first, past_last)
            if past_levels:
                past["qaidah"].append({"name": name, "class": label, "house": house, "n": past_levels})
            class_past += between(s.get("log", []), past_start, past_end)
            # A book pass from the Record page, or a red token from before house points moved to physical coins.
            if book_passed(s, month):
                books.append({"name": name, "class": label, "house": house})
            passes = since(s.get("log", []), month)
            levels = levels_passed(s, month_key, today_key)
            if passes:
                memo.append({"name": name, "class": label, "house": house, "n": passes})
            if levels:
                qaidah.append({"name": name, "class": label, "house": house, "n": levels})
            class_memo, class_qaidah = class_memo + passes, class_qaidah + levels
            stage = school_stage(s, class_name)
            if stage:
                stages.append(stage)
            students.append((s, name, house))
        stage = class_stage(class_name, stages)
        if stage:
            tops["memo"][stage].append({"class": label, "n": class_memo})
            tops["qaidah"][stage].append({"class": label, "n": class_qaidah})
            past[stage].append({"class": label, "n": class_past})
        star = week_star(students, last_week)
        if star:
            stars.append({"name": star[0], "house": star[1], "class": label, "week": last_week.strftime("%Y-%m-%d"),
                          "label": "Week of " + last_week.strftime("%-d %b")})
    return {
        "houses": sorted(({"house": h, "points": p} for h, p in houses.items()), key=lambda h: -h["points"]),
        "books": sorted(books, key=lambda b: b["name"]),
        "memo": podium(memo, places=None),  # every rank, shown as a list so ties are never cut
        "qaidah": podium(qaidah),
        "classes": {kind: {stage: podium(L) for stage, L in v.items()} for kind, v in tops.items()},
        "stars": sorted(stars, key=lambda s: natural(s["class"])),
        "teachers": [(title or "Ustadh") + " " + name for title, name in sorted(names.values(), key=lambda t: t[1].lower())],
        "month": now.strftime("%B"),
        # First place only (everyone tied on it) in each category; empty lists when nobody scored.
        "last": {"month": past_name, "qaidah": podium(past["qaidah"], places=1),
                 "primary": podium(past["primary"], places=1), "secondary": podium(past["secondary"], places=1),
                 "houses": podium([{"name": h, "house": h, "n": n} for h, n in past["houses"].items()], places=1)},
    }


def book_passed(student, start):
    """Whether the student finished a book since start (ms): a Record page book pass, or an older red token."""
    times = [x.get("t", 0) for x in student.get("bp") or [] if isinstance(x, dict)]
    return bool(since(times, start) or since(student.get("tr", []), start))


QURAN_DAYS = 3  # Quran scores a student needs this month to be on the Quran podium


def class_average(scores):
    return round(sum(scores) / len(scores), 1) if scores else 0


def month_scores(state, now):
    """A class's memorisation passes and its Quran scores this month."""
    month, month_key, today_key, _ = period(now)
    passes, scores = 0, []
    for s in state.get("S", []):
        if not short_name(s.get("name", "")):
            continue
        passes += since(s.get("log", []), month)
        scores += [v for d, v in (s.get("q") or {}).items() if month_key <= d <= today_key and isinstance(v, (int, float)) and v >= 0]
    return passes, class_average(scores)


def school_places(rows, class_id, now=None):
    """Where a class stands in the school this month, among the classes of its own stage (Primary or Secondary Years,
    as on the Rankings page and the TV): by memorisation passes and by Quran average. Classes on the same total share
    a place, and the next takes the next number (1, 1, 2). A class with nothing yet has no place."""
    now = now or datetime.now(auth.LONDON)
    classes = []
    for row in rows:
        state = json.loads(row["json"])
        class_name = state.get("name") or row["id"]
        stage = class_stage(class_name, [x for x in (school_stage(s, class_name) for s in state.get("S", [])) if x])
        classes.append((row["id"], stage) + month_scores(state, now))
    me = next((c for c in classes if c[0] == class_id), None)
    if not me:
        return {}
    group = [c for c in classes if c[1] == me[1]]
    places = {"stage": me[1] or ""}
    for key, i in (("memo", 2), ("quran", 3)):
        if me[i]:
            totals = sorted({c[i] for c in group if c[i]}, reverse=True)
            places[key] = {"place": totals.index(me[i]) + 1, "of": sum(1 for c in group if c[i]), "classes": len(group)}
    return places


def class_summary(row, now=None):
    """What a class's Presenter shows: this month's tops in one class, and its students' names to ride through."""
    now = now or datetime.now(auth.LONDON)
    month, month_key, today_key, weeks = period(now)
    state = json.loads(row["json"])
    class_name = state.get("name") or row["id"]
    tops = {k: [] for k in ("quran", "memo", "qaidah", "points", "merits")}
    houses, books, students = dict.fromkeys(HOUSES, 0), [], []
    earners = {h: [] for h in HOUSES}
    all_scores, days = [], set()
    for s in state.get("S", []):
        name = short_name(s.get("name", ""))
        if not name:
            continue
        house = s.get("house", "")
        entry = {"name": name, "house": house}
        scores = [v for d, v in (s.get("q") or {}).items() if month_key <= d <= today_key and isinstance(v, (int, float)) and v >= 0]
        all_scores += scores
        days.update(d for d, v in (s.get("q") or {}).items() if month_key <= d <= today_key and isinstance(v, (int, float)) and v >= 0)
        points = sum(since(s.get(k, []), month) * v for k, v in TOKEN_POINTS.items())
        if house in houses:
            houses[house] += points
            if points:
                earners[house].append(dict(entry, n=points))
        for key, n in (("quran", round(sum(scores) / len(scores), 1) if len(scores) >= QURAN_DAYS else 0),
                       ("memo", since(s.get("log", []), month)), ("qaidah", levels_passed(s, month_key, today_key)),
                       ("points", points), ("merits", since(s.get("ml", []), month))):
            if n:
                tops[key].append(dict(entry, n=n))
        if book_passed(s, month):
            books.append(entry)
        students.append((s, name, house))
    stars = []
    for week in weeks:
        star = week_star(students, week)
        if star:
            stars.append({"name": star[0], "house": star[1], "week": week.strftime("%Y-%m-%d"),
                          "label": "Week of " + week.strftime("%-d %b")})
    total = {k: sum(e["n"] for e in v) for k, v in tops.items() if k != "quran"}
    return {
        "mode": "presenter",
        "id": row["id"],
        "class": class_label(class_name),
        **{k: podium(v, places=None if k == "memo" else 3) for k, v in tops.items()},
        "houses": podium([{"name": h, "house": h, "n": p} for h, p in houses.items()]),
        # The House race shows who earned the most for each house: up to three, highest first.
        "earners": {h: sorted(e, key=lambda e: (-e["n"], natural(e["name"])))[:3] for h, e in earners.items()},
        "books": sorted(books, key=lambda b: b["name"]),
        "stars": stars,
        # Every student with enough Quran scores, in order (the podium shows the top three).
        "quran_all": podium(tops["quran"], places=None),
        # The class at a glance: only what the class has this month is shown on the slide.
        "glance": {
            "students": len(students),
            "memo": total["memo"], "memo_students": len(tops["memo"]),
            "quran": class_average(all_scores), "quran_days": len(days), "quran_students": len(tops["quran"]),
            "qaidah": total["qaidah"], "points": total["points"], "merits": total["merits"], "books": len(books),
        },
        "riders": sorted({n for _, n, _ in students}),
        "month": now.strftime("%B"),
    }


WINDOW = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def settings_of(row):
    try:
        settings = json.loads(row["settings"] or "{}")
    except (TypeError, ValueError, KeyError, IndexError):
        settings = {}
    return {k: v for k, v in settings.items() if k in ("qaidah", "memo") and isinstance(v, list) and len(v) == 2
            and all(isinstance(t, str) and WINDOW.match(t) for t in v) and v[0] != v[1]}


def focus(settings, now=None):
    """Which kinds of slide a screen plays now: its Qaidah or Memo window (both when they overlap), else everything."""
    t = (now or datetime.now(auth.LONDON)).strftime("%H:%M")
    on = [k for k in ("qaidah", "memo") if k in settings and (
        settings[k][0] <= t < settings[k][1] if settings[k][0] < settings[k][1]
        else t >= settings[k][0] or t < settings[k][1])]  # a window can run past midnight
    return on or None


def data(settings=None):
    with auth.db().transaction() as tx:
        rows = tx.execute("SELECT id, json FROM classrooms").fetchall()
        extra = house_extra.totals(tx)
        # Teachers' names drift through the scenes; summary() adds everyone named in the classrooms.
        teachers = [r["name"] for r in tx.execute(
            "SELECT name FROM users WHERE role IN ('teacher', 'head') AND disabled = 0 ORDER BY name").fetchall()]
    result = summary(rows, teachers=teachers, extra=extra)
    result["focus"] = focus(settings or {})
    return jsonify(result)


def page():
    html = (ROOT / "static" / "tv.html").read_text(encoding="utf-8")
    # The brand in the top left and the tab's name: the site title (site_settings.py), or just Ilmi.
    name = site_settings.title()
    if name:
        html = html.replace("<title>Ilmi · Live</title>", f"<title>{escape(name)} · Live</title>", 1)
        html = html.replace('<h1 id="brand">Ilmi</h1>', f'<h1 id="brand">{escape(name)}</h1>', 1)
    return Response(html, mimetype="text/html")


def screen(token):
    """The screen this token belongs to, if it is still turned on."""
    if len(token) > 100:
        return None
    t = auth.now()
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT id, last_seen_at, settings FROM tv_screens WHERE token_hash = ? AND revoked_at IS NULL",
                         (auth.token_hash(token),)).fetchone()
        if row and t - (row["last_seen_at"] or 0) >= SEEN_EVERY:
            tx.execute("UPDATE tv_screens SET last_seen_at = ? WHERE id = ?", (t, row["id"]))
    return row


@bp.get("/tv")
def staff_page():
    return page()


@bp.get("/api/tv")
def staff_data():
    return data()


def presenter_class(class_id, school=False):
    """The class a signed-in teacher may present: one of their own; an admin or HeadTeacher may present any. With school=True, also
    every classroom, to place the class in the school."""
    with auth.db().transaction() as tx:
        if g.user["role"] not in auth.SEES_ALL and not tx.execute(
                "SELECT 1 FROM classroom_members WHERE class_id = ? AND user_id = ?", (class_id, g.user["id"])).fetchone():
            abort(404)
        row = tx.execute("SELECT id, json FROM classrooms WHERE id = ?", (class_id,)).fetchone()
        rows = tx.execute("SELECT id, json FROM classrooms").fetchall() if school and row else []
    if not row:
        abort(404)
    return (row, rows) if school else row


# --- The teacher's Presenter, opened like a TV screen's link --------------------------------------------------------
# Pressing Presenter on the dashboard opens /presenter/<class> in a new tab, which makes a new random link
# (/present/<token>) for that class and sends the tab there. The first tab to open the link claims it and keeps its own
# key in sessionStorage, so the Presenter keeps working in that tab, signed in or not, until the tab is closed. The
# link doesn't open anywhere else; pressing Presenter again makes a new one. A link nobody opens within LINK_OPEN stops
# working, as does a tab that hasn't fetched the figures for LINK_IDLE (it has been closed, or the computer has slept
# through the night). The link only ever shows the class while whoever made it can still present it.
LINK_OPEN = 10 * 60
LINK_IDLE = 12 * 3600
LINK_ENDED = "This Presenter link has ended. Press Presenter on the dashboard to open a new one."


@bp.get("/presenter/<class_id>")
def presenter_page(class_id):
    presenter_class(class_id)
    token = secrets.token_urlsafe(32)
    with auth.db().transaction() as tx:
        tx.execute("INSERT INTO presenter_links (token_hash, class_id, created_by, created_at) VALUES (?, ?, ?, ?)",
                   (auth.token_hash(token), class_id, g.user["id"], auth.now()))
    return redirect("/present/" + token)


def live_link(tx, token):
    """The Presenter link for this token while it still works, with the user who made it."""
    if len(token) > 100:
        return None
    t = auth.now()
    row = tx.execute("SELECT l.*, u.role, u.disabled FROM presenter_links l JOIN users u ON u.id = l.created_by "
                     "WHERE l.token_hash = ?", (auth.token_hash(token),)).fetchone()
    if not row or row["disabled"] or (row["tab_hash"] is None and row["created_at"] < t - LINK_OPEN) or \
            (row["tab_hash"] is not None and (row["last_seen_at"] or 0) < t - LINK_IDLE):
        return None
    if row["role"] not in auth.SEES_ALL and not tx.execute("SELECT 1 FROM classroom_members WHERE class_id = ? AND user_id = ?",
                                                 (row["class_id"], row["created_by"])).fetchone():
        return None
    return row


def link_tab(tx, token):
    """The link, when this request comes from the tab that opened it (its key in the X-View header)."""
    row = live_link(tx, token)
    key = request.headers.get("X-View", "")
    if not row or not row["tab_hash"] or not key or len(key) > 100 or \
            not secrets.compare_digest(auth.token_hash(key), row["tab_hash"]):
        return None
    return row


@bp.get("/present/<token>")
def link_page(token):
    with auth.db().transaction() as tx:
        row = live_link(tx, token)
    if not row:
        return auth.page("presenter_off.html", 404)
    return page()


@bp.post("/present/<token>/open")
def link_open(token):
    """The first tab to open the link claims it: it gets a key of its own to keep in sessionStorage."""
    with auth.db().transaction() as tx:
        row = live_link(tx, token)
        if not row or row["tab_hash"]:
            return jsonify(error=LINK_ENDED if not row else
                           "This Presenter link has already been opened. It only works in the tab that opened it, so "
                           "press Presenter on the dashboard to open a new one."), 410
        key, t = secrets.token_urlsafe(24), auth.now()
        tx.execute("UPDATE presenter_links SET tab_hash = ?, last_seen_at = ? WHERE token_hash = ? AND tab_hash IS NULL",
                   (auth.token_hash(key), t, row["token_hash"]))
    return jsonify(key=key)


@bp.get("/present/<token>/data")
def link_data(token):
    t = auth.now()
    with auth.db().transaction() as tx:
        row = link_tab(tx, token)
        if not row:
            return jsonify(error=LINK_ENDED), 410
        if t - (row["last_seen_at"] or 0) >= 60:
            tx.execute("UPDATE presenter_links SET last_seen_at = ? WHERE token_hash = ?", (t, row["token_hash"]))
        room = tx.execute("SELECT id, json FROM classrooms WHERE id = ?", (row["class_id"],)).fetchone()
        rows = tx.execute("SELECT id, json FROM classrooms").fetchall()
    result = class_summary(room)
    result["school"] = school_places(rows, room["id"])
    return jsonify(result)


@bp.get("/api/presenter/<class_id>")
def presenter_data(class_id):
    row, rows = presenter_class(class_id, school=True)
    result = class_summary(row)
    result["school"] = school_places(rows, class_id)  # only the class's own places, never another class's figures
    return jsonify(result)


@bp.get("/tv/<token>")
def screen_page(token):
    if not screen(token):
        return auth.page("tv_off.html", 404)
    return page()


@bp.get("/tv/<token>/data")
def screen_data(token):
    row = screen(token)
    if not row:
        return jsonify(error="This screen's link has been turned off."), 404
    return data(settings_of(row))


def screens_page(status=200, **context):
    with auth.db().transaction() as tx:
        screens = [dict(row) for row in tx.execute(
            "SELECT s.id, s.name, s.created_at, s.last_seen_at, s.settings, u.name AS created_by FROM tv_screens s "
            "LEFT JOIN users u ON u.id = s.created_by WHERE s.revoked_at IS NULL ORDER BY s.created_at").fetchall()]
    for s in screens:
        s["windows"] = settings_of(s)
    return auth.page("admin_tv.html", status, screens=screens, **context)


@bp.get("/admin/tv")
@auth.admin_required
def admin_screens():
    return screens_page()


@bp.post("/admin/tv/new")
@auth.admin_required
def admin_new_screen():
    auth.check_form_token()
    name = " ".join(request.form.get("name", "").split())[:80] or "TV"
    token = secrets.token_urlsafe(32)
    with auth.db().transaction() as tx:
        screen_id = tx.insert("INSERT INTO tv_screens (name, token_hash, created_by, created_at) VALUES (?, ?, ?, ?)",
                              (name, auth.token_hash(token), g.user["id"], auth.now()))
        auth.audit(tx, "tv.created", detail={"screen": screen_id, "name": name})
    return screens_page(notice=f"Link made for {name}.", link=auth.public_url("/tv/" + token), link_for=name)


@bp.post("/admin/tv/<int:screen_id>/off")
@auth.admin_required
def admin_screen_off(screen_id):
    auth.check_form_token()
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT name FROM tv_screens WHERE id = ? AND revoked_at IS NULL", (screen_id,)).fetchone()
        if not row:
            abort(404)
        tx.execute("UPDATE tv_screens SET revoked_at = ? WHERE id = ?", (auth.now(), screen_id))
        auth.audit(tx, "tv.revoked", detail={"screen": screen_id, "name": row["name"]})
    return screens_page(notice=f"Turned off the link for {row['name']}. That TV stops showing the dashboard within a minute.")


@bp.post("/admin/tv/<int:screen_id>/settings")
@auth.admin_required
def admin_screen_settings(screen_id):
    """The daily time windows when this screen plays only the Qaidah slides or only the Memo slides."""
    auth.check_form_token()
    windows = {}
    for kind in ("qaidah", "memo"):
        start, end = request.form.get(kind + "_from", "").strip(), request.form.get(kind + "_to", "").strip()
        if not start and not end:
            continue
        if not (WINDOW.match(start) and WINDOW.match(end)) or start == end:
            return screens_page(400, error="Give each time window a start and an end time, or leave both empty.")
        windows[kind] = [start, end]
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT name FROM tv_screens WHERE id = ? AND revoked_at IS NULL", (screen_id,)).fetchone()
        if not row:
            abort(404)
        tx.execute("UPDATE tv_screens SET settings = ? WHERE id = ?", (json.dumps(windows), screen_id))
        auth.audit(tx, "tv.settings", detail={"screen": screen_id, "name": row["name"], **windows})
    return screens_page(notice=f"Saved the settings for {row['name']}. The TV picks them up within a minute.")
