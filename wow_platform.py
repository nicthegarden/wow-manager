"""Cross-platform helpers for WoW Realmlist Manager (Linux / Windows / macOS).

Stdlib only. Everything OS-specific lives here so the GTK and Tk apps
stay portable: same config shape, same Play flow, per-OS launch details.
"""
import os
import shlex
import signal
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def default_launcher():
    """Launcher command used when none is configured.

    Linux/macOS need Wine to run WoW.exe; Windows runs it directly.
    """
    return "" if IS_WINDOWS else "wine"


def split_args(text):
    """Split a launcher/args string. Windows keeps backslash paths intact."""
    return shlex.split(text, posix=not IS_WINDOWS)


def build_argv(target, launcher, args):
    """Build argv from launcher + extra args + target.

    Blank launcher -> OS default (wine on Linux/macOS, direct on Windows).
    With a launcher (wine-style): launcher + args + target.
    Direct start (no launcher): target + args.
    """
    launcher = (launcher or "").strip() or default_launcher()
    parts = split_args(launcher) if launcher else []
    extra = split_args(args) if (args or "").strip() else []
    if parts:
        return parts + extra + [target]
    return [target] + extra


def describe_argv(argv):
    return " ".join(argv)


def launch_detached(argv, cwd, env=None):
    """Start a process that survives manager exit. Returns the Popen object."""
    if IS_WINDOWS:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(argv, cwd=str(cwd), env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=flags)
    return subprocess.Popen(argv, cwd=str(cwd), env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def kill_pid(pid, force=False):
    """Terminate a launched process. Returns (ok, error_message)."""
    if IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(pid)] + (["/F"] if force else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                return True, ""
            out = ((r.stdout or "") + (r.stderr or "")).lower()
            if "not found" in out or "no tasks" in out or "does not exist" in out:
                return False, "process already gone"
            return False, ((r.stdout or "") + (r.stderr or "")).strip() or f"taskkill exited {r.returncode}"
        except FileNotFoundError:
            pass  # fall through to os.kill
        except Exception as e:
            return False, str(e)
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True, ""
    except ProcessLookupError:
        return False, "process already gone"
    except PermissionError:
        return False, "permission denied"
    except Exception as e:
        return False, str(e)


def proc_running(name):
    """True if a process called `name` seems to be running (for run_extra)."""
    try:
        if IS_WINDOWS:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}",
                                "/FO", "CSV", "/NH"],
                               capture_output=True, text=True)
            out = (r.stdout or "").lower()
            return name.lower() in out and "no tasks" not in out
        r = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


def open_folder(path):
    """Reveal a folder in the OS file manager. Raises on failure."""
    path = str(path)
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - local path from own config
    elif IS_MAC:
        subprocess.Popen(["open", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def known_profile_paths():
    """Version -> [candidate WoW.exe locations] probed to pre-fill profiles."""
    if IS_WINDOWS:
        return {
            "1.12.1": [r"C:\Games\WoW-1.12\WoW.exe",
                       r"C:\Games\WoW 1.12\WoW.exe",
                       r"C:\World of Warcraft 1.12\WoW.exe"],
            "2.4.3": [r"C:\Games\WoW-2.4.3\WoW.exe",
                      r"C:\Games\WoW 2.4.3\WoW.exe",
                      r"C:\World of Warcraft 2.4.3\WoW.exe"],
            "3.3.5a": [r"C:\Games\WoW-3.3.5\WoW.exe",
                       r"C:\Games\WoW 3.3.5\WoW.exe",
                       r"C:\World of Warcraft 3.3.5\WoW.exe",
                       r"C:\Program Files (x86)\World of Warcraft\WoW.exe"],
        }
    if IS_MAC:
        return {
            "1.12.1": ["/Applications/WoW-1.12/WoW.app/Contents/MacOS/WoW"],
            "2.4.3": ["/Applications/WoW-2.4.3/WoW.app/Contents/MacOS/WoW"],
            "3.3.5a": ["/Applications/World of Warcraft/WoW.app/Contents/MacOS/WoW"],
        }
    return {
        "1.12.1": ["/mnt/hdd/Wow - VANILLA/WoW.exe"],
        "2.4.3": ["/mnt/hdd/Wow - TBC/WoW.exe"],
        "3.3.5a": ["/mnt/hdd/Wow - WOTLK/WoW.exe"],
    }


def wineprefix_supported():
    """WINEPREFIX only means something under Wine (not on Windows)."""
    return not IS_WINDOWS
