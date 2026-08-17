"""
Periodic batch upload + retry -- the piece that actually satisfies "if
internet is unavailable, keep data locally and retry automatically."

Two independent cadences, both driven from main.py's own scheduler, not
from this module directly:
  - a routine sync every syncIntervalSeconds (server-configured, see
    agent.service.js's buildAgentConfig) while a session is active
  - one final, blocking sync on check-out/shutdown, with isFinal=True,
    which is what actually tells the server "no more activity is coming
    for this session" (see agent.service.js's syncBatch -- isFinal is
    what flips Attendance.monitoringActive off server-side too, as a
    second confirmation alongside checkOut() itself already having done so).
"""
import logging

from .. import api_client as api_client_module
from ..store import queue as store

logger = logging.getLogger("agent.sync")


class SyncService:
    def __init__(self, config, api_client: "api_client_module.ApiClient", db_path):
        self.config = config
        self.api_client = api_client
        self.db_path = db_path

    def sync_pending(self, attendance_id: str, is_final: bool = False) -> bool:
        """Uploads whatever unsynced rows exist for this session, in
        batches, until none remain (or a request fails, in which case it
        stops and returns False -- the remaining rows stay queued for the
        next attempt, nothing is lost). Returns True if everything queued
        was uploaded successfully (including the case where there was
        nothing to upload)."""
        while True:
            batch = store.get_unsynced_batch(self.db_path, attendance_id)
            if not batch:
                if is_final:
                    # Nothing pending, but the caller still needs the
                    # server to hear "this session is over" -- an empty
                    # final batch is a completely normal, expected call,
                    # not a no-op to skip.
                    return self._send_batch(attendance_id, [], is_final=True) is not None
                return True

            # Every batch sent inside this loop is a routine, non-final
            # upload -- the actual isFinal=True signal is only ever sent
            # once, from the `if not batch` branch above (when the queue
            # is finally empty) or from final_sync() below.
            result = self._send_batch(attendance_id, batch, is_final=False)
            if result is None:
                return False  # network/server error -- leave batch queued, try again later

            store.mark_synced(self.db_path, [row["id"] for row in batch])
            logger.info(
                "Synced %d event(s) for attendance %s (%d already-synced duplicates skipped)",
                result.get("inserted", 0), attendance_id, result.get("duplicates", 0),
            )

        # Unreachable in practice (loop only exits via return above), kept
        # only to make the function's control flow explicit to a reader.

    def final_sync(self, attendance_id: str) -> bool:
        """Drains every remaining batch normally, then sends one last
        isFinal=True call (even if the queue is already empty) so the
        server can close the session out -- see this class's own
        docstring for why isFinal matters as its own signal."""
        drained = self.sync_pending(attendance_id, is_final=False)
        final_ok = self._send_batch(attendance_id, [], is_final=True) is not None
        return drained and final_ok

    def _send_batch(self, attendance_id: str, rows: list, is_final: bool):
        payload_events = [store.to_api_payload(r) for r in rows]
        try:
            return self.api_client.sync(attendance_id=attendance_id, events=payload_events, is_final=is_final)
        except api_client_module.ApiError as err:
            if err.code in ("DEVICE_TOKEN_EXPIRED", "INVALID_DEVICE_TOKEN", "DEVICE_TOKEN_STALE", "DEVICE_REVOKED", "DEVICE_NOT_FOUND"):
                logger.warning("Device credential rejected during sync (%s) -- clearing local pairing", err.code)
                self.config.clear_pairing()
            else:
                logger.error("Sync batch rejected by server: %s", err)
            return None
        except Exception as err:  # noqa: BLE001 -- any network failure must be swallowed and retried, never crash the agent
            logger.warning("Sync batch failed (will retry): %s", err)
            return None
