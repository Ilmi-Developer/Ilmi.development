"""Ask Ilmi, Ilmi's assistant: one chat panel on the dashboard that behaves differently by role.

Teachers and HeadTeachers get a help assistant: it answers from assistant_knowledge.md (using the site, the syllabus,
general questions about Islam) and can submit a request to the admins. It has no student or classroom data and can
never change anything.

Admins get the same assistant plus data tools that look up and change students and classrooms. Which tools a request
carries is decided here, on the server, from the signed-in session, before the model is called: a non-admin's request
is sent with the help tools only, and every data tool checks again, against the users table, that the person is an
admin each time it runs. Nothing typed in the chat can change that.

A change never happens inside the chat. A write tool only makes a preview (what changes, before and after). The admin's
panel shows it with Confirm and Cancel; only a press of Confirm, a separate request carrying a one-time token tied to
that admin and that preview, applies it, within PREVIEW_TTL. The change goes through server.update_classroom, the same
save the dashboard uses (valid_state, the revision check, Class history and Activity, marked "via assistant"), and is
cancelled if the classroom changed since the preview.

Every question is logged in assistant_log (who, role, when, the question, the tools used, whether a request was
submitted or a change confirmed); the assistant's answers are never stored. Questions are limited per person per hour.
The conversation lives in the browser tab only and is sent back with each question.

Which AI answers: Google's Gemini (free tier) when GEMINI_API_KEY is set; else Gemma 4 on Cloudflare Workers AI (free
daily allowance) when CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set; else Claude with ANTHROPIC_API_KEY.
ASSISTANT_PROVIDER ("gemini", "cloudflare" or "anthropic") chooses explicitly. Admins' record questions go through query_records, read-only SQL over a per-question copy of the
records (assistant_records.py).

Variables: ANTHROPIC_API_KEY (for Claude), ASSISTANT_MODEL (default DEFAULT_MODEL, or CLOUDFLARE_MODEL on Cloudflare),
ASSISTANT_LOG_DAYS (how long assistant_log is kept, default 90), ASSISTANT_DAILY_TOKENS (each teacher's and
HeadTeacher's daily allowance, default DAILY_TOKENS; 0 turns it off).
"""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, g, jsonify, request
from werkzeug.exceptions import Forbidden

import urllib.error
import urllib.request

import assistant_records
import auth
import export
from tv import natural

ROOT = Path(__file__).resolve().parent
KNOWLEDGE_FILE = ROOT / "assistant_knowledge.md"
LONDON = ZoneInfo("Europe/London")
DEFAULT_MODEL = "claude-opus-5-5"
# Cloudflare Workers AI (free daily allowance): Google's Gemma 4, through Cloudflare's OpenAI-compatible endpoint. Used
# when CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set, unless ASSISTANT_PROVIDER says "anthropic".
CLOUDFLARE_MODEL = "@cf/google/gemma-4-26b-a4b-it"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"
# Google's Gemini API (free tier with daily limits), through its OpenAI-compatible endpoint. Used when GEMINI_API_KEY is
# set, unless ASSISTANT_PROVIDER chooses another. "gemini-flash-latest" follows Google's current Flash model; set
# ASSISTANT_MODEL (starting "gemini") to pin one.
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# When Google says the model is busy ("high demand", 503) or too slow, each call is tried again after these waits, then
# once more on the lighter Flash-Lite model, which is usually less busy.
GEMINI_FALLBACK = "gemini-flash-lite-latest"
RETRY_WAITS = (1.5, 3.0)
CALL_TIMEOUT = 40
PROVIDERS = ("gemini", "cloudflare", "anthropic")
OPEN_MAX_TOKENS = 3000
# Models that take server-side refusal fallbacks ("default" routing); others are sent without them.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
# Always medium: there is no effort choice in Ask Ilmi.
EFFORT = "medium"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 8
# A question stops calling tools after this long and answers with what it has.
DEADLINE = 100
QUESTION_MAX = 2000
HISTORY_MESSAGES = 30
HISTORY_TEXT_MAX = 8000
SUMMARY_MAX = 600
# Questions per person per hour.
HOURLY = {"admin": 60}
HOURLY_DEFAULT = 30
# Tokens a teacher or HeadTeacher can use in a London day (midnight to midnight), counted from each answer's usage:
# input, cache writes and output. Cache reads (the knowledge base, re-read on every question at a tenth of the price)
# aren't counted. Admins have no daily allowance, only the hourly limit.
DAILY_TOKENS = 100_000
DAILY_LIMITED = ("teacher", "head")
PREVIEW_TTL = 5 * 60
# At most this many questions run at once, so slow answers can't tie up every server thread (gunicorn.conf.py).
BUSY = threading.BoundedSemaphore(3)
CATEGORIES = ("feature", "change", "problem", "other")
STATUSES = {"new": "New", "done": "Done", "declined": "Declined"}
HOUSES = ("Makkah", "Al-Aqsa", "Madinah")
BOOKS = export.BOOKS
STUDENT_NUMBER = re.compile(r"SE(\d+)", re.IGNORECASE)
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
NAME_MAX = 60

bp = Blueprint("assistant", __name__)


def enabled(app=None):
    """Ask Ilmi is switched on only when ASSISTANT_ENABLED is set (on, 1, true or yes). While it's off, its menu items,
    page, scripts and every route here are gone (404), and nothing is sent anywhere; the code, tables and logs stay."""
    return bool((app or current_app).config.get("ASSISTANT_ENABLED"))


@bp.before_request
def switched_off():
    if not enabled():
        abort(404)


# --- System prompt ------------------------------------------------------------------------------------------------

SHARED_PROMPT = """You are Ask Ilmi, the assistant for staff at a Quran memorisation school. You help with: using Ilmi (the school's website), the syllabus, and general questions about Islam related to teaching.

The website and syllabus:
- Answer from the knowledge base below. Give short, numbered steps for how-to questions, using the exact button and page names.
- If the knowledge base doesn't cover something, say you're not sure and suggest who to ask (see contacts). Never invent features or steps.

Requests:
- If someone wants something changed or added, or reports a problem, offer to submit a request. Write a clear one or two sentence summary, show it to them, and use submit_request only after they confirm.
- Don't promise a request will be approved.

Questions about Islam:
- Give clear, respectful, mainstream explanations suitable for teachers, citing Quran or hadith references where helpful.
- Where scholars differ, say so briefly rather than picking a side.
- Do not give personal religious rulings (fatwas). For questions about a person's own situation, or anything needing a ruling, refer them to the person named for religious questions in the contacts section of the knowledge base.

Style:
- Brief, warm and clear. Lead with the answer.
- Politely decline anything unrelated to the school, the website, the syllabus or Islam.

Formatting (your answer is shown as Markdown):
- Use **bold** for key words, numbered lists for steps, and bullet points for short lists.
- For any list of students or figures, use a Markdown table with a header row, for example:
  | Student | Class | Passes |
  |---|---|---|
  | Aisha Khan | Yr3A | 12 |
- Write dates as "3 November 2026". Don't use HTML or code blocks, and never show SQL, tool names or internal references.

KNOWLEDGE BASE:
{knowledge}
"""

TEACHER_PROMPT = """You cannot see or change any student or classroom data. If asked about a specific student, or asked to change anything, explain that they can find or update it on the dashboard and how, or that an administrator can help. Offer to submit a request if a change needs an admin.
"""

