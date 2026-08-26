// GROUPED mode (the user 2026-07-13): a footer "Group" toggle organizes each column BY SESSION — session
// order = the kernel's session-order list (the same order the chat tabs + timeline lanes hold), a
// name+working-dot header on the column backdrop opens each session's run, and the cards below drop their
// own name row (the header carries the identity). (Clear used to re-home beside the timestamp in this mode
// only; since 2026-08-08 the action corner lives in row1 in EVERY mode — see feed-continue.test.ts.)
// Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

test("the view menu's Group-by-session row persists `grouped` in romp:settings; grouping is the DEFAULT (the user 2026-07-13)", () => {
  assert.match(FEED, /grouped: s\.grouped !== false/);   // default ON — the row opts OUT
  // the footer "Group" word-button folded into the view menu (the user 2026-08-24) — same pref, same default
  assert.match(FEED, /set\(2, "Group by session", \{/);
  assert.match(FEED, /current: p\.grouped,/, "the ✓ reads the same !== false default — a missing key stays checked");
  assert.match(FEED, /mk\(true, \(\) => setViewPref\("grouped", !feedPrefs\(\)\.grouped\)\)/);
});

test("session rank = the kernel's session-order list (tab/lane order); unknown sids keep time order after it", () => {
  // the order rides every feed push; the kernel emits session-order.json, federation concatenates per host
  assert.match(FEED, /if \(Array\.isArray\(m\.order\)\) sessionOrder = m\.order\.filter/);
  assert.match(FEED, /const rank = new Map\(sessionOrder\.map\(\(s, i\) => \[s, i\] as const\)\);/);
  assert.match(FEED, /return rank\.has\(s\) \? rank\.get\(s\)! : 1e9 \+ \(extra\.get\(s\) \|\| 0\);/);
  // stable sort: per-session cards keep the column's newest/oldest order
  assert.match(FEED, /buckets\[k\]\.sort\(\(x, y\) => rk\(x\) - rk\(y\)\);/);
  assert.match(FED, /if \(Array\.isArray\(f\.order\)\) merged\.order\.push\(\.\.\.f\.order\);/);
});

test("a name+dot header entry opens each session's run; only runs that exist get one", () => {
  // `folded` joined the header entry with collapsible threads (feed-thread-fold.test.ts, 2026-07-31):
  // it counts the cards a FOLDED header stands in for, and is 0 while the thread is open.
  assert.match(FEED, /\{ kind: "sess"; t: number; sid: string; name: string; color: \{ bg: string; fg: string \} \| null; live: boolean; folded: number \}/);
  assert.match(FEED, /if \(s !== cur\) \{/);
  assert.match(FEED, /head = \{ kind: "sess", t: e\.t, sid: s, name: src\.name, color: src\.color \|\| null, live: !!src\.live, folded: 0 \};\s*\n\s*withHeads\.push\(head\);/);
  // reconcile keys headers per (column, sid) — one session can head a run in EVERY column
  assert.match(FEED, /key = "s:" \+ listEl\.id \+ ":" \+ e\.sid;/);
  // the header carries the identity: colored name, host prefix treatment, the yellow working dot
  assert.match(FEED, /nm\.replaceChildren\(\.\.\.hostNameNodes\(e\.name, e\.sid\)\);/);
  assert.match(FEED, /setWorkDot\(nm, dotFor\(e\.name\)\);/);   // work OR awaiting dot — await-green when idle-but-awaiting (the user 2026-07-13)
  // headers aren't cards — but a FOLDED one stands in for its run, so the chip counts what it hides;
  // the column must report the board, not what the reader happens to have open (2026-07-31)
  assert.match(FEED, /const nCards = \(es: Entry\[\]\) => es\.reduce\(\(n, e\) => n \+ entryCards\(e\), 0\);/);
  // flex-wrap: the header hosts the background-process chip's expandable list on its own full-width
  // line (feed-bg-service-chip.test.ts, the user 2026-07-24)
  assert.match(CSS, /\.feed-sess-head \{ display: flex; flex-wrap: wrap; align-items: center;/);
});

test("grouped cards drop their own name row; the action corner needs no re-home (2026-08-08)", () => {
  // the name row hides (the header carries it)
  assert.match(FEED, /\(\(a\._name as HTMLElement\)\.parentElement as HTMLElement\)\.style\.display = gmode \? "none" : "";/);
  // the Clear re-home between rows is GONE: the action corner (fask-btns) lives in row1 in every mode,
  // so a mode flip moves nothing — the strongest form of the click-safety rule
  assert.doesNotMatch(FEED, /clrHome/);
  // row2 hides once nothing on it shows (ask card: badges may remain; group card: only the name now)
  assert.match(FEED, /r2\.style\.display = gmode && !r2live \? "none" : "";/);
  // float-right = right-justified beside the time when it fits, else its own right-aligned line
  assert.match(CSS, /\.fask-btns \{ float: right; margin-left: 8px;/);
});

test("clearing a run's last card drops its session header at once, not on the next push (the user 2026-07-13)", () => {
  // the 180ms dismiss timer finishes by dropping the item from the LOCAL model and re-rendering — the
  // grouped transform recomputes runs from the filtered list, so an emptied run loses its header (and the
  // column count follows) the moment the card element leaves the DOM. pendingCleared still guards pushes.
  assert.match(FEED, /function dropDismissed\(ids: string\[\]\): void \{/);
  assert.match(FEED, /asks = asks\.filter\(\(a\) => !gone\.has\(a\.itemId\)\);\s*\n\s*render\(\);/);
  // both optimistic dismiss paths finish through it: the single ask card and the sibling-group card
  assert.match(FEED, /card\.remove\(\); askEls\.delete\(it\.itemId\); dropDismissed\(\[it\.itemId\]\);/);
  assert.match(FEED, /groupEls\.delete\(cur\.turnId\); dropDismissed\(cur\.members\.map\(\(m\) => m\.itemId\)\);/);
});
