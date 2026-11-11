"""The Ilmi mark from the ʿayn rendered by tools/art/mark.js: a cream letter on an olive disc for static/favicon.png
(192px) and the letter knocked out of the disc for the logo (160px; base64 it into src/logo.b64, which the server
serves at /logo.png). Needs numpy and Pillow. Run: python3 tools/art/mark.py ayn.png static/favicon.png logo.png"""
import sys
import numpy as np
from PIL import Image, ImageFilter, ImageChops
src, fav, logo = sys.argv[1:4]
L = Image.open(src).convert("L")
ink = 255 - np.asarray(L, np.float32)          # 255 where the letter is
ys, xs = np.nonzero(ink > 128)
cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
H = 768
# Centre the letter optically (a touch above centre) in a 768 canvas.
letter = Image.fromarray(ink.astype(np.uint8)).transform((H, H), Image.AFFINE, (1, 0, cx - H / 2, 0, 1, cy - H / 2 + 14), Image.BICUBIC)
yy, xx = np.mgrid[0:H, 0:H]
r = H / 2 - 4
disc = np.clip(r - np.hypot(xx - H / 2 + .5, yy - H / 2 + .5), 0, 1.5) / 1.5
olive = np.array((77, 90, 44), np.float32); cream = np.array((246, 241, 228), np.float32)
lt = np.asarray(letter, np.float32) / 255
# A faint watercolour edge: the disc a little darker towards its rim.
rim = np.clip((np.hypot(xx - H / 2, yy - H / 2) / r) ** 6, 0, 1)[..., None]
base = olive * (1 - .18 * rim)
rgb = base * (1 - lt[..., None]) + cream * lt[..., None]
fav_img = Image.fromarray(np.dstack([rgb, disc * 255]).astype(np.uint8), "RGBA").resize((192, 192), Image.LANCZOS)
fav_img.save(fav, optimize=True)
alpha = disc * (1 - lt)
logo_img = Image.fromarray(np.dstack([np.broadcast_to(base, (H, H, 3)), alpha * 255]).astype(np.uint8), "RGBA").resize((160, 160), Image.LANCZOS)
logo_img.save(logo, optimize=True)
print("ok")
