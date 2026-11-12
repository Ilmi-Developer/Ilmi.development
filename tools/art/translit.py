"""Lilita One for Ilmi, and the transliteration letters it lacks. Lilita One is under the OFL
(static/fonts/OFL.txt).

    python3 tools/art/translit.py LilitaOne-Regular.ttf      (the file from Google Fonts, ofl/lilitaone)

writes two files to static/fonts:

- lilita-one.woff2: Lilita One exactly as published, only compressed. Nothing is added or taken away, so it keeps
  its name.
- ilmi-translit.woff2: the letters Arabic is written with in Latin script that Lilita One doesn't have
  (ā ī ū Ā Ī Ū, ḥ ṣ ḍ ṭ ẓ and their capitals, ʿ ʾ), which the site shows in every greeting, in Qāʿidah and Tajwīd and
  in hadith references. Each is put together from Lilita One's own shapes, so it sits in a word without a change
  of font: the letter, with a bar over it made from the hyphen or a dot under it made from the full stop, and ʿ ʾ
  are its quotation marks. This is a changed copy of the font, and Lilita One is a reserved font name, so the file
  carries a name of its own, Ilmi Translit. tokens.css lists it after Lilita One for those letters alone.

Needs fontTools with brotli."""
import sys
from pathlib import Path

from fontTools import subset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.woff2 import compress

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "static" / "fonts"
NAME = "Ilmi Translit"
# How far Lilita One lifts an accent from a small letter to a capital (its ä and Ä).
CAP_LIFT = 200
# The bar over a long vowel: the hyphen, shortened and thinned to the weight of the font's own accents, and where its
# foot sits over a small letter. The dot under a letter: the full stop at this size, its top this far below the line.
BAR_WIDTH, BAR_THICK, BAR_FOOT = 0.66, 0.84, 584
DOT_SIZE, DOT_TOP = 0.62, -52
MACRON = {"ā": "a", "ī": "ı", "ū": "u", "Ā": "A", "Ī": "I", "Ū": "U"}
DOT_BELOW = {"ḥ": "h", "ṣ": "s", "ḍ": "d", "ṭ": "t", "ẓ": "z", "Ḥ": "H", "Ṣ": "S", "Ḍ": "D", "Ṭ": "T", "Ẓ": "Z"}
# ʿ (ʿayn) and ʾ (hamzah) are written with the turned and the plain quotation mark.
SAME_AS = {"ʿ": "‘", "ʾ": "’"}


def bounds(font, glyph):
    pen = BoundsPen(font.getGlyphSet())
    font.getGlyphSet()[glyph].draw(pen)
    return pen.bounds


def build(source):
    font = TTFont(source)
    cmap = font.getBestCmap()
    glyphs, glyf, hmtx = font.getGlyphSet(), font["glyf"], font["hmtx"]
    made = {}

    def put(char, base, parts, advance=None):
        """A new glyph for `char`: each part is a glyph of the font and where it goes (xx, yy, dx, dy)."""
        pen = TTGlyphPen(glyphs)
        for glyph, (xx, yy, dx, dy) in parts:
            glyphs[glyph].draw(TransformPen(pen, (xx, 0, 0, yy, dx, dy)))
        name = "uni%04X" % ord(char)
        made[name] = (ord(char), pen.glyph(), advance if advance is not None else hmtx[base][0])

    hx0, hy0, hx1, hy1 = bounds(font, cmap[ord("-")])
    for char, letter in MACRON.items():
        base = cmap[ord(letter)]
        x0, _, x1, _ = bounds(font, base)
        capital = letter.isupper()
        # The bar is as wide as the letter allows, a little wider than a narrow one (ı, I), which is then given the
        # room its ï has.
        width = max((x1 - x0) * BAR_WIDTH, 250)
        xx = width / (hx1 - hx0)
        advance = max(hmtx[base][0], round(width + 60))
        shift = (advance - hmtx[base][0]) / 2
        dx = shift + (x0 + x1) / 2 - xx * (hx0 + hx1) / 2
        dy = BAR_FOOT + (CAP_LIFT if capital else 0) - BAR_THICK * hy0
        put(char, base, [(base, (1, 1, shift, 0)), (cmap[ord("-")], (xx, BAR_THICK, dx, dy))], advance)
    px0, _, px1, py1 = bounds(font, cmap[ord(".")])
    for char, letter in DOT_BELOW.items():
        base = cmap[ord(letter)]
        x0, _, x1, _ = bounds(font, base)
        dx = (x0 + x1) / 2 - DOT_SIZE * (px0 + px1) / 2
        put(char, base, [(base, (1, 1, 0, 0)), (cmap[ord(".")], (DOT_SIZE, DOT_SIZE, dx, DOT_TOP - DOT_SIZE * py1))])
    for char, like in SAME_AS.items():
        base = cmap[ord(like)]
        put(char, base, [(base, (1, 1, 0, 0))])

    order = font.getGlyphOrder()
    for name, (code, glyph, advance) in made.items():
        order.append(name)
        glyf[name] = glyph
        glyph.recalcBounds(glyf)
        hmtx[name] = (advance, glyph.xMin if glyph.numberOfContours else 0)
        for table in font["cmap"].tables:
            if table.isUnicode():
                table.cmap[code] = name
    font.setGlyphOrder(order)
    # A changed copy: its own name, the original's copyright and licence kept, and nothing that vouched for the
    # original file (its signature) or that only the letters left out would use.
    for table in ("DSIG", "FFTM", "GPOS", "GSUB", "GDEF"):
        if table in font:
            del font[table]
    names = font["name"]
    notice = names.getDebugName(0)
    for record in list(names.names):
        if record.nameID in (1, 4, 16):
            names.setName(NAME, record.nameID, record.platformID, record.platEncID, record.langID)
        elif record.nameID == 6:
            names.setName(NAME.replace(" ", ""), 6, record.platformID, record.platEncID, record.langID)
        elif record.nameID == 3:
            names.setName(NAME + " 1.0: letters added to Lilita One 1.002 for Ilmi", 3, record.platformID, record.platEncID, record.langID)
        elif record.nameID == 0:
            names.setName(notice + ". Ilmi Translit: transliteration letters put together from its glyphs for Ilmi, 2026.",
                          0, record.platformID, record.platEncID, record.langID)
    return font, sorted(code for code, _, _ in made.values())


def main(source):
    out = FONTS / "lilita-one.woff2"
    compress(source, str(out))
    print(f"{out.name}: {out.stat().st_size} bytes, Lilita One as published")
    font, codes = build(source)
    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.hinting = False
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codes)
    subsetter.subset(font)
    out = FONTS / "ilmi-translit.woff2"
    subset.save_font(font, str(out), options)
    print(f"{out.name}: {out.stat().st_size} bytes, {len(codes)} letters: " + "".join(map(chr, codes)))
    print("unicode-range:" + ",".join("U+%04X" % c for c in codes))


if __name__ == "__main__":
    main(sys.argv[1])
