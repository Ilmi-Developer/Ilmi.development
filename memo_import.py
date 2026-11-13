"""Admin Import page (/admin/import): CSVs that become the students' whole record of one kind of pass.

Memorisation passes and Qaidah/Tajweed level passes each have a CSV with a Date (dd/mm/yyyy hh:mm:ss, London time; a
date alone counts as midday), a Student ID (SE0001; a Name instead when a row has no ID) and a Target or Level, and
an optional Score (0 to 5) for level passes. Students are matched by their Student ID, or an earlier one (s.xid).
A name two students share needs a choice on the page.

Memo: written as the Record page's "Item number and Surah" list writes it: "L1 Target 1", "212. Surah Al-Mulk" or
"Fail". First every student's memo passes, fails and Memo level tests are cleared (a passed Memo level test's blue
coin stays, as a level completion). Each pass is then added as the Memo profile's "Add pass" adds one: a dated pass in
s.log, s.lt and s.lx, with no token or house points. The target becomes the one after the highest book target passed
on the sheet (an earlier target passed later is likely a revision target); a student not on the sheet keeps theirs.
A surah pass keeps its label in s.lx and leaves the target where it is, as on the Record page; a few surahs written
by hand are read as the nearest item on the list (WRITTEN).
A Fail is added to s.fl.

Qaidah: "Level 3", "Qaidah 3" or "3", or "Fail". First every student's Quran level tests are cleared (blue coins
stay, as for Memo). Each pass becomes a passed Quran level test on its day, as the Record page keeps one, with no
token or house points; a Fail is a test not passed at the level after the one they had then. The Qaidah level becomes
the latest level passed on the sheet; a student not on the sheet keeps theirs.

The same target or level twice for a student on the sheet is added once. Importing the same sheet again gives the
same result, as it clears first. The admin chooses the CSV, then sees what would be cleared, which rows match, which
don't and which need a choice; the sheet rides along in the form, and nothing changes until they type IMPORT. The
version of each class from just before is kept in Class history.
"""
import csv
import io
import json
import re
import time
from datetime import datetime

from flask import Blueprint, Response, current_app, g, request

import auth
from set_houses import name_key, sid_key

QAIDAH_LEVELS = 20  # keep in step with QAIDAH in src/page.html and QAIDAH_LEVELS in server.py
SURAHS = ("197. Surah An-Naba'", "198. Surah An-Nazi'aat", "199. Surah Abasa", "200. Surah At-Takwir",
          "201. Surah Al-Infitaar", "202. Surah Al-Mutaffifin", "203. Surah Al-Inshiqaq", "204. Surah Al-Burooj",
          "205. Surah At-Tariq", "206. Surah Al-A'la", "207. Surah Al-Ghaashiyah", "208. Surah Al-Fajr",
          "209. Surah Al-Balad", "210. Surah Ash-Shams", "211. Surah Al-Lail", "212. Surah Al-Mulk",
          "213. Surah-Al Qalam", "214. Surah Al-Haaqqa", "215. Al-Maarij", "216. Surah Nooh", "217. Surah Al-Jinn",
          "218. Surah Al-Muzzammil", "219. Surah Al-Muddaththir", "220. Surah Al-Qiyama", "221. Surah Al-Insan",
          "222. Surah Al-Mursalat", "223. Last 10 verses of Surah Kahf", "224. Surah Ya-Sin", "225. Dahhar v1-12",
          "226. Surah Dahhar v13-22", "228. Surah Mursalaat 1-23", "229. Surah Mursalaat 24-40",
          "230. Surah Mursalaat 41-50")  # keep in step with SUR in src/page.html
# Surahs written by hand on the sheet, and the label each pass gets.
WRITTEN = {"surah qalam page 1": "213. Surah-Al Qalam, page 1", "qalam page 2": "213. Surah-Al Qalam, page 2",
           "surah haque - page 1": "214. Surah Al-Haaqqa, page 1", "haaqah 2nd half": "214. Surah Al-Haaqqa, 2nd half"}
BOOK_TARGET = re.compile(r"L(\d+)\s*Target\s*(\d+)", re.I)

