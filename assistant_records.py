"""The admin assistant's read-only copy of the records, as SQL tables it can query (see assistant.query_records).

For each question an admin asks, the classrooms (one JSON document each in `classrooms`) are laid out as plain tables
in a private in-memory SQLite database, and the model may run SELECT queries against it. That database is never the
real one: it is built in this process for the question, can only be read, and disappears afterwards, so a query can't
change, lock or slow the live records. Dates of birth, Family IDs, gender and Student IDs are left out.

Positions are written out the way the dashboard shows them ("L4 T1", "205 Al-Buruj"), with the numbers beside them,
so the model never has to work out the syllabus arithmetic itself.
"""
import re
import sqlite3
import time

import export

MAX_ROWS = 200
MAX_CELL = 300
MAX_SQL = 4000
# A query gives up after this long, so a runaway join can't hold the request up.
TIME_LIMIT = 3.0

SCHEMA = """
CREATE TABLE classes (
  class_id TEXT PRIMARY KEY,   -- use with the class tools
  class_name TEXT,             -- e.g. "Yr3-A-QM-Br Ahmed"
  teachers TEXT,               -- comma-separated staff names
  current_students INTEGER
);
CREATE TABLE students (
  student_ref TEXT PRIMARY KEY, -- internal reference for the change tools; never show it
  name TEXT,
  class_id TEXT,
  class_name TEXT,
  house TEXT,                   -- Makkah, Al-Aqsa, Madinah or NULL
  year_group TEXT,              -- madrasah year group, e.g. "Year 4"
  position TEXT,                -- current target as shown on the dashboard: "L4 T1", "205 Al-Buruj", "Quran complete", or "No data yet"
  stage TEXT,                   -- 'book', 'juz', 'complete' or 'not started'
  book_level INTEGER,           -- 1 to 10 while on the book, else NULL
  juz INTEGER,                  -- 30 down to 1 once past the book, else NULL
  juz_item INTEGER,             -- 198 to 332 once past the book, else NULL (higher = further on)
  target_number INTEGER,        -- 1..107 book, 108+ Juz items: higher = further on; use it to rank progress
  memorisation_passes INTEGER,  -- every pass counts, repeats too
  last_pass_date TEXT,          -- YYYY-MM-DD or NULL
  fails INTEGER,
  qaidah_level TEXT,            -- e.g. "Qaidah Level 4" or NULL
  is_ex_student INTEGER,        -- 1 for students removed from their class; leave them out unless asked
  left_date TEXT                -- YYYY-MM-DD for ex students
);
CREATE TABLE passes (           -- one row per memorisation pass; never merge repeats
  student_ref TEXT, name TEXT, class_name TEXT,
  pass_number INTEGER,          -- 1 = the student's first pass
  date TEXT,                    -- YYYY-MM-DD (London)
  passed TEXT,                  -- what was passed, e.g. "L4 T1" or "205 Al-Buruj"
  target_number INTEGER
);
CREATE TABLE fails (student_ref TEXT, name TEXT, class_name TEXT, date TEXT);
CREATE TABLE level_tests (      -- end-of-level tests from the Record page
  student_ref TEXT, name TEXT, class_name TEXT, date TEXT,
  kind TEXT,                    -- 'Quran' (Qaidah/Tajweed level) or 'Memo'
  level TEXT,                   -- e.g. "Qaidah Level 4" or "L4 T1"
  score INTEGER,                -- out of 5, may be NULL
  passed INTEGER                -- 1 passed, 0 not passed
);
CREATE TABLE book_completions (student_ref TEXT, name TEXT, class_name TEXT, date TEXT, book TEXT);
"""

# Only reading is allowed: the authorizer refuses everything else, including ATTACH and PRAGMA.
_READ = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_START = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(select|with)\b", re.I)


class QueryError(Exception):
    """A query the model wrote that can't run; the message goes back to it."""


def schema_text():
    return SCHEMA.strip()


def _level(n, levels):
    for k, size in enumerate(levels, 1):
        if n <= size:
            return k
        n -= size
    return None


