# Ilmi

Ilmi (علمي) is a madrasah manager for Qur'an lessons, memorisation, classes and house points. An admin can
set the site title shown under Ilmi in the top left corner of every page (Account, Site title); with none, pages
show just Ilmi.

One page holds the school overview (one Summary page, with Classes and Record beside it) and a full class
dashboard (Quran, Memo, Progress, Settings) for every
classroom. The dashboard starts empty:
classrooms and students are added by uploading a CSV on the overview.

**Railway deployment:** see [RAILWAY.md](RAILWAY.md) for the variables, staff
accounts and roles, email, history and backups. Staff sign in with their own
email and password; teachers see only their own classrooms, and the school's
Office (the account in charge of its settings) sees everything.

**Many schools:** one site serves many schools, each with a database of its
own. The developer account (`DEVELOPER_EMAIL`) sees every school as a tile,
adds schools and sets each one's Office account, and opens any of them as its
Office, HeadTeacher or a Teacher would see it. See [Schools and the developer account](RAILWAY.md#schools-and-the-developer-account).

## Adding students

On the overview (Classes tab), an administrator's **Upload students from a CSV file** takes a CSV file with one row per
student and these six columns, with the column names in the first row:

```csv
Student ID,Student Name,Gender,School Year Group,Current Teacher [Quran and Memorisation],Current Teacher [Islamic Studies]
ST001,Aisha Khan,Female,Year 3,Yr3-A-QM-Br Ahmed,Yr3-A-AI-Sr Maryam
ST002,Yusuf Ali,Male,Year 4,Yr4-B-QM-Sr Zainab,Yr4-B-AI-Br Idris
```

- **Current Teacher [Quran and Memorisation]** is the student's classroom.
  Classrooms that don't exist yet are created, with the first class day
  chosen next to the file.
- **Student ID** identifies the student on later uploads. If their Quran and
  Memorisation teacher changes, the student moves to the new classroom with all
  of their passes, merits, warnings and Quran scores. Changed details (name,
  gender, year group, Islamic Studies teacher) are updated. Nobody is added
  twice.
- **Student Name**, **Gender**, **School Year Group** and **Current Teacher
  [Islamic Studies]** are saved with the student and shown in each
  student's details (the row's "…" menu and the Progress students table); the
  Student ID, madrasah year group and house also appear in the student's record.
- The **madrasah year group** is taken from the classroom name: `Yr7-B-QM-...` is
  Year 7. A shared `Yr9/10` classroom takes the year from the Islamic Studies
  class (`Yr9-AI-...` or `Yr10-AI-...`). An optional **Madrasah Year Group** column
  (older files call it Safar Year Group) overrides this. The **School Year Group** (the student's primary or secondary
  school year) is kept separately.
- Columns are found by their names, in any order. Student Name and the Quran
  and Memorisation teacher are required. Without a Student ID column, students
  are matched by name within their classroom.
- Before anything changes, a preview lists each classroom with the students
  who are new, moving in, updated or unchanged, the students on the dashboard
  who aren't in the file (they are left as they are), and any problems, such
  as one Student ID used for two different names.
- Commas, semicolons or tabs between columns all work.

## House points

Every student belongs to one of three houses: **Makkah**, **Al-Aqsa** or
**Madinah**. Houses come from an optional **House** column in the student CSV.
Spellings are matched loosely ("al aqsa", "Medina"), and anything else is
listed in the upload preview and left unchanged. A later upload with a House
column updates each student's house; one without it leaves houses as they are.

Only admins give house points. Teachers hand students physical house coins, students put them in the
box at the office, and an admin counts them and adds each house's count on **Counted coins**
(`/admin/house-points`, with the day counted), or gives a student points on the Record page's House points tab.
The server refuses a teacher's save that adds a coin. Tokens on students count:

| Token | Points |
| --- | --- |
| Green | 5 |
| Blue | 20 |
| Red (book completion) | 50 |

