# Ilmi design

Ilmi (علمي, "my knowledge") is a madrasah manager. The start page, the dashboard, the admin pages and
the TV display share one design system. The values live in `src/tokens.css` (inlined into the dashboard by
`tools/build.py` and served at the start of `/auth.css` and `/welcome.css`). The dashboard's own screens are styled
in `src/template.css`, the sign-in and admin pages in `static/auth.css`, the start page in `static/welcome.css`
(with `templates/welcome.html` and `static/welcome.js`), and the TV in `static/tv.html`.

## Direction

The November 2026 brief: move to an olive green and off-white colour scheme with a watercolour style, under the new
name Ilmi with its Arabic, علمي, and a new start page; keep everything animated and polished. The look comes from
the owner's reference image (its design features, not its content):

- **Paper and watercolour.** Pages are warm paper (`--canvas` with a faint paper tile, `/art/paper.webp`) with a
  pale sage-grey cloud drifting in the top right corner (`--page-wash`, `/art/wash-page.webp`). Olive surfaces (the
  menu, the start page's band, the sign-in welcome panel) carry darker olive washes. Washes are painted
  procedurally (`tools/art/wash.py`, fixed seeds) as WebP with soft, ragged edges and a darker dried rim.
- **Ink drawings.** A sprig, the Qur'an on its rahle, the feature icons, grass and a mosque skyline are inline SVG
  line drawings (`tools/art/ink.py` writes `templates/art/*.svg`; `tools/build.py` inlines `<!--ART:name-->` into
  the dashboard and Jinja includes them elsewhere). Every line has `pathLength="1"`, so CSS draws them on; the
  hidden state lives only in keyframes, so reduced motion leaves them visible.
- **Olive and cream.** The menu is olive (`#4b552f`) with cream text and a cream pill for the page you are on.
  Work sits in cream cards (`#fbf8f1`) with a hairline and a warm shadow. Olive (`#4d5a2c`) is the primary action
  and the selected marks. House, token, level and score colours keep their meaning; the score scale is a
  watercolour coral to leaf green.
- **Soft book type.** The wordmark, titles, figures and large italics in Fraunces (soft), علمي in Amiri, body text
  in Nunito (all self-hosted, SIL OFL). The wordmark is "Ilmi" with علمي beside it (`.ilmi`); the mark
  is the letter ʿayn (ع) on an olive disc (`tools/art/mark.js` and `mark.py`).

The tokens keep their earlier names, so `--navy` is now Ilmi's olive and the notes below that say navy mean that
primary colour.

The interface is light unless a person turns on Dark mode on Account; it never follows the OS setting. Dark is
olive-black: the paper texture and washes are off, cards are `#1e1f17`, and the primary is a brighter olive that
keeps white text at 4.5:1 or more. `--navy` is the fill and `--navy-text` is olive used as type (a pale olive in
dark). Pastel tints mix with `--paper` (the card colour), so they become dark tints; score and level chips keep
their own dark text. The sign-in, password and must-change pages are always light. Check every state for contrast
when adding colours: a hard-coded `#fff` or olive text on a surface is what breaks in dark.

Themes (Account, `themes.py`) build on the same tokens. Ilmi and Dark are
written by hand; Pastel pink, Midnight, Stealth, Coral and every theme a person
makes are four colours (menu, accent, background, cards) and an optional photo,
from which `themes.derive()` works out every token: body text 7:1 on the cards,
smaller text, links, headings and menu text 4.5:1, white on accent fills 4.5:1,
the logo (drawn as a mask in the accent) 3:1 on its disc. A chosen colour is
only moved, lighter or darker in the same hue, when no text colour could read
on it. Text straight on the page's background has its own tokens (`--canvas`,
`--c-ink`, `--c-ink-2`, `--c-mute`, `--c-title`), so a dark background can sit
behind light cards; use them for anything new placed outside a card. A photo
gets a shade of the canvas colour strong enough for the page titles to read on
97% of it, and the server bakes crop, blur, brightness, colour and shade into
the JPEG pages show. A theme's own colours (`html[data-logo]`) turn the paper, washes and cloud off and keep
its flat look. `data-mode="dark"` (not `data-theme`) is what dark-only
rules key off.

