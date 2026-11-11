"""Sends the dashboard's emails through Resend's HTTPS API.

Railway blocks outbound SMTP on the Hobby plan, so mail goes over HTTPS.
Without RESEND_API_KEY and MAIL_FROM nothing is sent, and admins copy the link
from the Manage users page instead. What each email says is in emails.py and
templates/email/.

Every email has an HTML part and a plain-text part. A failed send is retried
once when Resend is busy or briefly unavailable; the Idempotency-Key header
makes sure a retry can't deliver the same email twice.

MAIL_REDIRECT_TO, when set, sends every email to that one address instead of
the real recipient. Use it while testing so that no staff member is emailed.
MAIL_REPLY_TO (or SUPPORT_EMAIL) is where replies go, since the sending
address doesn't receive mail.
"""
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from html import escape

API_URL = "https://api.resend.com/emails"
# Resend answers 429 when sending too fast and 5xx when briefly unavailable; both are worth one more try.
RETRY_STATUSES = {429, 500, 502, 503, 504}


class Mailer:
    def __init__(self, api_key, sender, redirect_to="", reply_to=""):
        self.api_key = api_key
        self.sender = sender
        self.redirect_to = redirect_to
        self.reply_to = reply_to

    @property
    def enabled(self):
        return bool(self.api_key and self.sender)

    @property
    def sender_address(self):
        """The bare address in MAIL_FROM, for showing on the admin pages."""
        return self.sender.rsplit("<", 1)[-1].rstrip(">").strip() if self.sender else ""

    def send(self, to, subject, text, html=None, tag=None, background=False):
        """Returns True when the email was accepted for delivery.

        With background=True it returns straight away (True if a send was started) and sends from
        another thread, so a page's response time doesn't show whether an email went out.
        """
        if not self.enabled:
            return False
        payload = {"from": self.sender, "to": [to], "subject": subject, "text": text}
        if html:
            payload["html"] = html
        if self.redirect_to:
            note = f"[Test mode: this email was meant for {to}]"
            payload["text"] = note + "\n\n" + text
            if html:
                # templates/email/_layout.html marks where the note goes.
                payload["html"] = html.replace("<!--TEST-->", f'<p style="margin:0 0 16px;font-size:13px;'
                                               f'line-height:1.5;color:#664a0b">{escape(note)}</p>', 1)
            payload["to"] = [self.redirect_to]
        if self.reply_to:
            payload["reply_to"] = self.reply_to
        if tag:
            payload["tags"] = [{"name": "category", "value": tag}]
        if background:
            threading.Thread(target=self._deliver, args=(payload,), daemon=True).start()
            return True
        return self._deliver(payload)

    def _deliver(self, payload):
        body = json.dumps(payload).encode()
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json",
                   "User-Agent": "ilmi-dashboard", "Idempotency-Key": str(uuid.uuid4())}
        to = payload["to"][0]
        for attempt in (1, 2):
            request = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return 200 <= response.status < 300
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace")[:300]
                if attempt == 1 and error.code in RETRY_STATUSES:
                    time.sleep(1.5)
                    continue
                print(f"Email to {to} was not sent ({error.code}): {detail}", flush=True)
                return False
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 1:
                    time.sleep(1.5)
                    continue
                print(f"Email to {to} was not sent: {error}", flush=True)
                return False
        return False
