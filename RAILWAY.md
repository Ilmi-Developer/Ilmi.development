# Deploy Ilmi on Railway

The Railway version is a Flask/Gunicorn web server with a sign-in page, staff
accounts and shared class records in Railway PostgreSQL. The HTML files can
still be used in Claude or opened locally with their original storage
behaviour.

Setup: one web service that deploys the `main` branch of this repository, and a
**Postgres** service. Everything specific to a school (accounts, pupils, the
site title, email addresses, keys) is kept in Railway variables or in the
database, never in this repository.

## Schools and the developer account

One site serves many schools, and each school has a database of its own
(`schools.py`, `db.py`). In the one Postgres service that is a schema per
school: the first school keeps the tables the site always had (in `public`),
the control database is the schema `ilmi_control`, and each school added later
gets `ilmi_school_2`, `ilmi_school_3` and so on. A school's classes, students,
staff accounts, settings, history, FAQs and TV links are only in its own
schema, and a request only ever opens the schema of the school its sign-in or
link belongs to. Run locally without `DATABASE_URL`, each is a SQLite file in
`DATA_DIR` (`dashboard.sqlite3`, `control.sqlite3`, `school-2.sqlite3`).

- Everyone signs in on the same page. The email says which school: an email
  has one account across every school, so the same address can't be invited at
  two schools.
- Each school is run by its **Office** (called `admin` in the code, and
  "Administrator" before November 2026): the person in charge of that school's
  settings, accounts and records. Wherever this page says admin, it means a
  school's Office.
- The **developer** is one account, for the email in `DEVELOPER_EMAIL`, kept
  in the control database. It belongs to no school. Nobody can be given this
  role: it isn't a choice on Manage users and the server refuses it. Changing
  or removing the variable turns the earlier developer account off at the next
  start.
- The developer signs in like an Office account (password, then the emailed
  code) and lands on **Schools**: every school as a tile with its classes,
  students, staff and who its Office is. **Add a school** makes an empty
  database for it and, with a name and an email, its Office account.
  **Manage** on a tile sets that school's Office account or renames it.
- Setting a school's Office account: someone new gets an account at that
  school and an invite link to choose their password (emailed when email is
  set up, otherwise shown to copy; it expires after 72 hours). Someone who
  already has an account at that school is given the Office role and keeps
  their password. An email with an account at another school is refused. To
  turn an Office account off, open the school and use **Manage users**.
- Pressing a tile opens that school as its Office sees it. A strip above the
  page, with its controls at the top right, switches the view between
  **Office**, **HeadTeacher** and **Teacher** and leads back to **All
  schools**. Everything under the strip is the page as that role sees it. In the Teacher view the
  developer has every classroom, with a teacher's permissions. The view only
  changes what the developer sees; it is not an account anyone else can use.
- What the developer does inside a school is recorded in that school's own
  Activity and Class history under their name. Their place in the school's
  accounts is a stand-in that can't sign in and isn't listed on Manage users.
- A school added without an Office has no accounts until one is set. Once it
  has an Office, the Office invites its own staff from **Manage users**.
- The first time the site starts with `DEVELOPER_EMAIL` set, an existing
  account with that email becomes the developer account: it keeps its name,
  password and picture, and its history at its school stays under its name.
  If no account has that email, one is created with `DASHBOARD_PASSWORD` when
  that is set (to be changed at the first sign-in); otherwise use
  **Forgotten your password?** to choose a password by email.
- Links and sign-in cookies start with their school's number (`/tv/2.…`).
  Links and sign-ins from before there were schools have no number and belong
  to the first school, so TVs already on the wall keep working.
- The start page and the sign-in pages are Ilmi's own: they show just Ilmi,
  never a school's name. A school's title shows once someone from that school
  has signed in.
- The first school's weekly Help or Requests summary still goes to
  `HELP_SUMMARY_TO`; every other school's goes to its own Office accounts.

## Accounts and access

- The first page is the sign-in page. Everyone signs in with their own email
  and password.
