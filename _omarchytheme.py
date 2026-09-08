"""Make a wrapped third-party SPA wear the live Omarchy desktop theme.

The SPAs (OpenCode's web UI, the Hermes dashboard) can't be patched — they
belong to other projects. So the theming happens in front of them: a thin
reverse proxy that forwards every request to the real backend and, on the HTML
document, injects one extra stylesheet — `/__omarchy.css` — built live from
`~/.local/state/omarchy/current/theme/`. Change the desktop theme with
`omarchy theme set` and the app repaints on the next reload.

Stdlib only. Shared verbatim by omacode and hermcode.

    serve(listen=7795, backend=7796, app="opencode")
"""
from __future__ import annotations

import http.client
import os
import re
import socketserver
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
"""
    return ""


def theme_css(app: str) -> str:
    p = _palette()
    if not p:
        return "/* no Omarchy theme found — app keeps its own */\n"
    return ("/* generated live from ~/.local/state/omarchy/current/theme/ */\n"
            + _rules(app, p))


# -- the proxy -------------------------------------------------------------- #
_INJECT = ('<link rel="stylesheet" href="' + THEME_ROUTE + '">'
           '<script>try{var m=document.documentElement;'
           'm.setAttribute("data-theme","%s");m.classList.add("%s")}catch(e){}</script>')
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer",
        "upgrade", "proxy-authorization", "proxy-authenticate"}


def _make_handler(backend: int, app: str):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _serve_theme(self):
            body = theme_css(app).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self, method):
            if self.path == THEME_ROUTE:
                return self._serve_theme()
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection("127.0.0.1", backend, timeout=120)
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            hdrs["Host"] = f"127.0.0.1:{backend}"
            try:
                conn.request(method, self.path, body=payload, headers=hdrs)
                resp = conn.getresponse()
                raw = resp.read()
            except OSError as e:
                self.send_error(502, f"backend: {e}")
                return
            finally:
                conn.close()

            ctype = resp.getheader("Content-Type", "")
            mode = (_palette() or {}).get("mode", "dark")
            if "text/html" in ctype and b"</head>" in raw:
                inj = (_INJECT % (mode, mode)).encode()
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


def serve(listen: int, backend: int, app: str):
    srv = _Server(("127.0.0.1", listen), _make_handler(backend, app))
    print(f"omarchy-theme proxy: 127.0.0.1:{listen} -> :{backend} ({app})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
