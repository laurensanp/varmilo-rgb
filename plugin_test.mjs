// Runs the plugin against a simulated keyboard, no hardware needed: node plugin_test.mjs
import assert from "node:assert";
import { readFileSync } from "node:fs";

const ALLOWED = [0x90, 0x91, 0x92, 0x93]; // volatile commands only, never flash
const writes = [];
const buffer = new Array(264).fill(0); // the keyboard's frame: R x88, G x88, B x88
const px = (led) => [buffer[led], buffer[88 + led], buffer[176 + led]];
const setPx = (led, rgb) => { [buffer[led], buffer[88 + led], buffer[176 + led]] = rgb; };
let canvas = () => [0, 0, 0];
let overlayOn = false;
let beating = true;
let beat = 1;
const logoReports = [];

globalThis.device = {
	set_endpoint: (...a) => assert.deepStrictEqual(a, [1, 0xff19, 0xff19, 4]),
	write: (p, n) => {
		assert.strictEqual(n, 64);
		assert.strictEqual(p[0], 8);
		assert(ALLOWED.includes(p[1]));
		writes.push(p);
		if (p[1] === 0x91) { buffer.fill(0); }
		if (p[1] === 0x92) { for (let led = 0; led < 88; led++) { setPx(led, p.slice(5, 8)); } }
		if (p[1] === 0x93) { setPx(p[2] * 11 + p[3], p.slice(5, 8)); }
	},
	read: () => writes.at(-1),
	color: (x, y) => canvas(x, y),
	send_report: (data, n) => {
		assert.deepStrictEqual([data[0], n, data.length], [0x18, 20, 20], "only the indicator report, never firmware 0x06");
		logoReports.push(data[3]);
	},
	get_report: (data, n) => {
		assert.deepStrictEqual([data[0], n], [0x13, 284]);
		if (overlayOn) {
			if (beating) { beat = beat % 250 + 1; }
			setPx(86, [0x56, beat, 0x4f]);
		}

		return [0x13, ...buffer, ...new Array(19).fill(0)];
	},
};
Object.assign(globalThis, { LightingMode: "Canvas", forcedColor: "#102030", shutdownMode: "Hardware", shutdownColor: "#000000", logoLight: "On" });

const src = readFileSync(new URL("./Varmilo_Minilo75_Pro.js", import.meta.url), "utf8");
const p = await import("data:text/javascript," + encodeURIComponent(src));
const ESC = 77; // learned LED of Esc, canvas position [0, 0]
const run = (n = 1) => { for (let i = 0; i < n; i++) { p.Render(); } };

// One entry per learned key, each at its own place inside the canvas.
const pos = p.LedPositions();
const KEY_COUNT = 81;
assert.strictEqual(pos.length, KEY_COUNT);
assert.strictEqual(p.LedNames().length, KEY_COUNT);
assert.strictEqual(new Set(pos.map(String)).size, KEY_COUNT);
pos.forEach(([x, y]) => assert(x >= 0 && x < 16 && y >= 0 && y < 6));

assert(p.Validate({ interface: 1, usage_page: 0xff19, collection: 4 }));
assert(!p.Validate({ interface: 1, usage_page: 0xff19, collection: 5 }));

p.Initialize();
assert.deepStrictEqual(writes.map((w) => w[1]), [0x91, 0x92]);
assert.deepStrictEqual(logoReports, [0x80], "logo switched on when taking over");

// The logo is repeated while in charge, and follows the setting.
globalThis.logoLight = "Off";
run(10);
assert.strictEqual(logoReports.at(-1), 0);
globalThis.logoLight = "On";

// A uniform colour is one fill, and a settled frame sends nothing at all.
writes.length = 0;
canvas = () => [10, 20, 130];
run();
assert.deepStrictEqual(writes.map((w) => w.slice(1, 8)), [[0x92, 0, 0, 0, 10, 20, 130]]);
writes.length = 0;
run(5);
assert.strictEqual(writes.length, 0);

// A single differing key gets a single LED command (8 groups of 11).
canvas = (x, y) => (x === 0 && y === 0 ? [255, 0, 0] : [10, 20, 130]);
run();
assert.deepStrictEqual(writes.map((w) => w.slice(1, 8)), [[0x93, 7, 0, 0, 255, 0, 0]]);

// A key the firmware turned white on press is repainted in the very next frame.
writes.length = 0;
setPx(ESC, [255, 255, 255]);
run();
assert.deepStrictEqual(px(ESC), [255, 0, 0]);
assert.strictEqual(writes.length, 1);

// Changes too small to see are skipped.
writes.length = 0;
canvas = (x, y) => (x === 0 && y === 0 ? [255, 0, 0] : [12, 21, 131]);
run();
assert.strictEqual(writes.length, 0);

// A whole-board colour shift starts with one fill, then fixes the keys that differ.
writes.length = 0;
canvas = (x, y) => (x === 0 && y === 0 ? [0, 0, 255] : [200, 100, 0]);
run();
assert.strictEqual(writes[0][1], 0x92);
assert.deepStrictEqual(writes.slice(1).map((w) => w.slice(1, 8)), [[0x93, 7, 0, 0, 0, 0, 255]]);

// A gradient converges and then goes quiet.
canvas = (x, y) => [x * 16, y * 40, 200];
run(100);
writes.length = 0;
run(3);
assert.strictEqual(writes.length, 0);

// Default shutdown returns to onboard lighting.
p.Shutdown(false);
assert.strictEqual(writes.at(-1)[1], 0x90);

// Extern mode never touches the keyboard, and leaving it takes the keyboard back.
writes.length = 0;
globalThis.LightingMode = "Extern";
p.Initialize();
run();
p.Shutdown(false);
p.onLightingModeChanged();
assert.strictEqual(writes.length, 0);
globalThis.LightingMode = "Canvas";
p.onLightingModeChanged();
assert.deepStrictEqual(writes.map((w) => w[1]), [0x91, 0x92]);

// Auto: stands back while the overlay's heartbeat runs, takes over when it stops or disappears.
globalThis.LightingMode = "Auto";
overlayOn = true;
writes.length = 0;
p.Initialize();
const logoBefore = logoReports.length;
run(25);
p.Shutdown(false);
assert.strictEqual(writes.length, 0, "no writes while the overlay paints");
assert.strictEqual(logoReports.length, logoBefore, "the logo is the overlay's business while it paints");
overlayOn = false;
setPx(86, [0, 0, 0]);
run(10);
assert.strictEqual(writes.length, 0, "one missed check is tolerated (overlay re-entering direct mode)");
overlayOn = true;
run(10);
overlayOn = false;
setPx(86, [0, 0, 0]);
run(20);
assert.deepStrictEqual(writes.slice(0, 2).map((w) => w[1]), [0x91, 0x92], "takes over after two missed checks");
canvas = (x, y) => [x * 10, 50, 50];
const afterTakeover = writes.length;
run();
assert(writes.length > afterTakeover, "renders once in charge");
overlayOn = true;
run(10);
writes.length = 0;
run();
assert.strictEqual(writes.length, 0, "yields again when the overlay returns");

// A marker left behind by a killed overlay stops beating; the plugin takes over after a few checks.
beating = false;
run(10 * 3);
assert(writes.some((w) => w[1] === 0x91), "frozen marker is treated as no overlay");
console.log("ok");
