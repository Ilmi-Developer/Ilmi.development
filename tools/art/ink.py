"""Ink drawings for Ilmi, written as SVG partials into templates/art/.

Every line is a stroke in currentColor, so CSS sets the ink colour (dark olive on paper, cream on the olive band), and
has pathLength="1", so CSS can draw it on with stroke-dashoffset. Watercolour tints sit in <g class="tint"> under
the lines, a little off register, as a loose wash would be. Groups carry classes (k1, k2, ...) for staged drawing.

Run: python3 tools/art/ink.py (writes templates/art/*.svg; tools/build.py inlines <!--ART:name--> from there).
"""
import math
import random
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "templates" / "art"
R = random.Random(11)


def f(x):
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def pt(p):
    return f"{f(p[0])} {f(p[1])}"


def path(d, w=1.4, extra=""):
    return f'<path d="{d}" stroke-width="{f(w)}" pathLength="1"{extra}/>'


def fill(d, opacity=None, cls=None):
    o = f' fill-opacity="{opacity}"' if opacity is not None else ""
    c = f' class="{cls}"' if cls else ""
    return f'<path d="{d}"{o}{c}/>'


def cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def cubic_tan(p0, p1, p2, p3, t):
    u = 1 - t
    dx = 3 * u * u * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0]) + 3 * t * t * (p3[0] - p2[0])
    dy = 3 * u * u * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1]) + 3 * t * t * (p3[1] - p2[1])
    return math.atan2(dy, dx)


def leaf(b, ang, length, width, bend=.18):
    """An almond leaf from base b, pointing at angle ang: outline, midrib and a loose wash."""
    ca, sa = math.cos(ang), math.sin(ang)
    nx, ny = -sa, ca
    t = (b[0] + length * ca + nx * length * bend * .4, b[1] + length * sa + ny * length * bend * .4)
    m = (b[0] + length * .5 * ca + nx * length * bend * .25, b[1] + length * .5 * sa + ny * length * bend * .25)
    c1 = (m[0] + nx * width, m[1] + ny * width)
    c2 = (m[0] - nx * width * .8, m[1] - ny * width * .8)
    outline = f"M{pt(b)}Q{pt(c1)} {pt(t)}Q{pt(c2)} {pt(b)}"
    r = (b[0] + (t[0] - b[0]) * .82, b[1] + (t[1] - b[1]) * .82)
    rib = f"M{pt(b)}Q{pt((m[0] + nx * width * .12, m[1] + ny * width * .12))} {pt(r)}"
    # The wash: a touch bigger, nudged down and right, its edge not quite following the line.
    j = lambda p, k=1.6: (p[0] + 1.2 + R.uniform(-k, k), p[1] + 1.4 + R.uniform(-k, k))
    wash = f"M{pt(j(b, .6))}Q{pt(j((c1[0] + nx * 1.5, c1[1] + ny * 1.5)))} {pt(j(t, .8))}Q{pt(j((c2[0] - nx * 1.5, c2[1] - ny * 1.5)))} {pt(j(b, .6))}Z"
    return outline, rib, wash


def svg(name, vb, body, cls=""):
    out = (f'<svg class="art art-{name}{" " + cls if cls else ""}" viewBox="{vb}" aria-hidden="true" focusable="false" fill="none" '
           f'stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">{body}</svg>\n')
    (OUT / f"{name}.svg").write_text(out)
    return out


