// Fork a session (the user 2026-08-13): a NEW parallel session branches from a chosen point — the
// hover "fork" BELOW each response run (the user 2026-08-19: forking conceptually cuts under the
// response, so the button left the prompt's msg-acts row) or from the tip (the palette's "Fork this
// session…", and the tip run's own spot); the parent
// is untouched and both continue as separate threads. The modal asks the new name — default
// "<session>-fork", editable — and the provisional tab is the instant acknowledgement, joined by NAME
// exactly like a picker create. Source-level pins (no jsdom for the chat renderer), plus the kernel
// side of the contract (node-tests-pin-kernel-source precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const PALETTE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the fork affordance rides BELOW each response run — delegated, with the cut the old bubble button passed", () => {
  // the spot map: the FIRST genuine editable prompt after a run is its cut; the tip run forks everything
  assert.match(RENDER, /function applyForkSpots\(sid: string, v: View\): void \{/);
  assert.match(RENDER, /&& senderKind\(ev\) === "user" && editable\?\.has\(ev\.uuid\)/);
  assert.match(RENDER, /if \(run && !spots\.has\(run\)\) spots\.set\(run, ""\);/);
  assert.match(RENDER, /\.turn-assistant\[data-uuid="\$\{cssEscape\(anchor\)\}"\]/);
  // …applied on the marks' hooks, like the branch chips (the transcript DOM rebuilds constantly)
  assert.match(RENDER, /applyForkSpots\(sid, v\);/);
});

test("the fork button sits INLINE, right of the worked-seconds label — never its own row below it", () => {
  // the user 2026-08-25: the elapsed footer is the flex host; a turn with no footer (the live tip)
  // keeps the button on its own row exactly as before. The remover walks closest(.turn-assistant)
  // because the spot may nest inside the elapsed row.
  assert.match(RENDER, /const elapsed = turn\.querySelector\(":scope > \.turn-elapsed"\) as HTMLElement \| null;/);
  assert.match(RENDER, /if \(elapsed\) elapsed\.appendChild\(row\);\s*\n\s*else turn\.appendChild\(row\);/);
  assert.match(RENDER, /const anchor = \(old\.closest\("\.turn-assistant"\) as HTMLElement \| null\)\?\.dataset\.uuid \|\| "";/);
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.turn-elapsed \{[^}]*display: flex; align-items: center; gap: 8px; min-width: 0;/s);
  assert.match(CSS, /\.turn-elapsed \.fork-spot \{ margin-top: 0; \}/);
  // the OLD home is gone: the prompt's msg-acts row no longer carries a fork (the user 2026-08-19)
  assert.doesNotMatch(RENDER, /acts\.appendChild\(fk\);/);
  // click-safe: the button is DELEGATED (data-act), landing in the shared modal with the spot's own cut
  assert.match(RENDER, /fk\.dataset\.act = "forkspot";/);
  assert.match(RENDER, /forkspot: \(elx\) => \{/);
  assert.match(RENDER, /showForkPrompt\(activeId, cut\);/);
  // hover the RESPONSE to reveal it; not a rewind: no two-click arm, and never the destructive red
  assert.match(CSS, /\.turn-assistant:hover \.msg-fork, \.msg-fork:focus-visible \{ opacity: 0\.9; \}/);
  assert.match(CSS, /\.msg-fork:hover \{ color: var\(--fg\); border-color: var\(--accent\); \}/);
  assert.doesNotMatch(CSS, /\.msg-fork\.armed/);
  // the under-bubble button family wears NEUTRAL chrome (the user 2026-08-23): it sits on the page
  // ground, where the terracotta code tint (--code-bg) read as a faint red button. Only .code-copy
  // keeps the tint — it sits ON the tinted code block and blends there.
  for (const block of [".msg-edit {", ".msg-del, .msg-restorefiles, .msg-fork {", ".undelivered-act {"]) {
    const body = CSS.slice(CSS.indexOf(block), CSS.indexOf("}", CSS.indexOf(block)));
    assert.ok(body.includes("background: rgba(255, 255, 255, 0.06)"), block + " wears the neutral ground");
    assert.ok(!body.includes("--code-bg"), block + " must not borrow the code tint");
  }
  const copy = CSS.slice(CSS.indexOf(".code-copy {"), CSS.indexOf("}", CSS.indexOf(".code-copy {")));
  assert.ok(copy.includes("var(--code-bg)"), ".code-copy stays tinted — it lives on the code block");
});

test("the modal defaults to <session>-fork and posts forkSession {id, uuid, name}", () => {
  assert.match(RENDER, /function showForkPrompt\(sid: string, uuid: string\): void \{/);
  assert.match(RENDER, /input\.value = base \+ "-fork";/);
  assert.match(RENDER, /if \(!\/\^\[A-Za-z0-9._-\]\+\$\/\.test\(name\)\) \{ input\.classList\.add\("bad"\); input\.focus\(\); return; \}/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "forkSession", id: sid, uuid, name \}\);/);
  // the instant acknowledgement is the provisional tab, name-joined like a picker create
  assert.match(RENDER, /openProvisional\(\{ name, backend: "sdk", dir: "", host: hostOf\(sid\) \}\);/);
  // both cut semantics are said in the dialog itself
  assert.match(RENDER, /continues the conversation to just below this response/);
  assert.match(RENDER, /continues this whole conversation/);
  assert.match(CSS, /\.fork-name \{ display: block; width: 100%;/);
});

