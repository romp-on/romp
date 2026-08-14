// The new-session DIRECTORY field + per-session dir display (the user 2026-06-22). A session's working
// directory is fixed at creation, so the picker lets you choose it (prefilled from the gear default, the
// kernel-fed completer offering real folders), and every session shows its dir — dimmed basename on the
// lane tab (full path on hover) and in the system-context card's collapsed summary. Source-level pins
// (no jsdom for the renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SETTINGS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "settings.ts"), "utf8");

test("the picker has a directory field — with ONE suggestion surface, the kernel-fed completer", () => {
  assert.match(RENDER, /el\("input", "picker-dir-input"\)/);
  // appended into the picker box
  assert.match(RENDER, /box\.appendChild\(dirWrap\);/);
  // The recent-dirs datalist is GONE, not merely hidden (the user 2026-08-11): it was superseded by the
  // completer (2026-07-28) but stayed wired, and autocomplete="off" does not suppress a list-attribute
  // dropdown in Chromium — so TWO boxes popped over the field, the native one offering session-history
  // dirs that no longer exist on disk (a recorded dir outlives a rename). The completer asks the OWNING
  // kernel (dirComplete), the authoritative source for what a path IS.
  assert.doesNotMatch(RENDER, /picker-dir-list/);
  assert.doesNotMatch(RENDER, /createElement\("datalist"\)/);
  assert.match(RENDER, /dirInput\.setAttribute\("autocomplete", "off"\)/,
    "…and the browser's own form-history dropdown stays off the field too");
});

test("createSession carries the chosen dir, alongside name + backend", () => {
  // backend comes from the + dialog's per-session toggle, falling back to the gear default (the user
  // 2026-06-23). The whole request goes through startCreate, which remembers it so a missing directory
  // can be created and the SAME create re-sent (the user 2026-07-28).
  assert.match(RENDER, /startCreate\(\{ name, backend: beSel\?\.dataset\.be \|\| loadSettings\(\)\.backend,\s*\n\s*dir: dirInput\.value\.trim\(\), host: hostSel, \.\.\.\(auth \? \{ auth \} : \{\}\) \}\)/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "createSession", \.\.\.req/);
});

test("the dir field is prefilled with the gear default and hidden in pick-mode", () => {
  assert.match(RENDER, /return host \? "" : \(kernelDefaultDir \|\| loadSettings\(\)\.defaultDir \|\| ""\);/);
  assert.match(RENDER, /dirWrap\.style\.display = pick \? "none" : ""/);
});

test("renderPicker no longer refills a recent-dirs list — the completer owns suggestions", () => {
  assert.doesNotMatch(RENDER, /Recent dirs → the new-session field's autocomplete/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "dirComplete", value, reqId: \+\+dirReq, host: dirAskedHost \}\)/,
    "the live kernel-fed completer is the one suggestion path");
});

test("a session carries cwd, shown on the statusline just left of the mode/model/effort controls", () => {
  assert.match(RENDER, /interface Session \{[^}]*cwd\?: string/);
  assert.match(RENDER, /cwd: msg\.cwd \?\? \(prev \? prev\.cwd : ""\)/);
  // a status-dir element (basename; full path on hover + click-to-open via asFolderLink), appended BEFORE
  // #spinner-meta (the controls cluster)
  assert.match(RENDER, /el\("span", "status-dir"\)/);
  assert.match(RENDER, /asFolderLink\(dir, s\.cwd, activeId \|\| undefined\)/);
  assert.match(RENDER, /right\.appendChild\(dir\);[\s\S]*?const meta = el\("span", "spinner-meta"\)/);
  // and NOT on the tab anymore (the user 2026-06-23)
  assert.doesNotMatch(RENDER, /tab-dir/);
});

test("the system-context card shows the dir basename in its collapsed summary", () => {
  assert.match(RENDER, /bits\.push\("📁 " \+ \(ev\.cwd/);
});

test("the status-dir and picker-dir-input styles exist", () => {
  assert.match(CSS, /\.status-dir \{/);
  assert.match(CSS, /\.picker-dir-input \{/);
});

test("a Browse button opens the host-native folder dialog (browseDir → browseResult fills the field)", () => {
  assert.match(RENDER, /el\("button", "picker-browse"\)/);
  assert.match(RENDER, /postMessage\(\{ type: "browseDir" \}\)/);
  assert.match(RENDER, /m\.type === "browseResult"[\s\S]*?di\.value = m\.path/);
  assert.match(CSS, /\.picker-browse \{/);
});

// The dialog is drawn by the KERNEL, and not every kernel can draw one — a Linux server or cloud VM has
// no desktop session, and the button was a no-op there. The capability rides in on the local sessionList;
// every place that touches the button goes through ONE helper, so a new call site can't reintroduce a
// button that promises what the machine cannot do.
test("Browse… disappears on a kernel with no desktop, and is disabled (not hidden) for a remote host", () => {
  assert.match(RENDER, /let kernelNativeDialogs = true;/, "unknown → keep the button an older kernel always had");
  assert.match(RENDER, /function applyBrowseState\(host: string\)[\s\S]*?b\.style\.display = kernelNativeDialogs \? "" : "none";[\s\S]*?b\.disabled = !!host;/);
  // one helper, called from all three places the button's state can change
  assert.match(RENDER, /applyBrowseState\(h\);/);                // the host row was clicked
  assert.match(RENDER, /applyBrowseState\(openHost\);/);         // the picker opened (on its default host)
  assert.match(RENDER, /applyBrowseState\(pickerHost\(\)\);/);   // the capability landed while it was open
  assert.doesNotMatch(RENDER, /browse0/, "the old inline reset is gone — the state lives in the helper");
  assert.match(CSS, /\.picker-browse:disabled \{/, "a disabled button must LOOK disabled, not just ignore clicks");
});

test("the capability is adopted only from the LOCAL kernel's reply, like defaultDir", () => {
  // a remote's answer is about that machine's screen, not this one's
  assert.match(RENDER, /if \(typeof m\.nativeDialogs === "boolean" && !from\) \{\s*\n\s*kernelNativeDialogs = m\.nativeDialogs;/);
});

test("no tooltip still claims the folder dialog is macOS-only", () => {
  assert.doesNotMatch(RENDER, /native macOS dialog/);
});

test("the dir field prefills with the kernel's real default path (not blank), still editable", () => {
  // …and only from the LOCAL reply: a remote kernel's default directory is that machine's, not this one's
  assert.match(RENDER, /if \(typeof m\.defaultDir === "string" && !from\) kernelDefaultDir = m\.defaultDir/);
  assert.match(RENDER, /di\.value = kernelDefaultDir \|\| loadSettings\(\)\.defaultDir \|\| ""/);
});

test("browseResult routes by target: gear → #rs-defaultdir (+ change to persist), else the picker field", () => {
  assert.match(RENDER, /m\.target === "gear"/);
  assert.match(RENDER, /getElementById\("rs-defaultdir"\)[\s\S]*?dispatchEvent\(new Event\("change"\)\)/);
});

test("defaultDir is a persisted setting with an empty default", () => {
  assert.match(SETTINGS, /defaultDir: string;/);
  assert.match(SETTINGS, /defaultDir: ""/);
});
