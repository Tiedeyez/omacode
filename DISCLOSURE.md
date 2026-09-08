# What omacode does to your computer

omacode is a thin wrapper around OpenCode's own headless server. It exists to
make one specific thing safe: reaching an AI coding agent from your phone. That
agent can run commands and edit files as your user, so the exposure is opt-in,
temporary, and gated twice.

Every path and command below is greppable in this repository.

## What it is

A single stdlib-Python script. It does not contain a server, a UI, or any agent
logic — `opencode serve` provides all of that. omacode mints a password,
installs a systemd unit that runs the server on loopback, and toggles a
`tailscale serve` block in front of it.

It runs with your user account's access, like any script you run.

## What it writes

| Path | When | What |
|---|---|---|
| `$XDG_STATE_HOME/omacode/server.env` | `omacode init` | `OPENCODE_SERVER_PASSWORD=…`, created **0600 from the first byte** |
| `$XDG_STATE_HOME/omacode/config.json` | init / expose | working directory, ports — no secrets |
| `~/.config/systemd/user/omacode.service` | `omacode init` | the unit that runs `opencode serve` on `127.0.0.1` |
| `~/.config/systemd/user/omacode-theme.service` | `omacode init` **on Omarchy only** | the unit that runs the theme proxy on `127.0.0.1`; removed on init if the machine is not Omarchy |

Nothing outside `$HOME`, never `sudo`.

## What it reads

- **Omarchy only:** `~/.local/state/omarchy/current/theme/colors.toml`,
  `shell.toml`, `hyprland.lua` — **read-only**, colours and the corner radius,
  so the wrapped UI matches your desktop theme. On any non-Omarchy machine
  these are never read and there is no theme proxy.

Its own state, and (on Omarchy) the theme files above. It does not read your
projects, documents, or shell history — but note that **the OpenCode agent it
fronts can read and write anything your user can**, within the working
directory you point it at and beyond. That is the whole point of the agent, and
the reason for the locks.

## What other programs it runs

| Command | When |
|---|---|
| `systemctl --user …` | start / stop / status |
| `tailscale serve …` / `tailscale status` | expose / hide / url |
| `opencode serve` | run by the systemd unit, on loopback |
| `omacode proxy` (this script, theme proxy) | Omarchy only — run by a second systemd unit, on loopback |
| `journalctl --user` | `omacode logs` |

No shell is invoked by omacode itself. (OpenCode's agent runs a shell — that is
what it does.)


### The theme proxy — Omarchy only

**Only installed and run if `~/.local/state/omarchy/current/theme/` exists at
`omacode init` time.** On any other machine there is no theme proxy: the tailnet
points straight at the OpenCode server and this section does not apply.

Where it does run: `omacode start` also runs a ~180-line stdlib reverse proxy
on `127.0.0.1:7795`, in front of the backend. It forwards every request
unchanged (auth headers and cookies included) and, on the HTML document only,
injects one extra `<link>` to `/__omarchy.css` — a stylesheet it generates live
from the theme files above. It adds nothing else, stores nothing, opens no
outbound connection, and the backend's auth gate is unaffected (verified: no
creds → 401 through the proxy). `omacode expose` points Tailscale at this proxy;
without it, the raw un-themed backend is served instead.

## Network

The server binds **`127.0.0.1` only**. `omacode expose` puts it on **your
tailnet** via `tailscale serve --https <port>` — a dedicated HTTPS port, because
OpenCode's web app uses absolute asset paths and cannot live under a shared
`/path` mount. It is **never** Funnel; the tailnet is a boundary, the password
is the second.

Hidden by default. `omacode status` always says which it is. `omacode stop` and
`omacode hide` both take it off the tailnet.

omacode makes no outbound connections of its own. OpenCode itself talks to
whichever model provider you have configured — that traffic is OpenCode's, not
this wrapper's, and is covered by OpenCode's own documentation.

## The two locks

1. **Tailnet.** Only devices on your tailnet can reach the port at all.
   `tailscale serve` also injects `Tailscale-User-Login` — your identity, not
   just membership.
2. **HTTP Basic.** User `opencode`, password from `server.env`. OpenCode gates
   *every* route including `/`, so there is no unauthenticated surface.

Lose either and you still have the other. But this drives an agent that acts as
you: treat the password like an SSH key, and run `omacode hide` when you're done
for the day.

## What it does not do

No auto-update. No telemetry. No credential access beyond the password file it
writes. No Funnel, ever. It will not expose the server unless you run `expose`.

## Who maintains it

Tiedeyez. MIT. `omacode` is ~340 lines; `_omarchytheme.py` (Omarchy only) is
~275 more, shared verbatim with hermcode. Read them before you trust this with
a shell.
