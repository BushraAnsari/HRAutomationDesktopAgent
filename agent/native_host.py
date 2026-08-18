"""
The native messaging host relay -- now living INSIDE the main agent
package, instead of a separate standalone project, so there is only one
executable to distribute and one thing to keep updated. Chrome still
launches this via a tiny auto-generated launcher script (see
browser_bridge_setup.py's own generate_launcher), not this module
directly -- Chrome's native messaging spec has no way to pass a
"--native-host" argument through the manifest's own "path" field, so a
one-line wrapper script is what actually invokes
`HRActivityAgent.exe --native-host` (or `python -m agent.main
--native-host` from source).

Everything else about this is identical to the original standalone
host.py this replaces: reads a 4-byte little-endian length-prefixed
JSON message from stdin (Chrome's own native messaging protocol), writes
a small status file to the SAME app-data directory the rest of this
agent already uses (see config.get_app_data_dir), and responds so Chrome
considers the connection healthy. Deliberately dumb -- never talks to
the running agent process directly (no socket, no shared memory); the
agent's own sampling loop (see monitor/browser_meeting_detector.py and
monitor/browser_domain_detector.py) picks these files up on its next
cycle.

Two separate status files, not one merged file -- background.js's own
domain-tracking and content_script.js's own meeting-detection are
genuinely different concerns updated on different, independent
schedules; keeping them apart avoids any read-modify-write merge logic
here (this process only ever needs to write whichever one this specific
message is actually about).
"""
import json
import logging
import struct
import sys
from datetime import datetime, timezone

from . import config as config_module

logger = logging.getLogger("agent.native_host")


def _read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return None
    message_length = struct.unpack("<I", raw_length)[0]
    message = sys.stdin.buffer.read(message_length).decode("utf-8")
    return json.loads(message)


def _send_message(payload):
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def run_native_host():
    """Entry point when this process is launched as
    `... --native-host` by Chrome (via the auto-generated launcher, see
    browser_bridge_setup.py) -- runs the message relay loop and returns
    once Chrome disconnects (extension unloaded, browser closed), rather
    than doing anything else this agent normally does (no tray icon, no
    sampling loops, no server heartbeats -- this is a completely
    separate, short-lived process each time Chrome starts it)."""
    meeting_status_path = config_module.get_app_data_dir() / "browser_meeting_status.json"
    domain_status_path = config_module.get_app_data_dir() / "browser_domain_status.json"

    while True:
        try:
            message = _read_message()
        except (json.JSONDecodeError, UnicodeDecodeError, struct.error):
            continue  # a malformed message must never drop the connection entirely
        if message is None:
            break

        # background.js's own domain-tracking messages are shaped
        # {"domain": "..."} -- distinguished from meeting-status messages
        # ({"inMeeting": bool, "app": ...}) by which key is actually
        # present, since both share this same connection.
        if "domain" in message:
            domain_status = {
                "domain": message.get("domain") or None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                domain_status_path.write_text(json.dumps(domain_status))
            except OSError:
                pass  # best-effort -- a failed write here shouldn't crash the host
        else:
            meeting_status = {
                "in_meeting": bool(message.get("inMeeting")),
                "app": message.get("app") or None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                meeting_status_path.write_text(json.dumps(meeting_status))
            except OSError:
                pass  # best-effort -- a failed write here shouldn't crash the host

        _send_message({"ok": True})