# ---------------------------------------------------------------------------------------------------------------
def sprig():
    """A young olive-like sprig: one stem, two side shoots, almond leaves washed in sage."""
    stem = ((44, 236), (52, 186), (62, 150), (78, 116))
    stem2 = ((78, 116), (94, 82), (106, 48), (122, 14))
    shoots = [((64, 146), (52, 128), (38, 112), (22, 100)), ((92, 84), (104, 74), (120, 66), (140, 60)),
              ((54, 186), (66, 174), (80, 166), (96, 162))]
    ink1, ink2, tint = [], [], []
    ink1.append(path(f"M{pt(stem[0])}C{pt(stem[1])} {pt(stem[2])} {pt(stem[3])}C{pt(stem2[1])} {pt(stem2[2])} {pt(stem2[3])}", 1.25))
    # The stem thickens towards its foot: a second, shorter line beside the first.
    ink1.append(path("M45.6 236C53 190 62 156 74 124", .8))
    for s in shoots:
        ink1.append(path(f"M{pt(s[0])}C{pt(s[1])} {pt(s[2])} {pt(s[3])}", .95))

    def leaves_on(curve, ts, size, side0=1, tip=True):
        side = side0
        for i, t in enumerate(ts):
            p = cubic(*curve, t)
            a = cubic_tan(*curve, t)
            k = size * (1 - .35 * t)
            o, rb, w = leaf(p, a + side * math.radians(R.uniform(38, 52)), k * R.uniform(.9, 1.1), k * .32, bend=.2 * side)
            ink2.append(path(o, .85)); ink2.append(path(rb, .55))
            tint.append(fill(w, round(R.uniform(.34, .52), 2)))
            side = -side
        if tip:
            p = curve[3]; a = cubic_tan(*curve, 1)
            o, rb, w = leaf(p, a, size * .62, size * .2, bend=.1)
            ink2.append(path(o, .85)); ink2.append(path(rb, .55)); tint.append(fill(w, .44))

    leaves_on(stem2, [.06, .2, .33, .46, .58, .7, .82], 28)
    leaves_on(stem, [.42, .62, .82], 28, -1, tip=False)
    leaves_on(shoots[0], [.25, .5, .75], 22, 1)
    leaves_on(shoots[1], [.3, .55, .8], 20, -1)
    leaves_on(shoots[2], [.4, .75], 18, 1)
    # A few buds
    for (x, y) in ((126, 9), (20, 96), (144, 57)):
        ink2.append(path(f"M{f(x - 2)} {f(y)}a2 2 0 1 0 4 0a2 2 0 1 0-4 0", .7))
    body = f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(tint)}</g><g class="ink k1">{"".join(ink1)}</g><g class="ink k2">{"".join(ink2)}</g>'
    return svg("sprig", "0 0 160 240", body)


# ---------------------------------------------------------------------------------------------------------------
def tuft(x, y, h, n, lean=0, spread=14, w=1.1, seeds=0):
    """Blades of grass from (x, y); some with seed heads."""
    out = []
    for i in range(n):
        bx = x + R.uniform(-spread / 2, spread / 2)
        hh = h * R.uniform(.55, 1.05)
        l = lean + R.uniform(-.55, .55)
        tx, ty = bx + l * hh * .6, y - hh
        cx, cy = bx + l * hh * .1, y - hh * .55
        out.append(path(f"M{f(bx)} {f(y)}Q{f(cx)} {f(cy)} {f(tx)} {f(ty)}", w * R.uniform(.75, 1.1)))
    for i in range(seeds):
        bx = x + R.uniform(-spread / 2, spread / 2)
        hh = h * R.uniform(1.05, 1.45)
        l = lean + R.uniform(-.3, .3)
        tx, ty = bx + l * hh * .5, y - hh
        out.append(path(f"M{f(bx)} {f(y)}Q{f(bx + l * hh * .08)} {f(y - hh * .5)} {f(tx)} {f(ty)}", w * .8))
        for k in range(6):
            t = .7 + k * .055
            sx, sy = bx + (tx - bx) * t, y - hh * t
            side = 1 if k % 2 else -1
            out.append(path(f"M{f(sx)} {f(sy)}q{f(side * 3.4)} {f(-1)} {f(side * 2.6)} {f(-4.2)}q{f(-side * 1.6)} {f(1.2)} {f(-side * 2.6)} {f(4.2)}", .6))
    return out


