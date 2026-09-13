// The chat tab menu's TAGS section (the user 2026-08-24, overruling the earlier skip: tag editing
// belongs everywhere a session is in front of you). Same semantics as the timeline dialog — the
// name-keyed union rules bind (kernels are plumbing, never a host prefix in presentation), edits
// reuse the wire (local = the whole blob via postViews; remote-homed = the editTag op family) —
// never a forked implementation. Executable union coverage + source pins (no jsdom for render.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewTagUnion } from "./session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("executable: the union joins local and remote tags BY NAME — one group, local id/colour winning", () => {
  const u = viewTagUnion({
    active: "all", hidden: [],
    tags: [{ id: "g1", name: "pool", color: "#1EA1EB", members: ["S1"] }],
    remoteTags: [
      { id: "TESTHOST:g9", name: "pool", color: "#999999", members: ["TESTHOST:S7"], host: "TESTHOST" },
      { id: "TESTHOST:g8", name: "ops", color: "#54B204", members: ["TESTHOST:S7"], host: "TESTHOST" },
    ],
  });
  assert.equal(u.length, 2, "pool unions across kernels; ops stands alone");
  const pool = u.find((g) => g.name === "pool")!;
  assert.equal(pool.localId, "g1", "the local store is the union's write-home for adds");
  assert.equal(pool.color, "#1EA1EB", "the local colour wins");
  assert.deepEqual(pool.members.sort(), ["S1", "TESTHOST:S7"], "membership is the union");
  assert.equal(pool.remotes.length, 1);
  const ops = u.find((g) => g.name === "ops")!;
  assert.equal(ops.localId, null, "a remote-only tag has no local write-home");
  assert.equal(ops.remotes[0].host, "TESTHOST");
});

test("the Tags row opens the second section (where the session belongs); Browse stays last", () => {
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(tagsAt > 0 && browseAt > 0 && tagsAt < browseAt, "Tags above, Browse last");
  assert.match(body.slice(tagsAt - 500, tagsAt), /ctxIcon\("tag", false\)/, "the tag icon");
  assert.match(body, /return names\.length \? names\.join\(" · "\) : "none yet — tag it to organize and dispatch";/,
    "the compact one-line row: current names, or the honest empty state");
});

test("edits reuse the wire — never a fork: local edits post TARGETED tagEdit ops, remote edits ride editTag", () => {
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const body = RENDER.slice(at, at + 4200);
  assert.ok(body.includes('ops.push({ op: "addMember", tid: g.localId, sids: edit.add.slice() });'),
    "a local add is an addMember by the tag's stored id (2026-09-05: the whole-blob post was refused as stale against the page's own earlier write; by name, a refused rename left the next gesture addressing the other tag)");
  assert.ok(body.includes('ops.push({ op: "removeMember", tid: g.localId, sids: edit.remove.slice() });'), "…and a local remove");
  assert.ok(body.includes('vscodeApi?.postMessage({ type: "editTag", edit: { host: g.remotes[0].host || "", name: g.name, add: edit.add.slice() } });'),
    "an add with no local home routes to the tag's single home over the editTag wire");
  assert.ok(body.includes("for (const rt of g.remotes) {"),
    "a REMOVE walks every remote store holding the pair — remove-everywhere, never half");
  assert.ok(body.includes("postUnionEdits(nv, applyUnionEdit(nv, g, edit));"), "ONE optimistic blob per gesture — the flyout reads true instantly");
  assert.ok(body.includes("if (ops.length) { for (const op of ops) postTagEdit(nv, op); }"), "N targeted ops, the one copy shown for all of them");
  assert.ok(body.includes("else if (edits.some((e) => e.mirrored)) { pendingSessionViews = nv; renderTabs(); }"),
    "a remote-only edit has no local op: its mirror shows until the next frame, as before");
  assert.ok(body.includes("if (g.localId && !g.pending) {"), "a union whose create is still in flight takes no op (its id is the placeholder the ack replaces)");
  assert.ok(body.includes("const nvRemote = (rt: SessionTag)"),
    "the remote entries mirror optimistically too — echoed remoteTags are derived, kernel-dropped, presentation-only");
  assert.match(RENDER, /x\.title = "remove this tag from the session — everywhere it holds it";/);
});

test("New tag… is an inline input (menu vocabulary, no native prompt) that creates locally with a palette colour", () => {
  assert.match(RENDER, /inp\.placeholder = "New tag…"; inp\.maxLength = 40;/);
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("const editUnion")), /window\.prompt/);
  assert.match(RENDER, /const color = paletteColors\.find\(\(c\) => !used\.has\(c\)\) \|\| paletteColors\[0\] \|\| "#1EA1EB";/);
  assert.match(RENDER, /const tg = \{ id: "pending-" \+ Date\.now\(\)\.toString\(36\), name, color, members: \[id\] \};\s*\n\s*nv\.tags = viewTags\(nv\)\.concat\(\[tg\]\);/,
    "the optimistic row wears a placeholder id — the kernel mints the real one");
  assert.match(RENDER, /postTagEdit\(nv, \{ op: "create", name, color, sids: \[id\] \}, tg\.id\);/, "one targeted create, the session in it, no client id on the op (the placeholder rides beside it for the legacy path to re-id)");
  // an existing name typed into the box ADDS to that union instead of minting a duplicate tag
  assert.match(RENDER, /const existing = unionFor\(\)\.find\(\(g\) => g\.name === name\);/);
});

