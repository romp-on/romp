// The tray (the user 2026-08-13): config embedded in the board's own drag language. One
// bean per MODEL from the kernel's /models — the ONE list every picker shares, never a
// hardcoded copy — dragged onto a free hexagon to spawn that model there; the badge cycles
// the bean's EFFORT; a clean click makes that bean the default seed for new sessions. And
// dragging an EXISTING session to a free cell re-homes it there. Source-pinned like the
// pane's other interaction contracts: these surfaces only meet at runtime.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the tray builds from /models — the shared list plus the remembered defaults", () => {
  assert.match(HIVE, /fetch\("\/models", \{ cache: "no-store" \}\)/, "choices come from the kernel");
  assert.ok(!/["'](fable|opus|sonnet|haiku)["']/.test(HIVE), "no model literal is hardcoded in the hive");
  assert.ok(KERNEL.includes('_defaults = {k: _sd[k] for k in ("model", "effort") if _sd.get(k)}'),
    "/models now reports the remembered seed, so the tray can mark the default bean");
  assert.ok(KERNEL.includes('_defaults["host"] = _dh'),
    "…and WHERE a drop lands, so the tray spawns on the same machine the + picker does");
});

test("a dropped bean spawns THAT model there: reservation + createSession(model, effort)", () => {
  assert.match(HIVE, /world\.spawnAt\(slot, mc\.value, effort\)/, "the drop hands cell+model+effort over");
  // auto-named, with the bean's choice riding the create op — on the gear's backend rather than a
  // hardcoded "sdk" (the user 2026-08-13), and on the default create host when there is one
  assert.match(HIVE, /type: "createSession", name: this\.autoName\(model\),\s*\n\s*backend: loadSettings\(\)\.backend, model, effort/);
  assert.ok(KERNEL.includes('mdl0 = msg.get("model") if msg.get("model") in _MODEL_VALUES else ""'),
    "the kernel validates the model against the offered choices");
  assert.ok(KERNEL.includes("model=mdl0, effort=eff0)"), "…and hands it to the SDK spawn");
  assert.ok(KERNEL.includes('kwargs={"model": mdl0, "effort": eff0}'),
    "…and to the tmux spawn, so a bean drop means the same thing on either backend");
  assert.ok(SDK.includes("eff = (effort if effort in EFFORT_LEVELS"),
    "spawn(): the explicit choice outranks the remembered seed");
});

test("a clean click sets the DEFAULT seed — kernel-validated, confirmed back, ultracode refused", () => {
  assert.match(HIVE, /\{ type: "setSpawnDefaults", model: mc\.value, effort \}/);
  assert.ok(KERNEL.includes('msg.get("type") == "setSpawnDefaults"'), "the kernel op exists");
  assert.ok(KERNEL.includes("be.set_spawn_defaults(model=mdl, effort=eff)"), "…writing the shared store");
  assert.ok(KERNEL.includes('"type": "spawnDefaults"'), "…and confirming, never silently");
  assert.match(HIVE, /m\.type === "spawnDefaults"/, "the tray re-marks on the confirmation");
  assert.ok(/eff = None\s+# per-session by design/.test(KERNEL),
    "ultracode can ride a single spawn but is refused as a seed");
});

test("dragging a session to a free cell re-homes it: persisted, glided, ghost re-parked", () => {
  assert.match(HIVE, /this\.slots\.set\(sid, slot\);\s*\n\s*saveSlots\(loadSlots\(this\.slots\)\);/,
    "the new home lands in the slot map AND localStorage, so it survives reloads");
  assert.match(HIVE, /pad\.homeTo\(p\.x, p\.z\);/, "the tile GLIDES to the new cell");
  assert.match(HIVE, /this\.group\.position\.x = ease\(this\.group\.position\.x, this\.homeX, dt, 10\);/,
    "…via the eased home, never a teleport");
  assert.match(HIVE, /const slot = cancel \|\| pad\.dyingT >= 0 \? null : this\.freeCellAt\(\);/,
    "only a real drop re-homes — Esc and dying pads spring home");
});

test("the effort badge cycles the /models efforts and persists per bean", () => {
  assert.match(HIVE, /SPAWN_EFFORTS\[\(cur \+ 1\) % Math\.max\(1, SPAWN_EFFORTS\.length\)\]/);
  assert.match(HIVE, /localStorage\.setItem\(effKey, JSON\.stringify\(effSel\)\)/);
});
