"""Themes: how the signed-in pages look for each person.

Account has a theme picker. Ilmi (the default) and Dark are written by hand in src/tokens.css; Pastel pink,
Midnight, Stealth and Coral, and every theme a teacher makes, are four colours (menu, accent, background, cards)
and an optional background photo. derive() turns those into the site's colour tokens and picks every text, line
and logo colour so it stays readable: body text at 7:1 or more on the cards, smaller grey text, links, headings
and the menu's text at 4.5:1 or more, and white text on accent buttons at 4.5:1 or more. A colour the person
chose is only moved (lighter or darker, same hue) when no text colour could be read on it.

A photo is shown under the page head and around the cards. Text that sits straight on it (page titles and tabs)
gets its own colours (--c-*), and a shade of the canvas colour is laid over the photo, at least as strong as
needed for that text to read at 4.5:1 almost everywhere on the photo. The photo's crop, blur, brightness, colour
and shade are baked into the JPEG that pages show; the original stays so the theme can be changed again.

A teacher can share a theme they made, so the other staff see it in their picker too. Only its maker can change
it; anyone can use it or make their own copy. Admins can delete a shared theme. Whoever was using a theme that is
deleted or no longer shared goes back to Ilmi.

The sign-in and password pages are never themed (templates/base.html).
"""
import hashlib
import io
import json
import re

from flask import Blueprint, Response, abort, g, jsonify, redirect, request
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

import auth

bp = Blueprint("themes", __name__)

DEFAULT = "light"
# Written by hand in src/tokens.css; data-mode="dark" carries the dark tokens for every dark theme.
BUILT_IN = {"light": ("Ilmi", "light"), "dark": ("Dark", "dark")}
PRESETS = {
    "pink": {"name": "Pastel pink", "menu": "#f3d3df", "accent": "#c2477a", "bg": "#fbeff3", "card": "#ffffff"},
    "midnight": {"name": "Midnight", "menu": "#16131f", "accent": "#9a86e8", "bg": "#131119", "card": "#1d1a27"},
    "stealth": {"name": "Stealth", "menu": "#0b0b0c", "accent": "#0b0b0c", "bg": "#efefef", "card": "#ffffff"},
    "coral": {"name": "Coral", "menu": "#f2785c", "accent": "#e0573c", "bg": "#fff3ee", "card": "#ffffff"},
}
# The picker's order.
ORDER = ("light", "pink", "midnight", "stealth", "coral", "dark")
# Ilmi's and Dark's own colours as tokens.css draws them, for a new theme started from one of them and for the
# browser's own bar on phones (the menu colour, so it runs on from the light top bar).
LIGHT = {"menu": "#4b552f", "accent": "#4d5a2c", "bg": "#f4efe4", "card": "#fbf8f1"}
DARK = {"menu": "#14150f", "accent": "#617038", "bg": "#181912", "card": "#1e1f17"}
COLOURS = ("menu", "accent", "bg", "card")
# Photo settings and their ranges: zoom (x), focus point (% across and down), blur (px), brightness and
# colour (%), and shade (% of the canvas colour laid over the photo; more is added when text needs it).
PHOTO = {"zoom": (1.0, 3.0, 1.0), "x": (0, 100, 50), "y": (0, 100, 50), "blur": (0, 30, 0),
         "bright": (40, 160, 100), "sat": (0, 200, 100), "shade": (0, 90, 20)}
NAME_MAX = 40
MAX_THEMES = 12
# The browser sends a photo already resized to PHOTO_SIZE (static/theme.js); without scripts a large photo is
# refused by MAX_CONTENT_LENGTH.
PHOTO_SIZE = 2400
SHOWN_SIZE = 2000
MAX_PIXELS = 40_000_000
FORMATS = ("JPEG", "PNG", "WEBP")
# The width text is laid out against when blurring a photo: blur is set in screen pixels.
SCREEN = 1440
HEX = re.compile(r"#[0-9a-f]{6}")


# --- Colour arithmetic -----------------------------------------------------------

def rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def hexa(colour):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in colour)


def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour):
    r, g_, b = rgb(colour) if isinstance(colour, str) else colour
    return 0.2126 * _lin(r) + 0.7152 * _lin(g_) + 0.0722 * _lin(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a, b, t):
    """a moved t of the way to b (0 is a, 1 is b), in sRGB as CSS color-mix does."""
    ra, rb = rgb(a), rgb(b)
    return hexa([x + (y - x) * t for x, y in zip(ra, rb)])


