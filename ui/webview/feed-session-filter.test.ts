// The feed footer's SESSION FILTER (the user 2026-08-08): a menu right of the Group toggle listing
// every session the chat tab strip shows, in ITS order, each in canonical form — identity-colour dot +
// name with any "host:" prefix folded quiet (.host-prefix). Picking one shows only that session's
// cards; the DEFAULT is nothing selected, everything shows. The kernel attaches the tab list to the
// feed payload (name+colour resolved exactly as tab_meta); federation prefixes and concatenates it.
// The federation legs are pure and tested functionally; feed.ts has no jsdom harness → source pins
// (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { prefixInbound, mergeHostFeeds } from "./federation";
import { lensUnions } from "./tag-lens";

const ROOT = path.resolve(process.cwd(), "..");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";

test("the kernel's feed payload carries the chat tab strip's sessions, tab_meta-shaped", () => {
  assert.ok(KERNEL.includes('"sessions": [{"sid": s["sid"], "name": s.get("name", ""), "color": _name_color(s["sid"])}'));
  assert.ok(KERNEL.includes("for s in _chat_tab_sessions(now, tmux)]"), "the SAME list the tabs render, in ITS order");
});

test("federation prefixes each sessions[] entry's sid AND name, and the merge concatenates local-first", () => {
  const out = prefixInbound("TESTHOST", { type: "feed", sessions: [{ sid: U, name: "api", color: null }] });
  assert.equal(out.sessions[0].sid, "TESTHOST:" + U);
  assert.equal(out.sessions[0].name, "TESTHOST:api");
  const merged = mergeHostFeeds({
    "": { type: "feed", sessions: [{ sid: U, name: "web" }] },
    TESTHOST: { type: "feed", sessions: [{ sid: "TESTHOST:" + V, name: "TESTHOST:api" }] },
  }, ["", "TESTHOST"]);
  assert.deepEqual(merged.sessions.map((s: any) => s.name), ["web", "TESTHOST:api"]);
});

test("the filter defaults to NOTHING selected and only ever narrows the RENDER, never the data", () => {
  assert.ok(FEED.includes("let feedOnlySid: string | null = null;"));
  assert.ok(FEED.includes('sessionStorage.getItem("romp:feedOnly")'),
    "survives this tab's reloads only — a fresh window always starts unfiltered");
  // the filter chain lives in viewFiltered now (hover-freeze 2026-08-24: the deferred-churn badge
  // painter must count exactly what the user sees, so render and the painter share one view) — and
  // the tracked-delegation satellite exclusion lives INSIDE it for the same reason (2026-08-24):
  // the unfiltered board hides only satellites; a picked session still renders ALL its cards
  // the feed shows every session's cards (the user's 2026-08-25 ruling — the 2026-08-24 view gate
  // retired); the session filter / satellite split is the board's own first stage
  assert.ok(FEED.includes("let shown = feedOnlySid ? list.filter((a) => a.sid === feedOnlySid) : list.filter((a) => !a.satellite);"));
  assert.ok(FEED.includes("let shown = viewFiltered(asks);"), "render reads the shared view");
  // (`let`, since 2026-08-23: the SEARCH filter composes onto the same render-side view — see feed-search.test.ts)
  assert.ok(FEED.includes("for (const a of shown) {"), "the group-fold loop reads the filtered view");
  assert.ok(FEED.includes("for (const a of shown) { if (grouped.has(a.itemId)) continue;"), "…and the singles loop");
  // a filter aimed at a session the tab strip no longer shows clears itself — the deciding EVENT is
  // the session leaving the tab list, never a timer
  assert.ok(FEED.includes("if (feedOnlySid && !sessionsMeta.some((s) => s.sid === feedOnlySid)) setFeedOnly(null);"));
});

test("the combobox sits right of the view-menu icon, lists sessions in tab order, canonical form, click-safe", () => {
  // one control since 2026-08-24: the session picker and the search box merged into the combobox
  // the tag-lens button (T70, 2026-08-25) sits between the view menu and the combobox now
  assert.match(FEED, /ensureViewMenuBtn\(\)\.style\.display = showCA \? "" : "none";[^\n]*\n\s*ensureTagLensBtn\(\)\.style\.display = showCA \? "" : "none";[^\n]*\n\s*ensureSessionBox\(\)\.style\.display = showCA \? "" : "none";/);
  // the list is built ONCE per open; typing only toggles row display — a keystroke can never
  // rebuild a row out from under a press (click-safety)
  assert.match(FEED, /r\.style\.display = name === null \|\| searchMatches\(q, name\) \? "" : "none";/);
  assert.match(FEED, /row\(null, all, null\);/, "the All-sessions way back is never filtered away");
  // tab order: ranked by the kernel's session-order list — the same rank grouped mode sorts by
  assert.ok(FEED.includes("const rows = sessionsMeta.slice().sort((a, b) => (rank.get(a.sid) ?? 1e9) - (rank.get(b.sid) ?? 1e9));"));
  // the menu lives on document.body — outside render()'s reconcile, so a push can't rebuild it mid-press
  assert.ok(FEED.includes("document.body.appendChild(menu);"));
});

