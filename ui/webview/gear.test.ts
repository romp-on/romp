// The settings gear moved from kernel-inline strings into the shared feed
// bundle (gear.js + feed.css's gear section) so both hosts render the SAME
// modal (the user 2026-07-13). These pins keep that single-source shape:
// undoing the extraction, or adding a host-blind fetch/post, breaks here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const KERNEL = read("bin", "romp-kernel");
const GEAR = read("ui", "webview", "gear.js");
const FEED = read("ui", "webview", "feed.ts");
const GEAR_CSS = read("ui", "webview", "gear.css");
const EXT = read("vscode-extension", "src", "extension.ts");

test("the kernel no longer carries an inline gear (single source: the feed bundle)", () => {
  for (const twin of ["_GEAR_CSS", "_GEAR_JS", "_gear_html"])
    assert.ok(!KERNEL.includes(twin), `${twin} must stay deleted from the kernel`);
});

test("the feed bundle builds and wires the gear", () => {
  assert.ok(FEED.includes('require("./gear.js")'), "feed.ts must load the gear module");
  assert.ok(FEED.includes("initGear("), "feed.ts must init the gear on its kernel channel");
  assert.ok(GEAR.includes("module.exports = { initGear }"));
});

test("the gear opens on the shared {romp:'openSettings'} message on BOTH hosts", () => {
  assert.ok(GEAR.includes("e.data.romp === 'openSettings'"), "gear must listen for the open message");
  assert.ok(KERNEL.includes("openSettings"), "the web shell's rail must still post the open message");
  assert.ok(EXT.includes('{ romp: "openSettings" }'), "the VS Code menu must post the open message");
});

