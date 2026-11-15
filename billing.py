"""Signing a madrasah up, taking its subscription through Stripe, and the developer's controls over both.

The path from the pricing page:

    /signup                     the madrasah's name, the admin's name and email, how many active students, monthly or
                                annual, and the terms box (templates/terms.html, TERMS_VERSION). Nothing is made yet.
    /signup/<token>             the review: the price for that many students, worked out by pricing.quote, and one
                                button. The token is the sign-up's own secret link.
    POST /signup/<token>/go     free (within the free allowance) or made with the developer's test code: the school
                                is made at once. Paid: a Stripe Checkout Session for a subscription at exactly the
                                quoted amount, and the browser goes to Stripe.
    /signup/<token>/done        where Stripe sends the browser back. The session is read back from Stripe, and if it
                                is paid the school is made (unless the webhook already made it).
    POST /stripe/webhook        Stripe's own word that a checkout finished or a subscription changed, checked against
                                STRIPE_WEBHOOK_SECRET. This is what counts if the browser never comes back.

Making the school (fulfil) happens once however many of those arrive together: the sign-up is claimed in one UPDATE
before anything is made. It adds the school with an empty database of its own, the admin's account, a subscription
row, and the mark that sends the admin through School setup (setup.py) at their first sign-in; then it emails the
invite link to choose a password. A paid or test sign-up's own browser is taken straight to that page; a free one
has to open the email, so a free school can't be made in somebody else's name.

Stripe is off until STRIPE_SECRET_KEY is set: paid sign-ups then say payment isn't available yet, and nothing here
calls Stripe. No card details ever reach this server; Stripe's hosted page takes them. Stripe is called with urllib
(as mailer.py calls Resend), so there is no new dependency.

The price charged is always pricing.quote's: the Checkout Session carries the amount itself (price_data), not a
Price made by hand in Stripe's dashboard, so the page, the calculator and the charge can't drift apart. resync()
recounts a school's active students (current students only, each Student ID once) and moves the subscription to the
new amount from its next renewal; Stripe's `invoice.upcoming` event and the developer's Recalculate button run it.

The developer (schools.py) can suspend a school, which locks its staff and its screen links out with a plain page
until it is lifted and never deletes anything, and cancel its subscription, now or at the end of the paid period.
Cancelling doesn't suspend, and a failed payment doesn't either: the developer sees the status and decides.

SIGNUP_TEST_CODE, when set, is a code typed on the sign-up form that skips payment and makes a "test" school, so the
whole flow after payment can be tried without a card. Unset, there is no such code.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, abort, current_app, g, redirect, request

import auth
import pricing
import schools
import site_settings

bp = Blueprint("billing", __name__)

TERMS_VERSION = "2026-11-15"
SIGNUP_TTL = 24 * 3600          # how long a sign-up link can still be finished
WEBHOOK_TOLERANCE = 5 * 60      # how old a signed Stripe event may be
STATUS_TTL = 15                 # seconds a school's suspended state is remembered in this process
SIGNUP_PATHS = ("/signup", "/stripe/webhook", "/terms", "/pricing")
PLAN_NAMES = {"free": "Free", "paid": "Paid", "test": "Test (never charged)"}
STATUS_NAMES = {"active": "Active", "trialing": "Trial", "past_due": "Payment overdue", "unpaid": "Unpaid",
                "canceled": "Cancelled", "incomplete": "Payment not finished", "incomplete_expired": "Payment not finished",
                "paused": "Paused"}
_status = {}


def router():
    return current_app.config["DB"]


# --- Stripe ------------------------------------------------------------------------------------------------------

class StripeError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _flatten(value, prefix=""):
    """Stripe's form encoding: {"a": {"b": [1]}} is a[b][0]=1."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _flatten(inner, f"{prefix}[{key}]" if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            yield from _flatten(inner, f"{prefix}[{index}]")
    elif isinstance(value, bool):
        yield prefix, "true" if value else "false"
    elif value is not None:
        yield prefix, str(value)


