"""
Registers the native messaging host with Chrome -- fully automatic, no
prompt, no manual extension ID lookup. This only works because the
Chrome extension's own manifest.json has a "key" field embedded in it
(a fixed RSA public key), which makes Chrome assign it the exact same
extension ID every time it's loaded, instead of a random one -- see
browser-meeting-bridge/extension/manifest.json's own "key" field, and
KEEP_SAFE_signing_key.pem alongside it for the matching private key
(kept only so the same ID can be preserved if this extension is ever
published to the Chrome Web Store later; nothing at runtime uses the
private key at all).

Without that fixed key, Chrome assigns a random ID to an unpacked,
developer-mode-loaded extension -- different every time, with no way
for this agent to know it in advance, which is what used to require a
manual "paste your extension ID" step. Embedding a fixed key removes
that requirement entirely: the ID below is permanent and already known.

Windows-only for now, matching this agent's own general platform
priority (see autostart.py's identical scoping) -- macOS/Linux would use
their own file-placement approach (see the standalone project's
install_macos.sh/install_linux.sh) rather than a registry key, and
aren't wired into this automatic flow yet.
"""
import logging
import sys

from . import config as config_module

logger = logging.getLogger("agent.browser_bridge_setup")

NATIVE_HOST_NAME = "com.hrautomation.activity_agent_bridge"

# Deterministic -- computed once from the fixed RSA key embedded in the
# extension's own manifest.json (see that file's own "key" field). This
# value will always be correct for that manifest, on any machine, with
# no lookup needed -- that's the entire point of embedding a fixed key
# in the first place.
EXTENSION_ID = "cbcmjkmkndkpffeoghongfplmmfpmhdn"


def _manifest_path():
    return config_module.get_app_data_dir() / "native_host_manifest.json"


def _launcher_path():
    return config_module.get_app_data_dir() / "browser_bridge_launcher.bat"


def is_native_host_registered() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}"):
            return True
    except (FileNotFoundError, OSError):
        return False


def register_native_host() -> bool:
    """Writes the launcher script, manifest, and registry key, all to
    this agent's own app-data directory, using the fixed EXTENSION_ID
    above -- nothing here needs any input at all. Returns True on
    success. Safe to call repeatedly (see ensure_browser_bridge_registered
    below, which only calls this once per install)."""
    if sys.platform != "win32":
        return False

    if getattr(sys, "frozen", False):
        launcher_body = f'@echo off\n"{sys.executable}" --native-host\n'
    else:
        # Running from source (python -m agent.main), not a packaged
        # .exe -- the launcher re-invokes the same interpreter the same
        # way, with --native-host appended.
        launcher_body = f'@echo off\n"{sys.executable}" -m agent.main --native-host\n'

    launcher_path = _launcher_path()
    try:
        launcher_path.write_text(launcher_body)
    except OSError as err:
        logger.error("Could not write browser bridge launcher script: %s", err)
        return False

    # JSON needs backslashes doubled.
    escaped_launcher_path = str(launcher_path).replace("\\", "\\\\")
    manifest = (
        "{\n"
        '  "name": "%s",\n' % NATIVE_HOST_NAME
        + '  "description": "Bridges the HR Activity Agent Chrome extension to the desktop activity agent",\n'
        + '  "path": "%s",\n' % escaped_launcher_path
        + '  "type": "stdio",\n'
        + '  "allowed_origins": ["chrome-extension://%s/"]\n' % EXTENSION_ID
        + "}\n"
    )
    manifest_path = _manifest_path()
    try:
        manifest_path.write_text(manifest)
    except OSError as err:
        logger.error("Could not write native messaging host manifest: %s", err)
        return False

    try:
        import winreg
        key_path = rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
    except OSError as err:
        logger.error("Could not register native messaging host with Chrome: %s", err)
        return False

    logger.info("Browser meeting bridge registered automatically (extension ID %s)", EXTENSION_ID)
    return True


def ensure_browser_bridge_registered():
    """Called on every agent startup (see main.py) -- idempotent and
    silent: does nothing if already registered from a previous run, and
    needs no prompt or user input at all if it isn't, since the
    extension ID is fixed (see EXTENSION_ID above). The only remaining
    manual step, ever, is loading the extension itself in Chrome (see
    the browser-meeting-bridge project's own README) -- nothing about
    the native messaging host registration requires a person to do
    anything."""
    if sys.platform != "win32":
        return
    if is_native_host_registered():
        return
    register_native_host()
