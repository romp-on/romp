// The new-session directory field (the user 2026-07-28): what the verdict says about a typed path
// (the full sentence, and the compact form shown inside the field), how the keyboard walks the folder
// list, and how the "that folder isn't there" dialog reads. EXECUTES ./dir-complete; the picker
// plumbing (the request/reply pacing, the create fork, the host routing) is source-pinned against
// render.ts below.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { dirStatusLine, dirStatusHint, nextDirActive, createDirPrompt, type DirStatus } from "./dir-complete";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8")
  + fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
const STYLES = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const st = (over: Partial<DirStatus>): DirStatus => ({
  value: "~/GitRepos/api", path: "~/GitRepos/api", exists: true, isDir: true, isFile: false,
  canCreate: false, nearest: "~/GitRepos", missing: 0, isDefault: false, ...over,
});

test("an existing folder reads as a plain confirmation", () => {
  const said = dirStatusLine(st({}));
  assert.equal(said.text, "✓ ~/GitRepos/api");
  assert.equal(said.cls, "", "nothing is wrong, so nothing is coloured");
});

test("the untouched default says it is the default", () => {
  assert.match(dirStatusLine(st({ isDefault: true })).text, /\(the default\)$/);
});

test("a missing folder warns and names where it would go", () => {
  const said = dirStatusLine(st({ exists: false, isDir: false, canCreate: true, missing: 2 }));
  assert.equal(said.cls, "warn");
  assert.match(said.text, /no such folder yet/);
  // it NAMES the folder it would make, and how much of the chain (the user 2026-07-29)
  assert.match(said.text, /Starting will create ~\/GitRepos\/api and the 1 folder above it$/);
  const one = dirStatusLine(st({ exists: false, isDir: false, canCreate: true, missing: 1 }));
  assert.match(one.text, /Starting will create ~\/GitRepos\/api$/, "one folder needs no arithmetic");
  const three = dirStatusLine(st({ exists: false, isDir: false, canCreate: true, missing: 3 }));
  assert.match(three.text, /and the 2 folders above it$/, "plural when it is more than one");
});

test("a file where a folder was typed is an error, not an offer", () => {
  const said = dirStatusLine(st({ isDir: false, isFile: true, canCreate: false }));
  assert.equal(said.cls, "bad");
  assert.match(said.text, /not a folder: /);
});

test("an unreachable path says so rather than offering to create it", () => {
  const said = dirStatusLine(st({ exists: false, isDir: false, canCreate: false, nearest: "" }));
  assert.equal(said.cls, "bad");
  assert.match(said.text, /invalid path: /);
});

test("no answer yet says nothing at all", () => {
  assert.deepEqual(dirStatusLine(null), { text: "", cls: "" });
});

// ── the compact in-box form (the user 2026-08-11): the verdict sits inside the field, so it never
// repeats the path sitting right beside it — unless the kernel's expansion ADDS something — and the
// full sentence (dirStatusLine, wording history above) rides on hover as `title`.
test("an existing folder's hint is a bare ✓ when the kernel's path IS what was typed", () => {
  const said = dirStatusHint(st({}));
  assert.deepEqual(said, { text: "✓", cls: "", title: "✓ ~/GitRepos/api" });
  assert.equal(dirStatusHint(st({ value: "~/GitRepos/api/" })).text, "✓",
    "a trailing slash is not an expansion worth echoing");
});

test("…and shows the expansion when ~ / $VARs resolved to something new", () => {
  assert.equal(dirStatusHint(st({ value: "$REPOS/api" })).text, "✓ ~/GitRepos/api");
});

test("a blank field's hint names the default that blank stands for", () => {
  const said = dirStatusHint(st({ value: "", isDefault: true }));
  assert.equal(said.text, "~/GitRepos/api (the default)");
  assert.equal(said.cls, "");
});

test("a missing folder's hint is the offer, with the named-path sentence a hover away", () => {
  const said = dirStatusHint(st({ exists: false, isDir: false, canCreate: true, missing: 1 }));
  assert.equal(said.cls, "warn");
  assert.equal(said.text, "will be created");
  assert.match(said.title, /Starting will create ~\/GitRepos\/api$/,
    "the 2026-07-29 'which folder, where' answer still exists — on hover");
  const deep = dirStatusHint(st({ exists: false, isDir: false, canCreate: true, missing: 3 }));
  assert.equal(deep.text, "will be created (3 folders)");
});

test("bad paths read as verdicts, not path echoes — the path is already in the box", () => {
  const file = dirStatusHint(st({ isDir: false, isFile: true, canCreate: false }));
  assert.deepEqual([file.text, file.cls], ["a file, not a folder", "bad"]);
  const gone = dirStatusHint(st({ exists: false, isDir: false, canCreate: false, nearest: "" }));
  assert.deepEqual([gone.text, gone.cls], ["invalid path", "bad"]);
});

test("no answer yet: the hint says nothing and carries no stale title", () => {
  assert.deepEqual(dirStatusHint(null), { text: "", cls: "", title: "" });
});