def flower(x, y, h, lean=0):
    tx, ty = x + lean * h, y - h
    out = [path(f"M{f(x)} {f(y)}Q{f(x + lean * h * .2)} {f(y - h * .5)} {f(tx)} {f(ty)}", .9)]
    for a in range(5):
        ang = a * math.tau / 5 - math.pi / 2
        px, py = tx + 3.2 * math.cos(ang), ty + 3.2 * math.sin(ang)
        out.append(path(f"M{f(px - 1.5)} {f(py)}a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0", .7))
    return out


def rahle():
    """An open Qur'an on its folding stand, seen from the front and a little above, in grass."""
    ink1, ink2, ink3, tint = [], [], [], []
    # The stand: two boards crossing. The back board (from upper left to lower right) breaks where the front one
    # passes over it.
    J = (130, 150)
    def board(a, b, th):
        ang = math.atan2(b[1] - a[1], b[0] - a[0]); nx, ny = -math.sin(ang) * th / 2, math.cos(ang) * th / 2
        return (a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny)
    # Front board: upper right arm to the lower left foot.
    fa, fb, fc, fd = board((204, 114), (70, 204), 11)
    ink1.append(path(f"M{pt(fa)}L{pt(fb)}", 1.5))
    ink1.append(path(f"M{pt(fc)}L{pt(fd)}", 1.5))
    ink1.append(path(f"M{pt(fb)}L{pt(fd)}", 1.3))
    # Back board: upper left arm to the lower right foot, interrupted at the crossing.
    ba, bb, bc, bd = board((56, 114), (190, 204), 11)
    def seg(p, q, t0, t1):
        return (p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0), (p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1)
    for p, q in ((ba, bb), (bc, bd)):
        s1 = seg(p, q, 0, .46); s2 = seg(p, q, .6, 1)
        ink1.append(path(f"M{pt(s1[0])}L{pt(s1[1])}", 1.5)); ink1.append(path(f"M{pt(s2[0])}L{pt(s2[1])}", 1.5))
    ink1.append(path(f"M{pt(bb)}L{pt(bd)}", 1.3))
    # The hinge pin, and a carved notch on each foot.
    ink2.append(path("M127.6 150a2.4 2.4 0 1 0 4.8 0a2.4 2.4 0 1 0-4.8 0", 1))
    ink2.append(path("M58 206H82M178 206H202", 1.2))
    # Wood grain: a few hatch strokes along each lower leg.
    for t in (.62, .7, .78, .86):
        p = (fa[0] + (fb[0] - fa[0]) * t, fa[1] + (fb[1] - fa[1]) * t); q = (fc[0] + (fd[0] - fc[0]) * t, fc[1] + (fd[1] - fc[1]) * t)
        ink3.append(path(f"M{pt(p)}L{f(q[0] + 1.5)} {f(q[1] - 2.5)}", .6))
    for t in (.7, .8, .9):
        p = (ba[0] + (bb[0] - ba[0]) * t, ba[1] + (bb[1] - ba[1]) * t); q = (bc[0] + (bd[0] - bc[0]) * t, bc[1] + (bd[1] - bc[1]) * t)
        ink3.append(path(f"M{pt(p)}L{f(q[0] - 1.5)} {f(q[1] - 2.5)}", .6))

    # The book: a cover a little larger than the pages, two page blocks rising from the spine.
    ink1.append(path("M130 132Q96 106 44 112L42 118Q94 112 130 138Q166 112 218 118L216 112Q164 106 130 132", 1.4))
    # Pages: left and right leaves, curling up from the spine.
    L = "M130 128C114 100 86 86 50 92L48 106Q92 100 130 130"
    Rr = "M130 128C146 100 174 86 210 92L212 106Q168 100 130 130"
    ink1.append(path(L, 1.5)); ink1.append(path(Rr, 1.5))
    # Page edges: thin lines along the outer edge of each block.
    ink3.append(path("M49 97Q90 93 128 124M48.5 101.5Q91 97 129 127", .5))
    ink3.append(path("M211 97Q170 93 132 124M211.5 101.5Q169 97 131 127", .5))
    # Lines of text on each page, following the curl.
    # Lines of text run across each page, following its curl: short strokes, as handwriting seen from afar.
    for i in range(5):
        y0 = 103.5 - i * 0
        for side in (-1, 1):
            # a line from near the outer edge towards the spine, at a height that rises with the curl
            x_out, x_in = 130 + side * (70 - i * 3), 130 + side * (16 + i * 1.5)
            y_out, y_in = 100.5 - i * 1.6, 114 - i * 4.2
            xm, ym = (x_out + x_in) / 2, (y_out + y_in) / 2 - 5 - i * .5
            ink2.append(path(f"M{f(x_out - side * R.uniform(0, 4))} {f(y_out)}Q{f(xm)} {f(ym)} {f(x_in)} {f(y_in)}", .6))
    # A ribbon marker falling from the spine.
    ink2.append(path("M131 132q2 12-3 22q5-2 7 2", 1))
    tint.append(fill("M132 133q2 12-3 22q5-2 7 2q3-12-2-24z", .55, "t2"))
    # Washes: a pale wash under the pages, and a soft ground shadow.
    tint.append(fill("M52 95C88 86 112 98 131 127C150 97 176 86 209 94L212 108Q168 102 131 133Q94 102 50 108Z", .22))
    tint.append(fill("M40 206C70 196 110 200 140 199S206 196 228 205C200 213 150 212 120 212S56 214 40 206Z", .3))

    # Ground and grass.
    gnd = [path("M18 207C60 203 98 206 136 204S206 202 244 206", 1.1), path("M60 213C88 211 120 213 150 212", .7)]
    grass = tuft(36, 207, 30, 7, -.25, 20, seeds=1) + tuft(222, 206, 26, 6, .3, 18, seeds=1) + tuft(150, 205, 12, 4, .1, 10)
    grass += flower(28, 207, 26, -.15) + flower(236, 206, 20, .2)
    # A small stone.
    gnd.append(path("M178 206c2-7 12-9 18-5 3 2 4 4 4 5", 1.1))
    body = (f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(tint)}</g><g class="ink k1">{"".join(ink1)}</g>'
            f'<g class="ink k2">{"".join(ink2)}</g><g class="ink k3">{"".join(ink3)}</g><g class="ink k4">{"".join(gnd + grass)}</g>')
    return svg("rahle", "0 0 260 220", body)


