"""A seamless paper tile: soft mottling and a few fibres, as a dark speckle at very low opacity, so it can lie over
any cream. FFT noise is periodic, so the tile repeats without a seam.

Run: python3 tools/art/paper.py [static/art/paper.webp] (needs numpy and Pillow)."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

N = 320
rng = np.random.default_rng(5)


def periodic(scale):
    w = rng.standard_normal((N, N))
    fx = np.fft.fftfreq(N)[:, None]; fy = np.fft.fftfreq(N)[None, :]
    g = np.exp(-(fx ** 2 + fy ** 2) * (scale ** 2) * 20)
    n = np.real(np.fft.ifft2(np.fft.fft2(w) * g))
    return (n - n.min()) / (n.max() - n.min())


mottle = .55 * periodic(40) + .3 * periodic(12) + .15 * periodic(3)
grain = rng.random((N, N))
alpha = np.clip((mottle - .5) * 10, 0, 3.5) + (grain > .992) * 7 + grain * 2.2
img = Image.new("L", (N, N), 0)
d = ImageDraw.Draw(img)
for _ in range(70):  # fibres
    x, y = rng.uniform(0, N, 2)
    a = rng.uniform(0, np.pi)
    ln = rng.uniform(6, 22)
    pts = [(x + np.cos(a) * t + rng.normal(0, .6), y + np.sin(a) * t + rng.normal(0, .6)) for t in np.linspace(0, ln, 6)]
    for ox in (-N, 0, N):
        for oy in (-N, 0, N):
            d.line([(px + ox, py + oy) for px, py in pts], fill=int(rng.uniform(5, 9)), width=1)
alpha = np.clip(alpha + np.asarray(img, np.float32), 0, 255).astype(np.uint8)
rgb = np.zeros((N, N, 3), np.uint8); rgb[...] = (70, 62, 36)
Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "static" / "art" / "paper.webp", "WEBP", quality=80, method=6)
print("alpha mean", alpha.mean(), "max", alpha.max())
