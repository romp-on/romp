// The Billing flyout on a one-auth box (the user 2026-09-08): both choices are ALWAYS listed, the one this
// box cannot bill is greyed with its reason in the hover and inert, and a pick that fell to the other side
// says so in the sub-line. render.ts has no jsdom harness, so these are source pins, the same idiom as
// auth-selector.test.ts; the kernel/backend halves are pinned in tests/test_session_auth.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const STYLES = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");

test("the Billing submenu renders on availability, not only when both sides exist", () => {
  assert.match(RENDER, /if \(st && st\.auth && \(st\.authAvail \|\| st\.authBoth\)\) \{/);
  assert.match(RENDER, /const avail: AuthAvail = st\.authAvail \|\| \{ login: true, key: true \};/);
});

test("both options are listed; the unavailable one is greyed, titled with its reason, and inert", () => {
  assert.match(RENDER, /value: "login", why: avail\.login \? "" : \(avail\.loginWhy \|\| "no Claude login signed in on this machine"\)/);
  assert.match(RENDER, /value: "key", why: avail\.key \? "" : \(avail\.keyWhy \|\| "no apiKeyHelper configured"\)/);
  assert.match(RENDER, /\(cur \? " current" : ""\) \+ \(c\.why \? " disabled" : ""\)/);   // T346: cur = authChoiceCurrent (by WHICH login)
  assert.match(RENDER, /opt\.title = c\.why;\s*\n\s*opt\.setAttribute\("aria-disabled", "true"\);/);
  assert.match(RENDER, /if \(c\.why\) return;\s*\/\/ a disabled option posts nothing/);
  // the click handler posts setAuth only past that guard
  const i = RENDER.indexOf('if (c.why) return;');
  const j = RENDER.indexOf('vscodeApi.postMessage({ type: "setAuth", id, value: c.value })', i);
  assert.ok(i > 0 && j > i, "setAuth is posted only after the disabled guard");
  assert.match(STYLES, /\.ctx-sub \.ctx-item\.disabled \{ opacity: 0\.45; cursor: default; \}/);
});

test("a pick that fell to the other side is said in the sub-line, from the kernel's word on the fall", () => {
  assert.match(RENDER, /st\.authPickUnavailable === st\.auth\s*\n(?:\s*\/\/[^\n]*\n)*\s*\? `⚠ \$\{wordOf\(st\.auth\)\} unavailable`/);
  // the fall itself is the kernel's authPickFell (the launch's own decision), read by BOTH surfaces; an older
  // kernel without the field is inferred the way the sub-line always did (the other side exists)
  assert.match(RENDER, /authPickUnavailable\?: string; authPickFell\?: string;/);
  assert.match(RENDER, /function authFellTo\(st: Status\): string \{\s*\n\s*if \(st\.authPickFell !== undefined\) return st\.authPickFell \|\| "";/);
  assert.match(RENDER, /\+ \(authFellTo\(st\) \? `, billing \$\{wordOf\(authFellTo\(st\)\)\}` : ""\)/);
  assert.match(RENDER, /\+ \(authFellTo\(s\.status\) \? ` — this session bills \$\{authFellTo\(s\.status\) === "key" \? "the API key" : "the login"\}`/);
  assert.match(RENDER, /: " — nothing to fall to, so the launch went out as picked"\)/);
});

test("the kernel's status carries authAvail with reasons, and authBoth only for older clients", () => {
  assert.match(KERNEL, /"authAvail": _auth_avail_status\(\),/);
  assert.match(KERNEL, /def _auth_avail_status\(\):/);
  assert.match(KERNEL, /out\["loginWhy"\] = jd\._cred\.WHY_MANAGED_HELPER if managed else jd\._cred\.WHY_NO_LOGIN/);
  assert.match(KERNEL, /out\["keyWhy"\] = jd\._cred\.WHY_NO_HELPER/);
  // the default falls to the side that exists, in BOTH directions
  assert.match(KERNEL, /elif default == "login" and not login_ok and key:\s*\n\s*default = "key"/);
});
