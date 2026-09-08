"""Make a wrapped third-party SPA wear the live Omarchy desktop theme.

The SPAs (OpenCode's web UI, the Hermes dashboard) can't be patched — they
belong to other projects. So the theming happens in front of them: a thin
reverse proxy that forwards every request to the real backend and, on the HTML
document, injects one extra stylesheet — `/__omarchy.css` — built live from
`~/.local/state/omarchy/current/theme/`. Change the desktop theme with
`omarchy theme set` and the app repaints on the next reload.

Stdlib only. Shared verbatim by omacode and hermcode.

    serve(listen=7795, backend=7796, app="opencode")

Optionally it also holds a login for a backend whose only auth is HTTP Basic
(OpenCode's is): pass a `CookieGate` and the proxy serves its own login page,
sets a signed cookie that survives an app-switch, and injects the Basic header
upstream itself — so a phone browser stops re-prompting every time you leave
the tab. hermes has real sessions and passes no gate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import os
import re
import select
import socket
import socketserver
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_THEME = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) \
    / "omarchy" / "current" / "theme"
THEME_ROUTE = "/__omarchy.css"


# -- read the live theme ------------------------------------------------- #
def _flat_toml(text: str) -> dict[str, str]:
    """Omarchy's simple TOML. Strip the comment only after a quoted value is
    matched — colour values are `"#rrggbb"` and a naive split on `#` eats them."""
    out: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            continue
        m = re.match(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"', s) \
            or re.match(r'([A-Za-z0-9_-]+)\s*=\s*([^#\s]+)', s)
        if m:
            out[f"{section}.{m.group(1)}" if section else m.group(1)] = m.group(2)
    return out


def _palette() -> dict[str, str] | None:
    try:
        c = _flat_toml((_THEME / "colors.toml").read_text())
    except OSError:
        return None
    need = ("background", "foreground", "accent")
    if not all(k in c for k in need):
        return None
    return {
        "bg": c["background"],
        "bg_dim": c.get("dark_background", c["background"]),
        "bg_lift": c.get("lighter_background", c["background"]),
        "fg": c["foreground"],
        "fg_dim": c.get("dark_foreground", c.get("muted", c["foreground"])),
        "muted": c.get("muted", c.get("dark_foreground", c["foreground"])),
        "accent": c["accent"],
        "sel": c.get("selection", c.get("lighter_background", c["background"])),
        "mode": c.get("mode", "dark"),
    }


def _rounding() -> str:
    try:
        m = re.search(r"\brounding\s*=\s*(\d+)", (_THEME / "hyprland.lua").read_text())
        return f"{m.group(1)}px" if m else "0px"
    except OSError:
        return "0px"


# -- per-app token overrides ------------------------------------------------- #
# Each app derives everything from a handful of semantic tokens; set those and
# the rest of its scale recomputes. Verified against the shipped CSS.
def _rules(app: str, p: dict[str, str]) -> str:
    r = _rounding()
    if app == "opencode":
        return f"""
:root, [data-theme] {{
  color-scheme: {p['mode']};
  --background-base: {p['bg']} !important;
  --background-strong: {p['bg_dim']} !important;
  --background-weak: {p['bg_lift']} !important;
  --surface-base: {p['bg_lift']} !important;
  --surface-float-base: {p['bg_lift']} !important;
  --surface-hover: {p['sel']} !important;
  --border-base: {p['fg']}26 !important;
  --border-color: {p['fg']}26 !important;
  --border-hover: {p['fg']}40 !important;
  --border-focus: {p['accent']} !important;
  --surface-brand-base: {p['accent']} !important;
  --surface-brand-hover: {p['accent']} !important;
  --text-base: {p['fg']} !important;
  --content-base: {p['fg']} !important;
  --radius-xs: {r}; --radius-sm: {r}; --radius-md: {r};
  --radius-lg: {r}; --radius-xl: {r};
}}
body {{ background: {p['bg']}; color: {p['fg']}; }}
"""
    if app == "hermes":
        return f"""
:root, :host, .dark {{
  color-scheme: {p['mode']};
  --background: {p['bg']} !important;
  --background-base: {p['bg']} !important;
  --background-alpha: {p['bg']} !important;
  --foreground-base: {p['fg']} !important;
  --midground-base: {p['muted']} !important;
  --midground: {p['fg_dim']} !important;
  --color-background: {p['bg']} !important;
  --color-foreground: {p['fg']} !important;
  --color-card: {p['bg_lift']} !important;
  --color-popover: {p['bg_lift']} !important;
  --color-muted: {p['sel']} !important;
  --color-muted-foreground: {p['muted']} !important;
  --color-border: {p['fg']}22 !important;
  --color-input: {p['fg']}22 !important;
  --color-primary: {p['accent']} !important;
  --color-primary-foreground: {p['bg']} !important;
  --color-accent: {p['accent']} !important;
  --color-accent-foreground: {p['bg']} !important;
  --color-ring: {p['accent']} !important;
  --radius: {r}; --radius-sm: {r}; --radius-md: {r}; --radius-lg: {r};
}}
body {{ background: {p['bg']}; color: {p['fg']}; }}

