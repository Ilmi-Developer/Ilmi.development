// The emailed sign-in code page: six animated code boxes over the real code field. Our own take on the
// CodeSlots component from React Bits (no React, no libraries). Each digit's fill springs out from the middle of
// its box as the digit rises in; a wrong code drains the boxes last to first in red; a right one merges them into
// one pastel green wash with a tick before the dashboard opens.
//
// The real <input name="code"> stays in the form, invisible on top of the boxes, so typing, pasting, phone keyboards
// and the "one-time-code" autofill all work as before. When the sixth digit lands the form is sent with fetch, so
// the boxes can show the answer first. A redirect means the code was right (the session cookie comes with it);
// anything else is the page the server would have shown, and its message is shown here.
//
// Another form can use the same boxes for a shorter code: it sets data-codeboxes, data-len, data-chars="alnum" (letters and numbers, shown in capitals) and data-json, which asks the
// server for a JSON answer ({next} when right, {error, gone} when not).
(function () {
  var form = document.querySelector("form[data-codeboxes]") || document.querySelector('form[action="/login/code"]');
  var input = form && form.querySelector("input[name=code]");
  if (!input || !window.fetch || !window.DOMParser) return;

  var N = +form.getAttribute("data-len") || 6, ALNUM = form.getAttribute("data-chars") === "alnum",
    JSONED = form.hasAttribute("data-json"), UNIT = ALNUM ? "characters" : "digits", RISE = 8, SETTLE = 0.3, BOUNCE = 0.2, CASCADE = 20, SINK_FADE = 0.6;
  var still = false;
  try { still = matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  // --- Building the boxes ----------------------------------------------------------------------------------------
  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  var holder = input.parentNode;              // the .fin span inside the "Sign-in code" label
  var root = el("span", "cslots");
  var row = el("span", "cs-row", root);
  row.setAttribute("data-status", "idle");
  var slots = [];
  for (var i = 0; i < N; i++) {
    var box = el("span", "cs-slot", row);
    box.setAttribute("aria-hidden", "true");
    slots.push({box: box, fill: el("span", "cs-fill", box), digit: el("span", "cs-digit", box), t: 0, d: 0, ch: ""});
  }
  if (N !== 6) row.style.gridTemplateColumns = "repeat(" + N + ",minmax(0,64px))";
  var wash = el("span", "cs-wash", row);
  wash.setAttribute("aria-hidden", "true");
  var check = el("span", "cs-check", wash);
  var svgNS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("focusable", "false");
  var tick = document.createElementNS(svgNS, "path");
  tick.setAttribute("d", "M5 12.5l4.5 4.5L19 7.5");
  svg.appendChild(tick);
  check.appendChild(svg);
  var caret = el("span", "cs-caret", row);
  caret.setAttribute("aria-hidden", "true");
  var caretLine = el("span", "cs-line", caret);
  var count = el("span", "sr-only", root);
  count.id = "cs-count";
  count.setAttribute("aria-live", "polite");

  var icon = holder.querySelector(".ic");
  if (icon) icon.remove();
  holder.classList.add("cs-on");
  holder.appendChild(root);
  row.insertBefore(input, row.firstChild);
  input.className = "cs-input";
  input.removeAttribute("maxlength");          // a pasted "123 456" is cleaned up here rather than cut short
  input.setAttribute("aria-describedby", "cs-count");
  input.value = "";
  input.focus({preventScroll: true});

  var button = form.querySelector("button");
  var buttonText = button ? button.textContent : "";

  // --- A small animation engine: springs and eases on plain numbers ------------------------------------------------
  // spring(duration, bounce) settles in about `duration` seconds and overshoots by about `bounce`, like Motion's.
  function spring(p, bounce) {
    var z = Math.max(0.05, 1 - bounce), w = 5 / z;
    if (z >= 1) return 1 - Math.exp(-w * p) * (1 + w * p);
    var wd = w * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w * p) * (Math.cos(wd * p) + (z * w / wd) * Math.sin(wd * p));
  }
  function easeOut(p) { return 1 - Math.pow(1 - p, 4); }
  var tweens = [], frame = 0;
  // Animates obj[key] to `to`. o: {dur (s), delay (ms), bounce} for a spring, or {ease: true} for an ease-out.
  function tween(obj, key, to, o) {
    tweens = tweens.filter(function (tw) { return !(tw.obj === obj && tw.key === key); });
    if (still) { obj[key] = to; paint(); return; }
    tweens.push({obj: obj, key: key, from: obj[key], to: to, dur: (o.dur || SETTLE) * 1000,
                 start: performance.now() + (o.delay || 0), bounce: o.bounce || 0, ease: o.ease});
    if (!frame) frame = requestAnimationFrame(step);
  }
  function step(now) {
    frame = 0;
    tweens = tweens.filter(function (tw) {
      var p = (now - tw.start) / tw.dur;
      if (p < 0) return true;
      if (p >= 1) { tw.obj[tw.key] = tw.to; return false; }
      var k = tw.ease ? easeOut(p) : spring(p, tw.bounce);
      tw.obj[tw.key] = tw.from + (tw.to - tw.from) * k;
      return true;
    });
    paint();
    if (tweens.length) frame = requestAnimationFrame(step);
  }
  function clamp01(v) { return Math.min(1, Math.max(0, v)); }
  var look = {open: 0, tick: 0};
  function paint() {
    var sink = row.offsetHeight * 0.5;
    slots.forEach(function (s) {
      s.fill.style.transform = "scale(" + Math.max(s.t, 0) + ")";
      s.digit.style.transform = "translateY(" + ((1 - s.t) * RISE + Math.max(s.d, 0) * sink) + "px)";
      s.digit.style.opacity = clamp01(s.t) * (1 - clamp01(s.d / SINK_FADE));
    });
    var r = parseFloat(getComputedStyle(slots[0].box).borderTopLeftRadius) || 12;
    wash.style.clipPath = "inset(0 " + (1 - clamp01(look.open)) * 50 + "% round " + r + "px)";
    wash.style.visibility = look.open > 0 ? "visible" : "hidden";
    check.style.transform = "translateY(" + (1 - look.tick) * 8 + "px) scale(" + (0.85 + 0.15 * Math.max(look.tick, 0)) + ")";
    check.style.opacity = clamp01(look.tick);
  }

  // --- State ---------------------------------------------------------------------------------------------------------
  var code = "", status = "idle", busy = false;
  function digitsOf(v) {
    return (ALNUM ? String(v || "").toUpperCase().replace(/[^A-Z0-9]/g, "") : String(v || "").replace(/\D/g, "")).slice(0, N);
  }
  function land(i, delay) {
    var s = slots[i];
    s.d = 0;
    if (s.t > 0) s.t = 0;
    tween(s, "t", 1, {delay: delay, bounce: BOUNCE});
  }
  function drain(i, delay) { tween(slots[i], "t", 0, {delay: delay, bounce: BOUNCE}); }
  function setStatus(next) {
    status = next;
    row.setAttribute("data-status", next);
    input.setAttribute("aria-invalid", next === "error" ? "true" : "false");
  }
  function placeCaret() {
    var a = Math.min(code.length, N - 1), b = slots[a].box;
    caret.style.transform = "translateX(" + (b.offsetLeft + b.offsetWidth / 2) + "px)";
    slots.forEach(function (s, j) { s.box.toggleAttribute("data-active", j === a && !busy && code.length < N); });
    // A new caret starts its blink on, so it shows the moment it arrives.
    caretLine.style.animation = "none";
    void caretLine.offsetWidth;
    caretLine.style.animation = "";
    row.toggleAttribute("data-caret", !busy && status !== "success" && code.length < N);
  }
  function announce() {
    count.textContent = status === "success" ? "Code accepted" : code.length + " of " + N + " " + UNIT + " entered";
  }

  // Shows the boxes holding `next`: new digits land in a cascade, removed ones drain last to first.
  function show(next) {
    var prev = code, landing = [], leaving = [];
    for (var i = 0; i < N; i++) {
      var a = prev.charAt(i), b = next.charAt(i);
      if (a === b) continue;
      if (b) { slots[i].digit.textContent = b; landing.push(i); } else leaving.push(i);
    }
    var gap = still ? 0 : CASCADE;
    landing.forEach(function (i, k) { land(i, k * gap); });
    leaving.reverse().forEach(function (i, k) { drain(i, k * gap); });
    code = next;
    placeCaret();
    announce();
  }

  function onInput() {
    if (busy) { input.value = code; return; }
    var next = digitsOf(input.value);
    input.value = next;
    if (status === "error" && next) setStatus("idle");
    var wasFull = code.length === N;
    show(next);
    if (next.length === N && !wasFull) {
      busy = true;
      placeCaret();
      setTimeout(send, still ? 0 : SETTLE * 1000 + (N - 1) * CASCADE);
    }
  }
  input.addEventListener("input", onInput);
  // Typing always goes on the end, so the boxes fill left to right as they look.
  function toEnd() { try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) {} }
  input.addEventListener("keydown", function (e) {
    if (/^(ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Home|End)$/.test(e.key)) e.preventDefault();
  });
  ["select", "click", "focus"].forEach(function (ev) { input.addEventListener(ev, toEnd); });
  input.addEventListener("focus", function () { row.setAttribute("data-focused", ""); });
  input.addEventListener("blur", function () { row.removeAttribute("data-focused"); });

  // --- Sending ---------------------------------------------------------------------------------------------------------
  function message(text) {
    var main = document.querySelector("main");
    var box = main && main.querySelector(":scope > .error");
    if (!text) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement("p");
      box.className = "error";
      box.setAttribute("role", "alert");
      main.insertBefore(box, main.querySelector(".card"));
    }
    box.textContent = text;
  }
  function checking(on) {
    if (!button) return;
    button.disabled = on;
    button.textContent = on ? "Checking…" : buttonText;
  }
  // Where the server is sending us: the form's "next" if it is a path on this site (the server checks it too).
  function nextPage() {
    var field = form.querySelector("input[name=next]"), url;
    try { url = new URL(field ? field.value || "/" : "/", location.href); } catch (e) { return "/"; }
    return url.origin === location.origin ? url.pathname + url.search + url.hash : "/";
  }
  function accepted(next) {
    setStatus("success");
    message("");
    if (button) button.textContent = form.getAttribute("data-done") || "Signed in";
    announce();
    placeCaret();
    tween(look, "open", 1, {dur: 0.3, ease: true});
    slots.forEach(function (s, k) { tween(s, "d", 1, {dur: 0.3, delay: 60 + k * 30}); });
    tween(look, "tick", 1, {dur: 0.35, delay: 280, bounce: BOUNCE});
    setTimeout(function () { location.replace(next || nextPage()); }, still ? 500 : 1100);
  }
  // A wrong code: the boxes drain last to first under red, then empty and wait for the next try.
  function rejected(text) {
    setStatus("error");
    message(text);
    var filled = [];
    for (var i = N - 1; i >= 0; i--) if (code.charAt(i)) filled.push(i);
    var gap = still ? 0 : CASCADE;
    filled.forEach(function (i, k) { drain(i, k * gap); });
    setTimeout(function () {
      code = "";
      input.value = "";
      busy = false;
      checking(false);
      placeCaret();
      announce();
      input.focus({preventScroll: true});
    }, still ? 300 : Math.max(0, filled.length - 1) * gap + SETTLE * 1000);
  }
  // A code that has stopped working: the boxes stay empty and the form is closed; the message says what to do.
  function closed(text) {
    rejected(text);
    setTimeout(function () {
      busy = true;
      input.disabled = true;
      if (button) button.disabled = true;
      row.removeAttribute("data-caret");
    }, still ? 320 : 600);
  }
  function sendJson() {
    checking(true);
    fetch(form.action, {method: "POST", body: new FormData(form), credentials: "same-origin",
                        headers: {"Accept": "application/json"}})
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (d.next) return accepted(d.next);
        var text = d.error || "That code isn't right. Try again.";
        return d.gone ? closed(text) : rejected(text);
      })
      .catch(function () { rejected("Ilmi couldn't be reached. Check your connection and try again."); });
  }
  function send() {
    if (JSONED) return sendJson();
    checking(true);
    fetch(form.action, {method: "POST", body: new FormData(form), credentials: "same-origin", redirect: "manual"})
      .then(function (r) {
        // Only a right code is answered with a redirect; the browser keeps the session cookie it carries.
        if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) return accepted();
        return r.text().then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          var again = doc.querySelector('form[action="' + form.getAttribute("action") + '"]');
          var said = doc.querySelector("main > .error");
          var text = said ? said.textContent.trim() : "That code isn't right. Check the latest email and try again.";
          if (again) {
            var token = again.querySelector("input[name=csrf]"), mine = form.querySelector("input[name=csrf]");
            if (token && mine) mine.value = token.value;
            return rejected(text);
          }
          // The code has stopped working (too many tries, or it expired): show the sign-in page the server sent.
          rejected(text);
          setTimeout(function () {
            var main = document.querySelector("main"), theirs = doc.querySelector("main");
            if (!main || !theirs) { location.replace("/login"); return; }
            document.title = doc.title;
            main.innerHTML = theirs.innerHTML;
            var first = main.querySelector("input:not([type=hidden])");
            if (first) first.focus({preventScroll: true});
          }, still ? 300 : 700);
        });
      })
      .catch(function () {
        rejected("Ilmi couldn't be reached. Check your connection and try again.");
      });
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (busy) return;
    if (code.length < N) {
      message(ALNUM ? "Type all " + N + " characters of the code." : "Type all 6 digits from the email.");
      setStatus("error");
      input.focus({preventScroll: true});
      return;
    }
    busy = true;
    placeCaret();
    send();
  });

  window.addEventListener("resize", placeCaret);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeCaret);
  paint();
  placeCaret();
  announce();
  // Under reduced motion the caret keeps still and the wash fades in (auth.css).
})();
