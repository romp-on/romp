// The "queued" indicator (the user's messages submitted while a session is still working). It's the SAME
// generic {kind:"queued"} ChatEvent for BOTH backends — the kernel feeds it from the transcript queue-ops
// for tmux and from SdkBackend.pending_queued for SDK (business 2026-06-23). So pinning the one render path
// confirms the dot shows for either backend. The renderer has no jsdom harness, so pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a queued ChatEvent carries the pending messages (backend-agnostic, per-message md)", () => {
  // idx = backend-queue position (SDK); park = _pending_ops position (compaction/model parking, any backend)
  // `optimistic` (romp's own unconfirmed echo) rides along at the end — see optimistic-send.test.ts
  assert.match(RENDER, /kind: "queued"; texts: \{ md: string; followUp\?: boolean; goal\?: string; fuCtx\?: string; idx\?: number; park\?: number; cancelable\?: boolean; optimistic\?: boolean; imgPaths\?: string\[\] \}\[\]/);   // imgPaths: the echo's dragged-image thumbnails (2026-08-25)
});

test("renderQueued draws a wireframe-hourglass header (singular/plural) + one markdown bubble per queued message", () => {
  assert.match(RENDER, /ev\.kind === "queued"\) return renderQueued\(ev\)/);
  assert.match(RENDER, /el\("div", "turn turn-queued"\)/);
  // header: a stroked accent-blue hourglass ICON (no ⌛ emoji) + "N queued message(s)" — pluralizes on count
  assert.doesNotMatch(RENDER, /⌛/, "no hourglass emoji — it clashes with the app's line-icon style");
  assert.match(RENDER, /head\.appendChild\(hourglassIcon\(\)\)/);
  assert.match(RENDER, /function hourglassIcon\(\): HTMLElement/);
  assert.match(RENDER, /stroke="currentColor"[\s\S]*?<path d="M4 3 H12 L8 8 L12 13 H4 L8 8 Z"\/>/, "wireframe hourglass path");
  // noun matches the content: all-commands → "command", all-prose → "message", mixed → "item" (the user 2026-07-01)
  assert.match(RENDER, /const noun = nCmd === n \? "command" : nCmd === 0 \? "message" : "item";/);
  assert.match(RENDER, /return `\$\{n\} queued \$\{noun\}\$\{n === 1 \? "" : "s"\}`/);
  assert.match(RENDER, /label\.textContent = queuedCountText\(n, nCmd\) \+ why;/);
  assert.match(RENDER, /el\("div", "queued-head"\)/);
  // one faint "you" bubble per pending message, rendered as markdown (like a landed message)
  assert.match(RENDER, /for \(const t of ev\.texts\)[\s\S]*?el\("div", "queued-bubble md" \+ \(t\.cancelable \? " cancelable" : ""\)\)/);
  assert.match(RENDER, /if \(!isCmd\) bubble\.innerHTML = md\(t\.md\)/);
});

test("a queued slash command renders as a command chip, not a plain 'message' (the user 2026-07-01)", () => {
  // the SAME helper the landed user turn uses, so a queued /compact reads as a COMMAND
  assert.match(RENDER, /function renderSlashCmd\(bubble: HTMLElement, text: string\): boolean/);
  assert.match(RENDER, /el\("span", "slash-cmd-chip"\)/);
  // the header counts commands vs. prose to pick the noun
  assert.match(RENDER, /const nCmd = ev\.texts\.filter\(\(t\) => SLASH_CMD_RE\.test\(t\.md\)\)\.length;/);
});

