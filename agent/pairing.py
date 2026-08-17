"""
One-time setup: exchange the pairing code the HRMS web app showed after
check-in (see CheckInWidget.jsx's own PairingCodeDialog) for a long-lived
device token. This is the one moment this "no interface" agent needs any
UI at all -- a single native prompt, not a persistent window, and only
ever shown when config.is_paired is False.

Deliberately a plain Tk dialog rather than pulling in a heavier GUI
framework just for one text field -- Tk ships with the Python standard
library on Windows/macOS installers and PyInstaller bundles it
transparently, so it adds no extra dependency to the packaged app.
"""
import logging
import platform

import requests

from .api_client import ApiError

logger = logging.getLogger("agent.pairing")

AGENT_VERSION = "1.0.0"


def _os_name() -> str:
    system = platform.system()
    return {"Windows": "WINDOWS", "Darwin": "MACOS", "Linux": "LINUX"}.get(system, "LINUX")


def prompt_for_pairing_code() -> str | None:
    """Blocking, one-shot native dialog. Returns the entered code, or
    None if the person closed the dialog without entering one (the agent
    then just waits and re-prompts on the next launch, never forcing
    pairing before the tray can even start)."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()  # no main window -- only the modal dialog itself is shown
    code = simpledialog.askstring(
        "Set up desktop activity tracking",
        "Enter the pairing code shown after you checked in on the HR portal:",
        parent=root,
    )
    root.destroy()
    return code.strip() if code else None


def pair_agent(config, api_client) -> bool:
    """Runs the prompt-and-exchange flow once. Returns True if pairing
    succeeded and config now has a live device token."""
    code = prompt_for_pairing_code()
    if not code:
        logger.info("Pairing cancelled by user -- will re-prompt on next launch")
        return False

    try:
        result = api_client.pair(
            pairing_token=code,
            os_name=_os_name(),
            os_version=platform.version(),
            agent_version=AGENT_VERSION,
        )
    except ApiError as err:
        # The server's own actual reason -- an expired/wrong/already-used
        # code, a session that isn't active, etc. -- see agent.service.js's
        # own pairDevice for the exact messages this can be. Showing the
        # real one instead of a fixed generic guess is what actually lets
        # someone tell "this code is genuinely wrong" apart from "the
        # agent can't reach the server at all" below.
        logger.error("Pairing rejected by server (%s): %s", err.code, err)
        _show_error("Pairing failed", str(err))
        return False
    except requests.exceptions.ConnectionError as err:
        # Never reached the server at all -- almost always a wrong
        # api_base_url in config.json, or the backend simply isn't
        # running/reachable from this machine. A very different problem
        # than "the code was wrong," and one this message says outright
        # rather than leaving someone to guess from a vague failure.
        logger.error("Could not reach the server while pairing: %s", err)
        _show_error(
            "Could not reach the server",
            f"Check that api_base_url in config.json is correct and the server is running.\n\nCurrently set to: {api_client.base_url}\n\n{err}",
        )
        return False
    except requests.exceptions.Timeout:
        logger.error("Pairing request timed out")
        _show_error("Could not reach the server", "The request timed out -- the server may be slow or unreachable.")
        return False
    except Exception as err:  # noqa: BLE001 -- any other failure should still let the agent re-prompt, not crash
        logger.error("Pairing failed: %s", err)
        _show_error("Pairing failed", f"Something unexpected went wrong: {err}")
        return False

    config.update(
        device_token=result["deviceToken"],
        device_id=result["deviceId"],
        current_attendance_id=result["attendanceId"],
        monitoring_active=result["config"]["monitoringActive"],
    )
    logger.info("Paired successfully -- device id %s", result["deviceId"])
    return True


def _show_error(title, message):
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:  # noqa: BLE001 -- never let a failed error-dialog crash the agent
        logger.error("%s: %s", title, message)
