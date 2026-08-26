// The file VIEWER mounts in both documents (file-view.ts is one shared module), but the chat page
// loads styles.css alone and the feed page feed.css alone — so its dress is declared in BOTH sheets
// (the .romp-acted / filebrowse precedent). The copies had already drifted once (feed.css lacked the
// a.fileview-btn anchor rules, so the GitHub link rendered hrefless-underlined there, 2026-08-26).
// This pins the shared chrome byte-equal so it cannot drift again. Rules that are deliberately
// pane-specific (the md body, wrap mode, load cue) are not pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const CHAT = read("styles.css");
const FEED = read("feed.css");

const RULES = [
  "#romp-fileview {", ".fileview {", "body.fileview-open {", ".fileview-bar {", ".fileview-name {",
  ".fileview-dir {", ".fileview-base {", ".fileview-acts {", ".fileview-btn {", ".fileview-btn:hover {",
  "a.fileview-btn {", "a.fileview-btn[hidden] {", ".fileview-body {",
  ".fileview-cm {", ".fileview-cm .cm-editor {", ".fileview-editor {",
  ".fileview-dir-link {", ".fileview-dir-link:hover {",
];

function ruleOf(css: string, head: string): string {
  const at = css.indexOf(head);
  assert.ok(at >= 0, head + " present");
  return css.slice(at, css.indexOf("}", at) + 1);
}

test("the viewer's shared chrome exists in BOTH sheets, byte-equal", () => {
  for (const head of RULES) {
    assert.equal(ruleOf(CHAT, head), ruleOf(FEED, head), head + " mirrors exactly");
  }
});