ADMIN_PROMPT = """You are talking to an administrator. You also have tools to look up and change student and classroom data.

How the syllabus maps to the data:
- Targets 1 to 107 are the book (L1 T1 to L10 T14). After that, Juz item = target − 90, so target 108 is item 198 An-Naba (Juz 30), counting up to 332 (Juz 1). A higher item number means further progress.
- Each pass record is one separate pass. Count every one. Never merge them.

Changing data:
- Only change data when the admin clearly asks. If the request is ambiguous (e.g. two students with the same name), ask first.
- Every change produces a preview that the admin must confirm with the button. Never say a change is done until the tool reports it was applied.
- Make one change per student. For several students, list them all for one confirmation.
- Never delete students. Archive them instead.

Using the tools:
- For questions about the records (counts, lists, rankings, who has or hasn't done something, comparisons, dates), use query_records. It runs one read-only SQLite SELECT over a copy of the school's records; its description has the tables. Write the query, read the result, then answer in plain words with a table. If a query fails, fix it and try again.
- In query_records, leave out ex students (is_ex_student = 1) unless asked about them, count passes with COUNT(*) on the passes table (every row is a pass), and rank progress by target_number. "This year" means the school year from 1 September unless the admin says otherwise.
- The tools give positions as labels ("L3 T5", "205 Al-Buruj"); use those labels. "Passes" means memorisation passes; book completions and Quran (Qaidah) level tests are separate and should be labelled as such.
- Student and class references from the tools are internal. Never show them; refer to students by name and class.
- For a question about the whole school, use class_summary with class_id "all" rather than one class at a time.
- When you have made previews, tell the admin to check them and press Confirm or Cancel below. The result of their choice appears in the chat.
"""


def knowledge():
    try:
        return KNOWLEDGE_FILE.read_text(encoding="utf-8")
    except OSError:
        return "(The knowledge base is missing. Say you can't answer questions about the site right now.)"


def system_blocks(role, user_name):
    """The stable part (shared prompt, knowledge base, role part) is cached; today's date and who is asking come
    after the cache breakpoint so they don't invalidate it."""
    stable = SHARED_PROMPT.format(knowledge=knowledge()) + "\n" + (ADMIN_PROMPT if role == "admin" else TEACHER_PROMPT)
    today = datetime.now(LONDON)
    return [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Today is {today.strftime('%A %-d %B %Y')} (London time). You are talking to "
                                     f"{user_name}, whose role is {auth.role_name(role)}."}]


# --- Tool definitions -----------------------------------------------------------------------------------------------

def _tool(name, description, properties=None, required=()):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": list(required),
                             "additionalProperties": False}}


STUDENT = {"type": "string", "description": "The student's reference, exactly as a search or list tool returned it."}
CLASS = {"type": "string", "description": "The class's class_id, exactly as list_classes or another tool returned it."}
DATE = {"type": "string", "description": "A date as YYYY-MM-DD."}

HELP_TOOLS = [
    _tool("submit_request",
          "Send a request (a feature, a change, or a problem report) to the school's administrators. Only call this "
          "after showing the person your summary and them confirming it.",
          {"category": {"type": "string", "enum": list(CATEGORIES)},
           "summary": {"type": "string", "description": "One or two clear sentences, as shown to and confirmed by the person."}},
          ("category", "summary")),
    _tool("list_my_requests", "List the requests this person has submitted through the assistant, with their status."),
]

DATA_TOOLS = [
    _tool("query_records", "Run one read-only SQLite SELECT (or WITH ... SELECT) over a private copy of the school's "
          "records, rebuilt for this question. Returns columns and up to 200 rows. Tables:\n" + assistant_records.schema_text(),
          {"sql": {"type": "string", "description": "One SELECT query."}}, ("sql",)),
    _tool("search_students", "Find students by name (or part of a name). Returns current students with their class; "
          "ex students only when include_ex_students is true.",
          {"name": {"type": "string"}, "include_ex_students": {"type": "boolean"}}, ("name",)),
    _tool("get_student_progress", "One student's current position, every memorisation pass with its date and target, "
          "fails, level tests and book completions.", {"student": STUDENT}, ("student",)),
    _tool("list_classes", "Every classroom with its class_id, name, number of students and teachers."),
    _tool("list_class_students", "The current students in one class with their position and pass totals.",
          {"class_id": CLASS}, ("class_id",)),
    _tool("class_summary", "Memorisation passes and progress per student over a period (dates inclusive, London time). "
          "Use class_id \"all\" for the whole school.",
          {"class_id": {"type": "string", "description": "A class_id, or \"all\" for every class."},
           "from_date": DATE, "to_date": DATE}, ("class_id",)),
    _tool("add_student", "Preview adding a new student to a class. They get the next Student ID. Needs the admin's "
          "Confirm.", {"class_id": CLASS, "name": {"type": "string"}, "house": {"type": "string", "enum": list(HOUSES)},
                       "year": {"type": "string", "description": "Madrasah year group, e.g. \"Year 4\". Optional: "
                                                                 "taken from the class name when left out."}},
          ("class_id", "name")),
    _tool("update_student_details", "Preview changing a student's name, house or madrasah year group. Needs the admin's "
          "Confirm.", {"student": STUDENT, "name": {"type": "string"},
                       "house": {"type": "string", "enum": list(HOUSES) + [""],
                                 "description": "\"\" clears it."},
                       "year": {"type": "string", "description": "e.g. \"Year 4\"; \"\" clears it."}},
          ("student",)),
    _tool("move_student", "Preview moving a student, with all of their records, to another class. Needs the admin's "
          "Confirm.", {"student": STUDENT, "to_class_id": CLASS}, ("student", "to_class_id")),
    _tool("set_student_target", "Preview setting a student's current memorisation target, without recording a pass. "
          "Needs the admin's Confirm.",
          {"student": STUDENT, "target": {"type": "string", "description": "\"L4 T1\", an item number such as \"205\", "
                                                                           "or \"205 Al-Buruj\"."}},
          ("student", "target")),
    _tool("record_pass", "Preview recording one memorisation pass, like the Memo tab's past pass. Needs the admin's "
          "Confirm.", {"student": STUDENT,
                       "date": {"type": "string", "description": "YYYY-MM-DD; today when left out."},
                       "target": {"type": "string", "description": "What was passed (\"L4 T1\" or an item number); "
                                                                   "the student's current target when left out."}},
          ("student",)),
    _tool("correct_or_remove_pass", "Preview removing one memorisation pass, or changing its date. pass_number is the "
          "number get_student_progress gave the pass. Needs the admin's Confirm.",
          {"student": STUDENT, "pass_number": {"type": "integer"},
           "action": {"type": "string", "enum": ["remove", "change_date"]}, "new_date": DATE},
          ("student", "pass_number", "action")),
    _tool("archive_student", "Preview moving a student to Ex students (never a deletion; all records are kept and "
          "they can be returned from Students). Needs the admin's Confirm.",
          {"student": STUDENT, "last_day": {"type": "string", "description": "Their last official day, YYYY-MM-DD; "
                                                                             "today when left out."}},
          ("student",)),
]

HELP_NAMES = {t["name"] for t in HELP_TOOLS}
DATA_NAMES = {t["name"] for t in DATA_TOOLS}
WRITE_NAMES = {"add_student", "update_student_details", "move_student", "set_student_target", "record_pass",
               "correct_or_remove_pass", "archive_student"}


def tools_for(role):
    """The tools a request carries, from the signed-in role alone."""
    return HELP_TOOLS + DATA_TOOLS if role == "admin" else list(HELP_TOOLS)


