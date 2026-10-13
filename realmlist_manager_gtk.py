"""WoW Realmlist Manager (GTK4, no extra deps).
Replicates 'Realmlist modifier': main list + Settings + Add server (Basic/Additional).
Runs on Linux, Windows and macOS (see wow_platform.py for OS differences).
"""
import json
import os
import re
import shutil
import socket
import threading
import time
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

import wow_platform as wplat

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Pango, Gdk

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "servers.json"

APP_VERSION = "4.5.3"

DEFAULT_SETTINGS = {
    "profiles": {"1.12.1": "", "2.4.3": "", "3.3.5a": ""},
    "default_launcher": "wine",
    "default_wineprefix": "",
    "default_args": "",
    "default_windowed": False,
    "run_extra": False,
    "extra_path": "",
    "extra_skip_if_running": False,
    "timeout_ms": 3000,
    "save_window_pos": True,
    "check_updates": "Once a day",
}

# Probed on first run / Autodetect to pre-fill empty profile slots (per-OS).
KNOWN_PROFILE_PATHS = wplat.known_profile_paths()
KNOWN_CLIENT_PATHS = [
    ("/mnt/hdd/Wow - VANILLA/WoW.exe", "1.12.1"),
    ("/mnt/hdd/Wow - TBC/WoW.exe", "2.4.3"),
    ("/mnt/hdd/Wow - WOTLK/Wow.exe", "3.3.5a"),
]

# Three fixed client profiles. Each version maps to exactly one WoW.exe path.
PROFILE_VERSIONS = ["1.12.1", "2.4.3", "3.3.5a"]

PROFILE_LABELS = {
    "1.12.1": "1.12.1 (Vanilla)",
    "2.4.3": "2.4.3 (TBC)",
    "3.3.5a": "3.3.5a (WotLK)",
}

DEFAULT_SERVERS = [
    {"name": "OctoWoW", "address": "play.octowow.st",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "windowed": False, "auto_clear": False, "extra_path": "",
     "server_type": "1.12.1", "site": "https://octowow.st", "info": ""},
    {"name": "FrozenThrone", "address": "frozenthrone.co",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "windowed": False, "auto_clear": False, "extra_path": "",
     "server_type": "3.3.5a", "site": "https://frozenthrone.co", "info": ""},
    {"name": "Warmane", "address": "logon.warmane.com",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "windowed": False, "auto_clear": False, "extra_path": "",
     "server_type": "3.3.5a", "site": "https://warmane.com", "info": ""},
]

CSS = """
window { background: #0d0d0d; }
.gold { color: #d8a920; }
.listbox { background: #141414; border: 1px solid #d8a920; }
.listbox row { padding: 4px 8px; }
.listbox row:selected { background: #3a2f10; }
.bigplay { font-size: 20px; font-weight: bold; color: #d8a920; }
.dim { color: #8a6d15; }
.val { color: #e8d98a; }
.msg label { color: #e8d98a; }
button { background: #1a1a1a; color: #d8a920; border: 1px solid #8a6d15; border-radius: 4px; padding: 4px 12px; }
button:hover { background: #3a2f10; color: #ffe9a0; }
button:disabled { color: #555555; border-color: #444444; }
entry { background: #141414; color: #e8d98a; border: 1px solid #8a6d15; border-radius: 4px; padding: 4px 6px; caret-color: #d8a920; }
entry:disabled { background: #0d0d0d; color: #666666; }
checkbutton { color: #e8d98a; }
notebook { background: #0d0d0d; }
notebook header { background: #0d0d0d; border-bottom: 1px solid #8a6d15; }
notebook tab { background: #141414; color: #8a6d15; padding: 6px 14px; margin: 2px; }
notebook tab:checked { background: #3a2f10; color: #d8a920; }
headerbar { background: #141414; color: #d8a920; border-bottom: 1px solid #8a6d15; }
headerbar label { color: #d8a920; }
spinbutton { background: #141414; color: #e8d98a; }
dropdown { background: #141414; color: #e8d98a; border: 1px solid #8a6d15; border-radius: 4px; }
"""


def autodetect_wow_exe():
    """Look for WoW.exe next to this script (same folder), case-insensitive.

    Returns the path as str, or None if nothing found.
    """
    try:
        for p in APP_DIR.iterdir():
            if p.is_file() and p.name.lower() == "wow.exe":
                return str(p)
        for p in APP_DIR.iterdir():
            if p.is_file() and p.suffix.lower() == ".exe" and "wow" in p.name.lower():
                return str(p)
    except Exception:
        pass
    return None


def load_data():
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text())
            servers = d.get("servers", DEFAULT_SERVERS)
            settings = {**DEFAULT_SETTINGS, **d.get("settings", {})}
            settings["profiles"] = dict(settings.get("profiles") or {})
            if isinstance(d.get("profiles"), dict):
                for v in PROFILE_VERSIONS:
                    if not (settings["profiles"].get(v) or "").strip() and d["profiles"].get(v):
                        settings["profiles"][v] = d["profiles"][v]
            _ensure_profiles(servers, settings)
            # fill missing per-server keys for forward compat, drop the dead rss field
            for s in servers:
                s.setdefault("windowed", False)
                s.setdefault("extra_path", "")
                s.pop("rss", None)
            save_data(servers, settings)
            return servers, settings
        except Exception:
            pass
    # first run: generate servers.json right next to this script,
    # preferring a WoW.exe found in the same folder
    servers = [dict(s) for s in DEFAULT_SERVERS]
    settings = dict(DEFAULT_SETTINGS)
    settings["profiles"] = dict(DEFAULT_SETTINGS["profiles"])
    _ensure_profiles(servers, settings)
    try:
        save_data(servers, settings)
    except Exception:
        pass
    return servers, settings


