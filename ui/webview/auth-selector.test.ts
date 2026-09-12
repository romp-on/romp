// Per-session BILLING (the user 2026-08-08, reshaped 2026-08-09): pick, per session, whether it bills
// the Claude login or the API key the manager's environment carries — and SEE the fact everywhere even
// when there is nothing to pick.
//   * the new-session picker's Billing row is ALWAYS there for an SDK session: segmented buttons when
//     the selected host's own sessionList reply (authAvail) says both choices are real, and the same
//     spot writes the single choice out as plain text when only one is (a fact, not a control);
//   * the SWITCHING control is a Billing submenu in the chat tab's right-click menu (moved from a
//     statusline badge, the user 2026-08-09): it exists only when the machine offers both
//     (st.authBoth), posting the same setAuth; the sub-line says "applying…" while the reconnect
//     is pending;
//   * the chat tab's hover carries the fact as a Billing row whenever the backend reports it
//     (st.auth rides ungated now), naming WHICH login account (st.authAcct) beside 'Login'.
// The key is labelled plainly 'API key' — NO key material anywhere: not the key, not even a last-4
// tail (the user 2026-08-08, evening: a tail is still key material; hosts are told apart by name).
// No jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const INTENT = fs.readFileSync(path.join(ROOT, "vscode-extension", "src", "pipe-intent.ts"), "utf8");

test("the picker's Billing row shows for SDK whenever availability is known", () => {
  // one known choice is enough to SHOW the row (the user 2026-08-09) — the both-test only decides
  // buttons vs written-out text; the backend toggle still re-decides the row (Billing is a Claude Code
  // matter, so the Codex pick hides it)
  // (pickerBackendChoice reads the Backend row's chip alone since tab groups, 2026-09-04 — the Tags
  // row wears the same chip grammar, and a selected tag must never read as the backend)
  assert.match(RENDER, /const show = !pickMode && !!\(a && \(a\.login \|\| a\.key\)\) && pickerBackendChoice\(\) === "sdk";/);
  assert.match(RENDER, /function pickerBackendChoice\(\): string \{\s*\n\s*const beSel = document\.querySelector\("#picker \.picker-backend:not\(\.picker-host\):not\(\.picker-auth\):not\(\.picker-tags\) \.picker-be-opt\.sel"\) as HTMLElement \| null;\s*\n\s*return beSel\?\.dataset\.be \|\| effectiveDefaultBackend\(loadSettings\(\)\.backend\);/);
  assert.match(RENDER, /const both = !!\(a!\.login && a!\.key\);/);
  assert.match(RENDER, /auWrap\.style\.display = "none";\s*\/\/ hidden until a sessionList reply carries authAvail/);
  assert.match(RENDER, /beWrap\.addEventListener\("click", \(\) => \{ syncPickerAuth\(\); syncPickerTags\(\); \}\);/);   // the Tags row follows the backend pick too (tab groups)
  // a host switch clears the availability — the choices on screen belong to the OLD host
  assert.match(RENDER, /pickerAuthAvail = null;\s*\n\s*syncPickerAuth\(\);/);
  // …and the reply that re-arms it is dropped-if-stale by the same host check the list itself uses
  assert.match(RENDER, /pickerAuthAvail = \(m\.authAvail && typeof m\.authAvail === "object"\) \? m\.authAvail : null;/);
});

test("one real choice renders WRITTEN OUT in the buttons' place, naming the login account", () => {
  // the buttons hide, the fixed span shows the single applying choice as plain text (the user
  // 2026-08-09: informative, never a one-option selector) — Login named by its account when known
  assert.match(RENDER, /wrap\.querySelectorAll\("\.picker-be-opt"\)\.forEach\(\(x\) => \(\(x as HTMLElement\)\.style\.display = both \? "" : "none"\)\);/);
  assert.match(RENDER, /fixed\.style\.display = both \? "none" : "";/);
  assert.match(RENDER, /fixed\.textContent = both \? "" : \(a!\.key \? "API key" : \(a!\.acct \? `Login \(\$\{a!\.acct\}\)` : "Login"\)\);/);
  assert.match(RENDER, /const auFixed = el\("span", "picker-auth-fixed"\);/);
  // in button mode, the Login button's hover names WHICH account
  assert.match(RENDER, /if \(loginBtn && a!\.acct\) loginBtn\.title = `Bill this session to the machine's Claude login \(\$\{a!\.acct\}\)\.`;/);
});

test("the pick rides createSession, omitted when the row is hidden or written-out", () => {
  assert.match(RENDER, /function pickerAuthChoice\(\): string/);
  // (the picker's Tags row rides the same create since tab groups, 2026-09-04 — omitted the same way when nothing is picked)
  assert.match(RENDER, /host: hostSel, \.\.\.\(auth \? \{ auth \} : \{\}\), \.\.\.\(tags\.length \? \{ tags \} : \{\}\) \}\);/);
  assert.match(RENDER, /interface CreateReq \{ name: string; backend: string; dir: string; host: string; auth\?: string; tags\?: string\[\] \}/);
  // text mode sends nothing — the kernel default IS the single choice, and a stale .sel from a
  // previously-selected both-offering host must not ride along
  assert.match(RENDER, /if \(!sel \|\| sel\.style\.display === "none"\) return "";/);
  // fresh open forgets last time's pick + availability; the local reply re-arms it
  assert.match(RENDER, /auWrapEl\.querySelectorAll\("\.picker-be-opt"\)\.forEach\(\(x\) => x\.classList\.remove\("sel"\)\);/);
  // the default selection comes from the host's own answer, once, not on every re-sync
  assert.match(RENDER, /const def = a!\.default === "key" \? "key" : "login";/);
});

