"""Ilmi's emails: what each one says, rendered from templates/email/.

Each email has an HTML part (templates/email/<name>.html, built on _layout.html)
and a plain-text part (<name>.txt) with the same words. Subjects are here.

| Email            | Sent when                                                               |
| ---------------- | ----------------------------------------------------------------------- |
| invite           | An admin invites someone, or sends a new invite to someone who never    |
|                  | accepted theirs; also from "Forgotten your password?" in that case.      |
| reset            | Someone asks for a reset link, or an admin sends one.                   |
| password_changed | A password is changed on the Account page or with a reset link.         |
| login_code       | An admin types their password: the code that finishes signing in.      |
| test             | An admin sends a test email from Manage users.                          |
| help_reply       | An admin answers a Help or Requests message on FAQs and picks email.    |
| help_summary     | Sundays at 12:00 London time: the week's Help or Requests (helpdesk.py). |

PREVIEWS holds sample details for each email, for the admin preview pages and the tests.
"""
import time

from flask import current_app, render_template

import auth  # auth imports this module too; only use auth's names inside functions

SUBJECTS = {
    "invite": "You're invited to Ilmi",
    "reset": "Reset your Ilmi password",
    "password_changed": "Your Ilmi password was changed",
    "login_code": "Your Ilmi sign-in code",
    "test": "Test email from Ilmi",
    "help_reply": "A reply to your message on Ilmi",
    "help_summary": "This week's Help or Requests on Ilmi",
}
TAGS = {"invite": "invite", "reset": "password_reset", "password_changed": "security", "login_code": "security", "test": "test",
        "help_reply": "help_reply", "help_summary": "help_summary"}


def hours(n):
    return f"{n} hour{'s' if n != 1 else ''}"


def render(template, **context):
    """Returns (subject, text, html) for one email. The context's `name` is the recipient's name."""
    context = dict(context, site=auth.public_url("/"), logo=auth.public_url("/logo.png?v=4"),
                   signin=auth.public_url("/login"), forgot=auth.public_url("/forgot"),
                   support_email=current_app.config.get("SUPPORT_EMAIL", ""),
                   invite_hours=hours(auth.INVITE_TTL // auth.HOUR), reset_hours=hours(auth.RESET_TTL // auth.HOUR),
                   code_minutes=auth.LOGIN_CODE_TTL // auth.MINUTE)
    return (SUBJECTS[template], render_template(f"email/{template}.txt", **context).strip() + "\n",
            render_template(f"email/{template}.html", **context))


def send(template, to, background=False, **context):
    """Returns True when the email was sent (or, with background=True, a send was started)."""
    mailer = current_app.config["MAILER"]
    if not mailer.enabled:
        return False
    subject, text, html = render(template, **context)
    return mailer.send(to, subject, text, html=html, tag=TAGS[template], background=background)


# Sample details for previews. Names are made up.
PREVIEWS = {
    "invite": lambda: {"name": "Sr Maryam", "email": "maryam@example.com", "role": "teacher", "by": "Br Ahmed",
                       "rooms": ["Yr3-A-QM-Sr Maryam", "Yr4-B-QM-Sr Maryam"],
                       "link": auth.public_url("/invite/example-link")},
    "reset": lambda: {"name": "Sr Maryam", "email": "maryam@example.com", "by": None,
                      "link": auth.public_url("/reset/example-link")},
    "password_changed": lambda: {"name": "Sr Maryam", "email": "maryam@example.com", "how": "reset",
                                 "at": int(time.time())},
    "login_code": lambda: {"name": "Br Ahmed", "email": "ahmed@example.com", "code": "482913", "at": int(time.time())},
    "test": lambda: {"name": "Br Ahmed", "email": "ahmed@example.com", "at": int(time.time())},
    "help_reply": lambda: {"name": "Sr Maryam", "kind": "Help", "asked_at": int(time.time()) - 86400,
                           "body": "How do I record a Memo pass for a student who was absent last week?",
                           "reply": "Open the Memo tab, choose the student and use Add a past pass under Progress.",
                           "by": "Br Ahmed"},
    "help_summary": lambda: {"name": "Admin", "start": int(time.time()) - 7 * 86400, "end": int(time.time()),
                             "helps": 1, "requests": 1, "waiting": 1, "faqs": auth.public_url("/admin/faqs"),
                             "items": [{"kind": "Help", "name": "Sr Maryam", "email": "maryam@example.com",
                                        "body": "How do I record a Memo pass for a student who was absent?",
                                        "at": int(time.time()) - 3 * 86400, "reply": "Use Add a past pass.",
                                        "by": "Br Ahmed"},
                                       {"kind": "Request", "name": "Br Idris", "email": "idris@example.com",
                                        "body": "Could we have a second TV in the hall?", "at": int(time.time()) - 86400,
                                        "reply": None, "by": None}]},
}
