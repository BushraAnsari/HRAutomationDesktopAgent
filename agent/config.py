"""
Local, on-disk agent configuration and credentials.

Nothing here is the "server config" (poll intervals, productivity rules --
that's agent.api_client.get_config(), fetched from the HRMS API and never
persisted verbatim). This module is only what the agent needs *before* it
can even talk to the server: where the server is, what device it is, and
the device token it earned by pairing.

Storage location is deliberately per-OS-conventional (AppData on Windows,
Application Support on macOS, ~/.config on Linux) rather than next to the
executable -- a packaged .exe/.app is often in a location the running
process shouldn't write into (Program Files, a read-only .app bundle).
"""
import json
import os
import platform
import uuid
from pathlib import Path

APP_DIR_NAME = "HRAutomationActivityAgent"


def get_app_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_DIR_NAME
    elif system == "Darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:  # Linux and anything else -- XDG convention, architecture-ready
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = get_app_data_dir() / "config.json"
DB_PATH = get_app_data_dir() / "activity_queue.sqlite3"
LOG_PATH = get_app_data_dir() / "agent.log"
# See singleinstance.py's own ensure_single_instance -- this is what
# lets a new launch find and stop a still-running previous one, instead
# of the two just piling up side by side.
PID_PATH = get_app_data_dir() / "agent.pid"

DEFAULTS = {
    # Set once at install time (see packaging/README) or by an admin --
    # the API base your HRMS backend is actually reachable at, e.g.
    # "https://hrms.yourcompany.com/api/v1".
    "api_base_url": "http://localhost:4000/api/v1",
    # Generated once, on first run, and never regenerated after --
    # persisted specifically so re-pairing the same physical machine
    # updates the *same* AgentDevice row server-side instead of creating
    # a new one every reinstall (see agentDevice.model.js's own comment).
    "device_uuid": None,
    "device_token": None,
    "device_id": None,
    "current_attendance_id": None,
    "monitoring_active": False,
}


def _read() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        # A corrupted config file must never crash the agent on startup --
        # falls back to defaults, which just means re-pairing is needed.
        return dict(DEFAULTS)


def _write(data: dict) -> None:
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # Atomic replace -- a crash mid-write must never leave a half-written,
    # unparseable config file behind for the next run to choke on.
    os.replace(tmp_path, CONFIG_PATH)


class AgentConfig:
    def __init__(self):
        self._data = _read()
        if not self._data.get("device_uuid"):
            self._data["device_uuid"] = str(uuid.uuid4())
            self.save()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def update(self, **kwargs):
        self._data.update(kwargs)
        self.save()

    def save(self):
        _write(self._data)

    @property
    def is_paired(self) -> bool:
        return bool(self._data.get("device_token"))

    def clear_pairing(self):
        """Used when the server reports the device token as revoked/stale
        -- forces a fresh /pair on next check-in rather than looping on a
        credential that will never work again."""
        self._data["device_token"] = None
        self._data["device_id"] = None
        self.save()