/* Hermes' mobile nav drawer (fixed, z-50, w-64, full height) computes to a
   TRANSPARENT background — same bug in the un-themed dashboard, it just shows
   worse when the page behind is the same black. Force it to a lifted panel,
   and make the scrim a real dark layer. */
.fixed.z-50.w-64,
[class~="fixed"][class~="z-50"][class~="w-64"] {{
  background-color: {p['bg_lift']} !important;
}}
[class~="fixed"][class~="inset-0"][class~="z-40"],
[class~="fixed"][class~="inset-0"][class~="z-30"] {{
  background-color: {p['bg_dim']}d9 !important;
}}
"""
    return ""


def theme_css(app: str) -> str:
    p = _palette()
    if not p:
        return "/* no Omarchy theme found — app keeps its own */\n"
    return ("/* generated live from ~/.local/state/omarchy/current/theme/ */\n"
            + _rules(app, p))


# -- optional cookie login in front of a Basic-only backend --------------- #
class CookieGate:
    """A signed-cookie session that fronts an HTTP-Basic backend.

    The browser authenticates once against a login page here; the proxy holds
    the Basic credential and adds it to every upstream request. The cookie is
    an HMAC over its issue time, so it needs no server-side store and survives
    a proxy restart (the secret is stable). Backgrounding the tab no longer
    drops the login the way a Basic credential does.
    """

    def __init__(self, secret: str, password: str, username: str,
                 prefix: str = "/__gate", title: str = "sign in",
                 max_age_days: int = 14):
        self._secret = secret.encode()
        self.password = password
        self.prefix = prefix
        self.title = title
        self.max_age = max_age_days * 86400
        self.upstream_auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode()).decode()

    def _sign(self, msg: bytes) -> str:
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:32]

    def mint(self) -> str:
        ts = str(int(time.time()))
        return f"{ts}.{self._sign(ts.encode())}"

    def valid(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        ts, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(ts.encode())):
            return False
        try:
            return (time.time() - int(ts)) < self.max_age
        except ValueError:
            return False

    def token_from_cookie(self, header: str) -> str | None:
        for part in (header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                return v
        return None

    def password_ok(self, attempt: str) -> bool:
        return bool(self.password) and hmac.compare_digest(attempt, self.password)

    def login_html(self, error: bool = False) -> bytes:
        p = _palette() or {"bg": "#0e0e10", "fg": "#e8e8ea", "accent": "#e8e8ea",
                           "bg_lift": "#17171a", "muted": "#8b8b93", "mode": "dark"}
        r = _rounding()
        msg = ('<p class="e">that password didn’t match</p>' if error else "")
        return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{self.title}</title>
<style>
 :root {{ color-scheme: {p['mode']}; }}
 body {{ margin:0; min-height:100vh; display:grid; place-items:center;
   background:{p['bg']}; color:{p['fg']};
   font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }}
 form {{ background:{p['bg_lift']}; border:1px solid {p['fg']}22; border-radius:{r};
   padding:28px; width:min(88vw,320px); display:flex; flex-direction:column; gap:12px; }}
 h1 {{ font-size:1rem; margin:0 0 4px; font-weight:600; }}
 input {{ font:inherit; padding:9px 11px; border-radius:{r};
   border:1px solid {p['fg']}33; background:{p['bg']}; color:{p['fg']}; }}
 input:focus {{ outline:none; border-color:{p['accent']}; }}
 button {{ font:inherit; padding:9px; border-radius:{r}; border:1px solid {p['accent']};
   background:{p['accent']}; color:{p['bg']}; font-weight:600; cursor:pointer; }}
 .m {{ color:{p['muted']}; font-size:.8rem; }}
 .e {{ color:#e07a7a; font-size:.8rem; margin:0; }}
</style>
<form method=post action="{self.prefix}/login">
 <h1>{self.title}</h1>
 {msg}
 <input type=password name=password placeholder=password autofocus autocomplete=current-password>
 <button type=submit>sign in</button>
 <p class=m>tailnet + this password. stays signed in on this device.</p>
</form>""".encode()


# -- the proxy -------------------------------------------------------------- #
_INJECT = ('<link rel="stylesheet" href="' + THEME_ROUTE + '">'
           '<script>try{var m=document.documentElement;'
           'm.setAttribute("data-theme","%s");m.classList.add("%s")}catch(e){}</script>')
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer",
        "upgrade", "proxy-authorization", "proxy-authenticate"}


