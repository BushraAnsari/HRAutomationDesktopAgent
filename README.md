# HR Automation -- Desktop Activity Agent

Companion desktop agent for the HR Automation web app. Starts monitoring
when an employee checks in, records application/idle activity locally,
syncs it back in batches, and stops when they check out. No screenshots,
keystrokes, screen recording, or browser content -- see "What this does
not collect" below.

## How it fits with the web app

```
HRMS Web (check-in) --pairing code--> Desktop Agent (paired, starts monitoring)
                                              |
                                    samples every few seconds
                                              |
                                     aggregated intervals
                                              |
                                    local SQLite queue (offline-safe)
                                              |
                                periodic batch sync --> HRMS API
                                              |
HRMS Web (check-out) <----- agent notices, stops, final sync -----+
```

The agent never trusts its own claim of "which employee am I" -- every
request after pairing is authenticated by a server-issued device token
(see `agent/api_client.py`), the same way the web app's users are
authenticated, just with a distinct token audience so the two can never
be swapped for each other. See the backend's own `authenticateDevice.js`
for the server side of this.

## Project layout

```
agent/
  main.py                   entrypoint -- run with `python -m agent.main`
  config.py                 local config/credentials (per-OS app-data dir)
  api_client.py              HTTP calls to the existing HRMS API
  pairing.py                  one-time pairing-code exchange (native dialog)
  tray.py                      tray/menu-bar icon, status only
  store/queue.py              SQLite local queue (offline durability)
  monitor/
    collector_base.py         the ActivityCollector interface + OS dispatch
    collector_windows.py      real Win32 implementation
    collector_macos.py        real PyObjC implementation
    collector_linux.py        best-effort X11 implementation (architecture-ready)
    aggregator.py             raw samples -> aggregated intervals
  sync/sync_service.py        batch upload + retry
packaging/
  agent.spec                  PyInstaller build spec (.exe / .app)
  build_windows.md
  build_macos.md
requirements*.txt
```

## Running it during development

```bash
python3 -m venv venv
source venv/bin/activate       # venv\Scripts\activate on Windows
pip install -r requirements.txt -r requirements-windows.txt   # or -macos.txt
python -m agent.main
```

On first run with no saved pairing, a small dialog asks for the pairing
code shown on the HRMS web app right after check-in. After that, the
agent runs quietly with just a tray icon until the employee checks out.

When packaged as a real .exe (see below), the first run also registers
the agent to auto-start at every Windows login on its own (see
`agent/autostart.py`) -- an employee's entire setup is run the exe once,
enter the code, done. This is skipped entirely when running from source
in a dev venv, exactly as here.

Point it at your own backend by editing (or scripting the creation of)
`config.json` in the app-data directory `agent/config.py` resolves --
see that file's `get_app_data_dir()` for exactly where that is per OS.

## Packaging into a distributable .exe / .app

See `packaging/build_windows.md` and `packaging/build_macos.md`.
**Important:** PyInstaller cannot cross-compile -- build the Windows
.exe on a Windows machine and the macOS .app on a Mac.

## What this does not collect

By design, and enforced by what the collectors are even capable of
returning (not just a policy switch that could be silently flipped):

- No keystrokes
- No passwords
- No clipboard contents
- No file contents
- No camera or microphone
- No browser page contents (only the OS-level window title, where the OS
  itself permits reading it -- never the page's own content)
- No screenshots or screen recording
- No continuous server polling -- the agent talks to the server only for
  pairing, a periodic heartbeat/config check (a couple of minutes apart),
  and periodic batch syncs; not on every sample

## Linux support

Explicitly "architecture-ready," not a hardened MVP target: the same
`ActivityCollector` interface is implemented via `xdotool`/`wmctrl`/
`xprintidle` subprocess calls, which cover common X11 desktop sessions
but not Wayland-only ones. Swapping in a more complete implementation
later (e.g. Wayland portal-based) only ever touches
`collector_linux.py` -- nothing else in the agent needs to change.