Daily work happens without scrolling: at 1280×720 the head and tabs take about
a sixth of the screen and a class of 20 is in view at once. Details are one
selection or one labelled button away: the selected student's panel shows only
the target and today's recording, with Details (identifiers, rename, remove)
and History behind buttons. Attention shows as the warning dot on a name in Quran
and the Concern group in Memo, never as a second student list. Each page has
one title; rules live in "?" help rather than paragraphs.

## Palette

| Token | Value | Use |
| --- | --- | --- |
| `--canvas` / `--bg` | `#f4efe4` / `#f3eee2` | Warm paper behind everything, under the paper tile and the page's cloud. |
| `--surface` / `--surface-2` / `--sunk` | `#fbf8f1` / `#f6f1e6` / `#ede6d6` | Cream cards, hover, neutral pills and the track of segmented controls. |
| `--line` / `--line-2` | `#e4dccb` / `#cfc5ae` | Hairlines; control borders and inactive chart columns. |
| `--ink` / `--ink-2` / `--mute` | `#22231b` / `#44453a` / `#67654f` | Text, secondary text, captions (all at least 4.5:1 on paper and cream). |
| `--rail-bg` | `#4b552f` | The olive menu, with darker washes at its foot. Text `#ece6d3`, muted `#cdc9ae`; the page you are on is a cream pill (`#f4efe2`) with olive text. |
| `--navy` / `--navy-soft` | `#4d5a2c` / `#e8e9d6` | Olive: the primary action, selected indicators and links (white text 7.4:1). |
| `--sage` / `-soft` / `-ink` | `#6b8a4e` / `#ebefdf` / `#3c5a2a` | Healthy states and the weekly pass bars. |
| `--gold` / `-soft` / `-ink` | `#b0832a` / `#f3e8cf` / `#6a4c0e` | Achievement: prize dots, Prize won, Leading, merit +. |
| `--warn`, `--bad` | amber, brick red | Attention markers and destructive actions. Each has a `-soft` and an `-ink`. |
| `--r` / `--r-lg` / `--r-pill` | `10px` / `16px` / `999px` | Inputs and small boxes; cards and workspaces; buttons, tabs and chips. |
| `--shadow-card` | hairline and a warm shadow | Cards lie on the paper. |
| `--page-wash`, `--rail-wash`, `--paper-tex` | `/art/*.webp` | The page's cloud, the menu's washes and the paper tile; `none` in Dark and in a theme. |

Class cards and greeting pills use four pastel pairs (coral, sky, mint,
butter) with dark text of the same hue, each above 4.5:1.

`--sky` and `--teal` remain as names for older rules but resolve to the same
quiet slate and sage, so nothing renders as a sky or teal panel.

Kept because they mean something: house colours (Makkah gold, Al-Aqsa blue,
Madinah green), house token colours (green 5, blue 20, red 50), the book level
colours (L1 brown, L2 blue, ...) and the warning levels (yellow, orange, red).
They appear as dots, thin bars, chart columns and the tint of a chosen option;
warning levels are a neutral label with a coloured dot. Text on a level colour
is picked by luminance (`onC()` in `src/page.html`).

## Type

Two families, chosen in November 2026 to sit between classy and cute without clashing: they share the same soft,
rounded shapes. **Fraunces** is a book serif with a Soft axis; set all the way (rounded serifs and terminals, Wonky
off so upright letters stay upright) it reads as classic but warm. It sets the wordmark ("Ilmi"), titles and figures
(`--font-display`) and the large italic lines (`--font-hand`): the overview's name under the greeting, the start
page's tagline and the sign-in saying. Its optical sizes are kept, so small titles get sturdier letters and big
ones finer. **Nunito**, a rounded sans, sets everything you read and use (`--font`). Fraunces has no ʿ or ʾ, so its cut-down copy
draws them with its own quotation marks. Both are cut down to the letters Ilmi shows by `tools/art/type.py`. علمي is Amiri, in a small
subset holding only its four letters (`amiri-ilmi.woff2`, `unicode-range` limited to them). Atkinson
Hyperlegible Next stays on the TV, which is read across a hall, and emails keep their own mail-safe faces. All faces are self-hosted in `static/fonts` under the SIL Open Font License
(`OFL.txt`). Sentence case throughout; the small uppercase labels are the menu's section names and the Welcome tab
on the sign-in page.

