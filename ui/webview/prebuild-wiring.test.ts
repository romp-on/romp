// The pre-build POLICY is unit-tested in prebuild.test.ts; this pins how render.ts WIRES it, so the speedup
// can't rot as render.ts evolves (the user 2026-06-25, wants the improvement to persist). No jsdom for the
// webview, so these are source-level pins (the repo convention — see tab-switch-defer / chat-transcript-perf).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("render.ts drives pre-building from the pure planner in prebuild.ts", () => {
  assert.match(RENDER, /import \{ prebuildPlan, type ViewState \} from "\.\/prebuild";/);
  assert.match(RENDER, /prebuildPlan\(activeId, mru, order, viewState\)/); // active excluded, MRU-first — the policy
});

test("the pre-build runs at IDLE priority (requestIdleCallback) with a setTimeout fallback", () => {
  assert.match(RENDER, /requestIdleCallback/);
  assert.match(RENDER, /cancelIdleCallback/);
  // a graceful fallback where rIC is absent, so the optimization still runs (just on a small frame budget)
  assert.match(RENDER, /_ric \? _ric\(cb, \{ timeout: \d+ \}\) : \(window\.setTimeout/);
});

test("a pass YIELDS to the active tab's build and is chunked to the idle deadline", () => {
  // never compete with the active (foreground) heavy build — defer and retry next idle
  assert.match(RENDER, /if \(pendingBuildRaf != null\) \{ schedulePrebuild\(\); return; \}/);
  // stop when the idle budget is spent and resume next idle (chunked → never janks the main thread); checked
  // BEFORE each tab since 2026-09-04 (see tab-switch-lag.test.ts), and a timed-out callback runs regardless
  assert.match(RENDER, /if \(deadline\.timeRemaining\(\) < \d+ && !deadline\.didTimeout\) \{ schedulePrebuild\(\); break; \}/);
});

test("a pass builds each off-screen tab's hidden DOM via ensureView + syncView", () => {
  assert.match(RENDER, /function runPrebuild\(deadline: IdleDeadline\): void/);
  assert.match(RENDER, /ensureView\(id\);\s*\n\s*syncView\(id\);/);
  // one malformed tab must not abort pre-building the rest
  assert.match(RENDER, /try \{[\s\S]*ensureView\(id\);[\s\S]*syncView\(id\);[\s\S]*\} catch/);
  // restore the render-key so nothing keys off a pre-built tab after the pass
  assert.match(RENDER, /const savedRenderingSid = renderingSid;[\s\S]*renderingSid = savedRenderingSid;/);
});

test("schedulePrebuild is idempotent (coalesces a startup burst into one queued pass)", () => {
  assert.match(RENDER, /function schedulePrebuild\(\): void \{\s*\n\s*if \(prebuildHandle != null\) return;/);
});

test("pre-building is wired into the lifecycle: switch, content arrival, updates, and wholesale rebuild", () => {
  // a tab switch warms the others (so the NEXT switch is instant)
  assert.match(RENDER, /showActive\(\);\s*\n\s*schedulePrebuild\(\); \/\/ warm the OTHER tabs/);
  // startup + a new full session payload → build the off-screen tabs in idle
  assert.match(RENDER, /schedulePrebuild\(\); \/\/ startup \+ new content/);
  // an off-screen session marked stale by update()/chatTail() gets rebuilt in idle before the user switches
  const staleRebuilds = RENDER.match(/schedulePrebuild\(\); \/\/ rebuild the now-stale off-screen view/g) || [];
  assert.ok(staleRebuilds.length >= 2, "both update() and chatTail() re-warm a now-stale off-screen view");
  // a compact-mode flip resets every view → cancel the stale plan, then re-warm under the new setting
  assert.match(RENDER, /cancelPrebuild\(\);[\s\S]*showActive\(\);\s*\n\s*schedulePrebuild\(\); \/\/ rebuild every off-screen view/);
});
