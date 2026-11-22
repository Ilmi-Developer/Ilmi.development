"""Maps the Journey 2 Jannah (J2J) admin area, so the nightly reader knows where attendance lives.

It runs once, on Railway (j2j_sync/Dockerfile): signs in at J2J_LOGIN_URL with J2J_USERNAME and J2J_PASSWORD, then
only opens pages, the way a person reading the admin area would. It never presses a button or sends a form other than
the sign-in, and it skips any link that could change something (log out, delete, export, links with a nonce).

For each page it prints one line to the logs, starting "J2J-MAP ", with what the reader needs: the address, the
headings, each table's column names and how many rows it has, the forms and filters, and the data requests the page
makes (WordPress REST or admin-ajax). Pupil names and other free text are never printed: a value is shown only when it
is a date, a number or a short status word ("present", "absent"); anything else becomes "[text]".

Variables: J2J_USERNAME, J2J_PASSWORD (set by the school in Railway), J2J_LOGIN_URL (default below), J2J_PAGES (extra
paths to open, comma separated), J2J_MAX_PAGES (default 40).
"""
import json
import os
import re
import sys
import time
from collections import deque
from urllib.parse import parse_qsl, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

LOGIN_URL = os.environ.get("J2J_LOGIN_URL", "https://my.journey2jannah.com/login/")
MAX_PAGES = int(os.environ.get("J2J_MAX_PAGES", "40"))
SKIP = re.compile(r"log-?out|logoff|sign-?out|delete|remove|trash|destroy|_wpnonce|nonce=|action=|export|download|"
                  r"\.(pdf|csv|xlsx?|zip|png|jpe?g|gif|webp|mp4|mp3)(\?|$)|^mailto:|^tel:|^javascript:", re.I)
DATE = re.compile(r"^(\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?|\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?|"
                  r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*,? ?\d{0,2}|\d{1,2}(st|nd|rd|th)? "
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*( \d{2,4})?|"
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(st|nd|rd|th)?,?( \d{2,4})?|"
                  r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*,? \d{1,2}(st|nd|rd|th)? "
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*( \d{2,4})?)$", re.I)
NUMBER = re.compile(r"^[-+]?\d[\d,.]*\s?%?$|^\d+\s?/\s?\d+$")
STATUS = {"present", "absent", "late", "excused", "authorised", "unauthorised", "yes", "no", "y", "n", "p", "a", "l",
          "x", "✓", "✔", "✗", "✘", "-", "–", "n/a", "na", "none", "done", "complete", "completed", "incomplete",
          "pending", "attended", "missed", "on time", "active", "inactive", "true", "false"}
# Words that make a heading or menu label safe to print as it is (they name a page, not a person).
PAGE_WORDS = re.compile(r"attend|register|weekend|class|group|cohort|student|pupil|child|parent|famil|report|dashboard|"
                        r"setting|course|lesson|progress|practice|quiz|badge|subscription|account|profile|teacher|"
                        r"school|admin|home|overview|summary|member|user|level|week|term|date|track|log|history|"
                        r"search|filter|result|score|activity|message|notification|help|^(name|full name|first name|"
                        r"last name|surname|forename|email|status|total|actions?|id|age|gender|phone|plan|joined|"
                        r"last seen|points|minutes|time|day|edit|view|present|absent|notes?|#)$", re.I)


def say(kind, data):
    print("J2J-MAP " + json.dumps({"kind": kind, **data}, ensure_ascii=False, separators=(",", ":")), flush=True)


def safe(text):
    """A value as it may be printed: dates, numbers and status words as they are, anything else as [text]."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return ""
    if DATE.match(text) or NUMBER.match(text) or text.lower() in STATUS:
        return text
    if re.fullmatch(r"(yr|year)\s?\d{1,2}\b.{0,12}", text, re.I) and not re.search(r"[a-z]{3,} [A-Z][a-z]+$", text):
        return text  # a class or year group such as "Yr3-A"
    return f"[text {len(text.split())}w]"


def label(text):
    """A heading, column name or menu label: printed when it names a page, else hidden like a value."""
    text = re.sub(r"\s+", " ", (text or "")).strip()[:80]
    if not text:
        return ""
    return text if PAGE_WORDS.search(text) or DATE.match(text) or NUMBER.match(text) else safe(text)


def shape(value, depth=0):
    """The structure of a JSON value with no values in it: key names and types, the first item of a list."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [f"list of {len(value)}"] + ([shape(value[0], depth + 1)] if value else [])
    if isinstance(value, str):
        return "date" if DATE.match(value) else "number-like" if NUMBER.match(value) else "text"
    return type(value).__name__