def _ensure_profiles(servers, settings):
    """Fill the 3 fixed profile slots and migrate legacy configs.

    Migration sources (only fill empty slots, never overwrite):
    - old settings["profiles"] values are kept as-is (unknown versions dropped)
    - old settings["clients"] [{name, version, path}] -> profiles[version]
    - old settings["wow_exe"] -> guessed version slot, else first empty slot
    - old per-server alt_wow/wow_path -> that server's server_type slot
    - KNOWN_PROFILE_PATHS probe + WoW.exe next to the script
    """
    profiles = settings.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    for v in PROFILE_VERSIONS:
        profiles.setdefault(v, "")
    for v in [key for key in profiles if key not in PROFILE_VERSIONS]:
        del profiles[v]
    settings["profiles"] = profiles
    if wplat.IS_WINDOWS and (settings.get("default_launcher") or "").strip().lower() == "wine":
        # carried over from Linux or factory default: Wine doesn't run on Windows
        settings["default_launcher"] = ""
    for k in ("default_launcher", "default_wineprefix", "default_args", "default_windowed"):
        settings.setdefault(k, DEFAULT_SETTINGS[k])

    def guess_version(path):
        p = str(path or "").upper()
        if "VANILLA" in p:
            return "1.12.1"
        if "WOTLK" in p:
            return "3.3.5a"
        if "TBC" in p:
            return "2.4.3"
        return ""

    def fill(version, path):
        if not path:
            return
        if version not in PROFILE_VERSIONS:
            return
        if (profiles.get(version) or "").strip():
            return
        if Path(path).is_file():
            profiles[version] = path

    raw = settings.pop("clients", None)
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict):
                fill((c.get("version") or "").strip(), (c.get("path") or "").strip())
    old_exe = (settings.pop("wow_exe", "") or "").strip()
    if old_exe:
        fill(guess_version(old_exe) or "3.3.5a", old_exe)
    for s in servers if isinstance(servers, list) else []:
        if isinstance(s, dict) and s.get("alt_wow") and (s.get("wow_path") or "").strip():
            fill((s.get("server_type") or "").strip(), s["wow_path"].strip())
    for ver, paths in KNOWN_PROFILE_PATHS.items():
        for p in paths:
            if not (profiles.get(ver) or "").strip() and Path(p).is_file():
                profiles[ver] = p
                break
    nearby = autodetect_wow_exe()
    if nearby:
        taken = {p for p in profiles.values() if p}
        if nearby not in taken:
            for s in servers if isinstance(servers, list) else []:
                st = (s.get("server_type") or "").strip() if isinstance(s, dict) else ""
                if st in PROFILE_VERSIONS and not (profiles.get(st) or "").strip():
                    profiles[st] = nearby
                    break
            else:
                for v in PROFILE_VERSIONS:
                    if not (profiles.get(v) or "").strip():
                        profiles[v] = nearby
                        break
    for s in servers if isinstance(servers, list) else []:
        if isinstance(s, dict) and (s.get("server_type") or "").strip() not in PROFILE_VERSIONS:
            s["server_type"] = "3.3.5a"
    settings["profiles"] = profiles


def _guess_version(path):
    p = str(path or "").upper()
    if "VANILLA" in p:
        return "1.12.1"
    if "WOTLK" in p:
        return "3.3.5a"
    if "TBC" in p:
        return "2.4.3"
    return ""


def get_client_by_name(settings, name):
    # Backwards-compat shim: old code looked up clients by name.
    # The simplified model has no named clients; return None.
    return None


def save_data(servers, settings):
    CONFIG_FILE.write_text(json.dumps({"servers": servers, "settings": settings}, indent=2))


def ping_realm(host, port=3724, timeout_ms=3000):
    host = (host or "").strip().replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
    if not host:
        return False, None
    t0 = time.time()
    try:
        ip = socket.gethostbyname(host)
        s = socket.create_connection((ip, port), timeout=timeout_ms / 1000.0)
        s.close()
        return True, int((time.time() - t0) * 1000)
    except Exception:
        return False, None


def server_version(server):
    """Which of the 3 profiles a server needs (defaults to 3.3.5a)."""
    v = ((server or {}).get("server_type") or "").strip()
    return v if v in PROFILE_VERSIONS else "3.3.5a"


def profile_exe(version, settings):
    """WoW.exe path programmed for a profile version ('' if unset)."""
    try:
        return ((settings or {}).get("profiles", {}) or {}).get(version, "") or ""
    except Exception:
        return ""


def effective_wow_exe(server, settings):
    """Resolve which WoW.exe to use: profiles[server_type]."""
    return profile_exe(server_version(server), settings)


def effective_client(server, settings):
    # Backwards-compat shim: no per-server client objects any more.
    return None


def effective_launch(server, settings):
    """Resolve (launcher, wineprefix, args) from the global defaults.

    Blank launcher means the OS default (wine, or direct start on Windows).
    Wineprefix is Wine-only and ignored on Windows.
    """
    settings = settings or {}
    launcher = (settings.get("default_launcher") or "").strip()
    wineprefix = (settings.get("default_wineprefix") or "").strip()
    args = (settings.get("default_args") or "").strip()
    return launcher, wineprefix, args


def build_launch_cmd(target, launcher, args):
    """Build argv: launcher + extra args + [target] (see wow_platform.build_argv).

    Blank launcher -> OS default: wine on Linux/macOS, direct start on Windows.
    """
    return wplat.build_argv(target, launcher, args)


def describe_launch(server, settings):
    launcher, wineprefix, args = effective_launch(server, settings)
    cmd = " ".join(build_launch_cmd("<WoW>", launcher, args))
    if wineprefix and wplat.wineprefix_supported():
        cmd = f"WINEPREFIX={wineprefix} {cmd}"
    return cmd


def describe_client(server, settings):
    """Short label, e.g. '3.3.5a (WotLK): /path/WoW.exe' or '3.3.5a (not set)'."""
    v = server_version(server)
    label = PROFILE_LABELS.get(v, v)
    exe = profile_exe(v, settings)
    if exe and Path(exe).is_file():
        return f"{label}"
    if exe:
        return f"{label} (missing!)"
    return f"{label} (not set)"


def wow_dir_of(wow_exe_path):
    """Folder holding the client: parent dir of WoW.exe, or the path itself if a dir was given."""
    if not (wow_exe_path or "").strip():
        return None
    exe = Path(wow_exe_path)
    if exe.is_dir():
        return exe
    if exe.suffix.lower() == ".exe":
        return exe.parent
    return Path(wow_exe_path)


def locate_realmlists(wow_exe_path):
    """Find every realmlist.wtf under the configured WoW folder (case-insensitive).

    Covers 1.12 clients (root realmlist.wtf) and 2.x/3.x clients
    (Data/<locale>/realmlist.wtf) without assuming a fixed layout.
    Always includes <wow_dir>/realmlist.wtf as the fallback target.
    Returns a sorted list of Paths, deduplicated.
    """
    wow_dir = wow_dir_of(wow_exe_path)
    if wow_dir is None:
        return []
    found = []
    if wow_dir.is_dir():
        try:
            for p in wow_dir.rglob("*"):
                if p.is_file() and p.name.lower() == "realmlist.wtf":
                        found.append(p)
        except Exception:
            pass
    root = wow_dir / "realmlist.wtf"
    if not found:
        # nothing on disk: fall back to the classic 1.12 location
        return [root]
    found = sorted({str(p) for p in found})
    if str(root) not in found:
        # 3.x clients read Data/<locale>/realmlist.wtf but keep a root copy too;
        # always update both so switching clients never leaves a stale file.
        found.append(str(root))
    # keep the root file first if present (most clients read it first)
    found.sort(key=lambda s: (Path(s) != root, s))
    return [Path(s) for s in found]


