"""Profile pictures.

Everyone starts with their first name's initial on a pastel blue disc. On the Account page they can upload a photo,
move and zoom it in a round frame (static/avatar.js crops it in the browser), and it then shows by their name at the
bottom of the menu and on Manage users.

Uploads are never stored as sent: the image is opened with Pillow, turned upright, cropped square, resized to
AVATAR_SIZE and saved again as a plain JPEG, which drops any metadata (such as a phone's location) and anything that
isn't a picture. Pictures are only served to signed-in staff.
"""
import io

from flask import Blueprint, Response, abort, g, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError

import auth

AVATAR_SIZE = 256
# The browser sends a cropped 512px picture, far below this; a photo sent without the cropper is resized here.
MAX_BYTES = 2 * 1024 * 1024
# A picture is decoded whole before it is resized, at up to 4 bytes a pixel and a copy or two on the way; this keeps
# one upload to a few hundred MB at most. A phone photo is 12 to 50 million pixels, but a JPEG is decoded at a smaller
# size (draft below), so only very large PNG, WebP and GIF files are refused.
MAX_PIXELS = 16_000_000
FORMATS = ("JPEG", "PNG", "WEBP", "GIF")

bp = Blueprint("avatars", __name__)


@bp.app_template_global("initial")
def initial(name):
    """The capital first letter of a first name, for the default picture."""
    for ch in (name or "").strip():
        if ch.isalnum():
            return ch.upper()
    return "?"


@bp.app_template_global("avatar_url")
def url(user_id, version):
    """Where a person's picture is, or None for the letter. The version changes with the picture, so it can be cached."""
    return f"/avatar/{user_id}?v={version}" if version else None


def clean(data):
    """A square JPEG of AVATAR_SIZE from an uploaded picture, or None if it isn't a picture we accept."""
    if not data or len(data) > MAX_BYTES:
        return None
    try:
        # Only these formats' decoders ever see the file.
        with Image.open(io.BytesIO(data), formats=FORMATS) as image:
            if image.format == "JPEG":
                # Decodes at 1/2, 1/4 or 1/8 size straight away, still well over AVATAR_SIZE.
                image.draft("RGB", (AVATAR_SIZE * 2, AVATAR_SIZE * 2))
            if image.format not in FORMATS or image.width * image.height > MAX_PIXELS:
                return None
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            # Transparent parts become white, not black.
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, "#ffffff")
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            image = ImageOps.fit(image.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, "JPEG", quality=88, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def reply(ok, message, status=200, version=None):
    """JSON for the cropper; a plain form post (no scripts) gets the Account page back."""
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=ok, message=message, url=url(g.user["id"], version)), status
    return auth.account_page(status, **({"notice": message} if ok else {"error": message}))


@bp.post("/account/avatar")
def upload():
    auth.check_form_token()
    photo = request.files.get("photo")
    image = clean(photo.read(MAX_BYTES + 1) if photo else b"")
    if not image:
        return reply(False, "That file couldn't be used. Choose a JPEG, PNG, WebP or GIF picture under 2 MB.", 400)
    t = auth.now()
    with auth.db().transaction() as tx:
        if tx.execute("SELECT 1 FROM avatars WHERE user_id = ?", (g.user["id"],)).fetchone():
            tx.execute("UPDATE avatars SET image = ?, updated_at = ? WHERE user_id = ?", (image, t, g.user["id"]))
        else:
            tx.execute("INSERT INTO avatars (user_id, image, updated_at) VALUES (?, ?, ?)", (g.user["id"], image, t))
        tx.execute("UPDATE users SET avatar_at = ? WHERE id = ?", (t, g.user["id"]))
        auth.audit(tx, "avatar.changed")
    g.user["avatar_at"] = t
    return reply(True, "Profile picture saved.", version=t)


@bp.post("/account/avatar/remove")
def remove():
    auth.check_form_token()
    with auth.db().transaction() as tx:
        tx.execute("DELETE FROM avatars WHERE user_id = ?", (g.user["id"],))
        tx.execute("UPDATE users SET avatar_at = NULL WHERE id = ?", (g.user["id"],))
        auth.audit(tx, "avatar.removed")
    g.user["avatar_at"] = None
    return reply(True, "Profile picture removed. Your initial shows instead.")


@bp.get("/avatar/<int:user_id>")
def picture(user_id):
    with auth.db().transaction() as tx:
        row = tx.execute("SELECT image FROM avatars WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        abort(404)
    response = Response(bytes(row["image"]), mimetype="image/jpeg")
    # Cached by this browser only; a new picture has a new ?v= address (see server.py private_responses).
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