test("every gear fetch routes through the kernel base + token (VS Code's webview origin is synthetic)", () => {
  assert.ok(!/fetch\(['"`]\//.test(GEAR), "no bare same-origin fetches in gear.js — use ku()");
  assert.ok(!/fetch\(kb\(\) \+/.test(GEAR), "kb()-only fetches skip the serve token — use ku()");
  const kuFetches = GEAR.match(/fetch\(ku\(/g) || [];
  assert.ok(kuFetches.length >= 4, `expected the /palette, /models, /version, /analytics fetches via ku(), got ${kuFetches.length}`);
  assert.ok(EXT.includes("window.__rompKernelBase="), "the VS Code feed builder must inject the base");
  assert.ok(EXT.includes("window.__rompKernelToken="), "the VS Code feed builder must inject the serve token (the kernel gates every request, loopback included)");
  assert.ok(EXT.includes("connect-src ${kernelBase}"), "the feed webview CSP must allow the kernel origin");
});

test("the gear posts kernel ops through ONE shared channel (never re-acquires the VS Code API)", () => {
  assert.ok(!GEAR.includes("acquireVsCodeApi"), "a second acquire throws in a real webview");
  for (const op of ["setAutoNudge", "setJudgeModel", "setIndexModel", "setJudgeEffort", "setIndexEffort",
    "setDistillModel", "setDistillEffort", "setCommentModel", "setCommentEffort", "setCommentFast",
    "setFileEditing", "setColormap", "setPalette", "setDefaultDir", "browseDir"])
    assert.ok(GEAR.includes(`'${op}'`), `gear must post ${op}`);
});

test("model/effort options are written exactly once — the single-flight /models fetch (2026-08-30)", () => {
  // The user's "my Distilling pick keeps resetting": fillChoices memoized its RESULT, so a settings
  // open racing the page-load fetch fired a second /models fetch, and whichever resolved last
  // REWROTE every select's options AFTER fill() had set their values — a rewritten select falls
  // back to its first option ("Follow triage" for distill; invisible on Triage model, whose first
  // option coincides with the stored value). The promise is the memo now; a failed fetch clears it.
  assert.ok(GEAR.includes("var choicesP = null;"), "the promise is the memo");
  assert.ok(GEAR.includes("if (choicesP) return choicesP;"), "second callers reuse the in-flight fetch");
  assert.ok(GEAR.includes("choicesP = null; return null;"), "a failed fetch clears the memo for retry");
  assert.ok(!GEAR.includes("if (choices) return Promise.resolve(choices);"), "the result-memo race is gone");
});

test("the login flow lives in a modal behind one button (the user 2026-08-30)", () => {
  // "it would just be a login button… then it would pop up another modal that says paste the code
  // so it doesn't always sit there taking up space" — the paste-code UI collapses behind the
  // Account button; the modal wears the panel treatment (centered card over a dimmed backdrop).
  assert.ok(GEAR.includes("id=rs-login-modal"), "the modal exists");
  assert.ok(GEAR.includes("Log in to Claude Code"), "the button says what it does");
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.css"), "utf8");
  assert.match(CSS, /#rs-login-modal \{ position: fixed; inset: 0; z-index: \d+; background: var\(--overlay-dim, rgba\(0, 0, 0, 0\.55\)\);/);   // tokened with its literal fallback (2026-08-30 merge)
  assert.match(CSS, /\.rs-login-card \{ background: var\(--surface-raised, #252526\); border: 1px solid rgba\(255,255,255,0\.12\);/);
  // mid-flow the button REOPENS the modal, never restarts the flow; terminal state closes it;
  // Cancel + Escape + backdrop close; the code input stays a pure pass-through (T157)
  assert.ok(GEAR.includes("if (lgLive === 'url' || lgLive === 'starting' || lgLive === 'verifying') return;"));
  assert.ok(GEAR.includes("if (!f.state) lgModal(false);"));
  assert.ok(GEAR.includes("if (e.key === 'Escape' && lgM && !lgM.hidden) lgModal(false);"));
  assert.ok(GEAR.includes("if (e.target === lgM) lgModal(false);"));
  assert.ok(GEAR.includes("post({ type: 'loginCode', code: code });"), "pass-through untouched");
});

test("the distilling tier is its own gear pair, defaulting to follow-triage", () => {
  // The user 2026-08-14: the card-prose judges (distiller, briefer, staller) split out of triage so
  // what you READ can run a richer model than the placement judges. The stored sentinel "triage"
  // means follow the triage pick live — today's behavior until the user pins — so the selects must
  // lead with that option and fill from the RAW /version value, never the resolved model.
  for (const id of ["rs-distillmodel", "rs-distilleffort"])
    assert.ok(GEAR.includes(id), `the gear must render ${id}`);
  assert.ok(GEAR.includes('"triage">Follow triage'), "the follow-triage option leads both selects");
  assert.ok(GEAR.includes('"none">Default'), "the effort's no-flag pin is the stored sentinel, never ''");
  assert.ok(GEAR.includes("v.distillModel"), "fill() reads the RAW distill model value");
  assert.ok(GEAR.includes("v.distillEffort"), "fill() reads the RAW distill effort value");
});

test("the default-comment trio is its own gear group, defaulting to same-as-the-session", () => {
  // The user 2026-08-29: every new comment thread on one model/effort/fast pick regardless of the
  // session it branches from. The stored sentinel "session" means inherit the parent — today's
  // behavior until the user pins — so the selects lead with it and fill from the RAW /version value.
  for (const id of ["rs-cmtmodel", "rs-cmteffort", "rs-cmtfast"])
    assert.ok(GEAR.includes(id), `the gear must render ${id}`);
  assert.ok(GEAR.includes('"session">Same as the session'), "the sentinel option leads both selects");
  assert.ok(GEAR.includes("v.commentModel"), "fill() reads the RAW comment model value");
  assert.ok(GEAR.includes("v.commentEffort"), "fill() reads the RAW comment effort value");
  assert.ok(GEAR.includes("v.commentFast === 'on'"), "the fast box reflects the stored ask");
  // fast is an Opus-only research preview: a pinned non-Opus comment model disables the box, and a
  // model pick that strands a checked box unchecks it as part of that gesture — never a silent flap
  assert.ok(GEAR.includes("function cmtFastGate"), "the availability gate must exist");
  assert.ok(GEAR.includes("cmtFastGate(true)"), "the model pick runs the gate as a user gesture");
  assert.ok(GEAR.includes("cmtFastGate(false)"), "fill() re-checks availability without posting");
});

test("every kernel-side select says so when connected machines disagree (the autoNudge rule generalized)", () => {
  // The user 2026-08-14: everything stays in sync; on disagreement the gear ASKS by showing the local
  // value with a mixed mark beside it — hover names the hosts, one pick sets every machine — and a
  // machine that never reported (an older kernel) is unknown, never a disagreement to click away.
  assert.ok(GEAR.includes("fillMixedMarks"), "the generalized reconcile must exist and be called");
  assert.ok(/t\.settings && typeof t\.settings\[key\] !== 'undefined'/.test(GEAR),
    "non-reporting rows are excluded, not read as disagreeing");
  assert.ok(GEAR.includes("differs on: "), "the hover names the disagreeing hosts");
  const mixedSpans = GEAR.match(/class=rs-mixed hidden/g) || [];
  assert.ok(mixedSpans.length >= 10, `every kernel-side select carries a marker span (got ${mixedSpans.length})`);
});

test("the Auto Nudge box speaks for every attached machine, and says so when they disagree", () => {
  // /version answers for the kernel serving this page only, so the box used to show one machine's
  // setting as everyone's — the user 2026-08-14 unchecked it and the other machine kept nudging with
  // nothing on screen to say so. The reconcile reads each attached row's own setting off /tunnels.
  assert.ok(GEAR.includes("fetch(ku('/tunnels')"), "the box must check the OTHER kernels, not just /version");
  assert.ok(GEAR.includes("an.indeterminate = true"), "hosts that disagree put the box in the mixed state");
  assert.ok(GEAR.includes("rs-autonudge-split"), "…said in words too — a tri-state box alone reads past");
  assert.ok(GEAR_CSS.includes(".rs-mixed"), "…and that marker needs its styling, or it inherits nothing");
  assert.ok(/t\.status === 'up'/.test(GEAR) && /typeof t\.autoNudge === 'boolean'/.test(GEAR),
    "only a CONNECTED host that actually reported a setting counts — never guess for a silent one");
  // The description is the one level down: it names the machines, so it cannot be a frozen literal.
  assert.ok(/asub\.textContent = AUTONUDGE_SUB\s*\n?\s*\+/.test(GEAR) && GEAR.includes("split.join("),
    "the hover line must name who differs");
});

test("the /compact suggestion is a real settings checkbox beside Auto Nudge (the user 2026-09-01)", () => {
  // T208 shipped the kernel toggle with no UI; the user ruled it must be an ordinary settings
  // checkbox next to Auto Nudge — off by default for new installs, one click to turn on.
  assert.ok(GEAR.includes("id=rs-suggestcompact"), "the checkbox exists in the gear markup");
  const sessions = GEAR.indexOf(">Sessions<"), chat = GEAR.indexOf(">Chat<");
  const at = GEAR.indexOf("id=rs-suggestcompact");
  assert.ok(sessions < at && at < chat, "…in the Sessions section, with its siblings");
  assert.ok(GEAR.indexOf("id=rs-autonudge") < at && at < GEAR.indexOf("id=rs-conserve"),
    "…directly after Auto Nudge, where the user asked for it");
  assert.ok(/csg\.addEventListener\('change'/.test(GEAR)
    && GEAR.includes("post({ type: 'setCompactSuggest', enabled: csg.checked })"),
    "the click posts the kernel's designed setCompactSuggest message");
  assert.ok(GEAR.includes("csg.checked = !!v.compactSuggest"),
    "…and the box always shows the kernel's persisted answer, never a page default");
  assert.ok(GEAR.includes("['compactSuggest', csg]"),
    "attached machines that disagree get the same mixed mark as every kernel-side setting");
});

test("the gear owns its browseResult (the reply lands in the FEED document, not the chat's)", () => {
  assert.ok(GEAR.includes("'browseResult'") && GEAR.includes("'gear'"));
});

test("gear.css carries the modal styling for every pane that hosts it", () => {
  for (const sel of ["#rsettings", ".rs-card", "#rs-cmap-btn", "#rs-pal-btn", ".ra-openbtn", "#ranalytics"])
    assert.ok(GEAR_CSS.includes(sel), `gear.css must style ${sel}`);
  assert.ok(KERNEL.includes("/dist/gear.css"), "the kernel feed page must link the extracted stylesheet");
});

test("one tooltip per settings row: the Account row's live status is NOT a second rs-sub", () => {
  // the rs-sub CSS floats EVERY rs-sub in a hovered row at the same top:100% box, so a second one
  // stacks a second bordered popover — the 2026-09-02 stacked double tooltip (even empty it painted
  // a box). #rs-login-state is a live inline status, not a description: it wears rs-note.
  assert.ok(GEAR.includes("id=rs-login-state class=rs-note"), "the login status line is an inline note");
  const billing = GEAR.slice(GEAR.indexOf("id=rs-billing"), GEAR.indexOf(">Sessions<"));
  assert.equal((billing.match(/class=rs-sub/g) || []).length, 1, "the Account row keeps ONE description popover");
  assert.ok(GEAR_CSS.includes("#rsettings .rs-note {") && GEAR_CSS.includes("#rsettings .rs-note:empty { display: none; }"),
    "rs-note is inline, hidden while it has nothing to say");
  // native titles never stack on the row popover: the picker buttons speak through aria-label, and
  // the description stands down while a picker dropdown is open or the 'mixed' mark is hovered
  assert.doesNotMatch(GEAR, /id=rs-cmap-btn type=button title=/);
  assert.doesNotMatch(GEAR, /id=rs-pal-btn type=button title=/);
  assert.ok(GEAR.includes("aria-label='Pick the recency colormap'") && GEAR.includes("aria-label='Pick the session palette'"));
  assert.ok(GEAR_CSS.includes("#rsettings .rs-row:has(#rs-cmap-list:not([hidden])) .rs-sub"), "open cmap menu owns the row");
  assert.ok(GEAR_CSS.includes("#rsettings .rs-row:has(.rs-mixed:hover) .rs-sub { display: none; }"), "the mixed mark's title stands alone");
});