# ---------------------------------------------------------------------------------------------------------------
def grass():
    out = tuft(40, 150, 70, 12, -.3, 40, 1.15, seeds=3) + tuft(120, 152, 50, 8, .2, 30, 1.05, seeds=2)
    out += tuft(200, 151, 36, 6, .35, 26, 1, seeds=1) + flower(80, 151, 64, -.1) + flower(150, 151, 44, .15)
    out += [path("M0 152C60 148 140 154 300 150", 1)]
    body = f'<g class="ink k1">{"".join(out)}</g>'
    return svg("grass", "0 0 300 160", body)


# ---------------------------------------------------------------------------------------------------------------
def skyline():
    """A mosque with a dome and a minaret, a palm and low hills: drawn lightly, as a horizon."""
    k1, k2, k3, tint = [], [], [], []
    # Hills and ground
    k1.append(path("M0 168C70 160 140 166 210 162S330 156 400 160 480 166 560 160", 1.1))
    k3.append(path("M14 150C60 138 110 140 150 150M380 146C420 136 470 136 520 146", .8))
    # Main hall
    k1.append(path("M268 160V122H392V160", 1.4))
    k1.append(path("M262 122H398", 1.2))
    # Dome on its drum
    k1.append(path("M300 122V112H360V122", 1.2))
    k1.append(path("M302 112C300 86 316 72 330 60C344 72 360 86 358 112", 1.5))
    k1.append(path("M330 60V46", 1.2))
    k2.append(path("M333 41a4.5 4.5 0 1 1-4 6.6a3.6 3.6 0 1 0 4-6.6", 1))
    # Dome shading
    for i in range(5):
        x = 344 + i * 3
        k3.append(path(f"M{x} {78 + i * 4}Q{x + 3} {94 + i * 2} {x + 1} {110}", .6))
    # Arches
    for x in (278, 302, 326, 350, 374):
        k2.append(path(f"M{x} 160V142a6 6 0 0 1 12 0V160", 1))
    k3.append(path("M270 128H390", .6))
    # Small side dome
    k1.append(path("M232 160V138H268M236 138C236 126 244 120 250 116C256 120 264 126 264 138", 1.2))
    k2.append(path("M244 160V150a6 6 0 0 1 12 0V160", .9))
    # Minaret
    k1.append(path("M414 160V80M430 160V80", 1.4))
    k1.append(path("M408 96H436M410 100H434", 1))
    k1.append(path("M410 80H434L422 54Z", 1.3))
    k1.append(path("M422 54V44", 1))
    k2.append(path("M424.5 38a3.6 3.6 0 1 1-3.2 5.3a2.9 2.9 0 1 0 3.2-5.3", .9))
    k2.append(path("M418 122v-8a4 4 0 0 1 8 0v8M418 140v-6a4 4 0 0 1 8 0v6", .8))
    # Palm
    k1.append(path("M488 166C490 140 492 114 500 88", 1.5))
    for i in range(7):
        y = 160 - i * 10.5
        x = 489 + i * 1.5
        k3.append(path(f"M{f(x - 3)} {f(y)}q3 -2 6 0", .7))
    crown = (500, 86)
    fronds = [(-58, 6, -10), (-40, 26, 8), (-14, 36, 20), (18, 34, 16), (46, 22, 4), (60, 4, -12), (-30, -14, -22), (24, -16, -24)]
    for dx, dy, bend in fronds:
        ex, ey = crown[0] + dx, crown[1] + dy
        cx, cy = crown[0] + dx * .5, crown[1] + dy * .2 - 14 - bend * .2
        k2.append(path(f"M{pt(crown)}Q{f(cx)} {f(cy)} {f(ex)} {f(ey)}", 1.1))
        for t in (.3, .45, .6, .75, .88):
            px = (1 - t) ** 2 * crown[0] + 2 * (1 - t) * t * cx + t * t * ex
            py = (1 - t) ** 2 * crown[1] + 2 * (1 - t) * t * cy + t * t * ey
            ln = 7 * (1 - t * .5)
            k3.append(path(f"M{f(px)} {f(py)}l{f(dx * .02 + 1)} {f(ln)}", .6))
    # Bushes
    k2.append(path("M196 160c-4-10 6-16 12-12 2-8 14-8 16 0 8-3 14 4 10 12", 1))
    k2.append(path("M452 162c-3-8 5-13 10-9 3-7 12-6 13 1 6-2 10 4 7 9", 1))
    k2.append(path("M520 160c-2-7 4-11 9-8 2-6 11-5 12 1", .9))
    # A low tree on the left
    k1.append(path("M70 160C72 146 72 134 70 122", 1.2))
    k2.append(path("M52 124c-8-6-2-18 8-15 0-10 14-12 18-4 9-4 18 4 14 12 7 4 4 14-4 14-4 6-14 6-18 1-8 3-16-2-18-8z", 1.1))
    # Birds
    k3.append(path("M150 52q5-5 9 0q4-5 9 0M184 70q4-4 7 0q3-4 7 0", .9))
    # Wash under the buildings
    tint.append(fill("M224 162C228 128 260 118 296 116C300 80 320 66 330 58C346 72 364 90 362 114C380 116 400 116 410 120V84C420 70 432 76 434 96V162Z", .16))
    tint.append(fill("M40 128c-6-14 8-24 18-18 4-12 22-12 26 0 12-4 22 8 14 18-6 6-30 10-58 0z", .22))
    tint.append(fill("M466 92c10-12 30-14 44-6 12-2 22 6 20 14-20 0-40 2-64-8z", .22))
    body = (f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(tint)}</g><g class="ink k1">{"".join(k1)}</g>'
            f'<g class="ink k2">{"".join(k2)}</g><g class="ink k3">{"".join(k3)}</g>')
    return svg("skyline", "0 0 560 180", body)


