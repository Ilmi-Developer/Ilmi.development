// The wash page turn's starting point (src/tokens.css, "The wash page turn"). A new page bleeds in from wherever the
// last press was: a dock item, a link, the Sign in button. This script only remembers that point and hands it to the
// style sheet as --vx and --vy; the turn itself is one CSS animation. It runs in the head, before the first paint, so
// a page arriving from another page of the site already knows where its wash starts.
(function () {
  var root = document.documentElement, KEY = "ilmi-wash", last = null;
  function aim(p) {
    p = p || last;
    if (p && Date.now() - p[2] < 4000) {
      root.style.setProperty("--vx", Math.round(p[0]) + "px");
      root.style.setProperty("--vy", Math.round(p[1]) + "px");
    } else {
      root.style.removeProperty("--vx");
      root.style.removeProperty("--vy");
    }
  }
  try {
    var s = (sessionStorage.getItem(KEY) || "").split(",");
    if (s.length === 3) aim([+s[0], +s[1], +s[2]]);
  } catch (e) {}
  // A press with the keyboard has no point of its own, so the wash starts from the middle of what was pressed.
  addEventListener("click", function (e) {
    var x = e.clientX, y = e.clientY, t = e.target;
    if (!e.detail && t && t.getBoundingClientRect) {
      var r = t.getBoundingClientRect();
      x = r.left + r.width / 2;
      y = r.top + r.height / 2;
    }
    last = [x, y, Date.now()];
    try { sessionStorage.setItem(KEY, last.join(",")); } catch (err) {}
  }, true);
  var still = null;
  try { still = matchMedia("(prefers-reduced-motion: reduce)"); } catch (e) {}
  // Runs a change of page as a wash turn where the browser can, and at once where it cannot or motion is reduced.
  // The change itself (fn) must not depend on anything that follows the call.
  function turn(fn) {
    if (!document.startViewTransition || (still && still.matches) || document.hidden) { fn(); return; }
    aim();
    try { document.startViewTransition(fn); } catch (e) { fn(); }
  }
  window.IlmiWash = {aim: aim, turn: turn};
})();
