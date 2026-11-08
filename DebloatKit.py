"""
DebloatKit v2.0
Samsung Galaxy Debloater for Windows
Author: Yashwanth Ram Somireddy | TeamExyKings | Chennai, India
License: MIT — Free & Open Source
GitHub: https://github.com/yashwanthramsomireddy/DebloatKit

DISCLAIMER: DebloatKit is an independent open-source tool not affiliated with,
endorsed by, or connected to Samsung Electronics Co., Ltd. Samsung and Galaxy
are trademarks of Samsung Electronics. This tool uses Android's standard ADB
debugging interface.

UI architecture (v2.0 performance build)
----------------------------------------
* Package lists are drawn on ONE canvas (PackageList) and only the rows that are
  visible are drawn. 5 or 5,000 packages cost the same, so switching tabs is
  instant and never builds/destroys widgets.
* Log / Settings / About are built once, stacked on top of each other, and
  switched with tkraise() — no destroy/rebuild, no relayout, no flicker.
* Worker threads never touch Tk. They post work to a queue that the main
  thread drains every 40 ms (thread-safe, no stray flicker/minimise).
* Theme switch builds the new UI off-screen and swaps it in a single step.
"""

import customtkinter as ctk
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, filedialog
import threading
import queue
import collections
import os
import webbrowser
from bisect import bisect_right
from datetime import datetime
from PIL import Image

from core.adb_manager import ADBManager, DeviceInfo
from core.app_scanner import AppScanner, AppEntry
from core.debloater import Debloater
from ui.themes import get_theme, get_risk_color, get_state_color, get_risk_label, THEMES

APP_NAME    = "DebloatKit"
APP_VERSION = "v2.0"
APP_AUTHOR  = "Yashwanth Ram Somireddy"
APP_BRAND   = "TeamExyKings"
APP_GITHUB  = "https://github.com/yashwanthramsomireddy/DebloatKit"
APP_LICENSE = "MIT — Free & Open Source"

# Default portrait window (logical pixels). Fitted to the screen at start-up.
WINDOW_W = 640
WINDOW_H = 940
MIN_W    = 560
MIN_H    = 640

APP_TABS = ("system", "core", "user", "thirdparty")
TAB_DEFS = [("system", "System"), ("core", "Core"), ("user", "User"),
            ("thirdparty", "3rd Party"), ("logs", "Log"),
            ("settings", "Settings"), ("about", "About")]
FONT = "Segoe UI"
MONO = "Consolas"


