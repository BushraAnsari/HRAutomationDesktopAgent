"""
Registers this agent to launch automatically at Windows login -- the
".exe sets itself up, no separate IT step" property being built for
here. Uses the Windows registry's own per-user Run key
(HKEY_CURRENT_USER), not Task Scheduler or the Startup folder -- this
needs no admin rights at all, and is the same mechanism most consumer
background apps (chat clients, cloud sync tools) already use for
exactly this purpose.

Deliberately only takes effect once this is actually running as the
packaged .exe (see the sys.frozen check below, set by PyInstaller) --
registering a `python -m agent.main` invocation from a dev venv would be
actively wrong: a relative module invocation with no real interpreter
path baked in, silently persisting across every reboot just from
testing it once. Development/testing keeps using run_agent.bat +
schtasks (see packaging/build_windows.md) exactly as before; this is
what replaces that manual step for the real, distributed build.
"""
import logging
import sys

logger = logging.getLogger("agent.autostart")

# Whatever name shows up in Windows' own Startup Apps settings page --
# deliberately human-readable, since an employee (or their IT support)
# may well go looking for it there someday.
RUN_KEY_NAME = "HR Activity Agent"


def ensure_autostart_registered():
    """Called once, early, on every launch (see main.py). Idempotent and
    cheap -- only actually writes to the registry the first time, or if
    the exe's own path ever changes (e.g. a version update moved it)."""
    if not getattr(sys, "frozen", False):
        logger.debug("Not running as a packaged executable -- skipping autostart registration")
        return
    if sys.platform != "win32":
        # macOS's equivalent is a LaunchAgent plist, set up at install
        # time instead (see packaging/build_macos.md) -- a launch-at-login
        # entry there is normally created by the installer/DMG process,
        # not self-registered by the running app the way Windows allows
        # via a simple per-user registry key.
        return

    try:
        import winreg

        exe_path = sys.executable
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                existing, _ = winreg.QueryValueEx(key, RUN_KEY_NAME)
            except FileNotFoundError:
                existing = None
            if existing == exe_path:
                return  # already registered, nothing to do
            winreg.SetValueEx(key, RUN_KEY_NAME, 0, winreg.REG_SZ, exe_path)
            logger.info("Registered for auto-start at login: %s", exe_path)
    except Exception as err:  # noqa: BLE001 -- must never block the agent from actually running
        logger.warning("Could not register for auto-start (agent will still run normally): %s", err)


def remove_autostart_registration():
    """Not currently called from anywhere in this codebase -- kept here
    as the clean, obvious counterpart for a future uninstaller/settings
    option, since "how do I make this stop auto-starting" is a real
    question an IT admin or employee will eventually ask."""
    if sys.platform != "win32":
        return
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, RUN_KEY_NAME)
                logger.info("Removed auto-start registration")
            except FileNotFoundError:
                pass
    except Exception as err:  # noqa: BLE001
        logger.warning("Could not remove auto-start registration: %s", err)
