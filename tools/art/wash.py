"""Watercolour washes for Ilmi, painted procedurally: soft blobs with ragged, slightly darker edges, inner blooms,
pigment that varies across the wash and fine paper granulation. Writes RGBA WebP files into static/art.

Needs numpy and Pillow (not the app's own requirements). Run from anywhere, naming the washes to paint:
    python3 tools/art/wash.py page cloud
("splats" paints the three splashes of paint, splat-a/b/c.webp.)
Each wash has a fixed seed, so painting it again gives the same picture."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = str(Path(__file__).resolve().parents[2] / "static" / "art") + "/"


def noise(h, w, cell, seed, octaves=4, persist=.5):
    """Fractal value noise in 0..1: random grids upsampled smoothly, finer and fainter each octave."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        c = max(cell / (2 ** o), 2)
        gh, gw = int(h / c) + 3, int(w / c) + 3
        g = (rng.random((gh, gw)) * 255).astype(np.uint8)
        img = Image.fromarray(g).resize((int(gw * c), int(gh * c)), Image.BICUBIC).crop((0, 0, w, h))
        out += amp * np.asarray(img, np.float32) / 255
        tot += amp
        amp *= persist
    return out / tot


def blur(a, r):
    img = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def field(h, w, blobs):
    """Sum of soft elliptical blobs: (cx, cy, rx, ry, weight) in 0..1 of the canvas."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    f = np.zeros((h, w), np.float32)
    for cx, cy, rx, ry, wt in blobs:
        f += wt * np.exp(-(((x / w - cx) / rx) ** 2 + ((y / h - cy) / ry) ** 2))
    return f


def wash(h, w, blobs, seed, colours, opacity=.85, rag=.38, layers=(.42, .6, .78), edge=.55, grain=.35, cell=None):
    """Glazes of paint laid one over another. Each glaze has its own ragged outline (its own noise), an edge that is
    soft in some places and crisp in others, a darker rim where the pigment dried, and uneven pigment inside."""
    cell = cell or max(h, w) / 6
    return tint(glazes(h, w, blobs, seed, rag, layers, edge, grain, cell), seed, colours, opacity, cell)


def glazes(h, w, blobs, seed, rag, layers, edge, grain, cell, wet=.09):
    """How much pigment lies at each point (0..1) after the glazes of wash(). wet is how soft the softest edges run."""
    f = field(h, w, blobs)
    f = f / f.max()
    fine = noise(h, w, 5, seed + 2, 2)
    soft = noise(h, w, cell * .8, seed + 4, 3)          # where edges run soft (wet) or crisp (dry)
    total = np.zeros((h, w), np.float32)
    for i, t in enumerate(layers):
        n1 = noise(h, w, cell, seed + 10 * i, 5)
        n2 = noise(h, w, cell / 3, seed + 10 * i + 1, 4)
        d = f + rag * (n1 - .5) + .14 * (n2 - .5)
        width = .012 + wet * soft ** 2
        m = smooth(t - width, t + width, d)
        rim = np.clip(m - blur(m, 4 + 2 * i), 0, 1) * smooth(.35, .6, n2)
        dens = m * (.35 + .65 * smooth(.2, .8, n2)) + edge * rim
        total = 1 - (1 - total) * (1 - np.clip(dens, 0, 1) * (.75 if i == 0 else .55))
    total *= (1 - grain / 2) + grain * fine
    return total


def tint(total, seed, colours, opacity, cell):
    """The pigment in two colours that drift into each other across the paper, darker where it is thickest."""
    h, w = total.shape
    alpha = np.clip(total * opacity, 0, 1)
    c1, c2 = (np.array(c, np.float32) for c in colours)
    mix = smooth(.3, .7, noise(h, w, cell * 1.4, seed + 3, 3))[..., None]
    rgb = c1 * (1 - mix) + c2 * mix
    # Darker pigment where the paint is thickest.
    rgb = rgb * (1 - .18 * alpha[..., None])
    rgba = np.dstack([rgb, alpha * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def scatter(seed, n, box, rx=(.08, .2), ry=(.08, .2), wt=(.5, 1)):
    """n random soft blobs inside box (x0, y0, x1, y1), for clouds that are never regular."""
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = box
    return [(rng.uniform(x0, x1), rng.uniform(y0, y1), rng.uniform(*rx), rng.uniform(*ry), rng.uniform(*wt)) for _ in range(n)]


def splat(size, seed, colours, opacity=.8, core=.15, fingers=6, drops=130, reach=(1.25, 2.1), toward=None, spread=1.1):
    """A splash of watercolour flicked onto the paper: a pool laid down in glazes as wash() paints them, with a
    darker rim where it dried; short fingers of paint thrown out from it, each ending in a bead; and round droplets
    sprayed further off, fewer and smaller the further they flew. toward (radians) throws most of the paint one way,
    within spread either side. Square, centred on the pool, transparent around it."""
    rng = np.random.default_rng(seed)
    cell = size / 7
    # The pool: a few soft blobs around the centre, glazed with ragged, partly crisp edges.
    blobs = [(.5 + rng.normal(0, core * .42), .5 + rng.normal(0, core * .42), core * rng.uniform(.3, .8),
              core * rng.uniform(.3, .8), rng.uniform(.55, 1)) for _ in range(8)]
    pool = glazes(size, size, blobs, seed, .34, (.34, .5, .66), .75, .3, cell, wet=.035)
    # Fingers and droplets, drawn three times larger and then reduced, so the smallest stay round.
    k = 3
    big = size * k
    c, R = big / 2, core * big
    img = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(img)

    def ellipse(x, y, rx, ry, ang=0.0, n=36):
        ca, sa = np.cos(ang), np.sin(ang)
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        px, py = rx * np.cos(t), ry * np.sin(t)
        d.polygon(list(zip(x + px * ca - py * sa, y + px * sa + py * ca)), fill=255)

    def heading():
        return rng.uniform(0, 2 * np.pi) if toward is None else toward + rng.normal(0, spread / 2)

    for _ in range(fingers):
        a = heading()
        length = R * rng.uniform(*reach)
        w0 = R * rng.uniform(.07, .13)
        ca, sa = np.cos(a), np.sin(a)
        p0 = np.array((c + R * .4 * ca, c + R * .4 * sa))
        p2 = np.array((c + length * ca, c + length * sa))
        p1 = (p0 + p2) / 2 + np.array((-sa, ca)) * rng.normal(0, .05) * length
        steps = max(8, int(length / 3))
        for j in range(steps + 1):
            t = j / steps
            q = (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t * t * p2
            r = w0 * (1 - t) ** 1.6 + w0 * .1
            ellipse(q[0], q[1], r, r, n=18)
        tan = (p2 - p1) / np.hypot(*(p2 - p1))
        bead = w0 * rng.uniform(.38, .62)
        ellipse(p2[0] + tan[0] * bead * .5, p2[1] + tan[1] * bead * .5, bead * 1.2, bead, np.arctan2(tan[1], tan[0]))
        dist, r = np.hypot(*(p2 - c)) + bead * 3, bead * .6
        for _ in range(int(rng.integers(0, 3))):
            if dist + r > big * .47 or r < 2.5:
                break
            ellipse(c + dist * ca, c + dist * sa, r * 1.15, r, a)
            dist += r * rng.uniform(2.8, 4.6)
            r *= rng.uniform(.5, .72)
    for _ in range(drops):
        a = heading() if rng.random() < .65 else rng.uniform(0, 2 * np.pi)
        dist = R * (1.05 + min(rng.exponential(.8), 2.2))
        r = R * min(.014 * (1 + rng.pareto(2.4)), .1) * (1.3 - .3 * dist / (3.3 * R))
        if dist + r > big * .47:
            continue
        ellipse(c + dist * np.cos(a), c + dist * np.sin(a), r * rng.uniform(1, 1.3), r, a)
    mask = np.asarray(img.resize((size, size), Image.LANCZOS), np.float32) / 255
    edge = noise(size, size, 8, seed + 11, 3)
    shape = smooth(.4, .6, blur(mask, 1) + .14 * (edge - .5))
    rim = np.clip(shape - blur(shape, 2.5), 0, 1)
    mid = noise(size, size, cell / 2, seed + 12, 4)
    spray = np.clip(shape * (.58 + .3 * (mid - .5)) + .45 * rim, 0, 1)
    spray *= .84 + .32 * noise(size, size, 5, seed + 13, 2)
    # Where the pool already lies, the fingers run into it rather than over it.
    spray *= 1 - .9 * smooth(.05, .35, pool)
    return tint(1 - (1 - pool) * (1 - spray), seed, colours, opacity, cell)


def save(img, name, q=82):
    img.save(OUT + name, "WEBP", quality=q, method=6)
    print(name, img.size)


GREY_GREEN = ((92, 98, 82), (122, 126, 110))
OLIVE_DEEP = ((52, 60, 34), (70, 78, 46))
MIST = ((226, 224, 208), (200, 202, 182))
SAGE = ((150, 160, 118), (176, 180, 150))
PAGE = ((138, 146, 116), (168, 172, 150))
SPLAT_OLIVE = ((92, 108, 52), (138, 148, 88))
SPLAT_SAGE = ((128, 140, 94), (160, 166, 122))

if __name__ == "__main__":
    which = sys.argv[1:] or ["cloud", "band", "soft", "panel", "mist", "rail", "page", "low", "splats"]
    if "cloud" in which:   # the grey-green cloud in a top corner of the start page
        save(wash(700, 1100, scatter(1, 10, (.28, .24, .8, .6), (.06, .15), (.08, .15)), 11, GREY_GREEN, .7, rag=.5,
                  layers=(.3, .48, .66)), "wash-cloud.webp")
    if "band" in which:    # darker olive clouds over the olive band
        save(wash(560, 1300, scatter(2, 10, (.6, .05, 1.0, .95), (.06, .16), (.12, .3)), 21, OLIVE_DEEP, .85, rag=.5,
                  layers=(.28, .45, .62)), "wash-band.webp")
    if "soft" in which:    # a pale sage wash behind a page's greeting
        save(wash(560, 900, scatter(3, 9, (.28, .3, .72, .66), (.06, .14), (.08, .15)), 31, SAGE, .6, rag=.5,
                  layers=(.32, .52)), "wash-soft.webp")
    if "panel" in which:   # the sign-in welcome panel: olive washes over olive
        save(wash(1200, 900, scatter(4, 12, (0, 0, 1, 1), (.08, .22), (.06, .16)), 41, OLIVE_DEEP, .8, rag=.55,
                  layers=(.3, .48, .66)), "wash-panel.webp")
    if "mist" in which:    # pale mist drifting in at the top of the olive band
        save(wash(420, 900, scatter(6, 9, (.3, .3, .86, .66), (.05, .12), (.08, .16)), 61, MIST, .75, rag=.55,
                  layers=(.3, .5, .68)), "wash-mist.webp")
    if "rail" in which:    # the foot of the side menu: darker olive washes rising from the bottom
        save(wash(900, 500, scatter(7, 11, (-.05, .5, 1.05, 1.08), (.12, .32), (.06, .14)), 71, OLIVE_DEEP, .85, rag=.5,
                  layers=(.28, .46, .64)), "wash-rail.webp")
    if "page" in which:    # the dashboard's sky: pale sage-grey clouds drifting in the top right corner, kept light
        save(wash(640, 1200, scatter(14, 13, (.22, .3, .82, .6), (.06, .15), (.06, .11)), 94, PAGE, .5, rag=.5,
                  layers=(.3, .48, .64), grain=.3), "wash-page.webp")
    if "low" in which:     # a low, wide wash along the foot of the start page
        save(wash(360, 1600, scatter(5, 10, (0, .7, 1, 1), (.06, .16), (.15, .35)), 51, GREY_GREEN, .5, rag=.45,
                  layers=(.32, .5)), "wash-low.webp")
    if "splats" in which:  # splashes of paint for the opening titles, the start page's hero and its values
        save(splat(640, 22, SPLAT_OLIVE, .85, core=.14), "splat-a.webp")
        save(splat(560, 26, SPLAT_SAGE, .8, core=.14), "splat-b.webp")
        save(splat(480, 8, SPLAT_OLIVE, .82, core=.13, fingers=5, drops=170, toward=-.5, spread=1.0), "splat-c.webp")
