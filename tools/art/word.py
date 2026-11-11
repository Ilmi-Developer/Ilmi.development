"""علمي, Ilmi's name in Arabic, written by hand for the sign-in page's opening (templates/art/intro-word.svg, and the
pen's timings in static/auth.css).

    python3 tools/art/word.py

The word is shaped with HarfBuzz from the wordmark's own font (static/fonts/amiri-ilmi.woff2, Amiri Bold) with
Amiri's required ligatures (rlig) turned off, as the wordmark is everywhere, so لم sits level on the line instead of
stacking. Each letter's outline becomes a clip path, and the ink inside it is laid down by pen strokes: the path the
pen's centre takes through the letter, in the order a calligrapher writes the word (ع's head and out to its chin,
back along the line and up into ل's foot; then ل's stem from the top down, on through م's loop and ي to the end of
its tail; then ي's two dots), each stroke as wide as the ink it has to cover. Every stroke draws itself in with a
dash (pathLength 1), wet olive ink first and the dried ink a beat behind it.

The pen keeps a pace of its own (MOTION): it starts from rest, slows into turns, stops where it doubles back and
flicks off the end of ي's tail. Each piece of a stroke eases with the cubic that meets the pen's speed at both of
its ends, so one piece runs on into the next without a jolt. The timings go into static/auth.css between the pen
markers. Coordinates are font units (1000 to the em) with y pointing down, and the view box leaves a margin round
the letters.
Needs fontTools with brotli, and uharfbuzz."""
import io
import math
import re
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
WORD = "علمي"
SIDE = 70        # room either side of the letters, in font units
MARGIN = 120     # above the tallest ink and below the lowest

# The pen's path through each letter, in writing order: (letter, name, runs). A run is (width, points); the runs of
# one stroke share their end points and are smoothed as one curve, so a stroke can lay down more ink where the
# letter is thicker. Letters count from the right: 1 ع, 2 ل, 3 م, 4 ي, 5 ي's dots.
STROKES = [
    (1, "a", [(112, [(1560, -326), (1505, -362), (1440, -383), (1385, -374), (1335, -337), (1302, -282),
                     (1292, -222), (1306, -176)]),
              (112, [(1306, -176), (1360, -166), (1440, -166), (1515, -178), (1590, -210), (1650, -246)])]),
    (1, "b", [(124, [(1656, -214), (1600, -182), (1520, -144), (1440, -108), (1360, -74)]),
              (140, [(1360, -74), (1298, -56), (1250, -50)])]),
    (2, "f", [(108, [(1240, -36), (1214, -46), (1196, -80), (1182, -125), (1168, -170)])]),
    (2, "t", [(96, [(1122, -668), (1127, -520), (1132, -360), (1136, -230)]),
              (116, [(1136, -230), (1128, -150), (1112, -100), (1092, -62), (1066, -40), (1046, -36)])]),
    (3, "m", [(128, [(1048, -44), (990, -58), (935, -82), (890, -120)]),
              (96, [(890, -120), (848, -185), (808, -222), (764, -206), (728, -163), (738, -112), (790, -78),
                    (850, -52), (894, -4)])]),
    (3, "n", [(108, [(894, -4), (838, -24), (770, -50), (702, -82), (650, -66)]),
              (124, [(650, -66), (592, -45)])]),
    (4, "y", [(124, [(598, -45), (540, -45)]),
              (116, [(540, -45), (478, -24), (444, 36), (462, 80), (520, 78), (590, 86), (650, 100)])]),
    (4, "z", [(132, [(650, 106), (646, 150), (615, 200), (560, 236), (500, 262), (430, 285), (310, 304), (205, 292),
                     (135, 248), (96, 172), (93, 90), (110, 12), (136, -48), (150, -72)])]),
    (5, "d", [(124, [(358, 451), (478, 523)])]),    # each dot is one press of the nib, so a square-ended stroke
    (5, "e", [(124, [(257, 473), (372, 546)])]),
]

