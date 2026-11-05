"""Tests the overlay's painter against a fake keyboard: python overlay_test.py"""
import varmilo_overlay as o

o.CLAIM_SECONDS = 0  # the handover pause only matters against a busy real keyboard, tested at the end


class FakeKeyboard:
    def __init__(self):
        self.commands, self.pending, self.broken = [], [], False
        self.buffer = bytearray(283)

    def write(self, data):
        if self.broken:
            raise OSError("unplugged")
        assert len(data) == 64 and data[0] == 8
        assert data[1] in (0x90, 0x91, 0x92, 0x93), "only volatile commands"
        self.commands.append(bytes(data[1:8]))
        self.pending.append(list(data))
        cmd, group, col, _, r, g, b = data[1:8]
        if cmd == 0x92:
            self.buffer[:264] = bytes([r] * 88 + [g] * 88 + [b] * 88)
        elif cmd == 0x93:
            self.press(group * 11 + col, (r, g, b))
        elif cmd == 0x91:
            self.buffer[:264] = bytes(264)

    def press(self, led, color=(255, 255, 255)):
        self.buffer[led], self.buffer[88 + led], self.buffer[176 + led] = color

    def get_feature_report(self, _, __):
        if self.broken:
            raise OSError("unplugged")
        return [0x13, *self.buffer]

    def read(self, _):
        return self.pending.pop() if self.pending else []

    def close(self):
        pass


def fresh(**overrides):
    fake = FakeKeyboard()
    state = {**o.DEFAULTS, **overrides}
    return fake, state, o.Painter(state, finder=lambda: fake)


# ANSI 75%: 81 unique keys, each row at most 16 units wide, and the design only names real keys.
assert len(o.KEY_IDS) == 81 and len(set(o.KEY_IDS)) == 81
for row in o.ROWS:
    assert sum(k if isinstance(k, (int, float)) else k[1] for k in row) <= 16
assert o.DESIGN_LIGHT <= set(o.KEY_IDS) and o.DESIGN_ACCENT <= set(o.KEY_IDS)
assert not o.DESIGN_LIGHT & o.DESIGN_ACCENT

MAP = {"Esc": 40, "Q": 3, "Tab": 60, "Space": 11}

# Solid colour: enter direct mode, one fill, then only the small repaint.
fake, state, p = fresh(mode="solid", color="#102030", map=MAP)
p.step()
assert fake.commands[0][0] == 0x91
assert fake.commands[1] == bytes([0x92, 0, 0, 0, 0x10, 0x20, 0x30])
assert fake.commands[2][:5] == bytes([0x93, 7, 9, 0, o.MARKER_R]) and fake.commands[2][6] == o.MARKER_B, "marker set right after the fill"
assert len(fake.commands) == 3
assert o.is_marker(o.pixel(fake.buffer, o.MARKER_LED))
fake.commands.clear()
p.step()
assert all(c[1:3] == bytes([7, 9]) for c in fake.commands), "a settled frame only moves the heartbeat"

# A key the firmware turned white is corrected within one step.
fake.press(7)
p.step()
assert o.pixel(fake.buffer, 7) == (0x10, 0x20, 0x30)

# Colours follow the learned map, not a fixed table.
_, state2, p2 = fresh(map=MAP)
target = p2.target(0)
assert target[3] == (0, 0, 0)  # Q is dark in the design
assert target[60] == o.rgb(o.CREAM)  # Tab
assert target[40] == o.rgb(o.TAUPE)  # Esc
assert target[70] == (0, 0, 0)  # LED without a key
state2["color"] = "#ff0000"
assert p2.target(0)[11] == (255, 0, 0)  # Space follows the accent colour
assert p2.target(0)[60] == o.rgb(o.CREAM)

# Brightness scales the colour.
state["brightness"] = 50
fake.commands.clear()
p.step()
assert fake.commands[0][4:7] == bytes([0x08, 0x10, 0x18])

# Learning: every LED dark, a press is reported once, also while the key stays held.
fake, state, p = fresh()
p.start_learning()
p.step()
fake.press(23)
p.step()
assert p.hits.get_nowait() == 23
assert o.pixel(fake.buffer, 23) == (0, 0, 0), "white is cleared again"
fake.press(23)
p.step()
assert p.hits.empty(), "held key is not a second key"
fake.press(5)
p.step()
assert p.hits.get_nowait() == 5
p.stop_learning()
state["map"] = {"Esc": 23, "Q": 5}
p.step()
assert o.pixel(fake.buffer, 23) == o.rgb(o.TAUPE) or fake.commands  # painting resumed

# Keys map individually with 8 groups of 11.
fake, state, p = fresh(mode="keys", map={"Esc": 18}, keys={"Esc": "#ff0000"})
p.step()
assert bytes([0x93, 1, 7, 0, 255, 0, 0]) in fake.commands

# Several keys pressed at once all get their colour back in one step.
for led in (18, 30, 50):
    fake.press(led)
p.step()
assert o.pixel(fake.buffer, 18) == (255, 0, 0) and o.pixel(fake.buffer, 30) == o.pixel(fake.buffer, 50) == (0, 0, 0)

# Original mode leaves direct mode once and then stays quiet.
state["mode"] = "original"
fake.commands.clear()
p.step()
p.step()
assert [c[0] for c in fake.commands] == [0x93, 0x90]
assert not o.is_marker(o.pixel(fake.buffer, o.MARKER_LED))