def _res_dir() -> str:
    """Folder that holds assets/ — works from source and from a PyInstaller exe."""
    import sys
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _load_logo(size: int):
    """Load the DebloatKit logo. Returns CTkImage or None."""
    base  = _res_dir()
    paths = [
        os.path.join(base, "assets", "debloatkit_logo.png"),
        os.path.join(base, "assets", f"logo_{size}.png"),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                img = Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
                return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
            except Exception:
                pass
    return None


def _set_icon(win):
    ico = os.path.join(_res_dir(), "assets", "icon.ico")
    if os.path.exists(ico):
        try:
            win.iconbitmap(ico)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  PackageList — virtualised, single-canvas package list
# ═════════════════════════════════════════════════════════════════════════════
class PackageList(tk.Frame):
    """
    Draws only the visible rows onto one canvas.
    items: list of ("h", title, entries) | ("r", entry, None)
    """

    ROW_H = 46
    HDR_H = 32

    def __init__(self, master, app, T):
        super().__init__(master, bg=T["bg"], bd=0, highlightthickness=0)
        self.app = app
        self.T = T
        self.items: list = []
        self.message: str = ""
        self.top = 0
        self._ys = [0]
        self.total = 0
        self._pending = None
        self._hover = -1
        self._row_ids: dict[int, int] = {}
        self._scale = 0.0
        self._cut_cache: dict = {}

        self.canvas = tk.Canvas(self, bg=T["bg"], bd=0, highlightthickness=0, takefocus=0)
        self.vsb = ctk.CTkScrollbar(self, command=self._on_vsb, fg_color=T["bg"],
                                    button_color=T["scrollbar"],
                                    button_hover_color=T["accent"])
        self.vsb.pack(side="right", fill="y", padx=(2, 0))
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self.refresh())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(-1))
        self._sync_scale(force=True)

    # ── scale / fonts ────────────────────────────────────────────────────────
    def _sync_scale(self, force=False):
        s = self.app._scale()
        if not force and abs(s - self._scale) < 0.01:
            return
        self._scale = s
        self.row_h = round(self.ROW_H * s)
        self.hdr_h = round(self.HDR_H * s)
        px = lambda v: -max(1, round(v * s))
        self.f_name = tkfont.Font(root=self, family=FONT, size=px(13), weight="bold")
        self.f_pkg  = tkfont.Font(root=self, family=MONO, size=px(11))
        self.f_tag  = tkfont.Font(root=self, family=FONT, size=px(11), weight="bold")
        self.f_hdr  = tkfont.Font(root=self, family=FONT, size=px(12), weight="bold")
        self.f_msg  = tkfont.Font(root=self, family=FONT, size=px(15))
        self._cut_cache.clear()
        self._relayout()

    def _p(self, v):
        return round(v * self._scale)

    # ── data ─────────────────────────────────────────────────────────────────
    def set_items(self, items, message="", top=0):
        self.items = items
        self.message = message
        self.top = top
        self._relayout()
        self._hover = -1
        self.refresh()

    def _relayout(self):
        ys = [0]
        rh, hh = self.row_h, self.hdr_h
        for it in self.items:
            ys.append(ys[-1] + (hh if it[0] == "h" else rh))
        self._ys = ys
        self.total = ys[-1]

    def get_top(self) -> int:
        return self.top

    # ── drawing (always coalesced to once per event-loop turn) ───────────────
    def refresh(self):
        if self._pending is None:
            self._pending = self.after_idle(self._draw)

    def _cut(self, text, font, maxw):
        key = (text, id(font), maxw)
        v = self._cut_cache.get(key)
        if v is not None:
            return v
        if maxw <= 10:
            v = ""
        elif font.measure(text) <= maxw:
            v = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid] + "…") <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            v = text[:lo] + "…"
        if len(self._cut_cache) > 4000:
            self._cut_cache.clear()
        self._cut_cache[key] = v
        return v

    def _row_bg(self, entry, hover=False):
        T = self.T
        if hover:
            return T["bg_hover"]
        return T["bg_card"] if entry.state == "enabled" else T["disabled_bg"]

    def _draw(self):
        self._pending = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        c, T = self.canvas, self.T
        W, H = c.winfo_width(), c.winfo_height()
        if W <= 2 or H <= 2:
            return
        self._sync_scale()
        p = self._p
        c.delete("all")
        self._row_ids = {}

        if self.message or not self.items:
            c.create_text(W // 2, p(80), text=self.message, fill=T["text_muted"],
                          font=self.f_msg, width=W - p(48), justify="center")
            self.vsb.set(0, 1)
            return

        max_top = max(0, self.total - H)
        self.top = max(0, min(self.top, max_top))
        top, ys, items = self.top, self._ys, self.items
        n = len(items)
        i = max(0, bisect_right(ys, top) - 1)
        while i < n:
            y0 = ys[i] - top
            if y0 >= H:
                break
            kind, a, b = items[i]
            if kind == "h":
                self._draw_header(c, T, W, y0, a, b)
            else:
                self._draw_row(c, T, W, y0, i, a)
            i += 1
        if self.total > 0:
            self.vsb.set(top / self.total, min(1.0, (top + H) / self.total))

    def _draw_header(self, c, T, W, y0, title, entries):
        p = self._p
        h = self.hdr_h
        c.create_rectangle(0, y0 + p(3), W, y0 + h - p(1), fill=T["accent"], outline="")
        cy = y0 + (h + p(2)) // 2
        bw = p(26)
        x_none = W - p(8) - bw
        x_all = x_none - p(4) - bw
        c.create_text(p(12), cy, anchor="w", font=self.f_hdr, fill=T["btn_primary_text"],
                      text=self._cut(f"⚙  {title}  ·  {len(entries)}", self.f_hdr,
                                     x_all - p(24)))
        for x, ch in ((x_all, "✓"), (x_none, "✕")):
            c.create_rectangle(x, cy - p(10), x + bw, cy + p(10),
                               fill=T["accent_dim"], outline="")
            c.create_text(x + bw // 2, cy, text=ch, font=self.f_tag,
                          fill=T["btn_primary_text"])

    def _draw_row(self, c, T, W, y0, idx, e):
        p = self._p
        h = self.row_h
        is_keep, is_core = e.risk == "KEEP", e.risk == "CORE"
        rid = c.create_rectangle(0, y0 + 1, W, y0 + h - 1,
                                 fill=self._row_bg(e, idx == self._hover), outline="")
        self._row_ids[idx] = rid
        cy = y0 + h // 2

        # checkbox
        s = p(16)
        x = p(12)
        if is_keep:
            c.create_rectangle(x, cy - s // 2, x + s, cy + s // 2,
                               outline=T["text_dim"], width=1)
            c.create_line(x + p(4), cy, x + s - p(4), cy, fill=T["text_dim"], width=1)
        elif e.checked:
            col = T["core_red"] if is_core else T["accent"]
            c.create_rectangle(x, cy - s // 2, x + s, cy + s // 2, fill=col, outline=col)
            c.create_line(x + p(3), cy, x + p(7), cy + p(4), x + s - p(3), cy - p(4),
                          fill=T["btn_primary_text"] if not is_core else "#ffffff",
                          width=max(2, p(2)))
        else:
            c.create_rectangle(x, cy - s // 2, x + s, cy + s // 2,
                               outline=T["core_red"] if is_core else T["text_muted"], width=1)

        # right column: risk badge (top) + state (bottom)
        risk_txt = get_risk_label(e.risk)
        state_txt = e.state.title()
        rx = W - p(12)
        rw = max(self.f_tag.measure(risk_txt), self.f_tag.measure(state_txt))
        c.create_text(rx, y0 + h * 0.32, anchor="e", text=risk_txt, font=self.f_tag,
                      fill=get_risk_color(e.risk, self.app.current_theme_name))
        c.create_text(rx, y0 + h * 0.70, anchor="e", text=state_txt, font=self.f_tag,
                      fill=get_state_color(e.state))

        # left: name (top) + package id (bottom)
        tx = p(42)
        maxw = W - tx - rw - p(22)
        name = e.name if e.in_db else f"{e.name} *"
        c.create_text(tx, y0 + h * 0.32, anchor="w", font=self.f_name,
                      fill=T["text_dim"] if is_keep else T["text"],
                      text=self._cut(name, self.f_name, maxw))
        c.create_text(tx, y0 + h * 0.70, anchor="w", font=self.f_pkg, fill=T["text_dim"],
                      text=self._cut(e.pkg, self.f_pkg, maxw))

    # ── scrolling ────────────────────────────────────────────────────────────
    def _scroll_to(self, top):
        top = max(0, min(int(top), max(0, self.total - self.canvas.winfo_height())))
        if top != self.top:
            self.top = top
            self.refresh()

    def _on_vsb(self, *args):
        if not args:
            return
        H = self.canvas.winfo_height()
        if args[0] == "moveto":
            self._scroll_to(float(args[1]) * self.total)
        elif args[0] == "scroll":
            n = int(args[1])
            step = self.row_h if args[2] == "units" else max(self.row_h, H - self.row_h)
            self._scroll_to(self.top + n * step)

    def _on_wheel(self, e):
        try:
            if self.canvas.winfo_containing(e.x_root, e.y_root) is not self.canvas:
                return
        except Exception:
            return
        if getattr(e, "num", None) == 4:
            d = -1
        elif getattr(e, "num", None) == 5:
            d = 1
        else:
            d = -1 if e.delta > 0 else 1
        self._scroll_to(self.top + d * self.row_h * 2)

    # ── mouse ────────────────────────────────────────────────────────────────
    def _index_at(self, y):
        if not self.items or self.message:
            return -1
        i = bisect_right(self._ys, y + self.top) - 1
        return i if 0 <= i < len(self.items) else -1

    def _on_click(self, e):
        i = self._index_at(e.y)
        if i < 0:
            return
        kind, a, b = self.items[i]
        if kind == "r":
            self.app._toggle_entry(a)
            return
        W = self.canvas.winfo_width()
        bw, p = self._p(26), self._p
        x_none = W - p(8) - bw
        x_all = x_none - p(4) - bw
        if x_all <= e.x <= x_all + bw:
            self.app._set_checked(b, True)
        elif x_none <= e.x <= x_none + bw:
            self.app._set_checked(b, False)

    def _on_motion(self, e):
        i = self._index_at(e.y)
        if i >= 0 and self.items[i][0] != "r":
            i = -1
        self._set_hover(i)

    def _set_hover(self, i):
        if i == self._hover:
            return
        old, self._hover = self._hover, i
        try:
            for idx, hv in ((old, False), (i, True)):
                rid = self._row_ids.get(idx)
                if rid is not None and idx >= 0:
                    self.canvas.itemconfigure(rid, fill=self._row_bg(self.items[idx][1], hv))
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  Main application
# ═════════════════════════════════════════════════════════════════════════════
class DebloatKit(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.current_theme_name = "Green"
        self.T = get_theme(self.current_theme_name)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title(f"{APP_NAME} {APP_VERSION} — {APP_BRAND}")
        self.configure(fg_color=self.T["bg"])
        self._init_window()
        _set_icon(self)

        # Thread → UI plumbing (workers never touch Tk directly)
        self._ui_q: "queue.SimpleQueue" = queue.SimpleQueue()
        self._log_q: collections.deque = collections.deque()
        self._log_lines: list = []            # [(level, text)] kept for rebuilds
        self._progress = None                 # latest (value, message) from a worker
        self._list_dirty = False
        self._closing = False

        # Core modules
        self.adb       = ADBManager(log_callback=self._log)
        self.scanner   = AppScanner(self.adb, log_callback=self._log)
        self.debloater = Debloater(self.adb, log_callback=self._log)

        # State
        self.device_info: DeviceInfo | None = None
        self.app_data: dict[str, list[AppEntry]] = {
            "system": [], "core": [], "user": [], "thirdparty": [], "keep": []
        }
        self._scanning       = False
        self._scanned        = False
        self._action_running = False
        self._conn_status    = "no_device"
        self._active_tab     = "system"
        self._usb_guide_shown = False
        self._views = {c: self._new_view() for c in APP_TABS}
        self._filter_job = None
        self._loading_filters = False

        self.adb_path_var    = tk.StringVar(value=self.adb.adb_path)
        self.backup_path_var = tk.StringVar(value=self.debloater.backup_dir)
        self.subcat_var      = tk.StringVar(value="All")

        self.log_visible = False
        # One global wheel binding (same technique CTkScrollableFrame uses) so we
        # never clobber other widgets' bindings; survives theme swaps.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self._route_wheel, add="+")

        self.ui = None
        self._swap_ui(first=True)
        self._pump()
        self._start_device_polling()

    # ─── Window ───────────────────────────────────────────────────────────────

    def _scale(self) -> float:
        try:
            return float(self._get_window_scaling())
        except Exception:
            return 1.0

    def _init_window(self):
        """Proper portrait window, fitted to the screen and centred (NOT maximised)."""
        s = self._scale()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = max(MIN_W, min(WINDOW_W, int(sw / s) - 40))
        h = max(MIN_H, min(WINDOW_H, int(sh / s) - 110))
        self.minsize(MIN_W, MIN_H)
        self.geometry(f"{w}x{h}")
        x = max(0, (sw - int(w * s)) // 2)
        y = max(0, (sh - int(h * s)) // 2 - 20)
        tk.Wm.wm_geometry(self, f"+{x}+{y}")      # position only (size set above)

    def _place_dialog(self, win, w, h):
        s = self._scale()
        win.geometry(f"{w}x{h}")
        try:
            x = self.winfo_rootx() + (self.winfo_width() - int(w * s)) // 2
            y = self.winfo_rooty() + (self.winfo_height() - int(h * s)) // 3
            tk.Wm.wm_geometry(win, f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _make_modal(self, win):
        try:
            win.transient(self)
        except Exception:
            pass

        def grab():
            try:
                if win.winfo_exists():
                    win.grab_set()
                    win.focus_set()
            except Exception:
                pass
        win.after(80, grab)
        win.after(250, lambda: _set_icon(win) if win.winfo_exists() else None)

    def _route_wheel(self, e):
        pl = getattr(self, "pkg_list", None)
        if pl is not None and self._active_tab in APP_TABS:
            pl._on_wheel(e)

    # ─── Thread-safe UI queue ─────────────────────────────────────────────────

    def ui_call(self, fn):
        """Safe to call from ANY thread."""
        self._ui_q.put(fn)

    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    pass
            prog, self._progress = self._progress, None
            if prog is not None:
                try:
                    self.status_prog.set(prog[0])
                    if prog[1]:
                        self.status_text.configure(text=prog[1])
                except Exception:
                    pass
            if self._list_dirty:
                self._list_dirty = False
                self.pkg_list.refresh()
            self._flush_log()
        finally:
            try:
                self.after(40, self._pump)
            except Exception:
                pass

    def _set_progress(self, val, msg=""):
        self._progress = (val, msg)

    # ─── UI build / theme swap ────────────────────────────────────────────────

    def _swap_ui(self, first=False):
        """Build a complete UI off-screen, then show it in one step."""
        old = self.ui
        new = ctk.CTkFrame(self, fg_color=self.T["bg"], corner_radius=0)   # not mapped yet
        self.ui = new
        self._pages: dict = {}
        self._built: set = set()
        self._about_gen = None
        self.log_view = None
        self._build_ui(new)
        self.configure(fg_color=self.T["bg"])
        if old is not None:
            old.pack_forget()
        new.pack(fill="both", expand=True)
        if old is not None:
            self.after(50, old.destroy)
        self._restore_ui_state()
        self.after(350, self._prebuild_pages)

    def _switch_theme(self, name: str):
        if name == self.current_theme_name:
            return
        self.current_theme_name = name
        self.T = get_theme(name)
        self._swap_ui()

    def _restore_ui_state(self):
        """Re-apply live state to a freshly built UI."""
        self._apply_device_ui()
        key = self._active_tab
        self._active_tab = None
        self._switch_tab(key, force=True)
        for lvl, line in self._log_lines[-400:]:
            self._insert_log(self.log_text, lvl, line)

    def _build_ui(self, host):
        T = self.T

        # ── Topbar ───────────────────────────────────────────────────────────
        topbar = ctk.CTkFrame(host, fg_color=T["topbar_bg"], height=52, corner_radius=0)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        left = ctk.CTkFrame(topbar, fg_color="transparent")
        left.pack(side="left", padx=12, pady=6)
        logo_img = _load_logo(32)
        if logo_img:
            ctk.CTkLabel(left, image=logo_img, text="").pack(side="left", padx=(0, 7))
        else:
            ctk.CTkLabel(left, text="⬡", font=(FONT, 22), text_color=T["accent"]).pack(side="left", padx=(0, 5))
        ctk.CTkLabel(left, text=APP_NAME, font=(FONT, 16, "bold"), text_color=T["accent"]).pack(side="left")
        ctk.CTkLabel(left, text=f" {APP_VERSION}", font=(FONT, 10), text_color=T["text_muted"]).pack(side="left", pady=(4, 0))

        right = ctk.CTkFrame(topbar, fg_color="transparent")
        right.pack(side="right", padx=12, pady=6)
        self.status_label = ctk.CTkLabel(right, text="No Device", font=(FONT, 11), text_color=T["text_muted"])
        self.status_label.pack(side="right", padx=(4, 0))
        self.status_dot = ctk.CTkLabel(right, text="●", font=(FONT, 13), text_color="#333333")
        self.status_dot.pack(side="right", padx=(10, 0))

        # ── Device strip ─────────────────────────────────────────────────────
        dev_strip = ctk.CTkFrame(host, fg_color=T["bg_card"], height=40, corner_radius=0)
        dev_strip.pack(fill="x")
        dev_strip.pack_propagate(False)
        self.scan_btn = ctk.CTkButton(dev_strip, text="⟳  Scan", font=(FONT, 11, "bold"),
                                      fg_color=T["accent"], text_color=T["btn_primary_text"],
                                      hover_color=T["accent_dim"], width=96, height=28,
                                      command=self._start_scan, state="disabled")
        self.scan_btn.pack(side="right", padx=8, pady=6)
        self.bat_lbl = ctk.CTkLabel(dev_strip, text="", font=(FONT, 10), text_color=T["text_muted"])
        self.bat_lbl.pack(side="right", padx=4)
        self.dev_model_lbl = ctk.CTkLabel(dev_strip, text="No device connected",
                                          font=(FONT, 11, "bold"), text_color=T["text_muted"], anchor="w")
        self.dev_model_lbl.pack(side="left", padx=(12, 6))
        self.dev_info_lbl = ctk.CTkLabel(dev_strip, text="Connect via USB · Enable USB Debugging",
                                         font=(FONT, 10), text_color=T["text_dim"], anchor="w")
        self.dev_info_lbl.pack(side="left", padx=4)

        # ── Tab bar ───────────────────────────────────────────────────────────
        tab_bar = ctk.CTkFrame(host, fg_color=T["bg"], height=36, corner_radius=0)
        tab_bar.pack(fill="x", padx=10, pady=(4, 0))
        tab_bar.pack_propagate(False)
        self._tab_btns = {}
        for key, label in TAB_DEFS:
            btn = ctk.CTkButton(
                tab_bar, text=label, font=(FONT, 11),
                fg_color="transparent", text_color=T["text_muted"],
                hover_color=T["bg_hover"], corner_radius=5,
                height=28, width=0,
                command=lambda k=key: self._switch_tab(k)
            )
            btn.pack(side="left", padx=(0, 2), expand=True, fill="x")
            self._tab_btns[key] = btn

        # ── Status bar + bottom action bar (packed BEFORE content so they never clip)
        self.statusbar = ctk.CTkFrame(host, fg_color=T["bg"], height=20, corner_radius=0)
        self.statusbar.pack(fill="x", side="bottom")
        self.statusbar.pack_propagate(False)
        self.status_text = ctk.CTkLabel(self.statusbar, text="Connect your device via USB and enable USB Debugging.",
                                        font=(FONT, 9), text_color=T["text_muted"], anchor="w")
        self.status_text.pack(side="left", padx=10)
        self.status_prog = ctk.CTkProgressBar(self.statusbar, fg_color=T["progress_bg"],
                                              progress_color=T["accent"], height=4, width=120)
        self.status_prog.set(0)
        self.status_prog.pack(side="right", padx=10, pady=7)

        bottombar = ctk.CTkFrame(host, fg_color=T["bg_card"], height=58, corner_radius=0,
                                 border_width=1, border_color=T["border"])
        bottombar.pack(fill="x", side="bottom")
        bottombar.pack_propagate(False)

        self.log_btn = ctk.CTkButton(
            bottombar, text="📋 Log", font=(FONT, 11),
            fg_color=T["bg"], text_color=T["text"],
            hover_color=T["bg_hover"], width=64, height=40,
            command=self._toggle_log_panel)
        self.log_btn.pack(side="right", padx=(0, 10), pady=9)

        self.disable_btn = ctk.CTkButton(
            bottombar, text="⏸  Disable Selected", font=(FONT, 11, "bold"),
            fg_color=T["bg_hover"], text_color=T["warning"], hover_color=T["bg_hover"],
            border_width=1, border_color=T["warning"], corner_radius=6, height=40,
            command=lambda: self._run_action(self._active_tab, "disable"))
        self.disable_btn.pack(side="left", padx=(10, 4), pady=9, fill="x", expand=True)

        self.uninstall_btn = ctk.CTkButton(
            bottombar, text="🗑  Uninstall Selected", font=(FONT, 11, "bold"),
            fg_color=T["core_red"], text_color="#ffffff", hover_color="#cc0000",
            corner_radius=6, height=40,
            command=lambda: self._run_action(self._active_tab, "uninstall"))
        self.uninstall_btn.pack(side="left", padx=(0, 4), pady=9, fill="x", expand=True)

        self.reenable_btn = ctk.CTkButton(
            bottombar, text="↩  Re-enable Selected", font=(FONT, 11, "bold"),
            fg_color=T["accent"], text_color=T["btn_primary_text"], hover_color=T["accent_dim"],
            corner_radius=6, height=40,
            command=lambda: self._run_action(self._active_tab, "enable"))
        self.reenable_btn.pack(side="left", padx=(0, 4), pady=9, fill="x", expand=True)

        if self._action_running:
            for b in (self.disable_btn, self.uninstall_btn, self.reenable_btn):
                b.configure(state="disabled")

        # ── Content: every page lives here, stacked; tkraise() picks one ─────
        self.content = ctk.CTkFrame(host, fg_color=T["bg"], corner_radius=0)
        self.content.pack(fill="both", expand=True, padx=10, pady=(4, 0))

        self._build_pkg_page()
        for key in ("logs", "settings", "about"):
            pg = ctk.CTkFrame(self.content, fg_color=T["bg"], corner_radius=0)
            pg.place(x=0, y=0, relwidth=1, relheight=1)
            self._pages[key] = pg
        self._pages["pkg"].tkraise()

        # ── Floating log panel (overlay on the right of the content area) ────
        self.log_panel = ctk.CTkFrame(self.content, fg_color=T["log_bg"], width=300, corner_radius=0,
                                      border_width=1, border_color=T["border"])
        hdr = ctk.CTkFrame(self.log_panel, fg_color=T["bg_card"], height=30, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="LOG", font=(FONT, 9, "bold"), text_color=T["accent"]).pack(side="left", padx=8, pady=5)
        ctk.CTkButton(hdr, text="Clear", font=(FONT, 9), fg_color="transparent",
                      text_color=T["text_muted"], hover_color=T["bg_hover"], width=40, height=20,
                      command=self._clear_log).pack(side="right", padx=4)
        ctk.CTkButton(hdr, text="Export", font=(FONT, 9), fg_color="transparent",
                      text_color=T["text_muted"], hover_color=T["bg_hover"], width=46, height=20,
                      command=self._export_log).pack(side="right")
        self.log_text = self._make_log_box(self.log_panel, 10, corner=0)
        self.log_text.pack(fill="both", expand=True)
        if self.log_visible:
            self._show_log_panel()

    def _make_log_box(self, parent, size, corner=6):
        T = self.T
        box = ctk.CTkTextbox(parent, fg_color=T["log_bg"], text_color=T["text"],
                             font=(MONO, size), corner_radius=corner,
                             scrollbar_button_color=T["scrollbar"], wrap="word")
        for lvl, key in (("info", "log_info"), ("success", "log_success"),
                         ("warning", "log_warning"), ("error", "log_error")):
            try:
                box.tag_config(lvl, foreground=T[key])
            except Exception:
                pass
        box.configure(state="disabled")
        return box

    # ─── Package page ─────────────────────────────────────────────────────────

    def _build_pkg_page(self):
        T = self.T
        pg = ctk.CTkFrame(self.content, fg_color=T["bg"], corner_radius=0)
        pg.place(x=0, y=0, relwidth=1, relheight=1)
        self._pages["pkg"] = pg

        tb = ctk.CTkFrame(pg, fg_color=T["bg"], height=34, corner_radius=0)
        tb.pack(fill="x", pady=(0, 3))
        ctk.CTkButton(tb, text="✓  Select All", font=(FONT, 11, "bold"),
                      fg_color=T["accent"], text_color=T["btn_primary_text"],
                      hover_color=T["accent_dim"], width=96, height=28,
                      command=lambda: self._select_visible(True)).pack(side="left", padx=(0, 5))
        ctk.CTkButton(tb, text="✕  Deselect", font=(FONT, 11),
                      fg_color=T["bg_card"], text_color=T["text"],
                      hover_color=T["bg_hover"], width=90, height=28,
                      command=lambda: self._select_visible(False)).pack(side="left", padx=(0, 5))
        self.subcat_menu = ctk.CTkOptionMenu(
            tb, variable=self.subcat_var, values=["All"], width=130, height=28,
            fg_color=T["bg_card"], button_color=T["accent"],
            dropdown_fg_color=T["bg_card"], text_color=T["text"],
            command=lambda v: self._on_subcat_changed())
        self.subcat_menu.pack(side="left", padx=(0, 5))
        self.search_entry = ctk.CTkEntry(tb, placeholder_text="🔍  Search apps or package…",
                                         height=28, fg_color=T["bg_card"],
                                         border_color=T["border"], text_color=T["text"])
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", lambda e: self._on_filter_typed())

        info = ctk.CTkFrame(pg, fg_color=T["bg_card"], height=28, corner_radius=4)
        info.pack(fill="x", pady=(0, 3))
        self.info_lbl = ctk.CTkLabel(info, text="💾  No device scanned yet",
                                     font=(FONT, 10), text_color=T["accent"], anchor="w")
        self.info_lbl.pack(side="left", padx=10)

        self.pkg_list = PackageList(pg, self, T)
        self.pkg_list.pack(fill="both", expand=True)

    # ─── Tabs ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _new_view():
        return {"subcat": "All", "query": "", "top": 0, "items": None, "count": 0}

    def _style_tab(self, key, active):
        T = self.T
        btn = self._tab_btns.get(key)
        if btn is None:
            return
        if active:
            btn.configure(fg_color=T["accent"], text_color=T["btn_primary_text"])
        else:
            btn.configure(fg_color="transparent", text_color=T["text_muted"])

    def _switch_tab(self, key: str, force: bool = False):
        prev = self._active_tab
        if key == prev and not force:
            return
        if prev in APP_TABS and not force:
            self._views[prev]["top"] = self.pkg_list.get_top()
        self._active_tab = key

        if force:
            for k in self._tab_btns:
                self._style_tab(k, k == key)
        else:
            self._style_tab(prev, False)
            self._style_tab(key, True)

        if key in APP_TABS:
            self._load_tab(key)
            self._pages["pkg"].tkraise()
        else:
            self._ensure_page(key)
            self._pages[key].tkraise()
        if getattr(self, "log_visible", False):
            self.log_panel.lift()

    # ── package tab content ──────────────────────────────────────────────────

    def _empty_message(self, cat):
        if self._scanning:
            return "Scanning device… the list appears here when it finishes."
        if not self.device_info:
            return "No device connected.\n\nConnect your Galaxy phone via USB, enable USB Debugging, then click Scan."
        if not self._scanned:
            return "Device ready.\n\nClick  ⟳ Scan  to load the package list."
        return "No packages in this category."

    def _build_items(self, cat):
        v = self._views[cat]
        entries = self.app_data.get(cat, [])
        subcat, query = v["subcat"], v["query"].lower().strip()
        if subcat != "All":
            entries = [e for e in entries if (e.subcategory or "Other") == subcat]
        if query:
            entries = [e for e in entries if query in e.pkg.lower() or query in e.name.lower()]
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e.subcategory or "Other", []).append(e)
        items = []
        for title, lst in groups.items():
            items.append(("h", title, lst))
            items.extend(("r", e, None) for e in lst)
        v["items"] = items
        v["count"] = len(entries)

    def _load_tab(self, cat):
        """Show a category. Cheap: no widgets are created or destroyed."""
        v = self._views[cat]
        all_entries = self.app_data.get(cat, [])
        subs = sorted({(e.subcategory or "Other") for e in all_entries})
        if v["subcat"] != "All" and v["subcat"] not in subs:
            v["subcat"] = "All"

        self._loading_filters = True
        try:
            self.subcat_menu.configure(values=["All"] + subs)
            self.subcat_var.set(v["subcat"])
            if self.search_entry.get() != v["query"]:
                self.search_entry.delete(0, "end")
                if v["query"]:
                    self.search_entry.insert(0, v["query"])
        finally:
            self._loading_filters = False

        if v["items"] is None:
            self._build_items(cat)
        msg = ""
        if not v["items"]:
            msg = self._empty_message(cat) if not all_entries else "No packages match your filter."
        self.pkg_list.set_items(v["items"], msg, v["top"])
        self._update_info()

    def _update_info(self):
        cat = self._active_tab
        if cat not in APP_TABS:
            return
        total = len(self.app_data.get(cat, []))
        if not self.device_info and not total:
            txt = "💾  No device connected"
        elif not total:
            txt = "💾  No device scanned yet"
        else:
            sel = sum(1 for e in self.app_data[cat] if e.checked)
            shown = self._views[cat]["count"]
            extra = f"  ·  showing {shown}" if shown != total else ""
            txt = f"💾  {total} packages{extra}  ·  {sel} selected"
        self.info_lbl.configure(text=txt)

    def _invalidate_views(self, reset_top=True):
        for v in self._views.values():
            v["items"] = None
            if reset_top:
                v["top"] = 0
        if self._active_tab in APP_TABS:
            self._load_tab(self._active_tab)

    # ── filters ──────────────────────────────────────────────────────────────

    def _on_subcat_changed(self):
        cat = self._active_tab
        if cat not in APP_TABS or self._loading_filters:
            return
        v = self._views[cat]
        v["subcat"] = self.subcat_var.get()
        v["top"] = 0
        v["items"] = None
        self._load_tab(cat)

    def _on_filter_typed(self):
        if self._loading_filters or self._active_tab not in APP_TABS:
            return
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
        self._filter_job = self.after(160, self._apply_search)     # debounce typing

    def _apply_search(self):
        self._filter_job = None
        cat = self._active_tab
        if cat not in APP_TABS:
            return
        v = self._views[cat]
        v["query"] = self.search_entry.get()
        v["top"] = 0
        v["items"] = None
        self._load_tab(cat)

    # ── selection ────────────────────────────────────────────────────────────

    def _toggle_entry(self, e: AppEntry):
        if e.risk == "KEEP":
            self.status_text.configure(text=f"{e.name} is protected and can't be selected.")
            return
        if e.checked:
            e.checked = False
        elif e.risk == "CORE":
            self._show_core_warning(e)
            return
        else:
            e.checked = True
        self.pkg_list.refresh()
        self._update_info()

    def _set_checked(self, entries, state: bool):
        for e in entries:
            if e.risk == "KEEP":
                continue
            if state and e.risk == "CORE":
                continue            # core apps are never bulk-selected
            e.checked = state
        self.pkg_list.refresh()
        self._update_info()

    def _select_visible(self, state: bool):
        cat = self._active_tab
        if cat not in APP_TABS:
            return
        v = self._views[cat]
        if v["items"] is None:
            self._build_items(cat)
        self._set_checked([it[1] for it in v["items"] if it[0] == "r"], state)

    def _show_core_warning(self, entry: AppEntry):
        T = self.T
        win = ctk.CTkToplevel(self)
        win.title("Core System App")
        win.configure(fg_color=T["bg"])
        win.resizable(False, False)
        self._place_dialog(win, 500, 300)
        self._make_modal(win)
        ctk.CTkLabel(win, text="⛔  Core System App",
                     font=(FONT, 14, "bold"), text_color=T["core_red"]).pack(pady=(18, 3))
        ctk.CTkLabel(win, text=f"{entry.name}", font=(FONT, 12, "bold"),
                     text_color=T["text"]).pack()
        ctk.CTkLabel(win, text=entry.pkg, font=(MONO, 9),
                     text_color=T["text_muted"]).pack(pady=(0, 7))
        mf = ctk.CTkFrame(win, fg_color="#1a0505", corner_radius=7,
                          border_width=1, border_color=T["core_red"])
        mf.pack(fill="x", padx=18, pady=3)
        ctk.CTkLabel(mf,
                     text=f"{entry.description}\n\n"
                          "⚠ Disabling may affect device stability.\n"
                          "✓ Reversible — re-enable anytime while device is connected.",
                     font=(FONT, 10), text_color=T["text"],
                     wraplength=420, justify="left").pack(padx=10, pady=8)
        br = ctk.CTkFrame(win, fg_color="transparent")
        br.pack(pady=12)

        def proceed():
            entry.checked = True
            win.destroy()
            self.pkg_list.refresh()
            self._update_info()

        ctk.CTkButton(br, text="Cancel", width=110, fg_color=T["bg_hover"],
                      text_color=T["text"], command=win.destroy).pack(side="left", padx=7)
        ctk.CTkButton(br, text="I understand, proceed", width=170, fg_color=T["core_red"],
                      text_color="#ffffff", command=proceed).pack(side="left", padx=7)

    # ─── Non-package pages: built ONCE, then only raised ──────────────────────

    def _ensure_page(self, key):
        if key in self._built:
            return
        if key == "about" and getattr(self, "_about_gen", None) is not None:
            for _ in self._about_gen:          # finish the slices already in progress
                pass
            self._about_gen = None
            self._built.add("about")
            return
        self._built.add(key)
        page = self._pages[key]
        if key == "logs":
            self._build_logs_page(page)
        elif key == "settings":
            self._build_settings_page(page)
        elif key == "about":
            self._build_about_page(page)

    def _prebuild_pages(self):
        """Build the other pages in the background (tiny slices), so the first
        click on any tab is instant and nothing ever blocks the UI."""
        if self._closing:
            return
        owner = self.ui

        def alive():
            return owner is self.ui and not self._closing

        def build(key):
            if alive() and key not in self._built:
                try:
                    self._ensure_page(key)
                except Exception:
                    pass

        def build_about_sliced():
            if not alive() or "about" in self._built:
                return
            gen = self._about_steps(self._pages["about"])
            self._about_gen = gen

            def step():
                if not alive() or "about" in self._built:
                    return
                try:
                    next(gen)
                    self.after(30, step)
                except StopIteration:
                    self._built.add("about")
                except Exception:
                    self._built.add("about")
            step()

        self.after(100, lambda: build("logs"))
        self.after(250, lambda: build("settings"))
        self.after(400, build_about_sliced)

    def _build_logs_page(self, p):
        T = self.T
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(bar, text="Export Log", width=100, height=28,
                      fg_color=T["bg_card"], text_color=T["text"],
                      hover_color=T["bg_hover"], command=self._export_log).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Clear", width=60, height=28,
                      fg_color=T["bg_card"], text_color=T["text_muted"],
                      hover_color=T["bg_hover"], command=self._clear_log).pack(side="left")
        self.log_view = self._make_log_box(p, 11)
        self.log_view.pack(fill="both", expand=True)
        for lvl, line in self._log_lines[-1000:]:
            self._insert_log(self.log_view, lvl, line, see=False)
        try:
            self.log_view.see("end")
        except Exception:
            pass

    def _build_settings_page(self, p):
        T = self.T
        box = ctk.CTkFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True)

        def section(title):
            ctk.CTkLabel(box, text=title, font=(FONT, 11, "bold"),
                         text_color=T["accent"]).pack(anchor="w", pady=(12, 2))
            ctk.CTkFrame(box, fg_color=T["border"], height=1).pack(fill="x", pady=(0, 7))

        def path_row(label, var, browse_cmd, extra_btn):
            ctk.CTkLabel(box, text=label, font=(FONT, 10), text_color=T["text"],
                         anchor="w").pack(anchor="w")
            fr = ctk.CTkFrame(box, fg_color="transparent")
            fr.pack(fill="x", pady=(2, 4))
            ctk.CTkEntry(fr, textvariable=var, height=28, fg_color=T["bg_card"],
                         border_color=T["border"], text_color=T["text"]
                         ).pack(side="left", fill="x", expand=True, padx=(0, 5))
            ctk.CTkButton(fr, text="Browse", width=66, height=28, fg_color=T["bg_card"],
                          text_color=T["text"], hover_color=T["bg_hover"],
                          command=browse_cmd).pack(side="left", padx=(0, 5))
            extra_btn(fr)

        section("ADB Configuration")
        path_row("ADB executable", self.adb_path_var, self._browse_adb,
                 lambda fr: ctk.CTkButton(fr, text="Test ADB", width=76, height=28,
                                          fg_color=T["accent"], text_color=T["btn_primary_text"],
                                          hover_color=T["accent_dim"],
                                          command=self._test_adb).pack(side="left"))

        section("Backup")
        path_row("Backup folder", self.backup_path_var, self._browse_backup,
                 lambda fr: ctk.CTkButton(fr, text="Open", width=60, height=28,
                                          fg_color=T["bg_card"], text_color=T["text"],
                                          hover_color=T["bg_hover"],
                                          command=self._open_backup_folder).pack(side="left"))

        section("Appearance")
        tr = ctk.CTkFrame(box, fg_color="transparent")
        tr.pack(fill="x")
        for name in THEMES:
            t = get_theme(name)
            ctk.CTkButton(tr, text=("● " if name == self.current_theme_name else "") + name,
                          height=30, fg_color=t["accent"], text_color=t["btn_primary_text"],
                          hover_color=t["accent_dim"],
                          command=lambda n=name: self._switch_theme(n)
                          ).pack(side="left", fill="x", expand=True, padx=(0, 5))

        section("Emergency Recovery")
        ctk.CTkButton(box, text="⚡  Panic Restore — re-enable all from latest backup",
                      font=(FONT, 10, "bold"), fg_color=T["core_red"],
                      text_color="#ffffff", hover_color="#cc0000", height=34,
                      command=self._panic_restore).pack(fill="x", pady=6)
        ctk.CTkLabel(box, text="Re-enables all packages from the most recent automatic backup.",
                     font=(FONT, 9), text_color=T["text_muted"]).pack(anchor="w")

    def _build_about_page(self, p):
        for _ in self._about_steps(p):      # synchronous: used if the user opens About first
            pass

    def _about_steps(self, p):
        T = self.T
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent",
                                        scrollbar_button_color=T["scrollbar"],
                                        scrollbar_button_hover_color=T["accent"])
        scroll.pack(fill="both", expand=True)

        card = ctk.CTkFrame(scroll, fg_color=T["bg_card"], corner_radius=8,
                            border_width=1, border_color=T["border"])
        card.pack(fill="x", pady=(0, 5))
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        logo_img = _load_logo(44)
        if logo_img:
            ctk.CTkLabel(hdr, image=logo_img, text="").pack(side="left", padx=(0, 10))
        name_col = ctk.CTkFrame(hdr, fg_color="transparent")
        name_col.pack(side="left", anchor="w")
        ctk.CTkLabel(name_col, text=APP_NAME, font=(FONT, 14, "bold"),
                     text_color=T["accent"], anchor="w").pack(anchor="w")
        ctk.CTkLabel(name_col, text=f"{APP_VERSION}  ·  {APP_BRAND}  ·  Chennai, India",
                     font=(FONT, 9), text_color=T["text_muted"]).pack(anchor="w")
        ctk.CTkLabel(name_col, text="Works with Samsung Galaxy Devices",
                     font=(FONT, 9), text_color=T["text_dim"]).pack(anchor="w")
        ctk.CTkFrame(card, fg_color=T["border"], height=1).pack(fill="x", padx=14, pady=(0, 4))
        info_grid = ctk.CTkFrame(card, fg_color="transparent")
        info_grid.pack(fill="x", padx=14, pady=(0, 8))
        for label, value in [("Author", APP_AUTHOR), ("License", APP_LICENSE),
                             ("Platform", "Windows 10 / 11"), ("Version", APP_VERSION)]:
            r = ctk.CTkFrame(info_grid, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=label, font=(FONT, 9), text_color=T["text_muted"],
                         width=65, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=value, font=(FONT, 9, "bold"),
                         text_color=T["text"], anchor="w").pack(side="left")

        yield

        strip = ctk.CTkFrame(scroll, fg_color=T["bg_card"], corner_radius=6,
                             border_width=1, border_color=T["border"])
        strip.pack(fill="x", pady=(0, 5))
        ctk.CTkButton(strip, text=f"🔗  GitHub — {APP_GITHUB}", font=(FONT, 9),
                      fg_color="transparent", text_color=T["accent"],
                      hover_color=T["bg_hover"], height=28, anchor="w",
                      command=lambda: webbrowser.open(APP_GITHUB)).pack(fill="x", padx=8)
        ctk.CTkLabel(strip, text=f"☑  Latest version ({APP_VERSION})",
                     font=(FONT, 9), text_color=T["text_muted"],
                     anchor="w").pack(fill="x", padx=12, pady=(0, 6))

        yield

        don_card = ctk.CTkFrame(scroll, fg_color=T["bg_card"], corner_radius=8,
                                border_width=1, border_color=T["border"])
        don_card.pack(fill="x", pady=(0, 5))
        don_hdr = ctk.CTkFrame(don_card, fg_color="transparent")
        don_hdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(don_hdr, text="💙  Support DebloatKit",
                     font=(FONT, 10, "bold"), text_color=T["text"]).pack(side="left")
        ctk.CTkLabel(don_hdr, text="Free forever — support if it helped!",
                     font=(FONT, 8), text_color=T["text_muted"]).pack(side="right")
        don_btns = ctk.CTkFrame(don_card, fg_color="transparent")
        don_btns.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(don_btns, text="💙  PayPal ($)", font=(FONT, 10, "bold"),
                      fg_color="#003087", text_color="#FFFFFF", hover_color="#001f5b", height=30,
                      command=lambda: webbrowser.open("https://paypal.me/yash92duster")
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(don_btns, text="🟦  Razorpay (₹)", font=(FONT, 10, "bold"),
                      fg_color=T["accent"], text_color=T["btn_primary_text"],
                      hover_color=T["accent_dim"], height=30,
                      command=lambda: webbrowser.open("https://rzp.io/rzp/nsogoeD")
                      ).pack(side="left", fill="x", expand=True)

        yield

        cred = ctk.CTkFrame(scroll, fg_color=T["bg_card"], corner_radius=8,
                            border_width=1, border_color=T["border"])
        cred.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(cred, text="Credits", font=(FONT, 9, "bold"),
                     text_color=T["accent"]).pack(anchor="w", padx=12, pady=(6, 3))
        for cname, cdesc in [
            ("XDA Community", "Package safety ratings from XDA forums and Samsung debloat threads."),
            ("Google Research", "AOSP ADB docs, pm command reference, Android developer documentation."),
        ]:
            r = ctk.CTkFrame(cred, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=(0, 4))
            ctk.CTkLabel(r, text=f"· {cname}", font=(FONT, 9, "bold"),
                         text_color=T["text"], anchor="w").pack(anchor="w")
            ctk.CTkLabel(r, text=cdesc, font=(FONT, 8), text_color=T["text_muted"],
                         anchor="w", wraplength=480, justify="left").pack(anchor="w")
        ctk.CTkFrame(cred, fg_color="transparent", height=4).pack()

        yield

        usb_card = ctk.CTkFrame(scroll, fg_color=T["bg_card"], corner_radius=8,
                                border_width=1, border_color=T["border"])
        usb_card.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(usb_card, text="📱  How to Use DebloatKit",
                     font=(FONT, 10, "bold"), text_color=T["accent"]
                     ).pack(anchor="w", padx=12, pady=(8, 4))
        for section_title, steps in [
            ("Enable USB Debugging", [
                "Settings → About Phone → Software Information",
                "Tap Build Number 7 times → 'Developer mode enabled'",
                "Settings → Developer Options → USB Debugging → ON",
                "Connect phone to PC via USB",
                "Tap 'Allow' on phone when prompted",
                "Click Scan in DebloatKit",
            ]),
            ("After Debloating — Disable USB Debugging", [
                "Settings → Developer Options → USB Debugging → OFF",
                "Optional: Developer Options → OFF to hide the menu",
                "Disconnect USB cable",
            ]),
        ]:
            ctk.CTkLabel(usb_card, text=section_title, font=(FONT, 9, "bold"),
                         text_color=T["text"], anchor="w").pack(anchor="w", padx=12, pady=(4, 2))
            for i, step in enumerate(steps, 1):
                row = ctk.CTkFrame(usb_card, fg_color=T["bg"], corner_radius=4)
                row.pack(fill="x", padx=12, pady=1)
                ctk.CTkLabel(row, text=str(i), font=(FONT, 9, "bold"),
                             text_color=T["accent"], width=18, anchor="center"
                             ).pack(side="left", padx=(6, 4), pady=4)
                ctk.CTkLabel(row, text=step, font=(FONT, 9),
                             text_color=T["text_muted"], anchor="w"
                             ).pack(side="left", padx=(0, 6), pady=4)
        ctk.CTkLabel(usb_card, text="⚠  Always disable USB Debugging after use for security.",
                     font=(FONT, 8, "bold"), text_color=T["warning"],
                     anchor="w").pack(anchor="w", padx=12, pady=(4, 8))

        ctk.CTkLabel(scroll,
                     text="Not affiliated with Samsung Electronics Co., Ltd.  ·  MIT License\nFree to use, modify and distribute.",
                     font=(FONT, 8), text_color=T["text_dim"],
                     justify="center").pack(pady=(2, 8))

    # ─── Actions ──────────────────────────────────────────────────────────────

    def _run_action(self, category: str, action: str):
        if self._action_running:
            messagebox.showwarning("Busy", "An action is already running.")
            return
        if not self.device_info:
            messagebox.showerror("No Device", "Connect your device first.")
            return
        if category not in APP_TABS:
            messagebox.showinfo("Pick a tab", "Open System, Core, User or 3rd Party and select packages first.")
            return

        checked = [e for e in self.app_data.get(category, []) if e.checked and e.risk != "KEEP"]
        if not checked:
            messagebox.showinfo("Nothing Selected", "Check at least one package first.")
            return

        # Only act on apps whose state makes sense for the action
        if action == "disable":
            selected = [e for e in checked if e.state == "enabled"]
        elif action == "uninstall":
            selected = [e for e in checked if e.state in ("enabled", "disabled")]
        else:
            selected = [e for e in checked if e.state in ("disabled", "uninstalled")]
        skipped = len(checked) - len(selected)
        if not selected:
            messagebox.showinfo("Nothing to do",
                                {"disable": "Selected apps are already disabled or uninstalled.",
                                 "uninstall": "Selected apps are already uninstalled.",
                                 "enable": "Selected apps are already enabled."}[action])
            return

        verb = {"disable": "Disable", "uninstall": "Uninstall", "enable": "Re-enable"}[action]
        desc = {"disable": "Freeze selected apps (reversible — re-enable anytime).",
                "uninstall": "Remove from user profile (reversible — APK stays on device).",
                "enable": "Restore all selected disabled/uninstalled apps."}[action]
        note = f"\n\n({skipped} selected app(s) skipped — not applicable.)" if skipped else ""
        if not messagebox.askyesno(f"Confirm — {verb} {len(selected)} app(s)",
                                   f"{verb} {len(selected)} selected package(s)?\n\n{desc}{note}"):
            return

        self._action_running = True
        for btn in (self.disable_btn, self.uninstall_btn, self.reenable_btn):
            btn.configure(state="disabled")
        self.status_prog.set(0)
        all_entries = [e for cat in self.app_data.values() for e in cat]

        def progress_cb(val, msg):
            self._set_progress(val, msg)

        def result_cb(entry, result):
            self._list_dirty = True            # row colours/state update in place

        def run():
            try:
                if action == "disable":
                    results = self.debloater.disable_packages(selected, all_entries, progress_cb, result_cb)
                elif action == "uninstall":
                    results = self.debloater.uninstall_packages(selected, all_entries, progress_cb, result_cb)
                else:
                    results = self.debloater.restore_packages(selected, progress_cb, result_cb)
                summary = self.debloater.get_summary(results)
            except Exception as ex:
                self._log(f"Action error: {ex}", "error")
                summary = {"success": 0, "failed": len(selected)}

            def done():
                self._action_running = False
                for btn in (self.disable_btn, self.uninstall_btn, self.reenable_btn):
                    btn.configure(state="normal")
                self.status_prog.set(1)
                self.status_text.configure(
                    text=f"{verb} complete — {summary['success']} succeeded · {summary['failed']} failed")
                self._invalidate_views(reset_top=False)
                self._show_summary(summary, verb)
                if action in ("disable", "uninstall") and \
                        any(e.pkg == "com.sec.android.diagmonagent" for e in selected):
                    self.after(500, self._offer_soundalive_fix)
            self.ui_call(done)

        threading.Thread(target=run, daemon=True).start()

    def _show_summary(self, summary: dict, action: str):
        T = self.T
        win = ctk.CTkToplevel(self)
        win.title("Done")
        win.configure(fg_color=T["bg"])
        win.resizable(False, False)
        self._place_dialog(win, 340, 200)
        self._make_modal(win)
        ctk.CTkLabel(win, text="✓  Complete", font=(FONT, 14, "bold"),
                     text_color=T["success"]).pack(pady=(16, 7))
        info = ctk.CTkFrame(win, fg_color=T["bg_card"], corner_radius=7)
        info.pack(fill="x", padx=22, pady=3)

        def row(label, value, color=None):
            fr = ctk.CTkFrame(info, fg_color="transparent")
            fr.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(fr, text=label, font=(FONT, 10), text_color=T["text_muted"]).pack(side="left")
            ctk.CTkLabel(fr, text=str(value), font=(FONT, 10, "bold"),
                         text_color=color or T["text"]).pack(side="right")
        row("Action", action.title())
        row("Succeeded", summary["success"], T["success"])
        if summary["failed"] > 0:
            row("Failed", summary["failed"], T["error"])
        row("Backup", "Saved automatically", T["accent"])
        ctk.CTkButton(win, text="Close", fg_color=T["accent"], hover_color=T["accent_dim"],
                      text_color=T["btn_primary_text"], width=90,
                      command=win.destroy).pack(pady=12)

    def _offer_soundalive_fix(self):
        if messagebox.askyesno("SoundAlive Fix",
                               "Diagnostic Monitor was removed.\nRun SoundAlive flush? (Recommended)"):
            threading.Thread(target=self.debloater.soundalive_fix, daemon=True).start()

    # ─── Log ──────────────────────────────────────────────────────────────────

    def _toggle_log_panel(self):
        if self.log_visible:
            self.log_panel.place_forget()
            self.log_visible = False
            self.log_btn.configure(fg_color=self.T["bg"], text_color=self.T["text"])
        else:
            self._show_log_panel()

    def _show_log_panel(self):
        self.log_panel.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
        self.log_panel.lift()
        self.log_visible = True
        self.log_btn.configure(fg_color=self.T["accent"], text_color=self.T["btn_primary_text"])

    def _log(self, message: str, level: str = "info"):
        """Thread-safe: just queues the line; the UI thread flushes in batches."""
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        self._log_q.append((level if level in ("info", "success", "warning", "error") else "info", line))

    def _insert_log(self, box, level, line, see=True):
        try:
            box.configure(state="normal")
            box.insert("end", line, level)
            if see:
                box.see("end")
            box.configure(state="disabled")
        except Exception:
            pass

    def _flush_log(self):
        if not self._log_q:
            return
        batch = []
        while True:
            try:
                batch.append(self._log_q.popleft())
            except IndexError:
                break
        self._log_lines.extend(batch)
        if len(self._log_lines) > 3000:
            del self._log_lines[:1000]
        for box in (getattr(self, "log_text", None), getattr(self, "log_view", None)):
            if box is None:
                continue
            try:
                box.configure(state="normal")
                for lvl, line in batch:
                    box.insert("end", line, lvl)
                if int(box.index("end-1c").split(".")[0]) > 1500:
                    box.delete("1.0", "400.0")
                box.see("end")
                box.configure(state="disabled")
            except Exception:
                pass

    def _clear_log(self):
        self._log_lines.clear()
        self._log_q.clear()
        for box in (getattr(self, "log_text", None), getattr(self, "log_view", None)):
            try:
                if box is not None:
                    box.configure(state="normal")
                    box.delete("1.0", "end")
                    box.configure(state="disabled")
            except Exception:
                pass

    def _export_log(self):
        self._flush_log()
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text", "*.txt")],
            initialfile=f"DebloatKit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("".join(l for _, l in self._log_lines))
                self._log(f"Log exported: {path}", "success")
            except Exception as e:
                self._log(f"Export failed: {e}", "error")

    # ─── Device polling (callbacks arrive on the poll thread → queue to UI) ───

    def _start_device_polling(self):
        self.adb.start_polling(
            on_status_change=self._on_device_status,
            on_connected=self._on_device_connected,
            on_disconnected=self._on_device_disconnected
        )

    def _on_device_status(self, status: str):
        def upd():
            self._conn_status = status
            self._apply_device_ui()
            # USB guide: once per unauthorized episode, never from the poll thread
            if status == "unauthorized" and not self._usb_guide_shown:
                self._usb_guide_shown = True
                self.after(300, self._show_usb_guide)
            elif status != "unauthorized":
                self._usb_guide_shown = False
        self.ui_call(upd)

    def _on_device_connected(self, info: DeviceInfo):
        def upd():
            self.device_info = info
            self._scanned = False
            self._apply_device_ui()
            self.status_text.configure(text=f"Device ready — {info.brand} {info.model}")
            if self._active_tab in APP_TABS and not self.app_data.get(self._active_tab):
                self._load_tab(self._active_tab)      # refresh the hint message
        self.ui_call(upd)

    def _on_device_disconnected(self):
        def upd():
            self.device_info = None
            self._scanned = False
            self.app_data = {"system": [], "core": [], "user": [], "thirdparty": [], "keep": []}
            self._usb_guide_shown = False
            self._apply_device_ui()
            self.status_text.configure(text="Device disconnected.")
            self._invalidate_views()
        self.ui_call(upd)

    def _apply_device_ui(self):
        T, info = self.T, self.device_info
        try:
            colors = {"no_device": "#333333", "unauthorized": "#ffab00", "ready": "#00e676"}
            labels = {"no_device": "No Device", "unauthorized": "Unauthorized", "ready": "Connected"}
            self.status_dot.configure(text_color=colors.get(self._conn_status, "#333333"))
            self.status_label.configure(text=labels.get(self._conn_status, self._conn_status))
            if info:
                self.dev_model_lbl.configure(text=f"{info.brand} {info.model}", text_color=T["text"])
                self.dev_info_lbl.configure(
                    text=f"Android {info.android_version} · One UI {info.oneui_version or '?'} · API {info.api_level}",
                    text_color=T["text_muted"])
                self.bat_lbl.configure(
                    text=f"🔋 {info.battery_level}%",
                    text_color=T["error"] if info.battery_level < 20 else T["text_muted"])
                self.scan_btn.configure(
                    state="disabled" if self._scanning else "normal",
                    text="Scanning..." if self._scanning else ("⟳  Re-Scan" if self._scanned else "⟳  Scan"))
            else:
                self.dev_model_lbl.configure(text="No device connected", text_color=T["text_muted"])
                self.dev_info_lbl.configure(text="Connect via USB · Enable USB Debugging",
                                            text_color=T["text_dim"])
                self.bat_lbl.configure(text="")
                self.scan_btn.configure(state="disabled", text="⟳  Scan")
        except Exception:
            pass

    def _show_usb_guide(self):
        if getattr(self, "_usb_guide_win", None):
            try:
                if self._usb_guide_win.winfo_exists():
                    return
            except Exception:
                pass
        T = self.T
        win = ctk.CTkToplevel(self)
        win.title("Enable USB Debugging")
        win.configure(fg_color=T["bg"])
        win.resizable(False, False)
        self._place_dialog(win, 480, 380)
        win.after(250, lambda: _set_icon(win) if win.winfo_exists() else None)
        self._usb_guide_win = win      # non-modal on purpose: the app stays usable

        ctk.CTkLabel(win, text="Enable USB Debugging",
                     font=(FONT, 14, "bold"), text_color=T["accent"]).pack(pady=(14, 8))
        for i, step in enumerate([
            "Open Settings on your Galaxy device",
            "Go to About Phone → Software Information",
            "Tap Build Number 7 times to unlock Developer Options",
            "Go back to Settings → Developer Options",
            "Enable USB Debugging",
            "Connect via USB — tap Allow on your phone",
            "DebloatKit detects your device automatically",
        ], 1):
            r = ctk.CTkFrame(win, fg_color=T["bg_card"], corner_radius=5)
            r.pack(fill="x", padx=20, pady=2)
            ctk.CTkLabel(r, text=str(i), font=(FONT, 10, "bold"),
                         text_color=T["accent"], width=22).pack(side="left", padx=9, pady=5)
            ctk.CTkLabel(r, text=step, font=(FONT, 10),
                         text_color=T["text"], anchor="w").pack(side="left", padx=4)
        ctk.CTkButton(win, text="Got it", fg_color=T["accent"], hover_color=T["accent_dim"],
                      text_color=T["btn_primary_text"], width=110,
                      command=win.destroy).pack(pady=10)

    # ─── Scan ─────────────────────────────────────────────────────────────────

    def _start_scan(self):
        if self._scanning:
            return
        if not self.device_info:
            messagebox.showerror("No Device", "Connect your device first.")
            return
        self._scanning = True
        self._apply_device_ui()
        self.status_text.configure(text="Scanning device packages...")
        self.status_prog.set(0)
        if self._active_tab in APP_TABS and not self.app_data.get(self._active_tab):
            self._load_tab(self._active_tab)

        def run():
            try:
                data = self.scanner.scan(self.device_info,
                                         progress_callback=lambda v, m: self._set_progress(v, m))
            except Exception as ex:
                self._log(f"Scan failed: {ex}", "error")
                data = None

            def done():
                self._scanning = False
                if data is not None:
                    self.app_data = data
                    self._scanned = True
                    total = sum(len(v) for v in data.values())
                    self.status_text.configure(text=f"Scan complete — {total} packages found")
                else:
                    self.status_text.configure(text="Scan failed — see Log.")
                self.status_prog.set(1 if data is not None else 0)
                self._apply_device_ui()
                self._invalidate_views()
            self.ui_call(done)

        threading.Thread(target=run, daemon=True).start()

    # ─── Settings helpers ─────────────────────────────────────────────────────

    def _browse_adb(self):
        path = filedialog.askopenfilename(filetypes=[("ADB", "adb.exe"), ("All", "*.*")])
        if path:
            self.adb_path_var.set(path)
            self.adb.adb_path = path

    def _test_adb(self):
        self.adb.adb_path = self.adb_path_var.get()
        ok_exe, _ = self.adb.run(["version"], timeout=5)
        if not ok_exe:
            messagebox.showerror("ADB Not Found",
                                 f"ADB executable not found at:\n{self.adb_path_var.get()}\n\n"
                                 "Download Platform Tools and set the correct path.")
            return
        ok_dev, out = self.adb.run(["devices"], timeout=5)
        lines = [l for l in out.splitlines()[1:] if l.strip() and "\t" in l]
        if lines:
            messagebox.showinfo("ADB Working",
                                f"ADB found and device detected!\n\n{lines[0].strip()}")
        else:
            messagebox.showinfo("ADB Found — No Device",
                                "ADB executable is working correctly.\n\n"
                                "No device detected. Connect your Galaxy device\n"
                                "and enable USB Debugging.")

    def _browse_backup(self):
        path = filedialog.askdirectory()
        if path:
            self.backup_path_var.set(path)
            self.debloater.backup_dir = path

    def _open_backup_folder(self):
        d = self.debloater.backup_dir
        if os.path.exists(d) and hasattr(os, "startfile"):
            os.startfile(d)

    def _panic_restore(self):
        bp = self.debloater.get_latest_backup_path()
        if not bp:
            messagebox.showinfo("Panic Restore", "No backups found.")
            return
        if not self.device_info:
            messagebox.showerror("No Device", "Device must be connected.")
            return
        if messagebox.askyesno("Panic Restore",
                               f"Re-enable ALL from:\n{os.path.basename(bp)}\n\nProceed?"):
            def run():
                self.debloater.panic_restore(bp)
                self.ui_call(lambda: self.status_text.configure(text="Panic Restore finished — Re-Scan to refresh."))
            threading.Thread(target=run, daemon=True).start()

    def on_close(self):
        self._closing = True
        try:
            self.adb.stop_polling()
        except Exception:
            pass
        self.destroy()


def main():
    app = DebloatKit()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
