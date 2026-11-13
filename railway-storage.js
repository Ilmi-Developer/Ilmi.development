/* Shared Railway storage. The artifact and file:// versions keep their own storage.
   Classrooms are created and deleted from the page, so the class list comes from the server. */
(function () {
  "use strict";
  if (window.SIGNING_OUT) return;  // a new tab going back to the sign-in page (see TAB_GUARD in server.py)
  var revisions = {}, pending = {}, sending = false, reading = false, loaded = false, failed = false;
  var editVersion = 0;
  // Shown only when saving or loading fails; routine save status goes in the page heading.
  var banner = document.createElement("div");
  banner.className = "sync bad";
  banner.setAttribute("role", "alert");
  banner.hidden = true;
  var message = document.createElement("span");
  var retry = document.createElement("button");
  retry.textContent = "Retry";
  var download = document.createElement("button");
  download.textContent = "Download unsaved changes";
  var reload = document.createElement("button");
  reload.textContent = "Reload latest records";
  [message, retry, download, reload].forEach(function (e) { banner.appendChild(e); });
  var signIn = document.createElement("a");
  signIn.textContent = "Sign in again";
  signIn.href = "/login";
  signIn.target = "_blank";
  signIn.rel = "noopener";
  banner.appendChild(signIn);
  [retry, download, reload, signIn].forEach(function (e) { e.hidden = true; });
  document.querySelector(".work").prepend(banner);

  // The signed-in person, their account links and sign-out, in the navigation rail.
  var ME = window.ME || {};
  // The developer's bar (schools.py, static/developer.css), the same as on the admin pages (templates/base.html): a
  // strip of its own above the page, with the school that is open, the view it is seen as, and the way back to
  // every school. Choosing a view posts the form; the server sends the dashboard back in that view.
  if (ME.dev) (function () {
    var bar = document.createElement("div"), row = document.createElement("div");
    bar.className = "devbar"; row.className = "devbar-in";
    bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Developer");
    var who = document.createElement("span");
    who.className = "devwho"; who.textContent = "Developer";
    var at = document.createElement("span");
    at.className = "devat"; at.textContent = at.title = ME.dev.school || "";
    var views = document.createElement("form");
    views.method = "post"; views.action = "/schools/view"; views.className = "seg devseg";
    views.setAttribute("role", "group"); views.setAttribute("aria-label", "View this school as");
    var token = document.createElement("input");
    token.type = "hidden"; token.name = "csrf"; token.value = ME.csrf || "";
    views.appendChild(token);
    (ME.dev.views || []).forEach(function (v) {
      var b = document.createElement("button");
      b.name = "view"; b.value = v[0]; b.textContent = v[1];
      b.setAttribute("aria-pressed", String(ME.dev.view === v[0]));
      views.appendChild(b);
    });
    var all = document.createElement("a"), allText = document.createElement("span");
    all.className = "devlink devall"; all.href = "/schools"; all.title = "All schools";
    all.innerHTML = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/></svg>';
    allText.className = "devlink-t"; allText.textContent = "All schools";
    all.appendChild(allText);
    [who, at, views, all].forEach(function (e) { row.appendChild(e); });
    bar.appendChild(row);
    document.querySelector(".shell").prepend(bar);
  })();
  var ICONS = {
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.3-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.8a3.4 3.4 0 0 1 0 6.4"/><path d="M18 14.8c1.8.7 3 2.4 3.5 5.2"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v4.5h4.5"/><path d="M12 7.5V12l3 2"/>',
    activity: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/>',
    tv: '<rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M8 20h8"/><path d="M12 16.5V20"/>',
    sheet: '<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M3.5 9.5h17"/><path d="M3.5 15h17"/><path d="M9.5 9.5v11"/>',
    pie: '<path d="M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5v10z"/><path d="M14 3.2A8.5 8.5 0 0 1 20.8 10H14z"/>',
    upload: '<path d="M12 15V4"/><path d="m7.5 8.5 4.5-4.5 4.5 4.5"/><path d="M4 15v4.5h16V15"/>',
    faq: '<path d="M20.5 12a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1-4.4A8.5 8.5 0 1 1 20.5 12z"/><path d="M9.8 9.6a2.3 2.3 0 0 1 4.4.9c0 1.5-2.2 2-2.2 3.2"/><path d="M12 16.4h.01"/>'
  };
  ICONS.idcard = '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16c.6-1.6 1.8-2.4 3.2-2.4s2.6.8 3.2 2.4"/><path d="M15 10h3"/><path d="M15 13.5h3"/>';
  ICONS.pulse = '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>';
  ICONS.db = '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13"/><path d="M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>';
  ICONS.chev = '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>';
  function svg(name) {
    return '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + "</svg>";
  }
  function link(l, sub) {
    var a = document.createElement("a");
    a.className = sub ? "nav nsub" : "nav"; a.href = l[1];
    a.innerHTML = svg(l[2]);
    a.appendChild(document.createTextNode(l[0]));
    return a;
  }
  if (ME.role === "admin") {
    // The same groups, items and order as the admin pages' menu (templates/base.html). The groups fold open and
    // shut (static/menu.js, loaded after this script).
    var adminNav = document.getElementById("adminNav");
    var heading = document.createElement("div");
    heading.className = "navh";
    heading.textContent = "Administration";
    adminNav.appendChild(heading);
    [["users", "User management", "idcard", [null, ["Manage users", "/admin/users", "users"]]],
     ["live", "Live Activity", "pulse", [["Activity", "/admin/activity", "activity"], ["Class history", "/admin/history", "history"]]],
     ["data", "Data handling", "db", [["Counted coins", "/admin/house-points", "pie"], ["Spreadsheet", "/admin/export", "sheet"], ["Import", "/admin/import", "upload"]]]
    ].forEach(function (gr) {
      var g = document.createElement("div");
      g.className = "ngrp"; g.setAttribute("data-g", gr[0]);
      var head = document.createElement("button");
      head.type = "button"; head.className = "nav ngh";
      head.setAttribute("aria-expanded", "true"); head.setAttribute("aria-controls", "ng-" + gr[0]);
      var label = document.createElement("span");
      label.className = "ngt"; label.textContent = gr[1];
      head.innerHTML = svg(gr[2]); head.appendChild(label); head.insertAdjacentHTML("beforeend", svg("chev"));
      var body = document.createElement("div"), fold = document.createElement("div"), tree = document.createElement("div");
      body.className = "ngb"; body.id = "ng-" + gr[0]; fold.className = "ngf"; tree.className = "ngi";
      gr[3].forEach(function (l) {
        // Students (the dashboard's own page) opens User management.
        if (!l) {
          var stu = document.getElementById("nU");
          if (stu) { stu.classList.add("nsub"); tree.appendChild(stu); }
        } else tree.appendChild(link(l, true));
      });
      fold.appendChild(tree); body.appendChild(fold); g.appendChild(head); g.appendChild(body);
      adminNav.appendChild(g);
    });
    adminNav.appendChild(link(["FAQs", "/admin/faqs", "faq"]));
    if (ME.assistant) adminNav.appendChild(link(["Assistant requests", "/admin/requests", "faq"]));
    adminNav.appendChild(link(["Presenter", "/admin/tv", "tv"]));
  }
  // A HeadTeacher has FAQs, after Presenter, but none of the Administration pages.
  if (ME.role === "head") {
    var faq = document.createElement("a");
    faq.className = "nav"; faq.href = "/admin/faqs";
    faq.innerHTML = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS.faq + "</svg>";
    faq.appendChild(document.createTextNode("FAQs"));
    document.getElementById("adminNav").appendChild(faq);
  }
  var me = document.getElementById("me");
  var who = document.createElement("div");
  who.className = "who";
  // Their profile picture, or their initial (avatars.py).
  var av = document.createElement("span");
  av.className = "av"; av.setAttribute("aria-hidden", "true");
  if (ME.avatar) {
    var pic = document.createElement("img");
    pic.src = ME.avatar; pic.alt = ""; pic.width = 38; pic.height = 38;
    av.appendChild(pic);
  } else av.textContent = ME.initial || "";
  var text = document.createElement("div");
  text.className = "wt";
  var nameLine = document.createElement("b");
  nameLine.textContent = ME.name || "";
  var roleLine = document.createElement("span");
  roleLine.textContent = ME.role === "admin" ? "Admin" : ME.role === "head" ? "HeadTeacher" : "Teacher";
  text.appendChild(nameLine); text.appendChild(roleLine);
  who.appendChild(av); who.appendChild(text);
  var row = document.createElement("div");
  row.className = "row";
  var account = document.createElement("a");
  account.className = "linkbtn"; account.href = "/account"; account.textContent = "Account";
  var out = document.createElement("form");
  out.method = "post"; out.action = "/logout";
  var csrf = document.createElement("input");
  csrf.type = "hidden"; csrf.name = "csrf"; csrf.value = ME.csrf || "";
  var outButton = document.createElement("button");
  outButton.className = "linkbtn"; outButton.textContent = "Sign out";
  out.appendChild(csrf); out.appendChild(outButton);
  row.appendChild(account); row.appendChild(out);
  me.appendChild(who); me.appendChild(row);
  me.hidden = false;
  // Only admins upload CSVs and create or delete classrooms; the server enforces this too.
  if (ME.role !== "admin") {
    document.getElementById("up").style.display = "none";
    document.getElementById("delWrap").style.display = "none";
  }

  function lock(value) {
    document.querySelector("main").inert = value;
    document.querySelector("header").inert = value;
  }
  function status(text, state) { message.textContent = text; setSt(state === "bad" ? "Needs attention" : text, state); }
  function problem(error) {
    failed = true;
    lock(true);
    banner.hidden = false;
    var conflict = error.status === 409 || error.status === 404;
    var signedOut = error.status === 401, denied = error.status === 403;
    status(signedOut
      ? "You've been signed out. Sign in again (it opens in a new tab), then come back here and press Retry. Your unsaved changes are kept in this tab."
      : denied
      ? "Your account can't make this change. Reload to see the classrooms you can edit."
      : conflict
      ? "Another browser changed or deleted this classroom. Download your unsaved changes, then reload the latest records."
      : "Records could not be loaded or saved. Check your connection and retry. Keep this page open to preserve unsaved changes.", "bad");
    retry.hidden = conflict || denied;
    download.hidden = !Object.keys(pending).length;
    reload.hidden = !(conflict || denied);
    signIn.hidden = !signedOut;
  }
  async function api(path, options) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var response = await fetch(path, Object.assign({credentials: "same-origin", cache: "no-store", signal: controller.signal}, options));
      if (!response.ok) { var error = new Error("Request failed"); error.status = response.status; throw error; }
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  async function read() {
    if (reading || sending || Object.keys(pending).length || failed) return;
    reading = true;
    var versionAtStart = editVersion;
    try {
      var records = await api("/api/classes");
      // Discard responses that overlap local edits. A later poll will refresh them.
      if (sending || Object.keys(pending).length || editVersion !== versionAtStart) return;
      var active = document.activeElement;
      var editing = active && (active.matches("input,textarea,select") || active.isContentEditable);
      var updated = false, first = !loaded;
      Object.keys(records).forEach(function (c) {
        var record = records[c];
        // Never update the revision without also applying its state.
        if (loaded && editing && CID === c) return;
        if (revisions[c] !== record.revision) {
          parseC(c, record.json);
          revisions[c] = record.revision;
          updated = true;
        }
      });
      // Classrooms deleted in another browser.
      CIDS.slice().forEach(function (c) {
        if (records[c] || (loaded && editing && CID === c)) return;
        dropC(c);
        delete revisions[c];
        updated = true;
      });
      loaded = true;
      LOADED = true;
      DBST = "on";
      lock(false);
      // Leave a focused class form intact while other classes refresh.
      if (first || (updated && (!editing || !CID))) changed();
      status("All changes saved");
      retry.hidden = true;
      banner.hidden = true;
      refreshHouses();
    } catch (error) { problem(error); }
    finally { reading = false; }
  }
  // Whole-school house points for the Master page; teachers only load their own classrooms.
  async function refreshHouses() {
    try {
      var totals = await api("/api/houses");
      var before = JSON.stringify(window.HOUSE_TOTALS);
      window.HOUSE_TOTALS = totals;
      // Admins also get the points added by hand for a whole house, for the House points page.
      if (window.ME && (ME.role === "admin" || ME.role === "head")) {
        var extra = await api("/api/house-extra"), had = JSON.stringify(window.HOUSE_EXTRA || null);
        window.HOUSE_EXTRA = extra;
        if (had !== JSON.stringify(extra)) before = "";
      }
      if (before !== JSON.stringify(totals) && !CID) master();
    } catch (error) { /* The totals refresh with the next poll. */ }
  }
  async function flush() {
    if (sending || failed) return;
    var c = Object.keys(pending)[0];
    if (!c) { status("All changes saved"); banner.hidden = true; return; }
    sending = true;
    var json = pending[c];
    var path = "/api/classes/" + encodeURIComponent(c);
    var headers = {"Content-Type": "application/json", "X-Dashboard-Request": "1"};
    status("Saving…", "busy");
    try {
      if (json === null) {
        // A deleted classroom. Revision 0 means it was never saved.
        await api(path, {method: "DELETE", headers: headers, body: JSON.stringify({revision: revisions[c] || 0})});
        if (pending[c] === null) { delete pending[c]; delete revisions[c]; }
      } else {
        // Revision 0 creates a new classroom.
        var result = await api(path, {method: "PUT", headers: headers, body: JSON.stringify({json: json, revision: revisions[c] || 0})});
        revisions[c] = result.revision;
        if (pending[c] === json) delete pending[c];
      }
    } catch (error) { problem(error); }
    finally { sending = false; }
    if (!failed) flush();
  }
  // Replace device-only saves for the hosted page; no student data is cached in localStorage.
  persist = function (c) {
    if (!ST[c] || !loaded || failed) return;
    editVersion++;
    pending[c] = cj(c);
    flush();
  };
  unpersist = function (c) {
    if (!loaded || failed) return;
    editVersion++;
    pending[c] = null;
    flush();
  };
  retry.onclick = function () {
    failed = false;
    retry.hidden = download.hidden = reload.hidden = signIn.hidden = true;
    if (Object.keys(pending).length) {
      lock(false);
      flush();
    } else { read(); }
  };
  download.onclick = function () {
    var records = {};
    Object.keys(pending).forEach(function (c) { records[c] = {json: pending[c], revision: revisions[c]}; });
    var url = URL.createObjectURL(new Blob([JSON.stringify(records, null, 2)], {type: "application/json"}));
    var link = document.createElement("a");
    link.href = url; link.download = "ilmi-unsaved-changes.json"; link.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  };
  reload.onclick = function () {
    if (confirm("Reload server records? Unsaved changes in this tab will be discarded. Download them first if you need to keep them.")) {
      pending = {};
      location.reload();
    }
  };
  window.addEventListener("beforeunload", function (event) {
    if (sending || Object.keys(pending).length) { event.preventDefault(); event.returnValue = ""; }
  });
  lock(true);
  status("Loading class records…", "busy");
  read();
  // While the dashboard is open in a background tab, check in every few minutes so the sign-in stays alive;
  // once the app is closed the check-ins stop and the server ends the sign-in after 20 minutes.
  var checkedIn = Date.now();
  setInterval(function () {
    if (!document.hidden) { read(); checkedIn = Date.now(); return; }
    if (Date.now() - checkedIn < 240000) return;
    checkedIn = Date.now();
    fetch("/api/houses", { credentials: "same-origin", cache: "no-store" }).catch(function () {});
  }, 10000);
  // Help or Requests (helpdesk.py): teachers ask an admin for help or make a request from the bottom of their
  // overview, and read the replies in the same pop-up. Admins answer on the FAQs page.
  if (ME.role === "teacher") (function () {
    var KIND = {help: "Help", request: "Request"};
    var ASK = {help: "What do you need help with?", request: "What would you like to request?"};
    var SEND = {help: "Send message", request: "Send request"};
    var HINT = {help: "Say what you were trying to do and what happened. Include the class or student if it helps.",
                request: "Say what you'd like and why it would help your class."};
    var SVG = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
    var ICON = {help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6a2.3 2.3 0 0 1 4.4.9c0 1.5-2.2 2-2.2 3.2"/><path d="M12 16.4h.01"/>',
                request: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
                faq: ICONS.faq};
    var state = {items: [], unread: 0, email: false, loaded: false}, kind = null, note = "";
    var foot = document.createElement("div");
    foot.className = "hrq";
    foot.innerHTML = '<div class="hrq-t"><b>Need a hand?</b><span>Ask an admin for help, or make a request.</span></div>'
      + '<button type="button" class="btn hrq-b" aria-haspopup="dialog">' + SVG + ICON.faq + '</svg>Help or Requests'
      + '<span class="hrq-dot" hidden></span><span class="sr-only hrq-new"></span></button>';
    var overview = document.getElementById("vM");
    if (!overview) return;
    overview.appendChild(foot);
    var button = foot.querySelector("button"), dot = foot.querySelector(".hrq-dot"), srNew = foot.querySelector(".hrq-new");

    function e(text) { return esc(text == null ? "" : text); }
    function when(t) {
      return new Date(t * 1000).toLocaleString("en-GB", {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
    }
    function open() { return !document.getElementById("drw").hidden && DMODE === "hrq"; }
    function badge() {
      dot.hidden = !state.unread;
      srNew.textContent = state.unread ? " (" + state.unread + " new repl" + (state.unread === 1 ? "y" : "ies") + ")" : "";
    }
    function take(data) {
      state.items = data.items || []; state.unread = data.unread || 0; state.email = !!data.email; state.loaded = true;
      badge();
    }
    async function call(path, body) {
      var options = {credentials: "same-origin", cache: "no-store"};
      if (body) {
        options.method = "POST";
        options.headers = {"Content-Type": "application/json", "X-Dashboard-Request": "1"};
        options.body = JSON.stringify(body);
      }
      var response = await fetch(path, options), data = {};
      try { data = await response.json(); } catch (error) { /* An error page, not JSON. */ }
      if (!response.ok) {
        var error = new Error(data.error || (response.status === 401 ? "You've been signed out. Sign in again, then try once more." : "That didn't send. Check your connection and try again."));
        throw error;
      }
      return data;
    }
    async function refresh() {
      try {
        var before = JSON.stringify(state.items), data = await call("/api/help");
        take(data);
        // Redraw an open list only when something changed, so focus isn't lost for nothing.
        if (open() && !kind && JSON.stringify(state.items) !== before) home();
      } catch (error) { /* Tried again later. */ }
    }
    function list() {
      if (!state.items.length) return '<p class="cap hrq-none">Nothing sent yet. Your messages and the admins\' replies will appear here.</p>';
      return '<ol class="hrq-list">' + state.items.map(function (i) {
        return '<li class="hrq-it' + (i.unread ? " new" : "") + '"><div class="hrq-h"><span class="pill ' + (i.kind === "help" ? "blue" : "amber") + '">' + KIND[i.kind] + "</span>"
          + (i.unread ? '<span class="pill green">New reply</span>' : "") + '<span class="cap">' + e(when(i.at)) + "</span></div>"
          + '<p class="hrq-msg">' + e(i.body) + "</p>"
          + (i.reply ? '<div class="hrq-r"><p class="cap"><b>' + e(i.by || "An admin") + "</b> replied " + e(when(i.repliedAt)) + (i.emailed ? ", also by email" : "") + '</p><p class="hrq-msg">' + e(i.reply) + "</p></div>"
            : '<p class="cap hrq-wait">Waiting for a reply</p>') + "</li>";
      }).join("") + "</ol>";
    }
    function home() {
      kind = null;
      var body = document.getElementById("drwB");
      body.innerHTML = (note ? '<p class="hrq-ok" role="status">' + e(note) + "</p>" : "")
        + '<p class="cap">Choose one. An admin replies here' + (state.email ? ", and by email" : "") + ".</p>"
        + '<div class="hrq-pick">' + ["help", "request"].map(function (k) {
          return '<button type="button" class="hrq-opt" data-hk="' + k + '"><span class="hrq-ic">' + SVG + ICON[k] + "</svg></span><b>" + KIND[k] + "</b><span>"
            + (k === "help" ? "Something isn't working, or you're not sure how to do it." : "Ask for something new, like a change to the dashboard or a resource for your class.") + "</span></button>";
        }).join("") + "</div>"
        + '<h3>Your messages</h3>' + (state.loaded ? list() : '<p class="cap">Loading…</p>');
      note = "";
    }
    function write(k) {
      kind = k;
      document.getElementById("drwB").innerHTML = '<form class="addf hrq-f" id="hrqF" novalidate>'
        + '<button type="button" class="btn sm quiet hrq-back" data-hk="back">' + SVG + '<path d="m15 6-6 6 6 6"/></svg>Back</button>'
        + '<label>' + ASK[k] + '<span class="cap">' + HINT[k] + '</span><textarea id="hrqT" rows="7" maxlength="2000"></textarea></label>'
        + '<p class="naerr" id="hrqE" role="alert"></p><div class="acts"><button type="submit" class="btn p">' + SEND[k] + "</button></div></form>";
      document.getElementById("hrqT").focus();
    }
    button.onclick = function () {
      drawer("Help or Requests", "");
      DMODE = "hrq";
      home();
      document.getElementById("drwX").focus();
      if (!state.loaded) refresh();
      if (state.unread) {
        call("/api/help/seen", {}).then(function () { state.unread = 0; badge(); state.items.forEach(function (i) { i.unread = false; }); }).catch(function () {});
      }
    };
    document.getElementById("drwB").addEventListener("click", function (event) {
      if (DMODE !== "hrq") return;
      var b = event.target.closest("[data-hk]");
      if (!b) return;
      if (b.dataset.hk === "back") { home(); var first = document.querySelector("#drwB [data-hk]"); if (first) first.focus(); }
      else write(b.dataset.hk);
    });
    document.getElementById("drwB").addEventListener("submit", async function (event) {
      if (DMODE !== "hrq" || event.target.id !== "hrqF") return;
      event.preventDefault();
      var text = document.getElementById("hrqT"), err = document.getElementById("hrqE"), send = event.target.querySelector("[type=submit]");
      if (!text.value.trim()) { err.textContent = "Type your message first."; text.focus(); return; }
      send.disabled = true; send.textContent = "Sending…";
      try {
        take(await call("/api/help", {kind: kind, body: text.value}));
        note = (kind === "help" ? "Your message was sent." : "Your request was sent.") + " An admin will reply here" + (state.email ? " and by email." : ".");
        if (open()) { home(); document.getElementById("drwX").focus(); }
      } catch (error) {
        if (!document.contains(err)) return;
        err.textContent = error.message; send.disabled = false; send.textContent = SEND[kind];
      }
    });
    refresh();
    setInterval(function () { if (!document.hidden) refresh(); }, 180000);
  })();
})();
