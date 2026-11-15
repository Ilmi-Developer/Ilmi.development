"""Ilmi's prices: one configuration, used by the pricing page, its calculator, sign-up and billing.

A madrasah pays by its number of active enrolled students. The rates are graduated bands: each rate applies only
to the students inside its band, never to every student once a threshold is passed. With the defaults, 150 students
are 20 free, 30 at 30p, 50 at 25p and 50 at 20p: £31.50 a month.

Everything that can change lives in DEFAULTS and nowhere else: the bands, the free allowance, the smallest monthly
charge for a paid subscription, the annual discount, the size above which a madrasah gets a quotation, and the four
cards on the pricing page. The cards are marketing categories, not feature packages: every paying madrasah has the
same platform, and a card's "from" rate is read from the bands (the band its `rate_at` student falls in), so a card
can't show a rate the calculator doesn't use. PRICING_JSON (a JSON object) replaces any of these keys without a
code change; a value that doesn't make sense is refused at startup and the defaults are used.

Amounts are whole pence. The page's calculator (static/pricing.js) gets this configuration as JSON and does the
same sum; test_server checks the two agree.
"""
import json
from decimal import ROUND_HALF_UP, Decimal

from flask import Blueprint, Response, current_app

import auth

bp = Blueprint("pricing", __name__)

DEFAULTS = {
    "currency": "gbp",
    "symbol": "£",
    # Graduated bands, in order. `upto` is the last student the rate covers; `pence` is per student per month.
    "bands": [
        {"upto": 20, "pence": 0},
        {"upto": 50, "pence": 30},
        {"upto": 100, "pence": 25},
        {"upto": 200, "pence": 20},
        {"upto": 400, "pence": 15},
        {"upto": 750, "pence": 12},
    ],
    # The least a paid subscription costs in a month (0 for none). The free allowance is never charged.
    "minimum_monthly_pence": 1000,
    # Paying for a year costs this many months fewer than twelve.
    "annual_months_free": 2,
    # The four cards. `min`/`max` are the sizes a card is written for (max None: no upper end); `rate_at` names the
    # student whose band gives the card's "from" rate; `cta` is "signup" or "contact".
    "cards": [
        {"key": "free", "name": "Free", "tagline": "Try Ilmi with a small class", "min": 1, "max": 20, "cta": "signup",
         "cta_label": "Get Started Free",
         "features": ["Up to 20 active students", "Basic student management", "Attendance tracking",
                      "Qur’an and memorisation progress", "Basic reports", "No credit card required"]},
        {"key": "small", "name": "Small Madrasah", "tagline": "For 21 to 100 students", "min": 21, "max": 100,
         "rate_at": 21, "cta": "signup", "cta_label": "Get Started",
         "lead": "The complete core platform:",
         "features": ["Unlimited teachers and administrators", "Student and class management",
                      "Attendance and assessment tracking", "Qur’an and memorisation tracking", "Parent portal",
                      "Fee and finance management", "Reports and analytics"]},
        {"key": "growing", "name": "Growing Madrasah", "tagline": "For 101 to 400 students", "min": 101, "max": 400,
         "rate_at": 400, "cta": "signup", "cta_label": "Get Started", "popular": True,
         "lead": "Everything in Small Madrasah, plus:",
         "features": ["Lower marginal pricing as enrolment increases", "Unlimited teachers and administrators",
                      "Full access to Ilmi’s core functionality"]},
        {"key": "large", "name": "Large Madrasah", "tagline": "For 401 students and more", "min": 401, "max": None,
         "cta": "contact", "cta_label": "Contact Us",
         "lead": "All core Ilmi functionality, plus:",
         "features": ["Volume-based pricing", "Support for larger organisations", "Multi-site management options",
                      "Tailored onboarding and migration assistance"]},
    ],
}
MAX_STUDENTS = 100_000


class ConfigError(ValueError):
    pass


