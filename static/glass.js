/* Ilmi's glass: liquid glass on the surfaces that float over the page (static/glass.css says which, and how
   strongly each is tinted).

   A port of GlassSurface from React Bits (reactbits.dev, MIT) to plain script, as the site has no React. Where the
   browser can put an SVG filter in backdrop-filter (Chromium), each surface gets a displacement map drawn for its
   size and corners, so what is behind it bends at its edges with a faint split into colours, and its middle stays
   still. Safari and Firefox get frosted glass instead (glass.css). The surfaces keep their own layout: this only
   adds the filter, a rim of light and the class names glass.css styles.

   Nothing changes for anyone who asks their system for less transparency or more contrast, or for print.

   Surfaces are found by SPOTS below or a data-glass attribute (options as data-glass-scale="-120" and so on), on
   the page and as they are added later, such as the dock (static/dock.js) and the dashboard's menus. The map is a
   data: image, so pages that run this need img-src data: in their Content-Security-Policy (server.py). */
(function () {
  "use strict";
  if (window.IlmiGlass || typeof document === "undefined") return;

  var SVGNS = "http://www.w3.org/2000/svg";
  // GlassSurface's defaults.
  var DEFAULTS = { borderWidth: 0.07, brightness: 50, opacity: 0.93, blur: 11, displace: 0, scale: -180,
    redOffset: 0, greenOffset: 10, blueOffset: 20, xChannel: "R", yChannel: "G", blend: "difference", saturation: 1 };

  // Where glass goes. fallback: false keeps the surface as it was in Safari and Firefox, for those that are
  // frosted already (the dock: Safari flashed while things scaled over its blurred bar). phone: false keeps it off
  // on phones and touch tablets (LITE), where a filter redrawn under a surface on every frame of a scroll costs more
  // than it gives: the start page's decorations, which scroll with the page.
  var SPOTS = [
    // Everywhere signed in: the dock and its pop-ups.
    [".idk-bar", { scale: -150, fallback: false }],
    [".idk-pop", { scale: -120, fallback: false }],
    // The dashboard: search, the question box and the menus that open from a button.
    [".qkp", { scale: -140 }],
    [".dlg .card", { scale: -140 }],
    [".csm", { scale: -110 }],
    // The developer: their bar, the teacher picker and the schools.
    [".devbar", { scale: -90, borderWidth: 0.12 }],
    [".devpcard", { scale: -140 }],
    [".sch-tile", { scale: -120 }],
    // Signed out: the Welcome tab on the sign-in panel, and the start page's dock picture, values and ghost button.
    [".wtab", { scale: -100, borderWidth: 0.16 }],
    [".mk-dock", { scale: -110, fallback: false, phone: false }],
    [".vcard", { scale: -160, phone: false }],
    [".wbtn.ghost", { scale: -90, borderWidth: 0.14, phone: false }],
    ["[data-glass]", {}]
  ];
  var SELECTOR = SPOTS.map(function (s) { return s[0]; }).join(",");

  function quiet() {
    try {
      return matchMedia("(prefers-reduced-transparency: reduce)").matches || matchMedia("(prefers-contrast: more)").matches;
    } catch (e) { return false; }
  }

  // As GlassSurface: the SVG filter only where backdrop-filter takes a url(), and never in Safari or Firefox, which
  // accept it and draw nothing.
  function svgFilters() {
    var ua = navigator.userAgent;
    if ((/Safari/.test(ua) && !/Chrome|Chromium/.test(ua)) || /Firefox/.test(ua)) return false;
    var probe = document.createElement("div");
    probe.style.backdropFilter = "url(#ilmi-glass-probe)";
    return probe.style.backdropFilter !== "";
  }

  // The same query as static/welcome.js and welcome.css.
  var LITE = "(max-width: 959.98px), (pointer: coarse)";
  function phone() { try { return matchMedia(LITE).matches; } catch (e) { return false; } }

  var refract = svgFilters();
  var count = 0;
  var surfaces = new Map();

  function optionsFor(el) {
    var o = {};
    var k;
    for (k in DEFAULTS) o[k] = DEFAULTS[k];
    o.fallback = true;
    o.phone = true;
    SPOTS.forEach(function (s) {
      if (s[0] !== "[data-glass]" && el.matches(s[0])) for (k in s[1]) o[k] = s[1][k];
    });
    var d = el.dataset;
    for (k in DEFAULTS) {
      var v = d["glass" + k.charAt(0).toUpperCase() + k.slice(1)];
      if (v == null || v === "") continue;
      o[k] = typeof DEFAULTS[k] === "number" ? (isFinite(parseFloat(v)) ? parseFloat(v) : o[k]) : v;
    }
    return o;
  }

  function el(name, attrs) {
    var n = document.createElementNS(SVGNS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  // The filter of GlassSurface: each colour channel moved by the map by its own amount, put back together, softened.
  function buildFilter(id, o) {
    var svg = el("svg", { "class": "glass-filter", "aria-hidden": "true", focusable: "false" });
    var defs = el("defs", {});
    var filter = el("filter", { id: id, "color-interpolation-filters": "sRGB", x: "0%", y: "0%", width: "100%", height: "100%" });
    var image = el("feImage", { x: "0", y: "0", width: "100%", height: "100%", preserveAspectRatio: "none", result: "map" });
    filter.appendChild(image);
    var parts = [["red", o.redOffset, "1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0"],
      ["green", o.greenOffset, "0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0"],
      ["blue", o.blueOffset, "0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0"]];
    parts.forEach(function (p) {
      filter.appendChild(el("feDisplacementMap", { "in": "SourceGraphic", in2: "map", result: "d" + p[0],
        scale: String(o.scale + p[1]), xChannelSelector: o.xChannel, yChannelSelector: o.yChannel }));
      filter.appendChild(el("feColorMatrix", { "in": "d" + p[0], type: "matrix", values: p[2], result: p[0] }));
    });
    filter.appendChild(el("feBlend", { "in": "red", in2: "green", mode: "screen", result: "rg" }));
    filter.appendChild(el("feBlend", { "in": "rg", in2: "blue", mode: "screen", result: "output" }));
    filter.appendChild(el("feGaussianBlur", { "in": "output", stdDeviation: String(o.displace) }));
    defs.appendChild(filter);
    svg.appendChild(defs);
    return { svg: svg, image: image };
  }

  function num(v) { return Math.round(v * 100) / 100; }

  // GlassSurface's displacement map: red across, blue down, both fading out at the rounded edge, with a calm, blurred
  // grey middle that holds the view still.
  function mapFor(w, h, r, o) {
    var edge = Math.min(w, h) * (o.borderWidth * 0.5);
    w = num(w); h = num(h); r = num(r); edge = num(edge);
    var svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" xmlns="http://www.w3.org/2000/svg">' +
      '<defs><linearGradient id="r" x1="100%" y1="0%" x2="0%" y2="0%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="red"/></linearGradient>' +
      '<linearGradient id="b" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="blue"/></linearGradient>' +
      '<filter id="s" filterUnits="userSpaceOnUse" x="0" y="0" width="' + w + '" height="' + h + '" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="' + o.blur + '"/></filter></defs>' +
      '<rect width="' + w + '" height="' + h + '" fill="black"/>' +
      '<rect width="' + w + '" height="' + h + '" rx="' + r + '" fill="url(#r)"/>' +
      '<rect width="' + w + '" height="' + h + '" rx="' + r + '" fill="url(#b)" style="mix-blend-mode:' + o.blend + '"/>' +
      '<rect x="' + edge + '" y="' + edge + '" width="' + Math.max(0, num(w - edge * 2)) + '" height="' + Math.max(0, num(h - edge * 2)) +
      '" rx="' + r + '" fill="hsl(0 0% ' + o.brightness + '% / ' + o.opacity + ')" filter="url(#s)"/></svg>';
    return "data:image/svg+xml," + encodeURIComponent(svg);
  }

  function radiusOf(node, w, h) {
    var raw = getComputedStyle(node).borderTopLeftRadius || "0";
    var r = parseFloat(raw) || 0;
    if (/%/.test(raw)) r = (r / 100) * Math.min(w, h);
    return Math.max(0, Math.min(r, w / 2, h / 2));
  }

  function draw(s) {
    if (!s.parts) return;
    var w = s.node.offsetWidth;
    var h = s.node.offsetHeight;
    if (!w || !h) return;
    var r = radiusOf(s.node, w, h);
    var key = w + "x" + h + "@" + r;
    if (key === s.key) return;
    s.key = key;
    s.parts.image.setAttribute("href", mapFor(w, h, r, s.o));
  }

  var resizes = typeof ResizeObserver === "function" ? new ResizeObserver(function (entries) {
    entries.forEach(function (e) { var s = surfaces.get(e.target); if (s) draw(s); });
  }) : null;

  // Put the filter and the rim back if the surface's contents were replaced (the dashboard fills its menus anew).
  function mend(s) {
    var n = s.node;
    if (s.rim && s.rim.parentNode !== n) n.insertBefore(s.rim, n.firstChild);
    if (s.parts && s.parts.svg.parentNode !== n) n.insertBefore(s.parts.svg, n.firstChild);
  }

  function attach(node) {
    if (surfaces.has(node)) return;
    var o = optionsFor(node);
    if ((!refract && !o.fallback) || (!o.phone && phone())) return;
    var s = { node: node, o: o, key: "", parts: null, rim: null };
    var cs = getComputedStyle(node);
    if (cs.position === "static") node.style.position = "relative";
    if (refract) {
      var id = "ilmi-glass-" + (++count);
      s.parts = buildFilter(id, o);
      node.style.setProperty("--glass-filter", "url(#" + id + ")");
      node.style.setProperty("--glass-saturation", String(o.saturation));
    }
    // A scrolling surface would carry its rim away with its contents, so it has none.
    if (!/auto|scroll/.test(cs.overflowY)) {
      s.rim = document.createElement("span");
      s.rim.className = "glass-rim";
      s.rim.setAttribute("aria-hidden", "true");
    }
    surfaces.set(node, s);
    mend(s);
    node.classList.add("glass", refract ? "glass--svg" : "glass--fallback");
    draw(s);
    if (resizes) resizes.observe(node);
  }

  function detach(node) {
    var s = surfaces.get(node);
    if (!s) return;
    surfaces.delete(node);
    if (resizes) resizes.unobserve(node);
    if (s.parts) s.parts.svg.remove();
    if (s.rim) s.rim.remove();
    node.classList.remove("glass", "glass--svg", "glass--fallback");
    node.style.removeProperty("--glass-filter");
    node.style.removeProperty("--glass-saturation");
  }

  function scan() {
    surfaces.forEach(function (s, node) { if (!node.isConnected) detach(node); else mend(s); });
    if (quiet()) return;
    Array.prototype.forEach.call(document.querySelectorAll(SELECTOR), attach);
  }

  var pending = 0;
  function soon() {
    if (!pending) pending = requestAnimationFrame(function () { pending = 0; scan(); });
  }

  function start() {
    scan();
    new MutationObserver(soon).observe(document.body, { childList: true, subtree: true });
    // Asking for less transparency or more contrast takes the glass off at once, and putting it back returns it.
    ["(prefers-reduced-transparency: reduce)", "(prefers-contrast: more)"].forEach(function (q) {
      try {
        matchMedia(q).addEventListener("change", function () {
          if (quiet()) surfaces.forEach(function (s, node) { detach(node); }); else scan();
        });
      } catch (e) { /* older browsers: the choice at load stands */ }
    });
  }

  window.IlmiGlass = { attach: attach, detach: detach, refresh: scan };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