test("the hint lives IN the field — no second row under it", () => {
  assert.match(RENDER, /el\("span", "picker-dir-hint"\); dirHint\.id = "picker-dir-hint"/);
  assert.match(RENDER, /hint\.title = said\.title;/, "the full sentence rides on hover");
  // the typed text must stop where the hint starts: the input's right padding is measured off the
  // rendered hint, never guessed
  assert.match(RENDER, /input\.style\.paddingRight = said\.text && w \? w \+ 16 \+ "px" : ""/);
  // a press on the hint belongs to the input underneath
  assert.match(RENDER, /dirHint\.addEventListener\("mousedown"/);
  assert.doesNotMatch(RENDER, /picker-dir-status/, "the old status row is gone, not merely hidden");
  assert.match(STYLES, /\.picker-dir-hint \{/);
  assert.doesNotMatch(STYLES, /picker-dir-status/);
});

test("walking the list passes through 'nothing chosen' at both ends", () => {
  assert.equal(nextDirActive(-1, 1, 3), 0);
  assert.equal(nextDirActive(0, 1, 3), 1);
  assert.equal(nextDirActive(2, 1, 3), -1, "off the bottom hands the field back to typing");
  assert.equal(nextDirActive(-1, -1, 3), 2, "up from nothing takes the last row");
  assert.equal(nextDirActive(0, -1, 3), -1);
  assert.equal(nextDirActive(-1, 1, 0), -1, "an empty list has nothing to choose");
});

test("the create dialog names the path and how much it would make", () => {
  assert.match(createDirPrompt("api", st({ path: "~/w/a/b", missing: 2, nearest: "~/w" }), ""),
    /~\/w\/a\/b doesn't exist \(2 new folders under ~\/w\)/);
  // one folder needs no arithmetic in the sentence
  assert.match(createDirPrompt("api", st({ path: "~/w/a", missing: 1, nearest: "~/w" }), ""),
    /~\/w\/a doesn't exist\. Create it/);
  assert.match(createDirPrompt("api", null, "/typed/path"), /^\/typed\/path doesn't exist/);
});

test("the field asks the kernel that would OWN the session, so a remote host completes its own disk", () => {
  assert.match(RENDER, /dirAskedHost = pickerHost\(\);/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "dirComplete", value, reqId: \+\+dirReq, host: dirAskedHost \}\)/);
  assert.match(RENDER, /#picker \.picker-host \.picker-be-opt\.sel/, "the host comes off the picker's own selection");
});

test("pacing is the round trip, not a timer: one request in flight, the newest value queued behind it", () => {
  assert.match(RENDER, /if \(dirInFlight\) \{ dirQueued = value; return; \}/);
  assert.match(RENDER, /dirInFlight = false;\s*\n\s*const stale = m\.reqId !== dirReq \|\| \(typeof m\.host === "string" \? m\.host : ""\) !== pickerHost\(\);/);
  assert.match(RENDER, /if \(dirQueued !== null\) \{ const v = dirQueued; dirQueued = null; askDirComplete\(v\); \}/);
  assert.match(RENDER, /if \(stale\) return;/, "a reply for an older keystroke never renders");
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("function askDirComplete"), RENDER.indexOf("function dirMenuOpen")),
    /setTimeout/, "no debounce");
});

test("a chosen completion walks INTO the folder; it never starts the session", () => {
  assert.match(RENDER, /input\.value = it\.path \+ "\/";/);
  assert.match(RENDER, /if \(e\.key === "Enter" && dirActive >= 0\) \{[\s\S]*?acceptDir\(dirActive\);/);
  assert.match(RENDER, /if \(dirKey\(e\)\) return;/, "the completer gets the keys before the session list does");
});

test("a missing directory raises the create-or-edit choice, and Create re-sends the SAME create", () => {
  assert.match(RENDER, /else if \(m\.type === "createDirMissing" && m\.name\) onCreateDirMissing\(m\)/);
  // (2026-07-30: the cue became a provisional TAB, so the folder question retires it and holds what was
  // typed for the retry — "Create it and start" re-sends the same create, so the text is still that
  // session's, not the fallback tab's.)
  assert.match(RENDER, /const held = dropProvisional\(\);/);
  assert.match(RENDER, /pendingCarry = \[\.\.\.held\.queued, held\.draft\]\.filter\(Boolean\)/);
  assert.match(RENDER, /\{ label: "Create it and start", value: "create" \}, \{ label: "Edit the path", value: "edit" \}/);
  assert.match(RENDER, /if \(v === "create"\) \{ startCreate\(req, true\); return; \}/);
  assert.match(RENDER, /\.\.\.\(mkdir \? \{ mkdir: true \} : \{\}\)/, "mkdir rides the same message");
});

test("Edit reopens the picker with what was typed, cursor in the path", () => {
  const edit = RENDER.slice(RENDER.indexOf('if (v === "edit")'), RENDER.indexOf("function openPicker"));
  assert.match(edit, /search\.value = req\.name/);
  assert.match(edit, /dir\.value = req\.dir; dir\.focus\(\); dir\.select\(\); askDirComplete\(dir\.value\)/);
});

test("switching host re-asks: the folders on screen belong to the host that was selected", () => {
  assert.match(RENDER, /\/\/ the completions on screen belong to the host that just stopped being selected\s*\n\s*closeDirMenu\(\);/);
});

// ── round 2 (the user 2026-07-29) ────────────────────────────────────────────────────────────────
// Three complaints, all about the field acting before it was asked to: the folder list dropped over
// the dialog the moment the picker opened; the prefilled path was never vetted against the SERVER the
// session would run on, so a path that cannot exist there only failed after pressing New; and this
// machine's default was prefilled into a remote's field, where it is meaningless.

test("opening the picker asks about the path but does NOT drop a folder list over the dialog", () => {
  assert.match(RENDER, /if \(!dirItems\.length \|\| document\.activeElement !== input\) \{ menu\.style\.display = "none"; return; \}/);
  assert.match(RENDER, /if \(di && !pick\) askDirComplete\(di\.value\);/, "the status is still fetched on open");
});

test("the field itself goes red for a path that cannot work, amber for one that isn't there yet", () => {
  assert.match(RENDER, /input\.classList\.toggle\("bad", said\.cls === "bad"\)/);
  assert.match(RENDER, /input\.classList\.toggle\("warn", said\.cls === "warn"\)/);
  assert.match(STYLES, /\.picker-dir-input\.bad \{ border-color: #e5484d;/);
  assert.match(STYLES, /\.picker-dir-input\.warn \{ border-color: #e0a030; \}/);
});

test("a path that cannot work refuses the create at the field, not after a round trip", () => {
  assert.match(RENDER, /if \(dirStatus && dirStatus\.value === typed && !dirStatus\.isDir && !dirStatus\.canCreate && typed\)/,
    "only when the kernel's answer is about what is typed RIGHT NOW");
  assert.match(RENDER, /That path is a file, not a folder/);
  assert.match(RENDER, /can't be reached on the selected host/);
  // a missing-but-creatable path is NOT refused: that is the create-it-or-edit-it offer
  assert.doesNotMatch(RENDER, /dirStatus\.canCreate \&\& typed\) \{\s*\n\s*pickerError\("That folder doesn't exist/);
});

test("each host remembers the directory you last started a session in there", () => {
  assert.match(RENDER, /const DIR_BY_HOST_KEY = "romp:dirByHost"/);
  assert.match(RENDER, /rememberDir\(req\.host, req\.dir\);/, "recorded when the create is sent");
  assert.match(RENDER, /all\[host \|\| ""\] = d;/, "local is a host key too, so it keeps its own last path");
});

test("a remote's prefill is what you used THERE, never this machine's default", () => {
  // the gear default is one path on this machine; prefilling it into a Linux box's field is a path
  // that cannot exist there. Blank asks that kernel for its own default instead.
  assert.match(RENDER, /return host \? "" : \(kernelDefaultDir \|\| loadSettings\(\)\.defaultDir \|\| ""\);/);
  assert.match(RENDER, /if \(di\) di\.value = dirPrefill\(""\);/, "the open prefill goes through it");
  assert.match(RENDER, /if \(dirIn\) \{ dirIn\.value = dirPrefill\(h\); askDirComplete\(dirIn\.value\); \}/,
    "switching host swaps the path AND re-vets it against that host");
});

test("a new question drops the old verdict, so no answer ever describes another host's disk", () => {
  // the user 2026-07-29: switching host left the field insisting the path was fine. The verdict is about
  // one machine and one path, so it stops standing the moment a different question goes out — and a host
  // whose kernel is too old to answer leaves the line saying "checking", never a borrowed verdict.
  assert.match(RENDER, /dirStatus = null;\s*\n\s*dirItems = \[\];\s*\n\s*renderDirMenu\(false\);/);
  assert.match(RENDER, /checking on \$\{dirAskedHost\}…/);
  assert.match(RENDER, /if \(out\.type === "dirCompletions"\) out\.host = host;/,
    "federation stamps a remote answer with the machine that gave it");
});

test("the completion dropdown is a LIST, not the picker's full 900px (the user 2026-09-02)", () => {
  // width min(520px, calc(100% - 28px)) = the one-column dropdown measure on desktop, and
  // byte-identical to the old left+right-14px geometry on any picker ≤548px (phones unchanged)
  assert.match(STYLES, /\.picker-dir-menu \{[\s\S]{0,900}?width: min\(520px, calc\(100% - 28px\)\);/);
  assert.match(STYLES, /\.picker-dir-menu \{[\s\S]{0,900}?max-height: min\(216px, 40vh\);/);
  assert.match(STYLES, /\.picker-dir-menu \{[\s\S]{0,900}?border-radius: var\(--radius-menu\); box-shadow: var\(--shadow-menu\);/,
    "the dropdown wears the shared menu vocabulary");
});
