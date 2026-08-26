// Parked-to-dead HANDOFF decision in the feed (the user 2026-06-22): a send to a DEAD session parks until
// revival; the kernel surfaces it as a needs-you card (_parked_handoffs → build_feed) and the view gives it
// a "Revive" button that brings the offline recipient back (delivering the parked mail), plus the existing
// Clear to dismiss. Source-pin (no jsdom for the feed renderer), like the other feed-*.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("a parkedHandoff card gets a Revive button, shown only for that block state", () => {
  assert.match(FEED, /const revive = el\("button", "fdismiss frevive"\)/);
  assert.match(FEED, /actions\.append\(revive,/);   // resume-gate buttons ride the same row (Retry moved beside its badge 2026-08-24)
  assert.match(FEED, /const isParked = it\.blocked\?\.state === "parkedHandoff"/);
  assert.match(FEED, /a\._revive\.style\.display = isParked \? "" : "none"/);
});

test("Revive posts reviveSession for the OFFLINE recipient (toSid); Clear still dismisses", () => {
  assert.match(FEED, /const toSid = it\.blocked\.toSid \|\| it\.sid/);
  assert.match(FEED, /type: "reviveSession", id: toSid/);
  // dismissal rides the EXISTING Clear → askClear (cleared.jsonl); no new dismissal path
  assert.match(FEED, /type: "askClear", itemId: it\.itemId/);
});

test("the blocked type carries the parked-handoff fields the UI actually reads", () => {
  // fromName/msgId/body were produced-but-never-consumed — retired in the 2026-07-07 contract audit
  assert.match(FEED, /toName\?: string; toSid\?: string/);
});
