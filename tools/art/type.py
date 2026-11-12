"""Ilmi's two faces, cut down for the web. Both are under the OFL (static/fonts/OFL.txt).

    python3 tools/art/type.py path/to/google/fonts/ofl

reads Fraunces and Nunito from a copy of the Google Fonts repository (ofl/fraunces, ofl/nunito) and writes to
static/fonts:

- fraunces.woff2, fraunces-italic.woff2: Fraunces with its Soft axis set all the way (rounded serifs and terminals)
  and Wonky off (upright letters stay upright), keeping its weights 400 to 700 and its optical sizes, so small titles
  get sturdier letters and big ones finer. The wordmark, titles, figures and the large italic lines.
- nunito.woff2: Nunito, weights 400 to 800. Everything you read and use.

Each keeps the letters Ilmi shows: Latin with its accents, the transliteration letters (ā ī ū ḥ ṣ ʿ ʾ and the rest),
punctuation, arrows and the euro. Fraunces has no ʿ (ʿayn) or ʾ (hamzah), so they are given its turned and plain
quotation marks, as they are usually written; that's a change to the font's character map only (Fraunces reserves no
font name under the OFL). Needs fontTools with brotli."""
import io
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "static" / "fonts"
UNICODES = [*range(0x20, 0x7F), *range(0xA0, 0x180), 0x0192, 0x02BB, 0x02BC, 0x02BE, 0x02BF, 0x02C6, 0x02C7, 0x02DA,
            0x02DC, *range(0x1E00, 0x1F00), *range(0x2000, 0x2070), 0x20AC, 0x2122, *range(0x2190, 0x219A), 0x2212,
            0x2215, 0x2713]
# ʿ and ʾ drawn with the quotation marks, in fonts that lack them.
SAME_AS = {0x02BF: 0x2018, 0x02BE: 0x2019}
FACES = (("fraunces/Fraunces[SOFT,WONK,opsz,wght].ttf", "fraunces.woff2", {"SOFT": 100, "WONK": 0, "wght": (400, 700)}),
         ("fraunces/Fraunces-Italic[SOFT,WONK,opsz,wght].ttf", "fraunces-italic.woff2",
          {"SOFT": 100, "WONK": 0, "wght": (400, 700)}),
         ("nunito/Nunito[wght].ttf", "nunito.woff2", {"wght": (400, 800)}))


def main(ofl):
    for source, name, axes in FACES:
        font = instantiateVariableFont(TTFont(Path(ofl) / source, lazy=False), axes, updateFontNames=False)
        # Written out and read back, so the subsetter sees the pinned font as a whole.
        data = io.BytesIO()
        font.save(data)
        font = TTFont(io.BytesIO(data.getvalue()), lazy=False)
        cmap = font.getBestCmap()
        for code, like in SAME_AS.items():
            if code not in cmap and like in cmap:
                for table in font["cmap"].tables:
                    if table.isUnicode():
                        table.cmap[code] = cmap[like]
        options = subset.Options()
        options.flavor = "woff2"
        options.layout_features = ["kern", "liga", "calt", "ccmp", "locl", "mark", "mkmk", "lnum", "pnum", "case"]
        options.name_IDs = ["*"]
        options.hinting = False
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=UNICODES)
        subsetter.subset(font)
        out = FONTS / name
        subset.save_font(font, str(out), options)
        print(f"{name}: {out.stat().st_size // 1024} KB, axes {[a.axisTag for a in font['fvar'].axes]}")


if __name__ == "__main__":
    main(sys.argv[1])
