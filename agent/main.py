"""
Entrypoint. Ties together everything else in this package:

  pairing (once) -> sampling loop (thread) -> local queue (SQLite)
                  -> heartbeat/config poll (thread, every heartbeatIntervalSeconds)
                  -> periodic sync (thread, every syncIntervalSeconds)
                  -> tray icon (its own thread, status only)

No window is ever created except the one-time pairing dialog
(pairing.py) and, on a fatal pairing failure, a single error dialog --
otherwise this process has no visible UI at all beyond the tray icon.

Run directly with `python -m agent.main` during development; see
packaging/ for turning this into a windowless .exe/.app that starts
automatically at login.
"""
import logging
import logging.handlers
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

# Windows-only, used solely by _get_tick_count_ms below -- imported at
# module load time rather than lazily inside that method, since it's a
# standard-library module always present on Windows (ctypes itself is
# cross-platform; only the windll attribute accessed inside that method
# is Windows-specific).
import ctypes

from . import config as config_module
from . import pairing
from .autostart import ensure_autostart_registered
from .browser_bridge_setup import ensure_browser_bridge_registered
from .singleinstance import ensure_single_instance
from .api_client import ApiClient, ApiError
from .monitor.aggregator import ActivityAggregator
from .monitor.collector_base import get_collector
from .monitor.meeting_aggregator import MeetingAggregator
from .store import queue as store
from .sync.sync_service import SyncService
from .tray import TrayIcon, STATUS_MONITORING, STATUS_CHECKED_OUT, STATUS_NOT_PAIRED

logger = logging.getLogger("agent.main")


def _setup_logging(log_path):
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Also to stdout for `python -m agent.main` during development --
    # a packaged --windowed build has no console for this to reach
    # anyway, so it is harmless to leave attached.
    root.addHandler(logging.StreamHandler(sys.stdout))


