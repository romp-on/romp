// NOTICE CARDS over federation (T370, plans/notice-cards.md): a notice ask from another host rides the asks array the
// merge already carries; its sid and name take the host prefix like every card's, its item id stays as the owner minted it
// (notice:<sid>:<key>:<rev>, a namespaced family), and a viewer's foreign clear never names the family (the kernel's
// _cleared_foreign skips it), so a dismissal is a routed gesture into the owning kernel's ledger.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeHostFeeds } from "./federation";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

const SID = "11111111-2222-3333-4444-555555555555";
const notice = (sid: string, rev: number) => ({
  itemId: `notice:${sid}:figure:${rev}`, sid, name: "web", color: { bg: "#1EA1EB", fg: "#ffffff" }, text: "A new figure", t: 1757000000,
  live: false, trgb: [1, 2, 3], turnId: `notice:${sid}:figure:${rev}`, column: "completed", tree: [],
  notice: { producer: "figure", key: "figure", rev, body: "the body", attachment: null, actions: [{ label: "Send again", route: "/send", body: { text: "x" } }], expiresAt: null, dismissOnAction: false },
});

test("a remote host's notice card merges with its prefixed sid and name kept and its item id untouched; a foreign clear naming a goal leaves it", () => {
  // inbound frames are host-prefixed field by field at receive time (sid, name); the merge concatenates them
  const A = "HOSTA:" + SID;
  const remote = { type: "feed", asks: [{ ...notice(SID, 2), sid: A, name: "HOSTA:web" }], sessions: [{ sid: A, name: "HOSTA:web" }], working: [], awaiting: [], ledgers: [] };
  const local = { type: "feed", asks: [], sessions: [], working: [], awaiting: [], ledgers: [], clearedForeign: [SID + ":g1"] };
  const merged: any = mergeHostFeeds({ "": local, HOSTA: remote }, ["", "HOSTA"]);
  const a = merged.asks.find((x: any) => x.notice);
  assert.ok(a, "the notice ask rides the merged asks");
  assert.equal(a.sid, A, "the sid wears the host prefix (gestures route by it)");
  assert.equal(a.name, "HOSTA:web", "the name too (the chip shows the host)");
  assert.equal(a.itemId, `notice:${SID}:figure:2`, "the item id is the owner's, never prefixed");
  assert.deepEqual(a.notice, remote.asks[0].notice, "the flavour object passes through untouched");
  assert.equal(merged.asks.length, 1, "a foreign clear of a goal id touches no notice");
});

test("the dismissal of a notice card is a routed gesture: the feed posts askClear with the card's sid", () => {
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "askClear", itemId: it\.itemId, sid: it\.sid \}\);/);
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "noticeAction", itemId: it\.itemId, sid: it\.sid, route: act\.route, body: act\.body \}\);/, "an action carries the sid too");
});