def write_realmlist(wow_exe_path, address, patchlist="", account="", windowed=False):
    wow_dir = wow_dir_of(wow_exe_path)
    if wow_dir is None:
        return False, "no WoW path configured"
    lines = [f"set realmlist {address}"]
    if patchlist:
        lines.append(f"set patchlist {patchlist}")
    content = "\n".join(lines) + "\n"
    written = []
    try:
        for t in locate_realmlists(wow_exe_path):
            try:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(content)
                written.append(t)
            except Exception:
                pass  # keep going; one bad file must not block the others
    except Exception as e:
        return False, str(e)
    if not written:
        return False, f"no writable realmlist.wtf under {wow_dir}"
    cfg = wow_dir / "WTF" / "Config.wtf"
    try:
        if cfg.exists():
            clines = cfg.read_text(errors="ignore").splitlines()
            out, f1, f2, f3, f4 = [], False, False, False, False
            for ln in clines:
                u = ln.strip().upper()
                if u.startswith("SET REALMLIST"):
                    out.append(f'set realmlist {address}'); f1 = True
                elif u.startswith("SET PATCHLIST"):
                    if patchlist:
                        out.append(f'set patchlist {patchlist}'); f2 = True
                    # else drop patchlist line
                elif u.startswith("SET ACCOUNTNAME"):
                    if account:
                        out.append(f'SET accountName "{account}"'); f3 = True
                    # else leave the existing account untouched
                    else:
                        out.append(ln); f3 = True
                elif u.startswith("SET GXWINDOW"):
                    # windowed launch requested: force windowed; otherwise keep user's value
                    out.append('SET gxWindow "1"' if windowed else ln); f4 = True
                else:
                    out.append(ln)
            if not f1:
                out.append(f'set realmlist {address}')
            if patchlist and not f2:
                out.append(f'set patchlist {patchlist}')
            if account and not f3:
                out.append(f'SET accountName "{account}"')
            if windowed and not f4:
                out.append('SET gxWindow "1"')
            cfg.write_text("\n".join(out) + "\n")
        elif windowed:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(f'set realmlist {address}\nSET gxWindow "1"\n')
    except Exception as e:
        return False, str(e)
    targets = [str(t) for t in written] + ([str(cfg)] if cfg.exists() else [])
    return True, "\n".join(targets)


def effective_windowed(server, settings):
    """Windowed-mode launch? Per-server checkbox OR global default (OR semantics)."""
    return bool((server or {}).get("windowed", False) or (settings or {}).get("default_windowed", False))


def clear_wdb(wow_exe_path):
    wow_dir = wow_dir_of(wow_exe_path)
    if wow_dir is None:
        return
    wdb = wow_dir / "WDB"
    if wdb.exists():
        for p in wdb.rglob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass


def proc_running(name):
    """Is a process called `name` running? (pgrep on Linux/macOS, tasklist on Windows.)"""
    return wplat.proc_running(name)


def strip_wow_colors(text):
    return re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", text or "")


