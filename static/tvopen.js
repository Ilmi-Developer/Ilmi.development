// The Presenter page's Preview button (templates/admin_tv.html): opens the TV preview in a new tab, moves to it, and
// has it go full screen. A new tab can't go full screen by itself, so when it says it is ready this tab hands it the
// button press (capability delegation, where the browser supports it). Elsewhere the preview asks for one click.
(function () {
  var link = document.querySelector("[data-tv-preview]");
  if (!link) return;
  link.addEventListener("click", function (e) {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; // a middle or modified click keeps the browser's own behaviour
    e.preventDefault();
    var url = link.getAttribute("href") + "?full=1";
    var tab = window.open(url, "_blank");
    if (!tab) { location.href = url; return; } // pop-ups blocked: open it here instead
    try { tab.focus(); } catch (err) {}
    function ready(m) {
      if (m.source !== tab || m.origin !== location.origin || m.data !== "ilmi-tv-ready") return;
      window.removeEventListener("message", ready);
      try { tab.postMessage("ilmi-tv-full", {targetOrigin: location.origin, delegate: "fullscreen"}); }
      catch (err) { try { tab.postMessage("ilmi-tv-full", location.origin); } catch (err2) {} }
    }
    window.addEventListener("message", ready);
    setTimeout(function () { window.removeEventListener("message", ready); }, 15000);
  });
})();