class ToolError(Exception):
    """A problem the model should hear about and can fix (a bad reference, an invalid date)."""


class Stale(Exception):
    """The classroom changed between the preview and the Confirm."""


# --- Permissions --------------------------------------------------------------------------------------------------

def require_admin():
    """Every data tool and every Confirm checks this, each time, against the users table (not just the session that
    started the request), so a change of role or a turned-off account takes effect at once."""
    if not g.get("user") or g.user["role"] != "admin":
        raise Forbidden()
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT role, disabled FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if not row or row["role"] != "admin" or row["disabled"]:
        raise Forbidden()


def admin_tool(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        require_admin()
        return fn(*args, **kwargs)
    return wrapper


# --- Reading the records ----------------------------------------------------------------------------------------------

def load_classes(tx):
    """Every classroom: {class_id: (state, revision)}."""
    return {r["id"]: (json.loads(r["json"]), r["revision"])
            for r in tx.execute("SELECT id, json, revision FROM classrooms ORDER BY id").fetchall()}


def book_length(state):
    return sum(state.get("L") or [1])


def last_target(state):
    """The highest target a student in this class can have ("Quran complete")."""
    return book_length(state) + state.get("Q", 0) + 1


def position(n, state):
    """A target number as the dashboard shows it, with where it is in the syllabus."""
    levels, book = state.get("L") or [1], book_length(state)
    label = export.target_label(n, levels)
    if n <= book:
        return {"label": label, "stage": "book"}
    k = min(n - book, len(export.JUZ_ITEMS)) - 1
    if export.JUZ_NUMBERS[k] is None:
        return {"label": label, "stage": "Quran complete"}
    return {"label": label, "stage": "juz", "item": n - book + 197, "juz": export.JUZ_NUMBERS[k]}


def started(s):
    return s.get("target", 1) > 1 or s.get("log") or s.get("fl")


def when_text(ms):
    return datetime.fromtimestamp(ms / 1000, LONDON).strftime("%Y-%m-%d %H:%M")


def day_text(ms):
    return datetime.fromtimestamp(ms / 1000, LONDON).strftime("%Y-%m-%d")


def noon(day):
    """A date's pass time, as the Memo tab records a past pass: 12:00 that day, London time."""
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=LONDON).timestamp() * 1000)


def ms_range(from_date, to_date):
    """Inclusive London dates as a [start, end) range in milliseconds."""
    start, end = 0, float("inf")
    if from_date:
        start = noon(parse_day(from_date)) - 12 * 3600 * 1000
    if to_date:
        end = noon(parse_day(to_date)) + 12 * 3600 * 1000
    return start, end


def parse_day(text):
    text = str(text or "").strip()
    try:
        if not DAY.fullmatch(text):
            raise ValueError
        date.fromisoformat(text)
    except ValueError:
        raise ToolError(f"\"{text}\" isn't a date. Use YYYY-MM-DD.")
    return text


def pass_label(s, k, state):
    labels, targets = s.get("lx", []), s.get("lt", [])
    return (labels[k] if k < len(labels) else "") or (export.target_label(targets[k], state.get("L") or [1])
                                                       if k < len(targets) else "")


def tag(s):
    return hashlib.sha256(f"{s.get('name', '')}|{s.get('sid', '')}".encode()).hexdigest()[:8]


def ref(class_id, kind, i, s):
    """A student reference for the model: class, list (S current, X ex), position in that list and a tag of the
    student's name and Student ID, so a reference can't silently point at someone else after a list changes."""
    return f"{class_id}:{kind}:{i}:{tag(s)}"


def resolve(classes, reference, allow_ex=False):
    """(class_id, kind, index, student) for a reference, or a ToolError."""
    parts = str(reference or "").split(":")
    if len(parts) != 4 or parts[1] not in ("S", "X") or not parts[2].isdigit():
        raise ToolError("That isn't a student reference. Use search_students or list_class_students first.")
    class_id, kind, i, want = parts[0], parts[1], int(parts[2]), parts[3]
    if class_id not in classes:
        raise ToolError("That student's class no longer exists. Search for them again.")
    people = classes[class_id][0].get(kind, [])
    if not (i < len(people) and tag(people[i]) == want):
        found = [k for k, s in enumerate(people) if tag(s) == want]
        if len(found) != 1:
            raise ToolError("That student has changed or moved since you looked them up. Search for them again.")
        i = found[0]
    if kind == "X" and not allow_ex:
        raise ToolError(f"{people[i].get('name')} is an ex student. Ex students can only be looked up, not changed.")
    return class_id, kind, i, people[i]


def class_name(classes, class_id):
    return classes[class_id][0].get("name", class_id)


def student_line(classes, class_id, kind, i, s):
    state = classes[class_id][0]
    out = {"name": s.get("name", ""), "class": state.get("name", class_id), "class_id": class_id,
           "student": ref(class_id, kind, i, s)}
    out["position"] = position(s.get("target", 1), state)["label"] if started(s) else "No data yet"
    if kind == "X":
        out["ex_student"] = True
        out["left"] = s.get("left", "")
    return out


# --- Help tools (every role) ------------------------------------------------------------------------------------------

def submit_request(args):
    category, summary = args.get("category"), " ".join(str(args.get("summary") or "").split())[:SUMMARY_MAX]
    if category not in CATEGORIES:
        raise ToolError("category must be feature, change, problem or other.")
    if not summary:
        raise ToolError("The summary is empty.")
    with auth.db().transaction() as tx:
        item = tx.insert("INSERT INTO assistant_requests (user_id, name, created_at, category, summary) "
                         "VALUES (?, ?, ?, ?, ?)", (g.user["id"], g.user["name"], auth.now(), category, summary))
        auth.audit(tx, "assistant.request", detail={"item": item, "category": category})
    g.assistant_request_submitted = True
    return {"submitted": True, "category": category, "summary": summary,
            "note": "Saved for the administrators. Don't promise it will be approved."}


def list_my_requests(args):
    with auth.db().transaction() as tx:
        rows = tx.execute("SELECT created_at, category, summary, status FROM assistant_requests WHERE user_id = ? "
                          "ORDER BY created_at DESC, id DESC LIMIT 20", (g.user["id"],)).fetchall()
    return {"requests": [{"submitted": datetime.fromtimestamp(r["created_at"], LONDON).strftime("%Y-%m-%d"),
                          "category": r["category"], "summary": r["summary"], "status": STATUSES[r["status"]]}
                         for r in rows]}


# --- Data tools: reading (admins only) -----------------------------------------------------------------------------

@admin_tool
def search_students(args):
    needle = " ".join(str(args.get("name") or "").split()).lower()
    if len(needle) < 2:
        raise ToolError("Give at least two letters of the name.")
    kinds = ("S", "X") if args.get("include_ex_students") else ("S",)
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
    found = []
    for class_id, (state, _) in classes.items():
        for kind in kinds:
            for i, s in enumerate(state.get(kind, [])):
                names = [s.get("name", "")] + list(s.get("xn", []))
                if any(needle in n.lower() for n in names):
                    found.append(student_line(classes, class_id, kind, i, s))
    found.sort(key=lambda x: (natural(x["name"]), natural(x["class"])))
    return {"matches": found[:25], "total": len(found)}


