// The SEEK (the user 2026-08-25): clicking a distilled summary sometimes needed a second click, and
// nothing said a jump was in progress. The give-up underneath: landActive ran ONE scrollToAnchor
// attempt per pass and then nulled pendingAnchor unconditionally — the "stash for the next render
// pass" was wiped in the same breath, so a transient miss (a re-query racing the window re-render
// it had just asked for) one-shotted the navigation into the couldn't-locate toast; the second
// click "worked" because the first click's re-render had made the target resident. The seek is now
// durable state retried on every render pass (events, never timers), with a pane-local
// "finding the passage…" notice + cancel ✕ while it outlives the immediate landing. Source pins;
// the lifecycle is exercised headless over the built bundle (see the PR).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("one click lands or indicates: the seek survives a missed pass and retries on the next", () => {
  // durable state, armed at the ONE navigation entry (setActive) …
  assert.match(RENDER, /if \(anchor\) armSeek\(id, anchor, anchorKind \?\? null\);/);
  // …re-arming the per-pass attempt — never hijacking a scroll-back keep-offset restore
  assert.match(RENDER, /if \(!pendingAnchor && pendingAnchorT == null && pendingAnchorKeepY == null && seek && seek\.sid === activeId\) \{/);
  // the landing event clears it; a miss shows the notice instead of dying
  assert.match(RENDER, /if \(scrolled\) clearSeek\(\);/);
  assert.match(RENDER, /else showSeekNote\(\);/);
  // the one-shot toast yields to a live seek — the backstop owns the honest failure
  assert.match(RENDER, /&& !\(seek && att\.anchor === seek\.uuid\)\) \{/);
  assert.match(RENDER, /const SEEK_BACKSTOP_MS = 30_000;/);
  assert.match(RENDER, /seekBackstop = window\.setTimeout\(\(\) => failSeek\(\), SEEK_BACKSTOP_MS\);/);
  const fail = RENDER.split("function failSeek(")[1].split("\nfunction ")[0];
  assert.ok(fail.includes('landToast("couldn\'t locate this in the transcript")'), "the backstop is loud");
  assert.ok(fail.includes('notifyShell("locate"'), "…and files the error-center entry");
});

test("same-target clicks are idempotent; a different target supersedes cleanly", () => {
  assert.match(RENDER, /if \(seek && seek\.sid === sid && seek\.uuid === uuid\) return;\s*\/\/ same target mid-seek: idempotent, never a restart/);
  assert.match(RENDER, /clearSeek\(\);\s*\/\/ a different target supersedes cleanly/);
});

test("cancel leaves the reader exactly where they are — the seek truly dead", () => {
  const cancel = RENDER.split("function cancelSeek(")[1].split("\nfunction ")[0];
  assert.ok(cancel.includes("pendingAnchor = null;"), "no pending attempt survives the ✕");
  assert.ok(cancel.includes("releaseSeekFetch(sid);"), "an in-flight chunk arrives as a pure prepend");
  // the release re-points the arrival at the reader's OWN row + offset (the scroll-back idiom) —
  // never the abandoned target, so the landing chunk can't yank the scroll
  assert.match(RENDER, /if \(keep\) \{ pendingOlderAnchor\.set\(sid, keep\.uuid\); pendingOlderKeepY\.set\(sid, keep\.y\); return; \}/);
});

test("the notice is pane-local, quiet, and click-safe: created once per seek, ✕ = immediate removal", () => {
  assert.match(RENDER, /n\.id = "seek-note";/);
  assert.match(RENDER, /label\.textContent = "finding the passage…";/);
  assert.match(RENDER, /x\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); cancelSeek\(\); \}\);/);
  // created once per seek, never rebuilt by renders (the click-safety rule by construction)
  assert.match(RENDER, /if \(existing\) return;/);
  // shown only while the seeking session is the active tab
  assert.match(RENDER, /if \(seek\.sid !== activeId\) \{ existing\?\.remove\(\); return; \}/);
  // the dress: the menu-card vocabulary, fixed bottom-center — never a window blocker
  assert.match(CSS, /#seek-note \{\s*\n\s*position: fixed; left: 50%; bottom: 86px;/);
  assert.match(CSS, /#seek-note \.seek-note-x:hover \{ color: var\(--fg\); background: rgba\(255, 255, 255, 0\.08\); \}/);
});
