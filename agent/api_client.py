"""
The only place this agent speaks HTTP. Every call here corresponds
directly to one of agent.routes.js's device-facing endpoints on the
existing HRMS backend -- see that file for the exact contract.

Deliberately thin: no retry/backoff logic lives here (that's
sync.sync_service's job, since retry policy differs by call -- a failed
heartbeat is just skipped until the next cycle, a failed sync batch must
actually be retried without losing data).
"""
import logging
import platform

import requests

logger = logging.getLogger("agent.api_client")

REQUEST_TIMEOUT_SECONDS = 15


class ApiError(Exception):
    def __init__(self, status_code, code, message, details=None):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        # Per-field validation issues (see validate.js's own error shape --
        # [{ field, issue }, ...]), when the server rejected the request
        # body itself rather than the pairing code specifically. Without
        # this, a VALIDATION_ERROR shows only the generic top-level
        # message and hides exactly which field/why -- the one thing
        # actually needed to fix it rather than guess at it.
        self.details = details or []

    def __str__(self):
        base = super().__str__()
        if not self.details:
            return base
        detail_lines = "\n".join(f"  - {d.get('field', '?')}: {d.get('issue', '?')}" for d in self.details)
        return f"{base}\n{detail_lines}"


def _unwrap(response: requests.Response) -> dict:
    """The whole backend uses one envelope shape ({success, data, ...}) --
    see ApiResponse.js. Mirrored here so every call site gets back just
    the inner `data`, the same as the web app's own axios interceptor."""
    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise ApiError(response.status_code, "NON_JSON_RESPONSE", response.text[:200])

    if response.ok:
        return payload.get("data")

    error = payload.get("error") or {}
    raise ApiError(response.status_code, error.get("code", "UNKNOWN_ERROR"), error.get("message", "Request failed"), error.get("details"))


class ApiClient:
    def __init__(self, config):
        self.config = config

    @property
    def base_url(self) -> str:
        return self.config.get("api_base_url").rstrip("/")

    def _device_headers(self) -> dict:
        token = self.config.get("device_token")
        if not token:
            raise RuntimeError("Agent is not paired yet -- no device token to authenticate with")
        return {"Authorization": f"Bearer {token}"}

    def pair(self, pairing_token: str, os_name: str, os_version: str, agent_version: str) -> dict:
        body = {
            "pairingToken": pairing_token,
            "deviceUuid": self.config.get("device_uuid"),
            "hostname": platform.node(),
            "os": os_name,
            "osVersion": os_version,
            "agentVersion": agent_version,
        }
        response = requests.post(f"{self.base_url}/agent/pair", json=body, timeout=REQUEST_TIMEOUT_SECONDS)
        return _unwrap(response)

    def get_config(self) -> dict:
        response = requests.get(f"{self.base_url}/agent/config", headers=self._device_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
        return _unwrap(response)

    def heartbeat(self) -> dict:
        response = requests.post(f"{self.base_url}/agent/heartbeat", headers=self._device_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
        return _unwrap(response)

    def sync(self, attendance_id: str, events: list, is_final: bool) -> dict:
        body = {"attendanceId": attendance_id, "events": events, "isFinal": is_final}
        response = requests.post(f"{self.base_url}/agent/sync", json=body, headers=self._device_headers(), timeout=30)
        return _unwrap(response)