class Stripe:
    """The few Stripe API calls Ilmi makes. `enabled` is False without a secret key, and then nothing is sent."""
    BASE = "https://api.stripe.com"

    def __init__(self, secret_key="", timeout=20):
        self.key, self.timeout = (secret_key or "").strip(), timeout
        self.enabled = bool(self.key)
        self.live = self.key.startswith(("sk_live_", "rk_live_"))

    def call(self, method, path, params=None, idempotency_key=None):
        if not self.enabled:
            raise StripeError("Stripe isn't set up.")
        data = urllib.parse.urlencode(list(_flatten(params or {}))).encode() if method != "GET" else None
        url = self.BASE + path
        if method == "GET" and params:
            url += "?" + urllib.parse.urlencode(list(_flatten(params)))
        headers = {"Authorization": "Basic " + base64.b64encode((self.key + ":").encode()).decode(),
                   "User-Agent": "Ilmi/1.0"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read().decode("utf-8"))["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = f"Stripe answered {error.code}."
            raise StripeError(message, error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise StripeError(f"Stripe couldn't be reached ({type(error).__name__}).") from None


def stripe():
    return current_app.config["STRIPE"]


def verify_webhook(payload, header, secret, now=None):
    """True when Stripe signed this exact body with the endpoint's secret, recently. The header is
    "t=<seconds>,v1=<hex>[,v1=<hex>...]" with other entries to ignore; the signature is HMAC-SHA256 over
    "<t>.<body>"."""
    if not secret or not header or len(header) > 2000:
        return False
    stamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1":
            signatures.append(value)
    if not stamp or not stamp.isdigit() or not signatures:
        return False
    if abs((now or time.time()) - int(stamp)) > WEBHOOK_TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


# --- Suspension ----------------------------------------------------------------------------------------------------

def suspended(school_id):
    """Whether the developer has suspended this school. Asked on every request, so remembered for a few seconds."""
    if school_id in (None, schools.CONTROL):
        return False
    key = (id(router()), school_id)
    known = _status.get(key)
    if known and time.time() - known[1] < STATUS_TTL:
        return known[0]
    with router().control.transaction() as tx:
        row = tx.execute("SELECT suspended_at FROM schools WHERE id = ?", (school_id,)).fetchone()
    state = bool(row and row["suspended_at"])
    _status[key] = (state, time.time())
    return state


def set_suspended(school_id, reason=None):
    """Suspends (with a reason, kept for the developer) or, with reason None, lifts it."""
    with router().control.transaction() as tx:
        if reason is None:
            tx.execute("UPDATE schools SET suspended_at = NULL, suspended_reason = NULL WHERE id = ?", (school_id,))
        else:
            tx.execute("UPDATE schools SET suspended_at = ?, suspended_reason = ? WHERE id = ?",
                       (auth.now(), reason[:300], school_id))
        auth.audit(tx, "school.suspended" if reason is not None else "school.unsuspended",
                   detail={"school": school_id, **({"reason": reason[:300]} if reason else {})})
    _status.pop((id(router()), school_id), None)
    if reason is not None:
        # Everyone at the school is signed out; the next page any of them opens says why.
        with router().get(school_id).transaction() as tx:
            tx.execute("DELETE FROM sessions")


# --- What the developer sees ------------------------------------------------------------------------------------------

def info():
    """{school_id: its subscription and suspension} for the developer's tiles."""
    with router().control.transaction() as tx:
        subs = {r["school_id"]: dict(r) for r in tx.execute("SELECT * FROM subscriptions").fetchall()}
        rows = tx.execute("SELECT id, suspended_at, suspended_reason FROM schools").fetchall()
    cfg, out = pricing.config(), {}
    for row in rows:
        sub = subs.get(row["id"])
        item = {"suspended": bool(row["suspended_at"]), "suspended_at": row["suspended_at"],
                "suspended_reason": row["suspended_reason"] or "", "sub": None}
        if sub:
            per = "year" if sub["billing"] == "annual" else "month"
            item["sub"] = dict(sub, plan_name=PLAN_NAMES.get(sub["plan"], sub["plan"]),
                               status_name=STATUS_NAMES.get(sub["status"], sub["status"]),
                               price=pricing.money(cfg, sub["amount_pence"]) + " a " + per if sub["plan"] == "paid" else "",
                               ended=sub["status"] == "canceled",
                               can_cancel=sub["status"] != "canceled")
        out[row["id"]] = item
    return out


def active_students(school_id):
    """How many students a school is billed for: those currently enrolled in a class. Ex-students (kept under each
    class's X with the day they left) never count, and a Student ID that appears in two classes counts once."""
    seen, unnamed = set(), 0
    with router().get(school_id).transaction() as tx:
        rows = tx.execute("SELECT json FROM classrooms").fetchall()
    for row in rows:
        try:
            students = json.loads(row["json"]).get("S", [])
        except ValueError:
            continue
        for student in students:
            sid = str(student.get("sid") or "").strip().casefold() if isinstance(student, dict) else ""
            if sid:
                seen.add(sid)
            else:
                unnamed += 1
    return len(seen) + unnamed


# --- Sign-up ---------------------------------------------------------------------------------------------------------

def _signup_page(status=200, **context):
    cfg = pricing.config()
    form = context.pop("form", None) or {}
    try:
        students = int(form.get("students") or request.args.get("students") or pricing.free_students(cfg) or 1)
    except ValueError:
        students = pricing.free_students(cfg) or 1
    students = max(1, min(students, pricing.MAX_STUDENTS))
    billing = form.get("billing") or request.args.get("billing") or "monthly"
    billing = billing if billing in ("monthly", "annual") else "monthly"
    return auth.page("signup.html", status, form=form, students=students, billing=billing, top=pricing.self_service_max(cfg),
                     free=pricing.free_students(cfg), calc=json.dumps(pricing.public(cfg)),
                     months_free=cfg["annual_months_free"], code_on=bool(current_app.config.get("SIGNUP_TEST_CODE")),
                     name_max=schools.NAME_MAX, terms_version=TERMS_VERSION, contact=pricing.contact_email(),
                     payments_on=stripe().enabled, **context)


def _closed():
    return not current_app.config.get("SIGNUP_OPEN", True)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if _closed():
        return auth.page("signup_closed.html", 200, contact=pricing.contact_email())
    if request.method == "GET":
        return _signup_page()
    auth.check_form_token()
    form = {k: request.form.get(k, "") for k in ("school", "name", "email", "students", "billing", "code")}
    if request.form.get("website"):  # a field people never see; only a script fills it in
        return _signup_page(400, form=form, error="That couldn’t be sent. Reload the page and try again.")
    cfg = pricing.config()
    school = site_settings.tidy(form["school"])
    name = " ".join(form["name"].split())[:80]
    email = auth.norm_email(form["email"])[:254]
    billing = form["billing"] if form["billing"] in ("monthly", "annual") else "monthly"
    code = form["code"].strip()
    try:
        students = int(form["students"])
    except ValueError:
        students = 0
    error = None
    if not school or len(school) > schools.NAME_MAX:
        error = f"Enter your madrasah’s name, in {schools.NAME_MAX} characters or fewer."
    elif not name:
        error = "Enter your name."
    elif not auth.EMAIL.fullmatch(email):
        error = "Enter a valid email address."
    elif students < 1:
        error = "Enter how many active students you have, as a number."
    elif students > pricing.self_service_max(cfg):
        error = (f"For more than {pricing.self_service_max(cfg)} students we prepare a quotation. "
                 + (f"Email {pricing.contact_email()} and we’ll be in touch." if pricing.contact_email() else "Contact us and we’ll be in touch."))
    elif request.form.get("terms") != "1":
        error = "Tick the box to agree to the Terms and Conditions before continuing."
    if error:
        return _signup_page(400, form=form, error=error)
    with router().control.transaction() as tx:
        if auth.limited(tx, "signup", email, 5, 10, failures_only=False):
            return _signup_page(429, form=form, error="Too many sign-ups from here just now. Wait 15 minutes and try again.")
        auth.record_attempt(tx, "signup", email, True)
    if email == auth.developer_email() or router().home_of(email) is not None:
        return _signup_page(400, form=form, error="That email can’t be used for a new madrasah. If you already have an "
                                                  "Ilmi account, sign in instead, or use another email address.")
    if len(router().all()) >= schools.MAX_SCHOOLS:
        return _signup_page(400, form=form, error="Ilmi isn’t taking new madrasahs just now. Contact us and we’ll help.")
    quote = pricing.quote(cfg, students, billing == "annual")
    plan = "free" if quote["free"] else "paid"
    if code:
        expected = current_app.config.get("SIGNUP_TEST_CODE") or ""
        with router().control.transaction() as tx:
            blocked = auth.limited(tx, "signup_code", email, 5, 5)
            ok = bool(expected) and not blocked and hmac.compare_digest(code.encode(), expected.encode())
            auth.record_attempt(tx, "signup_code", email, ok)
        if not ok:
            return _signup_page(400, form=form, error="That access code isn’t right. Leave the box empty if you don’t have one.")
        plan = "test"
    elif plan == "paid" and not stripe().enabled:
        return _signup_page(400, form=form, error="Online payment isn’t switched on yet, so paid plans can’t be started "
                                                  "here today. " + (f"Email {pricing.contact_email()} and we’ll set you up."
                                                                    if pricing.contact_email() else "Contact us and we’ll set you up."))
    token = secrets.token_urlsafe(32)
    with router().control.transaction() as tx:
        tx.insert("INSERT INTO signups (token_hash, school_name, admin_name, email, students, billing, amount_pence, plan, "
                  "status, terms_version, terms_accepted_at, ip, user_agent, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                  (auth.token_hash(token), school, name, email, students, billing if plan == "paid" else "",
                   quote["charge"] if plan == "paid" else 0, plan, TERMS_VERSION, auth.now(), request.remote_addr,
                   (request.user_agent.string or "")[:200], auth.now()))
    return redirect(f"/signup/{token}", code=303)


def _find(token):
    if not token or len(token) > 100:
        abort(404)
    with router().control.transaction() as tx:
        row = tx.execute("SELECT * FROM signups WHERE token_hash = ?", (auth.token_hash(token),)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _expired(row):
    return row["status"] == "pending" and row["created_at"] + SIGNUP_TTL < auth.now()


@bp.get("/signup/<token>")
def review(token):
    row = _find(token)
    if row["status"] != "pending":
        return redirect(f"/signup/{token}/done", code=303)
    if _expired(row):
        return auth.page("signup_done.html", 410, state="expired", row=row)
    cfg = pricing.config()
    quote = pricing.quote(cfg, row["students"], row["billing"] == "annual")
    return auth.page("signup_review.html", row=row, quote=quote, token=token, money=lambda p, k=False: pricing.money(cfg, p, k),
                     months_free=cfg["annual_months_free"], minimum=pricing.money(cfg, cfg["minimum_monthly_pence"]),
                     test_mode=stripe().enabled and not stripe().live, error=request.args.get("e") == "pay" and
                     "Payment couldn’t be started just now. Nothing has been charged. Try again in a minute." or None)


@bp.post("/signup/<token>/go")
def go(token):
    auth.check_form_token()
    row = _find(token)
    if row["status"] != "pending":
        return redirect(f"/signup/{token}/done", code=303)
    if _expired(row):
        return auth.page("signup_done.html", 410, state="expired", row=row)
    if row["plan"] in ("free", "test"):
        result = fulfil(row["id"])
        return _after(token, result)
    cfg = pricing.config()
    # The amount is worked out again here, so a price change between the two pages can't charge a stale amount.
    quote = pricing.quote(cfg, row["students"], row["billing"] == "annual")
    if quote["custom"] or quote["free"] or quote["charge"] != row["amount_pence"]:
        with router().control.transaction() as tx:
            tx.execute("UPDATE signups SET status = 'expired' WHERE id = ?", (row["id"],))
        return auth.page("signup_done.html", 410, state="expired", row=row)
    base = auth.public_url(f"/signup/{token}")
    interval = "year" if row["billing"] == "annual" else "month"
    try:
        session = stripe().call("POST", "/v1/checkout/sessions", {
            "mode": "subscription",
            "client_reference_id": str(row["id"]),
            "customer_email": row["email"],
            "success_url": base + "/done?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": base,
            "line_items": [{"quantity": 1, "price_data": {
                "currency": cfg["currency"], "unit_amount": quote["charge"], "recurring": {"interval": interval},
                "product_data": {"name": f"Ilmi for {row['school_name']}",
                                 "description": f"{row['students']} active students, billed each {interval}"}}}],
            "metadata": {"signup": str(row["id"]), "students": str(row["students"])},
            "subscription_data": {"metadata": {"signup": str(row["id"]), "students": str(row["students"])}},
        }, idempotency_key=f"ilmi-signup-{row['id']}-{quote['charge']}")
    except StripeError as error:
        current_app.logger.error("Stripe couldn't start checkout for sign-up %s: %s", row["id"], error)
        return redirect(f"/signup/{token}?e=pay", code=303)
    url = session.get("url") or ""
    if not url.startswith("https://checkout.stripe.com/"):
        current_app.logger.error("Stripe gave no checkout address for sign-up %s.", row["id"])
        return redirect(f"/signup/{token}?e=pay", code=303)
    with router().control.transaction() as tx:
        tx.execute("UPDATE signups SET stripe_session = ? WHERE id = ?", (session.get("id"), row["id"]))
    return redirect(url, code=303)


def _paid(session):
    return session.get("mode") == "subscription" and session.get("status") == "complete" and \
        session.get("payment_status") in ("paid", "no_payment_required")


@bp.get("/signup/<token>/done")
def done(token):
    row = _find(token)
    if row["status"] == "pending" and row["plan"] == "paid" and stripe().enabled:
        session_id = request.args.get("session_id") or row["stripe_session"] or ""
        # Only the session this sign-up started counts, whatever the address says.
        if session_id and session_id == row["stripe_session"]:
            try:
                session = stripe().call("GET", f"/v1/checkout/sessions/{urllib.parse.quote(session_id, safe='')}")
            except StripeError as error:
                current_app.logger.warning("Couldn't read checkout session for sign-up %s: %s", row["id"], error)
                session = {}
            if _paid(session) and session.get("client_reference_id") == str(row["id"]):
                result = fulfil(row["id"], customer=session.get("customer"), subscription=session.get("subscription"))
                return _after(token, result)
    return _after(token, None)


def _after(token, result):
    """Where a sign-up's own browser goes once its school exists: straight to choosing a password when that is
    allowed, otherwise a page saying what happens next."""
    row = _find(token)
    if row["status"] == "done":
        link = (result or {}).get("link") or _continue_link(row)
        if link and (row["plan"] != "free" or not current_app.config["MAILER"].enabled):
            return redirect(link, code=303)
        return auth.page("signup_done.html", state="email", row=row)
    if row["status"] == "problem":
        return auth.page("signup_done.html", 409, state="problem", row=row, contact=pricing.contact_email())
    if _expired(row) or row["status"] == "expired":
        return auth.page("signup_done.html", 410, state="expired", row=row)
    # Paid and still waiting for Stripe's word, or another request is making the school this second.
    return auth.page("signup_done.html", 202, state="waiting", row=row, token=token)


def _continue_link(row):
    """A fresh link for the admin of a school just made to choose their password, while they haven't yet. The
    emailed link keeps working: this adds a token without cancelling that one."""
    if not row["school_id"] or (row["finished_at"] or 0) + SIGNUP_TTL < auth.now():
        return None
    every = router()
    with every.use(row["school_id"]), every.transaction() as tx:
        user = tx.execute("SELECT id FROM users WHERE email = ? AND developer = 0 AND disabled = 0 AND password_hash IS NULL",
                          (row["email"],)).fetchone()
        if not user:
            return None
        token, t = schools.new_token(), auth.now()
        tx.execute("INSERT INTO user_tokens VALUES (?, ?, 'invite', ?, ?, NULL)",
                   (auth.token_hash(token), user["id"], t, t + auth.INVITE_TTL))
    return auth.public_url(f"/invite/{token}")


def fulfil(signup_id, customer=None, subscription=None):
    """Makes the school a sign-up asked for, once. Returns {"school_id", "link"} when this call made it, and None
    when it was already made (or is being made by another request this moment); Stripe's ids are noted either way."""
    every = router()
    with every.control.transaction() as tx:
        claimed = tx.execute("UPDATE signups SET status = 'creating' WHERE id = ? AND status = 'pending'", (signup_id,)).rowcount
        row = tx.execute("SELECT * FROM signups WHERE id = ?", (signup_id,)).fetchone()
    if not row:
        return None
    row = dict(row)
    if not claimed:
        if row["school_id"] and (customer or subscription):
            _note_stripe(row["school_id"], customer, subscription)
        return None
    school_id = None
    try:
        if every.home_of(row["email"]) is not None or len(every.all()) >= schools.MAX_SCHOOLS:
            # The email got an account elsewhere while this sign-up waited. Nothing is made; the developer sorts it out.
            with every.control.transaction() as tx:
                tx.execute("UPDATE signups SET status = 'problem' WHERE id = ?", (signup_id,))
            current_app.logger.error("Sign-up %s finished but its school couldn't be made (email in use, or Ilmi is full).", signup_id)
            return None
        school_id = every.create(row["school_name"])
        t = auth.now()
        g.pop("site_title", None)
        with every.use(school_id):
            with every.transaction() as tx:
                user_id = tx.insert("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, 'admin', ?)",
                                    (row["email"], row["admin_name"], t))
                every.claim(row["email"], school_id)
                auth.audit(tx, "user.invited", detail={"email": row["email"], "role": "admin", "user": user_id, "signup": signup_id})
                # The admin's first sign-in opens School setup (setup.py) until they finish or skip it.
                tx.execute("INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES ('setup', 'pending', ?, NULL)", (t,))
                link = auth.issue_link(tx, user_id, "invite")
            with every.control.transaction() as tx:
                tx.execute("INSERT INTO subscriptions (school_id, plan, billing, students, amount_pence, status, stripe_customer, "
                           "stripe_subscription, signup_id, terms_version, terms_accepted_at, terms_accepted_by, created_at, updated_at) "
                           "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
                           (school_id, row["plan"], row["billing"], row["students"], row["amount_pence"], customer, subscription,
                            signup_id, row["terms_version"], row["terms_accepted_at"], row["email"], t, t))
                tx.execute("UPDATE signups SET status = 'done', school_id = ?, finished_at = ? WHERE id = ?", (school_id, t, signup_id))
                auth.audit(tx, "school.signup", detail={"school": school_id, "name": row["school_name"], "plan": row["plan"],
                                                        "students": row["students"]})
            auth.send_link({"name": row["admin_name"], "email": row["email"], "role": "admin"}, "invite", link)
        g.pop("site_title", None)
        return {"school_id": school_id, "link": link}
    except Exception:
        # Back to pending when nothing was made, so the next attempt (the webhook's retry, or a reload) tries again.
        # A school already made is kept, and the sign-up is left for the developer to look at.
        with every.control.transaction() as tx:
            tx.execute("UPDATE signups SET status = ? WHERE id = ? AND status = 'creating'",
                       ("problem" if school_id else "pending", signup_id))
        raise


def _note_stripe(school_id, customer, subscription):
    with router().control.transaction() as tx:
        tx.execute("UPDATE subscriptions SET stripe_customer = COALESCE(stripe_customer, ?), "
                   "stripe_subscription = COALESCE(stripe_subscription, ?), updated_at = ? WHERE school_id = ?",
                   (customer, subscription, auth.now(), school_id))


# --- Stripe's webhook --------------------------------------------------------------------------------------------------

def _period_end(sub):
    """When the paid period ends: on the subscription's item in current API versions, on the subscription before."""
    items = ((sub.get("items") or {}).get("data") or [{}])
    return items[0].get("current_period_end") or sub.get("current_period_end")


def _apply_subscription(sub):
    """Copies a Stripe subscription's state onto the school that has it."""
    with router().control.transaction() as tx:
        tx.execute("UPDATE subscriptions SET status = ?, cancel_at_period_end = ?, current_period_end = ?, canceled_at = ?, "
                   "updated_at = ? WHERE stripe_subscription = ?",
                   (str(sub.get("status") or "")[:40], 1 if sub.get("cancel_at_period_end") or sub.get("cancel_at") else 0,
                    _period_end(sub), sub.get("canceled_at") or sub.get("ended_at"), auth.now(), sub.get("id")))


@bp.post("/stripe/webhook")
def webhook():
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET") or ""
    payload = request.get_data(cache=False)
    if not stripe().enabled or not secret:
        abort(404)
    if not verify_webhook(payload, request.headers.get("Stripe-Signature", ""), secret):
        abort(400)
    try:
        event = json.loads(payload.decode("utf-8"))
        event_id, kind, obj = event["id"], event["type"], event["data"]["object"]
    except (ValueError, KeyError, TypeError):
        abort(400)
    if not isinstance(event_id, str) or not isinstance(obj, dict) or len(event_id) > 255:
        abort(400)
    with router().control.transaction() as tx:
        if tx.execute("SELECT 1 FROM stripe_events WHERE id = ?", (event_id,)).fetchone():
            return {"received": True, "repeat": True}
    if kind == "checkout.session.completed":
        ref = str(obj.get("client_reference_id") or "")
        if ref.isdigit() and _paid(obj):
            with router().control.transaction() as tx:
                known = tx.execute("SELECT stripe_session FROM signups WHERE id = ?", (int(ref),)).fetchone()
            if known and known["stripe_session"] == obj.get("id"):
                fulfil(int(ref), customer=obj.get("customer"), subscription=obj.get("subscription"))
    elif kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        if kind.endswith("deleted"):
            obj = dict(obj, status="canceled")
        _apply_subscription(obj)
    elif kind == "invoice.upcoming":
        sub_id = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
        if sub_id:
            with router().control.transaction() as tx:
                row = tx.execute("SELECT school_id FROM subscriptions WHERE stripe_subscription = ?", (sub_id,)).fetchone()
            if row:
                try:
                    resync(row["school_id"])
                except StripeError as error:
                    current_app.logger.error("Couldn't update school %s's price before renewal: %s", row["school_id"], error)
    # Recorded last: an event whose handling failed is delivered again by Stripe and handled then.
    with router().control.transaction() as tx:
        tx.execute("INSERT INTO stripe_events (id, type, received_at) VALUES (?, ?, ?) ON CONFLICT (id) DO NOTHING",
                   (event_id, str(kind)[:80], auth.now()))
        tx.execute("DELETE FROM stripe_events WHERE received_at < ?", (auth.now() - 60 * 86400,))
    return {"received": True}


# --- Keeping the price in step with the students ------------------------------------------------------------------------

def resync(school_id):
    """Recounts a school's active students and, for a paid plan, moves its subscription to the price for that many
    from the next renewal (nothing is charged or refunded part-way through a period). Returns what it found:
    {"students", "amount", "changed", "note"}."""
    with router().control.transaction() as tx:
        sub = tx.execute("SELECT * FROM subscriptions WHERE school_id = ?", (school_id,)).fetchone()
    if not sub:
        return {"students": active_students(school_id), "amount": 0, "changed": False, "note": "This school has no subscription."}
    sub, cfg = dict(sub), pricing.config()
    students = active_students(school_id)
    out = {"students": students, "amount": sub["amount_pence"], "changed": False, "note": ""}
    if sub["plan"] != "paid" or sub["status"] == "canceled" or not sub["stripe_subscription"]:
        with router().control.transaction() as tx:
            tx.execute("UPDATE subscriptions SET students = ?, updated_at = ? WHERE school_id = ?", (students, auth.now(), school_id))
        if sub["plan"] == "free" and students > pricing.free_students(cfg):
            out["note"] = f"It has more than the {pricing.free_students(cfg)} students the free plan covers."
        return out
    quote = pricing.quote(cfg, max(students, 1), sub["billing"] == "annual")
    if quote["custom"]:
        out["note"] = (f"It has {students} students, more than the {pricing.self_service_max(cfg)} with a listed price. "
                       "Its subscription is unchanged: agree a quotation with the school.")
        return out
    if quote["charge"] != sub["amount_pence"]:
        current = stripe().call("GET", f"/v1/subscriptions/{urllib.parse.quote(sub['stripe_subscription'], safe='')}")
        item = ((current.get("items") or {}).get("data") or [None])[0]
        if not item:
            raise StripeError("The subscription has no item to reprice.")
        price = item.get("price") or {}
        product = price.get("product") if isinstance(price.get("product"), str) else (price.get("product") or {}).get("id")
        stripe().call("POST", f"/v1/subscriptions/{urllib.parse.quote(sub['stripe_subscription'], safe='')}", {
            "proration_behavior": "none",
            "items": [{"id": item["id"], "price_data": {
                "currency": cfg["currency"], "product": product, "unit_amount": quote["charge"],
                "recurring": {"interval": "year" if sub["billing"] == "annual" else "month"}}}],
            "metadata": {"students": str(students)}})
        out.update(amount=quote["charge"], changed=True)
    with router().control.transaction() as tx:
        tx.execute("UPDATE subscriptions SET students = ?, amount_pence = ?, updated_at = ? WHERE school_id = ?",
                   (students, out["amount"], auth.now(), school_id))
    return out


def cancel(school_id, at_period_end):
    """Cancels a school's subscription: at the end of the period it has paid for, or now. Returns a sentence for the
    developer. A free or test plan has nothing at Stripe, and is simply marked cancelled."""
    with router().control.transaction() as tx:
        sub = tx.execute("SELECT * FROM subscriptions WHERE school_id = ?", (school_id,)).fetchone()
    if not sub:
        return None
    sub, t = dict(sub), auth.now()
    path = f"/v1/subscriptions/{urllib.parse.quote(sub['stripe_subscription'] or '', safe='')}"
    if sub["plan"] == "paid" and sub["stripe_subscription"] and sub["status"] != "canceled":
        if at_period_end:
            _apply_subscription(stripe().call("POST", path, {"cancel_at_period_end": True}))
            said = "Its subscription ends when the period it has paid for does, and it isn’t charged again."
        else:
            result = stripe().call("DELETE", path)
            _apply_subscription(dict(result, status="canceled", canceled_at=result.get("canceled_at") or t))
            said = "Its subscription is cancelled as of now. Nothing was refunded: do that in Stripe if it is due."
    else:
        with router().control.transaction() as tx:
            tx.execute("UPDATE subscriptions SET status = 'canceled', canceled_at = ?, updated_at = ? WHERE school_id = ?",
                       (t, t, school_id))
        said = "Its plan is marked cancelled."
    with router().control.transaction() as tx:
        auth.audit(tx, "school.subscription_cancelled", detail={"school": school_id, "at_period_end": bool(at_period_end)})
    return said


# --- The developer's buttons (the forms are on templates/schools.html) --------------------------------------------------

def _school_or_404(school_id):
    if not router().exists(school_id) or school_id == schools.CONTROL:
        abort(404)
    return router().name(school_id)


@bp.post("/schools/<int:school_id>/suspend")
@schools.developer_required
def suspend(school_id):
    auth.check_form_token()
    name = _school_or_404(school_id)
    reason = site_settings.tidy(request.form.get("reason", ""))[:300]
    if request.form.get("confirm") != "1":
        return schools.schools_page(400, error="Tick the box to confirm before suspending a school.", manage_open=school_id)
    set_suspended(school_id, reason or "No reason given")
    return schools.schools_page(notice=f"{name} is suspended. Its staff are signed out and can’t sign in, and its TV and "
                                       "Presenter links show a notice. Nothing was deleted.")


@bp.post("/schools/<int:school_id>/unsuspend")
@schools.developer_required
def unsuspend(school_id):
    auth.check_form_token()
    name = _school_or_404(school_id)
    set_suspended(school_id, None)
    return schools.schools_page(notice=f"{name} is no longer suspended. Its staff can sign in again.")


@bp.post("/schools/<int:school_id>/subscription/cancel")
@schools.developer_required
def cancel_subscription(school_id):
    auth.check_form_token()
    name = _school_or_404(school_id)
    if request.form.get("confirm") != "1":
        return schools.schools_page(400, error="Tick the box to confirm before cancelling a subscription.", manage_open=school_id)
    try:
        said = cancel(school_id, request.form.get("when") != "now")
    except StripeError as error:
        current_app.logger.error("Couldn't cancel school %s's subscription: %s", school_id, error)
        return schools.schools_page(502, error=f"Stripe couldn’t cancel it: {error} Nothing was changed.", manage_open=school_id)
    if said is None:
        return schools.schools_page(400, error=f"{name} has no subscription to cancel.", manage_open=school_id)
    return schools.schools_page(notice=f"{name}: {said} The school itself is untouched; suspend it to lock it.")


@bp.post("/schools/<int:school_id>/subscription/sync")
@schools.developer_required
def sync_subscription(school_id):
    auth.check_form_token()
    name = _school_or_404(school_id)
    try:
        found = resync(school_id)
    except StripeError as error:
        current_app.logger.error("Couldn't recalculate school %s's subscription: %s", school_id, error)
        return schools.schools_page(502, error=f"Stripe couldn’t update it: {error} Nothing was changed.", manage_open=school_id)
    cfg = pricing.config()
    said = f"{name} has {found['students']} active student{'s' if found['students'] != 1 else ''}."
    if found["changed"]:
        said += f" From its next renewal it pays {pricing.money(cfg, found['amount'])}."
    elif found["note"]:
        said += " " + found["note"]
    else:
        said += " Its price is unchanged."
    return schools.schools_page(notice=said)


# --- Terms -------------------------------------------------------------------------------------------------------------

@bp.get("/terms")
def terms():
    cfg = pricing.config()
    return auth.page("terms.html", version=TERMS_VERSION, legal_name=current_app.config.get("LEGAL_NAME") or "Ilmi",
                     legal_address=current_app.config.get("LEGAL_ADDRESS") or "", contact=pricing.contact_email(),
                     bands=pricing.band_rows(cfg), minimum=pricing.money(cfg, cfg["minimum_monthly_pence"]),
                     months_free=cfg["annual_months_free"], top=pricing.self_service_max(cfg), free=pricing.free_students(cfg))