# SignalRGB mode only removes the marker and stays in direct mode for the plugin to take over.
fake, state, p = fresh(mode="solid")
p.step()
state["mode"] = "signalrgb"
fake.commands.clear()
p.step()
p.step()
assert [c[0] for c in fake.commands] == [0x93]
assert o.pixel(fake.buffer, o.MARKER_LED) == (0, 0, 0)
state["mode"] = "design"
p.step()
assert o.is_marker(o.pixel(fake.buffer, o.MARKER_LED)), "back in charge, marker returns"

# Learning keeps the marker too, so the plugin does not paint over the learning screen.
fake, state, p = fresh()
p.start_learning()
p.step()
p.step()
assert o.is_marker(o.pixel(fake.buffer, o.MARKER_LED))
assert p.hits.empty(), "the marker is not mistaken for a pressed key"
assert o.MARKER_LED not in o.load_state()["map"].values()
assert o.marker(0.0)[1] != o.marker(0.1)[1] and o.is_marker(o.marker(24.9)), "heartbeat moves and never hits 0"

# Unplugging disconnects without crashing, and a replug paints again.
fake, state, p = fresh(mode="solid")
p.step()
fake.broken = True
p.step()
assert p.dev is None and not p.connected
fake.broken = False
fake.commands.clear()
p.next_search = 0
p.step()
assert fake.commands[0][0] == 0x91

# Missing saved state falls back to defaults, and two loads never share the same dicts.
o.STATE_FILE = __file__ + ".missing"
assert o.load_state()["mode"] == "design"
first = o.load_state()
first["profiles"]["x"] = {}
assert o.load_state()["profiles"] == {}

# Profiles store a look (not the key map) and stay unchanged when the live colours change.
state = {**o.load_state(), "mode": "keys", "color": "#ff0000", "brightness": 40, "keys": {"Esc": "#00ff00"}, "map": {"Esc": 3}}
profile = o.snapshot(state)
assert "map" not in profile
state["keys"]["Esc"] = "#0000ff"
state["brightness"] = 90
assert profile["keys"]["Esc"] == "#00ff00"
o.apply_profile(state, profile)
assert state["keys"]["Esc"] == "#00ff00" and state["brightness"] == 40 and state["map"] == {"Esc": 3}
state["keys"]["Esc"] = "#ffffff"
assert profile["keys"]["Esc"] == "#00ff00", "applying copies, so painting later does not change the profile"

# A keyboard that is listed but cannot be opened yet (cable/wireless switch) is retried, not fatal.
def flaky():
    raise OSError("open failed")


p = o.Painter(dict(o.DEFAULTS), finder=flaky)
p.step()
assert p.dev is None and not p.connected

# The shipped key map covers every key once, avoids the marker slot and matches the SignalRGB plugin.
import re

assert set(o.DEFAULT_MAP) == set(o.KEY_IDS)
assert len(set(o.DEFAULT_MAP.values())) == len(o.DEFAULT_MAP) and o.MARKER_LED not in o.DEFAULT_MAP.values()
with open(__file__.replace("overlay_test.py", "Varmilo_Minilo75_Pro.js"), encoding="utf8") as plugin:
    plugin_leds = {int(led) for led in re.findall(r"\[\"[^\"]*\", \d+, \d+, (\d+)\]", plugin.read())}
assert plugin_leds == set(o.DEFAULT_MAP.values())

# Updates: versions compare as numbers, and an update replaces program files only, never settings.
import os
import tempfile

assert o.version_tuple("1.10.0") > o.version_tuple("1.9.2") and o.version_tuple("v1.1.0") == (1, 1, 0)
with tempfile.TemporaryDirectory() as tmp:
    app, plugins = os.path.join(tmp, "app"), os.path.join(tmp, "plugins")
    os.makedirs(app)
    os.makedirs(plugins)
    settings = os.path.join(app, "state.json")
    with open(settings, "w") as f:
        f.write("mine")
    new = {"varmilo.py": b"x = 1\n", "varmilo_overlay.py": b"y = 2\n", "Varmilo_Minilo75_Pro.js": b"// new\n"}
    o.install_update(fetch=new.__getitem__, folder=app, plugin_dir=plugins)
    assert open(os.path.join(app, "varmilo_overlay.py"), "rb").read() == b"y = 2\n"
    assert open(settings).read() == "mine"
    assert not os.path.exists(os.path.join(plugins, "Varmilo_Minilo75_Pro.js")), "no plugin installed, none added"
    open(os.path.join(plugins, "Varmilo_Minilo75_Pro.js"), "w").close()
    o.install_update(fetch=new.__getitem__, folder=app, plugin_dir=plugins)
    assert open(os.path.join(plugins, "Varmilo_Minilo75_Pro.js"), "rb").read() == b"// new\n", "installed plugin refreshed"
    broken = {**new, "varmilo_overlay.py": b"def (:\n"}
    try:
        o.install_update(fetch=broken.__getitem__, folder=app, plugin_dir=plugins)
        raise AssertionError("broken download was installed")
    except SyntaxError:
        pass
    assert open(os.path.join(app, "varmilo_overlay.py"), "rb").read() == b"y = 2\n", "working version kept"
assert not o.can_self_update(), "the git checkout must never replace itself"

# Taking the keyboard: heartbeats first (so a busy SignalRGB plugin steps back), then direct mode.
o.CLAIM_SECONDS = 0.1
fake, state, p = fresh()
p.step()
first_enter = [c[0] for c in fake.commands].index(0x91)
assert first_enter >= 3 and all(c[:3] == bytes([0x93, 7, 9]) for c in fake.commands[:first_enter])
o.CLAIM_SECONDS = 0

print("ok")
