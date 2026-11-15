"""School setup: the steps a new madrasah's admin takes to make Ilmi their own, and where they change them later.

A school made by signing up (billing.py) carries the mark site_settings "setup" = "pending", and its admin's first
sign-in opens /admin/setup instead of the dashboard. The steps, each a page of its own with Back, Save and continue
and Skip:

    look      the look new staff accounts start with (one of the built-in themes; each person can still change theirs)
    houses    whether the school has houses, what they are called, and whether house points are counted
    options   the parts of Ilmi the school uses: Finance, Presenter and TV screens, volunteer requests, Ask Ilmi
    links     the links at the foot of the menu
    students  how to bring the students in: a CSV or Excel file on the dashboard's uploader, with its template

Finishing (or skipping) removes the mark. Every admin has School setup under Administration afterwards, where the
same pages change the same choices. The choices themselves are school_config.py's.

Renaming a house renames it on every student who has it and on the coins counted for it, in one go, so points
follow the new name. Turning houses off hides them everywhere and deletes nothing: students keep their house, and
turning houses back on shows them again.
"""
import csv
import io
import json
import zipfile

from flask import Blueprint, abort, g, redirect, request

import auth
import school_config
import site_settings
import themes

bp = Blueprint("school_setup", __name__)

STEPS = (("look", "Look"), ("houses", "Houses"), ("options", "Options"), ("links", "Links"), ("students", "Students"))
KEYS = tuple(k for k, _ in STEPS)
XLSX_MAX_ROWS, XLSX_MAX_COLS = 5000, 40


def pending():
    """Whether the current school's admin still has setup to go through."""
    if "setup_pending" not in g:
        with auth.db().transaction() as tx:
            g.setup_pending = site_settings.read(tx, "setup") == "pending"
    return g.setup_pending


def default_theme(tx):
    """The look the school chose for new accounts, if it is still one Ilmi has."""
    key = school_config.read(tx)["theme"]
    return key if key in themes.BUILT_IN or key in themes.PRESETS else themes.DEFAULT


def apply_default_theme(tx, user_id):
    """Gives a new account the school's look. Called where staff accounts are made."""
    key = default_theme(tx)
    if key != themes.DEFAULT:
        tx.execute("UPDATE users SET theme = ? WHERE id = ?", (key, user_id))


def rename_houses(tx, old, new):
    """Moves every student, and every counted coin, from each old house name to the name now in its place. Returns
    how many students changed. A house that was removed (fewer houses than before) is cleared from its students."""
    mapping = {a: (new[i] if i < len(new) else "") for i, a in enumerate(old)}
    mapping = {a: b for a, b in mapping.items() if a != b}
    if not mapping:
        return 0
    changed = 0
    # Through a name nothing else has, so swapping two houses' names doesn't fold them together.
    for index, (a, b) in enumerate(mapping.items()):
        tx.execute("UPDATE house_points SET house = ? WHERE house = ?", (f"~moving~{index}", a))
    for index, (a, b) in enumerate(mapping.items()):
        if b:
            tx.execute("UPDATE house_points SET house = ? WHERE house = ?", (b, f"~moving~{index}"))
        else:
            tx.execute("DELETE FROM house_points WHERE house = ?", (f"~moving~{index}",))
    import server  # the dashboard's own way of saving a classroom, with its history; server imports this module
    for row in tx.execute("SELECT id, json, revision FROM classrooms").fetchall():
        state, touched = json.loads(row["json"]), 0
        for student in state.get("S", []) + state.get("X", []):
            if isinstance(student, dict) and student.get("house") in mapping:
                target = mapping[student["house"]]
                if target:
                    student["house"] = target
                else:
                    student.pop("house", None)
                touched += 1
        if touched:
            changed += touched
            encoded = server.encode_state(state)
            tx.execute("UPDATE classrooms SET json = ?, revision = ? WHERE id = ?", (encoded, row["revision"] + 1, row["id"]))
            server.record_history(tx, row["id"], row["revision"] + 1, encoded, g.user["id"], via="setup",
                                  change="Houses renamed in School setup")
    return changed


def xlsx_to_csv(data):
    """The first sheet of an Excel workbook as CSV text, for the dashboard's student uploader. Raises ValueError with
    a sentence for the person when the file can't be used."""
    if not data or data[:2] != b"PK":
        raise ValueError("That isn’t an Excel .xlsx file. Save the list as .xlsx or .csv and try again.")
    try:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError, EOFError) as error:
        raise ValueError("That Excel file couldn’t be opened. Save it again as .xlsx or .csv and try again.") from error
    try:
        sheet = book.worksheets[0] if book.worksheets else None
        if sheet is None:
            raise ValueError("That workbook has no sheets.")
        out = io.StringIO()
        writer, kept = csv.writer(out, lineterminator="\n"), 0
        for row in sheet.iter_rows(values_only=True, max_col=XLSX_MAX_COLS):
            cells = ["" if v is None else _cell(v) for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            kept += 1
            if kept > XLSX_MAX_ROWS:
                raise ValueError(f"That sheet has more than {XLSX_MAX_ROWS} rows. Split it into smaller files.")
            writer.writerow(cells)
        if not kept:
            raise ValueError("The first sheet of that workbook is empty.")
        return out.getvalue()
    finally:
        book.close()


def _cell(value):
    """A cell as the text someone would have typed: whole numbers without .0, dates day first."""
    import datetime
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime.date):
        return value.strftime("%d/%m/%Y")
    return " ".join(str(value).split())