def where(url):
    """A link as it may be printed: its path, digits shown as {n}, and its query's keys (values only when safe)."""
    parts = urlparse(url)
    path = re.sub(r"/\d+(?=/|$)", "/{n}", parts.path)
    query = [f"{k}={v if safe(v) == v else '…'}" for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return path + ("?" + "&".join(query) if query else "")


PAGE_SCRIPT = r"""
() => {
  const txt = el => (el && el.innerText || "").replace(/\s+/g, " ").trim();
  const navLinks = [...document.querySelectorAll("nav a, header a, aside a, [class*=menu] a, [class*=nav] a, [class*=sidebar] a")]
    .map(a => ({text: txt(a), href: a.href})).filter(a => a.href);
  const allLinks = [...document.querySelectorAll("a[href]")].map(a => ({text: txt(a), href: a.href}));
  const tables = [...document.querySelectorAll("table")].slice(0, 6).map(t => {
    const rows = [...t.querySelectorAll("tr")];
    let head = [...t.querySelectorAll("thead th, thead td")].map(txt);
    if (!head.length && rows.length) head = [...rows[0].querySelectorAll("th")].map(txt);
    const body = rows.filter(r => !r.closest("thead") && !r.querySelector("th:only-child")).map(r => [...r.querySelectorAll("td")].map(c => {
      const box = c.querySelector("input[type=checkbox], input[type=radio]");
      return box ? (box.checked ? "✓" : "✗") : (c.querySelector("select") ? "[select]" : txt(c));
    })).filter(r => r.length);
    return {cls: t.className || "", head, rows: body.length, sample: body.slice(0, 3)};
  });
  const forms = [...document.querySelectorAll("form")].slice(0, 6).map(f => ({
    method: (f.method || "get").toLowerCase(), action: f.action || "",
    fields: [...f.querySelectorAll("input, select, textarea")].slice(0, 30).map(i => ({
      tag: i.tagName.toLowerCase(), type: i.type || "", name: i.name || i.id || "",
      label: txt(i.labels && i.labels[0]) || i.placeholder || "",
      options: i.tagName === "SELECT" ? [...i.options].map(o => txt(o)) : undefined}))}));
  const lists = [...document.querySelectorAll("ul, ol, [class*=list], [class*=grid], [class*=card]")].filter(l => l.children.length >= 5)
    .slice(0, 4).map(l => ({cls: (l.className || "").toString().slice(0, 60), items: l.children.length}));
  const words = (document.body.innerText || "").toLowerCase();
  const count = w => (words.match(new RegExp(w, "g")) || []).length;
  return {title: document.title, headings: [...document.querySelectorAll("h1, h2, h3")].slice(0, 15).map(txt),
          navLinks, allLinks, tables, forms, lists,
          words: {attendance: count("attend"), present: count("present"), absent: count("absent"),
                  weekend: count("weekend"), register: count("register"), saturday: count("saturday"), sunday: count("sunday")}};
}
"""


def sign_in(page):
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
    user = page.locator("input[name=log], #user_login, input[type=email], input[name*=user i], input[name*=email i]").first
    password = page.locator("input[type=password]").first
    user.fill(os.environ["J2J_USERNAME"])
    password.fill(os.environ["J2J_PASSWORD"])
    submit = page.locator("#wp-submit, button[type=submit], input[type=submit]").first
    with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
        submit.click()
    page.wait_for_timeout(2500)
    stuck = page.locator("input[type=password]").count() > 0 and "login" in page.url.lower()
    problem = page.locator(".login_error, #login_error, .error, [role=alert]").first
    message = problem.inner_text(timeout=1000) if problem.count() else ""
    return not stuck, re.sub(r"\s+", " ", message).strip()[:160]


def main():
    if not os.environ.get("J2J_USERNAME") or not os.environ.get("J2J_PASSWORD"):
        say("waiting", {"note": "Set J2J_USERNAME and J2J_PASSWORD on this service in Railway; it runs again by itself."})
        return 0
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-GB",
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                                 "Chrome/131.0 Safari/537.36 Ilmi-J2J-map")
        page = context.new_page()
        requests = []

        def on_response(response):
            request = response.request
            url = response.url
            if request.resource_type not in ("xhr", "fetch") and "wp-json" not in url and "admin-ajax" not in url:
                return
            item = {"method": request.method, "where": where(url), "status": response.status,
                    "type": (response.headers.get("content-type") or "").split(";")[0]}
            if request.method == "POST" and request.post_data:
                item["sends"] = sorted({k if k != "action" else f"action={v}" for k, v in parse_qsl(request.post_data)})[:20]
            if "json" in item["type"]:
                try:
                    item["shape"] = shape(response.json())
                except Exception:  # noqa: BLE001 - a body that isn't JSON after all
                    pass
            requests.append(item)

        page.on("response", on_response)
        try:
            ok, message = sign_in(page)
        except PlaywrightTimeout:
            say("sign-in", {"ok": False, "note": "The sign-in page didn't load or had no sign-in form.", "at": where(page.url)})
            return 1
        say("sign-in", {"ok": ok, "landed": where(page.url), "title": label(page.title()),
                        "message": safe(message) if message else "",
                        "code_or_captcha": bool(page.locator("iframe[src*=captcha], .g-recaptcha, .h-captcha, "
                                                             "input[name*=code i], input[name*=otp i]").count())})
        if not ok:
            return 1
        host = urlparse(page.url).netloc
        queue = deque([(page.url, 0)])
        for extra in filter(None, (x.strip() for x in os.environ.get("J2J_PAGES", "").split(","))):
            queue.append((urljoin(page.url, extra), 0))
        seen, done = set(), 0
        while queue and done < MAX_PAGES:
            url, depth = queue.popleft()
            key = url.split("#")[0]
            if key in seen or urlparse(key).netloc != host or SKIP.search(key):
                continue
            seen.add(key)
            requests.clear()
            try:
                page.goto(key, wait_until="networkidle", timeout=45000)
            except PlaywrightTimeout:
                say("page", {"where": where(key), "note": "didn't finish loading in 45 seconds"})
                continue
            info = page.evaluate(PAGE_SCRIPT)
            done += 1
            nav = []
            for a in info["navLinks"]:
                if urlparse(a["href"]).netloc == host and not SKIP.search(a["href"]):
                    nav.append({"text": label(a["text"]), "where": where(a["href"])})
            say("page", {
                "where": where(page.url), "title": label(info["title"]), "headings": [label(h) for h in info["headings"]],
                "menu": nav[:40] if depth == 0 else len(nav),
                "tables": [{"head": [label(h) for h in t["head"]], "rows": t["rows"], "cls": t["cls"][:60],
                            "sample": [[safe(c) for c in row[:14]] for row in t["sample"]]} for t in info["tables"]],
                "forms": [{"method": f["method"], "action": where(f["action"]) if f["action"] else "",
                           "fields": [{"tag": x["tag"], "type": x["type"], "name": x["name"], "label": label(x["label"]),
                                       **({"options": len(x["options"]),
                                           "first": [safe(o) for o in x["options"][:6]]} if x.get("options") is not None else {})}
                                      for x in f["fields"]]} for f in info["forms"]],
                "lists": info["lists"], "words": info["words"],
                "data_requests": requests[:25]})
            if depth < 2:
                for a in info["allLinks"]:
                    href = a["href"].split("#")[0]
                    if urlparse(href).netloc == host and href not in seen and not SKIP.search(href):
                        queue.append((href, depth + 1))
            page.wait_for_timeout(1200)  # gently: one page at a time
        say("done", {"pages": done, "left_unvisited": len(queue), "seconds": round(time.time() - started)})
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