- **The Office** sees and edits every classroom at its school, uploads CSVs,
  creates and deletes classrooms, and manages accounts (**Manage users**), the
  **Class history** and the **Activity** log.
- **Teachers** see and edit only the classrooms the Office has ticked
  for them on **Manage users**. They can't upload CSVs, create classrooms or
  delete them. The server enforces this; the page only hides the buttons.
- **HeadTeachers** see and record on every classroom, like an administrator, and
  have Overview, Record, Classes, House points, Rankings, Presenter and
  **FAQs** (where they can reply, but not delete). They have none of the
  Administration pages: no Manage users, Students, Class history, Activity, TV
  screens, Counted coins, Spreadsheet or Import, and they can't add or remove
  students or classrooms. They sign in like teachers, without an emailed code.
- New staff are invited from **Manage users**. They get a link (by email when
  email is set up, otherwise the admin copies it) to choose a password. Invite
  links expire after 72 hours, reset links after 1 hour; each works once.
- Forgotten passwords: **Forgotten your password?** on the sign-in page emails
  a reset link. An admin can also make one from **Manage users**
  (**Send password reset link**). Passwords are stored only as argon2 hashes,
  so nobody, admins included, can see one. The person's old password keeps
  working until they use the link, which then signs them out everywhere else.
- Invite, reset, TV screen and memo board tokens are replaced with `[hidden]`
  in the request and error logs, so the Railway logs can't be used to take
  over an account or open a screen.
- Admins finish signing in with a 6-digit code emailed to them after their
  password. Setting `LOGIN_CODES` to `off` turns it off for a time when email
  can't be sent (admins then sign in with their password only). It works once, in that browser, for 10 minutes, and 5 wrong codes
  end it (10 wrong codes in 15 minutes block new ones). Teachers sign in with
  their password only. Without `RESEND_API_KEY` and `MAIL_FROM` no code can be
  sent, so admins sign in with their password and the log says so.
- Turning off an account signs that person out everywhere at once.
- Sign-in is limited to 5 wrong passwords per email and 20 per IP address in
  15 minutes. Closing the app signs you out: the sign-in cookie ends with the
  browser, and the server ends a sign-in 20 minutes after the dashboard was last
  open (an open dashboard checks in, even in a background tab), and after 12
  hours at most. Ticking "Stay signed in for 30 days" keeps the sign-in across
  browser restarts and new tabs for 30 days, ending sooner if the dashboard
  isn't opened for 14 days. Signing out or having the account turned off still
  ends it at once, and changing your password ends it on your other devices.
  For admins it also trusts that browser for 30 days: after typing the emailed
  code once with the box ticked, their password alone signs them in there, even
  after signing out. Changing or resetting the password, "Sign out other
  devices" and turning the account off stop other browsers being trusted.

## Help or Requests and FAQs

Teachers have a **Help or Requests** button at the bottom of their overview,
on a computer and on a phone. They choose **Help** or **Request**, type a
message and send it; their messages and the replies stay in the same pop-up,
and a red dot on the button shows a reply they haven't opened yet.

Messages land on **FAQs** in the admin menu. An admin types a reply and sends
it **in the app**, or **in the app and by email** (email needs `RESEND_API_KEY`
and `MAIL_FROM`). A reply can be changed later, and a message deleted.

Every Sunday at 12:00 London time a summary of the week's messages, answered
or not, is emailed to `HELP_SUMMARY_TO` (a Railway variable; without it, to
`ADMIN_EMAIL`). A week with no messages gets an email
saying so. The web service checks once a minute from a background thread and
records each week's email in the database, so it goes once, even after a
restart; if the site was down at noon it goes out when it's back, up to Monday
evening. No cron service is needed.

## Ask Ilmi (the assistant)

**Ask Ilmi is switched off** unless `ASSISTANT_ENABLED` is `on`. While it's off, its menu items, page, scripts,
Assistant requests page and endpoints are all gone (they answer 404), and nothing is sent to any AI service. The code,
its tables and logs stay, and the tests keep it working.

