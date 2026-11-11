"""Amiri subsets for Ilmi, so pages load only the Arabic letters they show. Amiri is under the OFL
(static/fonts/OFL.txt).

    python3 tools/art/fonts.py Amiri-Regular.ttf      (or the .woff2 from Google Fonts)

writes static/fonts/amiri-text.woff2: every Arabic letter and mark on the start page (templates/welcome.html), for
the hadith and the values' names, with Amiri's joining forms and vowel placement kept. Run it again whenever that
Arabic changes, or the new letters fall back to another font. amiri-ilmi.woff2 (the wordmark's علمي) was cut the
same way from Amiri Bold with only those four letters. Needs fontTools with brotli."""
import re
import sys
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[2]
ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def main(source):
    page = (ROOT / "templates" / "welcome.html").read_text(encoding="utf-8")
    text = "".join(sorted(set(ARABIC.findall(page))))
    out = ROOT / "static" / "fonts" / "amiri-text.woff2"
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.hinting = False
    font = subset.load_font(source, options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text + " ")
    subsetter.subset(font)
    subset.save_font(font, str(out), options)
    print(f"{out.name}: {out.stat().st_size} bytes, {len(text)} characters")


if __name__ == "__main__":
    main(sys.argv[1])