test("presentation: one chip per NAME (the shared tag chip, T321), the action rows' identity dot, ✕, and never a host prefix in the flyout", () => {
  assert.match(RENDER, /const chip = tagChip\(g\.name, g\.color \|\| null, \{ inheritSize: true \}\);\s+\/\/ the one tag chip \(T321\)/, "a row that names a tag IS the tag chip, at the label's size");
  assert.match(RENDER, /chip\.classList\.add\("ctx-tag-chip"\);\s*\n\s*lb\.appendChild\(chip\); bodyE\.appendChild\(lb\);/, "in the label slot");
  assert.match(RENDER, /lb\.append\("\+ ", named\(\)\); bodyE\.appendChild\(lb\);/, "the + row names its tag as the chip inside the sentence");
  assert.doesNotMatch(RENDER + CSS, /ctx-tag-dot/, "no swatch-and-name pair is left in the flyout (T321)");
  const fly = RENDER.slice(RENDER.indexOf("const sub = el(\"div\", \"ctx-menu ctx-sub ctx-sub-tags\");"));
  assert.doesNotMatch(fly.slice(0, 2500), /host-prefix|hostNameNodes/, "kernels are plumbing — no host chrome in the flyout");
});

test("one-click MOVE between groups (tab groups, 2026-09-04): 'Move to <name>' adds the target and drops the HOME tag on ONE blob; '+' adds without moving", () => {
  const fly = RENDER.slice(RENDER.indexOf('const sub = el("div", "ctx-menu ctx-sub ctx-sub-tags");'), RENDER.indexOf("// New tag… — an inline input"));
  assert.match(fly, /const home0 = readTabGroups\(\)\.on \? \(\(copy !== undefined \? holding\(\)\.find\(\(g\) => g\.name === copy\) : undefined\) \?\? holding\(\)\[0\]\) : undefined;\s*\n\s*const home = home0 && !home0\.pending \? home0 : undefined;/,
    "the group THIS COPY sits in (T264b: a session under several tags has a copy per group, and the menu speaks for the right-clicked copy's group), else the first holder; only while the strip is sectioned, and never a tag whose create is still in flight");
  assert.match(fly, /lb\.append\("Move to ", named\(\)\); bodyE\.appendChild\(lb\);/, "the tag inside the sentence is the chip (T321)");
  assert.match(fly, /moveUnion\(home, g\); build\(\); sb\.textContent = subText\(\);/, "the row IS the move");
  assert.match(fly, /plus\.title = "add this tag too \(the session keeps its other tags\)" \+ \(settings\.tabsLocked \? ": adding is not a move, so the lock does not hold it" : ""\);/, "…and multi-tag stays one click away (the tab lock, T395, adds its clause)");
  assert.match(fly, /lb\.append\("\+ ", named\(\)\); bodyE\.appendChild\(lb\);/, "with no home tag, + <name> is the move");
  const mv = RENDER.slice(RENDER.indexOf("const moveUnion = (from: TagUnion, to: TagUnion)"), RENDER.indexOf("// HOVER-INTENT open"));
  assert.match(mv, /const a = applyUnionEdit\(nv, to, \{ add: \[id\] \}\);\s*\n\s*const r = applyUnionEdit\(nv, from, \{ remove: \[id\] \}\);/,
    "two edits on ONE blob shown — the strip never shows the half-moved state");
  assert.match(mv, /postUnionEdits\(nv, \{ ops: \[\{ op: "move", tid_from: rem\.tid, tid_to: add\.tid, sid: id \}\], mirrored: a\.mirrored \|\| r\.mirrored \}\);/,
    "…and with both tags local, ONE `move` op the kernel applies under its lock: both halves or neither");
  assert.match(mv, /else postUnionEdits\(nv, a, r\);/, "a half with no local home rides its own wire as before");
  assert.match(RENDER, /const applyUnionEdit = \(nv: SessionViews, g: TagUnion, edit: \{ add\?: string\[\]; remove\?: string\[\] \}\): UnionEdit =>/,
    "editUnion and moveUnion share the one edit — never a forked implementation");
});

test("the menu groups by what each item changes: [Rename+colours] / [Tags, Move] / [switches+Billing] / [Browse] (the user 2026-09-11)", () => {
  // supersedes the 2026-08-24 three-section ruling: membership and location (Tags, Move to folder…) leave
  // the switches for a section of their own. tab-menu-sections.test.ts is the one pin of the whole grouping;
  // this pins Rename's dress and the two dividers around the Tags section
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const renameAt = body.indexOf('l.textContent = "Rename"');
  assert.ok(renameAt > 0, "Rename wears the label span like its siblings");
  assert.match(body.slice(renameAt - 400, renameAt), /ctxIcon\("pencil", false\)/, "…and the pencil icon");
  assert.match(body, /sb\.textContent = "the name is a label — mail, goals and history follow the session";/,
    "…and a sub-line saying what a rename preserves (uuid-keyed truth)");
  const colorsAt = body.indexOf('el("div", "ctx-colors")');
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const moveAt = body.indexOf('l.textContent = "Move to folder…"');
  const firstToggleAt = body.indexOf('toggle("feed"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(renameAt < colorsAt && colorsAt < tagsAt && tagsAt < moveAt && moveAt < firstToggleAt && firstToggleAt < browseAt,
    "order: Rename, colours, Tags, Move, switches, Browse");
  // the MENU's dividers (the Tags flyout appends its own to `sub`, which never counts)
  const SEP = 'menu.appendChild(el("div", "ctx-sep"));';
  assert.ok(!body.slice(renameAt, colorsAt).includes(SEP), "Rename+colours are one section");
  assert.ok(body.slice(colorsAt, tagsAt).includes(SEP), "a divider splits sections 1/2");
  assert.ok(!body.slice(tagsAt, moveAt).includes(SEP), "Tags and Move to folder… are ONE section — where the session belongs");
  assert.ok(body.slice(moveAt, firstToggleAt).includes(SEP), "a divider splits sections 2/3 — the switches start a section of their own");
});