@admin_tool
def get_student_progress(args):
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
    class_id, kind, i, s = resolve(classes, args.get("student"), allow_ex=True)
    state = classes[class_id][0]
    out = student_line(classes, class_id, kind, i, s)
    if started(s):
        out["position_detail"] = position(s.get("target", 1), state)
    if s.get("house"):
        out["house"] = s["house"]
    log = s.get("log", [])
    out["memorisation_passes"] = [{"pass_number": k + 1, "date": when_text(t), "passed": pass_label(s, k, state)}
                                  for k, t in enumerate(log)]
    out["memorisation_pass_count"] = len(log)
    out["fails"] = [day_text(t) for t in s.get("fl", [])]
    out["level_tests"] = [{"date": x["d"], "kind": "Quran" if x["k"] == "q" else "Memo",
                           "level": export.quran_level(x["n"]) if x["k"] == "q" else
                           (x.get("x") or export.target_label(x["n"], state.get("L") or [1])),
                           "score_out_of_5": x.get("sc"), "result": "Passed" if x["ok"] else "Not passed"}
                          for x in s.get("tests", [])]
    out["book_completions"] = [{"date": day_text(b["t"]), "book": BOOKS.get(b["b"], "Book")} for b in s.get("bp", [])]
    if s.get("qa"):
        out["qaidah_level"] = export.quran_level(s["qa"])
    return out


@admin_tool
def list_classes(args):
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
        teachers = {}
        for r in tx.execute("SELECT m.class_id, u.name FROM classroom_members m JOIN users u ON u.id = m.user_id "
                            "ORDER BY u.name").fetchall():
            teachers.setdefault(r["class_id"], []).append(r["name"])
    rows = [{"class_id": cid, "name": state.get("name", cid), "students": len(state.get("S", [])),
             "teachers": teachers.get(cid, [])} for cid, (state, _) in classes.items()]
    return {"classes": sorted(rows, key=lambda r: natural(r["name"]))}


def _class_or_error(classes, class_id):
    if class_id not in classes:
        raise ToolError("There's no class with that class_id. Use list_classes.")
    return classes[class_id][0]


@admin_tool
def list_class_students(args):
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
    class_id = args.get("class_id")
    state = _class_or_error(classes, class_id)
    rows = []
    for i, s in enumerate(state.get("S", [])):
        row = student_line(classes, class_id, "S", i, s)
        del row["class"], row["class_id"]
        log = s.get("log", [])
        row["stage"] = position(s.get("target", 1), state)["stage"] if started(s) else "book"
        row["memorisation_passes"] = len(log)
        row["last_pass"] = day_text(max(log)) if log else None
        rows.append(row)
    rows.sort(key=lambda r: natural(r["name"]))
    return {"class": state.get("name", class_id), "students": rows}


@admin_tool
def class_summary(args):
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
    class_id = args.get("class_id")
    ids = sorted(classes, key=lambda c: natural(class_name(classes, c))) if class_id == "all" else [class_id]
    if class_id != "all":
        _class_or_error(classes, class_id)
    start, end = ms_range(args.get("from_date"), args.get("to_date"))
    rows, total = [], 0
    for cid in ids:
        state = classes[cid][0]
        for i, s in enumerate(state.get("S", [])):
            inside = [k for k, t in enumerate(s.get("log", [])) if start <= t < end]
            total += len(inside)
            row = {"name": s.get("name", ""), "class": state.get("name", cid), "student": ref(cid, "S", i, s),
                   "memorisation_passes_in_period": len(inside),
                   "position_now": position(s.get("target", 1), state)["label"] if started(s) else "No data yet",
                   "stage_now": position(s.get("target", 1), state)["stage"] if started(s) else "book",
                   "fails_in_period": sum(1 for t in s.get("fl", []) if start <= t < end),
                   "book_completions_in_period": sum(1 for b in s.get("bp", []) if start <= b["t"] < end),
                   "quran_level_tests_passed_in_period": sum(
                       1 for x in s.get("tests", []) if x["k"] == "q" and x["ok"] and start <= noon(x["d"]) < end)}
            if inside:
                row["first_pass_in_period"] = {"date": day_text(s["log"][inside[0]]),
                                               "passed": pass_label(s, inside[0], state)}
                row["last_pass_in_period"] = {"date": day_text(s["log"][inside[-1]]),
                                              "passed": pass_label(s, inside[-1], state)}
            log = s.get("log", [])
            row["last_pass_ever"] = day_text(max(log)) if log else None
            rows.append(row)
    return {"period": {"from": args.get("from_date") or "the beginning", "to": args.get("to_date") or "today"},
            "students": rows, "total_memorisation_passes": total,
            "note": "Each memorisation pass is counted separately; repeated passes all count."}


# --- Data tools: changes (admins only, preview then Confirm) ---------------------------------------------------------

def madrasah_year(class_title, ist=""):
    """The madrasah year group from a class name, as the dashboard's madrasahYear does."""
    m = re.match(r"^yr\s*(\d+)(/\d+)?", class_title or "", re.I)
    if not m:
        return ""
    if not m.group(2):
        return f"Year {m.group(1)}"
    n = re.match(r"^yr\s*(\d+)(?!/)", ist or "", re.I)
    return f"Year {n.group(1)}" if n else ""


def nkey(text):
    return " ".join(str(text or "").split()).lower()


def clean_name(text):
    name = " ".join(str(text or "").split())
    if not name:
        raise ToolError("The name is empty.")
    if len(name) > NAME_MAX:
        raise ToolError(f"Names can be at most {NAME_MAX} characters.")
    return name


def clean_year(text):
    year = " ".join(str(text or "").split())
    if year.isdigit():
        year = f"Year {year}"
    if len(year) > 20:
        raise ToolError("That year group is too long.")
    return year


def parse_target(text, state):
    """A target as "L4 T1", "L4T1", an item number ("205") or "205 Al-Buruj"; returns the target number."""
    text = " ".join(str(text or "").split())
    levels, book = state.get("L") or [1], book_length(state)
    m = re.fullmatch(r"[Ll]\s*(\d+)\s*[Tt]\s*(\d+)", text)
    if m:
        level, t = int(m.group(1)), int(m.group(2))
        if not (1 <= level <= len(levels) and 1 <= t <= levels[level - 1]):
            raise ToolError(f"There's no {text} in this class's book (it has {len(levels)} levels).")
        return sum(levels[:level - 1]) + t
    m = re.match(r"(\d+)\b", text)
    if m:
        item = int(m.group(1))
        n = item - 197 + book
        if not 198 <= item or n > last_target(state) - 1:
            raise ToolError(f"Item {item} isn't one of this class's Juz items (198 An-Naba onwards).")
        return n
    if text.lower() == "quran complete":
        return last_target(state)
    raise ToolError("Give the target as \"L4 T1\" or an item number such as \"205\".")


def target_text(n, state):
    return position(n, state)["label"]


def next_student_id(classes):
    last = 0
    for state, _ in classes.values():
        for s in state.get("S", []) + state.get("X", []):
            m = STUDENT_NUMBER.fullmatch(str(s.get("sid", "")).strip())
            if m:
                last = max(last, int(m.group(1)))
    return f"SE{last + 1:03d}"


def blank_student(name):
    """A new student, as the dashboard's blank() makes one."""
    return {"name": name, "target": 1, "passes": 0, "merits": 0, "hit": None, "log": [], "lt": [], "lx": [], "ml": [],
            "fl": [], "q": {}, "warn": 0, "tg": [], "tb": [], "tr": []}


def mark_edited(s, key):
    s["ed"] = [k for k in s.get("ed", []) if k != key] + [key]


