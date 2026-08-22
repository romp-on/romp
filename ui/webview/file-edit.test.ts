// Raw-mode editing in the file viewer (the file browser's slice 2, the user 2026-08-14): a plain
// textarea over the existing raw view, saved through the sid-routed saveFile WS op with a
// NANOSECOND mtime conflict floor — agents edit the same trees, so a stale save REFUSES instead of
// overwriting. Source pins (no jsdom for these modules), the repo convention. The review-driven
// hardening pins sit at the bottom: the first cut had nine confirmed defects, several of them
// data-loss (in-flight keystrokes eaten by the ack, latin-1 re-encoding, CRLF rewrites).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = web("file-view.ts");
const FEED = web("feed.ts");
const FEED_CSS = web("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("Edit arms only off the kernel's own verdicts: text/plain, faithful UTF-8, an ns anchor", () => {
  assert.match(VIEW, /isText = \(r\.headers\.get\("Content-Type"\) \|\| ""\)\.startsWith\("text\/plain"\)\n      && r\.headers\.get\("X-Romp-Text-Utf8"\) !== "0";/);
  assert.match(VIEW, /mtimeNs = r\.headers\.get\("X-Romp-Mtime-Ns"\) \|\| "";/);
  assert.match(VIEW, /editBtn\.hidden = editing \|\| text === null \|\| !isText \|\| !mtimeNs;/);
  // markdown edits from its RAW view — what you edit is what raw shows
  assert.match(VIEW, /if \(isMd && fmt\.md === "rendered"\) \{ fmt\.md = "raw"; saveFmt\(fmt\); \}/);
});

test("the ns anchor travels as a STRING end to end — JSON numbers would round it", () => {
  assert.match(VIEW, /let mtimeNs = "";/);
  assert.match(VIEW, /post\(\{ type: "saveFile", path, sid: sid \|\| undefined, content, baseMtimeNs: mtimeNs, reqId: saveSeq \}\);/);
  assert.match(VIEW, /h\.saved\(String\(m\.mtimeNs \|\| ""\)\);/);
  assert.match(KERNEL, /"mtimeNs": str\(mt\)/);
  assert.match(KERNEL, /if st\.st_mtime_ns != base_ns:/, "the kernel compares NANOSECONDS — same-second writes are caught");
});

test("the save acknowledges before the round-trip, and Ctrl/Cmd+S is the same save", () => {
  assert.match(VIEW, /saveBtn\.disabled = true; saveBtn\.textContent = "Saving…";/);
  assert.match(VIEW, /\(e\.ctrlKey \|\| e\.metaKey\) && e\.key\.toLowerCase\(\) === "s"/);
  assert.match(VIEW, /m\.type === "fileSaved" && editHooks && m\.reqId === editHooks\.reqId/);
  assert.match(VIEW, /m\.type === "fileSaveFailed" && editHooks && m\.reqId === editHooks\.reqId/);
});

test("no exit path can silently eat an edited buffer", () => {
  // the guard lives in closeFileView itself — the browser overlay and Escape close through it
  assert.match(VIEW, /if \(closeGuard && !closeGuard\(\)\) return;/);
  // …and the REPLACE path (opening file B over a dirty editor) asks the same question
  assert.match(VIEW, /if \(document\.getElementById\("romp-fileview"\) && closeGuard && !closeGuard\(\)\) return;/);
  assert.match(VIEW, /const confirmDiscard = \(\): boolean =>\n    !editing \|\| !dirty \|\| window\.confirm/);
  // Escape peels edit mode first, never the whole viewer
  assert.match(VIEW, /if \(editing\) \{\s*\/\/ Escape peels edit mode first, never the whole viewer\n      if \(confirmDiscard\(\)\) exitEdit\(\);\n      return;\n    \}/);
});