test("the switching CONTROL is the tab menu's Billing submenu, both sides listed (the unavailable one greyed)", () => {
  // moved OUT of the statusline (the user 2026-08-09): no auth badge kind survives there
  assert.match(RENDER, /type MetaKind = "mode" \| "model" \| "effort" \| "fast";/);
  assert.doesNotMatch(RENDER, /metaButton\("auth"/);
  assert.doesNotMatch(RENDER, /AUTH_CHOICES/);
  // …and INTO showTabMenu: only when the machine offers both choices does the item exist at all
  // (a one-auth machine keeps the fact on the tab hover, never a dead selector)
  assert.match(RENDER, /if \(st && st\.auth && \(st\.authAvail \|\| st\.authBoth\)\) \{/);   // 2026-09-08: availability, not only both
  // the flyout offers the two plain labels — Login named by its account, the key by NO material —
  // with the session's current choice check-marked
  assert.match(RENDER, /\{ label: st\.authAcct \? `Login \(\$\{st\.authAcct\}\)` : "Login", value: "login", why: avail\.login \? "" : /);   // 2026-09-08: each option carries the reason it is greyed, or ""
  assert.match(RENDER, /\{ label: "API key", value: "key", why: avail\.key \? "" : /);   // 2026-09-08: reason field, see above
  // the current mark is by WHICH login since T346 (authChoiceCurrent: the key, or a login by st.authLogin)
  assert.match(RENDER, /const cur = authChoiceCurrent\(st, c\.value\);/);
  assert.match(RENDER, /el\("div", "ctx-item" \+ \(cur \? " current" : ""\) \+ \(c\.why \? " disabled" : ""\)\)/);   // 2026-09-08: the unavailable side is greyed, never hidden
  // a pick posts the same setAuth the badge used, and only a CHANGE posts (current = dismiss)
  assert.match(RENDER, /if \(!cur && vscodeApi\) vscodeApi\.postMessage\(\{ type: "setAuth", id, value: c\.value \}\);/);
  // the item's sub-line names the current billing, or the applying reconnect
  assert.match(RENDER, /st\.authPending \? "applying…"/);
  assert.match(RENDER, /auth\?: string; authLive\?: string; authPending\?: boolean; authBoth\?: boolean; authAvail\?: AuthAvail; authPickUnavailable\?: string; authPickFell\?: string; authAcct\?: string; authLogin\?: string; authLabel\?: string;/);   // 2026-09-09: the fall the launch took rides beside the unavailable pick; T346: WHICH login
});

test("no key material reaches the webview — no tail plumbing survives anywhere", () => {
  // the user 2026-08-08 (evening): even a last-4 tail is more key than any label needs. The kernel
  // stopped shipping authTail/apiTail/tail, and the client has no code left that could render one.
  assert.doesNotMatch(RENDER, /authTail/);
  assert.doesNotMatch(RENDER, /apiTail/);
  assert.doesNotMatch(RENDER, /a!\.tail/);
});

test("the chat tab hover says Billing whenever the backend reports it, naming the login", () => {
  // ungated on machine shape (the user 2026-08-09: one-auth machines included) — and 'Login (account)' when known
  // the name beside Login is the kernel's authLabel (a stored login's display, else the machine's own as
  // email · organisation · kind), falling back to authAcct for an older kernel (T346, loginName)
  assert.match(RENDER, /s\.status\.auth === "key" \? "API key"\s*\n\s*: \(loginName\(s\.status\) \? `Login \(\$\{loginName\(s\.status\)\}\)` : "Login"\)\]\);/);
  assert.match(RENDER, /function loginName\(st: Status\): string \{ return st\.authLabel \|\| st\.authAcct \|\| ""; \}/);
  assert.match(RENDER, /: s\.status\.authPickUnavailable === s\.status\.auth\s*\n(?:\s*\/\/[^\n]*\n)*\s*\? `⚠ /);   // 2026-09-08: a pick this box cannot bill is said on the hover too
  // …and the row tells the TRUTH in every landing shape (T124, superseding the quiet-parenthetical
  // form: after a switch the row showed the pick as applied fact through the whole reconnect
  // window, and a wrong-side landing read as an aside). A PENDING pick says "applying — not
  // confirmed yet"; a CONFIRMED contradiction (authLive on the other side — a key found via
  // apiKeyHelper on a login launch) LEADS with the warning and names what is actually billed.
  // Anchored at the gate + label: a session reporting no auth must never grow a fabricated Billing row,
  // so the `if (s.status.auth)` guard is part of the pinned behavior.
  assert.match(RENDER, /if \(s\.status\.auth\) rows\.push\(\["Billing",\s*\n\s*s\.status\.authPending\s*\n\s*\? \(s\.status\.auth === "key" \? "API key" : "Login"\) \+ " \(applying — not confirmed yet\)"/,
    "the reconnect window renders as pending intent, never as applied fact");
  assert.match(RENDER, /⚠ \$\{s\.status\.auth === "key" \? "API key" : "Login"\} picked, but the CLI reports `\s*\n\s*\+ `\$\{s\.status\.authLive === "key" \? "the API key" : "the login"\} — this session bills that`/,
    "a confirmed contradiction leads with the warning");
  // the SWITCH CONTROL (the Billing submenu) carries the same truth where the pick lives
  // (T346: the stored-login evidence branch sits between "applying…" and the unavailable pick)
  assert.match(RENDER, /sb\.textContent = st\.authPending \? "applying…"\s*\n\s*: \(st\.auth === "login" && st\.authLogin && st\.authLoginLive === ""\)\s*\n\s*\? "⚠ CLI used another credential"[^\n]*\n\s*: st\.authPickUnavailable === st\.auth\s*\n(?:\s*\/\/[^\n]*\n)*\s*\? `⚠ \$\{wordOf\(st\.auth\)\} unavailable`[^\n]*\n\s*: st\.authLive && st\.authLive !== st\.auth\s*\n\s*\? `⚠ CLI reports \$\{st\.authLive === "key" \? "API key" : "login"\}`/,
    "the submenu sub-line shows the contradiction, not the unapplied pick");
});

test("set_auth refuses a login pick on a box with no login — the same bar the key side always had (T124)", () => {
  const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");
  assert.ok(BACKEND.includes('why = self.auth_unavailable_why(side, login_id)') && BACKEND.includes('if self.login_ok() is False:'),   // T346: the pick may name a stored login   // 2026-09-08: one reason vocabulary (credentials.WHY_*), login_ok still the probe; 2026-09-09: tri-state, None = cannot tell
    "refuse loudly at pick time when the box demonstrably lacks the credential");
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.ok(KERNEL.includes('_sdk_backend.login_ok = lambda: (None if _claude_account_state() == "unreadable" else bool(_claude_account()))'),
    "the probe is the credential store — the authority the usage bars trust; an unreadable store is cannot-tell, never no-login (2026-09-09)");
  assert.ok(KERNEL.includes("or this machine has no Claude login to switch to."),
    "the warn toast names the login case");
});

test("setAuth is an intent op — held through a kernel-restart window, never dropped", () => {
  assert.match(INTENT, /"setModel", "setEffort", "setMode", "setFast", "setAuth",/);
});
