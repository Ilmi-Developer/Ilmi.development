/* Ask Ilmi: the assistant's page on the dashboard (see assistant.py). The menu's Ask Ilmi item opens it for
   teachers, HeadTeachers and administrators; the server decides what it can do from the session. Admins see "Data
   tools on", and any change the assistant previews comes with Confirm and Cancel: only a press of Confirm applies it.

   The thinking line and the prompt box follow the React Bits ThoughtLine and PromptBar designs, rewritten without
   React (the dashboard has no build step, and its Content-Security-Policy runs only this site's own scripts). The
   page is kept to a greeting and the box; the server always answers at medium effort.

   The conversation is kept in this tab only (a variable, not storage): it is sent back with each question and is
   gone when the tab is closed or reloaded, or when New chat is pressed. */
(function () {
  "use strict";
  if (!window.RAILWAY || !window.ME) return;
  var ADMIN = ME.role === "admin", MAX_HISTORY = 30, history = [], busy = false, controller = null, built = false;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)");
  // Only a mouse or trackpad gets the field focused on opening: on a phone that would pop the keyboard up.
  var FINE = matchMedia("(pointer: fine)");
  var SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  var MARK = SVG + ' stroke-width="1.8"><path d="M12 6.8C9.6 5.2 6.4 4.8 3.5 5.6v8.6c2.9-.8 6.1-.4 8.5 1.2 2.4-1.6 5.6-2 8.5-1.2V5.6c-2.9-.8-6.1-.4-8.5 1.2z"/><path d="M12 6.8v8.6"/><path d="m7 16.8 10 3.7M17 16.8 7 20.5"/></svg>';
  var CHEV = SVG + ' stroke-width="2.2"><path d="m6 9 6 6 6-6"/></svg>';
  var TICK = SVG + ' stroke-width="2.5"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  var NEW = SVG + ' stroke-width="1.8"><path d="M12 20h8"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';

  // What each tool did, for the thinking line's trace. Names only: the page never sees the tool inputs or results.
  var DID = {
    submit_request: "Sent your request to the admins", list_my_requests: "Checked your requests",
    search_students: "Searched for students", get_student_progress: "Looked up a student's progress",
    list_classes: "Listed the classes", list_class_students: "Looked at a class's students",
    class_summary: "Summarised passes over the period", add_student: "Prepared a new student for you to confirm",
    update_student_details: "Prepared a change of details for you to confirm",
    move_student: "Prepared a class move for you to confirm", set_student_target: "Prepared a target change for you to confirm",
    record_pass: "Prepared a pass for you to confirm", correct_or_remove_pass: "Prepared a pass correction for you to confirm",
    archive_student: "Prepared a move to Ex students for you to confirm"
  };
  // Shortcuts, by typing / at the start of the box.
  var COMMANDS = [
    ["how", "/how", "Step-by-step help with the site", "How do I "],
    ["syllabus", "/syllabus", "How levels and Juz items work", "Explain how the syllabus works, from L1 to the Juz items."],
    ["islam", "/islam", "A question about Islam", "I have a question about Islam: "],
    ["request", "/request", "Send a request to the admins", "I'd like to request "],
    ["mine", "/my-requests", "Check your requests", "What's the status of my requests?"]
  ].concat(ADMIN ? [
    ["find", "/find", "Look up a student", "Show me the progress of "],
    ["summary", "/summary", "Passes in a class over a period", "Summarise passes this month for "],
    ["behind", "/behind", "Students with no recent pass", "Which students haven't passed anything in the last three weeks?"]
  ] : []);

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; });
  }
  // A small, safe subset of Markdown for the answers: everything is escaped first, then bold, italics, code, lists,
  // headings and pipe tables are turned into elements.
  function inline(s) {
    return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<i>$2</i>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
  function render(text) {
    var lines = String(text).replace(/\r/g, "").split("\n"), out = [], i = 0;
    function cells(line) { return line.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); }
    var LIST = /^\s*(\d+[.)]|[-*•])\s+/, TABLE = /^\s*\|.*\|\s*$/, HEAD = /^\s*#{1,4}\s+/;
    while (i < lines.length) {
      var line = lines[i];
      if (TABLE.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        var head = cells(line), rows = [];
        i += 2;
        while (i < lines.length && TABLE.test(lines[i])) rows.push(cells(lines[i++]));
        out.push('<div class="sai-tw"><table><thead><tr>' + head.map(function (c) { return '<th scope="col">' + inline(c) + "</th>"; }).join("") +
          "</tr></thead><tbody>" + rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("") +
          "</tbody></table></div>");
        continue;
      }
      var list = LIST.exec(line);
      if (list) {
        var ordered = /\d/.test(list[1]), items = [], same = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*•]\s+/;
        while (i < lines.length && same.test(lines[i])) items.push("<li>" + inline(lines[i++].replace(LIST, "")) + "</li>");
        out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>"));
        continue;
      }
      if (HEAD.test(line)) { out.push("<h3>" + inline(line.replace(HEAD, "")) + "</h3>"); i++; continue; }
      if (!line.trim()) { i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() && !LIST.test(lines[i]) && !TABLE.test(lines[i]) && !HEAD.test(lines[i])) para.push(inline(lines[i++]));
      out.push("<p>" + para.join("<br>") + "</p>");
    }
    return out.join("");
  }
  function post(path, body, signal) {
    return fetch(path, {method: "POST", credentials: "same-origin", cache: "no-store", signal: signal,
      headers: {"Content-Type": "application/json", "X-Dashboard-Request": "1"}, body: JSON.stringify(body)})
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || (r.status === 401 ? "Your session has ended. Sign in again." :
            r.status >= 500 ? "Ask Ilmi couldn't answer just now. Try again in a minute." : "Something went wrong. Try again."));
          return j;
        });
      });
  }

  // --- ThoughtLine: "Thinking…" with a breathing dot and a shimmer, a live clock, then "Thought for 3.2s" -------------------
  function fmt(ds) { return ds < 600 ? (ds / 10).toFixed(1) + "s" : Math.floor(ds / 600) + "m " + ((ds % 600) / 10).toFixed(1) + "s"; }
  function spoken(ds) { return ds < 600 ? (ds / 10).toFixed(1) + " seconds" : Math.floor(ds / 600) + " minutes " + ((ds % 600) / 10).toFixed(1) + " seconds"; }
  function thoughtLine(steps) {
    var root = el("div", "thought-line"), headBtn = el("button", "thought-line__head");
    root.setAttribute("data-working", "");
    headBtn.type = "button";
    headBtn.innerHTML = '<span class="thought-line__glyph" aria-hidden="true"><span class="thought-line__dot"></span></span>' +
      '<span class="thought-line__label" aria-hidden="true"><span class="thought-line__text" data-active><span class="thought-line__breath" data-shimmer>Thinking…</span></span>' +
      '<span class="thought-line__text thought-line__text--done">Thought for</span></span>' +
      '<span class="thought-line__timer" aria-hidden="true">0.0s</span>' +
      '<span class="thought-line__chevron" aria-hidden="true">' + CHEV + '</span>' +
      '<span class="thought-line__sr" role="status">Thinking…</span>';
    var trace = el("div", "thought-line__trace"), fold = el("div", "thought-line__fold"), list = el("div", "thought-line__steps");
    fold.appendChild(list); trace.appendChild(fold);
    root.appendChild(headBtn); root.appendChild(trace);
    var q = function (s) { return root.querySelector(s); };
    var work = q(".thought-line__text"), done = q(".thought-line__text--done"), stack = q(".thought-line__label"),
      timer = q(".thought-line__timer"), sr = q(".thought-line__sr"), chev = q(".thought-line__chevron");
    var working = true, open = true, ds = 0, started = performance.now();
    function place(glide) {
      var active = working ? work : done, shift = active.offsetWidth - stack.offsetWidth;
      if (!glide) timer.style.transition = "none";
      timer.style.transform = "translateX(" + shift + "px)";
      if (!glide) { void timer.offsetWidth; timer.style.transition = ""; }
    }
    function setSteps(items) {
      list.innerHTML = "";
      items.forEach(function (text, i) {
        var finished = !working || i < items.length - 1, row = el("div", "thought-line__step");
        if (finished) row.setAttribute("data-done", "");
        row.innerHTML = '<span class="thought-line__mark" aria-hidden="true">' + (finished ? TICK : '<i class="thought-line__pulse"></i>') + "</span>";
        row.appendChild(el("span", "thought-line__step-text", text));
        list.appendChild(row);
      });
      var has = items.length > 0;
      chev.toggleAttribute("data-on", has);
      headBtn.toggleAttribute("data-toggle", has);
      if (has) headBtn.setAttribute("aria-expanded", String(open)); else headBtn.removeAttribute("aria-expanded");
      headBtn.tabIndex = has ? 0 : -1;
      setOpen(open);
    }
    function setOpen(v) {
      open = v;
      var has = list.children.length > 0;
      root.toggleAttribute("data-open", open && has);
      trace.toggleAttribute("data-open", open);
      trace.setAttribute("aria-hidden", String(!open));
      if (has) headBtn.setAttribute("aria-expanded", String(open));
    }
    headBtn.addEventListener("click", function () { if (headBtn.hasAttribute("data-toggle")) setOpen(!open); });
    var clock = setInterval(function () { ds = Math.floor((performance.now() - started) / 100); timer.textContent = fmt(ds); }, 100);
    setSteps(steps || []);
    requestAnimationFrame(function () { place(false); });
    return {
      el: root,
      settle: function (items, doneText) {
        if (!working) return;
        clearInterval(clock);
        working = false;
        if (doneText) done.textContent = doneText;
        root.removeAttribute("data-working");
        work.removeAttribute("data-active");
        done.setAttribute("data-active", "");
        timer.setAttribute("data-done", "");
        place(true);
        setSteps(items || []);
        setOpen(false);
        sr.textContent = done.textContent + " " + spoken(ds);
      }
    };
  }

  // --- PromptBar: just the box, with a send tile whose arrow morphs into a stop square --------------------------
  var ARROW_UP = [12, 4.5, 18.5, 11, 14.25, 11, 14.25, 19.5, 9.75, 19.5, 9.75, 11, 5.5, 11];
  var SQUARE = [12, 6, 18, 6, 18, 12, 18, 18, 6, 18, 6, 12, 6, 6];
  function pathAt(t) {
    var d = "";
    for (var i = 0; i < ARROW_UP.length; i += 2) {
      d += (i ? "L" : "M") + (ARROW_UP[i] + (SQUARE[i] - ARROW_UP[i]) * t).toFixed(2) + " " + (ARROW_UP[i + 1] + (SQUARE[i + 1] - ARROW_UP[i + 1]) * t).toFixed(2);
    }
    return d + "Z";
  }
  function easeInOut(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }

  function promptBar(onSend, onStop) {
    var root = el("div", "prompt-bar"), field = el("div", "prompt-bar__field");
    var input = el("textarea", "prompt-bar__input");
    input.rows = 1;
    input.maxLength = 2000;
    input.placeholder = "How can I help?";
    input.setAttribute("aria-label", "Ask Ilmi");
    var send = el("button", "prompt-bar__send");
    send.type = "button";
    send.disabled = true;
    send.setAttribute("aria-label", "Send");
    send.innerHTML = '<svg class="prompt-bar__glyph" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="' + pathAt(0) + '"/></svg>';
    var glyph = send.querySelector("svg"), glyphPath = send.querySelector("path");
    field.appendChild(input); field.appendChild(send);
    root.appendChild(field);

    // Typing / at the start offers shortcuts; nothing on screen asks for them.
    var menu = null, rows = [], cursor = 0, isBusy = false, morph = 0, morphAnim = 0;

    function armed() { return isBusy || input.value.trim().length > 0; }
    function refresh() {
      var a = armed();
      send.disabled = !a;
      send.toggleAttribute("data-armed", a);
      send.setAttribute("aria-label", isBusy ? "Stop" : "Send");
    }
    function grow() {
      input.style.height = "0px";
      var max = 24 * 8;
      input.style.height = Math.min(input.scrollHeight, max) + "px";
      input.style.overflowY = input.scrollHeight > max ? "auto" : "hidden";
    }
    function setBusy(v) {
      isBusy = v;
      root.toggleAttribute("data-busy", v);
      refresh();
      var target = v ? 1 : 0, from = morph, dir = v ? 1 : -1, t0 = performance.now(), dur = reduce.matches ? 0 : 240;
      cancelAnimationFrame(morphAnim);
      (function step(now) {
        var k = dur ? Math.min(1, (now - t0) / dur) : 1, e = easeInOut(k);
        morph = from + (target - from) * e;
        glyphPath.setAttribute("d", pathAt(morph));
        var goo = reduce.matches ? 0 : Math.sin(morph * Math.PI), sx = 1 - 0.12 * goo;
        glyph.style.transform = goo > 0.001 ? "rotate(" + (dir * 8 * goo) + "deg) scale(" + sx + "," + (1 / sx) + ")" : "";
        if (k < 1) morphAnim = requestAnimationFrame(step);
      })(t0);
    }
    function closeMenu() { if (menu) menu.remove(); menu = null; }
    function moveGlow(fresh) {
      var glow = menu && menu.querySelector(".prompt-bar__glow"), row = rows[cursor];
      if (!glow) return;
      rows.forEach(function (r, i) { r.setAttribute("aria-selected", String(i === cursor)); });
      if (!row) { glow.style.opacity = "0"; return; }
      if (fresh) glow.style.transition = "none";
      glow.style.top = row.offsetTop + "px";
      glow.style.height = row.offsetHeight + "px";
      glow.style.opacity = "1";
      if (fresh) { void glow.offsetHeight; glow.style.transition = ""; }
    }
    function openCommands(query) {
      var list = COMMANDS.filter(function (c) { return c[1].slice(1).indexOf(query || "") === 0; }), fresh = !menu;
      closeMenu();
      if (!list.length) return;
      menu = el("div", "prompt-bar__menu");
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-label", "Shortcuts");
      var glow = el("span", "prompt-bar__glow");
      glow.setAttribute("aria-hidden", "true");
      menu.appendChild(glow);
      rows = list.map(function (c, i) {
        var row = el("button", "prompt-bar__row");
        row.type = "button";
        row.setAttribute("role", "option");
        row.appendChild(el("span", "prompt-bar__row-name", c[1]));
        row.appendChild(el("span", "prompt-bar__row-desc", c[2]));
        row.addEventListener("mousedown", function (e) { e.preventDefault(); });
        row.addEventListener("pointerenter", function () { cursor = i; moveGlow(false); });
        row.addEventListener("click", function () { input.value = c[3]; closeMenu(); grow(); refresh(); input.focus(); });
        menu.appendChild(row);
        return row;
      });
      cursor = Math.min(cursor, rows.length - 1);
      root.insertBefore(menu, field);
      moveGlow(fresh);
    }
    document.addEventListener("pointerdown", function (e) { if (menu && !root.contains(e.target)) closeMenu(); });
    field.addEventListener("click", function (e) { if (e.target === field) input.focus(); });
    input.addEventListener("input", function () {
      grow(); refresh();
      var m = /^\/([\w-]*)$/.exec(input.value);
      if (m) { cursor = 0; openCommands(m[1].toLowerCase()); } else closeMenu();
    });
    input.addEventListener("keydown", function (e) {
      if (menu && rows.length) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          cursor = (cursor + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length;
          moveGlow(false);
          return;
        }
        if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") { e.preventDefault(); rows[cursor].click(); return; }
      }
      if (e.key === "Escape" && menu) { e.preventDefault(); closeMenu(); return; }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    var pressed = function (v) { return function (e) { if (v && (e.button !== 0 || !armed())) return; send.toggleAttribute("data-pressed", v); }; };
    send.addEventListener("pointerdown", pressed(true));
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (k) { send.addEventListener(k, pressed(false)); });
    send.addEventListener("mousedown", function (e) { e.preventDefault(); });
    send.addEventListener("click", function () { if (isBusy) onStop(); else submit(); });
    function submit() {
      var text = input.value.trim();
      if (!text || isBusy) return;
      input.value = "";
      grow(); closeMenu(); refresh();
      onSend(text);
    }
    refresh();
    return {el: root, setBusy: setBusy, grow: grow, focus: function () { input.focus({preventScroll: true}); },
            fill: function (text) { input.value = text; grow(); refresh(); input.focus(); }};
  }

  // --- The page ------------------------------------------------------------------------------------------------------
  var page, log, col, hero, prompt;
  // A greeting in Claude's manner: short, warm and a little playful, with the person's name (as the overview greets
  // them, title included). A new one each time the page opens.
  function greeting() {
    var who = String(ME.name || "").trim(), n = who && who !== "Administrator" ? who : "";
    var now = new Date(), h = now.getHours(), day = now.getDay(), name = n ? ", " + n : "";
    var lines = [
      "Bismillah" + name + ". Where shall we start?",
      "Ready when you are" + name,
      "Which juz are we on today" + name + "?",
      "Back again" + name + "? Let's go",
      "What's on your mind" + name + "?",
      "One target at a time" + name,
      "Ask away" + name + ". I'm all ears"
    ];
    if (h >= 5 && h < 12) lines.push("Morning" + name + ". Tea first, then targets?", "Ṣabāḥ al-khayr" + name);
    else if (h >= 12 && h < 17) lines.push("Afternoon" + name + ". How's the class?", "Halfway through the day" + name);
    else if (h >= 17 && h < 23) lines.push("Evening" + name + ". Marking time?", "Masāʾ al-khayr" + name);
    else lines.push("Burning the midnight oil" + name + "?", "Still up" + name + "? I'm here");
    if (day === 0 || day === 6) lines.push("Class day" + name + "! What do you need?", "Weekend madrasah mode" + name);
    if (day === 5) lines.push("Jumuʿah Mubārak" + name);
    return lines[Math.floor(Math.random() * lines.length)];
  }
  function build() {
    built = true;
    var host = document.getElementById("aiW");
    page = el("div", "sai");
    page.setAttribute("data-empty", "");
    var top = el("div", "sai-top");
    if (ADMIN) {
      var on = el("span", "sai-dt", "Data tools on");
      on.title = "Ask Ilmi can look up and change student and classroom records for you. Every change needs your Confirm.";
      top.appendChild(on);
    }
    var fresh = el("button", "sai-new");
    fresh.type = "button";
    fresh.innerHTML = NEW;
    fresh.setAttribute("aria-label", "New chat");
    fresh.title = "New chat";
    fresh.addEventListener("click", reset);
    top.appendChild(fresh);

    log = el("div", "sai-log");
    log.setAttribute("aria-live", "polite");
    col = el("div", "sai-col");
    log.appendChild(col);

    hero = el("h2", "sai-hero");
    hero.innerHTML = '<span class="sai-mark" aria-hidden="true">' + MARK + '</span><span class="sai-hi"></span>';
    hero.querySelector(".sai-hi").textContent = greeting();

    var dock = el("div", "sai-dock");
    prompt = promptBar(ask, stop);
    dock.appendChild(prompt.el);

    page.appendChild(top);
    page.appendChild(log);
    page.appendChild(hero);
    page.appendChild(dock);
    host.appendChild(page);
  }
  function reset() {
    if (busy) stop();
    history = [];
    col.innerHTML = "";
    hero.querySelector(".sai-hi").textContent = greeting();
    page.setAttribute("data-empty", "");
    prompt.focus();
  }
  function scroll() { log.scrollTop = log.scrollHeight; }
  function remember(question, answer) {
    history.push({role: "user", content: question.slice(0, 8000)}, {role: "assistant", content: answer.slice(0, 8000)});
    while (history.length > MAX_HISTORY) history.splice(0, 2);
  }
  // A confirmation's result is added to the latest answer, so the assistant knows what happened next time.
  function note(text) {
    var last = history[history.length - 1];
    if (last && last.role === "assistant") last.content = (last.content + "\n\n[" + text + "]").slice(-8000);
  }
  function stop() { if (controller) controller.abort(); }

  function ask(question) {
    if (busy) return;
    busy = true;
    page.removeAttribute("data-empty");
    var u = el("div", "sai-u"), ub = el("div", "sai-ub");
    ub.innerHTML = render(question);
    u.appendChild(ub);
    col.appendChild(u);
    var a = el("div", "sai-a"), thought = thoughtLine(["Reading your question"]), body = el("div", "sai-text");
    a.appendChild(thought.el);
    a.appendChild(body);
    col.appendChild(a);
    scroll();
    prompt.setBusy(true);
    controller = new AbortController();
    post("/api/assistant", {message: question, history: history}, controller.signal).then(function (j) {
      var steps = ["Read your question"].concat((j.tools || []).map(function (t) {
        return t.indexOf("refused:") === 0 ? "A records tool was refused" : DID[t] || "Used a tool";
      })).concat(["Wrote the answer"]);
      thought.settle(steps);
      body.innerHTML = render(j.reply || "");
      body.classList.add("in");
      remember(question, j.reply || "(no answer)");
      if (j.pending && j.pending.length) a.appendChild(pendingCard(j.pending, j.expiresIn || 300));
    }, function (err) {
      if (err.name === "AbortError") {
        thought.settle(["Read your question", "Stopped"], "Stopped after");
        body.innerHTML = '<p class="sai-muted">Stopped. Ask again whenever you like.</p>';
      } else {
        thought.settle([], "Couldn't answer after");
        body.innerHTML = '<p class="sai-err">' + esc(err.message) + "</p>";
        prompt.fill(question);
      }
    }).then(function () {
      busy = false;
      controller = null;
      prompt.setBusy(false);
      scroll();
      prompt.focus();
    });
  }

  function pendingCard(items, seconds) {
    var card = el("div", "sai-card");
    card.appendChild(el("p", "sai-ch", items.length > 1 ? items.length + " changes to check" : "Change to check"));
    items.forEach(function (p) {
      var lines = p.summary.split("\n"), item = el("div", "sai-ci");
      item.appendChild(el("b", "", lines[0]));
      if (lines.length > 1) {
        var ul = el("ul");
        lines.slice(1).forEach(function (l) { ul.appendChild(el("li", "", l)); });
        item.appendChild(ul);
      }
      card.appendChild(item);
    });
    var acts = el("div", "sai-ca"), yes = el("button", "btn p", items.length > 1 ? "Confirm all " + items.length : "Confirm"),
      no = el("button", "btn", "Cancel"), left = el("span", "sai-exp");
    yes.type = no.type = "button";
    acts.appendChild(left); acts.appendChild(no); acts.appendChild(yes);
    card.appendChild(acts);
    var ends = Date.now() + seconds * 1000, timer = setInterval(tick, 1000);
    function tick() {
      var s = Math.max(0, Math.round((ends - Date.now()) / 1000));
      left.textContent = s ? "Expires in " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "Expired. Ask again to make a new preview.";
      if (!s) { clearInterval(timer); yes.disabled = no.disabled = true; }
    }
    tick();
    function choose(action) {
      clearInterval(timer);
      yes.disabled = no.disabled = true;
      left.textContent = action === "confirm" ? "Applying…" : "Cancelling…";
      post("/api/assistant/confirm", {action: action, tokens: items.map(function (p) { return p.token; })}).then(function (j) {
        acts.remove();
        var res = el("div", "sai-res");
        res.appendChild(el("p", "", j.message || ""));
        var ul = el("ul");
        (j.results || []).forEach(function (r) {
          var li = el("li", r.ok ? "ok" : "no", (r.ok ? "Changed: " : r.cancelled ? "Cancelled: " : "") + r.summary.split("\n").join("; "));
          if (r.error) { li.appendChild(document.createElement("br")); li.appendChild(el("b", "", r.error)); }
          ul.appendChild(li);
        });
        res.appendChild(ul);
        card.appendChild(res);
        scroll();
        note((action === "confirm" ? "The admin pressed Confirm. " : "The admin pressed Cancel. ") + (j.message || "") + " " +
          (j.results || []).map(function (r) { return (r.ok ? "Applied: " : "Not applied: ") + r.summary.split("\n").join("; ") + (r.error ? " (" + r.error + ")" : ""); }).join(" "));
      }, function (err) { left.textContent = err.message; });
    }
    yes.addEventListener("click", function () { choose("confirm"); });
    no.addEventListener("click", function () { choose("cancel"); });
    return card;
  }

  window.AskIlmi = {show: function () { if (!built) build(); setTimeout(function () { prompt.grow(); if (FINE.matches) prompt.focus(); }, 0); }};
  // The page may have opened on #ask (or the old #safarai) before this script loaded.
  if (window.PAGE === "A") window.AskIlmi.show();
})();