# How the pen moves through those pieces, in order: (seconds before it starts, seconds it takes, speed at its start,
# speed at its end), speeds in font units a second. None for both speeds is an even pace.
MOTION = [
    (0.18, 0.22, 0, 1300),        # ع: from the tip of the head round its back, slowing into the turn
    (0, 0.16, 1300, 0),           # and out along the jaw to the chin, where the pen stops to double back
    (0.02, 0.13, 0, 2300),        # back down along the line,
    (0, 0.05, 2300, 1600),
    (0, 0.09, 1600, 0),           # and up into ل's foot
    (0.08, 0.15, 0, 2600),        # ل's stem from the top
    (0, 0.10, 2600, 1700),        # round its foot into م
    (0, 0.09, 1700, 1800),
    (0, 0.25, 1800, 0),           # م's loop, stopping at its spur
    (0.015, 0.12, 0, 1900),       # back under it to ي
    (0, 0.035, 1900, 1800),
    (0, 0.035, 1800, 1700),
    (0, 0.17, 1700, 0),           # ي's neck, stopping where it turns
    (0.015, 0.36, 0, 2400),       # the sweep of its tail, flicked off the end
    (0.08, 0.07, None, None),     # the dots
    (0.035, 0.07, None, None),
]
DRY = 0.14                        # how far the dried ink follows behind the wet


def num(v):
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def t3(v):
    """A time or easing value in CSS, three places at most and no leading zero."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    s = "0" if s in ("-0", "") else s
    return s.replace("0.", ".", 1) if s.startswith(("0.", "-0.")) else s


class Path2(SVGPathPen):
    """An SVG path pen that writes one decimal place, which is plenty at a thousand units to the em."""
    def __init__(self):
        super().__init__(None, ntos=num)


def shape(font_path):
    font = TTFont(font_path)
    font.flavor = None
    data = io.BytesIO()
    font.save(data)
    hbfont = hb.Font(hb.Face(hb.Blob(data.getvalue())))
    buf = hb.Buffer()
    buf.add_str(WORD)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {"rlig": False})
    glyphs, x = [], 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        glyphs.append((info.cluster, font.getGlyphOrder()[info.codepoint], x + pos.x_offset, pos.y_offset))
        x += pos.x_advance
    return font.getGlyphSet(), glyphs


def contours(glyphset, name, dx, dy):
    """The glyph's contours, each as (commands, bounds, area), moved into place with y flipped to point down."""
    rec = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(rec)
    out, cur = [], []
    for op, args in rec.value:
        cur.append((op, [(px + dx, -(py + dy)) for px, py in args]))
        if op in ("closePath", "endPath"):
            out.append(cur)
            cur = []
    result = []
    for c in out:
        pen, bounds, area = Path2(), BoundsPen(None), AreaPen()
        for op, pts in c:
            getattr(pen, op)(*pts)
            getattr(bounds, op)(*pts)
            getattr(area, op)(*pts)
        result.append((pen.getCommands(), bounds.bounds, area.value))
    return result


def letters():
    """The word's letters in writing order (ع, ل, م, ي, then ي's dots), each a list of (commands, bounds, area)."""
    glyphset, glyphs = shape(ROOT / "static" / "fonts" / "amiri-ilmi.woff2")
    out = []
    for cluster, name, dx, dy in sorted(glyphs, key=lambda g: g[0]):
        parts = contours(glyphset, name, dx, dy)
        main_part = max(parts, key=lambda p: abs(p[2]))
        mb = main_part[1]
        # A dot winds the same way as the letter's main stroke (a hole, like م's, winds the other way) and stands
        # clear of it.
        dots = [p for p in parts if p is not main_part and p[2] * main_part[2] > 0
                and (p[1][3] < mb[1] or p[1][1] > mb[3] or p[1][2] < mb[0] or p[1][0] > mb[2])]
        out.append([p for p in parts if p not in dots])
        if dots:
            out.append(dots)
    return out


