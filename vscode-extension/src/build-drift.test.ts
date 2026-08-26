// Build-drift banner + one-click self-update in VS Code (the user 2026-07-13, who wanted a banner when anything gets out of
// sync; 2026-07-14, who wanted a button that does it for them, like the web view
// has). The VS Code panes run VSIX-BUNDLED webview code — no kernel-served page, no ?v= token, so the
// browser pages' shim check never runs here, and a pane's wsStale posts go to a parent that doesn't
// handle them. Instead the EXTENSION compares the `dv` (kernel dist token) riding every keepalive
// against its own bundled build stamp (__ROMP_BUILD__, baked by esbuild.js) and prompts ONCE when the
// installed bundle predates a rebuild. Unlike the browser (whose fix is a reload), a VS Code reload
// can't help — the code is baked into the on-disk VSIX — so the prompt offers a real "Update extension"
// that rebuilds + reinstalls the VSIX via vscode-extension/install.sh. Source pins (the extension host
// needs the vscode module, so the wiring can't run under node --test).
import { test } from "node:test";
import assert from "node:assert";
import * as fs from "fs";
import * as path from "path";

const EXT = fs.readFileSync(path.resolve(process.cwd(), "src", "extension.ts"), "utf8");
const ESBUILD = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");

// The body of updateExtension()..runInstall — where any reload lives — used to pin "reload is
// user-gated" without matching the whole file.
function slice(start: string, end: string): string {
  const a = EXT.indexOf(start);
  const b = EXT.indexOf(end, a + 1);
  assert.ok(a >= 0 && b > a, `could not slice ${start}..${end}`);
  return EXT.slice(a, b);
}

test("esbuild bakes a build stamp into the extension bundle", () => {
  // epoch SECONDS — the same clock as the kernel's dist token (newest dist/*.js mtime), so the two
  // compare directly with no unit conversion.
  assert.match(ESBUILD, /define:\s*\{\s*__ROMP_BUILD__:\s*String\(Math\.floor\(Date\.now\(\) \/ 1000\)\)\s*\}/);
});

test("the extension compares keepalive dv against the stamp and prompts once", () => {
  assert.ok(EXT.includes("declare const __ROMP_BUILD__: number;"), "the define is declared for tsc");
  assert.ok(EXT.includes("function maybeBuildNotice(dv: unknown)"), "the drift check exists");
  assert.ok(EXT.includes("if (buildNotified || !BUILD_STAMP || typeof dv !== \"number\" || dv <= BUILD_STAMP) return;"),
    "latched (one prompt per window), guarded when the stamp is absent, and only NEWER dv fires");
});

test("the drift prompt's buttons are resolved LOCALLY and never dead-end in an error toast", () => {
  const notice = slice("function maybeBuildNotice(dv: unknown)", "async function updateExtension");
  // The buttons come from a LOCAL resolution routed through driftNotice — never a fixed string, never
  // off the wire — so a copy that can't rebuild is offered the copy-command action, not an Update
  // button whose only outcome is an error toast.
  assert.ok(notice.includes("resolveInstallScript(ctx?.extensionPath || \"\", process.env.ROMP_DIR"),
    "resolves the target locally to decide the toast's buttons");
  assert.ok(notice.includes("driftNotice("), "the message + actions come from driftNotice");
  assert.ok(notice.includes("notice.message") && notice.includes("...notice.actions"),
    "shows driftNotice's message and its actions, in order");
  assert.ok(notice.includes("choice === UPDATE_ACTION") && notice.includes("void updateExtension()"),
    "the Update action runs the self-update");
  assert.ok(notice.includes("choice === COPY_ACTION") && notice.includes("clipboard.writeText(INSTALL_COMMAND)"),
    "the Copy action puts the install command on the clipboard — a client-side action that cannot fail");
  // The notice itself must NOT reload — the drift toast never auto-anything (the reload is gated later).
  assert.ok(!notice.includes("reloadWindow"), "maybeBuildNotice must not reload the window");
});

