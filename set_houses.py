"""One-off: give every student the Student ID, Family ID, house, year group, gender and Arabic and Islamic Studies
class on the school's list.

The list is personal data, so it is never part of the code. An admin uploads it as a CSV on /admin/set-houses and
it is kept in the database (site_settings, key "house_list") until they remove it or upload another.

Students are matched by their name (the Student Name column). The same name can be on the list twice, in two classes.

The page then shows, class by class, how many of these details would change, which students on the dashboard
aren't on the list and who on the list isn't on the dashboard; nothing changes until the admin confirms.
Anyone not on the list keeps their details. The version of each class from just before is kept in Class history, so
it can be restored from there. Remove this file once it has been used.
"""
import csv
import io
import json
import re
import time

from flask import Blueprint, current_app, g, request

import auth
import site_settings

KEY = "house_list"
HOUSES = ("Makkah", "Al-Aqsa", "Madinah")
FIELDS = ("sid", "fid", "house", "sy", "gender", "ist")
COLUMNS = ("Student Name", "QM", "AI", "Student ID", "Family ID", "House", "Year Group", "Gender")
# Lists made before the columns were renamed call two of them by older names; those still work.
OLDER = {"Student Name": "MySafar Name", "Student ID": "MySafar ID"}

bp = Blueprint("set_houses", __name__)


def sid_key(sid):
    """SE0264 and se264 are the same ID."""
    m = re.fullmatch(r"\s*([A-Za-z]*)\s*0*(\d+)\s*", str(sid or ""))
    return (m.group(1).upper() + m.group(2)) if m else str(sid or "").strip().upper()


def name_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def class_key(name):
    """Yr3-A-QM-Br Tariq, "Yr3A Ustadh Tariq" and "Year 3 A" are all class 3a, so the class's name on the
    dashboard needn't match the list's QM column letter for letter."""
    m = re.search(r"\b(?:yr|year)\s*(\d+(?:\s*/\s*\d+)?)\s*-?\s*([a-z])(?![a-z])", str(name or ""), re.I)
    return (re.sub(r"\s", "", m.group(1)) + m.group(2).lower()) if m else name_key(name)


def parse(text):
    """Rows of an uploaded list: {sid, name, cls, fid, house, sy, gender, ist}. ValueError says what's wrong with it."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
    cols = [c.strip() for c in reader.fieldnames or []]
    cols = [next((new for new, old in OLDER.items() if c == old and new not in cols), c) for c in cols]
    reader.fieldnames = cols
    lacking = [c for c in ("Student Name", "QM", "Family ID", "House") if c not in cols]
    if lacking:
        raise ValueError("That CSV has no " + ", ".join(lacking) + " column. It needs these columns: "
                         + ", ".join(COLUMNS) + ".")
    out = []
    for n, row in enumerate(reader, start=2):
        row = {k: (v or "") for k, v in row.items() if isinstance(k, str)}
        name = " ".join(row["Student Name"].split())
        if not name and not any(str(v).strip() for v in row.values()):
            continue
        house = row["House"].strip()
        if not name:
            raise ValueError(f"Row {n} has no Student Name.")
        if house not in HOUSES:
            raise ValueError(f"Row {n} ({name}) has the house “{house}”. It must be one of " + ", ".join(HOUSES) + ".")
        out.append({"sid": (row.get("Student ID") or "").strip(),
                    "name": name, "cls": class_key(row.get("QM")),
                    "fid": row["Family ID"].strip(), "house": house, "sy": " ".join(row.get("Year Group", "").split()),
                    "gender": row.get("Gender", "").strip(), "ist": " ".join(row.get("AI", "").split())})
    if not out:
        raise ValueError("That CSV has no students in it.")
    return out


def wanted(db):
    """The uploaded list's rows (see parse), or [] when no list has been uploaded."""
    saved = site_settings.read(db, KEY)
    return json.loads(saved) if saved else []


def _set(s, field, value, changes, j):
    """Sets a detail as a change on the student's profile would: remembered in s.ed so a CSV upload leaves it,
    and a replaced Student ID kept in s.xid so the upload still finds the student."""
    old = s.get(field, "")
    if field == "sid" and sid_key(old) == sid_key(value) or field != "sid" and old == value:
        return
    if field == "sid" and old:
        s["xid"] = [x for x in s.get("xid", []) if sid_key(x) != sid_key(old)][-9:] + [old]
    s[field] = value
    s["ed"] = [x for x in s.get("ed", []) if x != field] + [field]
    changes.append((j, field, old, value))