test("the palette forks the ACTIVE session from the tip, via the chat pane", () => {
  assert.match(PALETTE, /id: "session\.fork", title: "Fork this session…"/);
  assert.match(PALETTE, /pane\("f-chat"\)!\.contentWindow!\.postMessage\(\{ romp: "forkSession" \}, "\*"\)/);
  assert.match(RENDER, /if \(m\.romp === "forkSession"\) \{/);
  assert.match(RENDER, /if \(activeId && !isProvisionalId\(activeId\) && sessions\.get\(activeId\)\) showForkPrompt\(activeId, ""\);/);
});

test("kernel: forkSession is a session op; seeding precedes discoverability; the fsid is pinned to the sid", () => {
  assert.match(KERNEL, /"mcpAction", "forkSession",/);   // in ID_OPS — routed by session id like every session op
  assert.match(KERNEL, /elif t == "forkSession" and msg\.get\("name"\):/);
  // client rides through so only the ASKING dashboard's chat follows the fork (the per-viewer rule)
  assert.match(KERNEL, /def _fork_session\(parent_sid, cut_msg_uuid, new_name, now=None, client=None\):/);
  // the cut means the same thing the edit/delete rewind means: just before the clicked user message
  assert.match(KERNEL, /cut_uuid, err = _rewind_target\(sess\["path"\], parent_sid, str\(cut_msg_uuid\)\)/);
  // the judge stores are seeded BEFORE be.fork writes the names/ entry (discoverability)
  assert.match(KERNEL, /err = _seed_fork_stores\(parent_sid, sid, sess\["path"\], cut_uuid\)[\s\S]{0,200}be\.fork\(nm, parent_sid, cut_uuid, bg, fg, sid=sid\)/);
  // the backend rides the SDK's designed fork contract, with the new fsid PINNED to the romp sid
  assert.match(BACKEND, /kw\["fork_session"\] = True/);
  assert.match(BACKEND, /"forkOf": parent_sid, "forkAt": cut_uuid or ""/);
  // one-shot: the init's lastSid flip spends the flags, so a reconnect resumes the fork's own transcript
  assert.match(BACKEND, /if self\._fork_of and fsid == self\.sid:/);
  assert.match(BACKEND, /self\.backend\._update_reg\(self\.sid, forkOf="", forkAt=""\)/);
  // …and the names/ entry is written LAST (it is the discoverability trigger)
  assert.match(BACKEND, /write_reg\(self\.state_dir, sid, reg\)[\s\S]{0,400}write_name\(self\.state_dir, sid, name, cwd, bg, fg\)[\s\S]{0,200}append_state\(self\.state_dir, sid, "waiting"\)/);
});

// ── branch lineage (the user 2026-08-13: branching must SHOW) ───────────────────────────────────

test("a forked session renders its branch divider, deep-linked to the parent", () => {
  assert.match(RENDER, /\| \{ kind: "branch"; fromSid\?: string; fromName\?: string; cut\?: string/);
  assert.match(RENDER, /if \(ev\.kind === "branch"\) \{/);
  assert.match(RENDER, /label\.dataset\.act = "branchjump"/);
  assert.match(RENDER, /"Branched from " \+ \(ev\.fromName \|\| "another session"\)/);
});

test("the parent wears a chip where each branch departed, jumping to the child's divider", () => {
  assert.match(RENDER, /function applyBranchChips\(sid: string, v: View\)/);
  assert.match(RENDER, /chip\.dataset\.cut = "branch:" \+ k\.cut/);
  assert.match(RENDER, /applyBranchChips\(sid, v\);\s+\/\/ same driver, same hooks/);
  assert.match(RENDER, /branchjump: \(elx\) =>/);
});

test("kernel persists lineage durably and serves it on the session payload", () => {
  // forkOf/forkAt are one-shot launch flags — forkedFrom is the durable record
  assert.match(BACKEND, /reg\["forkedFrom"\] = \{"sid": parent_sid, "name": parent\.get\("name", ""\)/);
  assert.match(BACKEND, /lineage_cut = cut_uuid or last_record_uuid\(/);
  assert.match(BACKEND, /def fork_children\(self\)/);
  assert.match(KERNEL, /"branch": branch, "branches": _kids,/);
  assert.match(KERNEL, /"kind": "branch", "uuid": "branch:" \+ branch\["cut"\]/);
});

test("branch chrome wears the accent, like every highlight", () => {
  const css = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(css, /\.branch-divider::before, \.branch-divider::after \{[^}]*var\(--accent\)/s);
  assert.match(css, /\.branch-chip \{[^}]*color: var\(--accent\)/s);
});