def check(config):
    """Raises ConfigError when a pricing configuration can't be used."""
    bands = config.get("bands")
    if not isinstance(bands, list) or not bands:
        raise ConfigError("bands must be a list with at least one band")
    last = 0
    for band in bands:
        if not isinstance(band, dict) or type(band.get("upto")) is not int or band["upto"] <= last:
            raise ConfigError("each band needs a whole number `upto` larger than the band before it")
        if type(band.get("pence")) is not int or band["pence"] < 0:
            raise ConfigError("each band needs `pence`, a whole number of pence that isn't negative")
        last = band["upto"]
    for key in ("minimum_monthly_pence", "annual_months_free"):
        if type(config.get(key)) is not int or config[key] < 0:
            raise ConfigError(f"{key} must be a whole number that isn't negative")
    if config["annual_months_free"] >= 12:
        raise ConfigError("annual_months_free must be fewer than 12")
    if not isinstance(config.get("currency"), str) or len(config["currency"]) != 3:
        raise ConfigError("currency must be a three-letter code")
    if not isinstance(config.get("symbol"), str) or not 0 < len(config["symbol"]) <= 3:
        raise ConfigError("symbol must be one to three characters")
    cards = config.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ConfigError("cards must be a list")
    for card in cards:
        if not isinstance(card, dict) or not all(isinstance(card.get(k), str) and card[k] for k in ("key", "name", "cta_label")):
            raise ConfigError("each card needs a key, a name and a cta_label")
        if card.get("cta") not in ("signup", "contact") or not isinstance(card.get("features"), list):
            raise ConfigError("each card needs cta (signup or contact) and a list of features")
        if type(card.get("min")) is not int or (card.get("max") is not None and type(card["max"]) is not int):
            raise ConfigError("each card needs whole-number sizes")
    return config


def load(raw=""):
    """The configuration to use: the defaults, with PRICING_JSON's keys over them when it is valid."""
    config = json.loads(json.dumps(DEFAULTS))
    if raw and raw.strip():
        try:
            over = json.loads(raw)
            if not isinstance(over, dict):
                raise ConfigError("PRICING_JSON must be a JSON object")
            merged = dict(config, **{k: v for k, v in over.items() if k in DEFAULTS})
            config = check(merged)
        except (ValueError, ConfigError) as error:
            print(f"PRICING_JSON can't be used ({error}); using Ilmi's built-in prices.", flush=True)
            config = json.loads(json.dumps(DEFAULTS))
    return check(config)


def config():
    return current_app.config["PRICING"]


def free_students(cfg):
    """How many students cost nothing: the first band's reach when its rate is 0."""
    first = cfg["bands"][0]
    return first["upto"] if first["pence"] == 0 else 0


def self_service_max(cfg):
    """The largest madrasah that has a price; anything bigger gets a quotation."""
    return cfg["bands"][-1]["upto"]


def rate_for(cfg, student):
    """The rate of the band the nth student falls in, in pence, or None past the last band."""
    for band in cfg["bands"]:
        if student <= band["upto"]:
            return band["pence"]
    return None