def beziers(pts):
    """A smooth curve through the points (Catmull-Rom), as cubic Bézier segments (start, control, control, end)."""
    if len(pts) == 2:
        (x0, y0), (x1, y1) = pts
        return [(pts[0], (x0 + (x1 - x0) / 3, y0 + (y1 - y0) / 3), (x0 + 2 * (x1 - x0) / 3, y0 + 2 * (y1 - y0) / 3),
                 pts[1])]
    ext = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        out.append((p1, (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
                    (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6), p2))
    return out


def bezier_length(seg, steps=48):
    p0, c1, c2, p1 = seg
    def at(t):
        u = 1 - t
        return tuple(u * u * u * p0[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t * t * t * p1[k]
                     for k in (0, 1))
    pts = [at(i / steps) for i in range(steps + 1)]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def pieces():
    """Every run of every stroke, in writing order: (letter, width, path data, length in font units)."""
    out = []
    for letter, _, runs in STROKES:
        pts, spans = [], []
        for width, run in runs:
            start = max(len(pts) - 1, 0)
            pts += run if not pts else run[1:]
            spans.append((width, start, len(pts) - 1))
        segs = beziers(pts)
        for width, a, b in spans:
            part = segs[a:b]
            d = f"M{part[0][0][0]:.0f} {part[0][0][1]:.0f}" + "".join(
                f"C{c1[0]:.0f} {c1[1]:.0f} {c2[0]:.0f} {c2[1]:.0f} {p1[0]:.0f} {p1[1]:.0f}" for _, c1, c2, p1 in part)
            out.append((letter, width, d, sum(bezier_length(s) for s in part)))
    return out


def easing(length, seconds, v0, v1):
    """The cubic-bezier() whose curve leaves at v0 and arrives at v1 (font units a second) over the piece: with its
    control points a third of the way along in time, it is the cubic with those slopes at its ends."""
    if v0 is None:
        return "linear"
    a, b = v0 * seconds / length, v1 * seconds / length
    # The pen must never run backwards: the slope 3(a+b-2)t² + 2(3-2a-b)t + a stays at or above zero.
    for i in range(101):
        t = i / 100
        assert 3 * (a + b - 2) * t * t + 2 * (3 - 2 * a - b) * t + a >= -1e-9, (length, seconds, v0, v1)
    return f"cubic-bezier(.333,{t3(a / 3)},.667,{t3(1 - b / 3)})"


def main():
    parts = letters()
    boxes = [b for letter in parts for _, b, _ in letter]
    x0, x1 = min(b[0] for b in boxes) - SIDE, max(b[2] for b in boxes) + SIDE
    y0, y1 = min(b[1] for b in boxes) - MARGIN, max(b[3] for b in boxes) + MARGIN
    runs = pieces()
    assert len(runs) == len(MOTION), (len(runs), len(MOTION))

    clips = "".join(f'<clipPath id="iwc{i}"><path d="{"".join(d for d, _, _ in letter)}"/></clipPath>'
                    for i, letter in enumerate(parts, 1))
    def layer(kind):
        groups = []
        for i in range(1, len(parts) + 1):
            paths = "".join(f'<path class="p{n}" pathLength="1" stroke-width="{w}" d="{d}"/>'
                            for n, (letter, w, d, _) in enumerate(runs, 1) if letter == i)
            cap = ' stroke-linecap="butt"' if i == len(parts) else ""
            groups.append(f'<g clip-path="url(#iwc{i})"{cap}>{paths}</g>')
        return f'<g class="{kind}">{"".join(groups)}</g>'
    svg = (f'<svg class="iword-art" viewBox="{num(x0)} {num(y0)} {num(x1 - x0)} {num(y1 - y0)}" fill="none" '
           f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><defs>{clips}</defs>'
           f'{layer("wet")}{layer("dry")}</svg>\n')
    out = ROOT / "templates" / "art" / "intro-word.svg"
    out.write_text(svg, encoding="utf-8")

    rules, clock = [], 0.0
    for n, ((_, _, _, length), (pause, seconds, v0, v1)) in enumerate(zip(runs, MOTION), 1):
        clock += pause
        rules.append(f".iword .p{n}{{animation:i-pen {t3(seconds)}s {easing(length, seconds, v0, v1)} "
                     f"{t3(clock)}s both}}.iword .dry .p{n}{{animation-delay:{t3(clock + DRY)}s}}")
        clock += seconds
    done = clock + DRY
    rules.append(f".iword .wet{{animation:i-fade .2s linear {t3(done)}s forwards}}")
    css_path = ROOT / "static" / "auth.css"
    css = css_path.read_text(encoding="utf-8")
    block = ("/* pen: written by tools/art/word.py */\n" + "\n".join(rules) + "\n/* /pen */")
    css, found = re.subn(r"/\* pen: written by tools/art/word\.py \*/\n(?:.*?\n)?/\* /pen \*/", lambda m: block, css,
                         flags=re.S)
    assert found == 1, "static/auth.css needs the pen markers"
    css_path.write_text(css, encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {len(svg)} bytes, view box {num(x1 - x0)} x {num(y1 - y0)}, "
          f"{len(parts)} letters, {len(runs)} pen strokes, baseline at {num(-y0 / (y1 - y0) * 100)}% from the top; "
          f"the pen lifts at {t3(clock)}s and the ink is dry at {t3(done)}s")


if __name__ == "__main__":
    main()
