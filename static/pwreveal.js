// Show or hide a password: every password field on the page gets an eye button at its right end. The eye is shut
// (a lid with lashes) while the password is hidden and opens, with a short blink, when it is shown. The field keeps
// its focus and caret, so typing carries on. Without this script the fields are ordinary password fields.
(function () {
  var EYE = '<svg class="pweye" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<g class="pwopen"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle class="pwpupil" cx="12" cy="12" r="3"/></g>'
    + '<g class="pwshut"><path d="M2.5 11c2.4 3.6 5.6 5.4 9.5 5.4s7.1-1.8 9.5-5.4"/><path d="M5.6 14.6 4 16.6M9.4 16.1 8.8 18.6M14.6 16.1l.6 2.5M18.4 14.6l1.6 2"/></g>'
    + '</svg>';
  function add(input) {
    if (input.dataset.pw) return;
    input.dataset.pw = "1";
    var wrap = input.parentNode;
    if (!wrap.classList || !wrap.classList.contains("fin")) {
      wrap = document.createElement("span");
      wrap.className = "fin";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
    }
    wrap.classList.add("haspw");
    var button = document.createElement("button");
    button.type = "button";
    button.className = "pwbtn";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Show password");
    button.title = "Show password";
    button.innerHTML = EYE;
    // Pressing the button mustn't take focus from the field (the caret stays where it was).
    button.addEventListener("mousedown", function (e) { e.preventDefault(); });
    button.addEventListener("click", function (e) {
      e.preventDefault();
      var show = input.type === "password";
      var start = input.selectionStart, end = input.selectionEnd;
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", show ? "true" : "false");
      button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      button.title = show ? "Hide password" : "Show password";
      button.classList.toggle("on", show);
      button.classList.remove("blink");
      void button.offsetWidth; // restart the blink
      button.classList.add("blink");
      if (document.activeElement === input) {
        try { input.setSelectionRange(start, end); } catch (err) {}
      }
    });
    wrap.appendChild(button);
  }
  function run() {
    var fields = document.querySelectorAll('input[type="password"]');
    for (var i = 0; i < fields.length; i++) add(fields[i]);
    // A shown password is hidden again before the form goes, so the browser's password manager sees a password field.
    var forms = document.querySelectorAll("form");
    for (var j = 0; j < forms.length; j++) forms[j].addEventListener("submit", function () {
      var shown = this.querySelectorAll("input[data-pw]");
      for (var k = 0; k < shown.length; k++) shown[k].type = "password";
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run); else run();
})();