bp = Blueprint("memo_import", __name__)

DATES = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
ID_COLUMNS = ("student id", "id", "se", "se id", "se number", "student number")
# Each kind: its page title, the column that says what was passed, and what clearing takes away.
KINDS = {"memo": {"title": "Memorisation passes", "what": ("target",), "what_name": "Target"},
         "qaidah": {"title": "Qaidah/Tajweed level passes", "what": ("level", "target"), "what_name": "Level"}}


def rows(text, kind):
    """The sheet's rows: {n: row number on the sheet, t: time in ms, day, sid, name, target, score}. A sheet that
    can't be read raises ValueError with what is wrong, in words for the page."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    columns = {(c or "").strip().lower(): c for c in reader.fieldnames or []}
    what = next((columns[c] for c in KINDS[kind]["what"] if c in columns), None)
    ident = next((columns[c] for c in ID_COLUMNS if c in columns), None)
    if "date" not in columns or not what or not (ident or "name" in columns):
        raise ValueError(f"The sheet needs columns called Date, Student ID and {KINDS[kind]['what_name']}.")
    out = []
    for n, row in enumerate(reader, start=2):
        get = lambda col: " ".join(((row.get(col) if col else "") or "").split())  # noqa: E731
        date, sid, name, target, score = (get(columns.get("date")), get(ident), get(columns.get("name")), get(what),
                                          get(columns.get("score")))
        if not (date or sid or name or target):
            continue
        for form in DATES:
            try:
                when = datetime.strptime(date, form)
                break
            except ValueError:
                pass
        else:
            raise ValueError(f"Row {n}: \"{date}\" isn't a date like 12/09/2026 10:36:25.")
        if not (sid or name) or not target:
            raise ValueError(f"Row {n} has no {'Student ID' if not (sid or name) else KINDS[kind]['what_name'].lower()}.")
        if score and not (score.isdigit() and 0 <= int(score) <= 5):
            raise ValueError(f"Row {n}: the score \"{score}\" isn't 0 to 5.")
        # A date with no time counts as midday, as the Memo profile's Add pass does.
        when = (when.replace(hour=12) if len(date) <= 10 else when).replace(tzinfo=auth.LONDON)
        out.append({"n": n, "t": int(when.timestamp() * 1000), "day": when.strftime("%d %b %Y"), "date": when.strftime("%Y-%m-%d"),
                    "sid": sid, "name": name, "target": target, "score": int(score) if score else None})
    if not out:
        raise ValueError("The sheet has no rows.")
    return out


def read_target(text, levels):
    """("book", target number) | ("surah", label) | ("fail", None) | (None, why it can't be read)."""
    if text.lower() == "fail":
        return "fail", None
    m = BOOK_TARGET.fullmatch(text)
    if m:
        level, target = int(m.group(1)), int(m.group(2))
        if not 1 <= level <= len(levels) or not 1 <= target <= levels[level - 1]:
            return None, "Not on the class's list of targets"
        return "book", sum(levels[:level - 1]) + target
    if text in SURAHS:
        return "surah", text
    if text.lower() in WRITTEN:
        return "surah", WRITTEN[text.lower()]
    return None, "Not an item on the school's list"


def read_level(text):
    """("level", 1 to QAIDAH_LEVELS) | ("fail", None) | (None, why it can't be read)."""
    if text.lower() == "fail":
        return "fail", None
    numbers = re.findall(r"\d+", text)
    if len(numbers) == 1 and 1 <= int(numbers[0]) <= QAIDAH_LEVELS:
        return "level", int(numbers[0])
    return None, f"Not a level from 1 to {QAIDAH_LEVELS}"


def keep_coins(s, tests):
    """A passed level test's blue coin stays when the test goes, kept in s.hp as a level completion so the House
    points page still traces it."""
    for t in tests:
        if t.get("tk"):
            s["hp"] = s.get("hp", []) + [{"t": t["tk"], "r": "lv"}]


def clear(s, kind):
    """Takes away the student's record of this kind and returns how much went, as (passes, fails, tests)."""
    tests = [t for t in s.get("tests", []) if t.get("k") == ("m" if kind == "memo" else "q")]
    keep_coins(s, tests)
    s["tests"] = [t for t in s.get("tests", []) if t not in tests]
    if kind == "qaidah":
        return 0, 0, len(tests)
    gone = (len(s.get("log", [])), len(s.get("fl", [])), len(tests))
    s.update(log=[], lt=[], lx=[], fl=[], passes=0, hit=None)
    return gone


def add_memo(r, cls, s, latest, key):
    kind, value = read_target(r["target"], cls[3].get("L") or [])
    if kind is None:
        r.update(status="bad", note=value)
        return
    if kind == "fail":
        s["fl"] = sorted(s["fl"] + [r["t"]])
        r["status"] = "fail"
        return
    lt, lx = (value, "") if kind == "book" else (s.get("target", 1), value)
    r["label"] = value if kind == "surah" else r["target"]
    if r["label"] != r["target"]:
        r["note"] = f"Written as \"{r['target']}\" on the sheet"
    if any(s["lx"][k] == lx if lx else not s["lx"][k] and s["lt"][k] == lt for k in range(len(s["log"]))):
        r["status"] = "repeat"
        return
    if kind == "book" and lt < max([s["lt"][k] for k in range(len(s["log"])) if not s["lx"][k]] or [0]):
        r["note"] = "Earlier in the book than a target they had already passed: possibly a revision target"
    merged = sorted(zip(s["log"] + [r["t"]], s["lt"] + [lt], s["lx"] + [lx]), key=lambda x: (x[0], x[1]))
    s["log"], s["lt"], s["lx"] = (list(x) for x in zip(*merged))
    s["passes"] += 1
    s["hit"] = s["log"][-1]
    if kind == "book" and lt > latest.get(key, 0):
        # The one after the highest book target passed on the sheet: an earlier target passed later is likely a
        # revision target, which doesn't send the student back in the book.
        latest[key] = lt
        s["target"] = min(lt + 1, sum(cls[3]["L"]) + cls[3].get("Q", 0) + 1)


def add_qaidah(r, events):
    kind, value = read_level(r["target"])
    if kind is None:
        r.update(status="bad", note=value)
        return
    if kind == "fail":
        r["status"] = "fail"
    else:
        r["label"] = f"{'Tajweed' if value > 13 else 'Qaidah'} Level {value}"
        if any(n == value for _, n, _, _ in events):
            r["status"] = "repeat"
            return
        if value < max([n for _, n, _, _ in events if n] or [0]):
            r["note"] = "Lower than a level they had already passed"
    events.append((r["t"], value, r["score"], r["date"]))


def set_qaidah(s, events):
    """Rebuilds the student's Quran level tests from the sheet, in date order, and their Qaidah level."""
    last, tests = 0, []
    for _, n, score, day in sorted(events, key=lambda e: e[0]):
        t = {"d": day, "k": "q", "n": n or min(last + 1, QAIDAH_LEVELS), "ok": bool(n)}
        if n:
            t["pq"], last = last, n
        if score is not None:
            t["sc"] = score
        tests.append(t)
    s["tests"] = sorted(s.get("tests", []) + tests, key=lambda t: t["d"])
    if last:
        s["qa"] = last


def plan(db, kind, text, picks=None):
    """([(class_id, revision, class name, new state, number of changes)], report rows, choices for shared names, removed).

    Every student's record of this kind is cleared first (clear), so the sheet becomes the whole record; removed
    counts it as {passes, fails, tests, students}. Each report row is the sheet row with "status" (add, fail,
    repeat, missing, choose, bad), "cls", "label" and "note". picks maps a shared name's key to the chosen
    "class_id|place in the class"."""
    picks = picks or {}
    classes, by_id, by_name = [], {}, {}
    removed = dict.fromkeys(("passes", "fails", "tests", "students"), 0)
    for row in db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall():
        state = json.loads(row["json"])
        classes.append([row["id"], row["revision"], state.get("name", row["id"]), state, 0])
        for j, s in enumerate(state.get("S", [])):
            for sid in {s.get("sid", "")} | set(s.get("xid", [])):
                if sid:
                    by_id.setdefault(sid_key(sid), []).append((len(classes) - 1, j))
            for name in {s.get("name", "")} | set(s.get("xn", [])):
                by_name.setdefault(name_key(name), []).append((len(classes) - 1, j))
            gone = clear(s, kind)
            if any(gone):
                classes[-1][4] += 1
                removed["students"] += 1
                for k, n in zip(("passes", "fails", "tests"), gone):
                    removed[k] += n
    for found in (by_id, by_name):
        for key in found:
            found[key] = list(dict.fromkeys(found[key]))
    report, choices, latest, events = [], {}, {}, {}
    for r in rows(text, kind):
        r.update(status="add", cls="", label=r["target"], note="")
        report.append(r)
        if r["sid"]:
            who = by_id.get(sid_key(r["sid"]), [])[:1]
            if not who:
                r.update(status="missing", note="No student on the site has this Student ID")
                continue
        else:
            key = name_key(r["name"])
            who = by_name.get(key, [])
            if len(who) > 1:
                choices[key] = {"name": r["name"], "options": [(f"{classes[c][0]}|{j}", classes[c][3]["S"][j].get("name", "") + " in "
                                                                + classes[c][2] + (f" ({classes[c][3]['S'][j]['sid']})" if classes[c][3]["S"][j].get("sid") else ""))
                                                               for c, j in who], "pick": picks.get(key, "")}
                who = [(c, j) for c, j in who if f"{classes[c][0]}|{j}" == picks.get(key)]
            if not who:
                r.update(status="choose" if key in choices else "missing",
                         note="Two students share this name, choose one below" if key in choices else "No student on the site has this name")
                continue
        c, j = who[0]
        cls, s = classes[c], classes[c][3]["S"][j]
        r.update(cls=cls[2], name=s.get("name", "") or r["name"])
        if kind == "memo":
            add_memo(r, cls, s, latest, (c, j))
        else:
            add_qaidah(r, events.setdefault((c, j), []))
        if r["status"] in ("add", "fail"):
            cls[4] += 1
    for (c, j), found in events.items():
        set_qaidah(classes[c][3]["S"][j], found)
    return [tuple(c) for c in classes if c[4]], report, list(choices.items()), removed


def log_summary(db, why):
    """Prints, for the Railway logs, how many memo passes, fails and Qaidah level tests each class holds now, and
    when memo passes were last imported. Counts only, no names. Never raises: it is a check, not part of a save."""
    try:
        lines, totals = [], [0, 0, 0, 0]
        for row in db.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall():
            students = json.loads(row["json"]).get("S", [])
            counts = (len(students), sum(len(s.get("log", [])) for s in students),
                      sum(len(s.get("fl", [])) for s in students),
                      sum(1 for s in students for t in s.get("tests", []) if t.get("k") == "q"))
            totals = [a + b for a, b in zip(totals, counts)]
            lines.append(f"  {row['id']} (revision {row['revision']}): {counts[0]} students, {counts[1]} memo passes, "
                         f"{counts[2]} fails, {counts[3]} Qaidah tests")
        last = db.execute("SELECT at, detail FROM audit_log WHERE action = 'class.memo_passes_imported' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        when = datetime.fromtimestamp(last["at"] / (1000 if last["at"] > 10**11 else 1), auth.LONDON).strftime(
            "%d %b %Y %H:%M") if last else "never"
        print(f"[data check] {why}: {len(lines)} classes, {totals[0]} students, {totals[1]} memo passes, "
              f"{totals[2]} fails, {totals[3]} Qaidah tests. Memo passes last imported: {when}.\n" + "\n".join(lines),
              flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[data check] {why}: couldn't count ({e})", flush=True)


ORDER = ("missing", "choose", "bad", "repeat", "fail", "add")


def _page(kind=None, text=None, picks=None, **context):
    if not text:
        return auth.page("admin_import.html", kinds=KINDS, active=kind, sheet=None, **context)
    try:
        with current_app.config["DB"].transaction() as db:
            _, report, choices, removed = plan(db, kind, text, picks)
    except ValueError as e:
        return auth.page("admin_import.html", kinds=KINDS, active=kind, sheet=None, error=str(e))
    counts = {k: sum(1 for r in report if r["status"] == k) for k in ORDER}
    adding = [r for r in report if r["status"] in ("add", "fail")]
    return auth.page("admin_import.html", kinds=KINDS, active=kind, sheet=text, counts=counts, total=len(report),
                     problems=[r for r in report if r["status"] in ("missing", "choose", "bad")],
                     skipped=[r for r in report if r["status"] == "repeat"],
                     noted=[r for r in adding if r["note"]], adding=adding, choices=choices, removed=removed,
                     students=len({(r["cls"], r["name"]) for r in adding}), **context)


def _picks():
    return {k[5:]: v for k, v in request.form.items() if k.startswith("pick-") and v}


def _uploaded():
    """The text of the CSV chosen on the page, or ""."""
    f = request.files.get("csv")
    data = f.read() if f else b""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# Blank CSVs to fill in and upload: the column names each import reads, in the order the school's sheets have them.
# Students is the dashboard's student upload (COLS in src/page.html), the others this page's imports (rows()).
TEMPLATES = {"students": ("Student ID", "Student Name", "Family ID", "Date of birth", "Gender", "School Year Group",
                          "Current Teacher [Quran and Memorisation]", "Current Teacher [Islamic Studies]", "House"),
             "memo": ("Date", "Student ID", "Name", "Target"),
             "qaidah": ("Date", "Student ID", "Name", "Level", "Score")}
TEMPLATE_NAMES = {"students": "students", "memo": "memorisation-passes", "qaidah": "qaidah-tajweed-level-passes"}


@bp.get("/admin/import-template/<any(students, memo, qaidah):kind>.csv")
@auth.admin_required
def template(kind):
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow(TEMPLATES[kind])
    # The byte order mark lets Excel open the file as UTF-8.
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ilmi-{TEMPLATE_NAMES[kind]}-template.csv"',
                             "Cache-Control": "no-store"})


