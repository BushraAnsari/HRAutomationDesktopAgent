"""
Detects whether a known desktop meeting app (Zoom, Microsoft Teams
desktop client, Skype) currently has an open window -- checked across
ALL top-level windows, not just the foreground one this agent's own
main sampling loop already tracks (see collector_base.py).

This is deliberately a SEPARATE signal from foreground-window tracking:
someone can be in a Zoom call while their foreground window is a text
editor where they're taking notes. Foreground-only tracking would show
"Notepad" and lose the meeting entirely; this exists specifically to
catch that case.

Explicitly out of scope here, and worth being clear about: browser-based
meetings (Google Meet, Teams-in-browser) have no separate OS-level
window of their own -- they're just a tab, invisible to window
enumeration the moment they're not the foreground tab. Detecting those
needs a browser extension talking to this agent, a genuinely separate
project from this file.

Windows-only for now, matching this whole agent's own platform
priority (see collector_windows.py) -- macOS/Linux equivalents would use
their own window-enumeration APIs (NSWorkspace's runningApplications on
macOS, EWMH/Xlib on Linux) and are architecture-ready to add the same
way collector_macos.py/collector_linux.py were, not implemented here yet.
"""
import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("agent.monitor.meeting")

user32 = ctypes.windll.user32

# Matched case-insensitively against a window's own title text.
# Deliberately a small, conservative list of patterns that are
# genuinely distinctive of an in-progress *call*, not just "the app is
# open" -- e.g. Teams shows its own title as plain "Microsoft Teams"
# most of the time, and only changes it while actually in a meeting, so
# matching on "Microsoft Teams" alone would over-count "app is open in
# the background doing nothing" as "in a meeting". These patterns are a
# best-effort starting point based on each app's typical behavior, not
# a guarantee across every version/locale -- expect to tune this list
# once tested against real installs.
MEETING_WINDOW_PATTERNS = [
    # Zoom: the actual in-call window is titled "Zoom Meeting" (the
    # separate main/chat window is titled just "Zoom").
    ("Zoom", "zoom meeting"),
    # Teams (classic and new desktop client) shows the meeting title as
    # "... | Microsoft Teams" while a call is active.
    ("Microsoft Teams", "| microsoft teams"),
    # Skype's own in-call window includes "Skype" -- broader than ideal
    # (no separate "in call" marker Skype exposes reliably), kept as a
    # best-effort inclusion rather than skipping Skype entirely.
    ("Skype", "skype"),
]


def _enum_window_titles():
    """Every VISIBLE top-level window's title text, Windows-wide -- not
    just the foreground one. EnumWindows walks the whole z-order; each
    callback return of True means "keep going"."""
    titles = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value:
            titles.append(buf.value)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    try:
        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception as err:  # noqa: BLE001 -- a failed enumeration must never crash the sampling loop
        logger.warning("Window enumeration failed: %s", err)
    return titles


def detect_meeting():
    """Returns (app_name, matched_title) for the first known meeting app
    currently showing an in-call window, or (None, None) if none is
    found. Only the FIRST match is returned -- being in two meeting
    apps' call windows at once is not a real scenario worth designing
    for."""
    titles = _enum_window_titles()
    for title in titles:
        lowered = title.lower()
        for app_name, pattern in MEETING_WINDOW_PATTERNS:
            if pattern in lowered:
                return app_name, title
    return None, None