def _today():
    return datetime.now(LONDON).strftime("%Y-%m-%d")


def apply_change(kind, params, classes):
    """Makes one change to copies of the classrooms it touches. Returns (summary lines, {class_id: new state}). Run at
    the preview (to check it and describe it) and again at Confirm on the then-current records, which must still be at
    the previewed revisions, so both runs make the same change."""
    def student():
        cid, _, i, s = resolve(classes, params["student"])
        state = copy.deepcopy(classes[cid][0])
        return cid, state, state["S"][i]

    def take_out(state, s):
        """Removes this very student (not an equal-looking one) from the class list."""
        state["S"].pop(next(k for k, o in enumerate(state["S"]) if o is s))

    if kind == "add_student":
        cid = params["class_id"]
        state = copy.deepcopy(_class_or_error(classes, cid))
        name = clean_name(params["name"])
        if any(nkey(o.get("name")) == nkey(name) for o in state["S"]):
            raise ToolError(f"There is already a student called {name} in {state.get('name', cid)}.")
        sid = next_student_id(classes)
        if params.get("sid") and params["sid"] != sid:
            raise Stale(f"Student ID {params['sid']} has been given to someone else since the preview.")
        s = blank_student(name)
        s["sid"] = sid
        if params.get("house"):
            s["house"] = params["house"]
        year = params.get("year") or madrasah_year(state.get("name", ""))
        if year:
            s["year"] = year
        state["S"].append(s)
        params["sid"] = sid
        lines = [f"Add {name} to {state.get('name', cid)}", f"Student ID: {sid}",
                 f"House: {s.get('house') or 'not set'}", f"Madrasah year group: {s.get('year') or 'not set'}"]
        return lines, {cid: state}

    if kind == "update_student_details":
        cid, state, s = student()
        lines = [f"Change {s['name']}'s details in {state.get('name', cid)}"]
        if "name" in params:
            name = clean_name(params["name"])
            if name != s["name"]:
                if any(o is not s and nkey(o.get("name")) == nkey(name) for o in state["S"]):
                    raise ToolError(f"There is already a student called {name} in {state.get('name', cid)}.")
                lines.append(f"Name: {s['name']} → {name}")
                s["xn"] = [x for x in s.get("xn", []) if nkey(x) != nkey(s["name"])][-9:] + [s["name"]]
                s["name"] = name
                mark_edited(s, "name")
        for key, label in (("house", "House"), ("year", "Madrasah year group")):
            if key not in params:
                continue
            value = params[key] if key == "house" else clean_year(params[key])
            if key == "house" and value not in HOUSES + ("",):
                raise ToolError("House must be Makkah, Al-Aqsa or Madinah.")
            was = s.get(key, "")
            if value == was:
                continue
            lines.append(f"{label}: {was or 'not set'} → {value or 'not set'}")
            if value:
                s[key] = value
            else:
                s.pop(key, None)
            mark_edited(s, key)
        if len(lines) == 1:
            raise ToolError("Nothing would change: those are already the student's details.")
        return lines, {cid: state}

    if kind == "move_student":
        cid, state, s = student()
        to = params["to_class_id"]
        target_state = copy.deepcopy(_class_or_error(classes, to))
        if to == cid:
            raise ToolError(f"{s['name']} is already in {state.get('name', cid)}.")
        if (target_state.get("L") or [1]) != (state.get("L") or [1]) or s.get("target", 1) > last_target(target_state):
            raise ToolError(f"{target_state.get('name', to)} has a different book or Juz list, so {s['name']} can't "
                            "be moved here safely. Move them from Students on the dashboard instead.")
        if any(nkey(o.get("name")) == nkey(s["name"]) for o in target_state["S"]):
            raise ToolError(f"There is already a student called {s['name']} in {target_state.get('name', to)}.")
        take_out(state, s)
        year = madrasah_year(target_state.get("name", ""), s.get("ist", ""))
        lines = [f"Move {s['name']} from {state.get('name', cid)} to {target_state.get('name', to)}, with all of their "
                 f"records ({len(s.get('log', []))} memorisation passes)"]
        if year and year != s.get("year"):
            lines.append(f"Madrasah year group: {s.get('year') or 'not set'} → {year}")
            s["year"] = year
        mark_edited(s, "class")
        target_state["S"].append(s)
        return lines, {cid: state, to: target_state}

    if kind == "set_student_target":
        cid, state, s = student()
        n = parse_target(params["target"], state)
        if n == s.get("target", 1):
            raise ToolError(f"{s['name']}'s target is already {target_text(n, state)}.")
        lines = [f"Set {s['name']}'s target in {state.get('name', cid)} (no pass is recorded)",
                 f"Target: {target_text(s.get('target', 1), state)} → {target_text(n, state)}"]
        s["target"] = n
        return lines, {cid: state}

    if kind == "record_pass":
        cid, state, s = student()
        day = parse_day(params.get("date") or _today())
        params["date"] = day
        if day > _today():
            raise ToolError("That date is in the future.")
        if state.get("fcd") and day < state["fcd"]:
            raise ToolError(f"That's before the class's first day ({state['fcd']}).")
        n = parse_target(params["target"], state) if params.get("target") else s.get("target", 1)
        if n >= last_target(state):
            raise ToolError(f"{s['name']} has completed the Quran; there's no target left to pass.")
        t = noon(day)
        rows = sorted(list(zip(s["log"], s["lt"], s["lx"])) + [(t, n, "")], key=lambda r: (r[0], r[1]))
        s["log"], s["lt"], s["lx"] = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
        s["passes"] = s.get("passes", 0) + 1
        s["hit"] = s["log"][-1]
        lines = [f"Record a memorisation pass for {s['name']} in {state.get('name', cid)}",
                 f"Passed: {target_text(n, state)} on {day}",
                 f"Memorisation passes: {len(rows) - 1} → {len(rows)}"]
        if s.get("target", 1) <= n:
            new = min(n + 1, last_target(state))
            lines.append(f"Target: {target_text(s.get('target', 1), state)} → {target_text(new, state)}")
            s["target"] = new
        return lines, {cid: state}

    if kind == "correct_or_remove_pass":
        cid, state, s = student()
        k = params["pass_number"] - 1 if isinstance(params.get("pass_number"), int) else -1
        if not 0 <= k < len(s["log"]):
            raise ToolError(f"{s['name']} has {len(s['log'])} memorisation passes; there's no pass {params.get('pass_number')}.")
        if params.get("expect") and params["expect"] != [s["log"][k], s["lt"][k]]:
            raise Stale("That pass has changed since the preview.")
        params["expect"] = [s["log"][k], s["lt"][k]]
        what = f"{pass_label(s, k, state)} on {day_text(s['log'][k])}"
        if params.get("action") == "remove":
            for key in ("log", "lt", "lx"):
                s[key].pop(k)
            s["passes"] = max(0, s.get("passes", 0) - 1)
            s["hit"] = s["log"][-1] if s["log"] else None
            return [f"Remove one memorisation pass from {s['name']} in {state.get('name', cid)}: {what}",
                    f"Memorisation passes: {len(s['log']) + 1} → {len(s['log'])}",
                    f"Target stays {target_text(s.get('target', 1), state)}"], {cid: state}
        if params.get("action") == "change_date":
            day = parse_day(params.get("new_date"))
            if day > _today():
                raise ToolError("That date is in the future.")
            rows = list(zip(s["log"], s["lt"], s["lx"]))
            rows[k] = (noon(day), rows[k][1], rows[k][2])
            rows.sort(key=lambda r: (r[0], r[1]))
            s["log"], s["lt"], s["lx"] = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
            s["hit"] = s["log"][-1]
            return [f"Change the date of one of {s['name']}'s memorisation passes in {state.get('name', cid)}",
                    f"{what.rsplit(' on ', 1)[0]}: "
                    f"{what.rsplit(' on ', 1)[1]} → {day}"], {cid: state}
        raise ToolError("action must be remove or change_date.")

    if kind == "archive_student":
        cid, state, s = student()
        day = parse_day(params.get("last_day") or _today())
        params["last_day"] = day
        take_out(state, s)
        s["left"] = day
        state.setdefault("X", []).append(s)
        return [f"Move {s['name']} from {state.get('name', cid)} to Ex students, last official day {day}",
                "Nothing is deleted: all of their records are kept, and they can be returned from Students."], {cid: state}

    raise ToolError("Unknown change.")