test("updateExtension rebuilds+reinstalls the VSIX, then offers a USER-gated reload", () => {
  const upd = slice("async function updateExtension", "function runInstall");
  // The install target is resolved LOCALLY — this VSIX's own path or ROMP_DIR from our own
  // environment (update-target.ts) — never off the kernel's auth-exempt /version, where a rompDir
  // off the wire would let whatever answers the port pick the directory a shell command runs from.
  assert.ok(upd.includes("resolveInstallScript(ctx?.extensionPath || \"\", process.env.ROMP_DIR"),
    "resolves the install dir from local knowledge, not a kernel response");
  assert.ok(!/info\.rompDir|fetchJson\("\/version"\)/.test(upd),
    "updateExtension must not read the repo root off /version");
  assert.ok(upd.includes("runInstall(script, extDir)") && upd.includes("target.script"),
    "runs the resolved install.sh (script now comes from update-target, not a joined /version path)");
  assert.ok(upd.includes("packaged romp-chat-view\\.vsix") && upd.includes("install into:"),
    "a clean exit is not enough — require the packaged + installed markers (install.sh skips gracefully)");
  // Reload is behind an explicit button click, never automatic (prefer-reload-banner-not-auto).
  assert.ok(upd.includes('"Reload window"') && upd.includes('choice === "Reload window"') &&
    upd.includes('executeCommand("workbench.action.reloadWindow")'),
    "reload only fires when the user clicks Reload window");
  assert.ok(upd.includes("showErrorMessage") && upd.includes("install.sh in a terminal"),
    "a failed/skipped update fails loudly with the manual remedy");
});

test("runInstall shells out with the host's resolved env so node/npm/code resolve", () => {
  const run = slice("function runInstall", "function updateHint");
  assert.ok(run.includes('execFile("bash"') && run.includes("env: process.env"),
    "install.sh runs under bash with the extension host's PATH");
});

test("a palette command exposes the update anytime a faded toast can't be clicked", () => {
  assert.ok(EXT.includes('registerCommand("rompChat.updateExtension", updateExtension)'),
    "the command is registered");
  const pkg = fs.readFileSync(path.resolve(process.cwd(), "package.json"), "utf8");
  assert.ok(pkg.includes('"rompChat.updateExtension"') && pkg.includes('"romp: Update Extension"'),
    "declared in package.json so it shows in the command palette");
});

test("only panel pipes check drift — the passive status pipe never toasts", () => {
  assert.ok(EXT.includes('if (m && m.type === "ka" && !this.passive) maybeBuildNotice(m.dv);'),
    "the ka hook rides the pipe message handler, gated off the passive (status) pipe");
});

test("waiting never blocks the editor: progress in the status bar, action survives toast dismissal (2026-08-18)", () => {
  const upd = slice("async function updateExtension", "function runInstall");
  // A Notification-located progress toast is native chrome that covers content and cannot be moved;
  // Window location renders as a status-bar spinner instead.
  assert.ok(upd.includes("vscode.ProgressLocation.Window"), "update progress lives in the status bar");
  assert.ok(!upd.includes("ProgressLocation.Notification"), "no blocking progress toast during the update");
  // Dismissing the drift toast must not lose the update affordance: a status-bar item carries it
  // until an update SUCCEEDS.
  const notice = slice("function maybeBuildNotice(dv: unknown)", "async function updateExtension");
  assert.ok(notice.includes("showDriftStatusItem(!!target)"), "the drift notice arms the status-bar item");
  assert.ok(upd.includes("clearDriftStatusItem()"), "a successful update clears it");
  const item = slice("function showDriftStatusItem", "function maybeBuildNotice");
  assert.ok(item.includes('"rompChat.updateExtension"') && item.includes('"rompChat.copyInstallCommand"'),
    "the item's click matches the toast's action for this host (update when rebuildable, copy otherwise)");
});
