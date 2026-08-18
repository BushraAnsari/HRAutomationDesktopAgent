"""
Reads the small status file the native messaging host (see
../native_host.py) writes whenever the Chrome extension's own
background.js reports the domain of whichever tab is currently focused
(background.js is the only thing that can reliably answer "which tab is
actually focused right now" -- a content script embedded in one page has
no way to know if IT is the one the person is actually looking at).

Same "small shared local file, not real IPC" approach as
browser_meeting_detector.py's own identical reasoning -- this agent has
no way to see inside a browser tab on its own, and a shared file both
sides can already reach is simpler and more robust than real IPC between
two independently launched, independently lived processes.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("agent.monitor.browser_domain")

# If the native host hasn't refreshed this file more recently than this,
# the reported domain is stale enough to distrust -- the browser may
# have closed, or the extension stopped running, without a final update
# ever landing. Slightly more generous than the meeting-status
# equivalent's own threshold, since domain changes are typically reported
# on every tab switch rather than a periodic re-check, so a genuinely
# fresh session could otherwise go a little longer between updates.
STALE_AFTER_SECONDS = 45


def detect_browser_domain(status_path):
    """Returns the current domain string (e.g. "google.com"), or None if
    there's nothing fresh to report."""
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Perfectly normal -- means the bridge was never set up, or
        # Chrome/the extension simply isn't running right now.
        return None

    domain = data.get("domain")
    if not domain:
        return None

    updated_at_str = data.get("updated_at")
    if not updated_at_str:
        return None
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
    except ValueError:
        return None

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_seconds > STALE_AFTER_SECONDS:
        return None

    return domain