@bp.get("/admin/import")
@bp.get("/admin/import-memo-passes")
@bp.get("/admin/import-qaidah-passes")
@auth.admin_required
def preview():
    return _page()


@bp.post("/admin/import-<any(memo, qaidah):kind>-passes")
@auth.admin_required
def apply(kind):
    auth.check_form_token()
    uploaded = _uploaded()
    if uploaded:
        return _page(kind, uploaded)
    text, picks = request.form.get("sheet", ""), _picks()
    if not text:
        return _page(kind, error="Choose a CSV file first.")
    if request.form.get("confirm", "").strip().lower() != "import":
        return _page(kind, text, picks, error="Type IMPORT in the box to confirm.")
    t = int(time.time())
    done = 0
    try:
        with current_app.config["DB"].transaction() as db:
            changes, report, _, removed = plan(db, kind, text, picks)
            counts = {k: sum(1 for r in report if r["status"] == k) for k in ORDER}
            print(f"[data check] {kind} import by user {g.user['id']}: {len(report)} rows on the sheet; "
                  f"{counts['add']} passes and {counts['fail']} fails added, {counts['repeat']} repeats left out, "
                  f"{counts['missing']} no matching student, {counts['choose']} shared names not chosen, "
                  f"{counts['bad']} unreadable; cleared first: {removed}", flush=True)
            for class_id, revision, _, state, changed in changes:
                current = db.execute("SELECT json FROM classrooms WHERE id = ?", (class_id,)).fetchone()["json"]
                encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                # Keep the version from just before in Class history, then the imported one.
                db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                           (class_id, revision, current, g.user["id"], t))
                db.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (encoded, revision + 1, class_id))
                db.execute("INSERT INTO classroom_history (class_id, revision, json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                           (class_id, revision + 1, encoded, g.user["id"], t))
                auth.audit(db, f"class.{kind}_passes_imported", class_id, {"changes": changed})
                done += 1
    except ValueError as e:
        return _page(kind, error=str(e))
    with current_app.config["DB"].transaction() as db:
        log_summary(db, f"after the {kind} import")
    return _page(kind, done=done)
