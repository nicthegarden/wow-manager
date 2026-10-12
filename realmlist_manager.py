"""WoW Realmlist Manager (Tkinter) - mimics 'Realmlist modifier' screenshot.
Runs on Linux, Windows and macOS (see wow_platform.py for OS differences).
"""
import json
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path

import wow_platform as wplat

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "servers.json"

# Three fixed client profiles: each version maps to exactly one WoW.exe path.
PROFILE_VERSIONS = ["1.12.1", "2.4.3", "3.3.5a"]

DEFAULT_PROFILES = {v: "" for v in PROFILE_VERSIONS}

DEFAULT_SERVERS = [
    {"name": "OctoWoW", "realmlist": "play.octowow.st", "patchlist": "", "server_type": "1.12.1"},
    {"name": "FrozenThrone", "realmlist": "frozenthrone.co", "patchlist": "", "server_type": "3.3.5a"},
    {"name": "Warmane", "realmlist": "logon.warmane.com", "patchlist": "", "server_type": "3.3.5a"},
]

BG = "#0d0d0d"
PANEL = "#141414"
GOLD = "#d8a920"
GOLD_DIM = "#8a6d15"
FG = "#e8d98a"
GREEN = "#22dd44"
RED = "#dd2222"
GREY = "#666666"


def server_version(server):
    v = ((server or {}).get("server_type") or "").strip()
    return v if v in PROFILE_VERSIONS else "3.3.5a"


def profile_exe(version, profiles):
    return ((profiles or {}).get(version) or "")


def migrate_profiles(data):
    """Build the 3-slot profiles dict from old and new config shapes."""
    profiles = dict(DEFAULT_PROFILES)
    settings = (data.get("settings") or {}) if isinstance(data, dict) else {}
    for v in PROFILE_VERSIONS:
        if isinstance(settings.get("profiles"), dict) and settings["profiles"].get(v):
            profiles[v] = settings["profiles"][v]
    if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
        for v in PROFILE_VERSIONS:
            if data["profiles"].get(v):
                profiles[v] = data["profiles"][v]
    # legacy: single global exe / clients list / per-server wow_path
    for key in ("wow_exe",):
        if isinstance(data, dict) and data.get(key):
            for v in PROFILE_VERSIONS:
                if not profiles[v] and Path(data[key]).is_file():
                    profiles[v] = data[key]
                    break
    if isinstance(settings.get("clients"), list):
        for c in settings["clients"]:
            if isinstance(c, dict) and c.get("version") in PROFILE_VERSIONS and c.get("path"):
                if not profiles[c["version"]] and Path(c["path"]).is_file():
                    profiles[c["version"]] = c["path"]
    if isinstance(data, dict) and isinstance(data.get("servers"), list):
        for s in data["servers"]:
            if isinstance(s, dict) and s.get("wow_path") and Path(s["wow_path"]).is_file():
                v = s.get("server_type") if s.get("server_type") in PROFILE_VERSIONS else ""
                if v and not profiles[v]:
                    profiles[v] = s["wow_path"]
    return profiles


def load_servers():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, dict):
                servers = data.get("servers", DEFAULT_SERVERS)
                for s in servers:
                    s.setdefault("server_type", "3.3.5a")
                    if s.get("server_type") not in PROFILE_VERSIONS:
                        s["server_type"] = "3.3.5a"
                return servers, migrate_profiles(data)
            return data, dict(DEFAULT_PROFILES)
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_SERVERS], dict(DEFAULT_PROFILES)


def save_servers(servers, profiles):
    CONFIG_FILE.write_text(json.dumps({"servers": servers, "profiles": profiles}, indent=2))


def ping_realm(host, port=3724, timeout=3):
    """TCP connect to auth port; returns (online: bool, ms: int|None)."""
    host = host.strip()
    if not host:
        return False, None
    t0 = time.time()
    try:
        # strip possible http prefix
        host = host.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
        ip = socket.gethostbyname(host)
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True, int((time.time() - t0) * 1000)
    except Exception:
        # fallback: plain ping-less DNS check on port 80? just mark offline
        return False, None


