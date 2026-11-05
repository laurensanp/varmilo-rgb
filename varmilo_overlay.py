#!/usr/bin/env python3
"""Always-on-top lighting overlay for the Varmilo Minilo75 Pro (ANSI). Needs: pip install hidapi pystray pillow

Which LED belongs to which key ships as DEFAULT_MAP (learned on an ANSI Minilo75 Pro). Other layouts
can learn their own: the firmware paints a pressed key's LED white, so "Tasten einlernen" asks for each
key and records the LED that turned white.

Colours live in the keyboard's RAM, so the overlay must keep running. Quitting it from the tray hands
the keyboard to SignalRGB (plugin on "Auto") or back to the onboard lighting.
"""
__version__ = "1.1.0"
import colorsys
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
import winreg
from tkinter import colorchooser, simpledialog

import pystray
from PIL import Image, ImageDraw, ImageTk

import varmilo as kb

# (key id, width in key units), a bare number is a gap. Text on the key is the id without its L/R prefix.
ROWS = [
    [("Esc", 1), .25, ("F1", 1), ("F2", 1), ("F3", 1), ("F4", 1), .25, ("F5", 1), ("F6", 1), ("F7", 1), ("F8", 1), .25,
     ("F9", 1), ("F10", 1), ("F11", 1), ("F12", 1), 1.25, ("Del", 1)],
    [("`", 1), ("1", 1), ("2", 1), ("3", 1), ("4", 1), ("5", 1), ("6", 1), ("7", 1), ("8", 1), ("9", 1), ("0", 1), ("-", 1), ("=", 1),
     ("Backspace", 2), ("Ins", 1)],
    [("Tab", 1.5), ("Q", 1), ("W", 1), ("E", 1), ("R", 1), ("T", 1), ("Y", 1), ("U", 1), ("I", 1), ("O", 1), ("P", 1), ("[", 1), ("]", 1),
     ("\\", 1.5), ("PgUp", 1)],
    [("Caps", 1.75), ("A", 1), ("S", 1), ("D", 1), ("F", 1), ("G", 1), ("H", 1), ("J", 1), ("K", 1), ("L", 1), (";", 1), ("'", 1),
     ("Enter", 2.25), ("PgDn", 1)],
    [("LShift", 2.25), ("Z", 1), ("X", 1), ("C", 1), ("V", 1), ("B", 1), ("N", 1), ("M", 1), (",", 1), (".", 1), ("/", 1),
     ("RShift", 1.75), ("Up", 1), ("Fn", 1)],
    [("LCtrl", 1.25), ("Win", 1.25), ("Alt", 1.25), ("Space", 6.25), ("RAlt", 1), ("RCtrl", 1), 1, ("Left", 1), ("Down", 1), ("Right", 1)],
]
TEXT = {"LShift": "Shift", "RShift": "Shift", "LCtrl": "Ctrl", "RCtrl": "Ctrl", "RAlt": "Alt", "Backspace": "⌫", "Enter": "Enter", "Up": "↑", "Down": "↓", "Left": "←", "Right": "→", "Space": ""}
KEY_IDS = [key[0] for row in ROWS for key in row if not isinstance(key, (int, float))]
DEFAULT_MAP = {
    "Esc": 77, "F1": 68, "F2": 79, "F3": 69, "F4": 80, "F5": 70, "F6": 81, "F7": 61, "F8": 72, "F9": 83, "F10": 85, "F11": 64,
    "F12": 75, "Del": 20, "`": 66, "1": 67, "2": 78, "3": 57, "4": 47, "5": 58, "6": 48, "7": 59, "8": 71, "9": 82, "0": 50,
    "-": 73, "=": 84, "Backspace": 74, "Ins": 42, "Tab": 55, "Q": 45, "W": 56, "E": 46, "R": 25, "T": 36, "Y": 37, "U": 49,
    "I": 60, "O": 39, "P": 51, "[": 62, "]": 52, "\\": 63, "PgUp": 54, "Caps": 44, "A": 23, "S": 34, "D": 24, "F": 35, "G": 14,
    "H": 26, "J": 27, "K": 38, "L": 28, ";": 29, "'": 40, "Enter": 41, "PgDn": 43, "LShift": 33, "Z": 1, "X": 12, "C": 2,
    "V": 13, "B": 3, "N": 15, "M": 5, ",": 16, ".": 17, "/": 18, "RShift": 30, "Up": 32, "Fn": 7, "LCtrl": 0, "Win": 11,
    "Alt": 22, "Space": 4, "RAlt": 6, "RCtrl": 19, "Left": 9, "Down": 10, "Right": 21,
}
KEY_UNIT = 28
CREAM = "#ffd2a0"  # warm white needs less blue on RGB LEDs
TAUPE = "#a0784f"
DESIGN_LIGHT = {"F5", "F6", "F7", "F8", "Del", "Ins", "PgUp", "PgDn", "Backspace", "Tab", "Caps", "LShift", "RShift", "LCtrl", "Win", "Alt", "Fn", "RAlt", "RCtrl"}
DESIGN_ACCENT = {"Esc", "Enter", "Space", "Up", "Left", "Down", "Right"}
PRESETS = [TAUPE, "#ff0000", "#ff6a00", "#ffd000", "#00ff40", "#00e5ff", "#0040ff", "#a000ff", "#ff00a0", "#ffffff"]
MODES = [("Design", "design"), ("Alle", "solid"), ("Einzeln", "keys"), ("Regenbogen", "rainbow"), ("Original", "original"), ("SignalRGB", "signalrgb")]
HANDS_OFF = ("original", "signalrgb")  # modes where the overlay does not paint
DEFAULTS = {"mode": "design", "color": TAUPE, "brightness": 100, "keys": {}, "map": DEFAULT_MAP, "profiles": {}, "x": 80, "y": 80}
PROFILE_FIELDS = ("mode", "color", "brightness", "keys")  # the learned key map is hardware, not a look
STATE_FILE = os.path.join(os.environ.get("APPDATA", "."), "VarmiloOverlay", "state.json")
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", "."), r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_FILE = os.path.join(STARTUP_DIR, "Varmilo Overlay.vbs")  # .vbs starts it without a console flash
LEGACY_STARTUP_FILE = os.path.join(STARTUP_DIR, "Varmilo Overlay.cmd")
FRAME_BUDGET = 0.04  # about 7 ms per LED, so 5-6 LEDs per frame
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
# While the overlay paints it keeps a marker on LED slot 86, which no key uses: fixed red and blue, and a
# green that changes every 0.1 s as a heartbeat. The SignalRGB plugin in "Auto" stays out of the way while
# the heartbeat moves, and takes the keyboard when it stops (overlay closed, crashed or set to SignalRGB).
MARKER_LED, MARKER_R, MARKER_B = 86, 0x56, 0x4F
# While SignalRGB paints, the firmware is too busy to answer us. So before taking the keyboard the
# overlay repeats its heartbeat this long, unanswered, until the plugin has seen it and stepped back.
CLAIM_SECONDS = 1.2


