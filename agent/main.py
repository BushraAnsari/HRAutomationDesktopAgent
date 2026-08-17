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
from datetime import datetime, timezone

from . import config as config_module
from . import pairing
from .autostart import ensure_autostart_registered
from .singleinstance import ensure_single_instance
from .api_client import ApiClient, ApiError
from .monitor.aggregator import ActivityAggregator
from .monitor.collector_base import get_collector
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

        self.tray = TrayIcon(on_retry_sync=self._manual_retry_sync, on_quit=self._quit)
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

    def _take_sample(self):
        try:
            foreground = self.collector.get_foreground_app()
            idle_seconds = self.collector.get_idle_seconds()
        except Exception as err:  # noqa: BLE001 -- a single bad sample must never kill the sampling thread
            logger.warning("Sample failed: %s", err)
            return

        closed_interval = self.aggregator.add_sample(foreground, idle_seconds)
        if closed_interval:
            attendance_id = self.config.get("current_attendance_id")
            if attendance_id:
                store.enqueue_event(config_module.DB_PATH, attendance_id, closed_interval)

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
        attendance_id = self.config.get("current_attendance_id")
        if closed and attendance_id:
            store.enqueue_event(config_module.DB_PATH, attendance_id, closed)
        if attendance_id:
            self.sync_service.final_sync(attendance_id)
            self.config.set("monitoring_active", False)


def main():
    AgentRuntime().run()


if __name__ == "__main__":
    main()
