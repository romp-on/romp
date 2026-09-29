// A file passage's thread on the two surfaces that mark a comment: the chat's comment rail tick and the timeline's
// square. Both expressions are lifted from the source and EXECUTED over a chat thread and a file thread, so the file
// branch is read back as the words it shows, not as a regex over the code.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (rel: string) => fs.readFileSync(path.resolve(process.cwd(), "..", rel), "utf8");
const RENDER = read("ui/webview/render.ts");
const TIMELINE = read("ui/romp-timeline-view.js");

/** The expression between `head` and the first `tail` after it. */
function lift(src: string, head: string, tail: string): string {
  const a = src.indexOf(head);
  assert.ok(a >= 0, "anchor not found: " + head + " (re-anchor)");
  const b = src.indexOf(tail, a + head.length);
  assert.ok(b > a, "end not found after: " + head);
  return src.slice(a + head.length, b);
}
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

test("the rail tick names a file thread's file; a chat thread's reads as before", () => {
  const title = new Function("t", "return " + lift(RENDER, "tick.title = ", ";\n")) as (t: unknown) => string;
  assert.equal(title({ th: { name: "web-comment-1", src: "~/notes/a.md" } }), "web-comment-1 on ~/notes/a.md: click to open it");
  assert.equal(title({ th: { name: "web-comment-1" } }), "web-comment-1: click to jump to it");
  assert.equal(title({ th: { src: "" } }), "comment: click to jump to it");
});

test("the timeline square's tooltip names the file, escaped, and sends the click to the rail", () => {
  const expr = "c.src\n" + lift(TIMELINE, "this.body(c.src\n", "));\n") + ")";
  const body = new Function("c", "esc", "return " + expr) as (c: unknown, e: typeof esc) => string;
  assert.equal(body({ src: "~/a<b>.md", status: "open" }, esc), "a comment on ~/a&lt;b&gt;.md — open it from the chat's comment rail");
  assert.equal(body({ src: "~/a.md", status: "resolved" }, esc), "a resolved comment on ~/a.md — open it from the chat's comment rail");
  assert.equal(body({ status: "open" }, esc), "a comment on this message — click to open it there");
});