def plan(db):
    """([(class_id, revision, class name, new state, changes [(student's place in the class, field, old, new)], not on the list)],
    students on the list that match nobody on the dashboard, names listed more than once).

    Students are matched by name, so a Student ID that differs from the list shows up as a change rather than
    giving the student someone else's details. A name listed twice is told apart by class, or else by
    Student ID. Each student then gets the list's details (FIELDS)."""
    want = wanted(db)
    by_name = {}
    for i, r in enumerate(want):
        by_name.setdefault(name_key(r["name"]), []).append(i)
    used, out = set(), []
    for row in db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall():
        state = json.loads(row["json"])
        changes, extra = [], []
        for j, s in enumerate(state.get("S", [])):
            n = name_key(s.get("name"))
            fits = [i for i in by_name.get(n, []) if i not in used]
            if len(fits) > 1:
                # The same name in two classes (Adam Gh): told apart by class, or else by Student ID.
                fits = [i for i in fits if want[i]["cls"] == class_key(state.get("name"))] or \
                    [i for i in fits if sid_key(want[i]["sid"]) == sid_key(s.get("sid"))]
            if len(fits) != 1:
                extra.append(s.get("name", "") + (f" ({s['sid']})" if s.get("sid") else ""))
                continue
            r = want[fits[0]]
            used.add(fits[0])
            for field in FIELDS:
                if r[field]:
                    _set(s, field, r[field], changes, j)
        out.append((row["id"], row["revision"], state.get("name", row["id"]), state, changes, extra))
    missing = [f"{r['sid']} {r['name']}" for i, r in enumerate(want) if i not in used]
    return out, missing, sorted({want[v[0]]["name"] for v in by_name.values() if len(v) > 1})


def _page(status=200, **context):
    with current_app.config["DB"].transaction() as db:
        listed = len(wanted(db))
        rows, missing, twice = plan(db)
    ids = [f"{state['S'][j].get('name', '')}: {old or 'none'} to {new}" for _, _, _, state, ch, _ in rows
           for j, field, old, new in ch if field == "sid"]
    classes = [{"name": n, "extra": ex, **{f: sum(1 for c in ch if c[1] == f) for f in FIELDS}}
               for _, _, n, _, ch, ex in rows]
    counts = dict.fromkeys(HOUSES, 0)
    for _, _, _, state, _, _ in rows:
        for s in state.get("S", []):
            if s.get("house") in counts:
                counts[s["house"]] += 1
    return auth.page("admin_houses.html", status, ids=ids, classes=classes, missing=missing, twice=twice, counts=counts,
                     total=sum(len(ch) for _, _, _, _, ch, _ in rows), listed=listed, **context)


@bp.get("/admin/set-houses")
@auth.admin_required
def preview():
    return _page()


@bp.post("/admin/set-houses/list")
@auth.admin_required
def upload():
    """Keeps the chosen CSV as the school's list, in place of any earlier one, or removes the list."""
    auth.check_form_token()
    if request.form.get("remove"):
        with current_app.config["DB"].transaction() as db:
            if site_settings.read(db, KEY):
                site_settings.write(db, KEY, "")
                auth.audit(db, "houses.list_removed")
        return _page(notice="The list has been removed from the database.")
    f = request.files.get("csv")
    data = f.read() if f else b""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    try:
        rows = parse(text)
    except (ValueError, csv.Error) as e:
        return _page(400, error=str(e))
    with current_app.config["DB"].transaction() as db:
        site_settings.write(db, KEY, json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        auth.audit(db, "houses.list_uploaded", detail={"students": len(rows)})
    return _page(notice=f"Saved a list of {len(rows)} students. Check the changes below before confirming.")


@bp.post("/admin/set-houses")
@auth.admin_required
def apply():
    auth.check_form_token()
    if request.form.get("confirm", "").strip().lower() != "houses":
        return _page(error="Type HOUSES in the box to confirm.")
    t = int(time.time())
    with current_app.config["DB"].transaction() as db:
        done = 0
        for class_id, revision, _, state, changes, _ in plan(db)[0]:
            if not changes:
                continue
            current = db.execute("SELECT json FROM classrooms WHERE id = ?", (class_id,)).fetchone()["json"]
            encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            # Keep the version from just before in Class history, then the new one.
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision, current, g.user["id"], t))
            db.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (encoded, revision + 1, class_id))
            db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                       (class_id, revision + 1, encoded, g.user["id"], t))
            auth.audit(db, "class.houses_set", class_id, {"students": len(changes)})
            done += len({c[0] for c in changes})
    return _page(done=done)
