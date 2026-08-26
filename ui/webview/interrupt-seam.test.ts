// The interrupt SEAM (the user 2026-07-09): the model's null settle-reply ("No response requested.")
// closing an interrupted turn renders as part of the seam — a slim rail marker — not a full assistant
// bubble; and the interrupt marker itself names the CAUSE when romp's resume notice says so (a kernel
// restart / crash cut is not the user pressing stop). Source-level pin like feed-interrupting.test.ts
// (the chat renderer has no jsdom harness); the kernel flags are behavior-tested in
// tests/test_kernel_interrupt_seam.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the null settle-reply renders as a seam marker, never an assistant bubble", () => {
  assert.match(R, /if \(\(ev as any\)\.interruptSettle\) \{/);
  assert.match(R, /no response — turn settled/);
  assert.match(R, /the model closed the interrupted turn with nothing to add; the real work resumes below/);
  // it reuses the interrupt marker's own chrome (turn-interrupt + interrupt-line), minus the stop-square
  // glyph — the square means a stop happened HERE; the settle line is its echo, not a second stop
  const settle = R.slice(R.indexOf("interruptSettle"), R.indexOf("interruptSettle") + 600);
  assert.match(settle, /turn turn-interrupt/);
  assert.match(settle, /interrupt-line/);
  assert.doesNotMatch(settle, /interrupt-square/);
});

test("the interrupt marker names the cause when the kernel stamped one", () => {
  assert.match(R, /const cause = \(ev as any\)\.interruptCause;/);
  assert.match(R, /interrupted — kernel restart/);
  assert.match(R, /a romp kernel restart cut this turn; the session was resumed automatically/);
  assert.match(R, /interrupted — process died/);
  // the unlabeled seam keeps the user-stop reading
  assert.match(R, /you stopped this turn here \(the stop button \/ Ctrl\+C\)/);
});

test("a card anchored at a DROPPED settle's uuid lands on the seam that replaced it", () => {
  // A machine-cut turn's settle event is dropped server-side (tests/test_interrupt_settle_machine_cut.py),
  // but that settle atom is the cut turn's LAST assistant atom — exactly where verdicts/cards get
  // anchored — so the click honest-failed "couldn't locate" though the seam it closed was right there
  // (the user 2026-08-25, a card on a dozens-of-restarts session). The kernel aliases the dropped
  // uuids onto the marker (settleUuids) and the seam answers to them, event-based — never nearest-time.
  assert.match(R, /const su = \(ev as any\)\.settleUuids as string\[\] \| undefined;/);
  assert.match(R, /if \(su && su\.length\) turn\.dataset\.uuids = su\.join\(" "\);/);
  // BOTH anchor lookups honor the alias — the initial query and the post-re-render re-query (the
  // data-mids precedent: one selector short and the recovery still honest-fails pointer-not-rendered)
  const both = R.split('data-uuids~=').length - 1;
  assert.ok(both >= 2, "data-uuids must be in BOTH the initial query and the re-query");
  // …and the events search finds the alias too, so the window re-renders around the seam
  assert.match(R, /\(\(\(e as \{ settleUuids\?: string\[\] \}\)\.settleUuids \|\| \[\]\)\.includes\(uuid\)\)/);
});
