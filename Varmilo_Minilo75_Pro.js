// SignalRGB plugin for the Varmilo Minilo75 Pro over USB cable (062A:856B).
// Uses only the RAM-based direct mode: 0x91 enter, 0x92 fill, 0x93 one LED, 0x90 leave.
// Never send 02/04/05/07 (flash writes on the related K596) or feature report 0x06 (firmware unlock).
export function Name() { return "Varmilo Minilo75 Pro"; }
export function VendorId() { return 0x062a; }
export function ProductId() { return 0x856b; }
export function Publisher() { return "Community"; }
export function Type() { return "hid"; }
export function DeviceType() { return "keyboard"; }
export function Size() { return [16, 6]; }
export function DefaultPosition() { return [10, 100]; }
export function DefaultScale() { return 8.0; }
/* global
shutdownMode:readonly
shutdownColor:readonly
LightingMode:readonly
forcedColor:readonly
*/
export function ControllableParameters() {
	return [
		{ property: "shutdownMode", group: "lighting", label: "Shutdown Mode", type: "combobox", values: ["Hardware", "Color"], default: "Hardware" },
		{ property: "shutdownColor", group: "lighting", label: "Shutdown Color", min: "0", max: "360", type: "color", default: "#000000" },
		{ property: "LightingMode", group: "lighting", label: "Lighting Mode", type: "combobox", values: ["Auto", "Canvas", "Forced", "Extern"], default: "Auto" },
		{ property: "forcedColor", group: "lighting", label: "Forced Color", min: "0", max: "360", type: "color", default: "#009bde" },
	];
}

// [name, column, row, LED index]. Learned from the keyboard: a pressed key's LED turns white.
const KEYS = [
	["Esc", 0, 0, 77], ["F1", 1, 0, 68], ["F2", 2, 0, 79], ["F3", 3, 0, 69], ["F4", 4, 0, 80], ["F5", 6, 0, 70], ["F6", 7, 0, 81], ["F7", 8, 0, 61], ["F8", 9, 0, 72], ["F9", 10, 0, 83], ["F10", 11, 0, 85], ["F11", 12, 0, 64], ["F12", 13, 0, 75], ["Del", 15, 0, 20],
	["`", 0, 1, 66], ["1", 1, 1, 67], ["2", 2, 1, 78], ["3", 3, 1, 57], ["4", 4, 1, 47], ["5", 5, 1, 58], ["6", 6, 1, 48], ["7", 7, 1, 59], ["8", 8, 1, 71], ["9", 9, 1, 82], ["0", 10, 1, 50], ["-", 11, 1, 73], ["=", 12, 1, 84], ["Backspace", 14, 1, 74], ["Insert", 15, 1, 42],
	["Tab", 0, 2, 55], ["Q", 2, 2, 45], ["W", 3, 2, 56], ["E", 4, 2, 46], ["R", 5, 2, 25], ["T", 6, 2, 36], ["Y", 7, 2, 37], ["U", 8, 2, 49], ["I", 9, 2, 60], ["O", 10, 2, 39], ["P", 11, 2, 51], ["[", 12, 2, 62], ["]", 13, 2, 52], ["\\", 14, 2, 63], ["Page Up", 15, 2, 54],
	["CapsLock", 0, 3, 44], ["A", 2, 3, 23], ["S", 3, 3, 34], ["D", 4, 3, 24], ["F", 5, 3, 35], ["G", 6, 3, 14], ["H", 7, 3, 26], ["J", 8, 3, 27], ["K", 9, 3, 38], ["L", 10, 3, 28], [";", 11, 3, 29], ["'", 12, 3, 40], ["Enter", 13, 3, 41], ["Page Down", 15, 3, 43],
	["Left Shift", 1, 4, 33], ["Z", 2, 4, 1], ["X", 3, 4, 12], ["C", 4, 4, 2], ["V", 5, 4, 13], ["B", 6, 4, 3], ["N", 7, 4, 15], ["M", 8, 4, 5], [",", 9, 4, 16], [".", 10, 4, 17], ["/", 11, 4, 18], ["Right Shift", 13, 4, 30], ["Up Arrow", 14, 4, 32], ["Fn", 15, 4, 7],
	["Left Ctrl", 0, 5, 0], ["Left Win", 1, 5, 11], ["Left Alt", 3, 5, 22], ["Space", 6, 5, 4], ["Right Alt", 10, 5, 6], ["Right Ctrl", 11, 5, 19], ["Left Arrow", 13, 5, 9], ["Down Arrow", 14, 5, 10], ["Right Arrow", 15, 5, 21],
];
const NAMES = KEYS.map((k) => k[0]);
const POSITIONS = KEYS.map((k) => [k[1], k[2]]);

export function LedNames() { return NAMES; }
export function LedPositions() { return POSITIONS; }

// Measured: the firmware takes one LED command per ~7 ms, drops anything faster and sets one LED per
// packet, so ~140 updates/s is the ceiling. Each frame spends that budget where it is most visible.
const FRAME_BUDGET_MS = 40;
const LEDS_PER_FRAME = 5;
const MIN_DELTA = 8; // smaller steps are invisible on the LEDs and would waste the budget
let sent = KEYS.map(() => [0, 0, 0]); // what the keyboard shows, re-read every frame