## Layout

One shell everywhere: a 248px olive rail on screens 960px and wider, and below
that an olive top bar with the wordmark and a floating dock at the bottom of the
screen. The dashboard opens with a
compact head on the paper (`.head`: the page title in Fraunces, or on the overviews the
greeting with the overview's name under it in Fraunces italic and an ink sprig that draws itself on; Switch
class on small screens, save status, the figures button (a bar chart drawn on
one L-shaped axis so its corner is clean; not on the Students page), then pill tabs) above
the cream workspace card (`.ws`, 16px corners; the overview's parts are cards of their own). The selected tab is a white pill
with navy type and a soft shadow. The head is sticky on wide screens. Below
960px the tabs wrap as pills and the selected one turns navy (a two-column grid below 380px) so every tab is
visible without sideways scrolling. Admin pages use `.band`, the same title and
figures on the canvas.

**Phone dock** (below 960px, `.dock` in tokens.css): a white, slightly
translucent bar with 26px corners floating 10px above the bottom edge (and the
home indicator), with an icon and a short label per item. A teacher always has
Home, Quran, Memo and More; tapping a class page from elsewhere opens the
class used last. An admin has Home, Classes, Record and More, and while a class
is open Classes, Quran, Memo and More. Progress and Rewards are left off phones
because they repeat what Home and the Quran and Memo pages already show; the
desktop class tabs keep them. The
class tab row is not shown above the page on phones, since the dock carries it;
Class settings is the first item under More. The selected item sits in a pastel
bubble of its own colour (sky, mint, butter) with a small pop;
the rest are graphite. More turns navy and opens the full menu (the rail's
links, Account and Sign out) as a navy card rising from the dock over a dim
scrim; Escape or a tap outside closes it. The admin and account pages carry the
same dock as plain links (`/#classes`, `/#record`, `/#tab-Q` and so on) and a
CSS-only More, since they run without scripts. While a text field has the
keyboard open on a touch screen the dock slides away. Toasts rise above it.

**Quran on phones** (a sheet under 600px wide): only the focused day shows, as
three columns: the name on the left, the day's box in the middle of the screen
under the date, and the house on the right. A long name wraps onto a second
line. No message pops up after each score; the box shows it. Tapping
a box raises the score picker from the bottom: the student's full name, the
day, six large score buttons and Clear, over a dim backdrop that closes it.

**Phones keep to the daily work** (below 960px): no Full sheet on the Quran or
Memo page (the Day by day and student views only; a sheet left open on a wider
screen falls back when the window narrows), no Presenter button and no help
note on the Quran page. Desktop keeps all three.

**Classes** is its own page in the rail, under Journey2Jannah for admins
(teachers have Journey2Jannah at the foot of the menu, just above their name), in place of the
old list of class names: searchable class cards (patterned banner, year tile,
students, passes, merits and warnings), all on one scrolling page, with the CSV
upload under them. A card opens that class with every tab, Settings included; the rail keeps
Classes marked while a class is open, and Switch class sits by the title.

The name is Ilmi, set as a wordmark: "Ilmi" in Fraunces with علمي beside it in Amiri (`.ilmi`, the same
everywhere), and the site title under it in the menu when an admin has set one (Account, Site title;
`site_settings.py`), or nothing, so it reads just Ilmi. The browser tab icon (`/favicon.png`) is the mark,
a cream ʿayn on an olive disc.

**Overview**: one page, the Summary, with no tabs (the user removed the
Attention, Rankings and Houses tabs on 2026-10-15). Everything they held is
behind the section titles' pop-ups: Needs attention, House points (totals,
tokens by house, points by class), Most passes, Most merits and Top classes
(the class ranking). Only the old Furthest along list is gone.

**Needs attention** is three pairs of counts, each a soft grey button with a
dot for its tone (red concern, green going well, amber warnings):

