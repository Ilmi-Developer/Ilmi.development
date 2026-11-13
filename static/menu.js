// The side menu's groups (User management, Live Activity, Data handling, Links): each header folds its rows open and
// shut, and a branched line runs from the header down to every row, as in React Bits' BranchedMenu, rebuilt here
// without React. The page you are on lights its branch: a line in the accent colour draws itself from the header to that row.
// Groups remember being opened or shut in this browser; the group holding the page you are on always opens.
// Used by the dashboard (after railway-storage.js builds its admin groups) and the admin and account pages.
// Without this script every group simply stays open (tokens.css, .ngrp:not(.bm)).
(function () {
  "use strict";
  var KEY = "mysafar.menu", NS = "http://www.w3.org/2000/svg";
  var RM = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  // The trunk runs under the header's icon (12px padding plus half its 18px icon); branches end just before
  // the rows, which start at 34px (tokens.css, --bm-x).
  var TRUNK = 21, END = 30, RADIUS = 10;
  var saved = {};
  try { saved = JSON.parse(window.localStorage.getItem(KEY) || "{}") || {}; } catch (e) { saved = {}; }
  function keep() { try { window.localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private window */ } }

  function rows(g) { return g.querySelectorAll(".ngi>.nav"); }
  function current(g) {
    var r = rows(g);
    for (var i = 0; i < r.length; i++) if (r[i].getAttribute("aria-current") === "page") return r[i];
    return null;
  }
  function path(cls, d, k) {
    var p = document.createElementNS(NS, "path");
    p.setAttribute("class", cls); p.setAttribute("d", d);
    if (k !== undefined) p.setAttribute("data-k", k);
    return p;
  }

  // Lines are measured from the rows themselves, so a row that wraps or a font that loads late still meets its branch.
  function draw(g) {
    var tree = g.querySelector(".ngi"), r = rows(g);
    if (!tree || !r.length) return;
    var ys = [], h = tree.offsetHeight;
    for (var i = 0; i < r.length; i++) ys.push(Math.round(r[i].offsetTop + r[i].offsetHeight / 2));
    var sig = ys.join() + "|" + h, svg = tree.querySelector(":scope>.ngl");
    if (svg && svg.getAttribute("data-s") === sig) return;
    if (!svg) {
      svg = document.createElementNS(NS, "svg");
      svg.setAttribute("class", "ngl"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false");
      tree.insertBefore(svg, tree.firstChild);
    }
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute("width", END + 2); svg.setAttribute("height", h); svg.setAttribute("data-s", sig);
    var last = ys[ys.length - 1];
    svg.appendChild(path("b", "M " + TRUNK + " 0 V " + (last - RADIUS)));
    ys.forEach(function (y) {
      svg.appendChild(path("b", "M " + TRUNK + " " + (y - RADIUS) + " A " + RADIUS + " " + RADIUS + " 0 0 0 " + (TRUNK + RADIUS) + " " + y + " H " + END));
    });
    ys.forEach(function (y, k) {
      var p = path("r", "M " + TRUNK + " 0 V " + (y - RADIUS) + " A " + RADIUS + " " + RADIUS + " 0 0 0 " + (TRUNK + RADIUS) + " " + y + " H " + END, k);
      var len = (y - RADIUS) + Math.PI * RADIUS / 2 + (END - TRUNK - RADIUS) + 1;
      p.setAttribute("data-len", len.toFixed(1));
      p.style.strokeDasharray = len.toFixed(1) + " " + (len * 2).toFixed(1);  // a long gap, so no dot shows at the far end
      p.style.strokeDashoffset = len.toFixed(1);
      svg.appendChild(p);
    });
    reach(g, true);
  }

  // The lit branch follows the page you are on; it draws from the header down, or folds back up when you leave.
  function reach(g, jump) {
    var cur = current(g), r = rows(g);
    g.classList.toggle("has-cur", !!cur);
    var lines = g.querySelectorAll(".ngl .r");
    for (var i = 0; i < lines.length; i++) {
      var p = lines[i], on = r[+p.getAttribute("data-k")] === cur;
      if (jump) p.style.transition = "none";
      p.style.strokeDashoffset = on ? "0" : p.getAttribute("data-len");
      if (jump) { p.getBoundingClientRect(); p.style.transition = ""; }
    }
  }

  // One group open at a time, so the menu stays short enough for a laptop screen.
  function setOpen(g, open, byHand) {
    if (open) {
      var all = document.querySelectorAll(".rail .ngrp.bm.open");
      for (var j = 0; j < all.length; j++) if (all[j] !== g) setOpen(all[j], false, byHand);
    }
    g.classList.toggle("open", open);
    g.querySelector(".ngh").setAttribute("aria-expanded", open ? "true" : "false");
    // Folded rows take no clicks or focus and are hidden from screen readers.
    var body = g.querySelector(".ngb");
    body.inert = !open;
    if (open) body.removeAttribute("aria-hidden"); else body.setAttribute("aria-hidden", "true");
    if (byHand) { saved[g.getAttribute("data-g")] = open; keep(); }
  }

  // Scrolls the menu (never the page) just far enough to show an element.
  function show(el, smooth) {
    var nav = el.closest(".navs");
    if (!nav || nav.scrollHeight <= nav.clientHeight) return;
    var y = 0, e = el;
    while (e && e !== nav && nav.contains(e)) { y += e.offsetTop; e = e.offsetParent; }
    var to = null;
    if (y + el.offsetHeight > nav.scrollTop + nav.clientHeight) to = y + el.offsetHeight - nav.clientHeight + 8;
    else if (y < nav.scrollTop) to = y - 8;
    if (to === null) return;
    if (nav.scrollTo) nav.scrollTo({ top: to, behavior: smooth && !(RM && RM.matches) ? "smooth" : "auto" });
    else nav.scrollTop = to;
  }

  function setup(g) {
    if (g.classList.contains("bm")) return;
    var head = g.querySelector(".ngh"), tree = g.querySelector(".ngi");
    if (!head || !tree) return;
    var key = g.getAttribute("data-g"), cur = current(g);
    g.classList.add("bm");
    setOpen(g, !!cur || saved[key] === true);
    draw(g);
    // The lit branch draws itself once the page has settled, so it is seen arriving at the page you opened.
    var lines = g.querySelectorAll(".ngl .r");
    for (var i = 0; i < lines.length; i++) lines[i].style.strokeDashoffset = lines[i].getAttribute("data-len");
    g.getBoundingClientRect();
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        g.classList.add("ready");
        reach(g, !!(RM && RM.matches));
      });
    });
    head.addEventListener("click", function () {
      var open = !g.classList.contains("open");
      setOpen(g, open, true);
      // A group that opens below the fold of a long menu scrolls its rows into view as they unfold.
      if (open) setTimeout(function () { show(g.querySelector(".ngb"), true); }, RM && RM.matches ? 0 : 320);
    });
    // The dashboard changes the current page without loading a new one: the lit branch follows it, and a group
    // whose page is opened from elsewhere (a link, the address bar) unfolds to show it.
    if (window.MutationObserver) new MutationObserver(function () {
      if (current(g) && !g.classList.contains("open")) setOpen(g, true);
      reach(g);
    }).observe(tree, { subtree: true, attributes: true, attributeFilter: ["aria-current"] });
    if (window.ResizeObserver) new ResizeObserver(function () { draw(g); }).observe(tree);
  }

  var groups = document.querySelectorAll(".rail .ngrp"), here = null;
  // The group holding the page you are on opens first, so it is the one left open.
  for (var i = 0; i < groups.length; i++) if (current(groups[i])) here = groups[i];
  for (i = 0; i < groups.length; i++) if (groups[i] !== here) setup(groups[i]);
  if (here) { setup(here); show(current(here), false); }
})();

// Search, at the top of the menu. The dashboard has the search itself (page.html, with its own Ctrl+K); on the admin
// and account pages, Search and Ctrl+K (⌘K on a Mac) go to the dashboard with the search open (/#search), except
// while typing in a box.
(function () {
  "use strict";
  var mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent || "");
  var b = document.querySelector(".qkb kbd");
  if (b && mac) b.textContent = "⌘K";
  if (document.getElementById("qk") || !document.querySelector("a.qkb")) return;
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || (e.key !== "k" && e.key !== "K")) return;
    // Not from inside a box being typed in: leaving the page would lose what is in it.
    if (e.target.closest && e.target.closest("input,textarea,select,[contenteditable]")) return;
    e.preventDefault();
    window.location.href = "/#search";
  });
})();

// The developer's bar: on a narrow phone its views scroll sideways, so the one in use is brought into sight.
(function () {
  "use strict";
  var dv = document.querySelector('.devseg [aria-pressed="true"]');
  if (!dv) return;
  var g = dv.parentNode;
  if (g.scrollWidth <= g.clientWidth) return;
  g.scrollLeft = dv.getBoundingClientRect().left - g.getBoundingClientRect().left + g.scrollLeft - 8;
})();
