// A /clear typed into the composer must never silently drop the session's open cards (the user
// 2026-07-27): the kernel's episode boundary settles open tops when the conversation clears, and
// the composer is the one place romp sees the command before it runs. EXECUTES ./clear-confirm;
// the sendComposer gate, the boundary card's dropped line, and the feed's src-aware clear story
// are source-pinned (no jsdom for render.ts / feed.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { isClearCmd, openTopTitles, clearConfirmDetail, endConfirmDetail } from "./clear-confirm";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("isClearCmd mirrors the kernel's _is_clear_cmd truth table", () => {
  assert.ok(isClearCmd("/clear"));
  assert.ok(isClearCmd("  /clear  "));
  assert.ok(isClearCmd("/clear now"));
  assert.ok(!isClearCmd("/cleared"));
  assert.ok(!isClearCmd("please /clear"));
  assert.ok(!isClearCmd("/compact"));
});

test("openTopTitles counts exactly what the boundary settle takes: open tops, blocked included", () => {
  const tree = [
    { depth: 0, done: false, text: "Ship the deployment guide" },
    { depth: 0, done: false, cleared: false, text: "Tune the api rate limits" },
    { depth: 1, done: false, text: "a sub-step, not a card" },
    { depth: 0, done: true, text: "already finished" },
    { depth: 0, done: false, cleared: true, text: "already dismissed" },
  ];
  assert.deepEqual(openTopTitles(tree), ["Ship the deployment guide", "Tune the api rate limits"]);
  assert.deepEqual(openTopTitles(undefined), [], "no ledger yet → no titles → no modal");
});

test("the confirm detail: null with nothing open; singular/plural; long lists capped", () => {
  assert.equal(clearConfirmDetail([]), null, "no open cards → no modal, the /clear just sends");
  assert.match(clearConfirmDetail(["one card"])!, /Its 1 open card gets dropped with it: one card/);
  const two = clearConfirmDetail(["a", "b"])!;
  assert.match(two, /Its 2 open cards get dropped with it: a, b/);
  assert.match(two, /Undo on the feed/, "the way back is named in the same breath");
  const long = clearConfirmDetail(Array.from({ length: 30 }, (_, i) => "card number " + i))!;
  assert.ok(long.length < 400, "the titles list is capped so the modal stays readable");
  assert.match(long, /…/);
});

test("endConfirmDetail names the open cards before an End, and stays quiet with none", () => {
  const base = "The session shuts down.";
  assert.equal(endConfirmDetail([], base), base, "nothing open -> the short static line");
  const one = endConfirmDetail(["ship the exporter"], base);
  assert.ok(one.startsWith("1 card is still open on its board: ship the exporter."));
  assert.ok(one.endsWith(base));
  const two = endConfirmDetail(["a", "b"], base);
  assert.ok(two.startsWith("2 cards are still open on its board: a, b."));
});

test("sendComposer gates a /clear behind showConfirm — Cancel first (safe default), send only on confirm", () => {
  const at = RENDER.indexOf("const dropDetail");
  assert.ok(at > 0, "the gate exists in sendComposer");
  const gate = RENDER.slice(at, at + 700);
  assert.match(gate, /isClearCmd\(text\) \? clearConfirmDetail\(openTopTitles\(ledgers\.get\(sid\)\?\.tree\)\) : null/);
  assert.match(gate, /showConfirm\("Clear this conversation\?"/);
  assert.match(gate, /\{ label: "Cancel", value: "cancel" \}, \{ label: "Clear anyway", value: "clear", danger: true \}/);
  assert.match(gate, /if \(v === "clear"\) deliver\(\)/);
  // the deliver closure is pinned to the session the confirm was armed for
  assert.match(RENDER, /if \(activeId !== sid\) return;/);
});

test("the chat boundary card counts the dropped cards, and hover names them", () => {
  assert.match(RENDER, /" open card" \+ \(dropped\.length === 1 \? "" : "s"\) \+ " dropped with it"/);
  assert.match(RENDER, /setAttribute\("title", "dropped: " \+ dropped\.join\(", "\)\)/);
});

test("the feed's per-node clear story is src-aware — a romp boundary clear is not blamed on the user", () => {
  assert.match(FEED, /r\.src === "romp" \? "dropped with the cleared conversation" : "you cleared it"/);
});