test("session rows carry their tag chips — grouping visible, the pick untouched (2026-08-25)", () => {
  // the union math executes against both tag-home shapes (the shared model's own function)
  const views = { tags: [{ id: "t1", name: "infra", members: [U] }],
    remoteTags: [{ id: "TESTHOST:t9", name: "infra", members: [V] }, { id: "TESTHOST:t8", name: "web", members: [V] }] };
  const unions = lensUnions(views);
  const chipsFor = (sid: string) => unions.filter((g: any) => g.members.includes(sid)).map((g: any) => g.name);
  assert.deepEqual(chipsFor(U), ["infra"], "a locally-tagged session wears its union chip");
  assert.deepEqual(chipsFor(V), ["infra", "web"], "a remote-homed member joins the SAME name-keyed union");
  // the row wiring: chips appended only when the session has any, the dialog's outline vocabulary,
  // non-interactive, ellipsizing in the row's leftover space
  assert.match(FEED, /if \(!g\.members\.includes\(pick\)\) continue;/);
  assert.match(FEED, /if \(chips\.childElementCount\) r\.appendChild\(chips\);/);
  assert.match(FEED, /if \(g\.color\) \{ c\.style\.color = g\.color; c\.style\.borderColor = g\.color; \}/,
    "the tag's own colour, outline-pill like the dialog");
  const CSS2 = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
  assert.match(CSS2, /\.fsm-chips \{ flex: 1 1 auto; min-width: 0; margin-left: 8px; text-align: right;\s*\n\s*overflow: hidden; text-overflow: ellipsis; white-space: nowrap; pointer-events: none; \}/,
    "ellipsizes, never wraps the row; never a click target — the pick stays byte-identical");
  assert.match(CSS2, /\.fsm-chip-tag \{ display: inline-block; font-style: normal; padding: 0 6px; margin-left: 4px;/,
    "compact at the row's scale");
});

test("menu rows write a session the way the tabs do — coloured bold name + the shared status dot", () => {
  // the identity treatment every other surface gives a session (the user 2026-08-08, round two: a
  // colour SWATCH read as a status dot — on this board a dot beside a name means working/awaiting)
  assert.ok(FEED.includes("nm.replaceChildren(...hostNameNodes(s.name, s.sid));"), "host prefix folded quiet");
  assert.ok(FEED.includes("if (s.color) nm.style.color = s.color.bg;"), "name IN the identity colour");
  assert.ok(FEED.includes("for (const s of rows) row(s.sid, sessMenuName(s), s.name, s.name);"));
  assert.ok(FEED.includes("if (dotName) setWorkDot(label, dotFor(dotName));"),
    "the SAME working/awaiting dot the tabs and headers wear — never a colour swatch");
  assert.ok(!FEED.includes("sessDot"), "the swatch helper is gone");
  const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
  assert.match(CSS, /\.fsm-name \{ font-weight: 600; \}/, "bold, like the tab titles and session headers");
  assert.ok(!CSS.includes(".fsm-dot"), "no swatch styles either");
  // with a filter on, the CHIP in the bar quotes the picked session verbatim, dot included — a
  // narrowed board must never look like the whole one; its ✕ hands the bar back to typing. The ✕
  // is built ONCE (a per-render rebuild would swap it under a press — click-safety); the NAME
  // re-quotes per render so colour echoes and the dot stay live.
  assert.ok(FEED.includes("chip.replaceChildren(nm, x);"));
  assert.ok(FEED.includes("(chip as any)._nm = nm;"));
  assert.ok(FEED.includes("nm.replaceChildren(...hostNameNodes(cur.name, cur.sid));"));
  assert.ok(FEED.includes("setWorkDot(nm, dotFor(cur.name));"));
  assert.ok(FEED.includes('x.setAttribute("aria-label", "show all sessions");'));
});
