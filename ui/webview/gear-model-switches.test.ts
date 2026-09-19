// The Automation pane's Model section (the user 2026-09-17; under Automation by the maintainer's decision, 2026-09-17: both
// are kernel policies applied to every session on the kernel's own initiative, like the Nudges): two kernel-side switches,
// Always fast and Retry upgrades after downgrades, in the house grammar of every kernel setting, a stamped emitter under the
// store's own name, the stale maps, the /version fill, the mixed mark, membership in the federation broadcast set, sitting
// after the Nudges rows as the pane's second section, in the tab's own row shape: a permanent one-sentence line under the
// label in place of a hover popover (T408).
// Source pins (the gear has no DOM harness); the behaviour lives in the kernel and the SDK backend (tests/test_always_fast.py,
// tests/test_retry_upgrade.py, tests/test_model_switches_kernel.py).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const GEAR = read("gear.js");
const FED = read("federation.ts");
const DOCS = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "reference.md"), "utf8");

function pane(key: string, next: string): string {
  const a = GEAR.indexOf("'<div class=rs-pane data-pane=" + key + " hidden>' +");
  const b = GEAR.indexOf("'<div class=rs-pane data-pane=" + next + " hidden>' +", a);
  assert.ok(a > 0 && b > a, "the " + key + " pane's opener and the " + next + " pane's after it");
  return GEAR.slice(a, b);
}
const automationPane = () => pane("automation", "tasks");
const chatPane = () => pane("chat", "feed");

test("the Model section is the Automation pane's second, after the Nudges rows, with the two rows in the house grammar", () => {
  const p = automationPane();
  const nudges = p.indexOf(">Nudges</div>"), compact = p.indexOf("id=rs-suggestcompact>"), model = p.indexOf("<div class='rs-sec'>Model</div>");
  assert.ok(nudges > 0 && compact > nudges && model > compact, "Nudges < Suggest /compact < Model: " + [nudges, compact, model].join(","));
  assert.equal((p.match(/rs-sec-first/g) || []).length, 1, "Nudges keeps the pane's first head; Model is a plain rs-sec");
  assert.equal(p.indexOf("rs-sec", model + "<div class='rs-sec'>Model</div>".length), -1, "Model is the pane's last section: no head after it");
  for (const [id, label] of [["rs-alwaysfast", "Always fast"], ["rs-retryupgrade", "Retry upgrades after downgrades"]]) {
    const re = new RegExp("<label class='rs-row'><input type=checkbox id=" + id + ">\" \\+\\n\\s*'<span><b>" + label + "</b><span class=rs-mixed hidden></span>'");
    assert.match(p, re, id + ": a checkbox row, the label bold, the mixed mark inside the label (fillMixedMarks finds it by closest('label'))");
    assert.ok(p.indexOf("id=" + id) > model, id + " is in the Model section");
  }
  assert.equal((p.match(/id=rs-alwaysfast\b/g) || []).length, 1); assert.equal((p.match(/id=rs-retryupgrade\b/g) || []).length, 1);
  const c = chatPane();
  assert.doesNotMatch(c, /id=rs-alwaysfast\b|id=rs-retryupgrade\b|>Model<\/div>/, "the rows and their head left the Chat pane");
  assert.ok(c.indexOf(">Thinking</div>") > 0 && c.indexOf(">Chat history</div>") > c.indexOf(">Thinking</div>"), "Thinking and Chat history stand where they were, now adjacent");
});

test("the rows' words: the Automation tab's row shape, a permanent one-sentence line under the label and no hover popover (T408)", () => {
  const p = automationPane();
  const sub = (id: string) => { const i = p.indexOf("id=" + id); return p.slice(i, p.indexOf("</span></label>", i)); };
  const af = sub("rs-alwaysfast"), ru = sub("rs-retryupgrade");
  for (const s of [af, ru]) {
    assert.equal((s.match(/class=rs-line>/g) || []).length, 1, "one rs-line per row: the line that is always there");
    assert.doesNotMatch(s, /rs-sub|title=/, "no hover popover and no title: a popup under the pane's last rows ran past the card and scrolled it (T408)");
  }
  assert.match(af, /<span class=rs-line>Every Opus session runs Claude Code's fast mode, billed at a premium; a session you set to Slow stays slow\.<\/span>/, "Always fast's line");
  assert.match(ru, /<span class=rs-line>A session whose model fell back without a pick asks for its picked model again every ten minutes, once quiet, until it is back\.<\/span>/, "Retry upgrades after downgrades' line");
  assert.doesNotMatch(af + ru, /fleet/i);
});

test("each switch is a stamped kernel setting: the emitter under its store, the stale maps, the fill, the mixed mark, the broadcast set", () => {
  assert.match(GEAR, /afb = document\.getElementById\('rs-alwaysfast'\), rub = document\.getElementById\('rs-retryupgrade'\)/);
  assert.match(GEAR, /if \(afb\) afb\.addEventListener\('change', function \(\) \{ post\(\{ type: 'setAlwaysFast', enabled: afb\.checked, gt: gclock\.stamp\('always-fast'\) \}\); \}\);/);
  assert.match(GEAR, /if \(rub\) rub\.addEventListener\('change', function \(\) \{ post\(\{ type: 'setRetryUpgrade', enabled: rub\.checked, gt: gclock\.stamp\('retry-upgrade'\) \}\); \}\);/);
  assert.match(GEAR, /'always-fast': 'Always fast', 'retry-upgrade': 'Retry upgrades after downgrades'/, "STALE_LABELS");
  assert.match(GEAR, /'always-fast': 'setAlwaysFast', 'retry-upgrade': 'setRetryUpgrade'/, "STALE_TYPE");
  assert.match(GEAR, /\['alwaysFast', afb\], \['retryUpgrade', rub\]\]\.forEach\(function \(pair\) \{/, "the mixed marks read the cross-machine settings dict");
  assert.match(GEAR, /if \(afb && typeof v\.alwaysFast === 'string'\) afb\.checked = v\.alwaysFast === 'on';/, "filled RAW from /version");
  assert.match(GEAR, /if \(rub && typeof v\.retryUpgrade === 'string'\) rub\.checked = v\.retryUpgrade === 'on';/);
  assert.match(FED, /"setAlwaysFast", "setRetryUpgrade"\]\);/, "one value across machines: the ops reach every attached kernel");
  assert.doesNotMatch(GEAR, /Date\.now\(\)[^\n]*setAlwaysFast|setAlwaysFast[^\n]*Date\.now\(\)/, "stamped through the gesture clock, never the device clock");
});

test("the reference documents both switches where fast mode is described", () => {
  assert.match(DOCS, /^### Always fast, and retrying an upgrade after a downgrade$/m);
  assert.match(DOCS, /\*\*Always fast\*\* runs every session in fast mode whenever its model allows it/);
  assert.match(DOCS, /\*\*Retry upgrades after downgrades\*\* acts when a session's model changes to a\s+lower tier without a pick/);
  assert.match(DOCS, /never the literal `\/fast on`/);
});
