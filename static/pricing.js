// Ilmi's pricing calculator (templates/pricing.html) and the estimate beside the sign-up form (templates/signup.html).
// The numbers come from pricing.py's one configuration, handed over in data-config, and the sum here is the same sum
// as pricing.quote: graduated bands (each rate only for the students inside its band), then the smallest monthly
// charge for a paid plan, then the annual discount. Whole pence throughout. Without this script the page still shows
// the bands and a worked example, and sign-up shows the price on its review page, which the server works out.
(function () {
  "use strict";
  var host = document.querySelector("[data-config]");
  if (!host) return;
  var C;
  try { C = JSON.parse(host.getAttribute("data-config")); } catch (e) { return; }

  function quote(n, annual) {
    n = Math.max(0, Math.min(Math.floor(n) || 0, 100000));
    var out = { students: n, custom: n > C.max, lines: [], subtotal: 0, monthly: 0, min: false, free: true };
    if (out.custom) { out.free = false; return out; }
    var start = 1;
    for (var i = 0; i < C.bands.length && n >= start; i++) {
      var b = C.bands[i], count = Math.min(n, b.upto) - start + 1;
      out.lines.push({ from: start, to: start + count - 1, count: count, pence: b.pence, subtotal: count * b.pence });
      out.subtotal += count * b.pence;
      start = b.upto + 1;
    }
    var m = out.subtotal;
    if (m > 0 && m < C.minimum) { m = C.minimum; out.min = true; }
    out.monthly = m; out.free = m === 0;
    out.annual = m * (12 - C.monthsFree);
    out.saving = m * C.monthsFree;
    out.charge = annual ? out.annual : m;
    return out;
  }
  function money(p, keep) {
    var s = (p / 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    if (!keep && /\.00$/.test(s)) s = s.slice(0, -3);
    return C.symbol + s;
  }
  window.ilmiQuote = quote; // for the tests, which check this sum against the server's

  var annual = false;
  var num = document.getElementById("c-n"), range = document.getElementById("c-r");
  var segs = [].slice.call(document.querySelectorAll("[data-billing]"));
  function el(id) { return document.getElementById(id); }
  // Whether sign-up is open: when it isn't, the server points the button at Contact and it stays there.
  var open = !!el("c-go") && (el("c-go").getAttribute("href") || "").indexOf("/signup") === 0;
  function text(id, t) { var e = el(id); if (e) e.textContent = t; }
  function row(a, b, c, cls) {
    var tr = document.createElement("tr"), th = document.createElement("th"), t1 = document.createElement("td"), t2 = document.createElement("td");
    if (cls) tr.className = cls;
    th.scope = "row"; th.textContent = a; t1.textContent = b; t2.textContent = c;
    tr.appendChild(th); tr.appendChild(t1); tr.appendChild(t2);
    return tr;
  }

  function draw() {
    var n = Math.max(1, Math.floor(+num.value) || 1), q = quote(n, annual);
    var label = el("c-label");
    if (label) { label.textContent = "Estimated price for "; var b = document.createElement("b"); b.textContent = n.toLocaleString("en-GB"); label.appendChild(b); label.appendChild(document.createTextNode(n === 1 ? " student" : " students")); }
    var table = el("c-break"), custom = el("c-custom"), go = el("c-go");
    if (q.custom) {
      text("c-total", "Custom"); text("c-per", "pricing"); text("c-sub", "");
      if (table) table.hidden = true;
      if (custom) custom.hidden = false;
      if (go) { go.firstChild.textContent = "Talk to us"; go.setAttribute("href", "#contact"); }
      return;
    }
    if (table) table.hidden = false;
    if (custom) custom.hidden = true;
    text("c-total", q.free ? "Free" : money(q.charge, true));
    text("c-per", q.free ? "" : annual ? "per year" : "per month");
    text("c-sub", q.free ? "Up to " + C.free + " students, with no card needed."
      : annual ? "That is " + money(q.annual / 12, true) + " a month: " + C.monthsFree + " months free, saving " + money(q.saving, true) + " a year."
      : "Billed monthly. Pay annually and save " + money(q.saving, true) + " a year.");
    if (table) {
      var body = table.tBodies[0], foot = table.tFoot;
      body.textContent = ""; foot.textContent = "";
      q.lines.forEach(function (l) {
        body.appendChild(row(l.count === 1 ? "Student " + l.from : "Students " + l.from + " to " + l.to,
          l.count + " × " + (l.pence ? money(l.pence, true) : "free"), money(l.subtotal, true)));
      });
      if (q.min) body.appendChild(row("Smallest monthly charge", "", money(C.minimum, true), "min"));
      foot.appendChild(row("Each month", "", money(q.monthly, true)));
      if (annual && !q.free) foot.appendChild(row("Each year (" + (12 - C.monthsFree) + " months)", "", money(q.annual, true)));
    }
    if (go && open) {
      go.firstChild.textContent = q.free ? "Get started free" : "Get started";
      go.setAttribute("href", "/signup?students=" + n + "&billing=" + (annual ? "annual" : "monthly"));
    }
  }
  function setBilling(kind) {
    annual = kind === "annual";
    segs.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-billing") === kind)); });
    // The cards' buttons carry the billing choice into sign-up.
    [].forEach.call(document.querySelectorAll("a[data-signup]"), function (a) {
      var h = a.getAttribute("href") || "";
      if (h.indexOf("/signup") === 0) a.setAttribute("href", h.replace(/&billing=\w+/, "") + "&billing=" + kind);
    });
    if (num) draw();
    var form = document.getElementById("su-billing");
    if (form && form.value !== kind) { form.value = kind; estimate(); }
  }
  segs.forEach(function (b) { b.addEventListener("click", function () { setBilling(b.getAttribute("data-billing")); }); });
  if (num && range) {
    num.addEventListener("input", function () { range.value = Math.min(+range.max, Math.max(1, +num.value || 1)); draw(); });
    range.addEventListener("input", function () { num.value = range.value; draw(); });
    num.addEventListener("blur", function () { if (!(+num.value >= 1)) { num.value = 1; draw(); } });
    draw();
  }

  // The sign-up form: the same estimate, as the student count and billing period are typed.
  var sn = document.getElementById("su-students"), sb = document.getElementById("su-billing");
  function estimate() {
    if (!sn) return;
    var n = Math.floor(+sn.value) || 0, q = quote(n, sb && sb.value === "annual"), per = sb && sb.value === "annual" ? " a year" : " a month";
    text("su-total", n < 1 ? "–" : q.custom ? "Custom" : q.free ? "Free" : money(q.charge, true));
    text("su-per", n < 1 || q.custom || q.free ? "" : per.trim());
    text("su-sub", n < 1 ? "Enter your number of active students."
      : q.custom ? "For more than " + C.max + " students we prepare a quotation."
      : q.free ? "Up to " + C.free + " students Ilmi is free, with no card needed."
      : q.min ? "The smallest monthly charge for a paid plan applies."
      : "Worked out band by band; the next page shows every line.");
    var pick = document.getElementById("su-billing-f");
    if (pick) pick.hidden = q.free || q.custom || n < 1;
  }
  if (sn) { sn.addEventListener("input", estimate); if (sb) sb.addEventListener("change", estimate); estimate(); }
})();
