/* Account's Theme card (themes.py). Without this script every tile and button is a plain form post.

   Choosing a tile changes the page in place (cross-fading where the browser can, at once with reduced motion) and
   saves the choice. "New theme", "Make a copy" and the pencil open the editor, where every change to a colour or to
   the photo shows on this page straight away: the browser applies the photo settings itself, and the server works
   out the readable text, logo and shade colours for the new colours (/account/themes/preview). Save keeps the
   theme and puts it on; Cancel puts back the theme in use. */
(function () {
  var box = document.getElementById("theme");
  if (!box || !window.fetch || !window.FormData) return;
  var root = document.documentElement, pick = box.querySelector(".thpick"), ed = document.getElementById("thedit");
  var csrf = pick.querySelector('input[name="csrf"]').value, msg = box.querySelector(".thpmsg"), emsg = ed.querySelector(".thmsg");
  var still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var mine = document.getElementById("thmine"), file = document.getElementById("thfile");
  root.classList.add("js-th");

  function post(url, body) {
    if (!(body instanceof FormData)) { var f = new FormData(); Object.keys(body || {}).forEach(function (k) { f.append(k, body[k]) }); body = f }
    if (!body.has("csrf")) body.append("csrf", csrf);
    return fetch(url, {method: "POST", body: body, credentials: "same-origin", headers: {"X-Dashboard-Request": "1", "Accept": "application/json"}})
      .then(function (r) { return r.json().catch(function () { return {} }).then(function (j) { if (!r.ok || j.ok === false) throw j; return j }) });
  }
  function fail(el, j, text) { el.textContent = (j && j.message) || text || "Couldn't save that. Check your connection and try again." }
  function transition(fn) { if (window.IlmiWash) IlmiWash.turn(fn); else if (!still && document.startViewTransition) document.startViewTransition(fn); else fn() }

  // ---- The theme in use -----------------------------------------------------------------------------------------
  // What <html> carries for it, so Cancel can put it back.
  var inUse = snapshot();
  function snapshot() {
    var l = document.getElementById("themecss");
    return {theme: root.getAttribute("data-theme"), mode: root.getAttribute("data-mode"), logo: root.hasAttribute("data-logo"),
            photo: root.hasAttribute("data-photo"), css: l ? l.getAttribute("href") : null};
  }
  function setAttr(name, value) { if (value === null || value === false || value === undefined) root.removeAttribute(name); else root.setAttribute(name, value === true ? "" : value) }
  function applyLook(look, done) {
    // look: {theme, mode, logo, photo, css}. The new stylesheet loads before the page switches to it.
    var old = document.getElementById("themecss");
    function flip(link) {
      transition(function () {
        clearPreview();
        if (old && old !== link) old.remove();
        if (link) link.id = "themecss";
        setAttr("data-theme", look.theme === "light" ? null : look.theme);
        setAttr("data-mode", look.theme === "light" ? null : look.mode);
        setAttr("data-logo", !!look.css);
        setAttr("data-photo", !!look.photo);
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta && look.menu) meta.setAttribute("content", look.menu);
      });
      inUse = {theme: look.theme, mode: look.mode, logo: !!look.css, photo: !!look.photo, css: look.css, menu: look.menu};
      if (done) done();
    }
    if (!look.css) return flip(null);
    if (old && old.getAttribute("href") === look.css) return flip(old);
    var link = document.createElement("link");
    link.rel = "stylesheet"; link.href = look.css;
    link.onload = function () { flip(link) };
    link.onerror = function () { link.remove(); flip(null) };
    document.head.appendChild(link);
  }
  function lookOf(j) { return {theme: j.theme && j.theme.key ? j.theme.key : j.theme, mode: j.mode, css: j.css, photo: j.photo, menu: j.menu} }
  function mark(key) {
    box.querySelectorAll(".tht[data-key]").forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-key") === key ? "true" : "false") });
  }

  pick.addEventListener("submit", function (e) {
    var b = e.submitter;
    if (!b || b.name !== "theme") return;
    e.preventDefault();
    if (box.classList.contains("busy")) return;
    var key = b.value, before = inUse, was = box.querySelector('.tht[aria-pressed="true"]');
    msg.textContent = "";
    if (!ed.hidden) closeEditor();
    mark(key);
    box.classList.add("busy");
    post("/account/theme", {theme: key})
      .then(function (j) { applyLook(lookOf(j)) })
      .catch(function (j) { mark(was ? was.getAttribute("data-key") : ""); applyLook(before); fail(msg, j) })
      .then(function () { box.classList.remove("busy") });
  });

  // ---- New, copy, delete --------------------------------------------------------------------------------------
  document.getElementById("thnew").addEventListener("submit", function (e) { e.preventDefault(); create({}) });
  document.getElementById("thcopy").addEventListener("submit", function (e) { e.preventDefault(); create({from: e.submitter ? e.submitter.value : ""}) });
  function create(body) {
    msg.textContent = "";
    post("/account/themes", body).then(function (j) { addTile(j.theme); openEditor(j.theme) }).catch(function (j) { fail(msg, j) });
  }
  document.getElementById("thdel").addEventListener("submit", function (e) {
    e.preventDefault();
    var b = e.submitter, url = b && b.getAttribute("formaction");
    if (!url || !confirm("Delete " + (b.getAttribute("data-del") || "this theme") + "? Anyone using it goes back to Ilmi.")) return;
    post(url, {}).then(function (j) {
      var id = url.match(/themes\/(\d+)\//)[1], t = box.querySelector('.tht[data-key="c' + id + '"]');
      if (t) t.closest(".thc").remove();
      if (!box.querySelector("#thshared .thc")) document.getElementById("thshared").hidden = true;
      if (!ed.hidden && ed.getAttribute("data-id") === id) closeEditor(true);
      mark(j.theme);
      applyLook(lookOf(j));
      newTileLimit();
    }).catch(function (j) { fail(msg, j) });
  });
  box.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-edit]");
    if (a) { e.preventDefault(); openEditor(JSON.parse(a.getAttribute("data-edit"))); return }
    if (e.target.closest('[data-th="cancel"]')) { e.preventDefault(); closeEditor() }
    if (e.target.closest('[data-th="reset"]')) { Object.keys(PHOTO_DEFAULTS).forEach(function (k) { field("p_" + k).value = PHOTO_DEFAULTS[k] }); preview() }
  });

  var SVGNS = "http://www.w3.org/2000/svg";
  function addTile(t) {
    var c = document.createElement("div"); c.className = "thc";
    var src = box.querySelector(".thgrid .tht svg").cloneNode(true);
    var b = document.createElement("button"); b.className = "tht"; b.name = "theme"; b.value = t.key; b.setAttribute("data-key", t.key); b.setAttribute("aria-pressed", "false");
    var n = document.createElement("span"); n.className = "thn"; n.textContent = t.name;
    var s = document.createElement("span"); s.className = "ths"; s.textContent = t.shared ? "Shared" : "";
    b.appendChild(src); b.appendChild(n); b.appendChild(s);
    var tools = document.createElement("div"); tools.className = "thtools";
    var a = document.createElement("a"); a.className = "thtool"; a.href = "/account?edit=" + t.id + "#theme"; a.title = "Edit"; a.setAttribute("aria-label", "Edit " + t.name);
    a.setAttribute("data-edit", JSON.stringify(t));
    a.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4"/></svg>';
    tools.appendChild(a); c.appendChild(b); c.appendChild(tools);
    mine.insertBefore(c, mine.lastElementChild);
    paintTile(t.key, t.tile, t.photo ? t.photo.replace("o=1&", "") : null);
    newTileLimit();
  }
  function newTileLimit() {
    var n = mine.querySelectorAll(".tht[data-key]").length, last = mine.lastElementChild;
    if (last) last.hidden = n >= 12;
  }
  function paintTile(key, tile, photo) {
    var b = box.querySelector('.tht[data-key="' + key + '"]');
    if (!b || !tile) return;
    var svg = b.querySelector("svg");
    svg.querySelectorAll("[data-k]").forEach(function (el) { var v = tile[el.getAttribute("data-k")]; if (v) el.setAttribute("fill", v) });
    var img = svg.querySelector("image");
    if (photo === undefined) return;
    if (photo && !img) { img = document.createElementNS(SVGNS, "image"); img.setAttribute("width", "120"); img.setAttribute("height", "76"); img.setAttribute("preserveAspectRatio", "xMidYMid slice"); svg.insertBefore(img, svg.children[1]) }
    if (photo) img.setAttribute("href", photo); else if (img) img.remove();
  }

  // ---- The editor -------------------------------------------------------------------------------------------
  var editing = null, previewVars = [], timer = 0, seq = 0;
  function field(n) { return ed.elements[n] }
  function openEditor(t) {
    editing = t;
    ed.setAttribute("data-id", t.id);
    ed.action = "/account/themes/" + t.id;
    document.getElementById("thphoto").action = "/account/themes/" + t.id + "/photo";
    document.getElementById("thphotorm").action = "/account/themes/" + t.id + "/photo/remove";
    ed.querySelector('button[form="thdel"]').setAttribute("formaction", "/account/themes/" + t.id + "/delete");
    ed.querySelector('button[form="thdel"]').setAttribute("data-del", t.name);
    field("name").value = t.name;
    field("shared").checked = !!t.shared;
    ["menu", "accent", "bg", "card"].forEach(function (k) { field(k).value = t.spec[k] });
    Object.keys(t.spec.photo).forEach(function (k) { field("p_" + k).value = t.spec.photo[k] });
    setPhoto(t.photo);
    emsg.textContent = "";
    ed.hidden = false;
    labels();
    preview(true);
    ed.scrollIntoView({behavior: still ? "auto" : "smooth", block: "nearest"});
    field("name").focus({preventScroll: true});
  }
  function closeEditor(gone) {
    ed.hidden = true;
    clearPreview();
    if (editing && !gone) paintTile(editing.key, editing.tile);
    editing = null;
    applyLook(inUse);
  }
  function setPhoto(url) {
    editing.photo = url || null;
    ed.querySelector(".thsliders").hidden = !url;
    ed.querySelector(".thrm").hidden = !url;
    ed.querySelector('label[for="thfile"]').textContent = url ? "Change photo" : "Add a photo";
    var thumb = ed.querySelector(".ththumb");
    thumb.hidden = !url;
    thumb.textContent = "";
    if (url) { var img = new Image(); img.alt = ""; img.src = url; thumb.appendChild(img) }
  }
  function spec() {
    var s = {id: editing.id, photo: {}};
    ["menu", "accent", "bg", "card"].forEach(function (k) { s[k] = field(k).value });
    ["zoom", "x", "y", "blur", "bright", "sat", "shade"].forEach(function (k) { s["p_" + k] = field("p_" + k).value });
    return s;
  }
  var UNIT = {zoom: "×", blur: " px", bright: "%", sat: "%", shade: "%", x: "%", y: "%"};
  var PHOTO_DEFAULTS = {zoom: 1, x: 50, y: 50, blur: 0, bright: 100, sat: 100, shade: 20};
  function labels() {
    ed.querySelectorAll(".thsw").forEach(function (l) { l.querySelector("code").textContent = l.querySelector("input").value });
    ed.querySelectorAll(".thsl").forEach(function (l) {
      var i = l.querySelector("input"), k = i.name.slice(2), v = parseFloat(i.value);
      l.querySelector("output").textContent = (k === "zoom" ? v.toFixed(2).replace(/0$/, "") : v) + UNIT[k];
    });
  }
  function setVar(k, v) { root.style.setProperty(k, v); if (previewVars.indexOf(k) < 0) previewVars.push(k) }
  function clearPreview() {
    previewVars.forEach(function (k) { root.style.removeProperty(k) });
    previewVars = [];
    root.removeAttribute("data-live");
    clearTimeout(timer);
    seq++;
  }
  // The photo settings show at once; the colours follow from the server a moment later.
  function photoNow() {
    if (!editing || !editing.photo) { root.removeAttribute("data-live"); return }
    var v = function (k) { return parseFloat(field("p_" + k).value) };
    setAttr("data-photo", true); setAttr("data-live", true);
    setVar("--ph-src", 'url("' + editing.photo + '")');
    setVar("--ph-pos", v("x") + "% " + v("y") + "%");
    setVar("--ph-zoom", String(v("zoom")));
    setVar("--ph-filter", "blur(" + v("blur") + "px) brightness(" + v("bright") / 100 + ") saturate(" + v("sat") / 100 + ")");
    setVar("--ph-bleed", (-2 * v("blur")) + "px");
  }
  function preview(now) {
    labels();
    photoNow();
    clearTimeout(timer);
    var mine_ = ++seq;
    timer = setTimeout(function () {
      fetch("/account/themes/preview?spec=" + encodeURIComponent(JSON.stringify(spec())), {credentials: "same-origin", headers: {"Accept": "application/json"}})
        .then(function (r) { if (!r.ok) throw r; return r.json() })
        .then(function (j) {
          if (mine_ !== seq || !editing) return;
          Object.keys(j.vars).forEach(function (k) { setVar(k, j.vars[k]) });
          setAttr("data-mode", j.mode); setAttr("data-logo", true);
          if (!editing.photo) setAttr("data-photo", false);
          if (j.shade !== null && j.shade !== undefined && editing.photo) {
            var c = j.scrim.match(/\w\w/g).map(function (h) { return parseInt(h, 16) });
            setVar("--ph-scrim", "rgba(" + c.join(",") + "," + j.shade + ")");
          }
          paintTile(editing.key, j.tile);
        })
        .catch(function () {});
    }, now ? 0 : 90);
  }
  ed.addEventListener("input", function (e) { if (e.target.type !== "text") preview() });
  ed.addEventListener("change", function (e) { if (e.target.type === "color") preview() });

  ed.addEventListener("submit", function (e) {
    e.preventDefault();
    var saveBtn = ed.querySelector(".thacts .primary");
    saveBtn.disabled = true; emsg.textContent = "";
    post(ed.action, new FormData(ed)).then(function (j) {
      var t = j.theme, b = box.querySelector('.tht[data-key="' + t.key + '"]');
      if (b) {
        b.querySelector(".thn").textContent = t.name;
        b.querySelector(".ths").textContent = t.shared ? "Shared" : "";
        var a = b.parentNode.querySelector("a[data-edit]");
        a.setAttribute("data-edit", JSON.stringify(t)); a.setAttribute("aria-label", "Edit " + t.name);
      }
      paintTile(t.key, t.tile, t.photo ? t.photo.replace("o=1&", "") + "-" + Date.now() : null);
      editing = null;
      ed.hidden = true;
      mark(t.key);
      applyLook(lookOf(j));
    }).catch(function (j) { fail(emsg, j) }).then(function () { saveBtn.disabled = false });
  });

  // ---- The photo ------------------------------------------------------------------------------------------
  // Made no bigger than the server keeps (2400px) and sent as a JPEG, so a phone's large photo uploads quickly.
  function shrink(f) {
    if (!window.createImageBitmap) return Promise.resolve(f);
    return createImageBitmap(f, {imageOrientation: "from-image"}).then(function (img) {
      var k = Math.min(1, 2400 / Math.max(img.width, img.height)), c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      return new Promise(function (ok) { c.toBlob(function (b) { ok(b && b.size < 1900000 ? b : null) }, "image/jpeg", 0.86) })
        .then(function (b) { return b || new Promise(function (ok) { c.toBlob(ok, "image/jpeg", 0.7) }) });
    }).catch(function () { return f });
  }
  file.addEventListener("change", function () {
    var f = file.files && file.files[0];
    if (!f || !editing) return;
    emsg.textContent = "";
    var label = ed.querySelector('label[for="thfile"]');
    label.textContent = "Uploading…";
    shrink(f).then(function (b) {
      var body = new FormData(); body.append("photo", b, "photo.jpg");
      return post("/account/themes/" + editing.id + "/photo", body);
    }).then(function (j) {
      setPhoto(j.theme.photo);
      Object.keys(j.theme.spec.photo).forEach(function (k) { field("p_" + k).value = j.theme.spec.photo[k] });
      preview(true);
    }).catch(function (j) { setPhoto(editing && editing.photo); fail(emsg, j, "That photo couldn't be used. Choose a JPEG, PNG or WebP photo.") })
      .then(function () { file.value = "" });
  });
  document.getElementById("thphotorm").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!editing) return;
    post("/account/themes/" + editing.id + "/photo/remove", {}).then(function () {
      setPhoto(null);
      clearPreview();
      setAttr("data-photo", false);
      preview(true);
    }).catch(function (j) { fail(emsg, j) });
  });

  if (ed.getAttribute("data-id")) {
    // Opened at /account?edit=<id>: the same editor, now with the live preview.
    var a = box.querySelector('a[data-edit]');
    box.querySelectorAll("a[data-edit]").forEach(function (x) { if (JSON.parse(x.getAttribute("data-edit")).id == ed.getAttribute("data-id")) a = x });
    if (a) openEditor(JSON.parse(a.getAttribute("data-edit")));
  }
  newTileLimit();
})();