def _make_handler(backend: int, app: str, gate: "CookieGate | None" = None):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # -- cookie gate ------------------------------------------------- #
        def _redirect(self, to, cookie=None):
            self.send_response(303)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _send_html(self, body: bytes, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _cookie(self, value, clear=False):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            if clear:
                return f"session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"
            return (f"session={value}; Path=/; HttpOnly; SameSite=Lax; "
                    f"Max-Age={gate.max_age}{secure}")

        def _gate(self) -> bool:
            """Return True if the request may proceed. Handles login/logout and
            denies unauthenticated requests itself."""
            if gate is None:
                return True
            path = self.path.split("?", 1)[0]
            if path == gate.prefix + "/login":
                if self.command == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
                    if gate.password_ok((form.get("password") or [""])[0]):
                        self._redirect("/", self._cookie(gate.mint()))
                    else:
                        self._send_html(gate.login_html(error=True), status=401)
                else:
                    self._send_html(gate.login_html())
                return False
            if path == gate.prefix + "/logout":
                self._redirect(gate.prefix + "/login", self._cookie("", clear=True))
                return False
            if gate.valid(gate.token_from_cookie(self.headers.get("Cookie", ""))):
                return True
            # not signed in — send a browser navigation to the login page, but
            # answer an API/asset/XHR call with a plain 401
            nav = self.command in ("GET", "HEAD") and (
                "text/html" in self.headers.get("Accept", "")
                or self.headers.get("Sec-Fetch-Dest") == "document")
            if nav:
                self._redirect(gate.prefix + "/login")
            else:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return False

        def _serve_theme(self):
            body = theme_css(app).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _tunnel(self):
            """Raw bidirectional pipe for a WebSocket upgrade — the chat uses
            one, and http.client can't proxy it. Replay the request verbatim to
            the backend, then pump bytes until either side closes."""
            try:
                up = socket.create_connection(("127.0.0.1", backend), timeout=10)
            except OSError as e:
                self.send_error(502, f"backend ws: {e}")
                return
            req = [f"{self.command} {self.path} {self.request_version}"]
            for k, v in self.headers.items():
                if gate is not None and k.lower() in ("authorization", "cookie"):
                    continue                       # the gate owns auth; cookie stays here
                req.append(f"{k}: {v}")
            if gate is not None:
                req.append(f"Authorization: {gate.upstream_auth}")
            up.sendall(("\r\n".join(req) + "\r\n\r\n").encode())
            cli = self.connection
            cli.setblocking(False)
            up.setblocking(False)
            try:
                while True:
                    r, _, x = select.select([cli, up], [], [cli, up], 300)
                    if x or not r:
                        break
                    for s in r:
                        try:
                            data = s.recv(65536)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            return
                        if not data:
                            return
                        (up if s is cli else cli).sendall(data)
            finally:
                up.close()
                self.close_connection = True

        def _proxy(self, method):
            if self.path.split("?", 1)[0] == THEME_ROUTE:
                return self._serve_theme()
            if not self._gate():
                return
            if self.headers.get("Upgrade", "").lower() == "websocket":
                return self._tunnel()
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection("127.0.0.1", backend, timeout=120)
            _drop = _HOP | ({"authorization", "cookie"} if gate is not None else set())
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in _drop}
            hdrs["Host"] = f"127.0.0.1:{backend}"
            if gate is not None:
                hdrs["Authorization"] = gate.upstream_auth
            try:
                conn.request(method, self.path, body=payload, headers=hdrs)
                resp = conn.getresponse()
            except OSError as e:
                self.send_error(502, f"backend: {e}")
                conn.close()
                return

            ctype = resp.getheader("Content-Type", "")
            # OpenCode's live event feed is Server-Sent Events — an endless
            # response. Buffering it with .read() would hang the request
            # forever, so stream it straight through instead.
            if "text/event-stream" in ctype:
                if conn.sock:
                    conn.sock.settimeout(None)      # the stream has no deadline
                self.close_connection = True
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in _HOP | {"content-length", "connection"}:
                        self.send_header(k, v)
                self.send_header("Connection", "close")   # length-delimited by EOF
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    while True:
                        chunk = resp.read1(2048)     # one chunk, don't wait for more
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    conn.close()
                return

            try:
                raw = resp.read()
            finally:
                conn.close()

            pal = _palette()
            if pal and "text/html" in ctype and b"</head>" in raw:
                inj = (_INJECT % (pal["mode"], pal["mode"])).encode()
                raw = raw.replace(b"</head>", inj + b"</head>", 1)

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in _HOP or lk == "content-length":
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(raw)

        do_GET = lambda s: s._proxy("GET")
        do_POST = lambda s: s._proxy("POST")
        do_PUT = lambda s: s._proxy("PUT")
        do_PATCH = lambda s: s._proxy("PATCH")
        do_DELETE = lambda s: s._proxy("DELETE")
        do_HEAD = lambda s: s._proxy("HEAD")
        do_OPTIONS = lambda s: s._proxy("OPTIONS")

    return H


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(listen: int, backend: int, app: str, gate: "CookieGate | None" = None):
    srv = _Server(("127.0.0.1", listen), _make_handler(backend, app, gate))
    extra = "  + cookie login" if gate is not None else ""
    print(f"omarchy-theme proxy: 127.0.0.1:{listen} -> :{backend} ({app}){extra}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