**To turn it back on with Claude:** create an API key in the Anthropic Console (console.anthropic.com → Settings → API
Keys, with a spend limit under Settings → Limits), then on the MySafar service set `ANTHROPIC_API_KEY` to it,
`ASSISTANT_ENABLED` to `on`, and optionally `ASSISTANT_MODEL` (`claude-haiku-5-5` costs about a tenth of a penny a
question; the default is `claude-opus-5-5`). Claude is used whenever its key is set; Gemini and Cloudflare only without it.

**Ask Ilmi** in the menu, for every signed-in member of staff: a chat page in the middle of the dashboard
(`/#ask`): a greeting and the box, then the conversation, with a thinking line that shows what it did. Typing
`/` at the start of the box offers shortcuts. It always answers at medium effort. It runs on the
server (`assistant.py`) and calls Anthropic's Messages API; the browser never talks to Anthropic.

- **Teachers and HeadTeachers** get a help assistant. It answers from `assistant_knowledge.md` (using the site, the
  syllabus, general questions about Islam) and can submit a request, after showing its summary and the person
  confirming. It has no student or classroom data and can't change anything: their questions are sent with the help
  tools only, decided on the server from the session.
- **Administrators** also get data tools: look up students, classes and passes, and preview changes (add, rename,
  house, year group, move, set target, record or correct a pass, archive to Ex students). The panel shows "Data tools
  on". Every change is a preview first; only pressing **Confirm** applies it, within 5 minutes, once, for that admin.
  It is saved the same way as the dashboard's saves (revision check, Class history, Activity, marked "via
  assistant"), and is cancelled if the class changed since the preview. Undo it from **Class history**.
- Requests land on **Assistant requests** in the admin menu, where admins mark them done or declined.
- 30 questions per person per hour (60 for admins), at most 3 answered at once. Teachers and HeadTeachers also have a
  daily token allowance, `ASSISTANT_DAILY_TOKENS` (100,000 by default; 0 turns it off), which resets at midnight
  London time. Each question's tokens (input, cache writes and output; not cache reads) are recorded in
  `assistant_log.tokens`. Each question is logged in
  `assistant_log` (who, role, when, the question, the tools used, and whether a request was submitted or a change
  confirmed), kept for `ASSISTANT_LOG_DAYS`. The assistant's answers are never stored. The conversation lives only
  in the browser tab.
- `assistant_knowledge.md` is loaded into the assistant's instructions on every question (cached). Edit it, merge,
  and the next deploy uses it.