def money(cfg, pence, always_pence=False):
    """£31.50, £10, £0.30: whole pounds lose their pence unless asked to keep them."""
    pounds = (Decimal(pence) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{pounds:,.2f}"
    if text.endswith(".00") and not always_pence:
        text = text[:-3]
    return cfg["symbol"] + text


def quote(cfg, students, annual=False):
    """What a madrasah with this many active students pays.

    Returns a dict: `custom` (True when it is too large for a listed price, and then nothing else is priced),
    `lines` (one per band that holds any of its students: from, to, count, pence, subtotal), `monthly` (pence for a
    month, after the minimum), `minimum_applied`, `free` (nothing to pay), and for annual billing `annual` (pence
    for the year) and `saving`. `charge` is what is taken each period, and `period` its name.
    """
    students = max(0, min(int(students), MAX_STUDENTS))
    out = {"students": students, "annual_billing": bool(annual), "custom": False, "lines": [], "subtotal": 0,
           "monthly": 0, "minimum_applied": False, "free": True, "annual": 0, "saving": 0, "charge": 0,
           "period": "year" if annual else "month"}
    if students > self_service_max(cfg):
        out.update(custom=True, free=False)
        return out
    start = 1
    for band in cfg["bands"]:
        if students < start:
            break
        count = min(students, band["upto"]) - start + 1
        out["lines"].append({"from": start, "to": band["upto"], "count": count, "pence": band["pence"],
                             "subtotal": count * band["pence"]})
        out["subtotal"] += count * band["pence"]
        start = band["upto"] + 1
    monthly = out["subtotal"]
    if monthly > 0 and monthly < cfg["minimum_monthly_pence"]:
        monthly, out["minimum_applied"] = cfg["minimum_monthly_pence"], True
    out["monthly"] = monthly
    out["free"] = monthly == 0
    out["annual"] = monthly * (12 - cfg["annual_months_free"])
    out["saving"] = monthly * cfg["annual_months_free"]
    out["charge"] = out["annual"] if annual else monthly
    return out


def public(cfg):
    """The configuration as the calculator gets it: just what the sum needs."""
    return {"symbol": cfg["symbol"], "bands": cfg["bands"], "minimum": cfg["minimum_monthly_pence"],
            "monthsFree": cfg["annual_months_free"], "max": self_service_max(cfg), "free": free_students(cfg)}


def band_rows(cfg):
    """The bands as the page's table shows them."""
    rows, start = [], 1
    for band in cfg["bands"]:
        rows.append({"from": start, "to": band["upto"], "pence": band["pence"],
                     "rate": "Free" if band["pence"] == 0 else money(cfg, band["pence"], True)})
        start = band["upto"] + 1
    return rows


def cards(cfg):
    """The four cards with their prices worked out from the bands."""
    out = []
    for card in cfg["cards"]:
        item = dict(card)
        size = card["max"] if card.get("max") else None
        if card["cta"] == "contact" or (size and size > self_service_max(cfg) and "rate_at" not in card):
            item["price"], item["unit"], item["note"] = "Custom", "pricing", "A quotation for your madrasah’s size."
        elif "rate_at" in card and rate_for(cfg, card["rate_at"]) is not None:
            rates = []
            for row in band_rows(cfg):
                if row["to"] >= card["min"] and (size is None or row["from"] <= size) and row["pence"]:
                    lo, hi = max(row["from"], card["min"]), min(row["to"], size or row["to"])
                    rates.append(f"{row['rate']} for students {lo} to {hi}")
            item["price"] = money(cfg, rate_for(cfg, card["rate_at"]), True)
            item["from"], item["unit"] = True, "per student / month"
            item["note"] = "; ".join(rates) + f". The first {free_students(cfg)} are always free." if free_students(cfg) \
                else "; ".join(rates) + "."
        else:
            item["price"], item["unit"], item["note"] = money(cfg, 0), "/ month", "Free for small classes, for as long as you need."
        item["href"] = "#contact" if card["cta"] == "contact" else f"/signup?students={card['min'] if card['min'] > 1 else card['max'] or 1}"
        out.append(item)
    return out


def contact_email():
    return current_app.config.get("SALES_EMAIL") or current_app.config.get("SUPPORT_EMAIL", "")


@bp.get("/pricing")
def page():
    cfg = config()
    example = quote(cfg, 150)
    return auth.page("pricing.html", cards=cards(cfg), bands=band_rows(cfg), cfg=cfg, calc=json.dumps(public(cfg)),
                     free=free_students(cfg), top=self_service_max(cfg), minimum=money(cfg, cfg["minimum_monthly_pence"]),
                     months_free=cfg["annual_months_free"], example=example, money=lambda p, k=False: money(cfg, p, k),
                     contact=contact_email(), signup_open=current_app.config.get("SIGNUP_OPEN", True))


@bp.get("/pricing.css")
def styles():
    root = current_app.config["ROOT"]
    css = (root / "src" / "tokens.css").read_text(encoding="utf-8") + "\n" + \
        (root / "static" / "welcome.css").read_text(encoding="utf-8") + "\n" + \
        (root / "static" / "pricing.css").read_text(encoding="utf-8")
    return Response(css, mimetype="text/css")


@bp.get("/pricing.js")
def script():
    return Response((current_app.config["ROOT"] / "static" / "pricing.js").read_text(encoding="utf-8"),
                    mimetype="text/javascript")
