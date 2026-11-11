# Ilmi: product

Drafted from the repository and the owner's redesign briefs (October and November 2026) for
the design skills in `.claude/skills`. Correct it here if anything is wrong.

## Users

- **Teachers** at the madrasah record passes, merits, fails,
  warnings, Quran scores, memo progress and house tokens for their class,
  usually between lessons and often on a laptop or phone.
- **Administrators** watch whole-school performance, upload student lists,
  manage staff accounts and run the hall TV screens.

## Purpose

Keep every student's Quran memorisation journey (book levels L1 to L10, then
the numbered Juz items) accurate and visible, and celebrate achievement
through houses, prizes and the TV.

## Constraints

- Records, scoring rules, permissions, sign-in and TV privacy (first name and
  initial only) stay as they are.
- Flask on Railway with a single-page dashboard built by `tools/build.py`;
  merging to `main` deploys the live site.
- The identity is Ilmi (علمي, "my knowledge"), November 2026: olive green and off-white with a watercolour
  style, ink drawings and book type (see DESIGN.md). Since November 2026 Ilmi stands on its own: no school is
  named in the code, and an admin sets the site title (the madrasah's name) under Ilmi on Account.
- Daily recording must fit the screen: the class in front of the teacher,
  details one selection away.
