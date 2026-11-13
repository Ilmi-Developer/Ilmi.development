// The dock: after signing in, the menu is a row of icons floating at the foot of the screen, as the macOS dock is,
// in place of the old side menu. Each icon names itself in a warm tooltip (static/warm-tooltip.js) on hover, on
// keyboard focus or on a long press; once one label is showing, the next opens at once and glides across.
//
// It is built from the menu that is already on the page (src/page.html, templates/base.html and, on the dashboard,
// the admin items railway-storage.js adds), and it moves those same elements rather than copying them, so every
// page's own scripts keep working on them: which items show for which role, the page you are on (aria-current),
// clicks and the side-menu groups' branched lines (static/menu.js).
//  - Search and the main pages are icons.
//  - Administration, Links and your account are folders: an icon that opens a small olive menu above it, holding
//    the same groups the side menu had.
//  - The page you are on sits on a cream tile that glides from icon to icon (and, through the browser's view
//    transitions, from page to page), as the side menu's pill did.
//  - With a mouse, the icons swell as the pointer passes over them, and their neighbours a little less.
//  - On a phone, icons that don't fit go in a More folder.
// Styles are in src/tokens.css (".idock").
(function () {
  "use strict";
  var rail = document.querySelector(".shell .rail");
  if (!rail || !window.WarmTooltip || rail.querySelector(".idock")) return;
  var nav = rail.querySelector(".navs");
  if (!nav) return;
  var RM = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var FINE = window.matchMedia ? window.matchMedia("(hover: hover) and (pointer: fine)") : null;
  var SVG = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  // Administration: a portico, the school's office.
  var ADMIN_ICON = SVG + '<path d="M3 9.5 12 4l9 5.5"/><path d="M4.5 20h15"/><path d="M6 10.5V17M10 10.5V17M14 10.5V17M18 10.5V17"/><path d="M4 17.5h16"/></svg>';
  var MORE_ICON = SVG + '<circle cx="5.5" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="18.5" cy="12" r="1.5" fill="currentColor"/></svg>';

  // Ilmi's tooltip: a deep olive label with cream words (the theme's menu colour, a shade darker, so a chosen theme
  // carries through), hung above each icon. It pops without blur: Safari flashed a coloured line while a blurred
  // label scaled over the dock's frosted bar.
  var TIP = { side: "top", surfaceColor: "var(--wt-ilmi-surface)", inkColor: "var(--wt-ilmi-ink)", size: "md", radius: 9,
    gap: 12, arrow: true, popDuration: 160, popScale: 0.94, popBlur: 0, showFuse: false, longPress: 500 };
  var tips = WarmTooltip.group({ delay: 400, warmWindow: 300, travel: 320, lean: 0 });

  var dock = document.createElement("div");
  dock.className = "idock";
  var bar = document.createElement("div");
  bar.className = "idk-bar";
  var sel = document.createElement("span");
  sel.className = "idk-sel";
  sel.setAttribute("aria-hidden", "true");
  bar.appendChild(sel);
  dock.appendChild(bar);
  var pops = document.createElement("div");
  pops.className = "idk-pops";

  function textOf(el) {
    var c = el.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll(".ext,.sr-only,kbd,.ic,svg,.navsel,.ngl"), function (x) { x.remove(); });
    return c.textContent.replace(/\s+/g, " ").trim();
  }

  var slots = [];
  // A slot holds one icon: the tooltip's trigger, and the box that swells under the pointer.
  function slot(el, label, opts) {
    var s = document.createElement("span");
    s.className = "idk-i";
    s.appendChild(el);
    var tip = tips.attach(s, Object.assign({}, TIP, { target: el, anchor: el, content: label }, opts || {}));
    var rec = { el: s, item: el, tip: tip, size: 0, want: 0, folder: null, more: false };
    s._dock = rec;
    slots.push(rec);
    return rec;
  }
  function sep() {
    var s = document.createElement("span");
    s.className = "idk-sep";
    s.setAttribute("aria-hidden", "true");
    return s;
  }

  // A folder: an icon button that opens an olive menu above it.
  var folders = [];
  function folder(key, label, iconHtml, fill) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "nav idk-f";
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-label", label);
    if (iconHtml instanceof Node) b.appendChild(iconHtml); else b.innerHTML = iconHtml;
    var pop = document.createElement("div");
    pop.className = "idk-pop idk-pop-" + key;
    pop.id = "idk-pop-" + key;
    pop.hidden = true;
    pop.setAttribute("role", "group");
    pop.setAttribute("aria-label", label);
    b.setAttribute("aria-controls", pop.id);
    var arrow = document.createElement("span");
    arrow.className = "idk-arrow";
    arrow.setAttribute("aria-hidden", "true");
    var body = document.createElement("div");
    body.className = "idk-body";
    pop.appendChild(body);
    pop.appendChild(arrow);
    fill(body);
    pops.appendChild(pop);
    var rec = slot(b, label);
    rec.folder = { key: key, pop: pop, body: body, arrow: arrow, button: b };
    folders.push(rec);
    b.addEventListener("click", function (e) { toggle(rec, e.detail === 0); });
    return rec;
  }

  // ---------- Build ----------
  var main = [], tail = [];
  var search = rail.querySelector(".qkb"), searchSlot = null;
  if (search) {
    var kbd = search.querySelector("kbd");
    searchSlot = slot(search, "Search", { shortcut: function () { return kbd ? kbd.textContent : ""; } });
    main.push(searchSlot);
  }
  // The dashboard's class shortcuts from its old phone dock (Quran and Memo for a teacher, or while a class is
  // open), placed after Classes.
  var oldDock = rail.querySelector(".dock");
  var classKeys = oldDock ? Array.prototype.filter.call(oldDock.querySelectorAll(".dk-quran,.dk-memo"), function (d) { return d.tagName === "BUTTON"; }) : [];

  var admin = null, links = null;
  function adminFolder(nodes) {
    return folder("admin", "Administration", ADMIN_ICON, function (body) { nodes.forEach(function (n) { body.appendChild(n); }); });
  }
  var kids = Array.prototype.slice.call(nav.children);
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (k.classList.contains("navi")) continue;
    // The class's Settings (shown while a class is open) goes with the class pages.
    if (k.classList.contains("dkx")) { classKeys.push(k); continue; }
    if (k.id === "adminNav") {
      if (k.querySelector(".navh")) admin = adminFolder([k]);
      else Array.prototype.slice.call(k.children).forEach(function (n) { if (n.classList.contains("nav")) main.push(slot(n, textOf(n))); });
      continue;
    }
    if (k.classList.contains("navh")) {
      // The admin pages: the heading and everything after it, up to Links.
      var run = [];
      for (; i < kids.length && !kids[i].classList.contains("j2jb"); i++) run.push(kids[i]);
      i--;
      admin = adminFolder(run);
      continue;
    }
    if (k.classList.contains("ngrp")) {
      var head = k.querySelector(".ngh");
      var ic = head && head.querySelector(".ic");
      var label = head ? textOf(head) : "Links";
      links = folder(k.getAttribute("data-g") || "links", label, ic ? ic.cloneNode(true) : MORE_ICON, function (body) { body.appendChild(k); });
      continue;
    }
    if (k.classList.contains("nav")) {
      main.push(slot(k, textOf(k)));
      if (k.id === "nL" || (k.id === "nM" && !document.getElementById("nL"))) {
        classKeys.forEach(function (d) {
          d.classList.add("nav");
          main.push(slot(d, d.classList.contains("dkx") ? "Class settings" : textOf(d.querySelector("span:last-child") || d)));
        });
        classKeys = [];
      }
    }
  }
  if (admin) tail.push(admin);
  if (links) tail.push(links);
  var me = rail.querySelector(".me");
  var mine = null;
  if (me) {
    var who = me.querySelector(".who b");
    var av = me.querySelector(".av");
    var face = av ? av.cloneNode(true) : document.createElement("span");
    face.classList.add("idk-av");
    mine = folder("me", who ? who.textContent : "Account", face, function (body) { body.appendChild(me); });
    mine.item.setAttribute("aria-label", (who ? who.textContent + ", " : "") + "account");
  }
  // The Ilmi wordmark sits in the dock just before your picture (with the site title under it, if one is set),
  // and still takes you to the overview. On a phone, where the dock has no room, it stays at the top.
  var brandEl = rail.querySelector(".brand"), brand = null;
  if (brandEl) {
    var org = brandEl.querySelector("small:not([hidden])");
    brand = slot(brandEl, org && org.textContent.trim() ? org.textContent.trim() : "Overview");
    brand.fixed = true;
    brand.el.classList.add("idk-brand");
  }
  function wide() { return (document.documentElement.clientWidth || window.innerWidth) >= 960; }
  // The admin pages mark the page you are on with a pill of their own; the dock's tile takes over.
  Array.prototype.forEach.call(rail.querySelectorAll(".navsel"), function (n) { n.remove(); });
  pops.addEventListener("click", function (e) {
    // Choosing a page closes the menu; opening a group inside it doesn't.
    var a = e.target.closest("a.nav,button.nav:not(.ngh),.linkbtn");
    if (a) closeAll(false);
  });

  // The More folder, for phones.
  var more = folder("more", "More", MORE_ICON, function () {});
  more.el.classList.add("idk-more");
  var moreList = document.createElement("div");
  moreList.className = "idk-morelist";
  var sepA = sep(), sepB = sep();
  folders.forEach(function (f) {
    var sec = document.createElement("div");
    sec.className = "idk-sec";
    f.folder.section = sec;
  });

  function visible(r) { return r === more || !r.item.hidden; }
  function isShown(s) { return !s.hidden && (!s._dock || visible(s._dock)); }
  // Which icons stay in the bar when only `c` fit: Links goes into More first, then the main pages from the end
  // (never Search), then Administration. Your account always stays.
  function plan(c) {
    var M = main.filter(visible), T = tail.slice(), Me = (brand && wide() ? [brand] : []).concat(mine ? [mine] : []);
    var outMain = [], outFolders = [], top = false;
    function count() { return M.length + T.length + Me.length; }
    // When space is short, Search moves up beside the wordmark, as on a phone's top bar.
    if (count() > c && searchSlot && M[0] === searchSlot) { M.shift(); top = true; }
    if (count() > c) {
      // What gives way first: Links, the other pages from the end, Administration, then Overview, then Classes,
      // and last the open class's own pages (Quran, Memo), which a phone needs most while recording.
      function rank(r) {
        if (r.item.classList.contains("dk")) return 4;
        if (r.item.id === "nL" || r.item.getAttribute("href") === "/#classes") return 3;
        if (r.item.id === "nM" || r.item.getAttribute("href") === "/") return 2;
        return 0;
      }
      var cands = [];
      if (links) cands.push(links);
      M.slice().reverse().filter(function (r) { return rank(r) === 0; }).forEach(function (r) { cands.push(r); });
      if (admin) cands.push(admin);
      [2, 3, 4].forEach(function (k) { M.slice().reverse().filter(function (r) { return rank(r) === k; }).forEach(function (r) { cands.push(r); }); });
      while (count() + 1 > c && cands.length) {
        var x = cands.shift();
        if (x.folder) { T.splice(T.indexOf(x), 1); outFolders.push(x); }
        else { M.splice(M.indexOf(x), 1); outMain.push(x); }
      }
    }
    outFolders.sort(function (a, b) { return tail.indexOf(a) - tail.indexOf(b); });
    outMain.sort(function (a, b) { return main.indexOf(a) - main.indexOf(b); });
    return { M: M, T: T, Me: Me, outMain: outMain, outFolders: outFolders, top: top };
  }
  // Puts children in order, moving only those out of place, so a focused icon keeps its focus.
  function reorder(parent, want) {
    want.forEach(function (el, n) {
      if (parent.children[n] !== el) parent.insertBefore(el, parent.children[n] || null);
    });
  }
  var sig = "";
  function layout(c) {
    var P = plan(c), useMore = P.outMain.length + P.outFolders.length > 0;
    var key = c + "|" + wide() + "|" + slots.map(function (r) { return visible(r) ? 1 : 0; }).join("");
    if (key === sig) return false;
    sig = key;
    var want = [sel], seps = [sepA, sepB];
    [P.M, P.T.concat(useMore ? [more] : []), P.Me].forEach(function (g) {
      if (!g.length) return;
      if (want.length > 1) { var s = seps.shift(); s.hidden = false; want.push(s); }
      g.forEach(function (r) { r.el.hidden = false; want.push(r.el); });
    });
    seps.forEach(function (s) { s.hidden = true; want.push(s); });
    if (!useMore) { more.el.hidden = true; want.push(more.el); }
    main.forEach(function (r) { if (!visible(r) && P.outMain.indexOf(r) < 0) want.push(r.el); });
    tail.forEach(function (r) {
      var out = P.outFolders.indexOf(r) >= 0, f = r.folder;
      if (out) { r.el.hidden = true; want.push(r.el); }
      // A folder that moves into More takes its menu with it, as a section; it comes back when there is room.
      var from = out ? f.body : f.section, to = out ? f.section : f.body;
      while (from.firstChild) to.appendChild(from.firstChild);
    });
    if (brand) {
      var up = !wide();
      brand.top = up;
      brand.el.classList.toggle("idk-top", up);
      brand.tip.update({ disabled: up });
      if (up) { if (rail.firstChild !== brand.el) rail.insertBefore(brand.el, rail.firstChild); }
    }
    if (searchSlot) {
      searchSlot.top = P.top;
      searchSlot.el.classList.toggle("idk-top", P.top);
      searchSlot.tip.update({ side: P.top ? "bottom" : "top" });
      if (P.top) rail.insertBefore(searchSlot.el, dock); else if (want.indexOf(searchSlot.el) < 0) want.push(searchSlot.el);
    }
    reorder(bar, want);
    slots.forEach(function (r) {
      var was = r.more;
      r.more = P.outMain.indexOf(r) >= 0;
      r.el.classList.toggle("idk-in-more", r.more);
      // A row in More shows its name already.
      if (was !== r.more) r.tip.update({ disabled: r.more });
    });
    reorder(moreList, P.outMain.map(function (r) { return r.el; }));
    var body = [];
    if (P.outMain.length) body.push(moreList);
    P.outFolders.forEach(function (r) { body.push(r.folder.section); });
    reorder(more.folder.body, body);
    while (more.folder.body.children.length > body.length) more.folder.body.removeChild(more.folder.body.lastChild);
    sizes(true);
    return true;
  }

  // How many icons fit across the screen.
  var BASE = 44, GAP = 6, PAD = 8;
  function capacity() {
    // The screen, less the dock's margins, padding and two separators.
    var w = (document.documentElement.clientWidth || window.innerWidth) - 24 - PAD * 2 - 2 * (1 + 4 + GAP);
    if (wide() && brand) w -= brand.el.offsetWidth || 110;
    return Math.max(4, Math.floor((w + GAP) / (BASE + GAP)));
  }
  function refit() {
    if (openRec && layoutWouldChange()) closeAll(false);
    layout(capacity());
    mark();
  }
  function layoutWouldChange() {
    var key = capacity() + "|" + wide() + "|" + slots.map(function (r) { return visible(r) ? 1 : 0; }).join("");
    return key !== sig;
  }

  // ---------- The page you are on ----------
  function currentSlot() {
    // A class's own page (Quran, Memo) is more exact than Classes, which stays marked while a class is open.
    var found = null;
    for (var n = 0; n < slots.length; n++) {
      var r = slots[n];
      if (r.folder || r.more || !visible(r)) continue;
      if (r.item.getAttribute("aria-current") === "page" || r.item.getAttribute("aria-current") === "true") {
        if (!found || r.item.classList.contains("dk")) found = r;
      }
    }
    if (found) return found;
    for (n = 0; n < folders.length; n++) {
      var f = folders[n];
      if (f === more || f.el.hidden) continue;
      if (f.folder.body.querySelector('[aria-current="page"]:not([hidden])')) return f;
    }
    if (!more.el.hidden && more.folder.body.querySelector('[aria-current="page"]:not([hidden])')) return more;
    return null;
  }
  var on = null, selShown = false;
  // Where the tile was when the last page was left, so it can glide over from there on the next.
  var KEY = "ilmi.dock.sel", from = null;
  try { from = JSON.parse(window.sessionStorage.getItem(KEY) || "null"); window.sessionStorage.removeItem(KEY); } catch (e) { from = null; }
  if (from && !(Date.now() - from.t < 4000)) from = null;
  function remember() {
    if (!selShown || !on) return;
    try { window.sessionStorage.setItem(KEY, JSON.stringify({ x: on.el.offsetLeft, s: on.size, t: Date.now() })); } catch (e) { /* private window */ }
  }
  window.addEventListener("pagehide", remember);
  function mark() {
    var r = currentSlot();
    slots.forEach(function (s) { s.el.classList.toggle("is-on", s === r); });
    folders.forEach(function (f) { f.el.classList.toggle("has-on", f === r); });
    var jump = !selShown || !on;
    on = r;
    if (jump && from && on && !(RM && RM.matches)) {
      sel.classList.add("jump", "show");
      sel.style.transform = "translate(" + from.x + "px, " + (BASE_TOP() - from.s) + "px)";
      sel.style.width = sel.style.height = from.s + "px";
      sel.getBoundingClientRect();
      sel.classList.remove("jump");
      from = null;
      selShown = true;
      requestAnimationFrame(function () { placeSel(false); });
      return;
    }
    placeSel(jump);
  }
  function placeSel(jump) {
    if (!on || on.el.parentNode !== bar || !isShown(on.el)) { sel.classList.remove("show"); selShown = false; return; }
    var x = on.el.offsetLeft + (on.el.offsetWidth - on.size) / 2, s = on.size;
    if (jump) sel.classList.add("jump");
    sel.style.transform = "translate(" + x + "px, " + (BASE_TOP() - s) + "px)";
    sel.style.width = sel.style.height = s + "px";
    sel.classList.add("show");
    selShown = true;
    if (jump) { sel.getBoundingClientRect(); sel.classList.remove("jump"); }
  }
  function BASE_TOP() { return PAD + BASE; }

  // ---------- Magnification ----------
  var MAX = 1.5, RANGE = 150, px = null, raf = 0;
  function magnify() { return FINE && FINE.matches && !(RM && RM.matches); }
  function sizes(jump) {
    slots.forEach(function (r) {
      if (r.more || r.top || r.fixed) { r.want = BASE; r.size = BASE; return; }
      var c = r.el;
      var want = BASE;
      if (px !== null && magnify() && c.parentNode === bar) {
        var rect = c.getBoundingClientRect(), mid = rect.left + rect.width / 2, d = Math.abs(px - mid);
        if (d < RANGE) want = BASE + BASE * (MAX - 1) * (Math.cos(Math.PI * d / RANGE) + 1) / 2;
      }
      r.want = want;
      if (jump || !r.size) r.size = want;
    });
    apply();
    if (!jump) kick();
  }
  function apply() {
    slots.forEach(function (r) { if (!r.more) r.el.style.setProperty("--sz", r.size.toFixed(2) + "px"); else r.el.style.removeProperty("--sz"); });
  }
  function kick() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame() {
    raf = 0;
    var busy = false;
    slots.forEach(function (r) {
      var d = r.want - r.size;
      if (Math.abs(d) > 0.1) { r.size += d * 0.28; busy = true; } else r.size = r.want;
    });
    apply();
    if (px !== null) sizes2();
    placeSel(false);
    tips.follow();
    if (openRec) position(openRec);
    if (busy) kick();
  }
  // Retarget from the live layout each frame, as the dock widens under the pointer.
  function sizes2() {
    slots.forEach(function (r) {
      if (r.more || r.top || r.fixed || r.el.parentNode !== bar) return;
      var rect = r.el.getBoundingClientRect(), mid = rect.left + rect.width / 2, d = Math.abs(px - mid);
      r.want = d < RANGE ? BASE + BASE * (MAX - 1) * (Math.cos(Math.PI * d / RANGE) + 1) / 2 : BASE;
    });
  }
  bar.addEventListener("pointermove", function (e) {
    if (e.pointerType !== "mouse" || !magnify()) return;
    px = e.clientX;
    sizes(false);
  });
  bar.addEventListener("pointerleave", function () {
    px = null;
    slots.forEach(function (r) { r.want = BASE; });
    kick();
  });

  // ---------- Folders ----------
  var openRec = null;
  function position(rec) {
    var f = rec.folder, r = rec.el.getBoundingClientRect(), pop = f.pop;
    var w = pop.offsetWidth, vw = document.documentElement.clientWidth;
    var mid = r.left + r.width / 2;
    var left = Math.min(Math.max(12, mid - w / 2), vw - 12 - w);
    var barTop = bar.getBoundingClientRect().top;
    pop.style.left = left + "px";
    pop.style.bottom = (window.innerHeight - barTop + 12) + "px";
    f.arrow.style.left = Math.min(Math.max(18, mid - left), w - 18) + "px";
  }
  function animateIn(pop) {
    if (RM && RM.matches || !pop.animate) return;
    pop.animate([{ opacity: 0, transform: "translateY(4px) scale(.94)" },
      { opacity: 1, transform: "none" }], { duration: 180, easing: "cubic-bezier(.23,1,.32,1)" });
  }
  function toggle(rec, byKeyboard) {
    if (openRec === rec) { closeAll(true); return; }
    closeAll(false);
    var f = rec.folder;
    openRec = rec;
    tips.reset();
    rec.tip.update({ disabled: true });
    f.button.setAttribute("aria-expanded", "true");
    rec.el.classList.add("open");
    f.pop.hidden = false;
    // Links opens with its rows showing.
    var shut = f.body.querySelector(".ngrp.j2jb.bm:not(.open)>.ngh");
    if (shut) shut.click();
    position(rec);
    animateIn(f.pop);
    if (byKeyboard) {
      var first = f.body.querySelector("a[href],button:not([disabled]):not([hidden])");
      if (first) first.focus();
    }
  }
  function closeAll(refocus) {
    var rec = openRec;
    if (!rec) return;
    openRec = null;
    var f = rec.folder;
    f.button.setAttribute("aria-expanded", "false");
    rec.el.classList.remove("open");
    rec.tip.update({ disabled: false });
    if (RM && RM.matches || !f.pop.animate) f.pop.hidden = true;
    else {
      var a = f.pop.animate([{ opacity: 1, transform: "none" },
        { opacity: 0, transform: "translateY(4px) scale(.96)" }], { duration: 130, easing: "cubic-bezier(.23,1,.32,1)" });
      a.onfinish = function () { if (openRec !== rec) f.pop.hidden = true; };
    }
    if (refocus) f.button.focus();
  }
  document.addEventListener("pointerdown", function (e) {
    if (!openRec) return;
    if (openRec.el.contains(e.target) || openRec.folder.pop.contains(e.target)) return;
    closeAll(false);
  }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && openRec) { e.stopPropagation(); closeAll(true); }
  }, true);
  pops.addEventListener("focusout", function (e) {
    if (!openRec) return;
    var to = e.relatedTarget;
    if (to && !openRec.folder.pop.contains(to) && !openRec.el.contains(to)) closeAll(false);
  });
  // Left and right arrows move along the dock, as in a toolbar.
  bar.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var items = Array.prototype.filter.call(bar.querySelectorAll(".idk-i"), function (s) { return !s.hidden && isShown(s); })
      .map(function (s) { return s.firstElementChild; });
    var n = items.indexOf(document.activeElement);
    if (n < 0) return;
    e.preventDefault();
    var next = items[(n + (e.key === "ArrowRight" ? 1 : -1) + items.length) % items.length];
    if (next) next.focus();
  });

  // ---------- Put it on the page ----------
  rail.appendChild(dock);
  rail.appendChild(pops);
  document.documentElement.classList.add("has-dock");
  refit();
  var rt = 0;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () { refit(); sizes(true); placeSel(true); if (openRec) position(openRec); }, 60);
  });
  // The page's own scripts show and hide items and move the page you are on; follow them.
  var mt = 0;
  var watch = new MutationObserver(function () {
    clearTimeout(mt);
    mt = setTimeout(refit, 16);
  });
  watch.observe(rail, { subtree: true, attributes: true, attributeFilter: ["hidden", "aria-current"] });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { placeSel(true); });
})();