test("a cancelable queued bubble carries an explicit ✕ — messages AND parked commands (the user 2026-07-08)", () => {
  // both queues cancel: the backend's own (idx) and ops parked during compaction/model switches (park)
  assert.match(RENDER, /if \(t\.cancelable && \(t\.idx !== undefined \|\| t\.park !== undefined\)\)/);
  assert.match(RENDER, /el\("button", "queued-x"\)/);
  assert.match(RENDER, /x\.dataset\.act = "qx";/, "the ✕ routes through the stable document.body delegate");
  assert.match(RENDER, /if \(t\.idx !== undefined\) x\.dataset\.qidx = String\(t\.idx\);/);
  assert.match(RENDER, /if \(t\.park !== undefined\) x\.dataset\.qpark = String\(t\.park\);/);
  // the OLD whole-bubble click is gone — it was undiscoverable and a per-render listener (mid-press
  // rebuilds ate the click); the bubble itself must carry no listener now
  assert.doesNotMatch(RENDER, /bubble\.addEventListener\("click"/);
  assert.doesNotMatch(CSS, /\.queued-bubble\.cancelable \{ cursor: pointer/);
  assert.match(CSS, /\.queued-x \{/);
  assert.match(CSS, /\.queued-x:hover \{ color: var\(--vscode-errorForeground/, "red on hover = the remove reading");
});

test("the delegated qx handler cancels click-safely: kernel op + composer restore for messages only", () => {
  // one handler on document.body (stable across every per-push rebuild) — never a per-render listener
  assert.match(RENDER, /qx: \(el\) => \{/);
  assert.match(RENDER, /\{ type: "cancelQueued", id: activeId, md: qmd \}/, "the body rides along as the kernel's drift guard");
  assert.match(RENDER, /if \(el\.dataset\.qidx !== undefined\) msg\.idx = Number\(el\.dataset\.qidx\);/);
  assert.match(RENDER, /if \(el\.dataset\.qpark !== undefined\) msg\.park = Number\(el\.dataset\.qpark\);/);
  // a MESSAGE returns to the composer to re-edit; a slash COMMAND (qcmd) just cancels
  assert.match(RENDER, /if \(qmd && el\.dataset\.qcmd !== "1"\) \{/);
  assert.match(RENDER, /restoreToComposer\(qmd\);/);
  assert.match(RENDER, /const bub = el\.closest\("\.queued-bubble"\) as HTMLElement \| null;[\s\S]*?bub\?\.remove\(\);/,
    "optimistic removal before the next push");
  // restoreToComposer fills the composer textarea, fires input (autosize/enable), focuses, caret to end
  assert.match(RENDER, /function restoreToComposer\(text: string\)/);
  assert.match(RENDER, /getElementById\("composer-input"\)/);
  assert.match(RENDER, /dispatchEvent\(new Event\("input"/);
});

// ---- the loud already-delivered failure (the user 2026-07-20) ----------------------------------------
// A ✕ whose target had already been HANDED TO THE CLI (the SDK forwards queued sends mid-turn; no recall
// exists in the control protocol — the CLI folds its queue into the running turn) used to silently no-op
// kernel-side while the client removed the bubble and restored the text: a fake delete, answered anyway.

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDKBE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the kernel answers every cancelQueued with an authoritative cancelResult frame", () => {
  assert.equal(KERNEL.split('"type": "cancelResult"').length - 1, 2, "one reply per cancel arm (park + idx)");
  assert.match(KERNEL, /"ok": not err/);
  assert.match(KERNEL, /def _cancel_miss_text\(md\):/, "the 'too late' wording is built kernel-side");
  assert.match(KERNEL, /too late to cancel — the message already reached the session/);
});

test("the ✕ only renders while a recall can still win (queue_recallable gates cancelable)", () => {
  assert.match(KERNEL, /cancelable = hasattr\(_cbe, "unqueue"\) and _queue_recallable\(_cbe, sid\)/);
  assert.match(SDKBE, /def queue_recallable\(self, sid: str\) -> bool:/);
  assert.match(SDKBE, /def unqueue\(self, idx: int, expect: str \| None = None\)/,
    "the pop re-verifies the exact text under the session lock — never a wrong-message cancel");
});

test("a queued bubble with no ✕ says where the message actually is", () => {
  assert.match(RENDER, /else if \(!t\.cancelable && t\.idx !== undefined\)/);
  assert.match(RENDER, /queued in the session — it can't be recalled, and joins the conversation at the session's next step/);
});

test("the qx click stashes the composer before/after so a failed cancel can undo the restore", () => {
  assert.match(RENDER, /const pendingCancelRestores = new Map<string, \{ before: string; after: string \}>\(\);/);
  assert.match(RENDER, /pendingCancelRestores\.set\(activeId \+ " " \+ qmd, \{ before, after: ta \? ta\.value : "" \}\);/);
});

test("cancelResult ok:false toasts the kernel's 'too late' and reverts an untouched composer restore", () => {
  assert.match(RENDER, /m\.type === "cancelResult" && typeof m\.id === "string"/);
  assert.match(RENDER, /pendingCancelRestores\.delete\(key\);/, "the stash is one-shot, ok or not");
  assert.match(RENDER, /if \(typeof m\.text === "string" && m\.text\) warnToast\(m\.text\);/);
  // the undo fires ONLY when the draft still exactly equals the post-restore value — an edited draft
  // is the user's now; the toast alone covers it
  assert.match(RENDER, /if \(ta && ta\.value === stash\.after\) \{[\s\S]*?ta\.value = stash\.before;/);
});

// Executed replica of the untouched-draft guard — the one branchy bit worth running, not just pinning.
test("the revert guard: untouched drafts revert, edited drafts stay", () => {
  const revert = (value: string, stash: { before: string; after: string }): string =>
    value === stash.after ? stash.before : value;
  const stash = { before: "", after: "forget the obsidian thing" };
  assert.equal(revert("forget the obsidian thing", stash), "", "untouched → back to the pre-click draft");
  assert.equal(revert("forget the obsidian thing, actually keep it", stash),
    "forget the obsidian thing, actually keep it", "edited → the user owns it now");
});

// ---- the ✕'d message that stayed on screen (the user 2026-07-24) -------------------------------------
// Cancelling the only queued message left its group behind: the header sat there alone still reading
// "1 queued message" with no bubble under it, and it never went away. Three separate holes, one per test.

test("the ✕ reflows the GROUP, not just the bubble — the last one out takes the header with it", () => {
  assert.match(RENDER, /function reflowQueuedGroup\(turn: HTMLElement\): void/);
  assert.match(RENDER, /if \(grp\) reflowQueuedGroup\(grp\);/, "called from the qx handler in the same breath");
  assert.match(RENDER, /if \(!bubbles\.length\) \{ turn\.remove\(\); return; \}/, "empty group → the whole turn goes");
  // still-populated group → the count is rewritten from what's actually left, keeping the held/ask suffix
  assert.match(RENDER, /label\.textContent = queuedCountText\(bubbles\.length, nCmd\) \+ \(label\.dataset\.why \|\| ""\);/);
  assert.match(RENDER, /label\.dataset\.why = why;/, "renderQueued parks the suffix for the recount to reuse");
});

// Executed replica of the recount rule the ✕ and renderQueued share — the branchy bit, run not just pinned.
test("queuedCountText: the noun follows what's left after a cancel", () => {
  const countText = (n: number, nCmd: number): string => {
    const noun = nCmd === n ? "command" : nCmd === 0 ? "message" : "item";
    return `${n} queued ${noun}${n === 1 ? "" : "s"}`;
  };
  assert.equal(countText(2, 0), "2 queued messages");
  assert.equal(countText(1, 0), "1 queued message", "singular after cancelling one of two");
  assert.equal(countText(1, 1), "1 queued command", "the prose one went → the noun follows");
  assert.equal(countText(2, 1), "2 queued items", "mixed stays 'items'");
});

test("a chatTail that SHRINKS the transcript repaints (the no-op fast path can't be trusted there)", () => {
  // The kernel's diff lands on `from === new length` when the tail simply lost an event, so lowering
  // v.rendered to `from` leaves rendered === len and syncView's fast path skips the repaint — the retired
  // turn stays in the DOM for good. The shrink is measured BEFORE the reconciles so it reflects the splice.
  assert.match(RENDER, /const wasLen = s\.events\.length;[\s\S]*?s\.events\.length = from;/);
  assert.match(RENDER, /const shrank = s\.events\.length < wasLen;/);
  assert.match(RENDER, /v\.rendered = Math\.min\(v\.rendered, from\);[\s\S]*?if \(shrank\) v\.stale = true;/);
  // the fast path this defends against — pinned so a rewrite of it can't silently reopen the hole
  assert.match(RENDER, /if \(v\.rendered === len && !v\.stale && v\.el\.childNodes\.length > 0\) return v;/);
});

test("a FAILED cancel puts the bubble back, so the screen agrees with the 'too late' toast", () => {
  // ok:false means the message is still going through, but the kernel's build never changed — so its next
  // delta carries no repaint and the optimistic delete would stand: shown as cancelled, answered anyway.
  assert.match(RENDER, /const rv = m\.id === activeId && activeId \? views\.get\(activeId\) : null;\s*\n\s*if \(rv\) \{ rv\.stale = true; appendActive\(\); \}/);
});

test("the queued-header hourglass uses the accent blue, like the feed/mail toggle icons", () => {
  assert.match(CSS, /\.queued-head \.queued-icon \{ color: var\(--accent\)/);
});

test("the queued turn + bubbles are styled (so the dot is actually visible)", () => {
  assert.match(CSS, /\.turn-queued/);
  assert.match(CSS, /\.queued-head/);
  assert.match(CSS, /\.queued-bubble/);
});

// ---- a queue the ACCOUNT is holding (the user 2026-07-24) --------------------------------------------
// Hitting a usage limit turned every following gesture into its own failure: /compact came back refused
// ("this would take you over your limit"), the message typed after it went straight out and landed as a red
// API-error card, and the order the user meant to say things in was lost. Now a limit parks it ALL in the
// same FIFO a compaction parks it in — messages and slash commands alike, since the limit is on the ACCOUNT
// — and the queued bubbles say what they are waiting for instead of just sitting there.

test("the kernel parks every drive op while the account can't serve one, and drains at the reset", () => {
  assert.match(KERNEL, /def _limit_hold\(sid\):/);
  assert.match(KERNEL, /or _limit_hold\(sid\) is not None\)/, "the gate /model, /effort and /compact pass");
  // the send path needs its OWN arm, ahead of the forwards_sends handoff: an SDK backend takes a send even
  // mid-turn, so without this the message goes straight out and comes back an API error
  assert.match(KERNEL, /if _compacting_now\(sid\) or _pending_ops\.get\(sid\) or _limit_hold\(sid\):/);
  assert.match(KERNEL, /if _compacting_now\(sid\) or _working_now\(sid\) or _limit_hold\(sid\):/, "the drain gate");
  // RELEASE rides the API's own stamp — no romp-invented timer, and no clock promised without one
  assert.match(KERNEL, /"resetsAt": max\(known\) if len\(known\) == len\(resets\) else None,/);
  assert.match(KERNEL, /known = \[r for r in resets if isinstance\(r, \(int, float\)\) and r > 0\]/);
});

test("a held queue says WHY it isn't moving, and outranks the pending-ask note", () => {
  assert.match(RENDER, /held\?: \{ reason: string; resetsAt\?: number \| null; what: string; detail\?: string \}/);
  // `detail` carries the CLI's own sentence when the limit REFUSED THE LAUNCH (the user 2026-07-28) —
  // that flavor reports a wall-clock reset, not an epoch, so it has no countdown to render and the exact
  // words go one level deeper instead of into the one-line head.
  assert.match(RENDER, /if \(held\?\.detail\) label\.title = held\.detail;/);
  assert.match(RENDER, /const askNote = \(pendingAsk \? " · sends after you answer" : ""\);/);
  assert.match(RENDER, /\? ` · \$\{held\.what\}` \+ \(held\.resetsAt \? ` · in \$\{fmtReset\(held\.resetsAt, Math\.floor\(Date\.now\(\) \/ 1000\)\)\}` : ""\)/);
});

// Executed replica of the head-suffix decision — the branchy bit, run rather than only pinned.
test("the head suffix: the hold beats the ask note, and no reset stamp means no countdown", () => {
  const suffix = (held: { what: string; resetsAt?: number | null } | undefined, pendingAsk: boolean) => {
    const askNote = (pendingAsk ? " · sends after you answer" : "");
    return held ? ` · ${held.what}` + (held.resetsAt ? " · in 42m" : "") : askNote;
  };
  assert.equal(suffix(undefined, false), "");
  assert.equal(suffix(undefined, true), " · sends after you answer");
  assert.equal(suffix({ what: "waiting for your usage limit to reset", resetsAt: 1784930000 }, true),
    " · waiting for your usage limit to reset · in 42m",
    "the limit is what the queue waits on, not the question — answering it would move nothing");
  assert.equal(suffix({ what: "waiting for your monthly spend limit to be raised", resetsAt: null }, false),
    " · waiting for your monthly spend limit to be raised",
    "a spend cap has no readable reset — say the reason, promise no clock");
});