- **Quran, this month**: Struggling (average score 2 or less) and Excelling
  (4 or more). The average is rounded to one decimal place before it is
  sorted, so the figure shown always matches the group.
- **Memorisation**: Overdue (no pass in 3+ weeks, or since the first class day)
  and Advancing (passed a Memo target since Monday).
- **Behaviour**: Warnings (Warning 1 or 2) and Missed breaks (on Miss break).

A count opens the pop-up on its own tab. The pop-up's tabs are the same pairs,
two to a row (the Quiet classes line and tab were removed). Each tab lists the students with their class (which opens on the
Quran or Memo tab) and the figure: the average as a pastel score chip with
how many scores it covers, days since a pass, what was passed this week, or
the warning. Ten a page.

**Record** (main navigation, admins only): level tests in four numbered steps,
Quran (Qaidah level 1 to 14) or Memo (target as level and target, or item number and
Surah), the student, the level, then a 0 to 5 score on the pastel scale and
Passed or Not passed, with the student's latest tests and Take back the latest
below. Every pass, Quran or Memo, gives the student a blue token (20 house
points); a note under the buttons says so, the pass shows a small "Blue token"
pill in the student's level tests, and taking the test back takes the token
back. The score goes into that day's box on the Quran sheet with a gold border,
and the sheet will not change it; only the Record page can.

**Centred on big screens**: from 1500px wide the content column narrows to
1360px, centred beside the side menu, and on screens taller than 860px a top gap
grows with the height (up to 120px), so the title, tabs and work sit nearer the
middle of the screen. Nothing changes at 1280x720.

**Class overview** (teachers): the Overview title reads Class overview and its
Summary opens with Needs attention, then Rankings (House points, and the top
three students for Most passes and for Most merits), then Passes per week and
Students by level. Each of those section titles (and Top classes) has a small
arrow and opens its full list in a pop-up (the side drawer; a sheet on phones),
so the details don't need a trip to another tab. No latest passes on the
teacher's Summary. The **school overview** (admins) uses the same layout with
school-wide figures, and its last row adds Top classes and Latest passes.

**Record page look**: a subtitle under the title, the four numbered steps down
the left of the workspace divided by hairlines like the overview's sections (a
step's number fills navy once answered), and the chosen student in a soft grey
panel on the right (class, house, Memo target, Qaidah level, latest level tests,
Take back the latest). On phones the panel follows the steps.

**Star of the week**: a star button beside the Full sheet icon on the Quran
tab picks one student for the week holding the focused class day; a gold star
sits by their name. Kept on the student as the Monday of each week (sw).

