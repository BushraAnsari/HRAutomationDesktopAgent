"""
Windows collector -- the platform the spec targets for the MVP.

Foreground app: GetForegroundWindow -> GetWindowThreadProcessId -> the
process's own exe name (via psutil, which reads it more portably than
raw ctypes/OpenProcess calls). Window title: GetWindowText on the same
HWND -- always available on Windows, no special permission needed (unlike
macOS's Screen Recording requirement for the same signal).

Idle time: GetLastInputInfo, the same Win32 API every commercial
activity/idle tool on Windows uses -- system-wide last input tick count,
compared against GetTickCount64.
"""
import ctypes
import logging
from ctypes import wintypes

from .collector_base import ActivityCollector, ForegroundApp

logger = logging.getLogger("agent.monitor.windows")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class WindowsActivityCollector(ActivityCollector):
    def __init__(self):
        try:
            import psutil  # noqa: F401
            self._psutil_available = True
        except ImportError:
            logger.warning("psutil not installed -- foreground app names will be unavailable")
            self._psutil_available = False

    def is_available(self) -> bool:
        return True

    def get_foreground_app(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        window_title = self._get_window_title(hwnd)
        executable_name, display_name = self._get_process_info(hwnd)

        return ForegroundApp(executable_name=executable_name, display_name=display_name, window_title=window_title)

    def _get_window_title(self, hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or None

    def _get_process_info(self, hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or not self._psutil_available:
            return None, None
        try:
            import psutil
            proc = psutil.Process(pid.value)
            exe_name = proc.name()  # e.g. "Code.exe"
            # A friendlier display name where the exe's own file
            # description is readable -- falls back to the raw exe name
            # (with .exe stripped) when not, rather than leaving it blank.
            display_name = exe_name.rsplit(".", 1)[0] if exe_name else None
            return exe_name, display_name
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # The foreground window can belong to a process this agent
            # doesn't have permission to inspect (elevated/admin apps) --
            # degrade to "unknown application", never crash the sample.
            return None, None

    def get_idle_seconds(self) -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis_since_input = kernel32.GetTickCount64() - info.dwTime
        return max(0.0, millis_since_input / 1000.0)
