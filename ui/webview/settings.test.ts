import { test } from "node:test";
import * as assert from "node:assert/strict";

// Minimal localStorage shim BEFORE importing the module (load/save read it at call time).
const store: Record<string, string> = {};
(globalThis as any).localStorage = {
  getItem: (k: string) => (k in store ? store[k] : null),
  setItem: (k: string, v: string) => { store[k] = v; },
  removeItem: (k: string) => { delete store[k]; },
};
import { loadSettings, saveSettings, DEFAULT_SETTINGS } from "./settings";

test("loadSettings returns defaults when nothing is stored", () => {
  delete store["romp:settings"];
  assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
});

test("the Sub-goals card pref defaults ON; the old Explanations pref is gone (the user 2026-06-18)", () => {
  assert.equal(DEFAULT_SETTINGS.subgoals, true);
  assert.equal((DEFAULT_SETTINGS as any).explanations, undefined);
});

test("both judge-set toggles default OFF (the user 2026-06-29): the timeline's judging band stays hidden", () => {
  assert.equal(DEFAULT_SETTINGS.showIndexJudges, false);
  assert.equal(DEFAULT_SETTINGS.showTriageJudges, false);
});

test("Default backend defaults to tmux (the user 2026-08-13, superseding the 07-13 sdk default); both backends coexist", () => {
  // the terminal session is what this user actually works in, and the default is what every create
  // surface reads — the + picker's backend row AND the hive tray's bean drop
  assert.equal(DEFAULT_SETTINGS.backend, "tmux");
});

test("Compact transcript defaults ON (the user 2026-07-14): fresh installs read the tidy transcript", () => {
  assert.equal(DEFAULT_SETTINGS.compact, true);
});

// The settings change signal must cover every way a change can happen: another
// same-origin tab (storage event), THIS document (the gear now lives in the same
// page — same-document writes never fire storage), and another VS Code webview
// (separate origin + localStorage → the host relays {settingsSync}, applied by
// installSettingsSync). The dead compact toggle (the user 2026-07-14) was the
// same-document gap.
test("settings changes propagate same-document and cross-webview, not just cross-tab", () => {
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "settings.ts"), "utf8");
  assert.ok(src.includes('addEventListener("storage"'), "cross-tab: the storage event");
  assert.ok(src.includes('addEventListener("romp:settings"'), "same-document: the gear's save() signal");
  assert.ok(src.includes('"settingsSync"'), "cross-webview: the host-relayed sync applies here");
  const gear = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
  const save = gear.slice(gear.indexOf("function save(s)"), gear.indexOf("cc.addEventListener"));
  assert.ok(save.includes("dispatchEvent(new Event('romp:settings'))"), "save() always raises the same-doc signal");
  assert.ok(save.includes("settingsSync"), "save() always posts the cross-webview sync");
  const ext = fs.readFileSync(path.join(ROOT, "vscode-extension", "src", "extension.ts"), "utf8");
  assert.ok(ext.includes("function broadcastSettings"), "the host fans a gear save out to the other panes");
  const intercepts = ext.match(/m\.type === "settingsSync"/g) || [];
  assert.equal(intercepts.length, 2, "chat AND feed handlers intercept settingsSync (the two gear hosts)");
});

test("the backend pref roundtrips through storage (the gear writes it; createSession reads it fresh)", () => {
  delete store["romp:settings"];
  saveSettings({ backend: "sdk" });
  assert.equal(loadSettings().backend, "sdk");
});

test("saveSettings persists a patch and merges over defaults", () => {
  delete store["romp:settings"];
  const next = saveSettings({ compact: true });
  assert.equal(next.compact, true);
  assert.equal(loadSettings().compact, true, "the change is read back from storage");
});

test("loadSettings tolerates corrupt JSON → defaults", () => {
  store["romp:settings"] = "{not json";
  assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
});

test("an unknown key in storage is ignored, known keys still merge", () => {
  store["romp:settings"] = JSON.stringify({ compact: true, future: 42 });
  const s = loadSettings();
  assert.equal(s.compact, true);
  assert.equal((s as any).future, 42, "merge is shallow — extra keys pass through harmlessly");
});