def readable(colour, against, ratio, toward=None):
    """colour, moved toward black or white only as far as needed to reach ratio against every colour in against."""
    against = [against] if isinstance(against, str) else against
    if min(contrast(colour, a) for a in against) >= ratio:
        return colour
    if toward is None:
        toward = "#000000" if luminance(against[0]) > 0.18 else "#ffffff"
    lo, hi = 0.0, 1.0
    if min(contrast(mix(colour, toward, hi), a) for a in against) < ratio:
        return mix(colour, toward, hi)
    for _ in range(14):
        mid = (lo + hi) / 2
        if min(contrast(mix(colour, toward, mid), a) for a in against) >= ratio:
            hi = mid
        else:
            lo = mid
    return mix(colour, toward, hi)


def text_for(surface, dark="#1c2230", light="#eef1f6"):
    """The text colour (dark or light) that reads best on surface, and whether it is the light one."""
    return (light, True) if contrast(light, surface) > contrast(dark, surface) else (dark, False)


def settle(surface, ratio, dark="#1c2230", light="#eef1f6"):
    """surface, moved away from the middle greys until one of the two text colours reads on it at ratio."""
    ink, is_light = text_for(surface, dark, light)
    if contrast(ink, surface) >= ratio:
        return surface
    return readable(surface, ink, ratio, toward="#000000" if is_light else "#ffffff")


# --- From four colours to the site's tokens -----------------------------------------

def clean_spec(raw, base=None):
    """A theme's settings from a form or JSON, every value checked and kept in range."""
    base = base or {}
    spec = {}
    for key in COLOURS:
        value = str(raw.get(key) or base.get(key) or LIGHT[key]).strip().lower()
        spec[key] = value if HEX.fullmatch(value) else (base.get(key) or LIGHT[key])
    photo = {}
    for key, (lo, hi, default) in PHOTO.items():
        value = raw.get("p_" + key, (base.get("photo") or {}).get(key, default))
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        value = min(hi, max(lo, value))
        photo[key] = round(value, 2) if key == "zoom" else int(round(value))
    spec["photo"] = photo
    return spec


