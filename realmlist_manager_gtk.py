"""WoW Realmlist Manager for Linux (GTK4, no extra deps).
Replicates 'Realmlist modifier': main list + Settings + Add server (Basic/Additional).
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Pango, Gdk

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "servers.json"

DEFAULT_SETTINGS = {
    "wow_exe": "",  # resolved at runtime: config value, WoW.exe next to script, or Settings dialog
    "run_extra": False,
    "extra_path": "",
    "extra_skip_if_running": False,
    "timeout_ms": 3000,
    "save_window_pos": True,
    "check_updates": "Once a day",
}

DEFAULT_SERVERS = [
    {"name": "CHROMIECRAFT", "address": "logon.chromiecraft.com",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "alt_wow": False, "wow_path": "", "auto_clear": False,
     "server_type": "3.3.5a", "rss": "", "site": "https://chromiecraft.com", "info": ""},
    {"name": "Warmane", "address": "logon.warmane.com",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "alt_wow": False, "wow_path": "", "auto_clear": False,
     "server_type": "3.3.5a", "rss": "", "site": "https://warmane.com", "info": ""},
    {"name": "Example", "address": "logon.example.com",
     "own_patchlist": False, "patchlist": "", "uses_preloader": False, "preloader": "",
     "alt_wow": False, "wow_path": "", "auto_clear": False,
     "server_type": "3.3.5a", "rss": "", "site": "", "info": ""},
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
            if not Path(settings.get("wow_exe", "")).exists():
                nearby = autodetect_wow_exe()
                if nearby:
                    settings["wow_exe"] = nearby
                    save_data(servers, settings)
            return servers, settings
        except Exception:
            pass
    # first run: generate servers.json right next to this script,
    # preferring a WoW.exe found in the same folder
    servers = [dict(s) for s in DEFAULT_SERVERS]
    settings = dict(DEFAULT_SETTINGS)
    nearby = autodetect_wow_exe()
    if nearby:
        settings["wow_exe"] = nearby
    elif not Path(settings["wow_exe"]).exists():
        pass  # keep default; user picks it via Settings
    try:
        save_data(servers, settings)
    except Exception:
        pass
    return servers, settings


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


def effective_wow_exe(server, settings):
    if server.get("alt_wow") and server.get("wow_path"):
        return server["wow_path"]
    return settings["wow_exe"]


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
    # keep the root file first if present (most clients read it first)
    found.sort(key=lambda s: (Path(s) != root, s))
    return [Path(s) for s in found]


def write_realmlist(wow_exe_path, address, patchlist=""):
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
            out, f1, f2 = [], False, False
            for ln in clines:
                u = ln.strip().upper()
                if u.startswith("SET REALMLIST"):
                    out.append(f'set realmlist {address}'); f1 = True
                elif u.startswith("SET PATCHLIST"):
                    if patchlist:
                        out.append(f'set patchlist {patchlist}'); f2 = True
                    # else drop patchlist line
                else:
                    out.append(ln)
            if not f1:
                out.append(f'set realmlist {address}')
            if patchlist and not f2:
                out.append(f'set patchlist {patchlist}')
            cfg.write_text("\n".join(out) + "\n")
    except Exception as e:
        return False, str(e)
    targets = [str(t) for t in written] + ([str(cfg)] if cfg.exists() else [])
    return True, "\n".join(targets)


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
    try:
        r = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


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
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def proc_name(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return "?"


def kill_pid(pid, force=False):
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True, ""
    except ProcessLookupError:
        return False, "process already gone"
    except PermissionError:
        return False, "permission denied"
    except Exception as e:
        return False, str(e)


class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, settings):
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.settings = settings
        self.add_buttons("Save", Gtk.ResponseType.OK, "Cancel", Gtk.ResponseType.CANCEL)
        g = Gtk.Grid(column_spacing=6, row_spacing=6)
        g.set_margin_top(10); g.set_margin_bottom(10); g.set_margin_start(10); g.set_margin_end(10)
        self.get_content_area().append(g)

        g.attach(Gtk.Label(label="Path to WoW.exe:", halign=Gtk.Align.START), 0, 0, 1, 1)
        self.wow_entry = Gtk.Entry(hexpand=True)
        self.wow_entry.set_text(settings["wow_exe"])
        g.attach(self.wow_entry, 0, 1, 1, 1)
        hb = Gtk.Box(spacing=6)
        b_search = Gtk.Button(label="Search...")
        b_search.connect("clicked", lambda *a: self.pick_file(self.wow_entry))
        b_ver = Gtk.Button(label="Obtain version")
        b_ver.connect("clicked", lambda *a: self.show_version(self.wow_entry.get_text()))
        b_loc = Gtk.Button(label="Locate realmlist")
        b_loc.connect("clicked", lambda *a: self.show_located(self.wow_entry.get_text()))
        hb.append(b_search); hb.append(b_ver); hb.append(b_loc)
        g.attach(hb, 1, 1, 1, 1)

        self.run_extra = Gtk.CheckButton(label="Run another program together with WoW")
        self.run_extra.set_active(settings["run_extra"])
        g.attach(self.run_extra, 0, 2, 2, 1)
        self.skip_run = Gtk.CheckButton(label="Do not run, if it's already running")
        self.skip_run.set_active(settings["extra_skip_if_running"])
        g.attach(self.skip_run, 0, 3, 2, 1)
        self.extra_entry = Gtk.Entry(hexpand=True)
        self.extra_entry.set_text(settings["extra_path"])
        g.attach(self.extra_entry, 0, 4, 1, 1)
        b_search2 = Gtk.Button(label="Search...")
        b_search2.connect("clicked", lambda *a: self.pick_file(self.extra_entry))
        g.attach(b_search2, 1, 4, 1, 1)

        g.attach(Gtk.Label(label="Connection timeout (milliseconds):", halign=Gtk.Align.START), 0, 5, 1, 1)
        self.timeout = Gtk.SpinButton.new_with_range(500, 15000, 500)
        self.timeout.set_value(settings["timeout_ms"])
        g.attach(self.timeout, 1, 5, 1, 1)

        self.save_pos = Gtk.CheckButton(label="Save window position")
        self.save_pos.set_active(settings["save_window_pos"])
        g.attach(self.save_pos, 0, 6, 2, 1)

        g.attach(Gtk.Label(label="Check program updates automatically:", halign=Gtk.Align.START), 0, 7, 1, 1)
        self.upd = Gtk.DropDown.new_from_strings(["Never", "Once a day", "Once a week"])
        try:
            self.upd.set_selected(["Never", "Once a day", "Once a week"].index(settings["check_updates"]))
        except ValueError:
            self.upd.set_selected(1)
        g.attach(self.upd, 1, 7, 1, 1)

        note = Gtk.Label(label="Note: if a server has an alternative WoW path set, the path above is ignored.")
        note.set_wrap(True)
        g.attach(note, 0, 8, 2, 1)

    def pick_file(self, entry):
        d = Gtk.FileChooserDialog(title="Select file", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        d.connect("response", lambda dlg, r: (entry.set_text(dlg.get_file().get_path()) if r == Gtk.ResponseType.ACCEPT and dlg.get_file() else None, dlg.close()))
        d.present()

    def show_version(self, path):
        p = Path(path)
        msg = f"File exists: {p}\nSize: {p.stat().st_size} bytes" if p.exists() else f"Not found:\n{path}"
        m = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=msg)
        m.connect("response", lambda *a: m.close())
        m.present()

    def show_located(self, path):
        files = locate_realmlists(path)
        if not files:
            msg = "No WoW path set — pick WoW.exe first."
        else:
            existing = [str(p) for p in files if p.exists()]
            msg = "realmlist.wtf file(s) that Play will update:\n" + (
                "\n".join(existing) if existing else f"(none found yet — will create {files[0]})")
        m2 = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=msg)
        m2.connect("response", lambda *a: m2.close())
        m2.present()

    def result(self):
        return {
            "wow_exe": self.wow_entry.get_text().strip(),
            "run_extra": self.run_extra.get_active(),
            "extra_path": self.extra_entry.get_text().strip(),
            "extra_skip_if_running": self.skip_run.get_active(),
            "timeout_ms": int(self.timeout.get_value()),
            "save_window_pos": self.save_pos.get_active(),
            "check_updates": ["Never", "Once a day", "Once a week"][self.upd.get_selected()],
        }


class ServerDialog(Gtk.Dialog):
    """Add/Edit server with Basic + Additional tabs (like screenshots 2 and 3)."""
    def __init__(self, parent, server=None):
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
        basic.append(Gtk.Label(label="Server name:"))
        self.e_name = Gtk.Entry(); self.e_name.set_text(s.get("name", ""))
        basic.append(self.e_name)
        basic.append(Gtk.Label(label="Server address:"))
        self.e_addr = Gtk.Entry(); self.e_addr.set_text(s.get("address", s.get("realmlist", "")))
        basic.append(self.e_addr)

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

        self.c_alt = Gtk.CheckButton(label="Alternative WoW path")
        self.c_alt.set_active(s.get("alt_wow", False))
        adv.append(self.c_alt)
        hb2 = Gtk.Box(spacing=6)
        self.e_alt = Gtk.Entry(hexpand=True); self.e_alt.set_text(s.get("wow_path", ""))
        b_ver = Gtk.Button(label="Obtain version")
        b_ver.connect("clicked", lambda *a: self.version(self.e_alt.get_text()))
        b_alt = Gtk.Button(label="Search...")
        b_alt.connect("clicked", lambda *a: self.pick(self.e_alt))
        hb2.append(self.e_alt); hb2.append(b_ver); hb2.append(b_alt)
        adv.append(hb2)

        self.c_cache = Gtk.CheckButton(label="Automatic cache (WDB) clearing")
        self.c_cache.set_active(s.get("auto_clear", False))
        adv.append(self.c_cache)

        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        adv.append(grid)
        grid.attach(Gtk.Label(label="Server type:", halign=Gtk.Align.START), 0, 0, 1, 1)
        self.e_type = Gtk.DropDown.new_from_strings(["...", "1.12.1", "2.4.3", "3.3.5a"])
        cur = s.get("server_type", "...")
        opts = ["...", "1.12.1", "2.4.3", "3.3.5a"]
        self.e_type.set_selected(opts.index(cur) if cur in opts else 0)
        grid.attach(self.e_type, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Server RSS channel:", halign=Gtk.Align.START), 0, 1, 1, 1)
        self.e_rss = Gtk.Entry(hexpand=True); self.e_rss.set_text(s.get("rss", ""))
        grid.attach(self.e_rss, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Server site:", halign=Gtk.Align.START), 0, 2, 1, 1)
        self.e_site = Gtk.Entry(hexpand=True); self.e_site.set_text(s.get("site", ""))
        b_visit = Gtk.Button(label="Visit")
        b_visit.connect("clicked", lambda *a: webbrowser.open(self.e_site.get_text()) if self.e_site.get_text() else None)
        hb3 = Gtk.Box(spacing=6)
        hb3.append(self.e_site); hb3.append(b_visit)
        grid.attach(hb3, 1, 2, 1, 1)
        grid.attach(Gtk.Label(label="Additional info:", halign=Gtk.Align.START), 0, 3, 1, 1)
        self.e_info = Gtk.Entry(hexpand=True); self.e_info.set_text(s.get("info", ""))
        grid.attach(self.e_info, 1, 3, 1, 1)

    def pick(self, entry):
        d = Gtk.FileChooserDialog(title="Select file", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        d.connect("response", lambda dlg, r: (entry.set_text(dlg.get_file().get_path()) if r == Gtk.ResponseType.ACCEPT and dlg.get_file() else None, dlg.close()))
        d.present()

    def version(self, path):
        p = Path(path)
        msg = f"File exists: {p}\nSize: {p.stat().st_size} bytes" if p.exists() else f"Not found:\n{path}"
        m = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=msg)
        m.connect("response", lambda *a: m.close())
        m.present()

    def result(self):
        opts = ["...", "1.12.1", "2.4.3", "3.3.5a"]
        return {
            "name": self.e_name.get_text().strip(),
            "address": self.e_addr.get_text().strip(),
            "own_patchlist": self.c_patch.get_active(),
            "patchlist": self.e_patch.get_text().strip(),
            "uses_preloader": self.c_pre.get_active(),
            "preloader": self.e_pre.get_text().strip(),
            "alt_wow": self.c_alt.get_active(),
            "wow_path": self.e_alt.get_text().strip(),
            "auto_clear": self.c_cache.get_active(),
            "server_type": opts[self.e_type.get_selected()],
            "rss": self.e_rss.get_text().strip(),
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
            subprocess.Popen(["xdg-open", str(self.addons_dir())],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.error(str(e))

    def error(self, text):
        m = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=text)
        m.connect("response", lambda *a: m.close())
        m.present()


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
        win = Gtk.ApplicationWindow(application=self, title="Realmlist modifier")
        win.set_default_size(300, 470)
        win.set_resizable(False)
        css = Gtk.CssProvider()
        css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6); box.set_margin_bottom(6); box.set_margin_start(8); box.set_margin_end(8)
        win.set_child(box)

        top = Gtk.Box(spacing=6)
        title = Gtk.Label(label="Ⓦ  Realmlist modifier")
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
                                          ("patchlist", "Patchlist:"), ("altpath", "Alternative WoW path:"),
                                          ("preloader", "Uses preloader/C-D:"), ("cache", "Automatic cache clearing")]):
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
            row.set_tooltip_text("Drag to reorder")
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
        self.detail_labels["altpath"].set_text("Yes" if s.get("alt_wow") else "No")
        self.detail_labels["preloader"].set_text("Yes" if s.get("uses_preloader") else "No")
        self.detail_labels["cache"].set_text("Yes" if s.get("auto_clear") else "No")
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
        dlg = ServerDialog(win)
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
                m = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text="Server name and address are required.")
                m.connect("response", lambda *a: m.close())
                m.present()
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
        wow_exe = effective_wow_exe(s, self.settings) if s else self.settings.get("wow_exe", "")
        if not wow_exe or not Path(wow_exe).is_file():
            wow_exe = autodetect_wow_exe() or ""
        if not wow_exe:
            m = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK,
                                  text="No WoW.exe found.\n\nOpen Settings (⚙) to set the path.")
            m.connect("response", lambda *a: m.close())
            m.present()
            return
        dlg = AddonsDialog(win, wow_exe)
        dlg.connect("response", lambda d, r: d.close())
        dlg.present()

    def _on_settings(self, dlg, resp):
        if resp == Gtk.ResponseType.OK:
            self.settings.update(dlg.result())
            save_data(self.servers, self.settings)
        dlg.close()

    def show_info(self, win):
        s = self.current()
        if not s:
            return
        text = (f"{s['name']}\nAddress: {s.get('address')}\nType: {s.get('server_type')}\n"
                f"Site: {s.get('site')}\nInfo: {s.get('info')}")
        m = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=text)
        m.connect("response", lambda *a: m.close())
        m.present()

    def play(self, win):
        s = self.current()
        if not s:
            return
        wow_exe = effective_wow_exe(s, self.settings)
        if not wow_exe or not Path(wow_exe).is_file():
            # last resort: WoW.exe dropped next to the script
            nearby = autodetect_wow_exe()
            if nearby:
                wow_exe = nearby
                if not (s.get("alt_wow") and s.get("wow_path")):
                    self.settings["wow_exe"] = nearby
                    save_data(self.servers, self.settings)
        if not wow_exe or not Path(wow_exe).is_file():
            m = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK,
                                  text=f"WoW.exe not found:\n{wow_exe}\n\nOpen Settings (⚙) to fix the path.")
            m.connect("response", lambda *a: m.close())
            m.present()
            return
        patch = s.get("patchlist", "") if s.get("own_patchlist") else ""
        ok, info = write_realmlist(wow_exe, s.get("address", ""), patch)
        if not ok:
            m = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=f"Could not write realmlist:\n{info}")
            m.connect("response", lambda *a: m.close())
            m.present()
            return
        if s.get("auto_clear"):
            clear_wdb(wow_exe)
        st = self.settings
        if st.get("run_extra") and st.get("extra_path"):
            if not (st.get("extra_skip_if_running") and proc_running(Path(st["extra_path"]).name)):
                try:
                    # detached: survives manager exit
                    ep = subprocess.Popen([st["extra_path"]], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
                    self.launched.append({"pid": ep.pid, "proc": ep,
                                          "server": f"{s['name']} (extra)", "exe": st["extra_path"],
                                          "started": datetime.now().strftime("%H:%M:%S")})
                except Exception:
                    pass
        target = s.get("preloader") if s.get("uses_preloader") and s.get("preloader") else wow_exe
        try:
            # detached: survives manager exit (new session, no wait)
            p = subprocess.Popen(["wine", target], cwd=str(Path(target).parent if Path(target).is_file() else Path(wow_exe).parent),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.launched.append({"pid": p.pid, "proc": p, "server": s["name"], "exe": target,
                                  "started": datetime.now().strftime("%H:%M:%S")})
            self.refresh_instances()
        except Exception as e:
            m = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=str(e))
            m.connect("response", lambda *a: m.close())
            m.present()

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
            m = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.ERROR,
                                  buttons=Gtk.ButtonsType.OK, text=f"Could not kill PID {entry['pid']}:\n{err}")
            m.connect("response", lambda *a: m.close())
            m.present()
        # give SIGTERM a moment, then refresh (prunes dead PIDs)
        GLib.timeout_add(500, self.refresh_instances)


if __name__ == "__main__":
    App().run(None)
