#!/usr/bin/env python3
"""RGB control for the Varmilo Minilo75 Pro over USB cable (062A:856B). Needs: pip install hidapi

  varmilo.py fill RRGGBB               all keys one colour
  varmilo.py off                       all keys dark
  varmilo.py key ROW COL RRGGBB ...    only these keys lit, rest dark (firmware's grid, does NOT match key positions)
  varmilo.py rainbow [SECONDS]         animation, restores onboard lighting at the end or on Ctrl+C
  varmilo.py exit                      back to onboard lighting
  varmilo.py map                       print the LED grid
  varmilo.py check                     light every LED, verify by readback, then restore

Colours are not saved: they live in RAM until `exit` or a replug. Nothing here writes flash.
"""
import colorsys
import sys
import time

import hid

VID, PID = 0x062A, 0x856B
LEDS = 88  # measured: report 0x13 holds R at 0, G at 88, B at 176
ROWS, COLS, NO_LED = 6, 15, 0xFE
# Deliberate whitelist. On the related K596, 02/04/05/07 write flash and feature report 0x06
# unlocks firmware updates, so none of those can be reached from this tool.
LEAVE, ENTER, FILL, SET = 0x90, 0x91, 0x92, 0x93
ALLOWED = {LEAVE, ENTER, FILL, SET}


def find_keyboard():
    for d in hid.enumerate(VID, PID):
        if b"col04" in d["path"].lower():
            dev = hid.device()
            dev.open_path(d["path"])
            dev.set_nonblocking(True)
            return dev
    return None


def open_keyboard():
    return find_keyboard() or sys.exit("Keyboard not found. Is it connected by USB cable?")


def cmd(dev, *payload):
    """Send a command and wait for its echo: the firmware drops commands sent too early."""
    if payload[0] not in ALLOWED:
        raise ValueError(f"blocked command {payload[0]:#x}")
    for _ in range(3):
        while dev.read(64):
            pass
        dev.write(bytes([0x08, *payload]).ljust(64, b"\0"))
        end = time.monotonic() + 0.1
        while time.monotonic() < end:
            r = dev.read(64)
            if r and r[0] == 0x08 and r[1] == payload[0]:
                return
            time.sleep(0.0005)
    raise RuntimeError(f"no echo for command {payload[0]:#x}")


def send(dev, *payload):
    """Send without waiting for the echo; the firmware may drop it, so only for repeated messages."""
    if payload[0] not in ALLOWED:
        raise ValueError(f"blocked command {payload[0]:#x}")
    dev.write(bytes([0x08, *payload]).ljust(64, b"\0"))
    while dev.read(64):
        pass


def set_logo(dev, on):
    """Feature report 0x18 holds the indicator lights; its third byte drives the VARMILO logo (0x80 = on).
    Only works in direct mode and is not stored, so callers repeat it."""
    dev.send_feature_report(bytes([0x18, 0, 0, 0x80 if on else 0] + [0] * 16))


def fill(dev, r, g, b):
    cmd(dev, FILL, 0, 0, 0, r, g, b)


def set_led(dev, i, r, g, b):
    cmd(dev, SET, i // 11, i % 11, 0, r, g, b)  # 88 LEDs = 8 groups of 11 (the K596 uses 13)


def frame(dev):
    return bytes(dev.get_feature_report(0x13, 284))[1:]


def grid(dev):
    data = bytes(dev.get_feature_report(0x12, 91))[1:]
    return [list(data[r * COLS:(r + 1) * COLS]) for r in range(ROWS)]


def color(text):
    value = bytes.fromhex(text)
    if len(value) != 3:
        raise ValueError(f"colour must be RRGGBB, got {text}")
    return value


def rainbow(dev, seconds):
    leds = [(c, led) for row in grid(dev) for c, led in enumerate(row) if led != NO_LED]
    cmd(dev, ENTER)
    start = time.monotonic()
    frames = 0
    try:
        while seconds is None or time.monotonic() - start < seconds:
            shift = time.monotonic() / 4
            for c, led in leds:
                rgb = colorsys.hsv_to_rgb((c / COLS + shift) % 1, 1, 1)
                set_led(dev, led, *(int(v * 255) for v in rgb))
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        cmd(dev, LEAVE)
    print(f"{frames / (time.monotonic() - start):.1f} frames per second")


def check(dev):
    used = sorted(led for row in grid(dev) for led in row if led != NO_LED)
    assert len(set(used)) == len(used), "grid lists an LED twice"
    assert used[-1] < LEDS, "grid points outside the frame"
    cmd(dev, ENTER)
    try:
        time.sleep(0.05)
        start = time.monotonic()
        want = {i: (i + 1, 200 - i, (i * 2) % 256) for i in range(LEDS)}  # unique per LED
        for i, rgb in want.items():
            set_led(dev, i, *rgb)
        per_led = (time.monotonic() - start) / LEDS
        got = frame(dev)
        wrong = [i for i, (r, g, b) in want.items() if (got[i], got[LEDS + i], got[2 * LEDS + i]) != (r, g, b)]
    finally:
        cmd(dev, LEAVE)
    print(f"LEDs in grid: {len(used)}, {per_led * 1000:.1f} ms per LED, wrong on readback: {wrong or 'none'}")
    if wrong:
        sys.exit(1)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    action, args = argv[0], argv[1:]
    dev = open_keyboard()
    try:
        if action == "fill":
            cmd(dev, ENTER)
            fill(dev, *color(args[0]))
        elif action == "off":
            cmd(dev, ENTER)
            fill(dev, 0, 0, 0)
        elif action == "key":
            if not args or len(args) % 3:
                sys.exit("usage: key ROW COL RRGGBB [ROW COL RRGGBB ...]")
            layout = grid(dev)
            cmd(dev, ENTER)  # shortcut: ENTER clears the frame, so keys from an earlier call go dark
            for r, c, text in zip(args[::3], args[1::3], args[2::3]):
                led = layout[int(r)][int(c)]
                if led == NO_LED:
                    sys.exit(f"no LED at row {r}, column {c}")
                set_led(dev, led, *color(text))
        elif action == "rainbow":
            rainbow(dev, float(args[0]) if args else None)
        elif action == "exit":
            cmd(dev, LEAVE)
        elif action == "map":
            for row in grid(dev):
                print(" ".join(" ." if led == NO_LED else f"{led:2d}" for led in row))
        elif action == "check":
            check(dev)
        else:
            sys.exit(__doc__)
    finally:
        dev.close()


if __name__ == "__main__":
    main(sys.argv[1:])
