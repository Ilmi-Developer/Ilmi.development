# Journey 2 Jannah reader

Reads pupils' weekend attendance from the Journey 2 Jannah (J2J) admin area for Ilmi. It runs on Railway as its own
service, built from `j2j_sync/Dockerfile` (Playwright and Chromium), never on anyone's computer.

## Step 1: mapping the admin area (`explore.py`)

A one-off run that signs in and opens the admin pages without changing anything, to find where attendance lives. It
prints one `J2J-MAP` line per page to the service's logs: addresses, headings, tables' column names and row counts,
forms and filters, and the data requests each page makes. Pupil names and other free text are never printed (a value
shows only when it is a date, a number or a short status word such as "present").

Railway service settings: Dockerfile path `j2j_sync/Dockerfile`, restart policy Never. Variables, set by the school in
the Railway dashboard and never in this repository:

| Variable | What |
| --- | --- |
| `J2J_USERNAME` | The J2J admin account's username or email |
| `J2J_PASSWORD` | Its password |
| `J2J_LOGIN_URL` | Optional; `https://my.journey2jannah.com/login/` by default |
| `J2J_PAGES` | Optional; more paths to open, comma separated |
