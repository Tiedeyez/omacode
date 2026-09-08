# omacode

Reach [OpenCode](https://opencode.ai) from your phone, over your tailnet, safely.

OpenCode already ships a headless server with a full web UI and HTTP Basic auth.
omacode is the small amount of glue between "there is a server" and "I can open
this on my phone without leaving an AI agent that runs commands as me sitting
open on a port":

- mints a strong password (`0600` from the first byte)
- runs `opencode serve` on loopback under a systemd `--user` unit
- puts it on your **tailnet** — `tailscale serve`, never Funnel — only when you
  ask, and tells you when it's exposed

Stdlib Python only. One file plus an optional theme helper. No dependencies.

## Use

```sh
omacode init ~/code       # password + service, pointed at a working directory
omacode start             # server up, loopback only
omacode expose            # onto the tailnet; prints the URL + password
#   … drive it from your phone …
omacode hide              # back off the tailnet
```

- `omacode status` — what's running and whether it's reachable
- `omacode url` — reprint the link and credentials
- `omacode logs` — tail the server
- `omacode init --rotate` — new password

The URL is `https://<your-tailnet-host>:8443` — a dedicated port, because
OpenCode's web app uses absolute asset paths and can't sit under a `/path`
mount. Sign in as `opencode` with the password from `omacode url`.

## Safety

Two locks: the tailnet, and the password. This endpoint drives an agent that
can run commands and edit files as you — a bigger blast radius than most apps —
so exposure is opt-in and one word (`hide`) closes it. See
[DISCLOSURE.md](DISCLOSURE.md) for everything it touches, audited from the
source.

Requires `opencode` and `tailscale` on PATH, and Python 3.11+.

## Omarchy skin (optional)

On an [Omarchy](https://omarchy.org) desktop, `omacode init` also installs a
tiny reverse proxy that repaints OpenCode's UI in your live desktop theme — it
reads `~/.local/state/omarchy/current/theme/` and injects one stylesheet.
Everywhere else this is skipped entirely and the tailnet points straight at the
OpenCode server. `_omarchytheme.py` is shared verbatim with its sibling,
[hermcode](https://github.com/Tiedeyez/hermcode) (the same idea for the Hermes
dashboard).

MIT © Tiedeyez