**Which AI answers.** With `ANTHROPIC_API_KEY` set, Claude. Otherwise, with `GEMINI_API_KEY` set (from Google AI Studio → **Get API key**), Ask Ilmi uses Google's Gemini
Flash on the Gemini API's free tier, which has per-minute and daily limits; when one is reached Ask Ilmi says to try again
in a minute, or tomorrow. Check Google's Gemini API terms for how the free tier uses what's sent. Otherwise, with
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` set, Ask Ilmi uses Google's Gemma 4 on
Cloudflare Workers AI, which is free up to Cloudflare's daily allowance (10,000 "neurons", roughly 100 questions a
day for the whole school; it resets at 00:00 UTC, and then Ask Ilmi says it's back after midnight). Cloudflare doesn't
keep the questions or train on them. Otherwise it uses Claude with `ANTHROPIC_API_KEY` (paid per use).
`ASSISTANT_PROVIDER` (`anthropic`, `gemini` or `cloudflare`) chooses explicitly when more than one is set; otherwise
Claude comes first, then Gemini, then Cloudflare.

**Questions about the records (admins).** For each admin question Ask Ilmi builds a private, read-only SQL copy of the
records in memory (classes, students, passes, fails, level tests, book completions; `assistant_records.py`), with
positions already written as "L4 T1" or "205 Al-Buruj". The model can run any SELECT on that copy, so it can answer
counts, lists and comparisons the fixed tools don't cover. The copy has no dates of birth, Family IDs, gender or
Student IDs, can't be written to, and is thrown away after the question; changes still go only through the preview
and **Confirm**. Teachers and HeadTeachers never get it.

Without either set of keys the panel says the assistant isn't set up yet; nothing else changes.

### Turning Ask Ilmi off, or undoing it

- **Off at once, no deploy:** delete `ANTHROPIC_API_KEY` from the MySafar service's variables. Ask Ilmi then says it
  isn't set up, and nothing is sent to Anthropic.
- **Remove it from the site:** on GitHub, open the merged pull request that added the assistant (then called
  Ask Ilmi) and press **Revert**, then merge the
  pull request that makes. (By hand: `git revert -m 1 <merge commit>` on `main`.) The site before Ask Ilmi is commit
  `93a6659` on `main`.
- **The database after an undo:** Ask Ilmi only added tables (`assistant_log`, `assistant_requests`,
  `assistant_pending`) and the `tokens` column; the earlier code ignores them, and no classroom record changes. Leave
  them, so merging Ask Ilmi again carries on where it was, or drop the three tables once you're sure.

## TV dashboard

A TV in the admin area can show a live dashboard without anyone signing in on
it. An administrator opens **TV screens**, names the TV and makes a link, then
opens that link on the TV once and leaves it open. The link works on its own
and shows only the dashboard: it can't open the classrooms or change anything.
**Turn off link** stops it within a minute (the TV then shows a message); make
a new one if a link is lost or seen by someone it shouldn't be.

The dashboard changes slide every 30 seconds and updates every minute. Each
slide is a flat cartoon still from an Islamic place or story, kept moving: the
Kaaba in Makkah as it was in the Prophet's time (an open valley ringed by
mountains, people going round the Kaaba, the well of Zamzam, mud-brick houses
with washing tied out on the lines), Masjid an-Nabawi with its umbrellas opening
and closing, Masjid al-Aqsa and the Dome of the Rock, Cave Hira on Jabal al-Nour
under shooting stars, Cave Thawr with the spider's web and the dove's nest, the
Ark of Nuh after the flood, the sea parting for Musa, and the splitting of the
moon. Washing, palms, cypresses and the web move in a gusty wind with a little
physics and the Ark floats on the swell. House points is a festival with bunting
and fireworks. The podium steps rise in order: first, then second, then third,
and always stand below the title: when several are tied, the names shrink to fit.

Classes are shown by year group, class letter and teacher, with the title in
full: Sr, Sister, Ustadha and Mrs/Ms/Miss become Ustadha, every other title (Br,
Ustd...) Ustadh. `Yr3-A-QM-Br Tariq` shows as "Yr3A Ustadh Tariq", on the TV,
the Presenter and the dashboard (the stored name is unchanged, so Rename and the
CSV upload still use it). On the top-class podiums the teacher's name is the big
line and "Yr3A" sits under it.

The TV plays four slides:

1. **Qaidah level and book passes**, one board: first place for Qaidah levels
   passed this month (Quran level tests on the Record page, everyone tied on
   the most), then everyone who finished a book this month, in boxes of the
   same size. If it doesn't all fit, it stays on first place for a while, moves
   on to the books, and reaches the bottom 29 seconds in.
2. **Top Class for Memorisation (Primary Years)**: a podium of the class
   teachers whose classes passed the most memorisation this month (Years 2 to
   6; a Year 1 or Reception class counts as primary). Ties share a step.
3. **Top Class for Memorisation (Secondary Years)**: the same for Years 7 to 10
   and any later year. A class's stage comes from its name (`Yr7-...`),
   otherwise from most of its students' madrasah or school year groups.
4. **House points**: a podium of all three houses with their rank, points and
   a black silhouette logo (the Kaaba for Makkah, Masjid an-Nabawi for Madinah,
   the Dome of the Rock for Al-Aqsa), at a festival.

Empty slides are skipped. **Settings** on each TV sets a daily time for
the Qaidah slide only and one for the Memo slides only (London time; a window can run
past midnight). Outside those times the TV plays every slide.

Students are shown by first name and surname initial (`Aisha K.`) with their
classroom. Signed-in staff can preview it at `/tv`.

### Presenter

**Presenter**, at the top of a class, shows that class's month on the big screen in the
same scenes, with the class's students riding through by name (the TV has no riders). Teachers
can present their own classes and admins any class. Its slides are podiums for the best Quran average
(students with 3 or more scores this month), Qaidah levels, house points earned,
the class's house race (with the house logos) and merits; memorisation and book passes on
one board like the TV's; and the month's stars of the
week on one page. **Full screen** and **Close** show when the mouse moves;
Close (or Escape) goes back to the class.

Like a TV screen, the Presenter opens by link and needs no sign-in once open. Each press of
Presenter makes a new link (`/present/<random>`) and opens it in a new tab. Only the tab that
opened it can use it, and it keeps working there (signed in or not) until the tab is closed;
press Presenter again for a new link. There is no sharing by link and code. A link nobody opens within 10 minutes stops working, as
does one whose tab has been quiet for 12 hours, or whose teacher has been taken off the class.

## Variables for the web service

| Variable | Value |
| --- | --- |
| `PORT` | `8080` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `DEVELOPER_EMAIL` | The one developer account's email: it sees every school and adds schools (see [Schools and the developer account](#schools-and-the-developer-account)). Without it there is no developer and the site runs its one school as before. |
| `ADMIN_EMAIL` | The first school's first Office account's email. Used only when that school has no accounts yet; not needed when `DEVELOPER_EMAIL` is set. |
| `DASHBOARD_PASSWORD` | The setup password for that first Office account, or for a new developer account. Used only when the account doesn't exist yet; they must choose a new one at first sign-in. Delete it from Railway once they have. |
| `ADMIN_RESET_PASSWORD` | Only for an admin who is locked out while email isn't set up. On the next start, the `ADMIN_EMAIL` account gets this password (changed at the next sign-in), is turned back on and unblocked, and is signed out everywhere. Each value is used once. Remove it once signed in. |
| `RESEND_API_KEY` | A sending-only API key from [Resend](https://resend.com), limited to your sending domain. Optional. |
| `LOGIN_CODES` | `off` stops emailing admins a sign-in code after their password. Optional; on by default. |
| `MAIL_FROM` | The sender, such as `Ilmi <dashboard@example.org>` (the name before the address is what staff see as the sender). Its domain must be verified in Resend. |
| `MAIL_REPLY_TO` | Where replies to the emails go. Optional; defaults to `SUPPORT_EMAIL`. Without either, replies go to the sending address, which doesn't receive mail. |
| `MAIL_REDIRECT_TO` | Testing only: every email goes to this address instead of the real recipient. Remove it to go live. |
| `PUBLIC_URL` | The site's address, used in email links and for the logo in emails, such as `https://example.org`. Change it if the address changes. |
| `HELP_SUMMARY_TO` | Who gets the Sunday 12:00 Help or Requests summary. Optional; defaults to `ADMIN_EMAIL`. |
| `RETENTION_DAYS` | How long class history and the activity log are kept. Default `14`. |
| `ASSISTANT_ENABLED` | `on` shows Ask Ilmi (the assistant) to staff. Off by default: then it's gone from the site and nothing is sent to any AI service. |
| `ANTHROPIC_API_KEY` | An API key from the Anthropic Console, for Ask Ilmi. Without it the assistant panel says it isn't set up. |
| `ASSISTANT_MODEL` | Optional. On Gemini, a model starting `gemini` (default `gemini-flash-latest`); on Cloudflare, one starting `@cf/` (default `@cf/google/gemma-4-26b-a4b-it`); on Claude, the model (default `claude-opus-5-5`). |
| `ASSISTANT_LOG_DAYS` | How long the assistant's question log is kept. Default `90`. |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID (Cloudflare dashboard, Workers AI or the account home page). With the token, Ask Ilmi uses Gemma on Workers AI for free. |
| `CLOUDFLARE_API_TOKEN` | A Cloudflare API token with **Account → Workers AI → Read** permission only. |
| `GEMINI_API_KEY` | A Gemini API key from Google AI Studio. When set, Ask Ilmi uses Gemini Flash (free tier). |
| `ASSISTANT_PROVIDER` | Optional: `gemini`, `cloudflare` or `anthropic`, when more than one is set. |
| `ASSISTANT_DAILY_TOKENS` | Each teacher's and HeadTeacher's daily Ask Ilmi allowance in tokens. Default `100000`; `0` turns it off. Admins have no daily allowance. |
| `SUPPORT_EMAIL` | The school office address staff should write to for password help, for example `office@example.org`. The password pages show an **Email** button for it. Optional; without it they say to ask an administrator. |