# ---------------------------------------------------------------------------------------------------------------
def icons():
    """The start page's four feature drawings, 96 x 72, in the same ink and wash."""
    # Qur'an: an open book on a small stand
    q = [path("M48 40C40 30 28 26 14 28L13 34Q30 32 48 44Q66 32 83 34L82 28C68 26 56 30 48 40", 1.3),
         path("M48 40V46", 1.1), path("M28 64L68 40M68 64L28 40", 1.3), path("M24 66H34M62 66H72", 1.1),
         path("M20 31Q32 29 44 36M21 34.5Q33 32.5 44 39M52 36Q64 29 76 31M52 39Q63 32.5 75 34.5", .7)]
    qt = [fill("M15 30C30 27 42 32 48 41C54 32 66 27 82 30L83 36Q64 33 48 46Q32 33 13 36Z", .28)]
    # Memorisation: a stack of three books
    m = [path("M18 58H74V66H18Z", 1.3), path("M22 50H70V58H22Z", 1.3), path("M16 42H66V50H16Z", 1.3),
         path("M26 58V66M66 50V58M24 42V50", .9), path("M30 62H62M34 54H58M30 46H58", .6),
         path("M70 42q8-12 4-24M74 18q-6 2-8 8", 1)]
    mt = [fill("M17 43H66V51H22V58H74V67H17Z", .26)]
    # Houses: three flags, as a podium
    h = [path("M24 66V30M48 66V18M72 66V36", 1.3), path("M24 30L40 35L24 40", 1.2), path("M48 18L64 23L48 28", 1.2),
         path("M72 36L88 41L72 46", 1.2), path("M14 66H86", 1.1)]
    ht = [fill("M25 30L41 35L25 41Z", .55, "hm"), fill("M49 18L65 23L49 29Z", .55, "hd"), fill("M73 36L89 41L73 47Z", .55, "ha")]
    # Progress: rising bars with a shoot growing from the tallest
    p = [path("M18 66V54H28V66M36 66V46H46V66M54 66V38H64V66M72 66V28H82V66", 1.3), path("M12 66H88", 1.1),
         path("M77 28C77 22 78 18 80 14", 1), path("M80 16C84 10 90 10 92 12C88 16 84 16 80 16", 1),
         path("M79 20C75 16 70 16 68 18C71 21 75 21 79 20", 1)]
    pt_ = [fill("M18 54H28V66H18ZM36 46H46V66H36ZM54 38H64V66H54ZM72 28H82V66H72Z", .22)]
    out = {}
    for name, ink, t in (("i-quran", q, qt), ("i-memo", m, mt), ("i-houses", h, ht), ("i-progress", p, pt_)):
        body = f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(t)}</g><g class="ink k1">{"".join(ink)}</g>'
        out[name] = svg(name, "0 0 96 72", body, "icon")
    return out


