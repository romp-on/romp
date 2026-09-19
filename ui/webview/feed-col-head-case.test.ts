// The feed's three column-header chips ("Working" / "Blocked" / "Completed") read as sentence case,
// matching the chat + timeline status chips (the user 2026-07-03). The labels were always cased in
// feed.ts; the ALL-CAPS look came from a text-transform:uppercase on .feed-col-head — dropped here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { FEED_BOARD, columnTable } from "./board-def";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the column labels are sentence-case: the feed board's definition, which ensureCols iterates (plans/card-boards.md, phase one)", () => {
  // the literal table this pinned in feed.ts moved into board-def.ts's FEED_BOARD; the triples are today's, byte for byte
  assert.deepEqual(columnTable(FEED_BOARD).map((t) => [...t]),
    [["asks", "Working", "working"], ["needsInput", "Blocked", "blocked"], ["completed", "Completed", "completed"]]);
  assert.equal((FEED.match(/for \(const \[key, label, chip\] of columnTable\(FEED_BOARD\)\)/g) || []).length, 2, "the board and the focused section read the same table");
});

test(".feed-col-head no longer forces uppercase", () => {
  const m = CSS.match(/\.feed-col-head \{[\s\S]*?\}/);
  assert.ok(m, ".feed-col-head rule exists");
  assert.doesNotMatch(m![0], /text-transform:\s*uppercase/);
});
