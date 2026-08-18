"""
Turns periodic meeting_detector.py samples into closed MEETING intervals
-- the same "raw samples become one or a few records" job
monitor.aggregator.ActivityAggregator does for foreground-window
tracking, but simpler: there's no idle/active distinction here, only
"a meeting app's call window was open" or not.
"""
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeetingAggregator:
    def __init__(self):
        self._current = None  # the in-progress interval, or None

    def add_sample(self, app_name):
        """app_name is whatever meeting_detector.detect_meeting() returned
        (or None if no meeting window was found this sample). Returns a
        completed interval dict (ready for store.queue.enqueue_event) if
        this sample closed one, else None."""
        now = _now_iso()

        if self._current is None:
            if app_name:
                self._start_interval(app_name, now)
            return None

        if app_name == self._current["_app_name"]:
            self._current["ended_at"] = now
            return None

        # Either the meeting ended (app_name is None) or switched to a
        # different meeting app -- close the open interval either way,
        # and start a fresh one only if this sample found a new one.
        closed = self._close_interval(now)
        if app_name:
            self._start_interval(app_name, now)
        return closed

    def flush(self):
        """Called on shutdown/check-out -- whatever interval is still
        open gets closed and returned rather than silently discarded."""
        if self._current is None:
            return None
        return self._close_interval(_now_iso())

    def _start_interval(self, app_name, started_at):
        self._current = {
            "activity_type": "MEETING",
            "_app_name": app_name,
            "application": app_name,
            "application_display_name": app_name,
            "window_title": None,
            "started_at": started_at,
            "ended_at": started_at,
        }

    def _close_interval(self, ended_at):
        interval = self._current
        interval["ended_at"] = ended_at
        started = datetime.fromisoformat(interval["started_at"])
        ended = datetime.fromisoformat(interval["ended_at"])
        interval["duration_seconds"] = max(0.0, (ended - started).total_seconds())
        interval.pop("_app_name", None)
        self._current = None
        # Same "sub-second noise isn't worth a row" reasoning as
        # ActivityAggregator's own identical check.
        if interval["duration_seconds"] < 1:
            return None
        return interval
