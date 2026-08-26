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

test("the Tags row sits with the session controls ABOVE the divider; Browse stays last", () => {
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(tagsAt > 0 && browseAt > 0 && tagsAt < browseAt, "Tags above, Browse last");
  assert.match(body.slice(tagsAt - 500, tagsAt), /ctxIcon\("tag", false\)/, "the tag icon");
  assert.match(body, /return names\.length \? names\.join\(" · "\) : "none yet — tag it to organize and dispatch";/,
    "the compact one-line row: current names, or the honest empty state");
});

test("edits reuse the wire — never a fork: local adds post the whole blob, remote edits ride editTag", () => {
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const body = RENDER.slice(at, at + 2400);
  assert.ok(body.includes("t.members = Array.from(new Set((t.members || []).concat(edit.add))); dirty = true;"),
    "local add edits the whole blob (posted once below — pendingSessionViews echoes instantly)");
  assert.ok(body.includes('vscodeApi?.postMessage({ type: "editTag", edit: { host: g.remotes[0].host || "", name: g.name, add: edit.add.slice() } });'),
    "an add with no local home routes to the tag's single home over the editTag wire");
  assert.ok(body.includes("for (const rt of g.remotes) {"),
    "a REMOVE walks every remote store holding the pair — remove-everywhere, never half");
  assert.ok(body.includes("if (dirty) postViews(nv);"), "ONE optimistic blob per gesture — the flyout reads true instantly");
  assert.ok(body.includes("const nvRemote = (rt: SessionTag)"),
    "the remote entries mirror optimistically too — echoed remoteTags are derived, kernel-dropped, presentation-only");
  assert.match(RENDER, /x\.title = "remove this tag from the session — everywhere it holds it";/);
});

test("New tag… is an inline input (menu vocabulary, no native prompt) that creates locally with a palette colour", () => {
  assert.match(RENDER, /inp\.placeholder = "New tag…"; inp\.maxLength = 40;/);
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("const editUnion")), /window\.prompt/);
  assert.match(RENDER, /const color = paletteColors\.find\(\(c\) => !used\.has\(c\)\) \|\| paletteColors\[0\] \|\| "#1EA1EB";/);
  assert.match(RENDER, /nv\.tags = viewTags\(nv\)\.concat\(\[\{ id: "g" \+ Date\.now\(\)\.toString\(36\), name, color, members: \[id\] \}\]\);/);
  // an existing name typed into the box ADDS to that union instead of minting a duplicate tag
  assert.match(RENDER, /const existing = unionFor\(\)\.find\(\(g\) => g\.name === name\);/);
});

test("presentation: one chip per NAME, identity dot, ✕ — and never a host prefix in the flyout", () => {
  assert.match(RENDER, /lb\.textContent = g\.name; bodyE\.appendChild\(lb\);/);
  assert.match(RENDER, /lb\.textContent = "\+ " \+ g\.name; bodyE\.appendChild\(lb\);/);
  assert.match(CSS, /\.ctx-tag-dot \{ flex: 0 0 auto; width: 9px; height: 9px; border-radius: 50%; \}/);
  const fly = RENDER.slice(RENDER.indexOf("const sub = el(\"div\", \"ctx-menu ctx-sub ctx-sub-tags\");"));
  assert.doesNotMatch(fly.slice(0, 2500), /host-prefix|hostNameNodes/, "kernels are plumbing — no host chrome in the flyout");
});

test("the menu groups BY KIND: [Rename+colors] / [toggles+billing+Tags] / [Browse] (the user 2026-08-24, final ruling)", () => {
  // supersedes 644's single top section: aesthetic controls together at the top, the
  // behavior/membership controls as the middle section, Browse alone at the bottom
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const renameAt = body.indexOf('l.textContent = "Rename"');
  assert.ok(renameAt > 0, "Rename wears the label span like its siblings");
  assert.match(body.slice(renameAt - 400, renameAt), /ctxIcon\("pencil", false\)/, "…and the pencil icon");
  assert.match(body, /sb\.textContent = "the name is a label — mail, goals and history follow the session";/,
    "…and a sub-line saying what a rename preserves (uuid-keyed truth)");
  const colorsAt = body.indexOf('el("div", "ctx-colors")');
  const firstToggleAt = body.indexOf('toggle("feed"');
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(renameAt < colorsAt && colorsAt < firstToggleAt && firstToggleAt < tagsAt && tagsAt < browseAt,
    "order: Rename, colors, toggles, Tags, Browse");
  // one divider between colors and the toggles; NONE inside section 1 or section 2
  assert.ok(!body.slice(renameAt, colorsAt).includes('el("div", "ctx-sep")'), "Rename+colors are one section");
  assert.ok(body.slice(colorsAt, firstToggleAt).includes('menu.appendChild(el("div", "ctx-sep"));'), "a divider splits sections 1/2");
  assert.ok(!body.slice(firstToggleAt, tagsAt).includes('el("div", "ctx-sep")'),
    "toggles, billing and Tags are ONE behavior section — no inner dividers");
  assert.ok(body.slice(tagsAt, browseAt).includes('menu.appendChild(el("div", "ctx-sep"));'), "a divider splits sections 2/3 — Browse alone at the bottom");
});