`DASHBOARD_USERNAME` is no longer used and can be deleted.

## Email

Railway's Hobby plan blocks outbound SMTP, so email is sent over HTTPS
through Resend, from your verified sending domain. Without
`RESEND_API_KEY` and `MAIL_FROM` everything still works; admins copy invite
and reset links from **Manage users**.

| Email | Sent when |
| --- | --- |
| Invite | An admin invites someone or sends a new invite. It names the admin, the role and a teacher's classrooms. |
| Password reset | Someone uses **Forgotten your password?**, or an admin sends a reset link. |
| Password changed | A password is changed on **Account** or with a reset link: a security notice, so someone finds out if it wasn't them. |
| Test email | An admin presses **Send me a test email** on **Manage users**. |

Each email has a designed HTML part and a plain-text part with the same
words. The wording is in `templates/email/` and the subjects in `emails.py`.
The **Email** section at the bottom of **Manage users** shows whether email is
on and the sending address, and links to a preview of every email.

- Keep click tracking **off** for your sending domain in Resend. It would rewrite
  the one-time links, and Resend would see them.
- Each send is tagged (`category: invite`, `password_reset`, `security`,
  `test`), so Resend's **Emails** page can be filtered by kind.
- A send that Resend refuses for being busy or briefly down is retried once,
  without risk of a duplicate. Failures are written to the MySafar service's
  logs as `Email to ... was not sent`.
