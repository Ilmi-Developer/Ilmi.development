/* Profile picture cropper on the Account page (avatars.py). Choosing a photo opens it under a round frame; it can be
   dragged, zoomed with the slider, the mouse wheel, a pinch or the keyboard, and Save sends a 512px square of what is
   inside the frame. The server re-encodes it either way. Without this script the form uploads the photo as it is. */
(function () {
  "use strict";
  var box = document.querySelector(".pfp"), crop = document.getElementById("crop");
  if (!box || !crop || !window.fetch || !window.URL || !URL.createObjectURL) return;
  box.classList.add("js");
  var file = document.getElementById("pfpfile"), form = box.querySelector(".pfp-up"), msg = box.querySelector(".pfp-msg");
  var stage = crop.querySelector(".cropstage"), img = stage.querySelector("img"), zoom = crop.querySelector(".cropzoom input");
  var previews = [].slice.call(crop.querySelectorAll(".cropprev canvas"));
  var save = crop.querySelector('[data-crop="save"]');
  var still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var OUT = 512, src = null, w = 0, h = 0, S = 0, base = 1, z = 1, x = 0, y = 0, opener = null;

  function say(text, bad) { msg.textContent = text; msg.classList.toggle("bad", !!bad); }

  // Keep the photo covering the frame: it can't be moved or zoomed out past an edge.
  function clamp() {
    var k = base * z;
    x = Math.min(0, Math.max(S - w * k, x));
    y = Math.min(0, Math.max(S - h * k, y));
  }
  function draw() {
    clamp();
    img.style.transform = "translate(" + x + "px," + y + "px) scale(" + base * z + ")";
    previews.forEach(function (c) { paint(c, c.width); });
  }
  // What is inside the frame, drawn onto a canvas of the given size.
  function paint(canvas, size) {
    var k = base * z, ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, size, size);
    ctx.drawImage(img, -x / k, -y / k, S / k, S / k, 0, 0, size, size);
  }
  // Zoom to z2, keeping the point (px, py) of the frame still.
  function zoomTo(z2, px, py) {
    z2 = Math.min(4, Math.max(1, z2));
    if (px == null) { px = S / 2; py = S / 2; }
    var k = base * z, k2 = base * z2;
    x = px - (px - x) * k2 / k;
    y = py - (py - y) * k2 / k;
    z = z2;
    zoom.value = z;
    draw();
  }
  function reset() {
    S = stage.clientWidth;
    base = S / Math.min(w, h);
    z = 1; zoom.value = 1;
    x = (S - w * base) / 2; y = (S - h * base) / 2;
    draw();
  }

  function open() {
    opener = document.activeElement;
    crop.hidden = false;
    crop.classList.remove("out");
    if (!still) crop.classList.add("in");
    reset();
    stage.focus();
  }
  function close() {
    if (crop.hidden) return;
    var done = function () {
      crop.hidden = true;
      crop.classList.remove("in", "out");
      if (src) { URL.revokeObjectURL(src); src = null; }
      img.removeAttribute("src");
      file.value = "";
      if (opener && opener.focus) opener.focus();
    };
    if (still) return done();
    crop.classList.remove("in");
    crop.classList.add("out");
    setTimeout(done, 240);
  }

  file.addEventListener("change", function () {
    var f = file.files && file.files[0];
    if (!f) return;
    say("");
    if (!/^image\/(jpeg|png|webp|gif)$/.test(f.type)) {
      file.value = "";
      return say("Choose a JPEG, PNG, WebP or GIF picture.", true);
    }
    if (src) URL.revokeObjectURL(src);
    src = URL.createObjectURL(f);
    img.onload = function () { w = img.naturalWidth; h = img.naturalHeight; img.style.width = w + "px"; img.style.height = h + "px"; open(); };
    img.onerror = function () { file.value = ""; say("That picture couldn't be opened. Try another one.", true); };
    img.src = src;
  });
  // Without the cropper's Save (for example if the picture never loaded), the form still works as it is.
  form.addEventListener("submit", function (e) { if (!file.files || !file.files.length) e.preventDefault(); });

  zoom.addEventListener("input", function () { zoomTo(parseFloat(zoom.value)); });

  // Dragging with a mouse or one finger moves the photo; two fingers pinch to zoom.
  var points = {}, pinch = null;
  function local(e) { var r = stage.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  stage.addEventListener("pointerdown", function (e) {
    stage.setPointerCapture(e.pointerId);
    points[e.pointerId] = local(e);
    var ids = Object.keys(points);
    if (ids.length === 2) {
      var a = points[ids[0]], b = points[ids[1]];
      pinch = {d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: z};
    }
  });
  stage.addEventListener("pointermove", function (e) {
    if (!points[e.pointerId]) return;
    var p = local(e), last = points[e.pointerId];
    points[e.pointerId] = p;
    var ids = Object.keys(points);
    if (ids.length === 2 && pinch) {
      var a = points[ids[0]], b = points[ids[1]];
      zoomTo(pinch.z * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    } else if (ids.length === 1) {
      x += p[0] - last[0]; y += p[1] - last[1];
      draw();
    }
  });
  function lift(e) { delete points[e.pointerId]; if (Object.keys(points).length < 2) pinch = null; }
  stage.addEventListener("pointerup", lift);
  stage.addEventListener("pointercancel", lift);
  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    var p = local(e);
    zoomTo(z * Math.exp(-e.deltaY * 0.0015), p[0], p[1]);
  }, {passive: false});
  stage.addEventListener("keydown", function (e) {
    var step = e.shiftKey ? 24 : 6;
    if (e.key === "ArrowLeft") x += step; else if (e.key === "ArrowRight") x -= step;
    else if (e.key === "ArrowUp") y += step; else if (e.key === "ArrowDown") y -= step;
    else if (e.key === "+" || e.key === "=") return zoomTo(z * 1.1);
    else if (e.key === "-" || e.key === "_") return zoomTo(z / 1.1);
    else return;
    e.preventDefault();
    draw();
  });
  window.addEventListener("resize", function () { if (!crop.hidden && stage.clientWidth !== S) reset(); });

  crop.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key !== "Tab") return;
    // Keep focus inside the pop-up.
    var f = [].slice.call(crop.querySelectorAll("button, input, [tabindex='0']"));
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  crop.querySelector(".cropbg").addEventListener("click", close);
  crop.querySelector('[data-crop="cancel"]').addEventListener("click", close);
  crop.querySelector('[data-crop="reset"]').addEventListener("click", reset);

  save.addEventListener("click", function () {
    var canvas = document.createElement("canvas");
    canvas.width = canvas.height = OUT;
    paint(canvas, OUT);
    save.disabled = true;
    save.textContent = "Saving…";
    canvas.toBlob(function (blob) {
      var data = new FormData();
      data.append("csrf", form.querySelector('[name="csrf"]').value);
      data.append("photo", blob, "picture.jpg");
      fetch("/account/avatar", {method: "POST", body: data, credentials: "same-origin", headers: {Accept: "application/json"}})
        .then(function (r) { return r.json().catch(function () { return {ok: false}; }); })
        .then(function (reply) {
          if (!reply.ok) throw new Error(reply.message || "");
          show(reply.url);
          close();
          say(reply.message);
        })
        .catch(function (err) { say(err.message || "The picture couldn't be saved. Check your connection and try again.", true); close(); })
        .then(function () { save.disabled = false; save.textContent = "Save picture"; });
    }, "image/jpeg", 0.9);
  });

  // The new picture pops in here and by the name in the menu, without reloading the page.
  function show(url) {
    document.querySelectorAll(".pfp-pic .av, .me .av").forEach(function (av) {
      var pic = document.createElement("img");
      pic.alt = ""; pic.src = url;
      pic.width = pic.height = av.closest(".pfp-pic") ? 96 : 38;
      av.textContent = "";
      av.appendChild(pic);
      av.classList.remove("pop");
      void av.offsetWidth;
      if (!still) av.classList.add("pop");
    });
    var label = form.querySelector("label.btn");
    if (label) label.textContent = "Change picture";
    if (!box.querySelector(".pfp-rm")) {
      var rm = document.createElement("form");
      rm.method = "post"; rm.action = "/account/avatar/remove"; rm.className = "pfp-rm";
      var c = document.createElement("input");
      c.type = "hidden"; c.name = "csrf"; c.value = form.querySelector('[name="csrf"]').value;
      var b = document.createElement("button");
      b.className = "btn"; b.textContent = "Remove picture";
      rm.appendChild(c); rm.appendChild(b);
      box.querySelector(".pfp-acts").appendChild(rm);
    }
  }
})();