class AgentRuntime:
    def __init__(self):
        self.config = config_module.AgentConfig()
        _setup_logging(config_module.LOG_PATH)

        # As early as possible -- before the tray icon, before anything
        # else -- so a still-running previous instance (very easy to end
        # up with during testing: a crashed/errored run whose process
        # kept going anyway) gets stopped before this one claims the
        # same tray icon, same PID file, same everything.
        ensure_single_instance(config_module.PID_PATH)

        store.init_db(config_module.DB_PATH)

        self.api_client = ApiClient(self.config)
        self.sync_service = SyncService(self.config, self.api_client, config_module.DB_PATH)
        self.collector = get_collector()
        self.aggregator = ActivityAggregator(idle_threshold_seconds=300)
        # Separate from the collector/aggregator above -- this checks
        # ALL open windows, not just the foreground one, specifically so
        # a Zoom/Teams desktop call is still counted while someone's
        # foreground window is something else entirely. Windows-only for
        # now (see meeting_detector.py's own comment); silently does
        # nothing on other platforms rather than erroring.
        self.meeting_aggregator = MeetingAggregator()

        self.tray = TrayIcon(
            on_retry_sync=self._manual_retry_sync,
            on_quit=self._quit,
        )
        self._stop_event = threading.Event()
        self._server_config = None  # last successfully-fetched /agent/config response

    # ---- lifecycle -----------------------------------------------------

    def run(self):
        # Self-registers for auto-start at Windows login the moment this
        # runs as the real packaged .exe (see autostart.py's own comment
        # for why nothing happens here at all when running from source/a
        # dev venv) -- this, plus pairing just below, is the entire setup
        # an employee ever has to do: run the exe once, enter the code,
        # done. No IT-run schtasks command needed per machine.
        ensure_autostart_registered()

        # Registers the browser-meeting native messaging bridge with
        # Chrome, fully automatically -- see browser_bridge_setup.py's
        # own comment for why this needs no prompt at all: the
        # extension's manifest.json embeds a fixed key, giving it a
        # permanent, already-known ID, so there is nothing left for a
        # person to look up or paste in. A no-op after the first run
        # (and a no-op entirely on macOS/Linux, not wired into this
        # automatic flow yet).
        ensure_browser_bridge_registered()

        if not self.collector.is_available():
            logger.warning(
                "Activity collector for this platform is not fully available -- "
                "the agent will still run and sync heartbeats, but application/idle detection may be limited."
            )

        self.tray.start()

        if not self.config.is_paired:
            self.tray.update_status(STATUS_NOT_PAIRED)
            if not pairing.pair_agent(self.config, self.api_client):
                # Not paired and the user dismissed the prompt -- the
                # agent keeps running quietly in the tray so pairing can
                # be retried without reinstalling anything (a future
                # version could add a tray menu item to re-trigger this;
                # for now, restarting the agent re-prompts).
                logger.info("Running unpaired -- monitoring will not start until paired")

        self._sampling_thread = threading.Thread(target=self._sampling_loop, daemon=True)
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        for t in (self._sampling_thread, self._heartbeat_thread, self._sync_thread):
            t.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self._quit()

    def _quit(self):
        logger.info("Shutting down -- flushing any open interval and doing a final sync attempt")
        self._flush_and_sync_if_active()
        self._stop_event.set()
        self.tray.stop()

    # ---- background loops ----------------------------------------------

    def _sampling_loop(self):
        """Runs at pollIntervalSeconds (server-configured, default a
        handful of seconds) -- deliberately NOT a network call each tick;
        this only samples the local OS and appends to the in-memory
        aggregator. Nothing touches the network here at all."""
        while not self._stop_event.is_set():
            interval_seconds = (self._server_config or {}).get("pollIntervalSeconds", 3)
            if self.config.is_paired and self.config.get("monitoring_active"):
                self._take_sample()
            time.sleep(interval_seconds)

    # How much bigger than a normal poll interval a gap needs to be
    # before it's treated as "the process wasn't running" rather than
    # ordinary scheduling jitter (a slow tick, a GC pause) -- poll
    # intervals are always a handful of seconds (2-10s, admin-
    # configured), so 90 seconds sits comfortably above any of that
    # while still catching genuine gaps (sleep, shutdown, a crash), which
    # realistically are always minutes long at minimum.
    OFFLINE_GAP_THRESHOLD_SECONDS = 90
    # Small enough to ignore ordinary clock/tick drift between the two
    # counters (they're not perfectly synchronized even in normal
    # operation), large enough that a real sleep period is never missed
    # -- actual sleeps are realistically always many seconds at minimum.
    SLEEP_PORTION_THRESHOLD_SECONDS = 5

    @staticmethod
    def _get_tick_count_ms():
        """Milliseconds since this boot -- GetTickCount64 specifically
        does NOT advance while the system is asleep/hibernating (unlike
        the wall clock, which keeps moving), which is exactly what makes
        it possible to tell "the system was asleep for X seconds" apart
        from "the system was awake the whole time but this agent simply
        wasn't sampling" (a real problem worth flagging, not normal idle
        time). Windows-only for now -- returns None elsewhere, in which
        case _check_for_offline_gap falls back to treating any gap as
        sleep-equivalent rather than attempting a split it can't measure."""
        if sys.platform != "win32":
            return None
        try:
            return ctypes.windll.kernel32.GetTickCount64()
        except Exception:  # noqa: BLE001 -- must never crash sampling over a diagnostic call
            return None

    def _check_for_offline_gap(self):
        """Compares now against the last sample this agent ever took,
        persisted across restarts (see config.py's own generic get/set) --
        catches sleep, shutdown, a crash, or the agent simply being
        closed and reopened later. Splits the gap into two different
        things, not one: time the system was genuinely asleep/shut down
        (folded into IDLE -- the person wasn't working, same as sitting
        idle) versus time the system was awake but this agent specifically
        wasn't sampling (kept as OFFLINE/untracked -- a real problem with
        the tool itself, not a normal break). GetTickCount64 is what
        makes the split possible: it pauses during sleep, the wall clock
        doesn't, so the DIFFERENCE between how much wall-clock time
        passed and how much tick-count time passed is exactly how long
        the system was actually asleep for."""
        now = datetime.now(timezone.utc)
        last_sample_at_str = self.config.get("last_sample_at")
        last_tick_count = self.config.get("last_sample_tick_count")
        current_tick_count = self._get_tick_count_ms()

        if last_sample_at_str:
            try:
                last_sample_at = datetime.fromisoformat(last_sample_at_str)
                wall_gap_seconds = (now - last_sample_at).total_seconds()

                if wall_gap_seconds > self.OFFLINE_GAP_THRESHOLD_SECONDS:
                    attendance_id = self.config.get("current_attendance_id")
                    if attendance_id:
                        if last_tick_count is not None and current_tick_count is not None and current_tick_count >= last_tick_count:
                            tick_gap_seconds = (current_tick_count - last_tick_count) / 1000
                            asleep_seconds = max(0.0, wall_gap_seconds - tick_gap_seconds)
                        else:
                            # Tick count went backwards -- a reboot
                            # happened (this boot's own "time since
                            # start" reset to near-zero), or tick
                            # counting isn't available on this platform.
                            # Either way, "how long was it awake during
                            # this specific gap" isn't answerable, so the
                            # whole gap is treated as sleep/shutdown-
                            # equivalent rather than guessed at.
                            asleep_seconds = wall_gap_seconds

                        awake_untracked_seconds = wall_gap_seconds - asleep_seconds

                        # Sleep portion placed first, untracked portion
                        # last -- an approximation (the exact moment
                        # within the gap that sleep started isn't
                        # observable from two endpoint samples alone),
                        # but a reasonable one: an agent hiccup right
                        # before a scheduled sleep is a far more typical
                        # real-world sequence than the reverse.
                        cursor = last_sample_at
                        if asleep_seconds > self.SLEEP_PORTION_THRESHOLD_SECONDS:
                            sleep_end = cursor + timedelta(seconds=asleep_seconds)
                            store.enqueue_event(config_module.DB_PATH, attendance_id, {
                                "activity_type": "IDLE",
                                "application": None,
                                "application_display_name": None,
                                "window_title": None,
                                "started_at": cursor.isoformat(),
                                "ended_at": sleep_end.isoformat(),
                                "duration_seconds": asleep_seconds,
                            })
                            cursor = sleep_end
                        if awake_untracked_seconds > self.OFFLINE_GAP_THRESHOLD_SECONDS:
                            store.enqueue_event(config_module.DB_PATH, attendance_id, {
                                "activity_type": "OFFLINE",
                                "application": None,
                                "application_display_name": None,
                                "window_title": None,
                                "started_at": cursor.isoformat(),
                                "ended_at": now.isoformat(),
                                "duration_seconds": awake_untracked_seconds,
                            })
            except ValueError:
                pass  # a corrupted/malformed timestamp must never crash sampling -- just skip detection this once

        self.config.set("last_sample_at", now.isoformat())
        if current_tick_count is not None:
            self.config.set("last_sample_tick_count", current_tick_count)

    def _take_sample(self):
        self._check_for_offline_gap()
        try:
            foreground = self.collector.get_foreground_app()
            idle_seconds = self.collector.get_idle_seconds()
        except Exception as err:  # noqa: BLE001 -- a single bad sample must never kill the sampling thread
            logger.warning("Sample failed: %s", err)
            return

        # Only meaningful for a recognized browser -- checked here, not
        # inside the aggregator itself, so the aggregator never needs to
        # know which executables happen to be browsers at all. Read from
        # the same status file background.js's own domain-tracking
        # writes to (see browser_domain_detector.py's own comment for why
        # that has to come from the browser extension rather than
        # anything this agent could determine on its own).
        domain = None
        if foreground and foreground.executable_name and foreground.executable_name.lower() in ("chrome.exe", "msedge.exe", "firefox.exe"):
            try:
                from .monitor.browser_domain_detector import detect_browser_domain
                domain = detect_browser_domain(config_module.get_app_data_dir() / "browser_domain_status.json")
            except Exception as err:  # noqa: BLE001 -- a failed domain check must never kill the sampling thread
                logger.warning("Browser domain detection failed: %s", err)

        closed_interval = self.aggregator.add_sample(foreground, idle_seconds, domain=domain)
        if closed_interval:
            attendance_id = self.config.get("current_attendance_id")
            if attendance_id:
                store.enqueue_event(config_module.DB_PATH, attendance_id, closed_interval)

        # Meeting presence -- checked independently of the foreground
        # sample above (see MeetingAggregator's own comment for why: a
        # meeting can be genuinely ongoing in a window that isn't
        # focused at all). Two sources feed the same aggregator: desktop
        # meeting apps (Windows-only, all-window enumeration -- see
        # meeting_detector.py) and browser-based meetings (Google Meet,
        # Teams-in-browser -- see browser_meeting_detector.py, fed by the
        # separate Chrome extension + native messaging bridge project).
        # Desktop apps are checked first; if genuinely both were
        # somehow active at once, only one meeting is realistically
        # happening, so whichever is found first wins for this sample.
        app_name = None
        if sys.platform == "win32":
            try:
                from .monitor.meeting_detector import detect_meeting
                app_name, _title = detect_meeting()
            except Exception as err:  # noqa: BLE001 -- a single bad check must never kill the sampling thread
                logger.warning("Desktop meeting detection failed: %s", err)

        if not app_name:
            try:
                from .monitor.browser_meeting_detector import detect_browser_meeting
                app_name, _title = detect_browser_meeting(config_module.get_app_data_dir() / "browser_meeting_status.json")
            except Exception as err:  # noqa: BLE001
                logger.warning("Browser meeting detection failed: %s", err)

        closed_meeting = self.meeting_aggregator.add_sample(app_name)
        if closed_meeting:
            attendance_id = self.config.get("current_attendance_id")
            if attendance_id:
                store.enqueue_event(config_module.DB_PATH, attendance_id, closed_meeting)

    def _heartbeat_loop(self):
        """The one place this agent calls the server on a short,
        recurring cadence while checked in -- a handful of small requests
        per session, not the "continuous server polling" the spec rules
        out. This is also how the agent notices a check-out that happened
        from a browser on a different machine: getConfig()/heartbeat()
        both report the server's own idea of monitoringActive, which is
        authoritative over whatever this process last knew locally."""
        while not self._stop_event.is_set():
            if not self.config.is_paired:
                time.sleep(30)
                continue

            interval_seconds = (self._server_config or {}).get("heartbeatIntervalSeconds", 120)
            try:
                # heartbeat() first -- it's the one call that actually
                # updates lastSeenAt server-side (see agent.service.js's
                # own heartbeat() vs getConfig(), which never touches it).
                # Without this, the dashboard's own "Online/Offline"
                # status only ever updated whenever a sync happened to
                # fire (every syncIntervalSeconds, and only while an
                # active session exists to sync against) -- an agent that
                # was genuinely still running could sit there reading
                # "Offline" for the better part of an hour, which is
                # exactly the bug this fixes.
                self.api_client.heartbeat()
                self._server_config = self.api_client.get_config()
                self.config.set("monitoring_active", self._server_config["monitoringActive"])
                attendance_id = self._server_config.get("attendanceId")
                if attendance_id:
                    self.config.set("current_attendance_id", attendance_id)

                if not self._server_config["monitoringActive"] and self.config.get("current_attendance_id"):
                    # The server just told us this session ended (checked
                    # out, possibly from elsewhere) -- finalize locally
                    # exactly as if check-out had been detected directly.
                    self._flush_and_sync_if_active()

                if self._server_config.get("forceSyncNow"):
                    # HR clicked "Request sync now" on the Team Activity
                    # page (see agent.service.js's own requestSyncNow) --
                    # this is the one-shot flag it sets, picked up here on
                    # whatever heartbeat happens to come next rather than
                    # instantly, since there's no persistent connection to
                    # push this down the moment the button was clicked.
                    attendance_id = self.config.get("current_attendance_id")
                    if attendance_id:
                        logger.info("Server requested an immediate sync -- syncing now")
                        self.sync_service.sync_pending(attendance_id, is_final=False)

                self.tray.update_status(STATUS_MONITORING if self._server_config["monitoringActive"] else STATUS_CHECKED_OUT)
            except ApiError as err:
                logger.warning("Heartbeat/config poll failed: %s", err)
                if err.code in ("DEVICE_TOKEN_EXPIRED", "INVALID_DEVICE_TOKEN", "DEVICE_TOKEN_STALE", "DEVICE_REVOKED", "DEVICE_NOT_FOUND"):
                    self.config.clear_pairing()
                    self.tray.update_status(STATUS_NOT_PAIRED)
            except Exception as err:  # noqa: BLE001 -- network hiccups must never kill this loop
                logger.warning("Heartbeat/config poll failed: %s", err)

            time.sleep(interval_seconds)

    def _sync_loop(self):
        while not self._stop_event.is_set():
            interval_seconds = (self._server_config or {}).get("syncIntervalSeconds", 300)
            attendance_id = self.config.get("current_attendance_id")
            if self.config.is_paired and attendance_id:
                ok = self.sync_service.sync_pending(attendance_id, is_final=False)
                if ok:
                    self.tray.update_status(
                        STATUS_MONITORING if self.config.get("monitoring_active") else STATUS_CHECKED_OUT,
                        last_sync_text=datetime.now(timezone.utc).strftime("%H:%M UTC"),
                    )
            time.sleep(interval_seconds)

    def _manual_retry_sync(self):
        attendance_id = self.config.get("current_attendance_id")
        if attendance_id:
            threading.Thread(target=lambda: self.sync_service.sync_pending(attendance_id, is_final=False), daemon=True).start()
        else:
            # Previously a silent no-op -- indistinguishable from "synced
            # successfully and there was simply nothing new," which is
            # exactly the confusing symptom of clicking this and nothing
            # ever changing. Logged now so that distinction is at least
            # visible in agent.log, even though the tray menu itself has
            # nowhere to show a message directly.
            logger.warning("Retry sync requested, but no current attendance session is recorded -- is this device actually checked in?")

    def _flush_and_sync_if_active(self):
        closed = self.aggregator.flush()
        closed_meeting = self.meeting_aggregator.flush()
        attendance_id = self.config.get("current_attendance_id")
        if closed and attendance_id:
            store.enqueue_event(config_module.DB_PATH, attendance_id, closed)
        if closed_meeting and attendance_id:
            store.enqueue_event(config_module.DB_PATH, attendance_id, closed_meeting)
        if attendance_id:
            self.sync_service.final_sync(attendance_id)
            self.config.set("monitoring_active", False)


def main():
    # Dispatched here, before anything else about a normal agent run
    # starts -- Chrome launches this exact invocation (via the
    # auto-generated launcher script, see browser_bridge_setup.py's own
    # register_native_host) as a completely separate, short-lived
    # process each time it needs to relay a message, not as "the agent"
    # in its usual sense (no tray icon, no sampling loops, nothing else
    # here applies to that invocation at all).
    if "--native-host" in sys.argv:
        from .native_host import run_native_host
        run_native_host()
        return

    AgentRuntime().run()


if __name__ == "__main__":
    main()