def marker(now):
    return MARKER_R, int(now * 10) % 250 + 1, MARKER_B


def is_marker(color):
    return color[0] == MARKER_R and color[2] == MARKER_B and color[1] > 0


def rgb(text):
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def base_color(state, key):
    """Colour of one key in the non-animated modes, as #rrggbb."""
    mode = state["mode"]
    if mode == "keys":
        return state["keys"].get(key, "#000000")
    if mode == "design":
        return CREAM if key in DESIGN_LIGHT else state["color"] if key in DESIGN_ACCENT else "#000000"
    return state["color"]


def copy(value):
    return json.loads(json.dumps(value))


def snapshot(state):
    return copy({field: state[field] for field in PROFILE_FIELDS})


def apply_profile(state, profile):
    state.update(copy({field: profile[field] for field in PROFILE_FIELDS if field in profile}))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf8") as f:
            return {**copy(DEFAULTS), **json.load(f)}
    except (OSError, ValueError):
        return copy(DEFAULTS)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)  # a crash mid-write must not lose the saved colours


def pixel(frame, led):
    return frame[led], frame[kb.LEDS + led], frame[2 * kb.LEDS + led]


class Painter:
    """Moves the keyboard towards the wanted colours, and reconnects after a replug."""

    def __init__(self, state, finder=kb.find_keyboard):
        self.state, self.finder = state, finder
        self.dev = None
        self.direct = False
        self.sent = {}
        self.next_search = 0
        self.connected = False
        self.learning = False
        self.seen = set()
        self.hits = queue.Queue()

    def target(self, now):
        s = self.state
        scale = s["brightness"] / 100
        # LEDs without a learned key stay dark, except where every LED shows the same colour anyway.
        out = {led: rgb(s["color"]) if s["mode"] == "solid" else BLACK for led in range(kb.LEDS)}
        for key, led in s["map"].items():
            if s["mode"] == "rainbow":
                hue = KEY_IDS.index(key) / len(KEY_IDS) + now / 8
                out[led] = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(hue % 1, 1, 1))
            else:
                out[led] = rgb(base_color(s, key))
        out = {led: tuple(int(v * scale) for v in c) for led, c in out.items()}
        out[MARKER_LED] = marker(now)
        return out

    def repair(self):
        """The firmware paints pressed keys white and has no known switch for it, so undo it at once."""
        frame = kb.frame(self.dev)
        for led, color in self.sent.items():
            if pixel(frame, led) != color:
                kb.set_led(self.dev, led, *color)

    def paint(self, target):
        keys = {led: c for led, c in target.items() if led != MARKER_LED}
        if len(set(keys.values())) == 1 and any(self.sent.get(led) != c for led, c in keys.items()):
            color = next(iter(keys.values()))
            kb.fill(self.dev, *color)
            self.sent = {led: color for led in target}  # the fill also covered the marker, re-set below
        deadline = time.monotonic() + FRAME_BUDGET
        for led in [led for led in target if self.sent.get(led) != target[led]]:
            if time.monotonic() >= deadline:
                break
            kb.set_led(self.dev, led, *target[led])
            self.sent[led] = target[led]

    def learn(self):
        """All LEDs dark, each newly white LED is reported once. Held keys stay ignored."""
        frame = kb.frame(self.dev)
        for led in range(kb.LEDS):
            if pixel(frame, led) == WHITE:
                kb.set_led(self.dev, led, *BLACK)
                if led not in self.seen:
                    self.seen.add(led)
                    self.hits.put(led)
        beat = marker(time.monotonic())
        if pixel(frame, MARKER_LED) != beat:
            kb.set_led(self.dev, MARKER_LED, *beat)

    def claim(self):
        end = time.monotonic() + CLAIM_SECONDS
        while time.monotonic() < end:
            kb.send(self.dev, kb.SET, MARKER_LED // 11, MARKER_LED % 11, 0, *marker(time.monotonic()))
            time.sleep(0.02)

    def start_learning(self):
        self.seen = set()
        self.direct = False  # entering direct mode again clears the frame
        self.learning = True

    def stop_learning(self):
        self.learning = False
        self.direct = False

    def step(self):
        if self.dev is None:
            if time.monotonic() < self.next_search:
                return
            self.next_search = time.monotonic() + 2
            try:  # right after a cable/wireless switch the device can be listed but not openable yet
                self.dev = self.finder()
            except (OSError, ValueError):
                self.dev = None
            self.connected = self.dev is not None
            if self.dev is None:
                return
        try:
            if self.state["mode"] in HANDS_OFF and not self.learning:
                if self.direct:
                    kb.set_led(self.dev, MARKER_LED, *BLACK)  # tells the plugin the keyboard is free
                    if self.state["mode"] == "original":
                        kb.cmd(self.dev, kb.LEAVE)
                    self.direct = False
                return
            if not self.direct:
                self.claim()
                kb.cmd(self.dev, kb.ENTER)
                self.direct = True
                self.sent = {led: BLACK for led in range(kb.LEDS)}
            if self.learning:
                self.learn()
                return
            self.paint(self.target(time.monotonic()))
            self.repair()
        except (OSError, RuntimeError, ValueError):  # unplugged or woken from sleep: start over
            self.disconnect()

    def disconnect(self):
        if self.dev:
            try:
                self.dev.close()
            except (OSError, ValueError):
                pass
        self.dev, self.direct, self.connected = None, False, False

    def shutdown(self):
        if self.dev and self.direct:
            try:
                kb.cmd(self.dev, kb.LEAVE)
            except (OSError, RuntimeError, ValueError):
                pass
        self.disconnect()


class Overlay:
    BG, SURFACE, RAISED, LINE, FG, MUTED, ACCENT = "#0d1116", "#151b22", "#222b35", "#262f3a", "#e6edf3", "#7d8b99", "#4c8dff"
    KEY_OFF, PLATE, GOOD = "#1b222a", "#11161c", "#3fb950"
    FONT, FONT_BOLD = ("Segoe UI", 9), ("Segoe UI Semibold", 9)
    PAD = 14

    def __init__(self, hidden):
        self.state = load_state()
        self.state["keys"] = {k: v for k, v in self.state["keys"].items() if k in KEY_IDS}
        if not set(self.state["map"]) <= set(KEY_IDS):  # learned with an older layout: learn again
            self.state["map"] = {}
        self.painter = Painter(self.state)
        self.stop = threading.Event()
        self.learn_index = None
        self.learned = {}
        self.root = tk.Tk()
        self.root.title("Varmilo Overlay")
        self.root.protocol("WM_SAVE_YOURSELF", self.close)  # Windows is logging off: quit instead of blocking it
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.BG, highlightthickness=1, highlightbackground=self.LINE)
        self.root.geometry(f"+{self.state['x']}+{self.state['y']}")
        self.build()
        if os.path.exists(LEGACY_STARTUP_FILE):  # older versions used a .cmd that flashed a console
            self.set_autostart(True)
        self.refresh()
        self.round_corners()
        self.actions = queue.Queue()  # tray callbacks run on another thread, Tk must only be touched here
        self.tray = pystray.Icon("varmilo", tray_image(), "Varmilo", pystray.Menu(
            pystray.MenuItem("Öffnen", lambda: self.actions.put(self.show), default=True),
            pystray.MenuItem("Beenden", lambda: self.actions.put(self.close))))
        self.tray.run_detached()
        if hidden and self.state["map"]:
            self.root.withdraw()
        threading.Thread(target=self.worker, daemon=True).start()
        if can_self_update():
            threading.Thread(target=self.check_updates, daemon=True).start()
        self.poll()
        if not self.state["map"]:
            self.begin_learning()

    # ---- updates -----------------------------------------------------------------------------

    def check_updates(self):
        time.sleep(5)  # let the lighting start first
        while not self.stop.is_set():
            try:
                latest = latest_version()
                if version_tuple(latest) > version_tuple(__version__):
                    self.actions.put(lambda: self.offer_update(latest))
            except (OSError, ValueError, KeyError):  # offline or GitHub unreachable: just try again later
                pass
            self.stop.wait(UPDATE_CHECK_SECONDS)

    def offer_update(self, latest):
        self.update_button.configure(text=f"↑ Update auf v{latest}", fg=self.accent())
        self.update_button.bind("<Leave>", lambda _: self.update_button.configure(bg=self.BG, fg=self.accent()))
        self.update_button.pack(side="right", before=self.version)

    def run_update(self):
        self.update_button.configure(text="Lädt Update…")

        def work():
            try:
                install_update(plugin_dir=signalrgb_plugin_dir())
                self.actions.put(self.restart)
            except (OSError, ValueError, SyntaxError) as error:
                self.actions.put(lambda: self.update_button.configure(text=f"Update fehlgeschlagen: {error}"[:60]))

        threading.Thread(target=work, daemon=True).start()

    def restart(self):
        """Start the new version; it waits until this one has quit and released the keyboard."""
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([pythonw, os.path.abspath(__file__), "--restart"], cwd=APP_DIR)
        self.close()

    def worker(self):
        while not self.stop.is_set():
            try:
                self.painter.step()
            except Exception:  # an unexpected error must not silently stop the lighting for good
                with open(os.path.join(os.path.dirname(STATE_FILE), "error.log"), "a", encoding="utf8") as log:
                    log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc())
                self.painter.disconnect()
                time.sleep(1)
            time.sleep(0.002)

    def round_corners(self):
        try:  # Windows 11 only; older Windows keeps square corners
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
        except (AttributeError, OSError):
            pass

    # ---- small building blocks -------------------------------------------------------------

    def ghost(self, parent, text, command, bg=None, fg=None):
        """Flat text button that lights up on hover."""
        bg = bg or self.BG
        label = tk.Label(parent, text=text, bg=bg, fg=fg or self.MUTED, font=self.FONT, padx=8, pady=3, cursor="hand2")
        label.bind("<Button-1>", lambda _: command())
        label.bind("<Enter>", lambda _: label.configure(bg=self.LINE, fg=self.FG))
        label.bind("<Leave>", lambda _: label.configure(bg=bg, fg=fg or self.MUTED))
        return label

    def section(self, text):
        tk.Label(self.controls, text=text.upper(), bg=self.BG, fg=self.MUTED, font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x", pady=(10, 4))

    def accent(self):
        """UI highlights follow the chosen key colour, unless it is too dark or too pale to read."""
        r, g, b = rgb(self.state["color"])
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return self.state["color"] if s > .35 and v > .45 else self.ACCENT

    @staticmethod
    def mix(color, other, t):
        """Blend two #rrggbb colours, t=0 gives color, t=1 gives other."""
        a, b = rgb(color), rgb(other)
        return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))

    @staticmethod
    def round_rect(canvas, x1, y1, x2, y2, r, **options):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return canvas.create_polygon(points, smooth=True, **options)

    # ---- layout ------------------------------------------------------------------------------

    def build(self):
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=(self.PAD, 6), pady=(10, 4))
        self.logo = ImageTk.PhotoImage(tray_image().resize((22, 22), Image.LANCZOS))
        logo = tk.Label(header, image=self.logo, bg=self.BG)
        logo.pack(side="left", padx=(0, 8))
        name = tk.Label(header, text="Varmilo", bg=self.BG, fg=self.FG, font=("Segoe UI Semibold", 11))
        name.pack(side="left")
        model = tk.Label(header, text="Minilo75 Pro", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        model.pack(side="left", padx=(6, 0), pady=(2, 0))
        pill = tk.Frame(header, bg=self.SURFACE, padx=8, pady=2)
        pill.pack(side="left", padx=(12, 0))
        self.dot = tk.Canvas(pill, width=8, height=8, bg=self.SURFACE, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 7, 7, outline="")
        self.dot.pack(side="left", padx=(0, 5))
        self.status = tk.Label(pill, text="", bg=self.SURFACE, fg=self.MUTED, font=("Segoe UI", 8))
        self.status.pack(side="left")
        self.ghost(header, "✕", self.root.withdraw).pack(side="right")  # lighting keeps running, quit via the tray
        self.ghost(header, "—", self.root.withdraw).pack(side="right")
        for widget in (header, logo, name, model, pill, self.status, self.dot):
            widget.bind("<Button-1>", self.drag_start)
            widget.bind("<B1-Motion>", self.drag_move)
            widget.bind("<ButtonRelease-1>", lambda _: self.commit())

        self.body = tk.Frame(self.root, bg=self.BG)
        self.body.pack(fill="x", padx=self.PAD, pady=(0, self.PAD))

        self.controls = tk.Frame(self.body, bg=self.BG)
        self.controls.pack(fill="x")
        self.build_modes()
        self.section("Farbe")
        self.build_swatches()
        self.section("Helligkeit")
        self.build_slider()
        self.section("Profile")
        self.profile_frame = tk.Frame(self.controls, bg=self.BG)
        self.profile_frame.pack(fill="x")
        self.render_profiles()

        self.learn_bar = tk.Frame(self.body, bg=self.BG)
        self.build_learn_bar()

        self.build_board()

        self.footer = footer = tk.Frame(self.body, bg=self.BG)
        footer.pack(fill="x", pady=(10, 0))
        self.switch = tk.Canvas(footer, width=30, height=16, bg=self.BG, highlightthickness=0, cursor="hand2")
        self.switch.pack(side="left")
        self.switch.bind("<Button-1>", lambda _: self.set_autostart(not os.path.exists(STARTUP_FILE)))
        startup = tk.Label(footer, text="Mit Windows starten", bg=self.BG, fg=self.MUTED, font=self.FONT, cursor="hand2")
        startup.pack(side="left", padx=6)
        startup.bind("<Button-1>", lambda _: self.set_autostart(not os.path.exists(STARTUP_FILE)))
        self.ghost(footer, "Tasten einlernen", self.begin_learning).pack(side="right")
        self.update_button = self.ghost(footer, "", self.run_update)  # shown only when a newer release exists
        self.version = tk.Label(footer, text=f"v{__version__}", bg=self.BG, fg=self.LINE, font=("Segoe UI", 8))
        self.version.pack(side="right", padx=6)

    def build_modes(self):
        bar = tk.Frame(self.controls, bg=self.SURFACE, padx=3, pady=3)
        bar.pack(fill="x", pady=(6, 0))
        self.mode_labels = {}
        for text, mode in MODES:
            label = tk.Label(bar, text=text, bg=self.SURFACE, font=self.FONT, pady=5, cursor="hand2")
            label.pack(side="left", expand=True, fill="x")
            label.bind("<Button-1>", lambda _, m=mode: self.set_mode(m))
            label.bind("<Enter>", lambda _, l=label, m=mode: self.state["mode"] != m and l.configure(fg=self.FG))
            label.bind("<Leave>", lambda _: self.refresh())
            self.mode_labels[mode] = label

    def build_swatches(self):
        self.swatch_canvas = tk.Canvas(self.controls, height=26, bg=self.BG, highlightthickness=0)
        self.swatch_canvas.pack(fill="x")
        self.swatch_rings = {}
        for i, color in enumerate(PRESETS + ["custom"]):
            cx, cy = 13 + i * 30, 13
            ring = self.swatch_canvas.create_oval(cx - 12, cy - 12, cx + 12, cy + 12, outline="", width=2)
            if color == "custom":
                dot = self.swatch_canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=self.MUTED, dash=(2, 2))
                plus = self.swatch_canvas.create_text(cx, cy, text="+", fill=self.MUTED, font=("Segoe UI", 10))
                items, command = (dot, plus), self.choose
                self.custom_dot = dot
            else:
                dot = self.swatch_canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill=color, outline="")
                items, command = (dot,), lambda c=color: self.pick(c)
            for item in items:
                self.swatch_canvas.tag_bind(item, "<Button-1>", lambda _, c=command: c())
            self.swatch_canvas.tag_bind(dot, "<Enter>", lambda _: self.swatch_canvas.configure(cursor="hand2"))
            self.swatch_canvas.tag_bind(dot, "<Leave>", lambda _: self.swatch_canvas.configure(cursor=""))
            self.swatch_rings[color] = ring

    def build_slider(self):
        row = tk.Frame(self.controls, bg=self.BG)
        row.pack(fill="x")
        self.slider = tk.Canvas(row, height=18, bg=self.BG, highlightthickness=0, cursor="hand2")
        self.slider.pack(side="left", fill="x", expand=True)
        self.slider_value = tk.Label(row, text="", width=5, bg=self.BG, fg=self.MUTED, font=self.FONT, anchor="e")
        self.slider_value.pack(side="right")
        self.slider.bind("<Configure>", lambda _: self.draw_slider())
        self.slider.bind("<Button-1>", self.slide)
        self.slider.bind("<B1-Motion>", self.slide)
        self.slider.bind("<ButtonRelease-1>", lambda _: (self.commit(), self.mark_profile()))

    def build_learn_bar(self):
        self.learn_title = tk.Label(self.learn_bar, text="", bg=self.BG, fg=self.FG, font=("Segoe UI Semibold", 12), anchor="w")
        self.learn_title.pack(fill="x", pady=(6, 0))
        tk.Label(self.learn_bar, text="Drück die weiß markierte Taste. Geht eine nicht (z. B. Win), überspringen.", bg=self.BG,
                 fg=self.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")
        self.progress = tk.Canvas(self.learn_bar, height=3, bg=self.SURFACE, highlightthickness=0)
        self.progress.pack(fill="x", pady=8)
        self.progress_bar = self.progress.create_rectangle(0, 0, 0, 3, fill=self.ACCENT, outline="")
        buttons = tk.Frame(self.learn_bar, bg=self.BG)
        buttons.pack(fill="x", pady=(0, 6))
        self.ghost(buttons, "Überspringen", self.learn_skip).pack(side="left")
        self.ghost(buttons, "Zurück", self.learn_back).pack(side="left")
        self.ghost(buttons, "Abbrechen", lambda: self.end_learning(False)).pack(side="right")

    def build_board(self):
        unit, gap, pad = KEY_UNIT, 3, 8
        height = int(len(ROWS) * unit + unit * .25)
        self.board = tk.Canvas(self.body, width=16 * unit + 2 * pad, height=height + 2 * pad, bg=self.BG, highlightthickness=0)
        self.board.pack(pady=(14, 0))
        self.round_rect(self.board, 0, 0, 16 * unit + 2 * pad, height + 2 * pad, 12, fill=self.PLATE, outline=self.LINE)
        self.key_items = {}
        y = pad
        for r, row in enumerate(ROWS):
            x = 0
            for key in row:
                if isinstance(key, (int, float)):
                    x += key
                    continue
                x1, x2 = pad + x * unit + gap / 2, pad + (x + key[1]) * unit - gap / 2
                body = self.board.create_rectangle(x1, y + gap / 2, x2, y + unit - gap / 2, fill=self.KEY_OFF, outline="", width=1)
                text = self.board.create_text((x1 + x2) / 2, y + unit / 2, text=TEXT.get(key[0], key[0]), font=("Segoe UI", 7))
                for item in (body, text):
                    self.board.tag_bind(item, "<Button-1>", lambda _, k=key[0]: self.paint_key(k))
                    self.board.tag_bind(item, "<Enter>", lambda _, k=key[0]: self.draw_key(k, hover=True))
                    self.board.tag_bind(item, "<Leave>", lambda _, k=key[0]: self.draw_key(k))
                self.key_items[key[0]] = (body, text)
                x += key[1]
            y += unit * (1.25 if r == 0 else 1)
        self.board.configure(cursor="hand2")

    # ---- interaction -------------------------------------------------------------------------

    def drag_start(self, event):
        self.offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag_move(self, event):
        self.state["x"], self.state["y"] = event.x_root - self.offset[0], event.y_root - self.offset[1]
        self.root.geometry(f"+{self.state['x']}+{self.state['y']}")

    def commit(self):
        try:
            save_state(self.state)
        except OSError as error:
            self.status.configure(text=f"Speichern fehlgeschlagen: {error}")

    def set_mode(self, mode):
        self.state["mode"] = mode
        self.commit()
        self.refresh()

    def pick(self, color):
        self.state["color"] = color
        if self.state["mode"] in ("rainbow", *HANDS_OFF):
            self.state["mode"] = "design"
        self.commit()
        self.refresh()

    def dialog(self, ask, *args, **options):
        self.root.attributes("-topmost", False)  # otherwise the dialog can open behind the overlay
        try:
            return ask(*args, parent=self.root, **options)
        finally:
            self.root.attributes("-topmost", True)

    def choose(self):
        color = self.dialog(colorchooser.askcolor, color=self.state["color"])[1]
        if color:
            self.pick(color)

    # ---- profiles ----------------------------------------------------------------------------

    def render_profiles(self):
        for child in self.profile_frame.winfo_children():
            child.destroy()
        self.profile_chips = {}
        names = list(self.state["profiles"])
        for i, name in enumerate(names + [None]):
            if name is None:
                chip = self.ghost(self.profile_frame, "+ Speichern", self.save_profile)
            else:
                chip = tk.Label(self.profile_frame, text=name, bg=self.SURFACE, fg=self.FG, font=self.FONT, padx=10, pady=3, cursor="hand2")
                chip.bind("<Button-1>", lambda _, n=name: self.load_profile(n))
                chip.bind("<Button-3>", lambda e, n=name: self.profile_menu(e, n))
                self.profile_chips[name] = chip
            chip.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 6), pady=(0, 6))
        self.mark_profile()

    def mark_profile(self):
        current = snapshot(self.state)
        for name, chip in self.profile_chips.items():
            active = self.state["profiles"][name] == current
            chip.configure(bg=self.RAISED if active else self.SURFACE, fg=self.accent() if active else self.FG)

    def profile_menu(self, event, name):
        menu = tk.Menu(self.root, tearoff=0, bg=self.SURFACE, fg=self.FG, activebackground=self.ACCENT, activeforeground="#ffffff", bd=0)
        menu.add_command(label="Mit aktuellen Farben überschreiben", command=lambda: self.store_profile(name))
        menu.add_command(label="Umbenennen", command=lambda: self.rename_profile(name))
        menu.add_command(label="Löschen", command=lambda: self.delete_profile(name))
        menu.tk_popup(event.x_root, event.y_root)

    def ask_name(self, title, initial=""):
        name = self.dialog(simpledialog.askstring, title, "Name:", initialvalue=initial)
        return (name or "").strip()[:24]

    def save_profile(self):
        name = self.ask_name("Profil speichern", f"Profil {len(self.state['profiles']) + 1}")
        if name:
            self.store_profile(name)

    def store_profile(self, name):
        self.state["profiles"][name] = snapshot(self.state)
        self.commit()
        self.render_profiles()

    def load_profile(self, name):
        apply_profile(self.state, self.state["profiles"][name])
        self.commit()
        self.refresh()

    def rename_profile(self, name):
        new = self.ask_name("Profil umbenennen", name)
        if new in self.state["profiles"]:
            self.status.configure(text=f"„{new}“ gibt es schon")
        elif new:
            self.state["profiles"] = {new if k == name else k: v for k, v in self.state["profiles"].items()}  # keeps the order
            self.commit()
            self.render_profiles()

    def delete_profile(self, name):
        del self.state["profiles"][name]
        self.commit()
        self.render_profiles()

    def slide(self, event):
        width = max(self.slider.winfo_width() - 16, 1)
        self.state["brightness"] = max(0, min(100, round((event.x - 8) / width * 100)))
        self.draw_slider()

    def draw_slider(self):
        c, value = self.slider, self.state["brightness"]
        c.delete("all")
        left, right, mid = 8, max(c.winfo_width() - 8, 9), 9
        knob = left + (right - left) * value / 100
        c.create_line(left, mid, right, mid, fill=self.LINE, width=4, capstyle="round")
        c.create_line(left, mid, knob, mid, fill=self.accent(), width=4, capstyle="round")
        c.create_oval(knob - 7, mid - 7, knob + 7, mid + 7, fill=self.FG, outline="")
        self.slider_value.configure(text=f"{value} %")

    def paint_key(self, key):
        if self.learn_index is not None:
            return
        if self.state["mode"] != "keys":
            # start from what is on the keyboard now, so the first click changes only this key
            if self.state["mode"] in ("solid", "design"):
                self.state["keys"] = {k: base_color(self.state, k) for k in KEY_IDS}
            self.state["mode"] = "keys"
        self.state["keys"][key] = self.state["color"]
        self.commit()
        self.refresh()

    def begin_learning(self):
        self.learned = {}
        self.learn_index = 0
        while not self.painter.hits.empty():
            self.painter.hits.get()
        self.painter.start_learning()
        self.controls.pack_forget()
        self.learn_bar.pack(fill="x", before=self.board)
        self.footer.pack_forget()
        self.root.focus_force()  # typed keys land here instead of in another program
        self.refresh()

    def learn_skip(self):
        self.learn_index += 1
        self.after_learn_step()

    def learn_back(self):
        if self.learn_index > 0:
            self.learn_index -= 1
            led = self.learned.pop(KEY_IDS[self.learn_index], None)
            self.painter.seen.discard(led)
            self.refresh()

    def after_learn_step(self):
        if self.learn_index >= len(KEY_IDS):
            self.end_learning(True)
        else:
            self.refresh()

    def end_learning(self, keep):
        if keep:
            self.state["map"] = dict(self.learned)
            self.commit()
        self.learn_index = None
        self.painter.stop_learning()
        self.learn_bar.pack_forget()
        self.controls.pack(fill="x", before=self.board)
        self.footer.pack(fill="x", pady=(10, 0))
        self.refresh()

    # ---- drawing state -----------------------------------------------------------------------

    def key_fill(self, key):
        if self.learn_index is not None:
            return "#ffffff" if key == KEY_IDS[self.learn_index] else "#1e6b3f" if key in self.learned else self.KEY_OFF
        mode = self.state["mode"]
        if mode in HANDS_OFF:
            return self.KEY_OFF
        if mode == "rainbow":
            r, g, b = colorsys.hsv_to_rgb(KEY_IDS.index(key) / len(KEY_IDS), .75, .95)
            return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
        color = base_color(self.state, key)
        return self.KEY_OFF if color == "#000000" else color

    def draw_key(self, key, hover=False):
        """A dark keycap lit from below: tinted cap, glowing edge and legend in the LED colour."""
        body, text = self.key_items[key]
        color = self.key_fill(key)
        if color == self.KEY_OFF:
            cap, edge, legend = self.KEY_OFF, self.KEY_OFF, self.MUTED
        elif self.learn_index is not None and color == "#ffffff":
            cap, edge, legend = "#ffffff", "#ffffff", "#11161c"
        else:
            cap, edge, legend = self.mix(color, self.KEY_OFF, .72), self.mix(color, self.KEY_OFF, .15), self.mix(color, "#ffffff", .45)
        self.board.itemconfigure(body, fill=cap, outline=self.FG if hover else edge, width=2 if hover else 1)
        self.board.itemconfigure(text, fill=legend)

    def refresh(self):
        s = self.state
        for mode, label in self.mode_labels.items():
            active = s["mode"] == mode
            label.configure(bg=self.RAISED if active else self.SURFACE, fg=self.FG if active else self.MUTED,
                            font=self.FONT_BOLD if active else self.FONT)
        custom = s["color"] not in PRESETS
        for color, ring in self.swatch_rings.items():
            selected = s["color"] == color or (color == "custom" and custom)
            self.swatch_canvas.itemconfigure(ring, outline=self.FG if selected else "")
        self.swatch_canvas.itemconfigure(self.custom_dot, fill=s["color"] if custom else "", dash=() if custom else (2, 2))
        self.draw_slider()
        self.mark_profile()
        if self.learn_index is not None:
            current = KEY_IDS[self.learn_index]
            self.learn_title.configure(text=f"Drück jetzt:  {TEXT.get(current, current) or 'Leertaste'}")
            width = int(self.board.cget("width"))  # the bar is not laid out yet on the first draw
            self.progress.coords(self.progress_bar, 0, 0, width * self.learn_index / len(KEY_IDS), 3)
        for key in self.key_items:
            self.draw_key(key)
        on = os.path.exists(STARTUP_FILE)
        self.switch.delete("all")
        self.round_rect(self.switch, 1, 1, 29, 15, 7, fill=self.accent() if on else self.LINE)
        knob = 21 if on else 9
        self.switch.create_oval(knob - 5, 3, knob + 5, 13, fill="#ffffff", outline="")

    def poll(self):
        connected = self.painter.connected
        self.dot.itemconfigure(self.dot_id, fill=self.GOOD if connected else self.MUTED)
        self.status.configure(text="verbunden" if connected else "USB-Kabel nicht gefunden")
        while not self.painter.hits.empty():
            led = self.painter.hits.get()
            if self.learn_index is not None:
                self.learned[KEY_IDS[self.learn_index]] = led
                self.learn_index += 1
                self.after_learn_step()
        while not self.actions.empty():
            self.actions.get()()
        if not self.stop.is_set():
            self.root.after(40, self.poll)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def set_autostart(self, on):
        try:
            if os.path.exists(LEGACY_STARTUP_FILE):
                os.remove(LEGACY_STARTUP_FILE)
            if on:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                with open(STARTUP_FILE, "w", encoding="utf-16") as f:  # UTF-16 so a user folder with umlauts survives
                    f.write(f'CreateObject("WScript.Shell").Run """{pythonw}"" ""{os.path.abspath(__file__)}"" --hidden", 0, False\n')
            elif os.path.exists(STARTUP_FILE):
                os.remove(STARTUP_FILE)
        except OSError as error:
            self.status.configure(text=f"Autostart fehlgeschlagen: {error}")
        self.refresh()

    def close(self):
        self.commit()
        self.stop.set()
        time.sleep(0.15)  # let the worker finish its frame, the firmware drops overlapping commands
        self.painter.shutdown()
        self.tray.stop()
        self.root.destroy()


