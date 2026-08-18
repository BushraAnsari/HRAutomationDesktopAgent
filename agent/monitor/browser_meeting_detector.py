"""
Reads the small status file the native messaging host (see
../../browser-meeting-bridge/native_host/host.py, a separate project
alongside this agent) writes whenever the Chrome extension reports a
Google Meet or Teams-in-browser call is active.

This agent has no way to see inside a browser tab on its own -- that's
exactly the gap this whole bridge exists to close. Nothing here talks to
Chrome directly; it only ever reads a JSON file both sides happen to
have access to, the same "small local file, not real IPC" approach
config.py/the PID file already use elsewhere in this agent.

Returns the same (app_name, title) shape as monitor.meeting_detector's
own detect_meeting(), so both can feed the exact same MeetingAggregator
without it needing to know or care which one found the meeting.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("agent.monitor.browser_meeting")

# If the native host hasn't refreshed this file more recently than this,
# treat it as "not in a meeting" rather than trusting a stale value
# forever. Covers the case where the browser (and the native host
# process along with it) closes or crashes without ever sending a clean
# "meeting ended" message -- Chrome's own tabs.onRemoved handler in the
# extension tries to send that, but nothing can guarantee it always
# succeeds (the whole browser closing at once is exactly the case it
# might not).
STALE_AFTER_SECONDS = 30


def detect_browser_meeting(status_path):
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Perfectly normal -- means the bridge was never set up, or
        # Chrome/the extension simply isn't running right now.
        return None, None

    if not data.get("in_meeting"):
        return None, None

    updated_at_str = data.get("updated_at")
    if not updated_at_str:
        return None, None
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
    except ValueError:
        return None, None

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_seconds > STALE_AFTER_SECONDS:
        return None, None

    return data.get("app") or "Browser meeting", None