def preview(kind, args):
    """Checks a change and saves it as a pending preview for the admin to confirm. The model is told only that the
    preview exists; the token goes to the admin's browser, never to the model."""
    require_admin()
    params = {k: v for k, v in args.items() if v is not None}
    with auth.db().transaction() as tx:
        classes = load_classes(tx)
        try:
            lines, changed = apply_change(kind, params, classes)
        except Stale as e:
            raise ToolError(str(e))
        import server  # server imports this module; the shared save path lives there
        for cid, state in changed.items():
            if not server.valid_state(state):
                raise ToolError("That change wouldn't make a valid record, so it can't be made here.")
        params["revisions"] = {cid: classes[cid][1] for cid in changed}
        token = secrets.token_urlsafe(32)
        t = auth.now()
        summary = "\n".join(lines)
        tx.execute("INSERT INTO assistant_pending (token_hash, user_id, created_at, expires_at, kind, params, summary) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (auth.token_hash(token), g.user["id"], t, t + PREVIEW_TTL, kind, json.dumps(params), summary))
    g.assistant_pending.append({"token": token, "summary": summary, "kind": kind})
    return {"status": "preview_ready", "change": lines,
            "note": "The admin now sees this change with Confirm and Cancel buttons. It has NOT been made. Don't say "
                    "it is done; ask them to check it and press Confirm or Cancel."}


def _writer(kind):
    @admin_tool
    def run(args):
        return preview(kind, args)
    run.__name__ = kind
    return run


@admin_tool
def query_records(args):
    """A read-only SQL query over this question's copy of the records (assistant_records), built once per question."""
    if g.get("assistant_records") is None:
        with auth.db().transaction() as tx:
            classes = load_classes(tx)
            teachers = {}
            for r in tx.execute("SELECT m.class_id, u.name FROM classroom_members m JOIN users u ON u.id = m.user_id "
                                "ORDER BY u.name").fetchall():
                teachers.setdefault(r["class_id"], []).append(r["name"])
        g.assistant_records = assistant_records.build(classes, teachers, position, ref, day_text, pass_label)
    try:
        return assistant_records.run(g.assistant_records, args.get("sql"))
    except assistant_records.QueryError as e:
        raise ToolError(str(e))


HELP_HANDLERS = {"submit_request": submit_request, "list_my_requests": list_my_requests}
DATA_HANDLERS = {"query_records": query_records, "search_students": search_students, "get_student_progress": get_student_progress,
                 "list_classes": list_classes, "list_class_students": list_class_students,
                 "class_summary": class_summary, **{name: _writer(name) for name in WRITE_NAMES}}


def run_tool(name, args):
    """Runs one tool call. Data tools refuse anyone who isn't an admin (Forbidden), whatever was asked."""
    if not isinstance(args, dict):
        raise ToolError("The tool input must be an object.")
    if name in HELP_HANDLERS:
        return HELP_HANDLERS[name](args)
    if name in DATA_HANDLERS:
        require_admin()
        return DATA_HANDLERS[name](args)
    raise ToolError(f"There's no tool called {name}.")


# --- The Messages API -------------------------------------------------------------------------------------------------

def client():
    if "ASSISTANT_CLIENT" not in current_app.config:
        import anthropic
        current_app.config["ASSISTANT_CLIENT"] = anthropic.Anthropic(
            api_key=current_app.config["ANTHROPIC_API_KEY"], timeout=60.0, max_retries=2)
    return current_app.config["ASSISTANT_CLIENT"]


def ask_model(messages, tools, system, tool_choice=None):
    model = current_app.config["ASSISTANT_MODEL"] or DEFAULT_MODEL
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in FALLBACK_MODELS else {}
    if tool_choice:
        extra["tool_choice"] = tool_choice
    return client().beta.messages.create(model=model, max_tokens=MAX_TOKENS, system=system, tools=tools,
                                         messages=messages, output_config={"effort": EFFORT}, **extra)


def _has(*keys):
    return all((current_app.config.get(k) or "").strip() for k in keys)


def provider():
    """Which AI answers: ASSISTANT_PROVIDER when set ("anthropic", "gemini" or "cloudflare"), else the first one with
    its keys: Claude (ANTHROPIC_API_KEY), then Gemini (GEMINI_API_KEY), then Cloudflare (CLOUDFLARE_ACCOUNT_ID and
    CLOUDFLARE_API_TOKEN)."""
    chosen = (current_app.config.get("ASSISTANT_PROVIDER") or "").strip().lower()
    if chosen in PROVIDERS:
        return chosen
    if _has("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _has("GEMINI_API_KEY"):
        return "gemini"
    if current_app.config.get("ASSISTANT_HTTP") or _has("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        return "cloudflare"
    return "anthropic"


def configured():
    which = provider()
    if current_app.config.get("ASSISTANT_HTTP") and which != "anthropic":
        return True
    if which == "gemini":
        return _has("GEMINI_API_KEY")
    if which == "cloudflare":
        return _has("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    return bool(current_app.config.get("ANTHROPIC_API_KEY") or "ASSISTANT_CLIENT" in current_app.config)


class FreeAllowanceUsed(Exception):
    """The free tier's limit is reached: Cloudflare's daily allowance, or Gemini's per-minute or daily quota."""


def open_model():
    """The model for a Chat Completions provider: ASSISTANT_MODEL when it belongs to that provider, else its default."""
    model = (current_app.config.get("ASSISTANT_MODEL") or "").strip()
    if provider() == "gemini":
        return model if model.startswith("gemini") else GEMINI_MODEL
    return model if model.startswith("@cf/") else CLOUDFLARE_MODEL


class ProviderBusy(Exception):
    """The AI service stayed busy or too slow through every retry (Google's "high demand", for example)."""


def _send(url, key, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as res:
        return json.loads(res.read().decode())


def open_chat(payload):
    """One Chat Completions call to Gemini or Workers AI, whichever is in use, riding out short busy spells: a busy
    (5xx) answer or a timeout is retried after RETRY_WAITS, and on Gemini then tried on GEMINI_FALLBACK. Gemini is asked
    to think briefly (reasoning_effort "low"), so answers come quickly. Tests set ASSISTANT_HTTP to stand in."""
    fake = current_app.config.get("ASSISTANT_HTTP")
    if fake:
        return fake(payload)
    gemini = provider() == "gemini"
    if gemini:
        url, key, name = GEMINI_URL, current_app.config["GEMINI_API_KEY"].strip(), "Gemini"
        payload = dict(payload, reasoning_effort="low")
    else:
        url = CLOUDFLARE_URL.format(account=current_app.config["CLOUDFLARE_ACCOUNT_ID"].strip())
        key, name = current_app.config["CLOUDFLARE_API_TOKEN"].strip(), "Workers AI"
    models = [payload["model"]] + ([GEMINI_FALLBACK] if gemini and payload["model"] != GEMINI_FALLBACK else [])
    for model in models:
        waits = (0,) + RETRY_WAITS
        k = 0
        while k < len(waits):
            if waits[k]:
                time.sleep(waits[k])
            try:
                return _send(url, key, dict(payload, model=model))
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:500]
                if e.code == 429 or "allocation" in body.lower() or "4006" in body or "RESOURCE_EXHAUSTED" in body:
                    raise FreeAllowanceUsed(body)
                if e.code == 400 and "reasoning" in body.lower() and "reasoning_effort" in payload:
                    payload = {k2: v for k2, v in payload.items() if k2 != "reasoning_effort"}
                    continue
                if e.code >= 500 or "UNAVAILABLE" in body:
                    current_app.logger.warning("%s (%s) is busy (%s); trying again", name, model, e.code)
                    k += 1
                    continue
                current_app.logger.error("%s answered %s: %s", name, e.code, body)
                raise
            except (TimeoutError, urllib.error.URLError) as e:
                if isinstance(e, urllib.error.URLError) and not isinstance(getattr(e, "reason", None), TimeoutError):
                    raise
                current_app.logger.warning("%s (%s) was too slow; trying the next option", name, model)
                break
    raise ProviderBusy()


cloudflare_chat = open_chat


THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def open_tools(role):
    """The same tools, in the Chat Completions (function) shape."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools_for(role)]


def converse_open(role, messages):
    """The same tool-use loop as converse(), for Chat Completions models (Gemini, or Gemma on Workers AI)."""
    blocks = system_blocks(role, g.user["name"])
    chat = [{"role": "system", "content": blocks[0]["text"] + "\n\n" + blocks[1]["text"]}] + messages
    tools, used, calls, started_at = open_tools(role), g.assistant_used, 0, time.monotonic()
    model = open_model()
    while True:
        out_of_calls = calls >= MAX_TOOL_CALLS or time.monotonic() - started_at > DEADLINE
        payload = {"model": model, "messages": chat, "tools": tools, "max_tokens": OPEN_MAX_TOKENS, "temperature": 0.2}
        if out_of_calls:
            payload["tool_choice"] = "none"
        data = open_chat(payload)
        usage = data.get("usage") or {}
        g.assistant_tokens += int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        calls_asked = message.get("tool_calls") or []
        text = THINK.sub("", message.get("content") or "").strip()
        if not calls_asked or out_of_calls:
            return text or "Sorry, I couldn't put an answer together. Try asking another way."
        chat.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls_asked})
        for call in calls_asked:
            fn = call.get("function") or {}
            name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
            if calls >= MAX_TOOL_CALLS:
                content = "Tool limit reached for this question. Answer with what you have."
            else:
                calls += 1
                try:
                    args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                    content = json.dumps(run_tool(name, args), ensure_ascii=False, separators=(",", ":"))
                    used.append(name)
                except Forbidden:
                    used.append("refused:" + name)
                    content = "Error: this tool isn't available to this person."
                except ValueError:
                    used.append(name)
                    content = "Error: the tool input wasn't valid JSON."
                except ToolError as e:
                    used.append(name)
                    content = "Error: " + str(e)
            chat.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})


def text_of(response):
    return "\n\n".join(b.text for b in response.content if getattr(b, "type", "") == "text" and b.text).strip()


def converse(role, messages):
    """The tool-use loop on Cloudflare (Gemma) or Anthropic (Claude), whichever is set up."""
    if provider() in ("gemini", "cloudflare"):
        return converse_open(role, messages)
    return converse_claude(role, messages)


def converse_claude(role, messages):
    """The tool-use loop: ask, run the tools the model asks for, send back the results, until it answers. At most
    MAX_TOOL_CALLS tool calls per question; after that (or DEADLINE) the model must answer with what it has."""
    tools, system = tools_for(role), system_blocks(role, g.user["name"])
    used, calls, started_at = g.assistant_used, 0, time.monotonic()
    while True:
        out_of_calls = calls >= MAX_TOOL_CALLS or time.monotonic() - started_at > DEADLINE
        response = ask_model(messages, tools, system, {"type": "none"} if out_of_calls else None)
        g.assistant_tokens += tokens_of(response)
        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that one."
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if response.stop_reason != "tool_use" or not uses or out_of_calls:
            return text_of(response) or "Sorry, I couldn't put an answer together. Try asking another way."
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for use in uses:
            if calls >= MAX_TOOL_CALLS:
                results.append({"type": "tool_result", "tool_use_id": use.id, "is_error": True,
                                "content": "Tool limit reached for this question. Answer with what you have."})
                continue
            calls += 1
            try:
                result = run_tool(use.name, use.input)
                used.append(use.name)
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
            except Forbidden:
                used.append("refused:" + use.name)
                results.append({"type": "tool_result", "tool_use_id": use.id, "is_error": True,
                                "content": "This tool isn't available to this person."})
            except ToolError as e:
                used.append(use.name)
                results.append({"type": "tool_result", "tool_use_id": use.id, "is_error": True, "content": str(e)})
        messages.append({"role": "user", "content": results})


def clean_history(history):
    """The conversation so far, from the tab: alternating user and assistant text, starting with a question and ending
    with an answer. Anything else is refused rather than repaired."""
    if history is None:
        return []
    if not isinstance(history, list) or len(history) > HISTORY_MESSAGES:
        abort(400)
    out = []
    for k, item in enumerate(history):
        if not isinstance(item, dict) or item.get("role") != ("user" if k % 2 == 0 else "assistant"):
            abort(400)
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > HISTORY_TEXT_MAX:
            abort(400)
        out.append({"role": item["role"], "content": content})
    if out and out[-1]["role"] != "assistant":
        abort(400)
    return out


def tokens_of(response):
    """The tokens one answer counts against the daily allowance (see DAILY_TOKENS)."""
    usage = getattr(response, "usage", None)
    return sum(getattr(usage, k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))


def day_start():
    """London midnight today, as Unix seconds."""
    now = datetime.now(LONDON)
    return int(datetime(now.year, now.month, now.day, tzinfo=LONDON).timestamp())


def used_today(tx, user_id):
    return tx.execute("SELECT COALESCE(SUM(tokens), 0) AS n FROM assistant_log WHERE user_id = ? AND at >= ?",
                      (user_id, day_start())).fetchone()["n"]


def log(tx, question, tools=(), request_submitted=False, change_confirmed=False):
    return tx.insert("INSERT INTO assistant_log (user_id, role, at, question, tools, request_submitted, change_confirmed) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (g.user["id"], g.user["role"], auth.now(), question, json.dumps(list(tools)),
                      int(request_submitted), int(change_confirmed)))


@bp.post("/api/assistant")
def ask():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    question = str(data.get("message") or "").strip()
    if not question:
        return jsonify(error="Type a question first."), 400
    if len(question) > QUESTION_MAX:
        return jsonify(error=f"Questions can be at most {QUESTION_MAX} characters."), 400
    history = clean_history(data.get("history"))
    if not configured():
        return jsonify(error="The assistant isn't set up yet. Ask an administrator."), 503
    role = g.user["role"]
    with auth.db().transaction() as tx:
        asked = tx.execute("SELECT COUNT(*) AS n FROM assistant_log WHERE user_id = ? AND at > ? AND question IS NOT NULL",
                           (g.user["id"], auth.now() - auth.HOUR)).fetchone()["n"]
        if asked >= HOURLY.get(role, HOURLY_DEFAULT):
            return jsonify(error="You've asked a lot of questions in the last hour. Try again a little later."), 429
        allowance = current_app.config.get("ASSISTANT_DAILY_TOKENS", DAILY_TOKENS)
        if role in DAILY_LIMITED and allowance and used_today(tx, g.user["id"]) >= allowance:
            return jsonify(error="That's today's Ask Ilmi allowance used up. It resets at midnight."), 429
        entry = log(tx, question)
    if not BUSY.acquire(blocking=False):
        return jsonify(error="The assistant is busy. Try again in a moment."), 503
    g.assistant_pending, g.assistant_request_submitted, g.assistant_used, g.assistant_tokens = [], False, [], 0
    g.assistant_records = None
    try:
        reply = converse(role, history + [{"role": "user", "content": question}])
    except ProviderBusy:
        who = "Gemini is very busy right now (a spike on Google's side)" if provider() == "gemini" else \
            "The AI service is very busy right now"
        return jsonify(error=who + ". Try again in a minute."), 503
    except FreeAllowanceUsed:
        if provider() == "gemini":
            return jsonify(error="Ask Ilmi has reached its free limit for now. Try again in a minute; if it keeps "
                                 "happening, it's back tomorrow."), 429
        return jsonify(error="Ask Ilmi has used today's free allowance for the school. It's back after midnight (UTC)."), 429
    except Exception:
        current_app.logger.exception("The assistant could not answer")
        # 503, not 502: Railway's edge replaces an app's 502 with its own page, which hid this message.
        return jsonify(error="Ask Ilmi couldn't answer just now. Try again in a minute."), 503
    finally:
        BUSY.release()
        if g.get("assistant_records") is not None:
            g.assistant_records.close()
            g.assistant_records = None
        with auth.db().transaction() as tx:
            tx.execute("UPDATE assistant_log SET tools = ?, request_submitted = ?, tokens = ? WHERE id = ?",
                       (json.dumps(g.assistant_used), int(g.assistant_request_submitted), g.assistant_tokens, entry))
    pending = [{"token": p["token"], "summary": p["summary"]} for p in g.assistant_pending]
    # The tools it used (names only), so the page can show what it did while thinking.
    return jsonify(reply=reply, pending=pending, expiresIn=PREVIEW_TTL if pending else None,
                   dataTools=role == "admin", tools=g.assistant_used)


@bp.post("/api/assistant/confirm")
def confirm():
    """Applies (or cancels) previewed changes: only the admin who saw the preview, once, within PREVIEW_TTL. The
    changes of one confirmation are applied one by one, each checked against the revision it was previewed on (plus
    the saves this same confirmation made before it)."""
    require_admin()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("action") not in ("confirm", "cancel"):
        abort(400)
    tokens = data.get("tokens")
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= 20 or not all(isinstance(t, str) and t for t in tokens):
        abort(400)
    t = auth.now()
    claimed = []
    with auth.db().transaction() as tx:
        for token in tokens:
            row = tx.execute("SELECT token_hash, kind, params, summary FROM assistant_pending WHERE token_hash = ? "
                             "AND user_id = ? AND used_at IS NULL AND expires_at > ?",
                             (auth.token_hash(token), g.user["id"], t)).fetchone()
            if row and tx.execute("UPDATE assistant_pending SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                                  (t, row["token_hash"])).rowcount:
                claimed.append(dict(row))
    if len(claimed) != len(tokens):
        if claimed:
            with auth.db().transaction() as tx:
                for row in claimed:
                    tx.execute("UPDATE assistant_pending SET used_at = NULL WHERE token_hash = ?", (row["token_hash"],))
        return jsonify(error="This preview has expired or has already been used. Ask the assistant again."), 409
    if data["action"] == "cancel":
        return jsonify(results=[{"ok": False, "cancelled": True, "summary": row["summary"]} for row in claimed],
                       message="Cancelled. Nothing was changed.")
    import server  # server imports this module; the shared save path lives there
    results, saved_by_me = [], {}
    for row in claimed:
        params = json.loads(row["params"])
        revisions = params.pop("revisions")
        try:
            with auth.db().transaction() as tx:
                classes = load_classes(tx)
                for cid, rev in revisions.items():
                    if cid not in classes or classes[cid][1] != rev + saved_by_me.get(cid, 0):
                        raise Stale("The classroom changed since the preview.")
                lines, changed = apply_change(row["kind"], params, classes)
                summary = "; ".join(lines)
                for cid, state in changed.items():
                    if server.update_classroom(tx, cid, state, classes[cid][1], g.user["id"], via="assistant",
                                               change=summary[:500]) is None:
                        raise Stale("The classroom changed since the preview.")
            for cid in changed:
                saved_by_me[cid] = saved_by_me.get(cid, 0) + 1
            results.append({"ok": True, "summary": "\n".join(lines)})
        except (Stale, ToolError, ValueError) as e:
            results.append({"ok": False, "summary": row["summary"],
                            "error": f"Not changed: {e} Ask the assistant to try again." if isinstance(e, Stale)
                            else f"Not changed: {e}"})
    done = [r for r in results if r["ok"]]
    with auth.db().transaction() as tx:
        log(tx, None, [row["kind"] for row, r in zip(claimed, results) if r["ok"]], change_confirmed=bool(done))
    message = ("Done. " if done else "") + ("It can be undone from Class history (saved versions)." if done else
                                            "Nothing was changed.")
    return jsonify(results=results, message=message)


# --- Admin page: requests ---------------------------------------------------------------------------------------------

@bp.get("/admin/requests")
@auth.admin_required
def requests_page():
    with auth.db().transaction() as tx:
        rows = [dict(r) for r in tx.execute(
            "SELECT r.id, r.name, r.created_at, r.category, r.summary, r.status, r.updated_at, u.name AS updated_by_name, "
            "w.email FROM assistant_requests r LEFT JOIN users u ON u.id = r.updated_by LEFT JOIN users w ON "
            "w.id = r.user_id ORDER BY CASE WHEN r.status = 'new' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC "
            "LIMIT 300").fetchall()]
    return auth.page("admin_requests.html", items=rows, statuses=STATUSES,
                     categories={"feature": "Feature", "change": "Change", "problem": "Problem", "other": "Other"})


@bp.post("/admin/requests/<int:item>/status")
@auth.admin_required
def set_status(item):
    auth.check_form_token()
    status = request.form.get("status")
    if status not in STATUSES:
        abort(400)
    with auth.db().transaction() as tx:
        if not tx.execute("UPDATE assistant_requests SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                          (status, g.user["id"], auth.now(), item)).rowcount:
            abort(404)
        auth.audit(tx, "assistant.request_status", detail={"item": item, "status": status})
    from flask import redirect
    return redirect(f"/admin/requests#req-{item}", code=303)


def prune(database, days):
    """Removes assistant log entries past their retention, and spent or expired previews."""
    t = auth.now()
    with database.transaction() as tx:
        tx.execute("DELETE FROM assistant_log WHERE at < ?", (t - days * auth.DAY,))
        tx.execute("DELETE FROM assistant_pending WHERE expires_at < ?", (t - auth.HOUR,))