# ---------------------------------------------------------------------------------------------------------------
def values():
    """The start page's three values, 120 x 96: a grown plant with a seedling beside it (the next generation), an
    oil lamp with its flame (innovation), and a key whose bow is the eight-point star (amānah, a trust)."""
    # The next generation: a tall plant and a young one, in the same ground.
    g1, g2, gt = [], [], []
    stem = ((44, 86), (42, 64), (50, 44), (48, 16))
    g1.append(path(f"M{pt(stem[0])}C{pt(stem[1])} {pt(stem[2])} {pt(stem[3])}", 1.3))
    for i, t in enumerate((.22, .4, .56, .72, .86)):
        p = cubic(*stem, t)
        a = cubic_tan(*stem, t) + (1 if i % 2 else -1) * math.radians(48)
        o, rb, w = leaf(p, a, 22 * (1 - .4 * t), 7 * (1 - .3 * t), bend=.18 * (1 if i % 2 else -1))
        g2 += [path(o, .95), path(rb, .55)]
        gt.append(fill(w, .42))
    o, rb, w = leaf(stem[3], cubic_tan(*stem, 1), 12, 4, bend=.1)
    g2 += [path(o, .95), path(rb, .55)]; gt.append(fill(w, .46))
    seed = ((86, 86), (86, 78), (84, 72), (86, 64))
    g1.append(path(f"M{pt(seed[0])}C{pt(seed[1])} {pt(seed[2])} {pt(seed[3])}", 1.2))
    for ang in (-150, -30):
        o, rb, w = leaf(seed[3], math.radians(ang), 13, 5, bend=.16 if ang < -90 else -.16)
        g2 += [path(o, .95), path(rb, .55)]; gt.append(fill(w, .5))
    g1.append(path("M10 87C34 84 62 88 110 85", 1.1))
    g2.append(path("M32 87c4-4 20-4 24 0M76 87c3-3 16-3 19 0", .9))
    gen = (f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(gt)}</g><g class="ink k1">{"".join(g1)}</g>'
           f'<g class="ink k2">{"".join(g2)}</g>')
    # Innovation: an oil lamp, its flame lit, light rising from it.
    l1 = [path("M24 62C24 50 52 46 66 53L94 44C99 43 101 47 97 50L78 64C70 72 30 74 24 62Z", 1.35),
          path("M46 74C44 79 44 82 46 84H70C72 82 72 79 70 74", 1.2), path("M40 86H76", 1.2),
          path("M27 58C13 54 10 70 25 68", 1.2), path("M48 50C48 43 60 43 60 50", 1.1), path("M54 43V39", 1)]
    l2 = [path("M97 42C91 35 95 27 98 21C101 27 105 35 99 42", 1), path("M98 38C96 35 97 31 98.5 29", .7),
          path("M98 14V8M88 18L84 14M108 18L112 14", .9)]
    l3 = [path("M30 63C42 67 60 67 74 62", .7), path("M36 58Q44 56 52 57", .6)]
    lt = [fill("M26 62C26 51 52 48 66 55L92 47L77 63C69 70 32 72 26 62Z", .3),
          fill("M97 42C92 35 95 28 98 22C101 28 104 35 99 42Z", .85, "fl")]
    lamp = (f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(lt)}</g><g class="ink k1">{"".join(l1)}</g>'
            f'<g class="ink k2">{"".join(l2)}</g><g class="ink k3">{"".join(l3)}</g>')
    # Amānah: a key whose bow is the eight-point star, with a ring for its hole.
    cx, cy, h = 34, 48, 15
    sq1 = f"M{f(cx - h)} {f(cy - h)}H{f(cx + h)}V{f(cy + h)}H{f(cx - h)}Z"
    d = h * math.sqrt(2)
    sq2 = f"M{f(cx)} {f(cy - d)}L{f(cx + d)} {f(cy)}L{f(cx)} {f(cy + d)}L{f(cx - d)} {f(cy)}Z"
    k1 = [path(sq1, 1.25), path(sq2, 1.25), path(f"M{f(cx - 6)} {f(cy)}a6 6 0 1 0 12 0a6 6 0 1 0-12 0", 1.1),
          path("M55.2 45H100a3 3 0 0 1 0 6H55.2", 1.3), path("M84 51V63H90V58H95V51", 1.2)]
    k2 = [path("M61 42.5V53.5M66 42.5V53.5", 1), path("M74 48H96", .6)]
    kt = [fill(sq1, .26), fill(sq2, .26), fill("M56 46H99a2 2 0 0 1 0 4H56Z", .3)]
    key = (f'<g class="tint" fill="#7f8f4e" stroke="none">{"".join(kt)}</g><g class="ink k1">{"".join(k1)}</g>'
           f'<g class="ink k2">{"".join(k2)}</g>')
    return {name: svg(name, "0 0 120 96", body, "icon") for name, body in
            (("v-generation", gen), ("v-lamp", lamp), ("v-key", key))}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    sprig(); rahle(); grass(); skyline(); icons(); values()
    print("written to", OUT)