def tray_image():
    """Small keyboard in the design colours, drawn so no image file has to ship."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 12, 62, 52), 9, fill="#1f2730")
    for row, y in enumerate((18, 28)):
        for col in range(6):
            x = 8 + col * 8.5
            draw.rounded_rectangle((x, y, x + 6, y + 6), 1.5, fill=TAUPE if (row, col) == (0, 0) else CREAM)
    draw.rounded_rectangle((8, 38, 14, 44), 1.5, fill=CREAM)
    draw.rounded_rectangle((16.5, 38, 47.5, 44), 1.5, fill=TAUPE)
    draw.rounded_rectangle((50, 38, 56, 44), 1.5, fill=CREAM)
    return image


# ---- updates ---------------------------------------------------------------------------------
# Settings and profiles live in %APPDATA%\VarmiloOverlay, apart from the program files, so an update
# replaces only the files below and never touches them.
REPO = "laurensanp/varmilo-rgb"
APP_FILES = ("varmilo.py", "varmilo_overlay.py", "Varmilo_Minilo75_Pro.js")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_CHECK_SECONDS = 6 * 3600


def version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def latest_version():
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10) as response:
        return json.load(response)["tag_name"].lstrip("v")


def download(name):
    with urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest/download/{name}", timeout=30) as response:
        return response.read()


def can_self_update():
    """A git checkout is someone's source code; only installed copies replace themselves."""
    return not os.path.isdir(os.path.join(APP_DIR, ".git"))