- Reset requests are emailed from a background thread, so the page takes as
  long whether or not the email has an account.

## Records, history and backups

- Each classroom is a row of its school's `classrooms`. Accounts, sessions and
  the history live in other tables of the same school's database (see `db.py`).
  Every school's schema is in the one Postgres service, so one backup covers
  them all.
- Database changes are numbered migrations in `db.py`, applied once each at
  startup and recorded in `schema_migrations`. They only add tables; existing
  classroom records are left as they are.
- Every save is kept in **Class history** for `RETENTION_DAYS`. Saves by the
  same person within 15 minutes share one entry. An admin can restore any
  version, or bring back a deleted classroom (teachers then need to be ticked
  for it again).
- **Spreadsheet** in the admin menu downloads every class's records as an Excel
  workbook (`export.py`): a Summary sheet, then Students, Classes, Quran scores,
  Memo passes and fails, Level tests, Rewards, Stars of the week, Warnings and
  Staff, each with filters and a frozen header row. Only admins can open it,
  and each download is listed in **Activity**.
- Back up the database too: turn on scheduled backups in the Postgres service's
  **Backups** tab. Deleting the Postgres service or its volume deletes the live
  records. Redeploying or deleting the MySafar service does not.

## Run and verify locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ADMIN_EMAIL=you@example.com DASHBOARD_PASSWORD='a-local-setup-password'
export COOKIE_SECURE=0   # plain http://localhost; never set this on Railway
export PORT=8080
# Either point at a PostgreSQL database...
export DATABASE_URL=postgresql://user:password@localhost:5432/ilmi
# ...or leave DATABASE_URL unset to keep records in a local SQLite file in .local-data
.venv/bin/gunicorn --config gunicorn.conf.py 'server:create_app()'
```

```sh
python3 tools/build.py
.venv/bin/python -m unittest -v test_server
# Also run the tests against PostgreSQL. They work in a temporary schema
# that is dropped afterwards, so existing tables are not touched.
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/ilmi .venv/bin/python -m unittest -v test_server
```

GitHub Actions runs the same tests on every pull request and push to `main`
(`.github/workflows/ci.yml`).
