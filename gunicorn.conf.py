import os
import re

from gunicorn.glogging import Logger

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
# Assistant questions can take a while (at most 3 at once, see assistant.py), so leave threads for everything else.
threads = 8
timeout = 30
accesslog = "-"
errorlog = "-"

# Invite and reset links carry a one-time token in the path, and TV screen and memo board links a lasting one.
# Railway keeps these logs, so the token is hidden wherever it appears in a log line: the request line, the path,
# and the Referer of pages it loads. Keep in step with auth.LINK_TOKEN, which does the same for the app's own error log.
LINK_TOKEN = re.compile(r"((?<!/admin)/(?:invite|reset|tv|board)/)[^/?#\s\"]+")


class HideLinkTokens(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        return {k: LINK_TOKEN.sub(r"\1[hidden]", v) if isinstance(v, str) else v for k, v in atoms.items()}


logger_class = HideLinkTokens

# Gunicorn 25.1+ opens a control socket under $HOME by default. The image's app user has no home
# directory, so it would log an error on every start; the app never uses gunicornc, so turn it off.
control_socket_disable = True