def derive(spec, sample=None):
    """Every colour token for a theme, with the mode ("light" or "dark") its cards are, as {"vars", "mode", ...}.

    sample is the photo's small copy (photo_sample()), when the theme has a photo."""
    card = settle(spec["card"], 7)
    ink, dark_cards = text_for(card)
    mode = "dark" if dark_cards else "light"
    toward_ink = "#ffffff" if dark_cards else "#000000"
    ink = readable(ink, card, 7, toward_ink)
    surface2 = mix(card, ink, 0.035 if not dark_cards else 0.05)
    sunk = mix(card, ink, 0.06) if not dark_cards else mix(card, "#000000", 0.25)
    line = mix(card, ink, 0.1 if not dark_cards else 0.12)
    line2 = mix(card, ink, 0.2 if not dark_cards else 0.22)
    # Inside cards, the soft grey panels (.rside and others) use --bg; the page's own background is --canvas.
    panel = mix(card, ink, 0.05) if not dark_cards else mix(card, "#000000", 0.3)
    ink = readable(ink, [card, surface2, sunk, panel], 7, toward_ink)
    ink2 = readable(mix(ink, card, 0.18), [card, surface2, sunk, panel], 7, toward_ink)
    mute = readable(mix(ink, card, 0.38), [card, surface2, sunk, panel], 4.6, toward_ink)

    # The accent as a fill (buttons, selected chips) always carries white text.
    fill = readable(spec["accent"], "#ffffff", 4.6, "#000000")
    fill_hover = mix(fill, "#000000", 0.18) if not dark_cards else mix(fill, "#ffffff", 0.1)
    soft = mix(card, spec["accent"], 0.12 if not dark_cards else 0.2)
    accent_text = readable(spec["accent"], [card, surface2, soft, sunk], 4.6, toward_ink)

    # The side menu (and the phone's top bar).
    menu = settle(spec["menu"], 7, "#141821", "#ffffff")
    strong, light_menu_text = text_for(menu, "#141821", "#ffffff")
    rail_ink = readable(mix(strong, menu, 0.1), menu, 7, strong)
    rail_mute = readable(mix(strong, menu, 0.35), menu, 4.6, strong)
    if contrast(card, menu) >= 1.25:
        rail_on = card
    else:
        # Cards the colour of the menu: the selected item is a tint of the accent instead.
        rail_on = mix(menu, spec["accent"], 0.3) if light_menu_text else mix(menu, "#000000", 0.1)
        rail_on = settle(rail_on, 4.6, "#141821", "#ffffff")
    rail_on_ink = readable(spec["accent"], rail_on, 4.6, "#ffffff" if luminance(rail_on) < 0.18 else "#000000")
    overlay = "255,255,255" if light_menu_text else "20,24,33"
    # The logo on its disc: the accent, but always clear of the disc.
    disc = rail_on
    logo = readable(spec["accent"], disc, 3.2, "#ffffff" if luminance(disc) < 0.18 else "#000000")

    vars_ = {
        "--navy": fill, "--navy-2": fill_hover, "--navy-soft": soft, "--navy-text": accent_text, "--paper": card,
        "--primary": fill, "--primary-hover": fill_hover, "--primary-soft": soft, "--primary-ink": accent_text,
        "--focus": accent_text,
        "--surface": card, "--surface-2": surface2, "--sunk": sunk, "--line": line, "--line-2": line2, "--bg": panel,
        "--ink": ink, "--ink-2": ink2, "--mute": mute,
        "--rail-bg": menu, "--rail-ink": rail_ink, "--rail-mute": rail_mute, "--rail-strong": strong,
        "--rail-line": f"rgba({overlay},.16)", "--rail-hover": f"rgba({overlay},.1)",
        "--rail-on": rail_on, "--rail-on-ink": rail_on_ink, "--logo": logo, "--logo-disc": disc,
    }
    result = {"vars": vars_, "mode": mode, "menu": menu}

    if sample is None:
        canvas = settle(spec["bg"], 7)
        c_ink, _ = text_for(canvas)
        result["canvas"] = canvas
    else:
        shade = scrim(sample, spec["photo"], dark_cards)
        canvas = shade["colour"]
        c_ink = shade["ink"]
        result.update(canvas=canvas, shade=shade["alpha"], scrim=shade["colour"], light_text=shade["ink_light"])
        canvas = shade["worst"]
    toward_c = "#ffffff" if luminance(canvas) < 0.18 else "#000000"
    c_ink = readable(c_ink, canvas, 7, toward_c)
    vars_.update({
        "--canvas": result["canvas"],
        "--c-ink": c_ink,
        "--c-ink-2": readable(mix(c_ink, canvas, 0.15), canvas, 6, toward_c),
        "--c-mute": readable(mix(c_ink, canvas, 0.3), canvas, 4.6, toward_c),
        # Page titles: the accent where it reads, else the canvas text colour.
        "--c-title": c_ink if sample is not None else readable(spec["accent"], canvas, 4.6, toward_c),
        "--c-hover": "rgba(255,255,255,.1)" if luminance(canvas) < 0.18 else "rgba(20,24,33,.06)",
    })
    return result


# --- Photos ------------------------------------------------------------------------

# The photo's small copy kept for working out the shade, and the size text is checked against.
SAMPLE = 96
CHECK = 48


