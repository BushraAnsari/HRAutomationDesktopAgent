"""
Linux collector -- "architecture-ready" per the request, not a fully
hardened MVP target the way Windows is. Linux desktop environments don't
share one single API surface the way Windows/macOS do (X11 vs Wayland,
dozens of window managers), so this collector is intentionally
best-effort: it tries the most common X11 tools via subprocess, and
degrades to "no foreground app info" rather than crashing when they're
unavailable (headless server, pure Wayland session with no XWayland,
tools not installed).

Implements the exact same ActivityCollector interface as Windows/macOS --
swapping in a more complete implementation later (e.g. a proper
python-xlib EWMH query, or a Wayland-specific portal-based approach) never
requires touching main.py/aggregator.py, only this one file.
"""
import logging
import shutil
import subprocess

from .collector_base import ActivityCollector, ForegroundApp

logger = logging.getLogger("agent.monitor.linux")


def _run(cmd, timeout=2):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


class LinuxActivityCollector(ActivityCollector):
    def __init__(self):
        self._has_xdotool = shutil.which("xdotool") is not None
        self._has_wmctrl = shutil.which("wmctrl") is not None
        self._has_xprintidle = shutil.which("xprintidle") is not None
        if not (self._has_xdotool or self._has_wmctrl):
            logger.warning(
                "Neither xdotool nor wmctrl found -- foreground app detection unavailable on this session. "
                "Install one of them (apt install xdotool) for X11 sessions; Wayland support is a future addition."
            )
        if not self._has_xprintidle:
            logger.warning("xprintidle not found -- idle detection unavailable (install: apt install xprintidle)")

    def is_available(self) -> bool:
        return self._has_xdotool or self._has_wmctrl

    def get_foreground_app(self):
        if self._has_xdotool:
            window_id = _run(["xdotool", "getactivewindow"])
            if not window_id:
                return None
            window_title = _run(["xdotool", "getwindowname", window_id])
            window_class = _run(["xdotool", "getwindowclassname", window_id])
            return ForegroundApp(
                executable_name=window_class,
                display_name=window_class,
                window_title=window_title,
            )

        if self._has_wmctrl:
            # wmctrl has no direct "active window" query -- this is a
            # coarser fallback (most recently listed window), noted as a
            # known limitation rather than silently pretending it's exact.
            output = _run(["wmctrl", "-l"])
            if not output:
                return None
            lines = output.splitlines()
            if not lines:
                return None
            parts = lines[0].split(None, 3)
            title = parts[3] if len(parts) > 3 else None
            return ForegroundApp(executable_name=None, display_name=None, window_title=title)

        return None

    def get_idle_seconds(self) -> float:
        if self._has_xprintidle:
            output = _run(["xprintidle"])
            if output and output.isdigit():
                return float(output) / 1000.0
        # No reliable signal available -- 0 is the conservative choice
        # (never over-report idle time when it cannot actually be
        # measured; an agent that can't detect idleness should behave as
        # if the user is always active rather than guessing they're away).
        return 0.0