test("a conflict keeps the buffer, says why, and Reload asks before discarding", () => {
  assert.match(VIEW, /body\.prepend\(bar2\);/, "the error bar sits ABOVE the textarea — the buffer survives");
  assert.match(VIEW, /if \(\/changed on disk\/\.test\(err\)\) \{/);
  assert.match(VIEW, /dirty = false;\s*\/\/ confirmed once — the replace guard must not ask twice/);
});

test("the kernel's save is atomic, mode-preserving, symlink-transparent, and UTF-8-honest", () => {
  assert.match(KERNEL, /def _save_file\(raw, sid, content, base_mtime_ns\):/);
  assert.match(KERNEL, /changed on disk since you opened it/);
  assert.match(KERNEL, /fd, tmp = tempfile\.mkstemp\(prefix="\.romp-save-", dir=d\)/);
  assert.match(KERNEL, /os\.replace\(tmp, wp\)/);
  assert.match(KERNEL, /os\.chmod\(tmp, stat\.S_IMODE\(st\.st_mode\)\)/);
  assert.match(KERNEL, /wp = os\.path\.realpath\(p\)\s+# write THROUGH a symlink, never over it/);
  assert.match(KERNEL, /not UTF-8 on disk — saving would silently/);
  assert.match(KERNEL, /the request carried no text/, "None content refuses instead of truncating");
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "saveFile":/);
});

test("the feed boots the viewer with the poster, and the editor wears the code view's metrics", () => {
  assert.match(FEED, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/);
  // height: 100%, NOT flex — .fileview-body is a plain overflow block, so a flex basis on its
  // child is inert and the textarea sat at the UA's default few rows (the user 2026-08-17)
  assert.match(FEED_CSS, /\.fileview-editor \{ display: block; height: 100%; width: 100%;/);
  assert.doesNotMatch(FEED_CSS, /\.fileview-editor \{ flex:/, "the inert flex basis must not return");
});

// ── review-driven hardening (2026-08-14): nine confirmed defects on the first cut; these pins hold
// the fixes in place ──

test("keystrokes typed DURING a save survive the ack", () => {
  // the ack used to re-render from the doSave snapshot, silently deleting everything typed while
  // the save round-tripped (seconds, over a remote tunnel) — now the ack re-baselines and stays
  // in edit mode when the live buffer moved past the snapshot
  assert.match(VIEW, /if \(ta && ta\.value !== norm\(content\)\) \{\n          dirty = true;\n          saveBtn\.disabled = false; saveBtn\.textContent = "Save";\n          return;\n        \}/);
});

test("a lost save reply cannot wedge 'Saving…' forever", () => {
  // a federation drop answers with a warn; a socket drop mid-save loses the ack outright — both
  // re-arm Save with honest wording (a save that DID land refuses the retry as changed-on-disk)
  assert.match(VIEW, /m\.type === "warn" && editHooks/);
  assert.match(VIEW, /window\.addEventListener\("romp:wsdown", \(\) => \{\n    if \(!editHooks\) return;/);
  assert.match(VIEW, /it may or may not have landed/);
});

test("a cancelled save's late ack cannot touch a NEW editing session", () => {
  assert.match(VIEW, /editHooks = null;\s*\/\/ a cancelled save's late ack must not touch a NEW session/);
});

test("CRLF files round-trip byte-identical", () => {
  // textareas normalize CRLF→LF on assignment: dirty compares NORMALIZED, and the send restores
  // the file's own endings — an untouched CRLF file must not save with every line rewritten
  assert.match(VIEW, /const norm = \(s: string\): string => s\.replace\(\/\\r\\n\/g, "\\n"\);/);
  assert.match(VIEW, /eolCRLF = \/\\r\\n\/\.test\(text\);/);
  assert.match(VIEW, /dirty = ta!\.value !== norm\(text!\);/);
  assert.match(VIEW, /const content = eolCRLF \? ta\.value\.replace\(\/\\n\/g, "\\r\\n"\) : ta\.value;/);
});

test("the anchor headers ride the /remote relay too — mirrored, unlike Content-Type", () => {
  assert.match(KERNEL, /r_ns = resp\.getheader\("X-Romp-Mtime-Ns"\)/);
  assert.match(KERNEL, /r_u8 = resp\.getheader\("X-Romp-Text-Utf8"\)/);
});