def clean_photo(data):
    """The uploaded photo upright, as a plain JPEG no longer than PHOTO_SIZE, or None if it isn't a usable photo."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data), formats=FORMATS) as image:
            if image.format == "JPEG":
                image.draft("RGB", (PHOTO_SIZE, PHOTO_SIZE))
            if image.width * image.height > MAX_PIXELS:
                return None
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, "#ffffff")
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            image = image.convert("RGB")
            image.thumbnail((PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS)
            if min(image.size) < 32:
                return None
            out = io.BytesIO()
            image.save(out, "JPEG", quality=86, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def _adjust(image, photo, screen_width):
    """The photo cropped, blurred, brightened and coloured as set (the shade is laid on separately)."""
    w, h = image.size
    zoom = photo["zoom"]
    if zoom > 1:
        cw, ch = w / zoom, h / zoom
        left, top = (w - cw) * photo["x"] / 100, (h - ch) * photo["y"] / 100
        image = image.crop((round(left), round(top), round(left + cw), round(top + ch)))
    if photo["blur"]:
        # Blur is set in screen pixels; the picture is drawn about SCREEN wide, after the zoom.
        image = image.filter(ImageFilter.GaussianBlur(photo["blur"] * image.width / screen_width))
    if photo["bright"] != 100:
        image = ImageEnhance.Brightness(image).enhance(photo["bright"] / 100)
    if photo["sat"] != 100:
        image = ImageEnhance.Color(image).enhance(photo["sat"] / 100)
    return image


def photo_sample(original):
    with Image.open(io.BytesIO(original)) as image:
        image.draft("RGB", (SAMPLE * 4, SAMPLE * 4))
        image = image.convert("RGB")
        image.thumbnail((SAMPLE * 2, SAMPLE * 2), Image.Resampling.BILINEAR)
        return image.copy()


DARK_SCRIM, LIGHT_SCRIM = "#0d1118", "#f3f5f9"
LIGHT_TEXT, DARK_TEXT = "#f6f8fb", "#151a24"


def scrim(sample, photo, dark_cards):
    """The shade laid over the photo: its colour, how strong it is, and the text colour on it.

    Light text with a dark shade, or dark text with a light one, whichever needs the lighter shade (the cards'
    own look wins a near tie). The shade is at least the person's own setting, and enough that the text reads at
    4.5:1 or more on 97% of the (adjusted) photo."""
    small = _adjust(sample, photo, SCREEN)
    small.thumbnail((CHECK, CHECK))
    raw = small.convert("RGB").tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    least = photo["shade"] / 100

    def needed(text, shade):
        st = rgb(shade)
        def ok(alpha):
            fails = 0
            for p in pixels:
                c = tuple(x + (s - x) * alpha for x, s in zip(p, st))
                if contrast(text, c) < 4.5:
                    fails += 1
            return fails <= len(pixels) * 0.03
        if ok(least):
            return least
        lo, hi = least, 0.97
        for _ in range(7):
            mid = (lo + hi) / 2
            if ok(mid):
                hi = mid
            else:
                lo = mid
        return hi

    light = needed(LIGHT_TEXT, DARK_SCRIM)
    dark = needed(DARK_TEXT, LIGHT_SCRIM)
    use_light = light + (0 if dark_cards else 0.06) < dark + (0.06 if dark_cards else 0)
    alpha = light if use_light else dark
    colour = DARK_SCRIM if use_light else LIGHT_SCRIM
    ink = LIGHT_TEXT if use_light else DARK_TEXT
    # The colour behind text where the photo is worst for it, so the canvas text colours can be checked.
    st = rgb(colour)
    shaded = sorted((tuple(x + (s - x) * alpha for x, s in zip(p, st)) for p in pixels),
                    key=lambda c: contrast(ink, c))
    worst = hexa(shaded[int(len(shaded) * 0.03)])
    return {"alpha": round(alpha, 3), "colour": colour, "ink": ink, "ink_light": use_light, "worst": worst}


def bake(original, spec, result):
    """The photo as pages show it: cropped and adjusted, with the shade laid on, as a JPEG."""
    with Image.open(io.BytesIO(original)) as image:
        image = image.convert("RGB")
        image = _adjust(image, spec["photo"], SCREEN)
        if result.get("shade"):
            image = Image.blend(image, Image.new("RGB", image.size, result["scrim"]), result["shade"])
        image.thumbnail((SHOWN_SIZE, SHOWN_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue()


# --- Stored themes ------------------------------------------------------------------

def _row(tx, theme_id):
    return tx.execute("SELECT t.*, u.name AS owner_name FROM themes t LEFT JOIN users u ON u.id = t.owner_id "
                      "WHERE t.id = ?", (theme_id,)).fetchone()


def usable(row, user):
    return bool(row) and (row["owner_id"] == user["id"] or bool(row["shared"]))


def key_id(key):
    m = re.fullmatch(r"c(\d{1,12})", key or "")
    return int(m.group(1)) if m else None


def spec_of(row):
    return clean_spec(json.loads(row["json"]))


def compute(tx, theme_id, spec):
    """derive() for a stored theme, with its photo if it has one."""
    photo = tx.execute("SELECT sample FROM theme_photos WHERE theme_id = ?", (theme_id,)).fetchone()
    sample = Image.open(io.BytesIO(bytes(photo["sample"]))).convert("RGB") if photo else None
    return derive(spec, sample)


def css(selector, result, photo_url=None, spec=None):
    lines = [f"{selector}{{"]
    lines += [f" {k}:{v};" for k, v in result["vars"].items()]
    lines.append(f" color-scheme:{result['mode']}}}")
    if photo_url:
        lines.append(f'{selector}{{--ph-url:url("{photo_url}");--ph-pos:{spec["photo"]["x"]}% {spec["photo"]["y"]}%}}')
    return "\n".join(lines) + "\n"


_preset_css = {}


def preset_css(key):
    if key not in _preset_css:
        _preset_css[key] = css(f':root[data-theme="{key}"]', derive(clean_spec(PRESETS[key])))
    return _preset_css[key]


def current(user):
    """What <html> needs for this person's theme: {"key", "mode", "css" (a stylesheet address or None),
    "photo", "menu"}. Ilmi and Dark need no stylesheet."""
    key = (user or {}).get("theme") or DEFAULT
    if key in BUILT_IN:
        return {"key": key, "mode": BUILT_IN[key][1], "css": None, "photo": False,
                "menu": LIGHT["menu"] if key == "light" else DARK["menu"]}
    if key in PRESETS:
        result = derive(clean_spec(PRESETS[key]))
        version = hashlib.sha1(preset_css(key).encode()).hexdigest()[:10]
        return {"key": key, "mode": result["mode"], "css": f"/theme.css?t={key}&v={version}", "photo": False,
                "menu": result["menu"]}
    theme_id = key_id(key)
    if theme_id is not None:
        # A person's own themes are where their account is: for the developer inside a school, not in that school.
        home, owner = auth.own() if user is g.get("user") else (auth.db().current(), user)
        user = dict(user, id=owner["id"])
        with home.transaction() as tx:
            row = _row(tx, theme_id)
            if usable(row, user):
                info = json.loads(row["derived"] or "{}")
                return {"key": key, "mode": info.get("mode", "light"),
                        "css": f"/theme.css?t={key}&v={row['updated_at']}", "photo": bool(row["photo_at"]),
                        "menu": info.get("menu", LIGHT["menu"])}
    return current({"theme": DEFAULT})


@bp.app_template_global("theme_attrs")
def html_attrs(info):
    """The attributes <html> carries for a theme (tokens.css and the theme's stylesheet key off them)."""
    if info["key"] == DEFAULT:
        return ""
    out = f' data-theme="{info["key"]}" data-mode="{info["mode"]}"'
    if info["css"]:
        out += " data-logo"
    if info["photo"]:
        out += " data-photo"
    return out


@bp.app_template_global("theme_of")
def theme_of(user):
    if not user or user.get("must_change"):
        return current(None)
    return current(user)


def tile(spec, result):
    """The colours of a theme's tile in the picker (a small drawing of the menu, page and a card)."""
    v = result["vars"]
    return {"menu": v["--rail-bg"], "menu_ink": v["--rail-ink"], "on": v["--rail-on"], "bg": result["canvas"],
            "card": v["--surface"], "line": v["--line-2"], "fill": v["--navy"], "ink": v["--ink"],
            "title": v["--c-title"], "mute": v["--mute"]}


def _static_tile(key):
    # Ilmi and Dark are drawn from tokens.css itself (derive() would give a near copy, not the same colours).
    if key == "light":
        return {"menu": "#4b552f", "menu_ink": "#ece6d3", "on": "#f4efe2", "bg": "#f4efe4", "card": "#fbf8f1",
                "line": "#cfc5ae", "fill": "#4d5a2c", "ink": "#22231b", "title": "#22231b", "mute": "#67654f"}
    if key == "dark":
        return {"menu": "#14150f", "menu_ink": "#dcd8c4", "on": "#2c3120", "bg": "#181912", "card": "#1e1f17",
                "line": "#45473a", "fill": "#617038", "ink": "#ebe8db", "title": "#ebe8db", "mute": "#a3a08b"}
    spec = clean_spec(PRESETS[key])
    return tile(spec, derive(spec))


@bp.app_template_global("theme_choices")
def theme_choices(user):
    """The picker: the built-in themes, themes others have shared, and this person's own."""
    built = [{"key": k, "name": BUILT_IN[k][0] if k in BUILT_IN else PRESETS[k]["name"], "tile": _static_tile(k)}
             for k in ORDER]
    mine, shared = [], []
    with auth.db().transaction() as tx:
        rows = tx.execute("SELECT t.*, u.name AS owner_name FROM themes t LEFT JOIN users u ON u.id = t.owner_id "
                          "WHERE t.owner_id = ? OR t.shared = 1 ORDER BY t.created_at, t.id", (user["id"],)).fetchall()
    for row in rows:
        spec = spec_of(row)
        info = json.loads(row["derived"] or "{}")
        item = {"key": f"c{row['id']}", "id": row["id"], "name": row["name"], "spec": spec,
                "tile": info.get("tile") or _static_tile("light"), "shared": bool(row["shared"]),
                "owner": row["owner_name"] or "", "photo": photo_url(row) if row["photo_at"] else None,
                "editor": editor_data(row) if row["owner_id"] == user["id"] else None}
        (mine if row["owner_id"] == user["id"] else shared).append(item)
    return {"built": built, "shared": shared, "mine": mine, "max": MAX_THEMES}


def photo_url(row):
    return f"/theme-photo/{row['id']}?v={row['photo_at']}-{row['updated_at']}"


def save_derived(tx, theme_id, spec, stamp, original=None):
    """Works out a stored theme's colours (and photo) again after a change, and records them."""
    result = compute(tx, theme_id, spec)
    if original is None:
        photo = tx.execute("SELECT original FROM theme_photos WHERE theme_id = ?", (theme_id,)).fetchone()
        original = bytes(photo["original"]) if photo else None
    if original is not None:
        tx.execute("UPDATE theme_photos SET shown = ? WHERE theme_id = ?", (bake(original, spec, result), theme_id))
    info = {"mode": result["mode"], "menu": result["menu"], "tile": tile(spec, result)}
    tx.execute("UPDATE themes SET json = ?, derived = ?, updated_at = ? WHERE id = ?",
               (json.dumps(spec), json.dumps(info), stamp, theme_id))
    return result


# --- Routes -------------------------------------------------------------------------

def wants_json():
    return bool(request.headers.get("X-Dashboard-Request")) or request.accept_mimetypes.best == "application/json"


def reply(message, status=200, error=False, **data):
    if wants_json():
        return jsonify(ok=not error, message=message, **data), status
    return auth.account_page(status, **({"error": message} if error else {"notice": message}))


def own(tx, theme_id):
    row = _row(tx, theme_id)
    if not row or row["owner_id"] != g.user["id"]:
        abort(404)
    return row


@bp.get("/theme.css")
def stylesheet():
    key = request.args.get("t", "")
    if key in PRESETS:
        body = preset_css(key)
    else:
        theme_id = key_id(key)
        if theme_id is None:
            abort(404)
        with auth.db().transaction() as tx:
            row = _row(tx, theme_id)
            if not usable(row, g.user):
                abort(404)
            spec = spec_of(row)
            body = css(f':root[data-theme="{key}"]', compute(tx, theme_id, spec), photo_url(row) if row["photo_at"] else None,
                       spec)
    response = Response(body, mimetype="text/css")
    # A changed theme has a new ?v=, so this browser can keep this one.
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@bp.get("/theme-photo/<int:theme_id>")
def photo(theme_id):
    original = request.args.get("o") == "1"
    with auth.db().transaction() as tx:
        row = _row(tx, theme_id)
        if not usable(row, g.user) or (original and row["owner_id"] != g.user["id"]):
            abort(404)
        image = tx.execute(f"SELECT {'original' if original else 'shown'} AS image FROM theme_photos "
                           "WHERE theme_id = ?", (theme_id,)).fetchone()
    if not image or not image["image"]:
        abort(404)
    response = Response(bytes(image["image"]), mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@bp.get("/account/themes/preview")
def preview():
    """The colours a theme would have, while it is being changed on Account (static/theme.js shows them at once)."""
    try:
        raw = json.loads(request.args.get("spec", "{}"))
    except ValueError:
        abort(400)
    if not isinstance(raw, dict):
        abort(400)
    spec = clean_spec(raw)
    sample = None
    theme_id = raw.get("id")
    if isinstance(theme_id, int):
        with auth.db().transaction() as tx:
            own(tx, theme_id)
            photo = tx.execute("SELECT sample FROM theme_photos WHERE theme_id = ?", (theme_id,)).fetchone()
        if photo:
            sample = Image.open(io.BytesIO(bytes(photo["sample"]))).convert("RGB")
    result = derive(spec, sample)
    return jsonify(vars=result["vars"], mode=result["mode"], tile=tile(spec, result),
                   shade=result.get("shade"), scrim=result.get("scrim"))


@bp.post("/account/theme")
def choose():
    """Chooses this person's theme, on every device. The picker sends it with fetch (static/theme.js) and changes
    the page at once; without scripts the form posts and the page comes back in the new look."""
    auth.check_form_token()
    key = request.form.get("theme", "")
    if key not in BUILT_IN and key not in PRESETS:
        theme_id = key_id(key)
        with auth.db().transaction() as tx:
            if theme_id is None or not usable(_row(tx, theme_id), g.user):
                abort(400)
    with auth.db().transaction() as tx:
        tx.execute("UPDATE users SET theme = ? WHERE id = ?", (key, g.user["id"]))
    g.user["theme"] = key
    info = current(g.user)
    if wants_json():
        return jsonify(ok=True, theme=key, mode=info["mode"], css=info["css"], photo=info["photo"], menu=info["menu"])
    return auth.account_page(notice="Theme changed.")


@bp.post("/account/themes")
def create():
    """A new theme of this person's own, starting from the one they have on (or a copy of a shared one)."""
    auth.check_form_token()
    copy_of = key_id(request.form.get("from", ""))
    t = auth.now()
    with auth.db().transaction() as tx:
        count = tx.execute("SELECT COUNT(*) AS n FROM themes WHERE owner_id = ?", (g.user["id"],)).fetchone()["n"]
        if count >= MAX_THEMES:
            return reply(f"You can keep up to {MAX_THEMES} themes. Delete one to make another.", 400, error=True)
        original, name = None, "My theme"
        if copy_of is not None:
            source = _row(tx, copy_of)
            if not usable(source, g.user):
                abort(404)
            spec, name = spec_of(source), (source["name"] + " copy")[:NAME_MAX]
            photo = tx.execute("SELECT * FROM theme_photos WHERE theme_id = ?", (copy_of,)).fetchone()
            if photo:
                original, sample = bytes(photo["original"]), bytes(photo["sample"])
        else:
            key = g.user.get("theme") or DEFAULT
            start = PRESETS.get(key) or (DARK if key == "dark" else LIGHT)
            mine = key_id(key)
            if mine is not None:
                row = _row(tx, mine)
                if usable(row, g.user):
                    start = spec_of(row)
            spec = clean_spec(start)
        theme_id = tx.insert("INSERT INTO themes (owner_id, name, json, derived, shared, photo_at, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, 0, 0, ?, ?)", (g.user["id"], name, json.dumps(spec), "{}", t, t))
        if original is not None:
            tx.execute("INSERT INTO theme_photos (theme_id, original, sample, shown) VALUES (?, ?, ?, ?)",
                       (theme_id, original, sample, b""))
            tx.execute("UPDATE themes SET photo_at = ? WHERE id = ?", (t, theme_id))
        save_derived(tx, theme_id, spec, t, original)
        auth.audit(tx, "theme.created", detail=name)
    if wants_json():
        with auth.db().transaction() as tx:
            row = _row(tx, theme_id)
        return jsonify(ok=True, theme=editor_data(row))
    return redirect(f"/account?edit={theme_id}#theme", 303)


def editor_data(row):
    return {"id": row["id"], "key": f"c{row['id']}", "name": row["name"], "spec": spec_of(row),
            "shared": bool(row["shared"]), "photo": f"/theme-photo/{row['id']}?o=1&v={row['photo_at']}" if row["photo_at"] else None,
            "tile": json.loads(row["derived"] or "{}").get("tile")}


@bp.post("/account/themes/<int:theme_id>")
def save(theme_id):
    """Saves a theme's name, colours, photo settings and sharing, and puts it on."""
    auth.check_form_token()
    name = " ".join(request.form.get("name", "").split())[:NAME_MAX] or "My theme"
    t = auth.now()
    with auth.db().transaction() as tx:
        row = own(tx, theme_id)
        spec = clean_spec(request.form, spec_of(row))
        shared = 1 if request.form.get("shared") in ("1", "on", "true") else 0
        tx.execute("UPDATE themes SET name = ?, shared = ? WHERE id = ?", (name, shared, theme_id))
        save_derived(tx, theme_id, spec, t)
        if not shared and row["shared"]:
            # No longer shared: anyone else using it goes back to Ilmi.
            tx.execute("UPDATE users SET theme = ? WHERE theme = ? AND id <> ?", (DEFAULT, f"c{theme_id}", g.user["id"]))
        tx.execute("UPDATE users SET theme = ? WHERE id = ?", (f"c{theme_id}", g.user["id"]))
        auth.audit(tx, "theme.saved", detail=name + (" (shared)" if shared else ""))
    g.user["theme"] = f"c{theme_id}"
    if wants_json():
        info = current(g.user)
        with auth.db().transaction() as tx:
            row = _row(tx, theme_id)
        return jsonify(ok=True, message="Theme saved.", theme=editor_data(row), css=info["css"], mode=info["mode"],
                       photo=info["photo"], menu=info["menu"])
    return auth.account_page(notice="Theme saved.")


@bp.post("/account/themes/<int:theme_id>/photo")
def upload_photo(theme_id):
    auth.check_form_token()
    upload = request.files.get("photo")
    original = clean_photo(upload.read() if upload else b"")
    if not original:
        return reply("That file couldn't be used. Choose a JPEG, PNG or WebP photo.", 400, error=True)
    sample = io.BytesIO()
    photo_sample(original).save(sample, "PNG")
    t = auth.now()
    with auth.db().transaction() as tx:
        row = own(tx, theme_id)
        tx.execute("DELETE FROM theme_photos WHERE theme_id = ?", (theme_id,))
        tx.execute("INSERT INTO theme_photos (theme_id, original, sample, shown) VALUES (?, ?, ?, ?)",
                   (theme_id, original, sample.getvalue(), b""))
        spec = spec_of(row)
        # A new photo starts uncropped and unadjusted.
        spec["photo"] = clean_spec({})["photo"]
        tx.execute("UPDATE themes SET photo_at = ? WHERE id = ?", (t, theme_id))
        save_derived(tx, theme_id, spec, t, original)
        row = _row(tx, theme_id)
    if wants_json():
        return jsonify(ok=True, theme=editor_data(row))
    return redirect(f"/account?edit={theme_id}#theme", 303)


@bp.post("/account/themes/<int:theme_id>/photo/remove")
def remove_photo(theme_id):
    auth.check_form_token()
    t = auth.now()
    with auth.db().transaction() as tx:
        row = own(tx, theme_id)
        tx.execute("DELETE FROM theme_photos WHERE theme_id = ?", (theme_id,))
        tx.execute("UPDATE themes SET photo_at = 0 WHERE id = ?", (theme_id,))
        save_derived(tx, theme_id, spec_of(row), t)
        row = _row(tx, theme_id)
    if wants_json():
        return jsonify(ok=True, theme=editor_data(row))
    return redirect(f"/account?edit={theme_id}#theme", 303)


@bp.post("/account/themes/<int:theme_id>/delete")
def delete(theme_id):
    """Deletes a theme: its maker can, and an admin can delete a shared one. Its users go back to Ilmi."""
    auth.check_form_token()
    with auth.db().transaction() as tx:
        row = _row(tx, theme_id)
        if not row or not (row["owner_id"] == g.user["id"] or (row["shared"] and g.user["role"] == "admin")):
            abort(404)
        tx.execute("UPDATE users SET theme = ? WHERE theme = ?", (DEFAULT, f"c{theme_id}"))
        tx.execute("DELETE FROM theme_photos WHERE theme_id = ?", (theme_id,))
        tx.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
        auth.audit(tx, "theme.deleted", detail=row["name"])
    if g.user.get("theme") == f"c{theme_id}":
        g.user["theme"] = DEFAULT
    if wants_json():
        info = current(g.user)
        return jsonify(ok=True, message="Theme deleted.", theme=g.user["theme"], css=info["css"], mode=info["mode"],
                       photo=info["photo"], menu=info["menu"])
    return auth.account_page(notice="Theme deleted.")


@bp.app_template_global("theme_editing")
def theme_editing(user):
    """The theme named by ?edit= on Account, when it is this person's own (the editor without scripts)."""
    try:
        theme_id = int(request.args.get("edit", ""))
    except ValueError:
        return None
    with auth.db().transaction() as tx:
        row = _row(tx, theme_id)
    return editor_data(row) if row and row["owner_id"] == user["id"] else None