The Record page still records Quran level tests (Qaidah Book levels 1 to 13, Tajweed Book levels 14 to 20),
Memo tests and book passes (Qā'idah, Qā'idah Part 1, Qā'idah Part 2, Rules of Tajwīd, Memorisation Book 1 or
Memorisation Book 2), but none of them gives house points. Its House points
tab is for admins only (HeadTeachers don't see it, and the server refuses their coins) and can be hidden with `HPOFF` in `src/page.html`.
A Memo pass chosen there by **Item number and Surah** (the school's list: Fail, L1 Target 1 to L10 Target 14,
and items 197 to 230) is logged as a pass in the same way.
The overview's House points pop-up shows the whole school's totals, and on
Railway teachers see those totals too, without seeing other classes'
students. The points are set in `TOK` near the top of the script in
`src/page.html` and in `TOKEN_POINTS` in `server.py`; change both together.

## After the book: the Juz items

The book ends at L10 T14. After that, targets carry on through numbered items,
starting from Juz 30 and working back to Juz 1: `198 An-Naba` (Juz 30) through
`234 An-Nas`, then `235 Al-Mulk` (Juz 29), and so on to `332 Al-Baqarah`
(Juz 1). **+ Pass** moves a student to the next item, just like a book target,
and the target lists group the items by Juz. The items are listed in `JUZ` near
the top of the script in `src/page.html`. A surah that spans two juz (for
example Adh-Dhariyat) has an item in each.

Each classroom's Settings tab can rename it (**Rename class**), set its first
class day, add students by hand, and delete the classroom.

A classroom can be opened directly by adding its code to the end of the
dashboard's link, for example `#yr3-a-qm-br-ahmed` for "Yr3-A-QM-Br Ahmed". The code is the classroom's
name when it was created, in lower case with dashes.

## Where records are kept

| How the page is opened | Records are kept in |
| --- | --- |
| Railway (`server.py`) | The `classrooms` table in Railway PostgreSQL |
| Claude artifact | The artifact's database, collection `classroom` |
| `Master_Dashboard_standalone.html` opened as a file | That browser only |

Records from the earlier version, which shipped with fixed classes, have been
deleted: the Railway server removes its old `classes` table when it starts.

## Files

- `Master_Dashboard_standalone.html`: the dashboard as a complete web page.
  The Railway server serves this file.
- `Master_Dashboard.html`: the version published as a Claude artifact (the
  artifact host adds the page skeleton).
- `src/page.html`: the page's markup and script. `LEVELS` at the top of the
  script sets how many targets each level has (L1 to L10).
- `src/tokens.css`: the shared design tokens, application shell and controls,
  used by the dashboard and the admin pages. See [DESIGN.md](DESIGN.md).
- `src/template.css`, `src/logo.b64`: the dashboard screens' styles and the logo taken from the class
  dashboard template.
- `railway-storage.js`: shared storage for the page when it is served by
  Railway.
- `server.py`, `test_server.py`: the Railway server and its tests.
- `auth.py`, `templates/`, `static/auth.css`: sign-in, accounts, roles and the
  admin pages. `db.py`: database access and migrations. `schools.py`,
  `templates/schools.html`, `static/developer.css`: the schools, each with its
  own database, and the developer's pages. `mailer.py`: sends
  email through Resend. `emails.py`, `templates/email/`: what each email
  says (see [RAILWAY.md](RAILWAY.md#email)).
- `tv.py`, `static/tv.html`: the TV dashboard and its screen links (see
  [RAILWAY.md](RAILWAY.md#tv-dashboard)).
- `assistant.py`, `assistant_knowledge.md`, `static/assistant.js`, `static/assistant.css`: Ask Ilmi, the assistant's
  chat panel, its tools and the knowledge base it answers from (see [RAILWAY.md](RAILWAY.md#ask-ilmi-the-assistant)).
- `tools/build.py`: rebuilds both HTML files from `src/`. Run
  `python3 tools/build.py` after editing `src/page.html`, and commit the
  generated files.