**Class** tabs (a class opens on Quran). Teachers see Quran, Memo, Progress
and Settings, and so do admins (Record, Warnings and Rewards were removed; house
tokens are in the menu on a student's name). All
recording starts from the Quran tab.

- **Quran** scores show as colour (pastel coral 0, peach, butter,
  pistachio, mint, sea green 5), with no key: the picker shows each number with
  its colour. Only the focused day also shows the number. Boxes are 40px in every class, so long classes scroll
  rather than shrink. A student's name opens a menu with Merits and − and + in
  its top right, then Warning 1, Warning 2 and Miss break (with the current
  target, in a pale chip of its level colour, on the Warning line), and their house
  tokens with − and +. The house is not repeated there; it is in the row.
- **Quran**: the day-by-day sheet. The focused class day sits in a soft navy
  lane between the student names and their houses, with up to three earlier
  days fading out to the left and three later ones to the right (one each side
  on phones). Only the focused column can be changed: tap a box for the 0 to 5
  picker, or select it and type a digit, which moves down to the next student.
  The arrows (or Left and Right on a box) move one class day; the names and
  houses fade out, the columns slide one place left like the next column of a
  page, and the names come back. Class days are Saturdays and Sundays only: the
  weekends from the first class day, weekend days with a score, and any weekend
  day chosen with the calendar icon. A weekday score is never shown as a column.
  The space above the first box equals the gap between two boxes.
  The calendar icon opens a whole-year pop-up (twelve months, Monday first,
  days outside each month faded): class days are bold, days with scores carry
  a small sage dot, today is a filled navy circle and the day on show is
  ringed. Weekdays are shown, faded, for orientation only and can't be picked
  (today keeps a thin ring when it is a weekday). Days before the first class day can't be picked; picking a day jumps
  the sheet there. Arrow keys move a day or a week, Page Up and Down a month.
  Teachers have an add-student icon that opens a short form (name, house,
  optional Student ID); admins add students on the Students page, so they
  don't see it. The Full sheet icon (four corners) shows the whole table by
  class day; pressed, it turns navy with inward corners and goes back. The score
  picker's white pill has 7px of room around the boxes, with corners that
  follow theirs.
- **Memo profile** actions: Details (identifiers, Rename, Remove student) and
  History. Passes, fails and targets are set on the Record page.
- **Memo**: student cards whose top is a pale tint of their current target's
  level colour (grey for no data yet); the profile header matches. The Full
  sheet icon shows the sheet, scrolling inside its frame.
- **Progress**: Levels (position in the book in two columns, students by
  level with empty levels named once in a line instead of zero rows) and
  Pass history (passes per week, the history and Add a
  past pass).
- **Settings**: one column of rounded rows in the Memo card style: class
  name, first class day, adding students (teachers only), then reset and delete.

Long school-wide lists are paged with Previous and Next and a "1–10 of 61"
count; class lists stay whole. Below 640px modules go full-bleed and tables
become labelled rows.

## Components and states

Buttons: primary (navy, one per screen), default (outlined), quiet (text),
danger (red outline, solid red for the final irreversible step). A chosen
option in a row of toggles (warnings, Quran scores) is a light tint of its
meaning colour with a ring, keeping graphite text. 40px minimum height, 44px for
toggles and tabs. On touch screens and below 960px every control is at
least 44px tall. Segmented controls
(`.seg`) use `aria-pressed`. Help is a `<details class="help">` popover that
opens by keyboard, touch and pointer and closes with Escape. The drawer (`#drw`)
is a dialog with a white header, a focus trap, Escape to close and focus returned to the
control that opened it; it holds complete lists and student records.

States: loading ("Loading…"), empty ("No students yet" with the next step),
not recorded (Quran "Not recorded"), no activity ("No passes yet this month",
"Nothing recorded yet"), saving ("Saving…"), saved ("All changes saved"), and
failed (a red banner with Retry, Download unsaved changes or Sign in again, and
the status shows "Needs attention").

Rankings use competition ranking (1, 1, 3); ties are shown as "=1". Only values
above zero are ranked, and the rest are listed by name ("Not yet: ...").
Nothing is cut off at a podium: every ranked list pages through everyone.
Count badges never wrap their digits.

## Start page

`/welcome` is where anyone signed out arrives (`/` redirects there; other pages still go to sign-in with `next`).
It follows the reference: the Ilmi wordmark large with علمي under it, a sprig and the Qur'an on its rahle drawn in
ink, the tagline "Every step in learning matters.", then an olive band ("My knowledge.") explaining the name, four
features with ink icons (Qur'an tracking, Memorisation, Houses and rewards, Progress at a glance), a preview of the
overview as it looks after signing in (the greeting, Needs attention, Quran trackers, Rankings and the dock of icons),
drawn in HTML with made-up figures (never real pupils), and a closing line over grass and a mosque
skyline. On load the hero paints itself in (washes bloom, the sprig and letters draw on, علمي writes in from the
right, the i's dot drops); further down each part rises in as it scrolls into view and the preview's figures count
up (`static/welcome.js`, allowed by the page's CSP along with `static/wash.js`, which starts the wash page turn into
sign-in; see Motion). Without the script, or under reduced motion,
everything simply shows. Signed in, its buttons read "Open Ilmi" and go to the dashboard.

## Administration

Manage users leads with the accounts and an "Invite staff" button that jumps
to the invite form. All admin pages use the same canvas title and figures and
the same cream modules on the paper.
They stay free of scripts and inline styles to match their content security
policy.

## TV

The TV is a hall display with its own scenes and animations; since November 2026 its panels are Ilmi's olive
and its cards cream (the illustrations are unchanged). It keeps its two slides, rotation and privacy rules (first name and
surname initial, whole-school totals). House points are columns on one
baseline with heights in proportion to points; "Leading" and a gold number
appear only when a house is strictly ahead. Every joint first place is
highlighted. Star of the week and Passed a book shrink to fit, but never below
2.6% and 2.8% of the screen height; a list that still does not fit is split
into pages that turn every 10 seconds ("Page 1 of 3"), and the slide stays up
until every page has been shown. Names wrap rather than being cut off.

## Design skills in this repository

`.claude/skills/impeccable` (pbakaus/impeccable) and
`.claude/skills/ui-ux-pro-max` (uipro-cli) are installed for every Claude
session. Run `.claude/skills/impeccable/scripts/impeccable detect --json src/template.css`
for the static scan, and inject its `detect.js` into the running dashboard for
the in-browser scan. UI UX Pro Max's search for a neutral classroom dashboard
returned a zinc canvas (`#FAFAFA`), graphite primary (`#18181B`) and, among
the colour results, a minimal black-and-gold scheme; the grey canvas, graphite
rail and restrained gold come from those. Its pink accent and Lora/Raleway
pairing were not used, because the brief fixed the colours and type (now Ilmi's olive, cream and book
faces). Its interaction guidance (hover, pressed, disabled and focus states)
is applied to every control.
`PRODUCT.md` records the product facts the skills read.

## Motion

**Ink and water** (November 2026). Everything in Ilmi moves the way its pictures are made: ink is drawn on, a wash
of watercolour spreads and dries, a sheet settles on the paper. The tokens are in `src/tokens.css` (`--ease-ink`,
`--ease-wash`, `--ease-settle`, `--t-wash`, `--t-drop`, `--t-settle`). Four moments carry it; the rest of this
section still holds for everything else.

- **The wash page turn** (the signature; `src/tokens.css` "The wash page turn", `static/wash.js`). Changing page,
  inside the dashboard, between it and the admin pages, from the start page to sign-in and from sign-in into the
  dashboard, plays as a wash of watercolour: wet sage paper spreads out from whatever was pressed (a dock item, a
  class's name, the Sign in button; from the foot of the page for the Back button), the old page goes under its
  leading edge and the new page is left behind it as it dries. Half a second. It is a view transition with three
  layers: the wet paper at the back, the old page masked away inside the wash, and the new page masked in a little
  further back. The front is three pools spreading at slightly different rates, so its edge is uneven like paint
  and never a ring. The masks are plain gradients driven by one animated number (`--wash`, a registered property):
  no image, no script and no layout while it plays. `wash.js` only remembers where the last press was (`--vx`,
  `--vy`) and offers `IlmiWash.turn(fn)`; the dashboard's `turn()` and `nav()` (page.html) route the menu, the
  dock, class links, Search and the Back button through it, and Account's theme change uses it too, so a new theme
  bleeds in from its swatch. Tabs inside a class keep their quick slide. The dock stays put and stays live, with its
  cream tile drawn as part of it. Browsers without `mask-composite` or view transitions fade or change at once.
  A full page load (sign-in, the admin and account pages) is marked `types: pageload` and gets a different back
  layer: paper with a band of wet sage travelling with the front, where turns inside the dashboard have a flat
  sage sheet. It looks the same, costs one more animated layer, and is there for one reason: Chromium without a
  graphics card sometimes fails to draw either page picture on a full page load (about one load in four in the
  test browser, and with the browser's own fade as well). With the flat sheet that filled the screen with sage for
  half a second; with the band it shows paper with a wash passing over it, then the new page.
- **A drop of colour** (`src/template.css` `.qc.drop`, page.html `qDrop`). A Quran score or Memo mark that has just
  been recorded takes its colour like a drop on paper: the colour spreads from the middle of the box with the
  pigment gathered darker at the wet edge, a faint ring bleeds past the box, then the figure shows. 0.42s, one box.
  Five out of five leaves a small ink splash on the paper behind the box (`/art/splat-c.webp` through `--splat`,
  off in Dark and in a person's own theme).
- **Sheets settle** (after sign-in only; `body.enter` in template.css). Once the greeting has landed, the overview's
  cards settle up into place a beat apart, their figures come up like ink soaking in and the house bars are brushed
  on from the left. Ordinary loads and reloads show the page at once.
- **The start page's scroll** keeps its look and costs a fraction of what it did. Each scroll-driven property
  (`--h`, `--in`, `--t`, `--p`, `--o`, `--doc`) is registered as not inherited and `welcome.js` writes it only onto
  the elements that use it, only when it changes, from positions measured once; before, one property on `<html>`
  restyled the whole page on every frame. Only the sentences read in word by word inherit theirs (`--pw`, `--iw`).

**Budget.** Nothing here loops, loads a library or adds an image. Each effect animates transform, opacity, a
gradient or a mask on one layer or one small box, and is over within about half a second (the splash a second).
Measured in headless Chromium with the processor slowed four times: scrolling the start page went from 109 of 426
frames slower than 30 a second, with 8.6 seconds of style work, to at most 2 of about 510, with about 1.2 seconds;
the dock's magnification holds 60 a second; a page turn adds no script or layout and runs at about 40 frames a
second in that test browser, which has no graphics card, so the processor draws the masks (the plain fade it
replaces ran at about 55 there). Everything is off under reduced motion: pages change at once and
scores simply show.

Motion explains a change of place and is never decoration. The selected tab is
one white pill that slides to the tab chosen; a tab under the pointer lifts by a
pixel; a class card lifts slightly under the pointer; the new page slides in from the side its
tab is on. The Quran sheet slides its columns when the class day changes, and the Memo
calendar slides to the next or previous month. All
of it is off when the device asks for reduced motion (the rule at the end of
`src/tokens.css`).

**Opening titles** (sign-in page): a fresh visit to /login (not after signing
out or a failed attempt) plays a short CSS-only sequence of about 4½ seconds. A
pale sage wash blooms on the paper and علمي, the Arabic name alone, is written in by hand: a pen runs through each
letter in the order a calligrapher writes it (ع's head out to its chin and back along the line, ل's stem from the top
down and on through م's loop to the sweep of ي's tail, then ي's two dots), starting from rest, slowing into turns and
stopping where it doubles back. It lays down wet olive ink that dries to ink a beat behind the pen. Each letter's
outline clips the pen's strokes, so the ink keeps the shape of the wordmark's own font, with Amiri's stacking
ligatures off so it sits level as it does everywhere (`templates/art/intro-word.svg` and the pen's timings in
`static/auth.css`, both made by `tools/art/word.py`). Once the ink is dry the word fades back into the paper and
the stage crossfades into the
sign-in screen (a 1.4 second fade) while the form rises in underneath. The sign-in fields stay disabled while the opening plays and are enabled, with the email field focused, once it ends (static/intro.js, the one script the sign-in page runs), so saved-password suggestions appear then and not over the animation. Off
under reduced motion. Styles at the end of static/auth.css.
The opening's end overlaps the fade: the card floats up out of a soft blur with
its parts following one by one, and the welcome panel's quote and art drift in.
Afterwards the welcome panel keeps an idle sky: two pale mists drift slowly across the olive washes, and the
skyline's ink draws itself on. The site title, when set, appears under the wordmark in the card, on the panel
and in the phone's top band. Splashes of olive paint (`static/art/splat-*.webp`, painted by `tools/art/wash.py`)
land around the word one after another as the opening starts: each opens from its centre (a radial mask widening)
while it swells past its size and settles, wet and dark at first and a shade lighter as it dries.

**Greeting after signing in**: the first dashboard load after signing in shows
only the overview's own greeting ("Assalāmu ʿalaykum ...!"), typed out letter
by letter, large in the middle of the page, with an olive caret (a copy set at the
large font size, so it stays sharp, wrapping as the heading does; every letter is
laid out from the start, so lines never reflow). Once the records have loaded and
the greeting is typed, it shrinks and moves into its usual place in one motion,
and the rest of the page fades in behind it: on a computer the olive side menu
slides in from the left edge with its items following, the tabs rise, the
overview sections and class cards rise in turn, ranking rows follow, and the
bars grow. Once per sign-in (html.fresh, then body.enter for a few seconds),
off under reduced motion.

**Motion system** (page.html "Motion", template.css "Motion system"): one
easing, about 150-200ms for feedback and 200-300ms for panels. Views are drawn
with innerHTML, so a MutationObserver gives redrawn content continuity before it
paints, using Web Animations only (nothing is held back, controls work at once):
bars grow from nothing the first time they are seen and afterwards glide from
their old length (This month / All time changes the numbers at once and only
moves the bars); ranked students, houses and class cards glide to their new
places by name, and new rows fade in; class cards rise in a short stagger the
first time the Classes page is drawn; on Record a new step or a newly chosen
student's panel fades in and a level test just saved flashes green. Segmented
controls have a sliding thumb (.segi) and the side menu a sliding marker
(.navi). Class cards lift on hover, sink when pressed and show a navy ring for the
keyboard. Off under reduced motion and during the sign-in arrival.

**Animate UI layer** (2026-10-21). The user asked for `npx @animate-ui/cli@latest
animate all`. That command only swaps the primitives of shadcn/ui React
components (it needs a components.json), and this site is Flask templates with
plain HTML, so it can't run here. Its `audit` command does run. Animate UI's
effects are rebuilt in-house instead:
- Layers come and go like its dialogs, sheets and popovers. They come in with a
  soft spring (cubic-bezier(.22,1,.36,1)), a little scale or slide, and blur.
  They leave with a quicker fade the same way. This covers the side drawer (pop-ups),
  the ask dialog, the year calendar, the figures popover, the name menu, the
  score picker and the toast. A closing layer leaves an inert copy
  (.mo-ghost, page.html fxIn/fxOut) that plays the way out, so the real one is
  hidden at once.
- Counts in Needs attention, the figures and house points count up the first
  time they are seen, including as the overview comes in after the greeting.
  When a count changes, only the digits that changed slide in (up for more,
  down for fewer), like its sliding number.
- With a mouse, one soft highlight follows the pointer along the side menu
  and the class tabs, fading in and out at the ends. It hides over the current
  item, which has its own pill. This is its motion highlight.
- Icon buttons shrink a little when pressed, and their icons move on hover:
  the star wiggles, the calendar bobs, the figures bars grow, the arrows nudge
  and the Full sheet corners spread.
All of this is off under reduced motion. Nothing changes layout, so daily work
still fits 1280x720.

**Arrival and hover** (2026-11, page.html "Arrival motion", template.css "Hover
lift"). Each time a page, class tab or view is opened, its bars draw in from the
left one after another (clip-path, so rounded ends stay round) and its big
figures count up to their value, flickering through nearby numbers for the first
moments (`arrive()`, `grow()`, `countUp()`). Filters and typing redraw without
it. The Quran tracker's two-day switch brings its rows in one after another and
re-runs the bar and count. With a mouse, class cards, overview tiles, house
cards, tracker rows and the developer's school tiles rise a few pixels with a
deeper shadow and settle on press; larger panels only deepen their shadow.
Off under reduced motion and on touch screens.

**House points awards** (the drawer behind See awards): the total in Fraunces
with its coin split as one bar and three small tiles, "Where they came from" as
bars when there is more than one reason, then every award grouped by day, with
the same award given several times that day shown once with its count
(+120, 6 × 20). Counted coins rows carry no student line.

**Sign-in layout**: on a computer the white sign-in card is on the left and the
navy welcome panel on the right.

**Tracker button on phones**: a teacher's overview on a phone has one large
navy "Open tracker" button straight under the greeting (just the words), since
entering the day's scores is why most teachers open the app. It opens the class
they used last on the Quran tab (Switch class is there for the others). The
teacher's class chips that sat beside "Class overview" are gone. Not shown on
wider screens or to admins.

**New tab, new sign-in**: opening the site in a new tab or window always starts
at the sign-in page; refreshing a signed-in tab keeps you signed in (a
sessionStorage mark set by the first load after signing in; see TAB_GUARD in
server.py). A "Stay signed in" sign-in skips this and opens straight to the
dashboard in any tab.