export function Validate(endpoint) {
	return endpoint.interface === 1 && endpoint.usage_page === 0xff19 && endpoint.collection === 0x0004;
}

function cmd(...payload) {
	const packet = new Array(64).fill(0);
	packet[0] = 0x08;
	payload.forEach((byte, i) => { packet[i + 1] = byte; });

	// The firmware drops commands sent too early, so wait for each echo.
	device.write(packet, 64);
	for (let attempt = 0; attempt < 4; attempt++) {
		const reply = device.read([0x08], 64, 10);
		if (reply && reply[0] === 0x08 && reply[1] === payload[0]) { return true; }
	}

	return false;
}

function fill(rgb) {
	if (!cmd(0x92, 0, 0, 0, rgb[0], rgb[1], rgb[2])) { return false; }
	sent = KEYS.map(() => rgb);

	return true;
}

function hexToRgb(hex) {
	return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

function distance(a, b) {
	return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]);
}

// "Extern" always leaves the keyboard to the overlay program. "Auto" leaves it only while the overlay
// is painting: the overlay keeps a marker on LED slot 86 (no key is wired to it) with fixed red/blue and
// a green heartbeat. The overlay counts as gone after two checks in a row without a new heartbeat
// (closed, killed, or set to SignalRGB); one missed check, e.g. while it re-enters direct mode, is fine.
const MARKER_LED = 86;
const CHECK_EVERY_FRAMES = 10;
const STALE_CHECKS = 2;
let yielded = false;
let frame = 0;
let lastBeat = -1;
let misses = STALE_CHECKS;

function readFrame() {
	const report = device.get_report([0x13], 284); // [report id, R x88, G x88, B x88, ...]

	return report && report.length >= 265 ? report : null;
}

function pixel(report, led) {
	return [report[1 + led], report[89 + led], report[177 + led]];
}

function overlayActive(report) {
	const [r, g, b] = report ? pixel(report, MARKER_LED) : [0, 0, 0];
	const marker = r === 0x56 && b === 0x4f && g > 0;
	const beating = marker && g !== lastBeat;

	if (marker) { lastBeat = g; }
	misses = beating ? 0 : misses + 1;

	return misses < STALE_CHECKS;
}

function decide(report) {
	if (LightingMode === "Extern") { yielded = true; return; }
	const shouldYield = LightingMode === "Auto" && overlayActive(report);

	if (yielded && !shouldYield) { takeOver(); }
	yielded = shouldYield;
}

export function Initialize() {
	device.set_endpoint(1, 0xff19, 0xff19, 0x0004);
	yielded = true; // so decide() takes the keyboard over unless the overlay holds it
	misses = STALE_CHECKS;
	decide(readFrame());
}

function takeOver() {
	cmd(0x91);
	fill([0, 0, 0]);
}

export function onLightingModeChanged() {
	decide(readFrame());
}

function setLed(i, rgb) {
	const led = KEYS[i][3];

	if (cmd(0x93, Math.floor(led / 11), led % 11, 0, rgb[0], rgb[1], rgb[2])) { sent[i] = rgb; }
}

export function Render() {
	const report = readFrame();

	frame = (frame + 1) % CHECK_EVERY_FRAMES;
	if (frame === 0 || LightingMode === "Extern") { decide(report); }
	if (yielded) { return; }
	// Trust the keyboard over our memory: a key the firmware turned white on press, or a dropped
	// command, now shows up as a big difference and gets fixed first, in this very frame.
	if (report) { KEYS.forEach((key, i) => { sent[i] = pixel(report, key[3]); }); }
	const target = KEYS.map((key) => (LightingMode === "Forced" ? hexToRgb(forcedColor) : device.color(key[1], key[2])));

	sendChanges(target);
}

function changes(target) {
	return target
		.map((rgb, i) => ({ i, rgb, delta: distance(rgb, sent[i]) }))
		.filter((entry) => entry.delta > MIN_DELTA)
		.sort((a, b) => b.delta - a.delta);
}

function sendChanges(target) {
	let changed = changes(target);

	if (!changed.length) { return; }
	// One fill command moves every LED at once. Use it when that helps more than the few single LEDs a
	// frame allows, e.g. when the whole board shifts colour; the single LEDs then fix what differs.
	const avg = [0, 1, 2].map((c) => Math.round(target.reduce((sum, rgb) => sum + rgb[c], 0) / target.length));
	const fillGain = target.reduce((sum, rgb, i) => sum + distance(rgb, sent[i]) - distance(rgb, avg), 0);
	const ledGain = changed.slice(0, LEDS_PER_FRAME).reduce((sum, entry) => sum + entry.delta, 0);

	if (fillGain > ledGain && fill(avg)) { changed = changes(target); }
	const deadline = Date.now() + FRAME_BUDGET_MS;

	for (const { i, rgb } of changed) {
		if (Date.now() >= deadline) { break; }
		setLed(i, rgb);
	}
}

export function Shutdown(SystemSuspending) {
	if (yielded) { return; }
	if (SystemSuspending) {
		fill([0, 0, 0]);
	} else if (shutdownMode === "Color") {
		fill(hexToRgb(shutdownColor));
	} else {
		cmd(0x90);
	}
}
