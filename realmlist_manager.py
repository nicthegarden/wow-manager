"""WoW Realmlist Manager for Linux (Tkinter) - mimics 'Realmlist modifier' screenshot."""
import json
import os
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "servers.json"

DEFAULT_WOW_EXE = Path("WoW.exe")

DEFAULT_SERVERS = [
    {"name": "CHROMIECRAFT", "realmlist": "logon.chromiecraft.com", "patchlist": "", "wow_path": ""},
    {"name": "Warmane", "realmlist": "logon.warmane.com", "patchlist": "", "wow_path": ""},
    {"name": "Example", "realmlist": "logon.example.com", "patchlist": "", "wow_path": ""},
]

BG = "#0d0d0d"
PANEL = "#141414"
GOLD = "#d8a920"
GOLD_DIM = "#8a6d15"
FG = "#e8d98a"
GREEN = "#22dd44"
RED = "#dd2222"
GREY = "#666666"


def load_servers():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, dict):
                return data.get("servers", DEFAULT_SERVERS), data.get("wow_exe", str(DEFAULT_WOW_EXE))
            return data, str(DEFAULT_WOW_EXE)
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_SERVERS], str(DEFAULT_WOW_EXE)


def save_servers(servers, wow_exe):
    CONFIG_FILE.write_text(json.dumps({"servers": servers, "wow_exe": wow_exe}, indent=2))


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
        self.servers, self.wow_exe = load_servers()
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
                           ("altpath", "Alternative WoW path:"), ("preload", "Uses preloader/C-D:"),
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
        alt = s.get("wow_path") or "No"
        self.detail_vars["altpath"].set(alt if len(alt) < 28 else "Yes (custom)")
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
        self.servers.append({"name": name, "realmlist": rl, "patchlist": "", "wow_path": ""})
        save_servers(self.servers, self.wow_exe)
        self.refresh_list()
        self.check_all_status()

    def del_server(self):
        s = self.current()
        if s and messagebox.askyesno("Delete", f"Remove {s['name']}?", parent=self):
            self.servers.remove(s)
            save_servers(self.servers, self.wow_exe)
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
        save_servers(self.servers, self.wow_exe)
        self.refresh_list()
        self.check_all_status()

    def set_wow_path(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(title="Select WoW.exe",
            filetypes=[("WoW.exe", "WoW.exe"), ("All", "*")])
        if p:
            self.wow_exe = p
            save_servers(self.servers, self.wow_exe)
            messagebox.showinfo("WoW path", f"Set to:\n{p}", parent=self)

    def play(self):
        s = self.current()
        if not s:
            return
        wow_exe = s.get("wow_path") or self.wow_exe
        if not Path(wow_exe).exists():
            messagebox.showerror("Missing WoW", f"WoW.exe not found:\n{wow_exe}\n\nUse 'WoW path' button to fix it.", parent=self)
            return
        ok, info = write_realmlist(wow_exe, s["realmlist"])
        if not ok:
            messagebox.showerror("Error", f"Could not write realmlist:\n{info}", parent=self)
            return
        if self.cache_var.get():
            clear_cache(wow_exe)
        try:
            subprocess.Popen(["wine", wow_exe], cwd=str(Path(wow_exe).parent),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            messagebox.showerror("Launch failed", str(e), parent=self)


if __name__ == "__main__":
    App().mainloop()
