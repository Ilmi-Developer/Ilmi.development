// The Ilmi mark: the letter ʿayn (ع), the first letter of ʿilm, knocked out of an olive disc. Renders the letter in
// Amiri Bold (the site's own subset, static/fonts/amiri-ilmi.woff2), then tools/art/mark.py turns it into
// static/favicon.png (cream letter on olive) and the logo (the letter knocked out). Run:
//   node tools/art/mark.js /tmp/ayn && python3 tools/art/mark.py /tmp/ayn.png static/favicon.png /tmp/logo.png
const { chromium } = require('playwright');
const FONT = require('path').resolve(__dirname, '../../static/fonts/amiri-ilmi.woff2');
const out = process.argv[2];
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 768, height: 768 } });
  const fs = require('fs');
  const html = `<!doctype html><style>@font-face{font-family:A;src:url(file://${FONT})}
  html,body{margin:0;background:#fff}div{width:768px;height:768px;display:grid;place-items:center;font:700 560px/1 A;color:#000;padding-bottom:40px;box-sizing:border-box}</style><div>ع</div>`;
  fs.writeFileSync(out + '.html', html);
  await p.goto('file://' + out + '.html');
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(300);
  await p.screenshot({ path: out });
  await b.close();
})();