def build(classes, teachers, position, ref, day_text, pass_label):
    """The tables, from {class_id: (state, revision)}. position/ref/day_text/pass_label come from assistant.py so the
    copy shows exactly what the rest of the assistant does."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(SCHEMA)
    for cid, (state, _) in classes.items():
        cname, levels = state.get("name", cid), state.get("L") or [1]
        db.execute("INSERT INTO classes VALUES (?, ?, ?, ?)",
                   (cid, cname, ", ".join(teachers.get(cid, [])), len(state.get("S", []))))
        for kind in ("S", "X"):
            for i, s in enumerate(state.get(kind, [])):
                r, name, target = ref(cid, kind, i, s), s.get("name", ""), s.get("target", 1)
                log = s.get("log", [])
                started = target > 1 or log or s.get("fl")
                pos = position(target, state) if started else {"label": "No data yet", "stage": "not started"}
                stage = {"Quran complete": "complete"}.get(pos["stage"], pos["stage"])
                db.execute("INSERT INTO students VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    r, name, cid, cname, s.get("house") or None, s.get("year") or None, pos["label"], stage,
                    _level(target, levels) if stage == "book" else None, pos.get("juz"), pos.get("item"), target,
                    len(log), day_text(max(log)) if log else None, len(s.get("fl", [])),
                    export.quran_level(s["qa"]) if s.get("qa") else None, int(kind == "X"),
                    s.get("left") if kind == "X" else None))
                lt = s.get("lt", [])
                db.executemany("INSERT INTO passes VALUES (?, ?, ?, ?, ?, ?, ?)", [
                    (r, name, cname, k + 1, day_text(t), pass_label(s, k, state), lt[k] if k < len(lt) else None)
                    for k, t in enumerate(log)])
                db.executemany("INSERT INTO fails VALUES (?, ?, ?, ?)", [(r, name, cname, day_text(t)) for t in s.get("fl", [])])
                db.executemany("INSERT INTO level_tests VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                    (r, name, cname, x["d"], "Quran" if x["k"] == "q" else "Memo",
                     export.quran_level(x["n"]) if x["k"] == "q" else (x.get("x") or export.target_label(x["n"], levels)),
                     x.get("sc"), int(bool(x["ok"]))) for x in s.get("tests", [])])
                db.executemany("INSERT INTO book_completions VALUES (?, ?, ?, ?, ?)", [
                    (r, name, cname, day_text(b["t"]), export.BOOKS.get(b["b"], "Book")) for b in s.get("bp", [])])
    db.commit()
    db.execute("PRAGMA query_only = ON")
    db.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _READ else sqlite3.SQLITE_DENY)
    return db


def run(db, sql):
    """Runs one read-only query: {"columns", "rows", "row_count", "truncated"}."""
    sql = str(sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("The query is empty.")
    if len(sql) > MAX_SQL:
        raise QueryError("That query is too long. Keep it under 4,000 characters.")
    if not _START.match(sql):
        raise QueryError("Only SELECT (or WITH ... SELECT) queries can be run. The records copy is read-only.")
    if ";" in sql:
        raise QueryError("Run one query at a time.")
    deadline = time.monotonic() + TIME_LIMIT
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cursor = db.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as e:
        message = str(e)
        if "interrupted" in message:
            raise QueryError("That query took too long. Make it simpler or narrower.")
        if "not authorized" in message or "readonly" in message:
            raise QueryError("Only reading is allowed. The records copy can't be changed.")
        raise QueryError(f"SQL error: {message}")
    finally:
        db.set_progress_handler(None, 0)
    columns = [c[0] for c in cursor.description or []]
    clip = lambda v: v[:MAX_CELL] + "…" if isinstance(v, str) and len(v) > MAX_CELL else v
    return {"columns": columns, "rows": [[clip(v) for v in row] for row in rows[:MAX_ROWS]],
            "row_count": min(len(rows), MAX_ROWS), "truncated": len(rows) > MAX_ROWS}
