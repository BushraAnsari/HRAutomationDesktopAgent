"""
Turns a stream of raw samples (taken every poll_interval_seconds, per the
spec's own "every 2-5 seconds") into aggregated intervals -- exactly the
"300 samples become one or a few activity records" behavior the spec asks
for. Nothing is written to the local queue on every sample; only when an
interval actually closes (the foreground app changes, or active/idle
state flips) does a completed interval get handed to the caller to
persist.
"""
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivityAggregator:
    def __init__(self, idle_threshold_seconds: float):
        self.idle_threshold_seconds = idle_threshold_seconds
        self._current = None  # the in-progress interval, or None before the first sample

    def update_idle_threshold(self, seconds: float):
        """Called whenever a fresh /agent/config poll comes back with a
        different admin-configured threshold -- takes effect on the next
        sample, never retroactively reclassifies an interval already in
        progress."""
        self.idle_threshold_seconds = seconds

    def add_sample(self, foreground_app, idle_seconds: float, domain=None):
        """foreground_app is a collector_base.ForegroundApp or None.
        domain is the current browser tab's domain (e.g. "google.com"),
        passed in from main.py's own _take_sample only when the
        foreground app is a recognized browser -- None for every other
        app, and always None while idle, the same "no app identity while
        idle" reasoning app_key itself already follows. A domain change
        closes the current interval exactly the same way an app change
        does (switching from google.com to chatgpt.com within the same
        chrome.exe process is a real change worth its own row, even
        though the executable never changes). Returns a completed
        interval dict (ready for store.queue.enqueue_event) if this
        sample closed one, else None."""
        activity_type = "IDLE" if idle_seconds >= self.idle_threshold_seconds else "ACTIVE"
        # No app identity distinction while idle -- "which app was in the
        # background while nobody touched the keyboard" isn't a
        # meaningful signal the spec asks for; every IDLE interval has
        # application=None regardless of what's behind it.
        app_key = None if activity_type == "IDLE" else (foreground_app.executable_name if foreground_app else None)
        domain_key = domain if activity_type == "ACTIVE" else None

        now = _now_iso()

        if self._current is None:
            self._start_interval(activity_type, app_key, domain_key, foreground_app, now)
            return None

        same_interval = (
            self._current["activity_type"] == activity_type
            and self._current["_app_key"] == app_key
            and self._current["_domain_key"] == domain_key
        )

        if same_interval:
            self._current["ended_at"] = now
            return None

        # The interval just changed -- close out the previous one and
        # start a fresh one from this sample.
        closed = self._close_interval(now)
        self._start_interval(activity_type, app_key, domain_key, foreground_app, now)
        return closed

    def flush(self):
        """Called on shutdown/check-out -- whatever interval is still
        open gets closed and returned rather than silently discarded just
        because the agent stopped mid-interval."""
        if self._current is None:
            return None
        return self._close_interval(_now_iso())

    def _start_interval(self, activity_type, app_key, domain_key, foreground_app, started_at):
        self._current = {
            "activity_type": activity_type,
            "_app_key": app_key,
            "_domain_key": domain_key,
            "application": app_key,
            "application_display_name": foreground_app.display_name if foreground_app else None,
            "window_title": foreground_app.window_title if foreground_app else None,
            "domain": domain_key,
            "started_at": started_at,
            "ended_at": started_at,
        }

    def _close_interval(self, ended_at):
        interval = self._current
        interval["ended_at"] = ended_at
        started = datetime.fromisoformat(interval["started_at"])
        ended = datetime.fromisoformat(interval["ended_at"])
        interval["duration_seconds"] = max(0.0, (ended - started).total_seconds())
        interval.pop("_app_key", None)
        interval.pop("_domain_key", None)
        self._current = None
        # Sub-second intervals (a window flicker, a sample landing right
        # at a transition) aren't worth a whole database row -- discarded
        # rather than synced as noise.
        if interval["duration_seconds"] < 1:
            return None
        return interval
