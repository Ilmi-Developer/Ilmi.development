// WarmTooltip: React Bits' <WarmTooltip /> and <WarmTooltipGroup /> (JavaScript + CSS variant, built on motion),
// rebuilt here without React or motion, because Ilmi's pages are plain HTML and our CSP allows only our own scripts.
// The behaviour follows the original line by line:
//  - A group shares one label. The first one opens "cold", after `delay` (400ms), and pops in from `popScale` through
//    `popBlur` over `popDuration`. While the group is warm (a label is open, or one closed less than `warmWindow` ago),
//    the next trigger opens at once and the label glides to it on a spring taking `travel` ms, its words swapping
//    with a short sideways slide in the direction of travel.
//  - Leaving a trigger closes after an 80ms grace; the exit takes 0.8 of the pop. Keyboard focus opens instantly,
//    Escape and blur close instantly, a mouse press closes, and on touch a long press (500ms) opens it and swallows
//    the click that follows. A press anywhere else closes it. The label follows its trigger on scroll and resize.
//  - Reduced motion: no pop, blur, lean or glide; the label simply appears.
// Used by the dock (static/dock.js). Styles are in src/tokens.css (".warm-tooltip").
(function () {
  "use strict";
  var EASE_OUT = [0.23, 1, 0.32, 1];
  var FULL_LEAN_SPEED = 1200;
  var SIGN = { top: 1, bottom: -1, left: -1, right: 1 };
  var ORIGIN = { top: "center bottom", bottom: "center top", left: "right center", right: "left center" };
  var SIZES = { sm: { font: 11.5, px: 8, py: 5 }, md: { font: 12.5, px: 10, py: 6 }, lg: { font: 13.5, px: 12, py: 7 } };
  var MARGIN = 8, HOLD_SLOP = 10, SWAP = 140, SWAP_SHIFT = 10, RISE = 4, GRACE = 80;
  var DEFAULTS = { side: "top", surfaceColor: "#f5f5f5", inkColor: "#18181b", size: "md", radius: 8, gap: 8, arrow: true,
    popDuration: 160, popScale: 0.94, popBlur: 4, showFuse: false, longPress: 500, disabled: false };
  var RM = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var uid = 0;

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function now() { return window.performance ? performance.now() : Date.now(); }
  function horizontal(side) { return side === "top" || side === "bottom"; }
  function reduced() { return !!(RM && RM.matches); }

  // cubic-bezier(x1, y1, x2, y2) as an easing function, as CSS and motion read it.
  function bezier(x1, y1, x2, y2) {
    function at(a, b, t) { return ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t; }
    function slope(a, b, t) { return 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a; }
    return function (x) {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      var t = x;
      for (var i = 0; i < 8; i++) {
        var d = at(x1, x2, t) - x, s = slope(x1, x2, t);
        if (Math.abs(d) < 1e-5) break;
        if (Math.abs(s) < 1e-6) break;
        t -= d / s;
      }
      return at(y1, y2, clamp(t, 0, 1));
    };
  }
  var easeOut = bezier.apply(null, EASE_OUT);

  function anchorOf(rect, side, gap) {
    if (side === "top") return [rect.left + rect.width / 2, rect.top - gap];
    if (side === "bottom") return [rect.left + rect.width / 2, rect.bottom + gap];
    if (side === "left") return [rect.left - gap, rect.top + rect.height / 2];
    return [rect.right + gap, rect.top + rect.height / 2];
  }
  function layoutOf(x, y, width, height, side) {
    if (horizontal(side)) {
      var X = clamp(x - width / 2, MARGIN, Math.max(MARGIN, window.innerWidth - MARGIN - width));
      return { X: X, Y: side === "top" ? y - height : y };
    }
    var Y = clamp(y - height / 2, MARGIN, Math.max(MARGIN, window.innerHeight - MARGIN - height));
    return { X: side === "left" ? x - width : x, Y: Y };
  }

  // A few animated numbers, stepped together on one animation frame, standing in for motion's motion values:
  // tweens (duration and easing) and springs (motion's duration-and-bounce form).
  function Values(onFrame) {
    var v = {}, runs = {}, raf = 0, last = 0;
    function step(t) {
      raf = 0;
      var dt = Math.min(0.064, last ? (t - last) / 1000 : 1 / 60);
      last = t;
      var busy = false;
      Object.keys(runs).forEach(function (k) {
        var r = runs[k];
        if (r.kind === "tween") {
          var p = r.dur > 0 ? clamp((t - r.t0) / r.dur, 0, 1) : 1;
          v[k] = r.from + (r.to - r.from) * r.ease(p);
          if (p >= 1) { delete runs[k]; if (r.done) r.done(); } else busy = true;
        } else {
          // Semi-implicit Euler in small steps: steady at any frame rate.
          var n = Math.ceil(dt / 0.004), h = dt / n;
          for (var i = 0; i < n; i++) {
            var a = (-r.k * (v[k] - r.to) - r.c * r.vel) / r.m;
            r.vel += a * h;
            v[k] += r.vel * h;
          }
          if (Math.abs(v[k] - r.to) < 0.02 && Math.abs(r.vel) < 0.05) { v[k] = r.to; delete runs[k]; } else busy = true;
        }
      });
      onFrame(dt);
      if (busy || v.__keep) kick(); else last = 0;
    }
    function kick() { if (!raf) raf = requestAnimationFrame(step); }
    return {
      v: v,
      get: function (k) { return v[k] || 0; },
      jump: function (k, to) { delete runs[k]; v[k] = to; kick(); },
      tween: function (k, to, ms, ease, done) {
        runs[k] = { kind: "tween", from: v[k] || 0, to: to, t0: now(), dur: ms, ease: ease || easeOut, done: done };
        kick();
      },
      // motion: { type: "spring", duration, bounce } → damping ratio 1 - bounce, and a stiffness that settles in about
      // that duration.
      spring: function (k, to, ms, bounce) {
        var zeta = 1 - (bounce || 0), w = 4.4 / (zeta * ms / 1000);
        var prev = runs[k];
        runs[k] = { kind: "spring", to: to, m: 1, k: w * w, c: 2 * zeta * w, vel: prev && prev.kind === "spring" ? prev.vel : 0 };
        kick();
      },
      raw: function (k, opts) { runs[k] = opts; kick(); },
      stop: function (k) { delete runs[k]; },
      busy: function (k) { return !!runs[k]; },
      kick: kick
    };
  }

  function group(options) {
    options = options || {};
    var G = {
      delay: options.delay == null ? 400 : options.delay,
      warmWindow: options.warmWindow == null ? 300 : options.warmWindow,
      travel: options.travel == null ? 320 : options.travel,
      lean: options.lean || 0,
      onWarmChange: options.onWarmChange
    };
    var id = "warm-tooltip-" + (++uid);
    var st = { state: "closed", current: null, mode: "cold", instant: false, warmUntil: -Infinity, warm: false,
      swap: { dir: 0, across: false }, closeTimer: 0, leaveTimer: 0, warmTimer: 0, lastX: null, lastY: null };
    var el = null, box = null, arrowEl = null, textEl = null;
    var V = Values(render);

    function isWarm() { return st.state !== "closed" || now() < st.warmUntil; }
    function notify() {
      var next = isWarm();
      if (next === st.warm) return;
      st.warm = next;
      if (G.onWarmChange) G.onWarmChange(next);
    }

    // What motion's useTransform chains computed: the label's place, the arrow's offset, the pop and the lean.
    function render(dt) {
      var c = st.current;
      if (!el || !c) return;
      var side = c.side, x = V.get("ax"), y = V.get("ay"), w = V.get("w"), h = V.get("h"), p = V.get("presence");
      var L = layoutOf(x, y, w, h, side);
      el.style.transform = "translate(" + L.X + "px, " + L.Y + "px)";
      el.style.width = w + "px";
      el.style.height = h + "px";
      if (arrowEl) {
        if (horizontal(side)) arrowEl.style.left = clamp(x - L.X, 10, w - 10) + "px";
        else arrowEl.style.top = clamp(y - L.Y, 10, h - 10) + "px";
      }
      // The lean: the label tilts with the speed of its glide (useVelocity → useTransform → useSpring).
      var leanDeg = reduced() ? 0 : G.lean, l = 0;
      if (leanDeg) {
        var pos = horizontal(side) ? x : y, prev = horizontal(side) ? st.lastX : st.lastY;
        var speed = prev == null || !dt ? 0 : (pos - prev) / dt;
        if (horizontal(side)) st.lastX = pos; else st.lastY = pos;
        var target = clamp(-speed / FULL_LEAN_SPEED, -1, 1);
        var u = st.lean || { x: 0, v: 0 };
        var a = (-260 * (u.x - target) - 22 * u.v) / 0.4;
        u.v += a * dt; u.x += u.v * dt; st.lean = u;
        l = u.x;
        V.v.__keep = Math.abs(u.x) > 0.001 || Math.abs(u.v) > 0.01;
      }
      if (reduced()) {
        box.style.transform = "none";
        box.style.filter = "none";
      } else {
        var scale = c.popScale + (1 - c.popScale) * p;
        var rise = (1 - p) * RISE * SIGN[side] * (side === "left" ? -1 : 1);
        var tx = horizontal(side) ? 0 : rise, ty = horizontal(side) ? rise : 0;
        box.style.transform = "translate(" + tx + "px, " + ty + "px) scale(" + scale + ") rotate(" + (l * leanDeg * SIGN[side]) + "deg)";
        box.style.filter = c.popBlur && p < 1 ? "blur(" + (c.popBlur * (1 - p)) + "px)" : "none";
      }
      box.style.opacity = String(p);
    }

    function layer(c) {
      var lay = document.createElement("span");
      lay.className = "warm-tooltip__layer";
      var text = document.createElement("span");
      text.className = "warm-tooltip__text";
      if (c.content instanceof Node) text.appendChild(c.content.cloneNode(true)); else text.textContent = c.content;
      if (c.shortcut) {
        var kbd = document.createElement("kbd");
        kbd.className = "warm-tooltip__kbd";
        kbd.textContent = c.shortcut;
        text.appendChild(kbd);
      }
      lay.appendChild(text);
      return { layer: lay, text: text };
    }

    // The swap: the old words slide out one way, the new ones slide in from the other (AnimatePresence). No blur:
    // Safari draws a blurred layer sliding inside the rounded label with a flash of colour along its edge.
    function swapIn(c) {
      var made = layer(c), dir = st.swap.dir, across = st.swap.across;
      var olds = box.querySelectorAll(".warm-tooltip__layer:not(.out)");
      box.insertBefore(made.layer, arrowEl);
      textEl = made.text;
      if (!olds.length || dir === 0 || reduced() || !made.layer.animate) {
        for (var i = 0; i < olds.length; i++) olds[i].remove();
        return;
      }
      var shift = function (d) { return across ? "translate(0px, " + d + "px)" : "translate(" + d + "px, 0px)"; };
      var timing = { duration: SWAP, easing: "cubic-bezier(" + EASE_OUT.join(",") + ")", fill: "both" };
      made.layer.animate([{ opacity: 0, transform: shift(SWAP_SHIFT * dir) },
        { opacity: 1, transform: "translate(0px, 0px)" }], timing);
      Array.prototype.forEach.call(olds, function (o) {
        o.classList.add("out");
        var anim = o.animate([{ opacity: 1, transform: "translate(0px, 0px)" },
          { opacity: 0, transform: shift(-SWAP_SHIFT * dir) }], timing);
        anim.onfinish = function () { o.remove(); };
      });
    }

    function mount(c) {
      el = document.createElement("span");
      el.id = id;
      el.setAttribute("role", "tooltip");
      el.className = "warm-tooltip";
      box = document.createElement("span");
      box.className = "warm-tooltip__box";
      arrowEl = document.createElement("span");
      arrowEl.className = "warm-tooltip__arrow";
      arrowEl.setAttribute("aria-hidden", "true");
      box.appendChild(arrowEl);
      el.appendChild(box);
      document.body.appendChild(el);
      window.addEventListener("scroll", follow, { capture: true, passive: true });
      window.addEventListener("resize", follow);
      document.addEventListener("visibilitychange", onHidden);
      document.addEventListener("pointerdown", onOutside, true);
    }
    function unmount() {
      if (!el) return;
      el.remove();
      el = box = arrowEl = textEl = null;
      window.removeEventListener("scroll", follow, { capture: true });
      window.removeEventListener("resize", follow);
      document.removeEventListener("visibilitychange", onHidden);
      document.removeEventListener("pointerdown", onOutside, true);
    }
    function style(c) {
      var s = el.style;
      el.setAttribute("data-side", c.side);
      arrowEl.setAttribute("data-side", c.side);
      arrowEl.hidden = !c.arrow;
      arrowEl.style.left = arrowEl.style.top = "";
      s.setProperty("--wt-surface", c.surfaceColor);
      s.setProperty("--wt-ink", c.inkColor);
      s.setProperty("--wt-radius", c.radius + "px");
      s.setProperty("--wt-font", c.font + "px");
      s.setProperty("--wt-origin", ORIGIN[c.side]);
    }

    function finishClose() {
      var c = st.current;
      st.state = "closed";
      st.current = null;
      if (c && c.onActive) c.onActive(false);
      unmount();
      notify();
    }

    // The useLayoutEffect that runs when the label opens or moves: measure the words, then glide, pop or jump.
    function place() {
      var c = st.current;
      if (!c || !textEl) return;
      var a = anchorOf(c.trigger.getBoundingClientRect(), c.side, c.gap);
      var tw = textEl.offsetWidth + c.px * 2, th = textEl.offsetHeight + c.py * 2;
      var mode = st.mode;
      if (mode === "move" && !reduced() && G.travel > 0) {
        V.spring("ax", a[0], G.travel, 0.1);
        V.spring("ay", a[1], G.travel, 0.1);
        V.spring("w", tw, G.travel, 0.1);
        V.spring("h", th, G.travel, 0.1);
        V.tween("presence", 1, 120);
        return;
      }
      V.jump("ax", a[0]); V.jump("ay", a[1]); V.jump("w", tw); V.jump("h", th);
      st.lastX = st.lastY = null;
      if (mode === "cold" && !reduced()) {
        V.jump("presence", 0);
        V.tween("presence", 1, c.popDuration);
      } else V.jump("presence", 1);
    }

    function show(payload, mode) {
      clearTimeout(st.closeTimer);
      clearTimeout(st.leaveTimer);
      var prev = st.current, fresh = st.state === "closed";
      if (prev && prev.id !== payload.id) {
        var p = anchorOf(prev.trigger.getBoundingClientRect(), prev.side, prev.gap);
        var n = anchorOf(payload.trigger.getBoundingClientRect(), payload.side, payload.gap);
        var across = !horizontal(payload.side);
        st.swap = { dir: Math.sign(across ? n[1] - p[1] : n[0] - p[0]) || 1, across: across };
      } else st.swap = { dir: 0, across: !horizontal(payload.side) };
      st.mode = fresh ? mode : mode === "instant" ? "instant" : "move";
      st.instant = mode === "instant";
      st.current = payload;
      st.state = "open";
      if (!el) mount(payload);
      style(payload);
      if (!prev || prev.id !== payload.id || fresh) {
        if (prev && prev.onActive && prev.id !== payload.id) prev.onActive(false);
        swapIn(payload);
      }
      if (payload.onActive) payload.onActive(true);
      place();
      notify();
    }

    function beginClose(instant) {
      var c = st.current;
      if (!c || st.state !== "open") return;
      st.state = "closing";
      st.warmUntil = now() + c.warmWindow;
      clearTimeout(st.warmTimer);
      st.warmTimer = setTimeout(notify, c.warmWindow + 1);
      if (instant || reduced()) {
        V.jump("presence", 0);
        finishClose();
        return;
      }
      var closeMs = Math.round(c.popDuration * 0.8);
      V.tween("presence", 0, closeMs);
      st.closeTimer = setTimeout(finishClose, closeMs);
    }

    function hide(tooltipId, instant) {
      var c = st.current;
      if (!c || c.id !== tooltipId || st.state !== "open") return;
      clearTimeout(st.leaveTimer);
      if (instant || st.instant) { beginClose(true); return; }
      st.leaveTimer = setTimeout(function () { beginClose(false); }, GRACE);
    }

    function follow() {
      var c = st.current;
      if (!c) return;
      var a = anchorOf(c.trigger.getBoundingClientRect(), c.side, c.gap);
      // While gliding, retarget the spring; otherwise stick to the trigger.
      if (V.busy("ax")) { V.spring("ax", a[0], G.travel, 0.1); V.spring("ay", a[1], G.travel, 0.1); }
      else { V.jump("ax", a[0]); V.jump("ay", a[1]); }
    }
    function onHidden() { if (document.visibilityState === "hidden" && st.current) hide(st.current.id, true); }
    function onOutside(e) {
      var c = st.current;
      if (c && st.state === "open" && c.wrap && !c.wrap.contains(e.target)) c.hideSelf(false);
    }

    function reset() {
      if (st.current) hide(st.current.id, true);
      st.warmUntil = -Infinity;
      clearTimeout(st.warmTimer);
      notify();
    }

    // One trigger: `wrap` takes the handlers (the React version's wrapper span), `target` gets aria-describedby, and
    // the label hangs from `anchor` (the wrapper unless given; the dock hangs it from the icon, which swells).
    function attach(wrap, opts) {
      var o = {};
      Object.keys(DEFAULTS).forEach(function (k) { o[k] = DEFAULTS[k]; });
      Object.keys(opts || {}).forEach(function (k) { if (opts[k] !== undefined) o[k] = opts[k]; });
      var tid = "wt-" + (++uid);
      var target = o.target || wrap.firstElementChild || wrap;
      var t = { open: 0, press: 0, press0: null, suppressClick: false };
      var fuse = null;
      wrap.classList.add("warm-tooltip__trigger");
      if (o.showFuse) {
        fuse = document.createElement("span");
        fuse.className = "warm-tooltip__fuse";
        fuse.setAttribute("aria-hidden", "true");
        wrap.appendChild(fuse);
      }
      function coldDelay() { return o.delay == null ? G.delay : o.delay; }
      function setFuse(state) {
        if (!fuse) return;
        fuse.setAttribute("data-side", o.side);
        fuse.setAttribute("data-fuse", state);
        wrap.style.setProperty("--wt-fuse-ms", (t.press0 ? o.longPress : coldDelay()) + "ms");
      }
      function content() { return typeof o.content === "function" ? o.content() : o.content; }
      function payload() {
        var preset = SIZES[o.size] || SIZES.md;
        return { id: tid, trigger: o.anchor || wrap, content: content(), shortcut: typeof o.shortcut === "function" ? o.shortcut() : o.shortcut,
          side: o.side, gap: o.gap, arrow: o.arrow, surfaceColor: o.surfaceColor, inkColor: o.inkColor, radius: o.radius,
          font: preset.font, px: preset.px, py: preset.py, popDuration: o.popDuration, popScale: o.popScale, popBlur: o.popBlur,
          warmWindow: o.warmWindow == null ? G.warmWindow : o.warmWindow, hideSelf: hideMe, onActive: active, wrap: wrap };
      }
      var described = target.getAttribute("aria-describedby");
      function active(on) {
        if (on) target.setAttribute("aria-describedby", id);
        else if (described) target.setAttribute("aria-describedby", described);
        else target.removeAttribute("aria-describedby");
      }
      function hideMe(instant) {
        clearTimeout(t.open);
        setFuse("idle");
        hide(tid, instant);
      }
      function arm() {
        if (isWarm()) { show(payload(), "warm"); return; }
        setFuse("arming");
        clearTimeout(t.open);
        t.open = setTimeout(function () { setFuse("idle"); show(payload(), "cold"); }, coldDelay());
      }
      function cancelPress() {
        clearTimeout(t.press);
        if (!t.press0) return;
        t.press0 = null;
        wrap.removeAttribute("data-pressing");
        setFuse("idle");
      }
      var on = {
        pointerenter: function (e) { if (!o.disabled && e.pointerType !== "touch" && e.buttons === 0) arm(); },
        pointerleave: function (e) { if (!o.disabled && e.pointerType !== "touch") hideMe(false); },
        pointerdown: function (e) {
          if (o.disabled) return;
          if (e.pointerType === "mouse") { hideMe(false); return; }
          // The original captures the pointer here; a captured pointer can't reach the dock's other icons, so the
          // dock lets it be.
          t.press0 = { x: e.clientX, y: e.clientY, id: e.pointerId };
          wrap.setAttribute("data-pressing", "");
          setFuse("arming");
          t.press = setTimeout(function () {
            t.suppressClick = true;
            t.press0 = null;
            wrap.removeAttribute("data-pressing");
            setFuse("idle");
            show(payload(), "cold");
          }, o.longPress);
        },
        pointermove: function (e) {
          var p = t.press0;
          if (p && p.id === e.pointerId && Math.hypot(e.clientX - p.x, e.clientY - p.y) > HOLD_SLOP) cancelPress();
        },
        pointerup: cancelPress,
        pointercancel: cancelPress,
        contextmenu: function (e) { if (t.press0 || t.suppressClick) e.preventDefault(); },
        focusin: function (e) {
          if (o.disabled) return;
          var focusVisible = false;
          try { focusVisible = e.target.matches(":focus-visible"); } catch (err) { focusVisible = false; }
          if (focusVisible) show(payload(), "instant");
        },
        focusout: function (e) { if (!wrap.contains(e.relatedTarget)) hideMe(true); },
        keydown: function (e) { if (e.key === "Escape") hideMe(true); }
      };
      Object.keys(on).forEach(function (k) { wrap.addEventListener(k, on[k]); });
      function swallow(e) {
        if (!t.suppressClick) return;
        t.suppressClick = false;
        e.preventDefault();
        e.stopPropagation();
      }
      wrap.addEventListener("click", swallow, true);
      return {
        update: function (next) {
          Object.keys(next).forEach(function (k) { o[k] = next[k]; });
          if (o.disabled) { cancelPress(); hideMe(true); }
        },
        isOpen: function () { return !!(st.current && st.current.id === tid && st.state === "open"); },
        destroy: function () {
          clearTimeout(t.open); clearTimeout(t.press);
          hideMe(true);
          Object.keys(on).forEach(function (k) { wrap.removeEventListener(k, on[k]); });
          wrap.removeEventListener("click", swallow, true);
          if (fuse) fuse.remove();
          wrap.classList.remove("warm-tooltip__trigger");
        }
      };
    }

    return { attach: attach, reset: reset, follow: follow, isWarm: isWarm };
  }

  window.WarmTooltip = { group: group };
})();
