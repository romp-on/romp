// The viewer's GitHub link (the user 2026-08-15): a lazy fileGitLink ask per open, answered by the
// file-OWNING kernel (git on ITS disk is the authority), an anchor that appears only when a real
// URL comes back. Source pins (no jsdom for these modules), the repo convention.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = web("file-view.ts");
const RENDER = web("render.ts");
const CHAT_CSS = web("styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the ask is lazy, per open, and sid-routed — never on the /file byte path", () => {
  assert.match(VIEW, /post\(\{ type: "fileGitLink", path, sid: sid \|\| undefined, reqId: gitSeq \}\);/);
  // thumbnails must not pay three git subprocesses each: /file itself is untouched
  assert.doesNotMatch(KERNEL, /_file_github_url\(fp/);
});

test("the poster is bound at the boot of the document that hosts the viewer", () => {
  assert.match(RENDER, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/);
});

test("the anchor appears only on a real URL, opens a new tab, and shows where it goes", () => {
  assert.match(VIEW, /const gh = el\("a", "fileview-btn fileview-gh"\) as HTMLAnchorElement;/);
  assert.match(VIEW, /gh\.target = "_blank"; gh\.rel = "noopener";/);
  assert.match(VIEW, /gh\.hidden = true;/);
  assert.match(VIEW, /if \(!url\) return;/, "an empty url is the no-link verdict — the button never appears");
  assert.match(VIEW, /gh\.title = url;/, "the full URL one hover away");
  assert.match(CHAT_CSS, /a\.fileview-btn \{ text-decoration: none;/);
});

test("replies are reqId-guarded and cannot touch a later open", () => {
  assert.match(VIEW, /m\.type === "fileGitLink" && gitHooks && m\.reqId === gitHooks\.reqId/);
  // both the close and the replace path drop the hooks, so a late reply lands nowhere
  const closes = VIEW.match(/gitHooks = null;/g) || [];
  assert.ok(closes.length >= 2, "cleared on close AND on replace-open");
});

test("the kernel's answer is a verdict from git itself, threaded off the recv loop", () => {
  assert.match(KERNEL, /def _file_github_url\(raw, sid\):/);
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "fileGitLink":/);
  assert.match(KERNEL, /threading\.Thread\(target=_gl, daemon=True\)\.start\(\)/);
  // the spellings git actually writes for a GitHub origin — incl. ports and ssh.github.com
  assert.match(KERNEL, /ssh:\/\/git@\(\?:ssh\\\.\)\?github\\\.com\(\?::\\d\+\)\?/);
  assert.match(KERNEL, /ls-files", "--error-unmatch"/, "tracked files only — no link to a thing not there");
  // realpath, not normpath: a lexical '..' collapse linked a DIFFERENT file than the viewer shows
  assert.match(KERNEL, /p = os\.path\.realpath\(p\)\n    d = os\.path\.dirname\(p\)/);
});

test("the hidden anchor stays hidden — an author display must not beat [hidden]", () => {
  assert.match(CHAT_CSS, /a\.fileview-btn\[hidden\] \{ display: none; \}/);
});

test("the GitHub link is the action REGISTRY's first entry, not another hand-wired button", () => {
  // the registry (the user 2026-08-22): internal seam, no compatibility promise — actions on the
  // open file declare a mount() instead of editing openFileView, so viewer PRs stop colliding there
  assert.match(VIEW, /export function registerFileViewAction\(a: FileViewAction\): void \{/);
  assert.match(VIEW, /if \(!fileViewActions\.some\(\(x\) => x\.id === a\.id\)\) fileViewActions\.push\(a\);/, "same id registered twice mounts once");
  assert.match(VIEW, /registerFileViewAction\(\{\n  id: "github-link",/);
  // openFileView renders registered actions by WALKING THE TABLE, after the built-ins
  assert.match(VIEW, /for \(const a of fileViewActions\) \{\n    const n = a\.mount\(\{ path, sid: sid \|\| null \}\);\n    if \(n\) acts\.appendChild\(n\);\n  \}/);
});
