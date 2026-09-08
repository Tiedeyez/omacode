# What omacode does to your computer

omacode is a thin wrapper around OpenCode's own headless server. It exists to
make one specific thing safe: reaching an AI coding agent from your phone. That
agent can run commands and edit files as your user, so the exposure is opt-in,
temporary, and gated twice.

Every path and command below is greppable in this repository.

## What it is

A single stdlib-Python script plus one proxy helper. It contains no server, no
UI, and no agent logic — `opencode serve` provides all of that. omacode mints a
password, installs two systemd `--user` units (the server on loopback, and a
small proxy in front of it), and toggles a `tailscale serve` block.

It runs with your user account's access, like any script you run.

## What it writes

| Path | When | What |
|---|---|---|
| `$XDG_STATE_HOME/omacode/server.env` | `omacode init` | `OPENCODE_SERVER_PASSWORD=…` and `OMACODE_SESSION_SECRET=…` (signs the proxy's login cookie) — created **0600 from the first byte** |
| `$XDG_STATE_HOME/omacode/config.json` | init / expose | working directory, ports — no secrets |
| `~/.config/systemd/user/omacode.service` | `omacode init` | runs `opencode serve` on `127.0.0.1` |
| `~/.config/systemd/user/omacode-theme.service` | `omacode init` | runs the proxy (`omacode proxy`) on `127.0.0.1` |

Nothing outside `$HOME`, never `sudo`.

## What it reads

- its own `$XDG_STATE_HOME/omacode/` state
- **Omarchy only:** `~/.local/state/omarchy/current/theme/colors.toml`,
  `shell.toml`, `hyprland.lua` — read-only, colours and the corner radius, for
  the theme skin. Never read on a non-Omarchy machine.

It does not read your projects, documents, or shell history — but note that
**the OpenCode agent it fronts can read and write anything your user can**,
within the working directory you point it at and beyond. That is the whole
point of the agent, and the reason for the locks.

## What other programs it runs

| Command | When |
|---|---|
| `systemctl --user …` | start / stop / status |
| `tailscale serve …` / `tailscale status` | expose / hide / url |
| `opencode serve` | run by the systemd unit, on loopback |
| `omacode proxy` (this script) | run by the second systemd unit, on loopback |
| `journalctl --user` | `omacode logs` |

No shell is invoked by omacode itself. (OpenCode's agent runs a shell — that is
what it does.)

## The proxy

`omacode` always runs a ~450-line stdlib reverse proxy on `127.0.0.1:7795`, in
front of the OpenCode server. The tailnet points at this, never the raw
backend. It does exactly three things:

1. **Cookie login.** OpenCode's server only speaks HTTP Basic, and a phone
   browser drops Basic credentials every time you background the tab. The proxy
   serves its own login page at `/__omacode/login`; a correct password sets a
   cookie (`session=<issue-time>.<HMAC-SHA256 over it, truncated>`, `HttpOnly`,
   `SameSite=Lax`, `Secure` when the request arrived over HTTPS, 14-day
   `Max-Age`). No server-side session store — the HMAC *is* the check, so it
   survives a proxy restart. `/__omacode/logout` clears it. A request without a
   valid cookie gets a browser redirect to the login page, or a plain `401` for
   an API/asset call — verified.
2. **Upstream auth.** On a valid cookie, the proxy adds
   `Authorization: Basic <base64(opencode:password)>` to the request it makes to
   the backend, and does **not** forward the browser's own `Authorization` or
   `Cookie` headers upstream. The password is read from `server.env`; it is
   never sent to the browser except on the `omacode url` / login screen you
   asked for.
3. **Theme (Omarchy only).** On the HTML document it injects one `<link>` to
   `/__omarchy.css`, generated live from your desktop theme. Server-Sent-Event
   streams (`text/event-stream`, OpenCode's live feed) are passed straight
   through, not buffered. Nothing is stored; no outbound connection is opened.

## Network

The OpenCode server and the proxy both bind **`127.0.0.1` only**. `omacode
expose` puts the proxy on **your tailnet** via `tailscale serve --https <port>`
— a dedicated HTTPS port, because OpenCode's web app uses absolute asset paths
and cannot live under a shared `/path` mount. It is **never** Funnel.

Hidden by default. `omacode status` always says which it is. `omacode stop` and
`omacode hide` both take it off the tailnet.

omacode makes no outbound connections of its own. OpenCode itself talks to
whichever model provider you have configured — that traffic is OpenCode's, not
this wrapper's, and is covered by OpenCode's own documentation.

## The two locks

1. **Tailnet.** Only devices on your tailnet can reach the port at all.
   `tailscale serve` also injects `Tailscale-User-Login` — your identity, not
   just membership.
2. **The password.** Entered once on the proxy's login page; held after that as
   a signed cookie on your device. The proxy is the only thing that ever sends
   Basic credentials to OpenCode, and it only does so for a request that
   already carries a valid cookie.

Lose either and you still have the other. But this drives an agent that acts as
you: treat the password like an SSH key, and run `omacode hide` when you're done
for the day.

## What it does not do

No auto-update. No telemetry. No credential access beyond the `server.env` it
writes. No Funnel, ever. It will not expose anything unless you run `expose`.
The cookie is signed, not encrypted — it carries only its own issue time, no
identity or payload.

## Who maintains it

Tiedeyez. MIT. `omacode` is ~350 lines; `_omarchytheme.py` (the proxy) is
~450 more, shared verbatim with hermcode. Read them before you trust this with
a shell.
