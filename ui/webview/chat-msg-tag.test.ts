// A sender-declared RENDER HINT on an injected message (the user 2026-08-15, via the session that
// launches experiment runs): auto-generated text — a kickoff template, a scripted brief — used to
// render as the user's own blue bubble, posing machine text as typed words. A message carrying
// `<!-- romp-tag: <label> -->` (romp send --tag, or the marker appended by hand) now renders as a
// third class: the gray injected family, labeled "⚙ <label>" with the SENDER's own word — romp
// attaches no meaning to the label (a render hint, not a hard-coded message type, so the mechanism
// stays agnostic to any one workflow). Long templates fold to their own first line, nudge-style.
// Source pins (no jsdom for the chat renderer) + the kernel/event_model side of the contract.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const EM = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "event_model.py"), "utf8");
const CLI = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("event_model: the marker is a comment-form regex with a bounded one-word label", () => {
  assert.match(EM, /MSG_TAG_RE = re\.compile\(r"<!--\\s\*romp-tag:\\s\*\(\[A-Za-z0-9\]\[A-Za-z0-9-\]\{0,23\}\)\\s\*-->"\)/,
    "comment form only — content merely mentioning romp-tag must not match (the ROMP_INJECT_RE rule)");
});

test("kernel: the tag lifts on HUMAN-author turns only, riding the user event", () => {
  assert.match(KERNEL, /if author == "human":\s*\n\s+mtag = em\.MSG_TAG_RE\.search\(text\)/);
  assert.match(KERNEL, /ev\["tag"\] = mtag\.group\(1\)/);
});

test("chat: a tagged message wears the gray injected family with the sender's ⚙ label, never user blue", () => {
  assert.match(RENDER, /tag\?: string/, "the user event carries the kernel's lift");
  // the predicate lives in sender-identity.ts since 2026-08-18 (ONE classifier for bubble, dot,
  // and notch); render.ts derives its flags from that one verdict
  assert.match(RENDER, /const tagged = kind === "tagged";/);
  // the rail dot is its own identity channel and must AGREE with the bubble (2026-08-18): a tagged
  // machine-sent message wears the gray tag dot, never the blue "you typed this" one — so `tagged`
  // is hoisted above the dot call
  assert.match(RENDER, /turn\.appendChild\(dot\(romp \? "romp" : tagged \? "tag" : injected \? "ring" : "user"\)\);/);
  assert.ok(RENDER.indexOf('const tagged = kind === "tagged"') < RENDER.indexOf('dot(romp ? "romp" : tagged'),
    "tagged must be declared before the dot call reads it");
  assert.match(CSS, /\.dot\.tag \{ background: #8a8f98; border: none; \}/);
  // the label chip reuses the romp-tag dress (one vocabulary), ⚙ marking "scripted" vs romp's swirl
  // 2026-09-08 (the notice-vocabulary pass): the ⚙ text glyph is the gear LINE icon at the source-label size; the
  // injected (harness) line is a system notice, not the .user-note box
  assert.match(RENDER, /tchip\.appendChild\(noticeGlyph\("system"\)\);[^\n]*\n\s*tchip\.appendChild\(document\.createTextNode\(ev\.tag \|\| ""\)\);/);
  assert.match(RENDER, /tagged \? "romp-bubble tag-bubble" : injected \? "notice-md" : "user-bubble"/);
  // a tagged message is neither a slash-command row nor the canned Continue gesture
  assert.match(RENDER, /if \(!romp && !injected && !tagged && ev\.md && renderSlashCmd\(bubble, ev\.md\)\) \{/);
  assert.match(RENDER, /\} else if \(!romp && !injected && !tagged && ev\.md && ev\.canned === "continue"\) \{/);
});

test("chat: a long template folds to its OWN first non-quote line — nudge fold, keyed, delegated", () => {
  const branch = RENDER.slice(RENDER.indexOf("} else if (tagged) {"), RENDER.indexOf("} else if (romp && ev.md) {"));
  assert.ok(branch.length > 0, "the tagged branch sits between the canned and romp branches");
  assert.match(branch, /const first = lines\.find\(\(l\) => l && !l\.startsWith\(">"\)\) \|\| lines\.find\(\(l\) => l\) \|\| raw;/);
  assert.match(branch, /const more = collapseWs\(raw\) !== collapseWs\(gist\);/, "a one-liner never grows a dead caret");
  assert.match(branch, /bubble\.dataset\.act = "nudgetoggle";/, "the stable body delegate, never a per-render listener");
  assert.match(branch, /const tkey = ev\.uuid \? "tag:" \+ ev\.uuid : undefined;/, "keyed — the fold survives re-renders");
});

test("cli: romp send --tag is sugar for the marker, validated to one word", () => {
  // the trailing form loops since --from-user joined it (2026-10-06): either flag, in either order, after the session name
  assert.match(CLI, /while \[\[ "\$\{1:-\}" == "--tag" \|\| "\$\{1:-\}" == "--from-user" \]\]; do/);
  assert.match(CLI, /\[A-Za-z0-9\]\[A-Za-z0-9-\]\{0,23\}/, "the CLI enforces the same label shape the kernel lifts");
  assert.match(CLI, /<!-- romp-tag: \$_tag -->/);
});