def write_realmlist(wow_exe_path, realmlist):
    exe = Path(wow_exe_path)
    wow_dir = exe.parent if exe.suffix.lower() == ".exe" else Path(wow_exe_path)
    targets = [wow_dir / "realmlist.wtf"]
    # some clients also keep Data/enUS/realmlist.wtf / Data/enGB/...
    for lang in ("enUS", "enGB"):
        p = wow_dir / "Data" / lang / "realmlist.wtf"
        if p.parent.exists():
            targets.append(p)
    for t in targets:
        try:
            if t.exists():
                t.write_text(f"set realmlist {realmlist}\n")
        except Exception as e:
            return False, str(e)
    if not targets[0].exists():
        try:
            targets[0].write_text(f"set realmlist {realmlist}\n")
        except Exception as e:
            return False, str(e)
    # also patch WTF/Config.wtf SET realmList
    cfg = wow_dir / "WTF" / "Config.wtf"
    try:
        if cfg.exists():
            lines = cfg.read_text(errors="ignore").splitlines()
            out, found = [], False
            for ln in lines:
                if ln.strip().upper().startswith('SET REALMLIST') or ln.strip().upper().startswith('SET REALMNAME') or ln.strip().upper().startswith('SET REALM'):
                    if 'realmList' in ln or 'REALMLIST' in ln.upper():
                        out.append(f'SET realmList "{realmlist}"')
                        found = True
                        continue
                out.append(ln)
            if not found:
                out.append(f'SET realmList "{realmlist}"')
            cfg.write_text("\n".join(out) + "\n")
    except Exception as e:
        return False, str(e)
    return True, str(targets[0])


