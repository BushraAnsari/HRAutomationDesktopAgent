"""
macOS collector -- uses PyObjC (AppKit/Quartz), Apple's own Objective-C
bridge, not a third-party scraping approach.

Foreground app: NSWorkspace.sharedWorkspace().frontmostApplication() --
always available, no special permission.

Window title: genuinely different from Windows here. Reading another
app's window title system-wide requires the Screen Recording permission
(macOS 10.15+) -- without it, CGWindowListCopyWindowInfo simply omits
titles rather than erroring. This collector checks for that gracefully
and returns None for window_title rather than failing, exactly matching
the spec's own "Window title where OS allows" wording -- this IS the "OS
allows" boundary on macOS specifically.

Idle time: CGEventSourceSecondsSinceLastEventType against
kCGAnyInputEventType, the same API System Preferences' own "Energy
Saver -> display sleep" timer is built on.
"""
import logging

from .collector_base import ActivityCollector, ForegroundApp

logger = logging.getLogger("agent.monitor.macos")


class MacActivityCollector(ActivityCollector):
    def __init__(self):
        self._pyobjc_available = False
        try:
            from AppKit import NSWorkspace  # noqa: F401
            from Quartz import CGEventSourceSecondsSinceLastEventType, kCGEventSourceStateHIDSystemState, kCGAnyInputEventType  # noqa: F401
            self._pyobjc_available = True
        except ImportError:
            logger.warning("pyobjc (AppKit/Quartz) not installed -- macOS collector cannot function")

    def is_available(self) -> bool:
        return self._pyobjc_available

    def get_foreground_app(self):
        if not self._pyobjc_available:
            return None

        from AppKit import NSWorkspace
        active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if active_app is None:
            return None

        display_name = active_app.localizedName()
        bundle_identifier = active_app.bundleIdentifier()
        # macOS has no "exe name" the way Windows does -- the bundle
        # identifier (e.g. "com.microsoft.VSCode") is the closest stable
        # equivalent, and what ProductivityRule.executableName should be
        # configured with for a macOS-run app.
        executable_name = bundle_identifier or display_name

        window_title = self._get_window_title_if_permitted(active_app.processIdentifier())

        return ForegroundApp(executable_name=executable_name, display_name=display_name, window_title=window_title)

    def _get_window_title_if_permitted(self, pid):
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
            )
        except ImportError:
            return None

        try:
            window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        except Exception:  # noqa: BLE001 -- Quartz can raise plain Exception on permission issues
            return None

        for window in window_list or []:
            if window.get("kCGWindowOwnerPID") == pid:
                title = window.get("kCGWindowName")
                # Without Screen Recording permission, kCGWindowName is
                # simply absent from the dict (not an empty string) --
                # this naturally returns None in that case, exactly the
                # "where OS allows" behavior the spec asks for.
                if title:
                    return str(title)
        return None

    def get_idle_seconds(self) -> float:
        if not self._pyobjc_available:
            return 0.0
        from Quartz import CGEventSourceSecondsSinceLastEventType, kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
        seconds = CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
        return max(0.0, float(seconds))