def _page(step, status=200, **context):
    config = school_config.get()
    with auth.db().transaction() as tx:
        counts = {}
        for row in tx.execute("SELECT json FROM classrooms").fetchall():
            for student in json.loads(row["json"]).get("S", []):
                house = student.get("house", "") if isinstance(student, dict) else ""
                counts[house] = counts.get(house, 0) + 1
    built = [{"key": k, "name": themes.BUILT_IN[k][0] if k in themes.BUILT_IN else themes.PRESETS[k]["name"],
              "tile": themes._static_tile(k)} for k in themes.ORDER]
    index = KEYS.index(step)
    context.setdefault("houses_draft", config["houses"] + [""] * (school_config.MAX_HOUSES - len(config["houses"])))
    context.setdefault("links_draft", config["links"] + [{"label": "", "url": ""}] * (school_config.MAX_LINKS - len(config["links"])))
    return auth.page("setup.html", status, step=step, steps=STEPS, index=index, first_time=pending(), cfg=config,
                     themes_built=built, modules=school_config.MODULES, students=sum(counts.values()),
                     with_house=sum(n for h, n in counts.items() if h), house_counts=counts,
                     prev=KEYS[index - 1] if index else None, max_houses=school_config.MAX_HOUSES,
                     min_houses=school_config.MIN_HOUSES, house_max=school_config.HOUSE_NAME_MAX,
                     link_label_max=school_config.LINK_LABEL_MAX, link_url_max=school_config.LINK_URL_MAX,
                     assistant_site=bool(_assistant_on()), **context)


def _assistant_on():
    import assistant
    from flask import current_app
    return assistant.enabled(current_app)


def _next(step):
    index = KEYS.index(step)
    return redirect(f"/admin/setup?step={KEYS[index + 1]}" if index + 1 < len(KEYS) else "/admin/setup?step=students", code=303)


@bp.get("/admin/setup")
@auth.admin_required
def page():
    step = request.args.get("step", "")
    return _page(step if step in KEYS else KEYS[0], notice="Saved." if request.args.get("saved") == "1" else None)


@bp.post("/admin/setup/<step>")
@auth.admin_required
def save(step):
    auth.check_form_token()
    if step not in KEYS and step != "finish":
        abort(404)
    if step == "finish":
        with auth.db().transaction() as tx:
            if site_settings.read(tx, "setup") == "pending":
                site_settings.write(tx, "setup", "")
                auth.audit(tx, "setup.finished")
        g.pop("setup_pending", None)
        return redirect("/#classes" if request.form.get("to") == "upload" else "/", code=303)
    config = json.loads(json.dumps(school_config.get()))
    changed = None
    try:
        if step == "look":
            key = request.form.get("theme", "")
            if key not in themes.BUILT_IN and key not in themes.PRESETS:
                raise ValueError("Choose one of the looks.")
            config["theme"] = key
        elif step == "houses":
            config["houses_on"] = request.form.get("houses_on") == "1"
            config["house_points"] = config["houses_on"] and request.form.get("house_points") == "1"
            if config["houses_on"]:
                typed = request.form.getlist("house")
                names = school_config.tidy_houses(typed)
                old = config["houses"]
                # A house keeps its place in the list, so its students follow a new name; one whose box was emptied
                # is removed, and the houses after it keep their own students.
                kept_old = [old[i] for i, v in enumerate(typed) if i < len(old) and site_settings.tidy(v)]
                changed = (old, kept_old, names)
                config["houses"] = names
        elif step == "options":
            for key in school_config.MODULES:
                config["modules"][key] = request.form.get(f"module_{key}") == "1"
        elif step == "links":
            config["links"] = school_config.tidy_links(zip(request.form.getlist("label"), request.form.getlist("url")))
    except ValueError as error:
        drafts = {}
        if step == "houses":
            typed = (request.form.getlist("house") + [""] * school_config.MAX_HOUSES)[:school_config.MAX_HOUSES]
            drafts = {"houses_draft": typed, "houses_on_draft": True, "house_points_draft": request.form.get("house_points") == "1"}
        if step == "links":
            pairs = list(zip(request.form.getlist("label"), request.form.getlist("url")))[:school_config.MAX_LINKS]
            drafts = {"links_draft": [{"label": a, "url": b} for a, b in pairs] +
                      [{"label": "", "url": ""}] * (school_config.MAX_LINKS - len(pairs))}
        return _page(step, 400, error=str(error), **drafts)
    moved = 0
    with auth.db().transaction() as tx:
        if changed:
            old, kept_old, names = changed
            # Houses whose box was emptied lose their students' membership; the rest map by position.
            removed = [h for h in old if h not in kept_old]
            if removed:
                moved += rename_houses(tx, removed, [])
            moved += rename_houses(tx, kept_old, names)
        school_config.write(tx, config)
        if step == "look":
            # The admin sees their choice at once, and so does anyone invited who hasn't signed in yet.
            if not g.get("developer"):
                tx.execute("UPDATE users SET theme = ? WHERE id = ?", (config["theme"], g.user["id"]))
            tx.execute("UPDATE users SET theme = ? WHERE password_hash IS NULL AND developer = 0", (config["theme"],))
        auth.audit(tx, "setup.saved", detail={"step": step, **({"students_moved": moved} if moved else {})})
    school_config.forget()
    if pending() or request.form.get("then") == "next":
        return _next(step)
    return redirect(f"/admin/setup?step={step}&saved=1", code=303)
