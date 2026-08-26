// The managed image fetch heals from every failure mode the wire can produce (the user 2026-08-18,
// whose inline figures "never render until I send another message"): a refused status voids the
// resume state (the stale-Range 416 loop), a settled give-up chip still rides the push-heal (one
// attempt per NEW error, never one per push), and every tap acknowledges even when an attempt is
// already in flight. Source pins on ui/webview/preview.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");

test("a refused status voids the resume state — the stale-Range 416 loop (2026-08-18)", () => {
  // an agent re-plotting the same filename SHRINKS the file; the kernel's 416 expects a clean
  // restart, but the client kept `got`, so every tap and heal replayed the same stale Range and
  // failed deterministically fast — while a send's freshly-minted box (got=0) rendered instantly
  const at = PREVIEW.indexOf("a refused status VOIDS the resume state");
  assert.ok(at > 0);
  const tail = PREVIEW.slice(at, at + 700);
  assert.ok(tail.indexOf("parts = []; got = 0;") > 0, "reset BEFORE the throw");
  assert.ok(tail.indexOf("throw new Error(why") > tail.indexOf("parts = []; got = 0;"));
});

test("a settled chip still rides the push-heal, one attempt per NEW error (2026-08-18)", () => {
  // only the retrying branch registered for the heal, so a spent budget dropped the box from the
  // map forever — "figures never render on their own, only when I send a message" (the send's tail
  // re-render minted a fresh box; the old one healed never)
  assert.match(PREVIEW, /let chipHealedErr: string \| null = null;/);
  assert.match(PREVIEW, /if \(lastErr !== chipHealedErr\) \{\s*\n\s*failedPreviews\.set\(box, \(\) => \{ chipHealedErr = lastErr; autoRetries = 1; build\(true\); \}\);/,
    "re-registers ONLY on new information — never one fetch per push for a dead figure");
});

test("every tap acknowledges, even mid-attempt when build() no-ops on its fetching guard", () => {
  assert.match(PREVIEW, /const ackTap = \(ev: Event\) => \{/);
  assert.match(PREVIEW, /autoRetries = 3; ackTap\(ev\); build\(true\); \};   \/\/ a tap re-arms persistence/);
  assert.equal((PREVIEW.match(/ackTap\(ev\); build\(true\)/g) || []).length, 2,
    "both the retrying note and the settled chip acknowledge");
});

// ── the three no-retry dead-ends (the user 2026-08-24: figures stayed blank until a new message
// forced a re-render — the send's registerOptimistic full-window rebuild minted fresh boxes, which
// is why "sending anything fixes them") ─────────────────────────────────────────────────────────

test("an UNVERIFIED failure never self-removes — it hides, registers, and unhides on a later success", () => {
  // box.remove() erased the figure's spot permanently: no failedPreviews registration, nothing for
  // the push-heal to retry. Every file:// mention and every no-pathLinks payload is unverified, so
  // ONE transient failure in a kernel-restart window meant a blank figure until the next send.
  // Scoped to the MANAGED machinery (previewFull and below). previewThumb came back with the feed's
  // artifact strips, and a thumb is not healable — a missing file costs its strip slot nothing, so it
  // still self-removes on error. The ban is about figures that must survive a transient failure.
  assert.doesNotMatch(PREVIEW.slice(PREVIEW.indexOf("export function previewFull")), /box\.remove\(\)/,
    "the self-remove is gone from every healable failure path");
  // the happy-path onerror and the managed catch both hide instead (same retry machinery as verified)
  assert.match(PREVIEW, /if \(!verified\) box\.style\.display = "none";\s*\n\s*failAfterBeat\(0\);/);
  assert.match(PREVIEW, /if \(!verified\) box\.style\.display = "none";\s*\/\/ hidden while failed, healable — never removed\s*\n\s*failAfterBeat\(started\);/);
  // …and both success paths unhide the healed sentinel in place
  assert.equal((PREVIEW.match(/box\.style\.display = "";\s+\/\/ a hidden unverified sentinel that healed comes back/g) || []).length, 2,
    "the resolved-url fast path AND the managed success both unhide");
  // the PDF card's HEAD probe re-registers itself for the heal on every failure, and unhides on ok
  assert.match(PREVIEW, /const probe = \(\) => fetch\(fileUrl\(path, sid\), \{ method: "HEAD" \}\)/);
  assert.match(PREVIEW, /box\.style\.display = "none"; failedPreviews\.set\(box, probe\);/);
});

test("a settled chip re-registers for RECONNECT-class heals regardless of error text", () => {
  // the push-heal stays new-evidence-gated (the pin above), but a byte-identical 404 while the file
  // was still being written — or a constant connection-refused — parked the chip inert forever. The
  // link coming back (romp:wsup / hostUp) is new information even when the words didn't change; the
  // budget refills exactly like a send's fresh box (autoRetries = 3, not the push-heal's 1).
  assert.match(PREVIEW, /settledPreviews\.set\(box, \(\) => \{ chipHealedErr = lastErr; autoRetries = 3; build\(true\); \}\);/);
  assert.match(PREVIEW, /const settledPreviews = new Map<HTMLElement, \(\) => void>\(\);/);
  assert.match(PREVIEW, /export function refreshSettledPreviews\(\): void \{/);
  assert.match(PREVIEW, /settledPreviews\.delete\(box\);\s*\/\/ one attempt per registration; re-registers on error/);
});

test("markdown-inline <img> failures register through ONE capture-phase listener", () => {
  // DOMPurify strips inline handlers (correctly) and md() returns a string, so per-site onerror
  // wiring is impossible — a failed md img sat as a dead element in the cached DOM forever. Error
  // events don't bubble but DO capture: one document-level listener covers every md img, skips the
  // preview machinery's own imgs (they run budgets/resume/chips), and rides failedPreviews so every
  // kernel message re-attempts.
  assert.match(PREVIEW, /export function installMdImgHeal\(\): void \{/);
  assert.match(PREVIEW, /document\.addEventListener\("error", \(e\) => \{/);
  assert.match(PREVIEW, /\}, true\);\s*\n\}/, "capture phase — error events do not bubble");
  assert.match(PREVIEW, /if \(!src \|\| src\.startsWith\("data:"\)\) return;/, "a broken data: URI has no server to heal");
  assert.match(PREVIEW, /if \(img\.onerror \|\| img\.closest\("\.path-full"\)\) return;/, "previews own their own retries");
  assert.match(PREVIEW, /if \(mdImgHealOn\) return;/, "ensure-once");
});