def list_addons(wow_exe_path):
    """Return [{'dir': Path, 'name': str, 'title': str, 'version': str, 'enabled': bool}] for AddOns dir."""
    wow_dir = wow_dir_of(wow_exe_path)
    if wow_dir is None:
        return []
    addons_dir = wow_dir / "Interface" / "AddOns"
    out = []
    if not addons_dir.is_dir():
        return out
    for child in sorted(addons_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() and not (child.is_symlink()):
            continue
        if not child.is_dir():
            continue
        name = child.name
        enabled = True
        if name.lower().endswith(".disabled"):
            enabled = False
            name = name[: -len(".disabled")]
        title, version = name, ""
        tocs = [f for f in child.iterdir() if f.is_file() and f.suffix.lower() == ".toc"]
        if tocs:
            try:
                for ln in tocs[0].read_text(errors="ignore").splitlines():
                    if ln.startswith("## Title:"):
                        title = strip_wow_colors(ln.split(":", 1)[1].strip()) or name
                    elif ln.startswith("## Version:"):
                        version = ln.split(":", 1)[1].strip()
            except Exception:
                pass
        out.append({"dir": child, "name": name, "title": title, "version": version, "enabled": enabled})
    return out


def pid_alive(pid):
    return wplat.pid_alive(pid)


def proc_name(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return "?"


def kill_pid(pid, force=False):
    """Terminate a process (signals on Linux/macOS, taskkill on Windows)."""
    return wplat.kill_pid(pid, force=force)


def show_message(parent, message_type, text):
    """Orange-themed message popup (.msg styling), auto-closes on response."""
    m = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=message_type,
                          buttons=Gtk.ButtonsType.OK, text=text)
    m.add_css_class("msg")
    m.connect("response", lambda *a: m.close())
    m.present()
    return m


class SettingsDialog(Gtk.Dialog):
    """Settings: 3 fixed WoW profiles (one WoW.exe per version) + launch defaults."""
    def __init__(self, parent, settings):
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(560, 520)
        self.settings = settings
        self.add_buttons("Save", Gtk.ResponseType.OK, "Cancel", Gtk.ResponseType.CANCEL)
        profiles = dict((settings.get("profiles") or {}))
        for v in PROFILE_VERSIONS:
            profiles.setdefault(v, "")
        outer = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        outer.set_min_content_height(400)
        self.get_content_area().append(outer)
        g = Gtk.Grid(column_spacing=6, row_spacing=6)
        g.set_margin_top(10); g.set_margin_bottom(10); g.set_margin_start(10); g.set_margin_end(10)
        outer.set_child(g)

        head = Gtk.Label(label="WoW clients: one WoW.exe per version.", halign=Gtk.Align.START)
        head.add_css_class("gold")
        g.attach(head, 0, 0, 2, 1)
        note0 = Gtk.Label(label="Pick the WoW.exe for each version you play. Play uses the profile matching the server's version.")
        note0.set_wrap(True)
        note0.add_css_class("dim")
        g.attach(note0, 0, 1, 2, 1)
        self.profile_entries = {}
        row = 2
        for v in PROFILE_VERSIONS:
            ver_lbl = Gtk.Label(label=f"{PROFILE_LABELS.get(v, v)}:", halign=Gtk.Align.START)
            ver_lbl.add_css_class("val")
            g.attach(ver_lbl, 0, row, 1, 1)
            hb = Gtk.Box(spacing=6, hexpand=True)
            entry = Gtk.Entry(hexpand=True, placeholder_text=".../WoW.exe")
            entry.set_text(profiles.get(v, ""))
            ok = Path(entry.get_text()).is_file() if entry.get_text().strip() else False
            entry.set_tooltip_text("OK" if ok else "Not set - pick WoW.exe")
            b_search = Gtk.Button(label="Search...")
            b_search.connect("clicked", lambda *a, e=entry: self.pick_file(e))
            b_loc = Gtk.Button(label="Locate realmlist")
            b_loc.connect("clicked", lambda *a, e=entry: self.show_located(e.get_text()))
            hb.append(entry); hb.append(b_search); hb.append(b_loc)
            g.attach(hb, 1, row, 1, 1)
            self.profile_entries[v] = entry
            row += 1
        b_auto = Gtk.Button(label="Autodetect")
        b_auto.set_tooltip_text("Probe standard install locations for empty slots")
        b_auto.connect("clicked", lambda *a: self.autodetect())
        g.attach(b_auto, 0, row, 2, 1)
        row += 1

        launch_head = Gtk.Label(label="Launcher (used for all profiles):", halign=Gtk.Align.START)
        launch_head.add_css_class("gold")
        g.attach(launch_head, 0, row, 2, 1)
        row += 1
        defgrid = Gtk.Grid(column_spacing=6, row_spacing=6)
        g.attach(defgrid, 0, row, 2, 1)
        launcher_lbl = Gtk.Label(label="Launcher:", halign=Gtk.Align.START)
        launcher_lbl.add_css_class("dim")
        defgrid.attach(launcher_lbl, 0, 0, 1, 1)
        self.def_launcher = Gtk.Entry(hexpand=True, placeholder_text=wplat.default_launcher() or "(start directly)")
        self.def_launcher.set_text(settings.get("default_launcher", wplat.default_launcher()))
        if wplat.IS_WINDOWS:
            self.def_launcher.set_tooltip_text("Optional: a wrapper to start WoW through (e.g. gamemoderun). Blank = start WoW.exe directly.")
        defgrid.attach(self.def_launcher, 1, 0, 1, 1)
        prefix_lbl = Gtk.Label(label="WINEPREFIX:", halign=Gtk.Align.START)
        prefix_lbl.add_css_class("dim")
        defgrid.attach(prefix_lbl, 0, 1, 1, 1)
        self.def_prefix = Gtk.Entry(hexpand=True, placeholder_text="(none)")
        self.def_prefix.set_text(settings.get("default_wineprefix", ""))
        defgrid.attach(self.def_prefix, 1, 1, 1, 1)
        if not wplat.wineprefix_supported():
            # Wine-only option: grey out on Windows where it does nothing.
            prefix_lbl.set_sensitive(False)
            prefix_lbl.set_tooltip_text("Wine-only: not used on Windows.")
            self.def_prefix.set_sensitive(False)
            self.def_prefix.set_tooltip_text("Wine-only: not used on Windows.")
        args_lbl = Gtk.Label(label="Extra args:", halign=Gtk.Align.START)
        args_lbl.add_css_class("dim")
        defgrid.attach(args_lbl, 0, 2, 1, 1)
        self.def_args = Gtk.Entry(hexpand=True, placeholder_text="(none)")
        self.def_args.set_text(settings.get("default_args", ""))
        defgrid.attach(self.def_args, 1, 2, 1, 1)
        self.def_windowed = Gtk.CheckButton(label="Start windowed by default (SET gxWindow on Play)")
        self.def_windowed.set_active(settings.get("default_windowed", False))
        defgrid.attach(self.def_windowed, 0, 3, 2, 1)
        row += 1

        self.run_extra = Gtk.CheckButton(label="Run another program together with WoW")
        self.run_extra.set_active(settings.get("run_extra", False))
        g.attach(self.run_extra, 0, row, 2, 1)
        row += 1
        self.skip_run = Gtk.CheckButton(label="Do not run, if it's already running")
        self.skip_run.set_active(settings.get("extra_skip_if_running", False))
        g.attach(self.skip_run, 0, row, 2, 1)
        row += 1
        self.extra_entry = Gtk.Entry(hexpand=True)
        self.extra_entry.set_text(settings.get("extra_path", ""))
        g.attach(self.extra_entry, 0, row, 1, 1)
        b_search2 = Gtk.Button(label="Search...")
        b_search2.connect("clicked", lambda *a: self.pick_file(self.extra_entry))
        g.attach(b_search2, 1, row, 1, 1)
        row += 1

        timeout_lbl = Gtk.Label(label="Connection timeout (milliseconds):", halign=Gtk.Align.START)
        timeout_lbl.add_css_class("dim")
        g.attach(timeout_lbl, 0, row, 1, 1)
        self.timeout = Gtk.SpinButton.new_with_range(500, 15000, 500)
        self.timeout.set_value(settings.get("timeout_ms", 3000))
        g.attach(self.timeout, 1, row, 1, 1)
        row += 1

        self.save_pos = Gtk.CheckButton(label="Save window position")
        self.save_pos.set_active(settings.get("save_window_pos", True))
        g.attach(self.save_pos, 0, row, 2, 1)
        row += 1

        upd_lbl = Gtk.Label(label="Check program updates automatically:", halign=Gtk.Align.START)
        upd_lbl.add_css_class("dim")
        g.attach(upd_lbl, 0, row, 1, 1)
        self.upd = Gtk.DropDown.new_from_strings(["Never", "Once a day", "Once a week"])
        try:
            self.upd.set_selected(["Never", "Once a day", "Once a week"].index(settings.get("check_updates", "Once a day")))
        except ValueError:
            self.upd.set_selected(1)
        g.attach(self.upd, 1, row, 1, 1)

    def pick_file(self, entry):
        d = Gtk.FileChooserDialog(title="Select WoW.exe", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        d.connect("response", lambda dlg, r: (entry.set_text(dlg.get_file().get_path()) if r == Gtk.ResponseType.ACCEPT and dlg.get_file() else None, dlg.close()))
        d.present()

    def show_located(self, path, title="realmlist.wtf file(s) that Play will update:"):
        files = locate_realmlists(path)
        if not files:
            msg = "No WoW path set - pick WoW.exe first."
        else:
            existing = [str(p) for p in files if p.exists()]
            msg = title + "\n" + (
                "\n".join(existing) if existing else f"(none found yet - will create {files[0]})")
        show_message(self, Gtk.MessageType.INFO, msg)

    def autodetect(self):
        for ver, paths in KNOWN_PROFILE_PATHS.items():
            entry = self.profile_entries.get(ver)
            if entry is None:
                continue
            if entry.get_text().strip():
                continue
            for p in paths:
                if Path(p).is_file():
                    entry.set_text(p)
                    break

    def result(self):
        return {
            "profiles": {v: e.get_text().strip() for v, e in self.profile_entries.items()},
            "default_launcher": self.def_launcher.get_text().strip() or wplat.default_launcher(),
            "default_wineprefix": self.def_prefix.get_text().strip(),
            "default_args": self.def_args.get_text().strip(),
            "default_windowed": self.def_windowed.get_active(),
            "run_extra": self.run_extra.get_active(),
            "extra_path": self.extra_entry.get_text().strip(),
            "extra_skip_if_running": self.skip_run.get_active(),
            "timeout_ms": int(self.timeout.get_value()),
            "save_window_pos": self.save_pos.get_active(),
            "check_updates": ["Never", "Once a day", "Once a week"][self.upd.get_selected()],
        }


class ServerDialog(Gtk.Dialog):
    """Add/Edit server: name + address + which of the 3 profiles it needs."""
    def __init__(self, parent, server=None, clients=None):
        super().__init__(title="Add server" if server is None else "Edit server", transient_for=parent, modal=True)
        self.add_buttons("Add" if server is None else "Save", Gtk.ResponseType.OK, "Cancel", Gtk.ResponseType.CANCEL)
        s = server or {}
        nb = Gtk.Notebook()
        nb.set_margin_top(8); nb.set_margin_bottom(8); nb.set_margin_start(8); nb.set_margin_end(8)
        self.get_content_area().append(nb)

        # Basic tab
        basic = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        basic.set_margin_top(30); basic.set_margin_bottom(30); basic.set_margin_start(30); basic.set_margin_end(30)
        nb.append_page(basic, Gtk.Label(label="Basic settings"))

        def dim_label(text):
            lbl = Gtk.Label(label=text, halign=Gtk.Align.START)
            lbl.add_css_class("dim")
            return lbl

        basic.append(dim_label("Server name:"))
        self.e_name = Gtk.Entry(); self.e_name.set_text(s.get("name", ""))
        basic.append(self.e_name)
        basic.append(dim_label("Server address:"))
        self.e_addr = Gtk.Entry(); self.e_addr.set_text(s.get("address", s.get("realmlist", "")))
        basic.append(self.e_addr)
        basic.append(dim_label("Account name (optional):"))
        self.e_acct = Gtk.Entry(); self.e_acct.set_text(s.get("account", ""))
        basic.append(self.e_acct)
        basic.append(dim_label("Client version (picks one of the 3 profiles):"))
        self.e_type = Gtk.DropDown.new_from_strings(PROFILE_VERSIONS)
        cur = (s.get("server_type") or "").strip()
        self.e_type.set_selected(PROFILE_VERSIONS.index(cur) if cur in PROFILE_VERSIONS else 2)
        basic.append(self.e_type)

        # Additional tab
        adv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        adv.set_margin_top(8); adv.set_margin_bottom(8); adv.set_margin_start(8); adv.set_margin_end(8)
        nb.append_page(adv, Gtk.Label(label="Additional settings"))

        self.c_patch = Gtk.CheckButton(label="Own patchlist")
        self.c_patch.set_active(s.get("own_patchlist", False))
        self.e_patch = Gtk.Entry(hexpand=True); self.e_patch.set_text(s.get("patchlist", ""))
        adv.append(self.c_patch); adv.append(self.e_patch)

        self.c_pre = Gtk.CheckButton(label="Uses preloader/C-D")
        self.c_pre.set_active(s.get("uses_preloader", False))
        adv.append(self.c_pre)
        hb = Gtk.Box(spacing=6)
        self.e_pre = Gtk.Entry(hexpand=True); self.e_pre.set_text(s.get("preloader", ""))
        b_pre = Gtk.Button(label="Search...")
        b_pre.connect("clicked", lambda *a: self.pick(self.e_pre))
        hb.append(self.e_pre); hb.append(b_pre)
        adv.append(hb)

        adv.append(dim_label("Launch before WoW (optional, e.g. controller mapper):"))
        hb_extra = Gtk.Box(spacing=6)
        self.e_extra = Gtk.Entry(hexpand=True, placeholder_text="e.g. /path/to/WoWPadX")
        self.e_extra.set_text(s.get("extra_path", ""))
        b_extra = Gtk.Button(label="Search...")
        b_extra.connect("clicked", lambda *a: self.pick(self.e_extra))
        hb_extra.append(self.e_extra); hb_extra.append(b_extra)
        adv.append(hb_extra)

        self.c_cache = Gtk.CheckButton(label="Automatic cache (WDB) clearing")
        self.c_cache.set_active(s.get("auto_clear", False))
        adv.append(self.c_cache)

        self.c_win = Gtk.CheckButton(label="Start in windowed mode (SET gxWindow on Play)")
        self.c_win.set_active(s.get("windowed", False))
        adv.append(self.c_win)

        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        adv.append(grid)
        grid.attach(dim_label("Server site:"), 0, 0, 1, 1)
        self.e_site = Gtk.Entry(hexpand=True); self.e_site.set_text(s.get("site", ""))
        b_visit = Gtk.Button(label="Visit")
        b_visit.connect("clicked", lambda *a: webbrowser.open(self.e_site.get_text()) if self.e_site.get_text() else None)
        hb3 = Gtk.Box(spacing=6)
        hb3.append(self.e_site); hb3.append(b_visit)
        grid.attach(hb3, 1, 0, 1, 1)
        grid.attach(dim_label("Additional info:"), 0, 1, 1, 1)
        self.e_info = Gtk.Entry(hexpand=True); self.e_info.set_text(s.get("info", ""))
        grid.attach(self.e_info, 1, 1, 1, 1)

    def pick(self, entry):
        d = Gtk.FileChooserDialog(title="Select file", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        d.connect("response", lambda dlg, r: (entry.set_text(dlg.get_file().get_path()) if r == Gtk.ResponseType.ACCEPT and dlg.get_file() else None, dlg.close()))
        d.present()

    def result(self):
        return {
            "name": self.e_name.get_text().strip(),
            "address": self.e_addr.get_text().strip(),
            "account": self.e_acct.get_text().strip(),
            "own_patchlist": self.c_patch.get_active(),
            "patchlist": self.e_patch.get_text().strip(),
            "uses_preloader": self.c_pre.get_active(),
            "preloader": self.e_pre.get_text().strip(),
            "windowed": self.c_win.get_active(),
            "auto_clear": self.c_cache.get_active(),
            "server_type": PROFILE_VERSIONS[self.e_type.get_selected()],
            "extra_path": self.e_extra.get_text().strip(),
            "site": self.e_site.get_text().strip(),
            "info": self.e_info.get_text().strip(),
        }


class AddonsDialog(Gtk.Dialog):
    """Browse Interface/AddOns of the selected server's client."""
    def __init__(self, parent, wow_exe):
        super().__init__(title="AddOns", transient_for=parent, modal=True)
        self.add_buttons("Close", Gtk.ResponseType.CLOSE)
        self.set_default_size(420, 380)
        self.wow_exe = wow_exe
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8); box.set_margin_bottom(8); box.set_margin_start(8); box.set_margin_end(8)
        self.get_content_area().append(box)

        self.title_lbl = Gtk.Label(halign=Gtk.Align.START)
        self.title_lbl.add_css_class("gold")
        try:
            self.title_lbl.set_text(f"AddOns — {Path(wow_exe).parent.name}")
        except Exception:
            self.title_lbl.set_text("AddOns")
        box.append(self.title_lbl)

        self.path_lbl = Gtk.Label(halign=Gtk.Align.START, hexpand=True)
        self.path_lbl.add_css_class("dim")
        self.path_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.append(self.path_lbl)

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        box.append(scroll)
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("listbox")
        scroll.set_child(self.listbox)

        brow = Gtk.Box(spacing=6)
        box.append(brow)
        self.b_toggle = Gtk.Button(label="Disable")
        self.b_toggle.connect("clicked", lambda *a: self.toggle())
        b_del = Gtk.Button(label="Delete")
        b_del.connect("clicked", lambda *a: self.delete())
        b_zip = Gtk.Button(label="Install from .zip...")
        b_zip.connect("clicked", lambda *a: self.install_zip())
        b_open = Gtk.Button(label="Open folder")
        b_open.connect("clicked", lambda *a: self.open_folder())
        for b in (self.b_toggle, b_del, b_zip, b_open):
            brow.append(b)
        self.listbox.connect("row-selected", lambda *a: self.update_toggle())
        self.addons = []
        self.refresh()

    def addons_dir(self):
        d = wow_dir_of(self.wow_exe)
        return (d / "Interface" / "AddOns") if d is not None else Path("Interface/AddOns")

    def refresh(self):
        self.addons = list_addons(self.wow_exe)
        self.path_lbl.set_text(str(self.addons_dir()))
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        if not self.addons:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label="(no addons found — install one from a .zip)")
            lbl.add_css_class("dim")
            row.set_child(lbl)
            row.set_selectable(False)
            self.listbox.append(row)
            return
        for a in self.addons:
            row = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=8)
            state = Gtk.Label(label="●")
            state.set_markup('<span foreground="#22dd44">●</span>' if a["enabled"] else '<span foreground="#666666">●</span>')
            title = Gtk.Label(label=a["title"], halign=Gtk.Align.START, hexpand=True)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            ver = Gtk.Label(label=a["version"])
            ver.add_css_class("dim")
            h.append(state); h.append(title); h.append(ver)
            row.set_child(h)
            self.listbox.append(row)
        self.update_toggle()

    def selected(self):
        r = self.listbox.get_selected_row()
        if r is None or not self.addons or r.get_index() >= len(self.addons):
            return None
        return self.addons[r.get_index()]

    def update_toggle(self):
        a = self.selected()
        self.b_toggle.set_label("Disable" if (a and a["enabled"]) else "Enable")
        self.b_toggle.set_sensitive(a is not None)

    def toggle(self):
        a = self.selected()
        if not a:
            return
        try:
            if a["enabled"]:
                a["dir"].rename(a["dir"].with_name(a["dir"].name + ".disabled"))
            else:
                base = a["dir"].name
                if base.lower().endswith(".disabled"):
                    base = base[: -len(".disabled")]
                a["dir"].rename(a["dir"].with_name(base))
        except Exception as e:
            self.error(str(e))
        self.refresh()

    def delete(self):
        a = self.selected()
        if not a:
            return
        c = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=f"Delete addon '{a['name']}'?")
        c.add_css_class("msg")
        def on_resp(d, r):
            if r == Gtk.ResponseType.OK:
                try:
                    shutil.rmtree(a["dir"], ignore_errors=False)
                except Exception as e:
                    self.error(str(e))
                self.refresh()
            d.close()
        c.connect("response", on_resp)
        c.present()

    def install_zip(self):
        d = Gtk.FileChooserDialog(title="Select addon .zip", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        f = Gtk.FileFilter()
        f.set_name("ZIP archives")
        f.add_pattern("*.zip")
        d.add_filter(f)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Install", Gtk.ResponseType.ACCEPT)
        def on_resp(dlg, r):
            if r == Gtk.ResponseType.ACCEPT and dlg.get_file():
                try:
                    self.extract_zip(Path(dlg.get_file().get_path()))
                except Exception as e:
                    self.error(str(e))
                self.refresh()
            dlg.close()
        d.connect("response", on_resp)
        d.present()

    def extract_zip(self, zippath):
        dest = self.addons_dir()
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zippath) as z:
            for m in z.infolist():
                target = (dest / m.filename).resolve()
                if str(target) != str(dest) and dest not in target.parents:
                    continue  # zip-slip protection
                if m.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)

    def open_folder(self):
        try:
            wplat.open_folder(self.addons_dir())
        except Exception as e:
            self.error(str(e))

    def error(self, text):
        show_message(self, Gtk.MessageType.ERROR, text)


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.wow.realmlist")
        self.servers, self.settings = load_data()
        self.statuses = {}
        self.selected = 0
        self.rows = []
        self.launched = []  # [{"pid": int, "proc": Popen|None, "server": str, "exe": str, "started": str}]
        self._drag_src = None

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self, title=f"Realmlist modifier {APP_VERSION}")
        win.set_default_size(300, 540)
        win.set_resizable(False)
        css = Gtk.CssProvider()
        css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6); box.set_margin_bottom(6); box.set_margin_start(8); box.set_margin_end(8)
        win.set_child(box)

        top = Gtk.Box(spacing=6)
        title = Gtk.Label(label=f"Ⓦ  Realmlist modifier {APP_VERSION}")
        title.add_css_class("gold"); title.set_hexpand(True); title.set_halign(Gtk.Align.START)
        top.append(title)
        b_set = Gtk.Button(label="⚙")
        b_set.set_tooltip_text("Settings")
        b_set.connect("clicked", lambda *a: self.open_settings(win))
        top.append(b_set)
        b_addons = Gtk.Button(label="AddOns")
        b_addons.set_tooltip_text("Manage addons of the selected server's client")
        b_addons.connect("clicked", lambda *a: self.open_addons(win))
        top.append(b_addons)
        box.append(top)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("listbox")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.connect("row-selected", lambda *a: self.update_details())
        self.listbox.connect("row-activated", lambda *a: self.play(win))
        box.append(self.listbox)

        sbar = Gtk.Box(spacing=8)
        box.append(sbar)
        self.status_lbl = Gtk.Label(label="Status: ..."); self.status_lbl.add_css_class("val")
        self.ping_lbl = Gtk.Label(label="Ping: ..."); self.ping_lbl.add_css_class("val")
        sbar.append(self.status_lbl); sbar.append(self.ping_lbl)
        refresh = Gtk.Button(label="⟳")
        refresh.connect("clicked", lambda *a: self.check_all())
        info_btn = Gtk.Button(label="ⓘ")
        info_btn.connect("clicked", lambda *a: self.show_info(win))
        sbar.append(refresh); sbar.append(info_btn)

        brow = Gtk.Box(spacing=4)
        box.append(brow)
        play = Gtk.Button(label="▶  Play")
        play.add_css_class("bigplay"); play.set_hexpand(True)
        play.connect("clicked", lambda *a: self.play(win))
        brow.append(play)
        for label, tip, fn in [("+", "Add server", self.add_server), ("–", "Remove server", self.del_server),
                               ("✓", "Edit server", self.edit_server)]:
            b = Gtk.Button(label=label)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fn: f(win))
            brow.append(b)

        self.detail_labels = {}
        det = Gtk.Grid(column_spacing=8)
        box.append(det)
        for i, (key, label) in enumerate([("addr", "Server address:"), ("realmlist", "Realmlist:"),
                                          ("patchlist", "Patchlist:"), ("altpath", "WoW client:"),
                                          ("launcher", "Launcher:"), ("windowed", "Windowed:"),
                                          ("preloader", "Uses preloader/C-D:"), ("cache", "Automatic cache clearing"),
                                          ("extra", "Extra program:"), ("account", "Account:")]):
            k = Gtk.Label(label=label, halign=Gtk.Align.START); k.add_css_class("dim")
            v = Gtk.Label(label="--", halign=Gtk.Align.END, hexpand=True)
            v.add_css_class("val"); v.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            det.attach(k, 0, i, 1, 1); det.attach(v, 1, i, 1, 1)
            self.detail_labels[key] = v

        self.rebuild_list()
        self.check_all()

        # Launched instances (tracked PIDs, detached so WoW survives manager exit)
        inst_title = Gtk.Label(label="Launched by manager:", halign=Gtk.Align.START)
        inst_title.add_css_class("dim")
        box.append(inst_title)
        self.inst_list = Gtk.ListBox()
        self.inst_list.add_css_class("listbox")
        self.inst_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        box.append(self.inst_list)
        ibrow = Gtk.Box(spacing=6)
        box.append(ibrow)
        b_kill = Gtk.Button(label="Kill")
        b_kill.set_tooltip_text("Terminate selected instance (SIGTERM)")
        b_kill.connect("clicked", lambda *a: self.kill_selected(False))
        b_force = Gtk.Button(label="Force kill")
        b_force.set_tooltip_text("Force terminate selected instance (SIGKILL)")
        b_force.connect("clicked", lambda *a: self.kill_selected(True))
        ibrow.append(b_kill)
        ibrow.append(b_force)
        self.refresh_instances()
        GLib.timeout_add_seconds(2, self.refresh_instances)

        win.present()
        try:
            _ic = APP_DIR / "icon.png"
            if _ic.is_file():
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                _pix = GdkPixbuf.Pixbuf.new_from_file(str(_ic))
                _tex = Gdk.Texture.new_for_pixbuf(_pix)
                _surf = win.get_surface()
                if _surf is not None and hasattr(_surf, "set_icon_list"):
                    _surf.set_icon_list([_tex])
        except Exception:
            pass
        self.win = win

    # ---- list ----
    def rebuild_list(self):
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        self.rows = []
        for i, s in enumerate(self.servers):
            row = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=6)
            name = Gtk.Label(label=s["name"], halign=Gtk.Align.START, hexpand=True)
            name.add_css_class("gold")
            dot = Gtk.Label(label="●")
            on, _ = self.statuses.get(s["name"], (None, None))
            color = "#666666" if on is None else ("#22dd44" if on else "#dd2222")
            dot.set_markup(f'<span foreground="{color}">●</span>')
            h.append(name); h.append(dot)
            row.set_child(h)
            exe = effective_wow_exe(s, self.settings)
            ver = server_version(s)
            row.set_tooltip_text(f"Profile: {ver}\n{exe}\nDrag to reorder")
            # drag source: remember which row is being dragged
            ds = Gtk.DragSource.new()
            ds.set_actions(Gdk.DragAction.MOVE)
            ds.set_content(Gdk.ContentProvider.new_for_value(str(i)))
            ds.connect("drag-begin", self._on_drag_begin, i)
            row.add_controller(ds)
            # drop target: dropping onto row i moves the server there
            dt = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
            dt.connect("drop", self._on_drop, i)
            row.add_controller(dt)
            self.listbox.append(row)
            self.rows.append(row)
        if self.servers:
            self.listbox.select_row(self.rows[min(self.selected, len(self.rows) - 1)])
        self.update_details()

    def _on_drag_begin(self, source, drag, idx):
        self._drag_src = idx

    def _on_drop(self, target, value, x, y, dst_idx):
        src = getattr(self, "_drag_src", None)
        if src is None or src == dst_idx:
            return False
        self._move_server(src, dst_idx)
        return True

    def _move_server(self, src, dst):
        item = self.servers.pop(src)
        self.servers.insert(dst, item)
        self.selected = dst
        self._drag_src = None
        save_data(self.servers, self.settings)
        self.rebuild_list()

    def current(self):
        r = self.listbox.get_selected_row()
        if r is None or not self.servers:
            return None
        for i, row in enumerate(self.rows):
            if row == r:
                self.selected = i
                return self.servers[i]
        return None

    def update_details(self):
        s = self.current() or (self.servers[0] if self.servers else None)
        if not s:
            return
        addr = s.get("address", s.get("realmlist", ""))
        self.detail_labels["addr"].set_text("")
        self.detail_labels["realmlist"].set_text(addr)
        self.detail_labels["patchlist"].set_text(s.get("patchlist", "") if s.get("own_patchlist") else "---")
        client_lbl = describe_client(s, self.settings)
        self.detail_labels["altpath"].set_text(client_lbl)
        self.detail_labels["altpath"].set_tooltip_text(effective_wow_exe(s, self.settings) or "")
        self.detail_labels["launcher"].set_text(describe_launch(s, self.settings))
        self.detail_labels["launcher"].set_tooltip_text(describe_launch(s, self.settings))
        self.detail_labels["windowed"].set_text("Yes" if effective_windowed(s, self.settings) else "No")
        self.detail_labels["preloader"].set_text("Yes" if s.get("uses_preloader") else "No")
        self.detail_labels["cache"].set_text("Yes" if s.get("auto_clear") else "No")
        xp = (s.get("extra_path") or "").strip()
        self.detail_labels["extra"].set_text(Path(xp).name if xp else "---")
        self.detail_labels["extra"].set_tooltip_text(xp)
        self.detail_labels["account"].set_text(s.get("account", "") or "---")
        on, ms = self.statuses.get(s["name"], (None, None))
        if on is None:
            self.status_lbl.set_text("Status: ..."); self.ping_lbl.set_text("Ping: ...")
        elif on:
            self.status_lbl.set_text("Status: Online"); self.ping_lbl.set_text(f"Ping: {ms}ms")
        else:
            self.status_lbl.set_text("Status: Offline"); self.ping_lbl.set_text("Ping: --")

    def check_all(self):
        timeout = self.settings.get("timeout_ms", 3000)
        def worker():
            res = {s["name"]: ping_realm(s.get("address", s.get("realmlist", "")), timeout_ms=timeout) for s in self.servers}
            GLib.idle_add(self.apply_status, res)
        threading.Thread(target=worker, daemon=True).start()

    def apply_status(self, res):
        self.statuses = res
        for row, s in zip(self.rows, self.servers):
            h = row.get_child()
            dot = h.get_last_child()
            on, _ = res.get(s["name"], (None, None))
            color = "#666666" if on is None else ("#22dd44" if on else "#dd2222")
            dot.set_markup(f'<span foreground="{color}">●</span>')
        self.update_details()

    # ---- actions ----
    def add_server(self, win):
        dlg = ServerDialog(win, None)
        dlg.connect("response", lambda d, r: self._on_server_dlg(d, r, None))
        dlg.present()

    def edit_server(self, win):
        s = self.current()
        if not s:
            return
        dlg = ServerDialog(win, s)
        dlg.connect("response", lambda d, r: self._on_server_dlg(d, r, s))
        dlg.present()

    def _on_server_dlg(self, dlg, resp, old):
        if resp == Gtk.ResponseType.OK:
            data = dlg.result()
            if not data["name"] or not data["address"]:
                show_message(self.win, Gtk.MessageType.ERROR, "Server name and address are required.")
                return
            if old is None:
                self.servers.append(data)
            else:
                old.update(data)
            save_data(self.servers, self.settings)
            self.rebuild_list()
            self.check_all()
        dlg.close()

    def del_server(self, win):
        s = self.current()
        if s:
            self.servers.remove(s)
            save_data(self.servers, self.settings)
            self.selected = 0
            self.rebuild_list()

    def open_settings(self, win):
        dlg = SettingsDialog(win, self.settings)
        dlg.connect("response", lambda d, r: self._on_settings(d, r))
        dlg.present()

    def open_addons(self, win):
        s = self.current()
        wow_exe = effective_wow_exe(s, self.settings) if s else ""
        if not wow_exe or not Path(wow_exe).is_file():
            wow_exe = autodetect_wow_exe() or ""
        if not wow_exe:
            show_message(win, Gtk.MessageType.ERROR,
                         "No WoW.exe found for this profile.\n\nOpen Settings and set the path.")
            return
        dlg = AddonsDialog(win, wow_exe)
        dlg.connect("response", lambda d, r: d.close())
        dlg.present()

    def _on_settings(self, dlg, resp):
        if resp == Gtk.ResponseType.OK:
            self.settings.update(dlg.result())
            save_data(self.servers, self.settings)
            self.rebuild_list()
        dlg.close()

    def show_info(self, win):
        s = self.current()
        if not s:
            return
        exe = effective_wow_exe(s, self.settings)
        files = locate_realmlists(exe) if exe else []
        rl = "\n".join(str(p) for p in files[:4]) or "(profile not set)"
        text = (f"{s['name']}\nAddress: {s.get('address')}\nType: {s.get('server_type')}\n"
                f"Client: {describe_client(s, self.settings)}\nExe: {exe}\nLaunch: {describe_launch(s, self.settings)}\n"
                f"Windowed: {'Yes' if effective_windowed(s, self.settings) else 'No'}\n"
                f"Extra program: {(s.get('extra_path') or '').strip() or '---'}\nRealmlist files:\n{rl}\n"
                f"Site: {s.get('site')}\nInfo: {s.get('info')}")
        show_message(win, Gtk.MessageType.INFO, text)

    def ask_profile_path(self, win, server):
        """Profile for this server's version is missing: let the user point at WoW.exe now.

        Async (GTK dialogs): saves the profile when the user picks a file.
        Always aborts the current Play - the user presses Play again afterwards.
        """
        ver = server_version(server)
        msg = (f"No WoW.exe configured for {PROFILE_LABELS.get(ver, ver)}.\n\n"
               f"Server '{server.get('name')}' needs a {ver} client.\n"
               "Choose its WoW.exe now?")
        q = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=msg)
        q.add_css_class("msg")

        def on_answer(dlg, resp):
            dlg.close()
            if resp != Gtk.ResponseType.OK:
                return
            fc = Gtk.FileChooserDialog(title=f"Select WoW.exe for {ver}", transient_for=win,
                                       action=Gtk.FileChooserAction.OPEN)
            fc.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)

            def on_file(fdlg, r):
                if r == Gtk.ResponseType.ACCEPT and fdlg.get_file():
                    p = fdlg.get_file().get_path()
                    if p and Path(p).is_file():
                        self.settings.setdefault("profiles", {})[ver] = p
                        save_data(self.servers, self.settings)
                        self.rebuild_list()
                fdlg.close()
            fc.connect("response", on_file)
            fc.present()
        q.connect("response", on_answer)
        q.present()

    def _launch_extra(self, tag, xpath, skip_if_running=False,
                      launcher="", wineprefix="", args=""):
        """Start a helper program alongside WoW (detached, tracked).

        On Linux/macOS a helper ending in .exe runs through the same
        launcher/WINEPREFIX as WoW (e.g. wine); anything else (native
        binaries, scripts, AppImages) starts directly.
        """
        if not (xpath or "").strip():
            return
        if skip_if_running and proc_running(Path(xpath).name):
            return
        try:
            if Path(xpath).suffix.lower() == ".exe" and not wplat.IS_WINDOWS:
                argv = wplat.build_argv(xpath, launcher, args)
                env = None
                if wineprefix and wplat.wineprefix_supported():
                    env = dict(os.environ)
                    env["WINEPREFIX"] = wineprefix
                ep = wplat.launch_detached(argv, Path(xpath).parent, env=env)
            else:
                ep = wplat.launch_detached([xpath], Path(xpath).parent)
            self.launched.append({"pid": ep.pid, "proc": ep,
                                  "server": tag, "exe": xpath,
                                  "started": datetime.now().strftime("%H:%M:%S")})
        except Exception:
            pass

    def play(self, win):
        s = self.current()
        if not s:
            return
        wow_exe = effective_wow_exe(s, self.settings)
        if not wow_exe or not Path(wow_exe).is_file():
            nearby = autodetect_wow_exe()
            if nearby:
                wow_exe = nearby
                self.settings.setdefault("profiles", {})[server_version(s)] = nearby
                save_data(self.servers, self.settings)
                self.rebuild_list()
        if not wow_exe or not Path(wow_exe).is_file():
            self.ask_profile_path(win, s)
            return
        patch = s.get("patchlist", "") if s.get("own_patchlist") else ""
        ok, info = write_realmlist(wow_exe, s.get("address", ""), patch, s.get("account", ""),
                                   effective_windowed(s, self.settings))
        if not ok:
            show_message(win, Gtk.MessageType.ERROR, f"Could not write realmlist:\n{info}")
            return
        if s.get("auto_clear"):
            clear_wdb(wow_exe)
        st = self.settings
        launcher, wineprefix, extra_args = effective_launch(s, st)
        # 1. this server's own pre-launch program (e.g. controller mapper)
        server_extra = (s.get("extra_path") or "").strip()
        if server_extra:
            self._launch_extra(f"{s['name']} (extra)", server_extra,
                               skip_if_running=st.get("extra_skip_if_running", False),
                               launcher=launcher, wineprefix=wineprefix, args=extra_args)
        # 2. the global extra program (Settings), if any
        if st.get("run_extra") and st.get("extra_path"):
            self._launch_extra(f"{s['name']} (extra)", st["extra_path"],
                               skip_if_running=st.get("extra_skip_if_running", False),
                               launcher=launcher, wineprefix=wineprefix, args=extra_args)
        target = s.get("preloader") if s.get("uses_preloader") and s.get("preloader") else wow_exe
        argv = build_launch_cmd(target, launcher, extra_args)
        env = None
        if wineprefix and wplat.wineprefix_supported():
            env = dict(os.environ)
            env["WINEPREFIX"] = wineprefix
        try:
            # detached: survives manager exit (no wait)
            p = wplat.launch_detached(argv, Path(target).parent if Path(target).is_file() else Path(wow_exe).parent, env=env)
            self.launched.append({"pid": p.pid, "proc": p, "server": s["name"], "exe": target,
                                  "cmd": " ".join(argv),
                                  "started": datetime.now().strftime("%H:%M:%S")})
            self.refresh_instances()
        except Exception as e:
            show_message(win, Gtk.MessageType.ERROR, str(e))

    # ---- launched instances (PID tracking, detached) ----
    def entry_alive(self, e):
        proc = e.get("proc")
        if proc is not None:
            return proc.poll() is None  # poll() also reaps zombies
        return pid_alive(e["pid"])

    def refresh_instances(self):
        if not hasattr(self, "inst_list") or self.inst_list is None:
            return True
        # drop dead PIDs from tracking (silently)
        self.launched = [e for e in self.launched if self.entry_alive(e)]
        while (row := self.inst_list.get_row_at_index(0)) is not None:
            self.inst_list.remove(row)
        if not self.launched:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label="(none — press Play to launch)", halign=Gtk.Align.START)
            lbl.add_css_class("dim")
            row.set_child(lbl)
            row.set_selectable(False)
            self.inst_list.append(row)
            return True
        for e in self.launched:
            row = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=6)
            name = Gtk.Label(label=f"{e['server']}  ·  {Path(e['exe']).name}  ·  PID {e['pid']}  ·  {e['started']}",
                             halign=Gtk.Align.START, hexpand=True)
            name.add_css_class("val")
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            h.append(name)
            row.set_child(h)
            if e.get("cmd"):
                row.set_tooltip_text(e["cmd"])
            self.inst_list.append(row)
        return True  # keep GLib timeout alive

    def kill_selected(self, force):
        row = self.inst_list.get_selected_row()
        if row is None or not self.launched:
            return
        idx = row.get_index()
        # placeholder "(none)" row is not selectable, but guard anyway
        if idx < 0 or idx >= len(self.launched):
            return
        entry = self.launched[idx]
        ok, err = kill_pid(entry["pid"], force=force)
        if not ok and "already gone" not in err:
            show_message(self.win, Gtk.MessageType.ERROR, f"Could not kill PID {entry['pid']}:\n{err}")
        # give SIGTERM a moment, then refresh (prunes dead PIDs)
        GLib.timeout_add(500, self.refresh_instances)


if __name__ == "__main__":
    App().run(None)
