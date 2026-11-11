// The sign-in page's opening. The form's fields stay disabled while it plays (a fieldset in login.html), so
// browsers, Safari included, can't offer saved passwords over the animation. When it ends they're enabled and
// the email field takes focus, which is when the saved-password suggestions appear. The dashboard needs
// scripts anyway, so a browser without them couldn't use the site either way.
(function () {
  var hold = document.querySelector("fieldset.hold");
  if (!hold) return;
  var still = false;
  try { still = matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}
  setTimeout(function () {
    hold.disabled = false;
    var field = hold.querySelector("input[name=email]");
    if (field) field.focus({preventScroll: true});
  }, still ? 0 : 4600);
})();
