"""
ActivityCollector: the one interface every OS-specific collector
implements. main.py and aggregator.py talk only to this interface, never
to a Windows/macOS/Linux-specific module directly -- that's what keeps
the platform split at exactly this one seam, per the requested
architecture:

    ActivityCollector
     |-- Windows
     |-- macOS
     `-- Linux (architecture-ready)

Every method here returns None fields rather than raising when a signal
genuinely isn't available on that OS/permission state (e.g. window title
on macOS without Screen Recording permission) -- a missing signal is a
normal, expected condition to design around, not an error state.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ForegroundApp:
    executable_name: Optional[str]  # e.g. "Code.exe", "Google Chrome"
    display_name: Optional[str]  # e.g. "Visual Studio Code"
    window_title: Optional[str]  # None where the OS/permission doesn't allow it


class ActivityCollector(ABC):
    """Every sample the agent takes goes through exactly two calls:
    get_foreground_app() and get_idle_seconds(). Nothing else about the
    OS leaks past this class -- no ctypes/pyobjc/Xlib import ever needs
    to appear in main.py or aggregator.py."""

    @abstractmethod
    def get_foreground_app(self) -> Optional[ForegroundApp]:
        """The application currently in the foreground, or None if it
        cannot be determined right now (e.g. between window switches,
        or on a lock screen)."""
        raise NotImplementedError

    @abstractmethod
    def get_idle_seconds(self) -> float:
        """Seconds since the last keyboard/mouse input, system-wide.
        Never negative; 0 means input just happened."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """True if this collector can actually function on the current
        machine (right OS, required libraries importable, permissions
        granted where checkable). main.py falls back to a
        no-window-title-only degraded mode rather than crashing if this
        is False for reasons other than wrong-OS."""
        return True


def get_collector() -> ActivityCollector:
    """Picks the right collector for the current OS. The one place in
    the whole agent that branches on platform.system() to decide which
    concrete class to instantiate."""
    import platform

    system = platform.system()
    if system == "Windows":
        from .collector_windows import WindowsActivityCollector
        return WindowsActivityCollector()
    if system == "Darwin":
        from .collector_macos import MacActivityCollector
        return MacActivityCollector()
    # Linux and anything else -- architecture-ready, best-effort.
    from .collector_linux import LinuxActivityCollector
    return LinuxActivityCollector()
