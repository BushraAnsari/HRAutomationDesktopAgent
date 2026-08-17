"""
Ensures only one copy of this agent is ever running at a time.

Without this, every test run -- especially one that errored out before
ever finishing pairing -- leaves its own process quietly running behind
it. That's exactly what piled up a dozen "not paired" tray icons during
testing: each failed attempt was still alive, just stuck, and every new
`python -m agent.main` added one more on top instead of replacing it.

On startup, this checks a small PID file in the app-data directory. If
the PID recorded there belongs to a still-running process, that process
is stopped first, before this one continues -- so running the agent
again always means "the current one," never "one more of them."
"""
import logging
import os
import time

logger = logging.getLogger("agent.singleinstance")


def _is_running(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        # Best-effort fallback if psutil somehow isn't available --
        # os.kill(pid, 0) raises if the process doesn't exist, on both
        # POSIX and Windows (Python's os.kill supports signal 0 as a
        # pure existence check on Windows too, despite Windows having no
        # real signal 0 of its own).
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _terminate(pid: int):
    try:
        import psutil
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            # Didn't exit gracefully in time -- force it. A stuck-in-limbo
            # previous instance is exactly the failure mode this whole
            # module exists to clean up; leaving it half-stopped would be
            # worse than the pile-up it's meant to prevent.
            proc.kill()
    except ImportError:
        import signal
        os.kill(pid, signal.SIGTERM)
    except Exception as err:  # noqa: BLE001 -- must never block this instance from starting
        logger.warning("Could not cleanly stop the previous instance (pid %s): %s", pid, err)


def ensure_single_instance(pid_file_path):
    """Call once, at the very start of startup -- before the tray icon,
    before pairing, before anything else. If a previous instance is
    still running, it's stopped first; either way, this process's own
    PID is then written to the same file, so the *next* launch can find
    and replace this one in turn."""
    if pid_file_path.exists():
        try:
            old_pid = int(pid_file_path.read_text().strip())
        except (ValueError, OSError):
            old_pid = None

        if old_pid and old_pid != os.getpid() and _is_running(old_pid):
            logger.info("A previous agent instance (pid %s) is still running -- stopping it before continuing", old_pid)
            _terminate(old_pid)
            # A short pause so the old instance actually releases its own
            # tray icon/registry handles before this one starts claiming
            # the same ones.
            time.sleep(1)

    try:
        pid_file_path.write_text(str(os.getpid()))
    except OSError as err:
        logger.warning("Could not write PID file (single-instance guard may not work next launch): %s", err)
