"""
The "optional tray/status icon" the spec asks for -- pystray, which draws
a real native tray icon on Windows (system tray), macOS (menu bar), and
Linux (AppIndicator-compatible desktops). No window, no taskbar entry;
this is the entire UI surface of the running agent.

Runs on its own thread (pystray's own requirement on most backends) --
main.py starts it and everything else (sampling, sync, heartbeat) runs
independently on their own threads/timers, so this tray icon locking up
would never stop actual monitoring, and vice versa.
"""
import logging
import threading

logger = logging.getLogger("agent.tray")

STATUS_MONITORING = "Monitoring"
STATUS_CHECKED_OUT = "Checked out"
STATUS_NOT_PAIRED = "Not paired"


def _make_icon_image(color):
    """A plain colored circle -- no image asset to ship, no risk of a
    missing-file crash in a packaged build. Swap for a real .ico/.icns
    asset at packaging time if a branded icon is wanted; functionally
    identical either way."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    return image


class TrayIcon:
    def __init__(self, on_retry_sync, on_quit):
        self._on_retry_sync = on_retry_sync
        self._on_quit = on_quit
        self._status_text = STATUS_NOT_PAIRED
        self._last_sync_text = "never"
        self._icon = None
        self._thread = None

    def _build_menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem(lambda item: f"Status: {self._status_text}", None, enabled=False),
            pystray.MenuItem(lambda item: f"Last sync: {self._last_sync_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Retry sync now", lambda: self._on_retry_sync()),
            pystray.MenuItem("Quit", lambda: self._quit()),
        )

    def start(self):
        import pystray

        color = self._color_for_status()
        self._icon = pystray.Icon("hr_activity_agent", _make_icon_image(color), "HR Activity Agent", self._build_menu())
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _color_for_status(self):
        return {
            STATUS_MONITORING: (34, 197, 94, 255),  # green
            STATUS_CHECKED_OUT: (148, 163, 184, 255),  # gray
            STATUS_NOT_PAIRED: (234, 179, 8, 255),  # amber
        }.get(self._status_text, (148, 163, 184, 255))

    def update_status(self, status_text: str, last_sync_text: str = None):
        self._status_text = status_text
        if last_sync_text is not None:
            self._last_sync_text = last_sync_text
        if self._icon is not None:
            self._icon.icon = _make_icon_image(self._color_for_status())
            self._icon.menu = self._build_menu()

    def _quit(self):
        if self._icon is not None:
            self._icon.stop()
        self._on_quit()

    def stop(self):
        if self._icon is not None:
            self._icon.stop()
