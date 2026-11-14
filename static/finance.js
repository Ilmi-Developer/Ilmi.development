// The Finance report's Print button (finance.py serves this at /finance/finance.js). Without the script the
// button stays hidden and the browser's own Print still uses the report's print layout (static/auth.css).
document.querySelectorAll("[data-print]").forEach(function (b) {
  b.hidden = false;
  b.addEventListener("click", function () { window.print(); });
});
