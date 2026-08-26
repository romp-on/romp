// Thread mail draws from the comment's anchor square (the user 2026-08-23): a thread endpoint's x is
// the square's anchorT (fromThreadT/toThreadT from the kernel), not the mail time, on the PARENT's
// lane — the existing same-lane path then bridges out of the square and back into the lane where the
// mail lands. Source pins (the view is a big canvas module; the kernel side is executed in
// tests/test_thread_arc.py).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const VIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("send and land positions honor the thread square everywhere a message draws", () => {
  assert.match(VIEW, /const sendXT = \(mm\) => mm\.fromThreadT \|\| mm\.sent;/);
  assert.match(VIEW, /const landXT = \(mm\) => mm\.toThreadT \|\| execAt\(mm\);/);
  // the per-message connector, its windowing, the obstacles pass, and the arrival dot all draw
  // from the same two accessors — one frame, never a mix of square-x and mail-x
  assert.match(VIEW, /const xs = x\(offL \? t0 : sendXT\(mm\)\), ys = laneY\(sLane\), xe = x\(landXT\(mm\)\)/);
  assert.match(VIEW, /if \(landXT\(mm\) < t0 \|\| sendXT\(mm\) > t1\) return;/);
  assert.match(VIEW, /if \(inWin\(landXT\(mm\)\) && vidx\[mm\.toId\] != null\) obstacles\.push\(\{ x: x\(landXT\(mm\)\)/);
  assert.match(VIEW, /const c = dot\(x\(landXT\(mm\)\), cy, col/);
});