def clear_cache(wow_exe_path):
    exe = Path(wow_exe_path)
    wow_dir = exe.parent if exe.suffix.lower() == ".exe" else Path(wow_exe_path)
    wdb = wow_dir / "WDB"
    errors = 0
    if wdb.exists():
        for p in wdb.rglob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                errors += 1
    return errors == 0


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.servers, self.profiles = load_servers()
        self.statuses = {}  # name -> (online, ms)
        self.title("Realmlist modifier")
        self.configure(bg=BG)
        self.geometry("300x430")
        self.resizable(False, False)
        self.build_ui()
        self.refresh_list()
        self.check_all_status()

    # ---- UI ----
    def build_ui(self):
        # title bar imitation
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(top, text="Ⓦ  Realmlist modifier", bg=BG, fg=GOLD, font=("Arial", 10, "bold")).pack(side="left")

        # list frame with gold border
        border = tk.Frame(self, bg=GOLD, padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=8, pady=6)
        inner = tk.Frame(border, bg=PANEL)
        inner.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(inner, bg=PANEL, fg=GOLD, selectbackground="#3a2f10",
                                  selectforeground="#ffe9a0", font=("Arial", 11), relief="flat",
                                  highlightthickness=0, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.on_select())
        self.listbox.bind("<Double-Button-1>", lambda e: self.play())

        # status dots drawn as a second listbox-like column of labels
        self.dotframe = tk.Frame(inner, bg=PANEL)
        self.dotframe.pack(side="right", padx=(0, 8))
        self.dot_labels = []

        # status bar
        sbar = tk.Frame(self, bg=BG)
        sbar.pack(fill="x", padx=8)
        self.status_var = tk.StringVar(value="Status:")
        self.ping_var = tk.StringVar(value="Ping: --")
        tk.Label(sbar, textvariable=self.status_var, bg=BG, fg=FG, font=("Arial", 9)).pack(side="left")
        tk.Label(sbar, textvariable=self.ping_var, bg=BG, fg=FG, font=("Arial", 9)).pack(side="left", padx=(8, 0))
        tk.Button(sbar, text="⟳", bg=BG, fg=GOLD, relief="flat", command=self.check_all_status).pack(side="right")

        # buttons row: Play etc.
        brow = tk.Frame(self, bg=BG)
        brow.pack(fill="x", padx=8, pady=4)
        tk.Label(brow, text="▼  ▲  ◉", bg=BG, fg=GOLD_DIM, font=("Arial", 9)).pack(side="left")
        tk.Button(brow, text="Play", bg=BG, fg=GOLD, font=("Georgia", 14, "bold"),
                  relief="flat", command=self.play).pack(side="left", expand=True)
        tk.Button(brow, text="+", bg=BG, fg=GOLD, relief="flat", command=self.add_server).pack(side="left")
        tk.Button(brow, text="–", bg=BG, fg=GOLD, relief="flat", command=self.del_server).pack(side="left")
        tk.Button(brow, text="✓", bg=BG, fg=GOLD, relief="flat", command=self.edit_server).pack(side="left")

        # details
        det = tk.Frame(self, bg=BG)
        det.pack(fill="x", padx=8, pady=2)
        tk.Label(det, text="Server address:", bg=BG, fg=GOLD, font=("Arial", 8, "bold"), anchor="w").pack(fill="x")
        self.detail_vars = {}
        for key, label in [("realmlist", "Realmlist:"), ("patchlist", "Patchlist:"),
                           ("altpath", "WoW profile:"), ("preload", "Uses preloader/C-D:"),
                           ("cache", "Automatic cache clearing")]:
            r = tk.Frame(det, bg=BG)
            r.pack(fill="x")
            tk.Label(r, text=label, bg=BG, fg=GOLD_DIM, font=("Arial", 8), width=22, anchor="w").pack(side="left")
            v = tk.StringVar(value="--")
            tk.Label(r, textvariable=v, bg=BG, fg=FG, font=("Arial", 8), anchor="e").pack(side="right", expand=True, fill="x")
            self.detail_vars[key] = v

        # bottom row: wow path + cache toggle
        brow2 = tk.Frame(self, bg=BG)
        brow2.pack(fill="x", padx=8, pady=(2, 6))
        tk.Button(brow2, text="Profiles", bg="#1a1a1a", fg=GOLD, font=("Arial", 8),
                  command=self.edit_profiles).pack(side="left")
        tk.Button(brow2, text="WoW path", bg="#1a1a1a", fg=GOLD, font=("Arial", 8),
                  command=self.set_wow_path).pack(side="left")
        self.cache_var = tk.BooleanVar(value=False)
        tk.Checkbutton(brow2, text="clear cache", bg=BG, fg=GOLD_DIM, selectcolor=BG,
                       variable=self.cache_var, font=("Arial", 8)).pack(side="left", padx=6)
        tk.Label(brow2, text="1  2  RSS", bg=BG, fg=GOLD_DIM, font=("Arial", 8)).pack(side="right")

    # ---- logic ----
    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        for w in self.dot_labels:
            w.destroy()
        self.dot_labels = []
        for s in self.servers:
            self.listbox.insert(tk.END, s["name"])
            on, ms = self.statuses.get(s["name"], (None, None))
            color = GREY if on is None else (GREEN if on else RED)
            dot = tk.Label(self.dotframe, text="●", bg=PANEL, fg=color, font=("Arial", 10))
            dot.pack()
            self.dot_labels.append(dot)
        if self.servers and not self.listbox.curselection():
            self.listbox.selection_set(0)
        self.on_select()

    def on_select(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        s = self.servers[sel[0]]
        self.detail_vars["realmlist"].set(s.get("realmlist", ""))
        self.detail_vars["patchlist"].set(s.get("patchlist", "") or "---")
        ver = server_version(s)
        exe = profile_exe(ver, self.profiles)
        self.detail_vars["altpath"].set(f"{ver}" + ("" if exe and Path(exe).is_file() else " (not set)"))
        self.detail_vars["preload"].set("No")
        self.detail_vars["cache"].set("Yes" if self.cache_var.get() else "No")
        on, ms = self.statuses.get(s["name"], (None, None))
        if on is None:
            self.status_var.set("Status: ...")
            self.ping_var.set("Ping: ...")
        elif on:
            self.status_var.set("Status: Online")
            self.ping_var.set(f"Ping: {ms}ms")
        else:
            self.status_var.set("Status: Offline")
            self.ping_var.set("Ping: --")

    def check_all_status(self):
        def worker():
            res = {}
            for s in self.servers:
                res[s["name"]] = ping_realm(s.get("realmlist", ""))
            self.statuses = res
            self.after(0, self.refresh_list)
        threading.Thread(target=worker, daemon=True).start()

    def current(self):
        sel = self.listbox.curselection()
        return self.servers[sel[0]] if sel else None

    def add_server(self):
        name = simpledialog.askstring("Add", "Server name:", parent=self)
        if not name:
            return
        rl = simpledialog.askstring("Add", "Realmlist address:", parent=self)
        if not rl:
            return
        self.servers.append({"name": name, "realmlist": rl, "patchlist": "", "server_type": "3.3.5a"})
        save_servers(self.servers, self.profiles)
        self.refresh_list()
        self.check_all_status()

    def del_server(self):
        s = self.current()
        if s and messagebox.askyesno("Delete", f"Remove {s['name']}?", parent=self):
            self.servers.remove(s)
            save_servers(self.servers, self.profiles)
            self.refresh_list()

    def edit_server(self):
        s = self.current()
        if not s:
            return
        rl = simpledialog.askstring("Edit", f"Realmlist for {s['name']}:",
                                    initialvalue=s.get("realmlist", ""), parent=self)
        if rl is None:
            return
        s["realmlist"] = rl.strip()
        save_servers(self.servers, self.profiles)
        self.refresh_list()
        self.check_all_status()

    def set_wow_path(self):
        from tkinter import filedialog
        ver = server_version(self.current())
        p = filedialog.askopenfilename(title=f"Select WoW.exe for {ver}",
            filetypes=[("WoW.exe", "WoW.exe"), ("All", "*")])
        if p:
            self.profiles[ver] = p
            save_servers(self.servers, self.profiles)
            self.on_select()
            messagebox.showinfo("WoW path", f"{ver} set to:\n{p}", parent=self)

    def edit_profiles(self):
        from tkinter import filedialog
        top = tk.Toplevel(self)
        top.title("WoW profiles (one WoW.exe per version)")
        top.configure(bg=BG)
        entries = {}
        for i, ver in enumerate(PROFILE_VERSIONS):
            tk.Label(top, text=ver, bg=BG, fg=GOLD, anchor="w").grid(row=i, column=0, sticky="w", padx=6, pady=3)
            e = tk.Entry(top, width=40)
            e.insert(0, self.profiles.get(ver, ""))
            e.grid(row=i, column=1, padx=6, pady=3)
            entries[ver] = e
            def browse(v=ver):
                q = filedialog.askopenfilename(title=f"Select WoW.exe for {v}",
                    filetypes=[("WoW.exe", "WoW.exe"), ("All", "*")])
                if q:
                    entries[v].delete(0, tk.END)
                    entries[v].insert(0, q)
            tk.Button(top, text="...", command=browse).grid(row=i, column=2, padx=6)
        def save():
            for v, e in entries.items():
                self.profiles[v] = e.get().strip()
            save_servers(self.servers, self.profiles)
            self.on_select()
            top.destroy()
        tk.Button(top, text="Save", command=save).grid(row=len(PROFILE_VERSIONS), column=0, columnspan=3, pady=8)

    def play(self):
        s = self.current()
        if not s:
            return
        ver = server_version(s)
        wow_exe = profile_exe(ver, self.profiles)
        if not wow_exe or not Path(wow_exe).is_file():
            from tkinter import filedialog
            if messagebox.askyesno("Missing WoW", f"No WoW.exe set for {ver}.\nChoose it now?", parent=self):
                q = filedialog.askopenfilename(title=f"Select WoW.exe for {ver}",
                    filetypes=[("WoW.exe", "WoW.exe"), ("All", "*")])
                if q:
                    self.profiles[ver] = q
                    save_servers(self.servers, self.profiles)
                    wow_exe = q
        if not wow_exe or not Path(wow_exe).is_file():
            messagebox.showerror("Missing WoW", f"No WoW.exe set for {ver}.\nUse 'Profiles' to set it.", parent=self)
            return
        ok, info = write_realmlist(wow_exe, s["realmlist"])
        if not ok:
            messagebox.showerror("Error", f"Could not write realmlist:\n{info}", parent=self)
            return
        if self.cache_var.get():
            clear_cache(wow_exe)
        try:
            argv = wplat.build_argv(wow_exe, wplat.default_launcher(), "")
            wplat.launch_detached(argv, Path(wow_exe).parent)
        except Exception as e:
            messagebox.showerror("Launch failed", str(e), parent=self)


if __name__ == "__main__":
    App().mainloop()
