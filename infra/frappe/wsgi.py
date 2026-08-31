"""WSGI wrapper that applies static middleware before gunicorn workers serve.

`frappe.app:application` bypasses Frappe's static middleware (which is only
installed in `frappe.app.serve()` for the werkzeug dev server). This wrapper
applies the same static middleware so `/assets/*` and `/files/*` are served
directly from sites/assets instead of going through the WSGI app for every
static request.

It also adds a SPA fallback: the WebODM frontend is a Vue app using history
mode with base `/assets/webodm_frontend/frontend/`. Requests for that base that
do not resolve to a real file are served the app's `index.html` so client-side
routes (e.g. `/login`, `/map`) work on reload.
"""
import os

os.environ.setdefault("SITES_PATH", "/workspace/frappe-bench/sites")

# Import frappe.app explicitly so `frappe.app.application` is available.
# Do NOT call frappe.init() at module level — frappe.app.application handles
# per-request init using the SITES_PATH env var set above.
import frappe  # noqa: F401
import frappe.app as frappe_app  # noqa: F401

from werkzeug.middleware.shared_data import SharedDataMiddleware
from frappe.middlewares import StaticDataMiddleware

SITES_PATH = os.environ["SITES_PATH"]
SPA_ASSET_PREFIX = "/assets/webodm_frontend/frontend/"


class SpaFallback:
    """Serve the SPA index.html for history-mode routes under its asset base."""

    def __init__(self, app, prefix, sites_path):
        self.app = app
        self.prefix = prefix
        self.index_path = os.path.join(
            sites_path, "assets", "webodm_frontend", "frontend", "index.html"
        )

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith(self.prefix):
            rel = path[len(self.prefix) :]
            if not rel or "." not in os.path.basename(rel):
                # A route (no extension in the final segment) that isn't a real
                # file — serve index.html so the Vue router can handle it.
                try:
                    with open(self.index_path, "rb") as f:
                        body = f.read()
                except OSError:
                    return self.app(environ, start_response)
                start_response(
                    "200 OK",
                    [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))],
                )
                return [body]
        return self.app(environ, start_response)


_application = SharedDataMiddleware(
    frappe.app.application,
    {"/assets": str(os.path.join(SITES_PATH, "assets"))},
)
_application = StaticDataMiddleware(
    _application,
    {"/files": SITES_PATH},
)
_application = SpaFallback(_application, SPA_ASSET_PREFIX, SITES_PATH)
application = _application
