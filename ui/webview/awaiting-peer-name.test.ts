// Every peer-kind awaiting stamp names the ACTUAL session (the user 2026-08-26): 'a peer' is a bug
// to trace, not a style. The kernel now resolves identities through ONE ladder (_peer_identity:
// registry first — dormant sessions keep their names — then the cross-host pair, then the sid stub)
// and ships them on every arm; these pins hold the render half: the feed box's chips open the
// session, the chat box + chip name the peer in identity colour, and the fallback says WHY a name
// is missing instead of presenting 'peer' as a style.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const FEED = ui("webview", "feed.ts");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the feed box's peer chips are the standard session chip — click opens the session", () => {
  const box = FEED.slice(FEED.indexOf("const awPeers ="), FEED.indexOf("a._awaitSpin.title"));
  assert.match(box, /hostPartsNodes\(p\.host, p\.name\)/, "quiet host: prefix, the ↪ from treatment");
  assert.match(box, /nm\.style\.color = p\.color\.bg/, "identity colour");
  assert.match(box, /postMessage\(\{ type: "openSession", id: p\.sid \}\)/, "the handoffTo click idiom");
  assert.match(box, /nm\.style\.cursor = "pointer"/);
});

test("the feed's nameless peer wait explains itself — honest fallback, not a style", () => {
  assert.match(FEED,
    /if \(awaitingBg && !awPeers\.length && it\.awaiting && it\.awaiting\.kind === "peer"\)\s*\n\s*a\._awaitSpin\.title \+=/,
    "peer-kind with no names → the tooltip says the record predates capture or an older kernel shipped it");
});

test("the chat pane names the peer: box in identity colour, pill with the colour dot", () => {
  assert.match(RENDER, /awaitingPeers\?: PeerIdent\[\] \| null;/, "the payload field beside awaitingKind");
  assert.match(RENDER, /type PeerIdent = \{ name: string; host\?: string; sid\?: string; color\?: \{ bg: string; fg: string \} \| null \};/,
    "named alias — the Status interface line stays brace-free for its other pins");
  const boxAt = RENDER.indexOf("const awPeers = s!.status.awaitingPeers");
  const box = RENDER.slice(boxAt, RENDER.indexOf("head.appendChild(lab);", boxAt));
  assert.match(box, /el\("span", "bg-await-peer"\)/);
  assert.match(box, /\(pr\.host \? pr\.host \+ ":" : ""\) \+ pr\.name/, "host-prefixed when cross-host");
  assert.match(box, /nm\.style\.color = pr\.color\.bg/);
  const chipAt = RENDER.indexOf("const chipPeers = s.status.awaitingPeers");
  const chip = RENDER.slice(chipAt, RENDER.indexOf("chip.title =", chipAt));
  assert.match(chip, /el\("span", "chip-peer-dot"\)/, "'Awaiting <name>' wears the identity dot");
  assert.match(chip, /dot\.style\.background = chipPeers\[0\]\.color\.bg/);
  assert.match(chip, /chipPeers\.length \+ " peers"/, "several peers keep the one-line rule as a count");
  assert.match(CSS, /\.chip-peer-dot \{ display: inline-block; width: 7px; height: 7px; border-radius: 50%;/);
});

test("the kernel ships identities on every arm — the or-chain's hardcoded Nones are gone", () => {
  assert.match(KERNEL, /\(_stamp_why, _stamp_kind, _stamp_since, _stamp_peers\)/, "the judge-stamp arm");
  assert.match(KERNEL, /\(sess_awaiting_why, sess_awaiting_kind, sess_awaiting_since, sess_awaiting_peers\)/,
    "the session-snapshot arm");
  assert.match(KERNEL, /"awaitingPeers": \(\(_aw or \{\}\)\.get\("peers"\) or None\)/, "the chat status payload");
  assert.match(KERNEL, /"awaitingPeers": \(\(_aw_bg or \{\}\)\.get\("peers"\) or None\)/, "the timeline sessions payload");
  assert.match(KERNEL, /def _peer_identity\(psid\):/, "the ONE identity ladder");
  assert.ok(!/_name_of\(p\) or "a peer"/.test(KERNEL),
    "the bare registry read that named every cross-host delegation 'a peer' is gone");
  assert.match(KERNEL, /_hnodes = \[x for x in _open_leaves\(nodes, nid\)/,
    "the handoff scan walks the same open set its gate proved non-empty");
});
