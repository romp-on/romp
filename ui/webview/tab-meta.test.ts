// Live tab label/color/tag application from the kernel's recurring tabOrder push (the user
// 2026-08-24): a headless `romp rename` / `romp color` / `romp tag` used to update kernel state —
// and the timeline — while the CHAT strip held the old label/color until reload, because the pushed
// per-tab meta was applied only to placeholder tabs, never to existing sessions, and per-session
// frames ride a build cache whose sig (transcript+states) a rename/recolor never busts. The pure
// sync lives in tab-meta.ts (executable below); the render.ts wiring is pinned at the source
// (render.ts has no jsdom harness — the apierror-retry-now.test.ts idiom). Synthetic names only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { syncSessionsFromTabMeta, applyMetaToSession, notePendingMeta, PENDING_META_MAX_AGE,
         TabSessionMeta, PendingTabMeta } from "./tab-meta";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const sess = (name: string, bg?: string): TabSessionMeta =>
  ({ name, color: bg ? { bg, fg: "#ffffff" } : null });

test("a pushed rename and recolor land on the existing session — the strip follows the push, not a reload", () => {
  const s = sess("web", "#336699");
  const store = new Map([["S1", s]]);
  const changed = syncSessionsFromTabMeta(
    [{ id: "S1", name: "api", color: { bg: "#aa3366", fg: "#ffffff" } }],
    (id) => store.get(id), new Map());
  assert.equal(changed, true, "the caller learns it must repaint");
  assert.equal(s.name, "api", "the pushed rename lands");
  assert.deepEqual(s.color, { bg: "#aa3366", fg: "#ffffff" }, "the pushed recolor lands");
});

test("an unchanged push reports no visible change, and junk entries are ignored", () => {
  const s = sess("web", "#336699");
  const store = new Map([["S1", s]]);
  assert.equal(syncSessionsFromTabMeta(
    [{ id: "S1", name: "web", color: { bg: "#336699", fg: "#ffffff" } },
     { id: 42 as any, name: "junk" }, null as any, { name: "no-id" }],
    (id) => store.get(id), new Map()), false);
  assert.equal(s.name, "web");
});

test("an empty pushed name or malformed color never wipes what the session has", () => {
  const s = sess("web", "#336699");
  assert.equal(applyMetaToSession(s, { name: "", color: { bg: 7 } }), false);
  assert.equal(s.name, "web");
  assert.deepEqual(s.color, { bg: "#336699", fg: "#ffffff" });
});

test("a pending optimistic edit holds a stale in-flight push, and the kernel echo clears it", () => {
  const s = sess("web", "#336699");
  const store = new Map([["S1", s]]);
  const pending = new Map<string, PendingTabMeta>();
  notePendingMeta(pending, "S1", { colorBg: "#AA3366" });        // the swatch click, case differs on purpose
  s.color = { bg: "#AA3366", fg: "#ffffff" };                    // …applied optimistically by the caller
  // a push BUILT BEFORE the kernel processed the recolor still carries the old color → held
  syncSessionsFromTabMeta([{ id: "S1", name: "web", color: { bg: "#336699", fg: "#ffffff" } }],
    (id) => store.get(id), pending);
  assert.equal(s.color!.bg, "#AA3366", "the stale push cannot revert the optimistic swatch");
  assert.ok(pending.has("S1"), "the expectation stands until echoed");
  // the echo (case-insensitive on the hex) adopts and clears
  syncSessionsFromTabMeta([{ id: "S1", name: "web", color: { bg: "#aa3366", fg: "#ffffff" } }],
    (id) => store.get(id), pending);
  assert.equal(pending.has("S1"), false, "the echo retires the pending edit");
});

test("an unechoed pending edit yields to the kernel after " + PENDING_META_MAX_AGE + " pushes — the store of record wins", () => {
  const s = sess("web");
  const store = new Map([["S1", s]]);
  const pending = new Map<string, PendingTabMeta>();
  notePendingMeta(pending, "S1", { name: "api" });
  for (let i = 0; i < PENDING_META_MAX_AGE; i++)
    syncSessionsFromTabMeta([{ id: "S1", name: "tests" }], (id) => store.get(id), pending);
  assert.equal(pending.has("S1"), false, "the silent-push cap retires it");
  syncSessionsFromTabMeta([{ id: "S1", name: "tests" }], (id) => store.get(id), pending);
  assert.equal(s.name, "tests", "after yielding, the kernel's name lands");
});

test("a pending edit whose tab left the push ages out too", () => {
  const pending = new Map<string, PendingTabMeta>();
  notePendingMeta(pending, "S9", { name: "gone" });
  for (let i = 0; i < PENDING_META_MAX_AGE; i++)
    syncSessionsFromTabMeta([{ id: "S1", name: "web" }], () => undefined, pending);
  assert.equal(pending.size, 0);
});

test("a rename pending guard holds the NAME against a pre-rename push — a pushed recolor still lands (fields are independent)", () => {
  // the renamed confirm applied "api" optimistically; a push BUILT BEFORE the kernel's rename
  // still carries "web" — the guard must keep the confirm's label, never flap back
  const s = sess("api", "#336699");
  const store = new Map([["S1", s]]);
  const pending = new Map<string, PendingTabMeta>();
  notePendingMeta(pending, "S1", { name: "api" });
  syncSessionsFromTabMeta([{ id: "S1", name: "web", color: { bg: "#aa3366", fg: "#ffffff" } }],
    (id) => store.get(id), pending);
  assert.equal(s.name, "api", "the stale pre-rename push cannot revert the confirmed label");
  assert.equal(s.color!.bg, "#aa3366", "the color field is not hostage to the name's guard");
});

// ── render.ts wiring (source pins — no jsdom harness for the monolith) ─────────────────────────────
test("applyTabOrder syncs the pushed meta onto existing sessions inside the tabs branch", () => {
  assert.match(RENDER, /syncSessionsFromTabMeta\(tabs, \(id\) => sessions\.get\(id\), pendingTabMeta\);/);
  // inside the Array.isArray(tabs) rebuild — the same frame that refreshes the placeholders
  const block = (RENDER.match(/if \(Array\.isArray\(tabs\)\) \{[\s\S]*?\n  \}/) || [""])[0];
  assert.ok(block.includes("syncSessionsFromTabMeta"), "the sync rides the tabMeta rebuild");
});

test("a session frame cannot roll the strip back: upsert re-applies the freshest pushed meta", () => {
  assert.match(RENDER, /sessions\.set\(msg\.id, s\);\n[\s\S]{0,400}?const tm = tabMeta\.get\(msg\.id\);\n\s*if \(tm\) applyMetaToSession\(s, tm, pendingTabMeta\.get\(msg\.id\)\);/);
});

test("both optimistic paths note their expectation: the color swatch and the renamed confirm", () => {
  assert.match(RENDER, /notePendingMeta\(pendingTabMeta, id, \{ colorBg: bg \}\);/);
  assert.match(RENDER, /notePendingMeta\(pendingTabMeta, m\.id, \{ name: m\.name \}\);/);
});

test("the tabOrder frame's views land before the strip repaints — a CLI tag edit re-filters the tabs on the same push", () => {
  // captureViews (adopt the pushed views/tags blob) must run BEFORE applyTabOrder (whose renderTabs
  // re-filters via tabInView) in the tabOrder handler — the (c) leg of the live-update fix
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{ captureViews\(m\.views \|\| null\); applyTabOrder\(m\.order, m\.tabs\); \}/);
  assert.match(RENDER, /const inViewIds = ids\.filter\(tabInView\);/);
});
