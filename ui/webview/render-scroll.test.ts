// Chat scroll behavior (the user 2026-06-15): a new "session" push must NOT snap the view to the
// bottom. The kernel re-sends the FULL payload every push, so an APPEND (more turns on the same
// transcript) keeps the user's scroll position; only a FORK (the tab re-pointed onto a new
// transcript) rebuilds + lands at the bottom. The chat renderer has no jsdom harness, so — like the
// other render-*.test.ts / feed-*.test.ts files — pin the behavior at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("upsert tells append from fork by transcript OVERLAP, not the first uuid (survives a tail-window slide)", () => {
  // firstUuid mis-fired: once a session passes WIRE_TAIL, a full-session push re-windows to the last N, so the
  // first uuid changes though it's the same transcript → a false fork dropped the DOM + snapped to the bottom
  // (the user 2026-07-06). A fork = the events share NO uuid (a wholesale /clear-style replacement).
  assert.match(RENDER, /function sharesAnyUuid\(/, "a helper tells a continuation from a wholesale replacement");
  assert.match(RENDER, /const forked = [\s\S]*?!sharesAnyUuid\(msg\.events, prev\.events\)/,
    "fork = NO shared uuid, so a mere tail-window slide is NOT a fork");
});

test("a content refresh appends (preserves scroll); only a fork drops the DOM", () => {
  assert.match(RENDER, /if \(forked\) \{[\s\S]{0,120}?v\.el\.remove\(\)/,
    "the cached DOM is dropped only on a fork, not on every push");
  assert.match(RENDER, /if \(existed && !forked && !firstBuild\) \{\s*appendActive\(\);/,
    "a refresh of the active tab appends instead of snapping to the bottom");
});

test("a placeholder's first content-bearing build LANDS (bottom), it does not append (top)", () => {
  // A fork's provisional tab and a revive's stub hold zero events; the payload that fills them used
  // to route down the append path, whose overflow gate read the one-line placeholder as "not at
  // bottom" and left the whole arriving history at scrollTop 0 — with the never-yank rule then
  // holding the top forever (the user 2026-09-02: an opened/forked session sat at the top after its
  // context loaded). First build = prev had NO events and the payload brings some → showActive →
  // landActive pins the bottom exactly like a brand-new tab.
  assert.match(RENDER, /const firstBuild = !!\(existed && prev && !prev\.events\.length && msg\.events && msg\.events\.length\);/,
    "first build = the view's zero-event placeholder filling with its first real events");
  assert.match(RENDER, /if \(msg\.id === activeId && !\(existed && !forked && !firstBuild\)\) \{/,
    "the land branch's scroll capture covers the first build too");
});

test("a rebuild NEVER snaps a scrolled-up reader to the bottom — it captures + restores their anchor", () => {
  // even a genuine rebuild (new tab / fork / slid tail-window) preserves position for a scrolled-up reader:
  // capture nearBottom + the anchor BEFORE dropping the DOM, restore after (the user 2026-07-06). A true
  // fork's anchor uuid isn't in the new transcript, so restoreScrollAnchor no-ops → it stays at the bottom.
  assert.match(RENDER, /_wasNear = !_scrollContent \|\| !_v0 \|\| !_v0\.shown \|\| nearBottom\(_scrollContent\);/);
  assert.match(RENDER, /_scrollAnchor = \(!_wasNear && _scrollContent && _v0\) \? captureScrollAnchor\(_scrollContent, _v0\) : null;/);
  assert.match(RENDER, /if \(!_wasNear && _scrollAnchor && _scrollContent\) \{[\s\S]*?restoreScrollAnchor\(_scrollContent, v1, _scrollAnchor\)/);
});

// executed: mirror sharesAnyUuid to guard its intent — a slide/append shares uuids, a fork shares none
test("sharesAnyUuid: a continuation shares a uuid, a wholesale fork shares none", () => {
  const sharesAnyUuid = (a: { uuid?: string }[], b: { uuid?: string }[]): boolean => {
    const seen = new Set<string>();
    for (const e of b) if (e.uuid) seen.add(e.uuid);
    for (const e of a) if (e.uuid && seen.has(e.uuid)) return true;
    return false;
  };
  const slidWindow = [{ uuid: "c" }, { uuid: "d" }, { uuid: "e" }];   // window advanced: dropped a/b, kept c/d, added e
  const before = [{ uuid: "a" }, { uuid: "b" }, { uuid: "c" }, { uuid: "d" }];
  assert.equal(sharesAnyUuid(slidWindow, before), true, "a tail-window slide overlaps → NOT a fork");
  assert.equal(sharesAnyUuid([{ uuid: "z1" }, { uuid: "z2" }], before), false, "a /clear-style replacement → a fork");
  assert.equal(sharesAnyUuid([{}, {}], before), false, "no uuids to match (live tail-only) → treated as a fork-ish rebuild");
});

test("appendActive snaps only when the user is already near the bottom of OVERFLOWING content", () => {
  // the slack rule (the user 2026-08-25): while nothing overflows, nearBottom is trivially true —
  // ungated, the very append crossing the overflow boundary yanked the view; now streaming into
  // slack writes in place and grows the scrollbar, and the stick engages only once overflowing
  assert.match(RENDER, /const stick = content\.scrollHeight > content\.clientHeight \+ 2 && nearBottom\(content\);[\s\S]*?if \(stick\) content\.scrollTop = content\.scrollHeight/,
    "tail-append follows the live edge only if content overflows AND the reader was at the bottom");
  // the popover's thread list speaks the same rule
  assert.match(RENDER, /const overflowed = list\.scrollHeight > list\.clientHeight \+ 2;/);
  assert.match(RENDER, /const atTail = overflowed && list\.scrollTop >= list\.scrollHeight - list\.clientHeight - 8;/);
});

// Scrolled-up re-renders anchor on a TURN, not a pixel offset (the user 2026-07-05): chatTail deep-fills
// rewrite EARLIER cards in place — a running subagent's Task report above the viewport grows on every
// update — so restoring the raw scrollTop let the text being read drift. appendActive now captures the
// first turn visible at the viewport top (stable data-uuid) and puts THAT element back at its exact
// offset after the rebuild; the raw scrollTop remains only as the eviction fallback.
test("a scrolled-up append restores by turn ANCHOR (data-uuid), raw scrollTop only as fallback", () => {
  const fn = RENDER.slice(RENDER.indexOf("function appendActive"), RENDER.indexOf("window.addEventListener(\"resize\", scheduleRestamp)"));
  assert.match(fn, /const anchor = !stick && v \? captureScrollAnchor\(content, v\) : null;/,
    "the anchor is captured BEFORE the rebuild, only when scrolled up");
  assert.match(fn, /else if \(!\(v && restoreScrollAnchor\(content, v, anchor\)\)\) content\.scrollTop = before;/,
    "anchor-relative restore first; the raw pixel offset only when the anchor was evicted");
  assert.match(RENDER, /function captureScrollAnchor\(content: HTMLElement, v: View\)/);
  assert.match(RENDER, /r\.bottom > cTop \+ 1/, "the anchor is the first turn still visible at the viewport top");
  assert.match(RENDER, /querySelector\(`\[data-uuid="\$\{cssEscape\(a\.uuid\)\}"\]`\)/,
    "the anchor re-resolves by its stable uuid after the rebuild");
  assert.match(RENDER, /content\.scrollTop = yNow - a\.y;/, "the anchor turn keeps its exact on-screen offset");
});

// BY-ID landing only — NO time-based fallback anywhere (the user 2026-06-20, who wanted to shrink the 29%, then remove
// the time fallback). Prompt-intent jumps resolve by id (promptAnchorUuid → a user turn OR a peer's postal
// card, see the kind-guard test below); the genuinely-unanchorable (autonomous / pruned-or-compacted) honest-
// fail with a toast. The whole time-nearest mechanism (scrollToNearestT) is deleted.
test("scrollToNearestT is GONE — time never silently substitutes for an ID anchor", () => {
  assert.doesNotMatch(RENDER, /function scrollToNearestT\b/, "the time-nearest helper is deleted");
  assert.doesNotMatch(RENDER, /scrollToNearestT\(/, "nothing calls it");
  // landNearestMoment (2026-08-25) is NOT that tier reborn: it fires only when the navigation
  // carries NO id at all (anchorT-only producers — timeline lane clicks, deep links), so there is
  // no right turn for time to impersonate — and it announces itself with the honest note instead
  // of posing as an exact jump. An ID anchor that fails still never falls back to time:
  assert.match(RENDER, /if \(!scrolled && !att\.anchor && att\.t != null\) scrolled = landNearestMoment\(att\.t\);/);
});

test("a time-only navigation lands at the nearest moment and SAYS so — never the bare toast", () => {
  // the fifth can't-locate shape (the user 2026-08-25): anchor null + anchorT set dead-ended with
  // "couldn't locate" and an empty trail — the whole producer class (timeline/deep links/atomless
  // cards) could never land after the id-only hardening
  const fn = RENDER.split("function landNearestMoment(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes("const d = Math.abs(ep - t);"), "nearest by the anchor's own datum");
  assert.ok(fn.includes('landTrail.push("time-nearest");'), "the audit names the landing kind");
  assert.ok(fn.includes('"that link points at a time, not a message — landed at the closest one"'), "the honest note");
  assert.ok(fn.includes('"that link points at a moment before the loaded history — landed at the oldest loaded message"'),
    "…and the pre-history case names itself too");
  assert.ok(fn.includes("renderWindowItems(v, s, items,"), "off-window moments re-window like the id recovery");
});

test("the PROMPT-tier time fallback is removed — an unresolvable prompt anchor honest-fails (no clock-nearest)", () => {
  assert.doesNotMatch(RENDER, /pendingAnchorKind === "user" && pendingAnchorT != null/,
    "the nearest-USER-turn time fallback (8a24c16) is gone");
});

test("the kind guard accepts a peer's postal card as a valid PROMPT target (recovers peer openers by id)", () => {
  // a peer-opened node's promptAnchorUuid is the postal atom's uuid; the card is .turn-postal-service, not
  // .turn-user. The guard used to refuse it (→ the time fallback); now it accepts user OR postal, so the
  // click lands on the originating message BY ID — shrinking the ~29% before the fallback was removed (the user 2026-06-20).
  assert.match(RENDER, /pendingAnchorIntent === "user"\s+&& !target\.classList\.contains\("turn-user"\) && !target\.classList\.contains\("turn-postal-service"\)/);
});

test("honest-fail fires whenever the deep-link can't resolve by id (the turn is genuinely gone)", () => {
  // now gated on !anchorPendingOlder so it doesn't fire while we're fetching older history for the anchor —
  // and on !att.keep, since a scroll-back position restore is nobody's navigation (chat-older-restore.test.ts)
  assert.match(RENDER, /if \(!scrolled && !anchorPendingOlder && !att\.keep && !\(seek && att\.anchor === seek\.uuid\)\) \{[^\n]*\n\s*landToast\("couldn't locate this in the transcript"\)/);   // a live SEEK retries instead; its backstop owns the failure (2026-08-25)
  // 2026-07-28: the same failure ALSO files an error-center entry — a transient toast left nothing the
  // user could point at once it faded (the full bridge is pinned in chat-delta-resync.test.ts).
  assert.match(RENDER, /notifyShell\("locate",/);
});

test("a deep-link to an anchor OLDER than the resident tail fetches older history, then lands (the user 2026-06-27)", () => {
  // the bug: the chat ships only WIRE_TAIL events; an anchor past the tail wasn't in s.events, so
  // scrollToAnchor honest-failed even though the message is in the transcript. Fix: fetch the older chunk
  // re-anchored on the uuid (chatHead re-lands), looping until resident or headFrom hits 0.
  assert.match(RENDER, /else if \(s && \(s\.headFrom \?\? 0\) > 0\) \{/, "older-than-tail branch in scrollToAnchor");
  // 2026-07-20: an in-flight fetch counts as pending too (loadingOlder), and the arrival re-land is
  // re-pointed at THIS uuid — a push re-render mid-fetch used to fall through to a false "couldn't locate"
  // 2026-08-02: …and it drops any keep-offset the in-flight fetch left, so a click can't be demoted to a
  // scroll-back position restore (chat-older-restore.test.ts).
  assert.match(RENDER, /if \(fetchOlderForAnchor\(activeId, uuid\) \|\| loadingOlder\.has\(activeId\)\) \{\s*pendingOlderAnchor\.set\(activeId, uuid\);\s*pendingOlderKeepY\.delete\(activeId\);\s*pendingAnchor = uuid; anchorPendingOlder = true;/);
  // the helper stashes the TARGET uuid (not the current top row) so chatHead lands on it
  assert.match(RENDER, /function fetchOlderForAnchor\(sid: string, uuid: string\): boolean/);
  assert.match(RENDER, /pendingOlderAnchor\.set\(sid, uuid\)/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "loadOlder", id: sid, before: s\.headFrom \}\)/);
  // the flag is reset at the start of each attempt so it can't leak a stale "fetching" state
  assert.match(RENDER, /anchorPendingOlder = false;\s+\/\/ fresh attempt/);
});

// (The two ledger-zone deep-link tests were removed 2026-06-24 with the in-chat ledger box itself —
//  the per-session digest now lives in the tab tooltip + Fleet. scrollToNearestT stays deleted.)

test("timeline→chat glow matches turns BY UUID, not a ±2s time window (the user 2026-06-19)", () => {
  // applyGlow lights .turn[data-uuid] against the segment's atom uuids the kernel sends (kernel
  // _segment_atom_uuids); the old data-t range match was a flaky time heuristic and is gone.
  assert.match(RENDER, /function applyGlow\(groups: Array<\{ sid: string; uuids: string\[\] \}>/);
  assert.match(RENDER, /uset\.has\(n\.dataset\.uuid \|\| ""\)/, "glow matches by uuid set");
  assert.doesNotMatch(RENDER, /t >= s - 2 && t <= e \+ 2/, "the old ±2s data-t window match is gone");
});


test("a postal deep-link resolves to the message's card BY data-mid, not just data-uuid (the user 2026-06-20)", () => {
  // the timeline connector / feed delegation passes the postal message id as the anchor; postal cards carry
  // data-mid, so scrollToAnchor matches it to the EXACT card instead of falling through to a nearest-time
  // guess that drifts onto whatever turn was closest in time (e.g. a 'retry' the user typed nearby).
  assert.match(RENDER, /querySelector\(`\.turn\[data-mid="\$\{cssEscape\(uuid\)\}"\]`\)/,
    "scrollToAnchor resolves a postal anchor by data-mid");
});

test("a deep-link into a HIDDEN chat pane defers the land until the pane is visible (the user 2026-06-30)", () => {
  // landActive keeps the pending anchor and re-runs once the pane shows, instead of scrolling a 0-height view
  assert.match(RENDER, /if \(\(pendingAnchor \|\| pendingAnchorT != null\) && content\.clientHeight === 0\) \{/);
  assert.match(RENDER, /whenChatVisible\(\(\) => \{ const c = document\.getElementById\("content"\);/);
  // the visibility watch: ResizeObserver on #content catches the 0→height reflow, window resize as a fallback
  assert.match(RENDER, /function whenChatVisible\(cb: \(\) => void\): void/);
  assert.match(RENDER, /new ResizeObserver\(fire\)\.observe\(c\)/);
  assert.match(RENDER, /window\.addEventListener\("resize", fire\)/);
});

// Boxes above the transcript must not jump the chat text (the user 2026-06-30 for #tabbar; #ledger added
// 2026-07-05). Both are flex 0 0 auto directly above the flex 1 1 auto #content scroll area, so a working
// dot wrapping the strip — or a ledger item deepening the summary box — shoves #content and every line
// under it down. A ResizeObserver per box cancels that by shifting #content.scrollTop by the same height
// delta (unless stuck to the bottom / pane hidden). Source-pin.
test("ResizeObservers on #tabbar AND #ledger compensate #content.scrollTop by the height delta", () => {
  assert.match(RENDER, /for \(const boxId of \["tabbar", "ledger"\]\)/, "both boxes above the transcript are observed");
  assert.match(RENDER, /const tro = new ResizeObserver/, "via a dedicated per-box ResizeObserver");
  // shift scrollTop by (new - old) box height — only when not stuck to bottom and the pane is visible
  assert.match(RENDER, /content\.clientHeight > 0 && !nearBottom\(content\)/,
    "skipped when stuck to the bottom or the pane is hidden");
  assert.match(RENDER, /content\.scrollTop \+= h - lastH/, "compensates by the exact height delta");
  assert.match(RENDER, /if \(v\) v\.scrollTop = content\.scrollTop/, "keeps the per-view saved scroll in sync");
});

test("a focus with `live` lands on the live tail — a blocked card's picker/permission prompt (the user 2026-07-08)", () => {
  // stick the target view to bottom so showActive scrolls there
  assert.match(RENDER, /if \(m\.live\) \{ const v = views\.get\(m\.id\); if \(v\) v\.stick = true; \}/);
  // cover the ALREADY-ACTIVE case, where setActive early-returns (activeId === id, no anchor) → jump to
  // bottom ONE FRAME later (the user 2026-08-13): when this focus is what un-hid a closed pane, the
  // synchronous scroll ran at display:none (scrollHeight 0) and the jump read as a no-op
  assert.match(RENDER, /if \(m\.live && activeId === m\.id\) \{[\s\S]{0,700}?window\.requestAnimationFrame\(\(\) => \{\s*\n\s*const c = document\.getElementById\("content"\); if \(c\) c\.scrollTop = c\.scrollHeight;/);
});