def signalrgb_plugin_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\WhirlwindFX\SignalRgb") as key:
            return os.path.join(winreg.QueryValueEx(key, "UserDirectory")[0], "Plugins")
    except OSError:
        return None


def install_update(fetch=download, folder=APP_DIR, plugin_dir=None):
    files = {name: fetch(name) for name in APP_FILES}  # everything is downloaded before anything is replaced
    for name, content in files.items():
        if name.endswith(".py"):
            compile(content, name, "exec")  # a broken download must never replace a working program
    targets = [(os.path.join(folder, name), content) for name, content in files.items()]
    plugin = os.path.join(plugin_dir, "Varmilo_Minilo75_Pro.js") if plugin_dir else None
    if plugin and os.path.exists(plugin):  # only refresh a plugin the user installed
        targets.append((plugin, files["Varmilo_Minilo75_Pro.js"]))
    for path, content in targets:
        with open(path + ".download", "wb") as f:
            f.write(content)
        os.replace(path + ".download", path)


def already_running(wait=0):
    """A second copy would fight the first one over the LEDs. After an update, wait for the old one to quit."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    end = time.monotonic() + wait
    while True:
        already_running.mutex = kernel32.CreateMutexW(None, False, "Local\\VarmiloOverlay")  # held for the process lifetime
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return False
        kernel32.CloseHandle(already_running.mutex)
        if time.monotonic() >= end:
            return True
        time.sleep(0.3)


def wait_for_taskbar(seconds=90):
    """At logon the Startup folder can run before the taskbar exists, and the tray icon would be lost."""
    import ctypes
    end = time.monotonic() + seconds
    while not ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None) and time.monotonic() < end:
        time.sleep(1)


if __name__ == "__main__":
    try:
        if not already_running(wait=15 if "--restart" in sys.argv else 0):
            if "--hidden" in sys.argv:
                wait_for_taskbar()
            Overlay(hidden="--hidden" in sys.argv).root.mainloop()
    except Exception:  # pythonw has no console, so a start-up crash would otherwise leave no trace
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(os.path.join(os.path.dirname(STATE_FILE), "error.log"), "a", encoding="utf8") as log:
            log.write(time.strftime("%Y-%m-%d %H:%M:%S start: ") + traceback.format_exc())
        raise
