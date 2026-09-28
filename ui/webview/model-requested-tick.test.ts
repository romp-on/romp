// The model picker's REQUESTED-model mark (the user 2026-09-17): while a session's live model sits a tier below its
// pick, the picker draws a yellow tick beside the requested model — the family row and its version row — with a
// tooltip saying why the pick is not answering and whether romp is retrying. Source pins over render.ts and styles.css,
// the same style as mode-selector.test.ts, plus the tooltip function executed over the wire shape the kernel sends.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

function fnSource(name: string): string {
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} is defined in render.ts`);
  const j = RENDER.indexOf("\n}\n", i);
  return RENDER.slice(i, j + 3);
}
// the tooltip builder, executed as written (no DOM): the kernel's ModelFallback shape in, one sentence pair out
const requestedModelTip = new Function(fnSource("requestedModelTip").replace(/: ModelFallback\)/, ")").replace(/\): string \{/, ") {") + "\nreturn requestedModelTip;")() as (fb: any) => string;
const requestedModelSub = new Function(fnSource("requestedModelSub").replace(/: ModelFallback\)/, ")").replace(/\): string \{/, ") {") + "\nreturn requestedModelSub;")() as (fb: any) => string;
const isRequestedFamily = new Function(fnSource("isRequestedFamily").replace(/\(fb: ModelFallback, value: string\): boolean/, "(fb, value)") + "\nreturn isRequestedFamily;")() as (fb: any, v: string) => boolean;
const isRequestedVersion = new Function(fnSource("isRequestedVersion").replace(/\(fb: ModelFallback, v: \{ label: string; value: string \}\): boolean/, "(fb, v)") + "\nreturn isRequestedVersion;")() as (fb: any, v: any) => boolean;

const fb = (over: any = {}) => ({ pick: "Fable 5.1", pickValue: "claude-fable-5-1", live: "Opus 5", cause: "safeguards",
  retry: { on: true, everyMin: 10, armed: true, nextIn: 250, attempts: 1 }, ...over });

test("the tooltip says why, then what romp does: the cadence when a retry is armed, the truth when only the switch is on, else where to turn it on", () => {
  assert.equal(requestedModelTip(fb()), "Requested model blocked due to safety classifiers. Retrying every 10 minutes. Next attempt in 5 min.");
  assert.equal(requestedModelTip(fb({ retry: { on: true, everyMin: 10, armed: true, nextIn: null, attempts: 0 } })),
    "Requested model blocked due to safety classifiers. Retrying every 10 minutes.");
  // the switch on with nothing armed (a dormant session, whose next launch carries the pick): never a cadence nothing is running
  assert.equal(requestedModelTip(fb({ retry: { on: true, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })),
    "Requested model blocked due to safety classifiers. Auto-retry is on; the requested model is asked for when the session next starts.");
  assert.equal(requestedModelTip(fb({ retry: { on: false, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })),
    "Requested model blocked due to safety classifiers. Configure auto-retry in Settings, Automation.");
  // the cause is named only once the CLI named it: an unknown cause (a capacity fallback, or the end-of-turn frame not yet in) says so plainly
  assert.equal(requestedModelTip(fb({ cause: "", retry: { on: false, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })),
    "Requested model is not answering; Opus 5 is. Configure auto-retry in Settings, Automation.");
  // the API's refusal category rides along once the CLI named it
  assert.equal(requestedModelTip(fb({ category: "cyber", retry: { on: true, everyMin: 10, armed: true, nextIn: null, attempts: 0 } })),
    "Requested model blocked due to safety classifiers (cyber). Retrying every 10 minutes.");
});

test("the requested row carries a permanent one-line sub-line: the tooltip's gist, never dependent on a hover", () => {
  assert.equal(requestedModelSub(fb()), "requested · blocked by safety classifiers · retrying every 10 min");
  assert.equal(requestedModelSub(fb({ category: "bio" })), "requested · blocked by safety classifiers (bio) · retrying every 10 min");
  assert.equal(requestedModelSub(fb({ cause: "", retry: { on: true, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })), "requested · not answering; Opus 5 is · auto-retry on");
  assert.equal(requestedModelSub(fb({ retry: { on: false, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })), "requested · blocked by safety classifiers · auto-retry off");
  // the family row's explanation is its own sub-line div after the row's version line (2026-09-28: the family row names
  // the version it runs), so the requested row is `requested`, the narrowed fb, in the same vocabulary
  assert.match(RENDER, /const rsub = el\("div", "meta-item-sub"\);\s*\n\s*rsub\.textContent = requestedModelSub\(requested\);/, "the family row's sub-line, the menu vocabulary");
  assert.match(RENDER, /const vsub = el\("div", "meta-item-sub"\);\s*\n\s*vsub\.textContent = requestedModelSub\(fb\);/, "the version row's");
  assert.match(RENDER, /lsub\.textContent = requestedModelSub\(fb\) \+ " — " \+ lsub\.textContent;/, "Latest keeps its own line after the gist");
});

test("the requested row is found by family alias, by pick id and by version label; the answering row never wears it", () => {
  assert.ok(isRequestedFamily(fb(), "fable"));
  assert.ok(!isRequestedFamily(fb(), "opus"), "the family that answers is the blue ✓, not the yellow one");
  assert.ok(isRequestedFamily(fb({ pick: "Fable", pickValue: "fable" }), "fable"), "an alias pick");
  assert.ok(isRequestedVersion(fb(), { label: "Fable 5.1", value: "claude-fable-5-1" }));
  assert.ok(!isRequestedVersion(fb(), { label: "Fable 5", value: "claude-fable-5" }), "the other version of the family is not the pick");
  assert.ok(isRequestedVersion(fb({ pick: "Fable", pickValue: "claude-fable-5-1" }), { label: "Fable 5.1", value: "claude-fable-5-1" }), "by id when the label is the bare family");
});

test("the picker wires the mark on the family row and the version row, never over the current one, with the tooltip as title", () => {
  assert.match(RENDER, /const fb = kind === "model" \? \(s\.status\.modelFallback \|\| null\) : null;/);
  assert.match(RENDER, /const requested = fb && !item\.classList\.contains\("current"\) && isRequestedFamily\(fb, c\.value\) \? fb : null;\s*\n\s*if \(requested\) \{\s*\n\s*item\.classList\.add\("requested"\);\s*\n\s*setTip\(item, requestedModelTip\(requested\)\);/,
    "the one tooltip treatment (tip.ts setTip): hover and focus alike, above the menu");
  assert.match(RENDER, /if \(fb && !cur && isRequestedVersion\(fb, v\)\) \{\s*\n\s*row\.classList\.add\("requested"\);[^\n]*\n\s*rowTip = requestedModelTip\(fb\);/);
  assert.match(RENDER, /rowTip = rowTip \? rowTip \+ "\\n" \+ note : note;/, "a requested learned version keeps its explanation beside the learned note");
  assert.match(RENDER, /if \(rowTip\) setTip\(row, rowTip\);/);
  assert.match(RENDER, /latest\.classList\.add\("requested"\);\s*\/\/ an alias pick IS the floating family/, "an alias pick marks the submenu's Latest row");
  assert.match(RENDER, /function closeMetaMenu\(\) \{[\s\S]{0,400}pruneTip\(\);/, "a tip up on a menu row drops with the menu");
  assert.match(RENDER, /const closeSub = \(\) => \{ subEl\?\.remove\(\); subEl = null; pruneTip\(\); \};/);
  assert.match(RENDER, /modelFallback: th\.modelFallback \?\? null,/, "the comment popover's picker gets the mark too");
  assert.match(RENDER, /interface Status \{ state: ChipState; sinceEpoch: number \| null; modelFallback\?: ModelFallback \| null;/);
});

test("the yellow tick is the working-state token, the same geometry as the current ✓ (menus wear one vocabulary)", () => {
  assert.match(CSS, /\.meta-item\.requested::after \{\s*\n\s*content: "✓"; position: absolute; right: 6px; top: 50%; transform: translateY\(-50%\);\s*\n\s*background: var\(--st-working-bg, #e0b020\); color: var\(--st-working-fg, #332600\); border-radius: 50%;\s*\n\s*width: 13px; height: 13px; font-size: 9px; font-weight: 900;/);
});
