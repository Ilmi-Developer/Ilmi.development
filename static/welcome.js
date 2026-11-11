// Ilmi's start page (templates/welcome.html). Runs in the page's head, so the page is marked .js before anything is
// drawn: parts further down then wait out of sight and arrive as they scroll into view (static/welcome.css), and the
// preview's figures count up when it arrives.
//
// With motion allowed the page is also marked .motion and the scroll drives it: a gentle, weighted glide for mouse
// wheels (touch and keyboard scroll natively), the hero easing back as the hadith band opens over it, a gallery that
// runs sideways while it is pinned, a statement read in word by word, a pinned preview that steps through three parts
// of the overview, and parallax on the values and the closing. Each part gets its progress as CSS variables (--in,
// --t, --p) and welcome.css does the rest. Without this script, or under reduced motion, nothing is pinned or moved by
// the scroll and every part rests in its finished state.
(function () {
  var root = document.documentElement;
  root.classList.add("js");
  var still = false, fine = false;
  try {
    still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  } catch (e) {}
  if (!still) root.classList.add("motion");

  function count(box) {
    [].forEach.call(box.querySelectorAll("[data-n]"), function (b) {
      var n = +b.getAttribute("data-n"), t0 = null, d = 1500;
      b.textContent = "0";
      function step(t) {
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - k, 3);
        b.textContent = String(Math.round(n * e));
        if (k < 1) requestAnimationFrame(step);
      }
      setTimeout(function () { requestAnimationFrame(step); }, 500);
    });
  }

  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  // Progress is written only on the elements that use it, and only when it changes. The properties are registered
  // in welcome.css as not inherited, so a scroll restyles those few elements and never the page under them.
  function set(el, name, v) {
    var s = v.toFixed(4), k = "_" + name;
    if (el[k] === s) return;
    el[k] = s;
    el.style.setProperty(name, s);
  }
  function setAll(list, name, v) { for (var i = 0; i < list.length; i++) set(list[i], name, v); }
  function list(root, sel) { return root ? [].slice.call(root.querySelectorAll(sel)) : []; }
  var USES = ".bback,.band-in,.f-track,li .art,.f-bar i,.i-splash,.mock-3d,.vcard,.c-big,.art-skyline,.art-grass";

  // Wraps each word of [data-words] in a span that knows its place (--i) and the count (--n), keeping <em> and the
  // like as they are. Screen readers still read the sentence whole.
  function words(el) {
    var n = 0;
    (function walk(node) {
      [].slice.call(node.childNodes).forEach(function (c) {
        if (c.nodeType === 1) { walk(c); return; }
        if (c.nodeType !== 3) return;
        var frag = document.createDocumentFragment();
        c.textContent.split(/(\s+)/).forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
          var w = document.createElement("span");
          w.className = "w";
          w.textContent = part;
          w.style.setProperty("--i", n++);
          frag.appendChild(w);
        });
        node.replaceChild(frag, c);
      });
    })(el);
    el.style.setProperty("--n", n);
  }

  function start() {
    var parts = [].slice.call(document.querySelectorAll(".rv"));
    if (still || !("IntersectionObserver" in window)) {
      parts.forEach(function (p) { p.classList.add("in"); });
    } else {
      var seen = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          en.target.classList.add("in");
          seen.unobserve(en.target);
          if (en.target.classList.contains("mock")) count(en.target);
        });
      }, {rootMargin: "0px 0px -10% 0px", threshold: 0.12});
      parts.forEach(function (p) { seen.observe(p); });
    }
    if (still) return;

    [].forEach.call(document.querySelectorAll("[data-words]"), words);
    var hero = document.querySelector(".hero"), nav = document.querySelector(".wnav");
    var feats = document.querySelector(".feats"), track = feats && feats.querySelector(".f-track");
    var bar = document.querySelector(".sprog i"), heroParts = list(hero, ".hero-art,.hero-text,.cue");
    var scrubs = list(document, "[data-scrub],[data-pin]").map(function (el) {
      return {el: el, parts: [el].concat(list(el, USES)), words: list(el, "[data-words]"), pin: el.hasAttribute("data-pin"),
        steps: +el.getAttribute("data-steps") || 0, top: 0, h: 0};
    });
    var vh = 0, lastY = -1, ticking = false, heroH = 1, docMax = 1;

    // The gallery is as tall as the distance its track has to travel sideways, so a turn of the wheel moves it as
    // far as it would move the page. Where it is not pinned (narrow, short screens; welcome.css) it keeps its height.
    function measure() {
      vh = window.innerHeight;
      if (track) {
        feats.style.height = "";
        var pinned = getComputedStyle(feats.querySelector(".pin-view")).position === "sticky";
        var dx = pinned ? Math.max(0, track.scrollWidth - root.clientWidth) : 0;
        feats.style.setProperty("--dx", dx);
        if (pinned) feats.style.height = (vh + dx) + "px";
      }
      // Where each part sits on the page is read here, once, so scrolling itself never asks for layout.
      var y0 = window.scrollY;
      scrubs.forEach(function (s) { var r = s.el.getBoundingClientRect(); s.top = r.top + y0; s.h = r.height; });
      heroH = hero ? hero.offsetHeight : 1;
      docMax = Math.max(1, root.scrollHeight - vh);
      update(true);
    }

    function update(force) {
      ticking = false;
      var y = window.scrollY;
      if (y === lastY && !force) return;
      if (bar) set(bar, "--doc", clamp(y / docMax));
      if (nav) {
        root.classList.toggle("scrolled", y > 24);
        if (y > vh * 0.9 && y > lastY + 2) root.classList.add("nav-away");
        else if (y < lastY - 2 || y <= vh * 0.9) root.classList.remove("nav-away");
      }
      setAll(heroParts, "--h", clamp(y / Math.max(1, heroH)));
      scrubs.forEach(function (s) {
        var top = s.top - y, bottom = top + s.h;
        if (bottom < -vh || top > vh * 2) return;
        var a = clamp((vh - top) / vh);
        setAll(s.parts, "--in", a);
        setAll(s.parts, "--t", clamp((vh - top) / (vh + s.h)));
        setAll(s.words, "--iw", a);
        if (s.pin) {
          var p = clamp(-top / Math.max(1, s.h - vh));
          setAll(s.parts, "--p", p);
          setAll(s.words, "--pw", p);
          if (s.steps) {
            var step = String(Math.min(s.steps - 1, Math.floor(p * s.steps)));
            if (s.el.getAttribute("data-step") !== step) s.el.setAttribute("data-step", step);
          }
        }
      });
      lastY = y;
    }
    function queue() { if (!ticking) { ticking = true; requestAnimationFrame(function () { update(false); }); } }

    addEventListener("scroll", queue, {passive: true});
    addEventListener("resize", measure);
    addEventListener("load", measure);
    if ("ResizeObserver" in window) new ResizeObserver(function () { measure(); }).observe(document.body);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
    measure();

    // A weighted glide for mouse wheels: each turn sets where the page is heading and the page eases towards it.
    // Pinch zoom, sideways scrolling, keyboards, scroll bars and touch all scroll as the browser does.
    var goal = 0, at = 0, gliding = false, last = 0, tween = null;
    function maxY() { return Math.max(0, root.scrollHeight - window.innerHeight); }
    function glide(t) {
      var dt = last ? Math.min(64, t - last) : 16.7;
      last = t;
      at += (goal - at) * (1 - Math.pow(1 - 0.1, dt / 16.7));
      if (Math.abs(goal - at) < 0.5) { at = goal; gliding = false; }
      window.scrollTo(0, at);
      if (gliding) requestAnimationFrame(glide);
    }
    if (fine) {
      addEventListener("wheel", function (e) {
        if (e.ctrlKey || e.defaultPrevented || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
        e.preventDefault();
        tween = null;
        if (!gliding) { goal = at = window.scrollY; }
        var d = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1);
        goal = Math.max(0, Math.min(maxY(), goal + d));
        if (!gliding) { gliding = true; last = 0; requestAnimationFrame(glide); }
      }, {passive: false});
    }

    // The page's own links (#what, #inside, #values) travel there smoothly too, then move focus to the section.
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a || a.classList.contains("skip") || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      var to = document.getElementById(a.getAttribute("href").slice(1));
      if (!to) return;
      e.preventDefault();
      var from = window.scrollY, dest = Math.min(maxY(), to.getBoundingClientRect().top + from), t0 = null;
      var dur = Math.min(1600, 600 + Math.abs(dest - from) * 0.35), mine = {};
      gliding = false; tween = mine;
      requestAnimationFrame(function step(t) {
        if (tween !== mine) return;
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / dur), ease = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
        window.scrollTo(0, from + (dest - from) * ease);
        if (k < 1) requestAnimationFrame(step);
        else {
          tween = null;
          if (!to.hasAttribute("tabindex")) to.setAttribute("tabindex", "-1");
          to.focus({preventScroll: true});
          if (history.replaceState) history.replaceState(null, "", "#" + to.id);
        }
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
