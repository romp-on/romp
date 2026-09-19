// The feed as one board definition (plans/card-boards.md, phase one). Two proofs: the DEFINITION equals the literals feed.ts
// and kernel.py carry today (executed over board-def.ts, and held to the sources by regex, so a value that moves on one side
// reads red on the other), and feed.ts reads the definition at the plan's section-4 sites and nowhere else. Then the schema
// check runs over the constant, and over copies with one member changed on purpose, each refused by name.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { ORDER_RULES, FEED_BOARD, FEED_KINDS, FEED_LOCAL_KEY, CHIPS, SORT_FIELDS, KIND_IDS, RESERVED_BOARD_IDS,
         kindOf, columnOf, columnTable, feedColumns, isNeedsYou, boardCheck, defaultBoard, adoptBoards, boardOf, knownBoards } from "./board-def";
import { mergeHostFeeds } from "./federation";
import { FEED_COLUMNS } from "./feed-view-state";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const FEED = read("ui", "webview", "feed.ts");
const KERNEL = read("kernel", "kernel.py");
const BOARD_SRC = read("ui", "webview", "board-def.ts");

// today's three header triples, exactly as ensureCols spelt them before the extraction (feed-col-head-case.test.ts pinned
// the literal table; it now pins the definition through columnTable)
const TRIPLES = [["asks", "Working", "working"], ["needsInput", "Blocked", "blocked"], ["completed", "Completed", "completed"]];

test("the feed definition equals today's literals: the columns, their titles and chips, in the build order", () => {
  assert.deepEqual(columnTable(FEED_BOARD).map((t) => [...t]), TRIPLES);
  assert.deepEqual([...feedColumns(FEED_BOARD)], ["asks", "needsInput", "completed"]);
  assert.deepEqual([...feedColumns(FEED_BOARD)], [...FEED_COLUMNS], "the view state's column list is the same order");
  // the stacked and side-by-side CSS defaults feed.ts keeps as literals (pinned by feed-col-fold.test.ts): the board's
  // order and its reverse
  const row = JSON.parse((FEED.match(/const ROW_DEFAULT = (\[[^\]]*\]);/) || [])[1] || "null");
  const stack = JSON.parse((FEED.match(/const STACK_DEFAULT = (\[[^\]]*\]);/) || [])[1] || "null");
  assert.deepEqual(row, [...feedColumns(FEED_BOARD)], "ROW_DEFAULT is the board's order");
  assert.deepEqual(stack, [...feedColumns(FEED_BOARD)].reverse(), "STACK_DEFAULT is the board's order reversed");
  assert.equal(FEED_BOARD.defaultCategory, "working");
});

test("the category mapping: the kernel's raw column values to the renderer's local keys, an unknown value to the default", () => {
  assert.equal(columnOf(FEED_BOARD, "needs_input"), "needsInput");
  assert.equal(columnOf(FEED_BOARD, "completed"), "completed");
  assert.equal(columnOf(FEED_BOARD, "working"), "asks");
  assert.equal(columnOf(FEED_BOARD, "awaiting"), "asks", "a value the kernel never sends files under the default, as the old mapping did");
  // a Map, so a prototype-named category (producer-facing from phase three) files under the default like any unknown one
  for (const bad of ["toString", "constructor", "__proto__", "hasOwnProperty"]) assert.equal(columnOf(FEED_BOARD, bad), "asks", bad);
  assert.deepEqual([...FEED_LOCAL_KEY], [["working", "asks"], ["needs_input", "needsInput"], ["completed", "completed"]]);
  assert.equal(isNeedsYou(FEED_BOARD, "needs_input"), true);
  assert.equal(isNeedsYou(FEED_BOARD, "working"), false);
  assert.equal(isNeedsYou({ ...FEED_BOARD, needsYou: null }, "needs_input"), false, "a board that never badges lets nothing through as needs-you");
  assert.deepEqual(FEED_BOARD.categories.map((c) => c.id), ["working", "needs_input", "completed"], "the ids are the kernel's raw values");
});

test("the sort, the grouping and the notification set equal the sources' literals", () => {
  // feed.ts: oldest at the top unless newestFirst (feed-sort.test.ts pins the line); grouped default on
  assert.deepEqual(FEED_BOARD.sort, { key: "t", dir: "asc" });
  assert.match(FEED, /buckets\[k\]\.sort\(\(x, y\) => newestFirst \? y\.t - x\.t : x\.t - y\.t\)/, "asc is x.t - y.t when newestFirst is off");
  assert.equal(FEED_BOARD.groupBy, "session");
  assert.match(FEED, /grouped: s\.grouped !== false/, "grouped mode defaults on");
  // kernel.py: the board table's feed entry (phase two) is this constant field for field; tests/test_card_boards.py holds the
  // two together over every member, this end reads the three the bell and the badge use
  const feedTable = (KERNEL.match(/_CODE_BOARDS = \{\s*\n\s*"feed": \{([\s\S]*?)\n    \},/) || [])[1] || "";
  assert.ok(feedTable.length > 0, "the kernel's board table carries the feed");
  const notify = (feedTable.match(/"notify": \[([^\]]*)\]/) || [])[1] || "";
  assert.deepEqual([...FEED_BOARD.notify], notify.split(",").map((s) => s.trim().replace(/^"|"$/g, "")).filter(Boolean));
  assert.match(feedTable, /"needsYou": "needs_input"/);
  assert.equal(FEED_BOARD.needsYou, "needs_input");
  assert.match(KERNEL, /_NOTIFY_COLUMNS = tuple\(_CODE_BOARDS\["feed"\]\["notify"\]\)/, "the feed's notify set is read from the table");
  assert.match(KERNEL, /a\.get\("category", a\.get\("column"\)\) == _board_needs_you\(a\.get\("board"\)\)/, "_needs_you_count counts the board's badge category, the column from an older card");
  assert.deepEqual([...FEED_BOARD.kinds], [...KIND_IDS]);
  assert.deepEqual([...FEED_BOARD.rules], []); assert.deepEqual([...FEED_BOARD.order], ["ownerRank"]); assert.deepEqual([...FEED_BOARD.subSorts], []);   // the owner rank is what feed.ts applies (PR 1831)
});

test("feed.ts reads the definition at the section-4 sites and nowhere else", () => {
  assert.match(FEED, /import \{ FEED_BOARD, columnOf, columnTable, feedColumns, isNeedsYou, adoptBoards, boardOf, type FeedCategory \} from "\.\/board-def";/);
  assert.match(FEED, /column: FeedCategory;/, "the record's column is typed to the feed board's category ids");
  assert.match(FEED, /board\?: string;[^\n]*\n\s*category\?: string;/, "the record carries its board and category (phase two), optional for an older kernel's frame");
  assert.match(FEED, /return columnOf\(FEED_BOARD, it\.category \?\? it\.column\);/, "askColumn is the definition's table over the category, the column as the older frame's fallback");
  assert.match(FEED, /\|\| isNeedsYou\(FEED_BOARD, a\.category \?\? a\.column\)\);/, "the lens's breakthrough is the board's badge category, not a literal (the 1834 read, low 2)");
  assert.doesNotMatch(FEED, /\.column === "needs_input"/, "no hand comparison against the raw category id remains");
  assert.equal((FEED.match(/for \(const \[key, label, chip\] of columnTable\(FEED_BOARD\)\)/g) || []).length, 2, "ensureCols and the focused section's twin");
  assert.equal((FEED.match(/of feedColumns\(FEED_BOARD\)\)/g) || []).length, 4, "the four column loops (the stack, the focused layout, the order flip, the freeze badges)");
  assert.match(FEED, /const FLY_COLS: readonly \("asks" \| "needsInput" \| "completed"\)\[\] = feedColumns\(FEED_BOARD\);/);
  assert.doesNotMatch(FEED, /\[\["asks", "Working", "working"\]/, "the literal header table is gone from the renderer");
  assert.doesNotMatch(FEED, /for \(const key of \["asks", "needsInput", "completed"\]\)/, "no column loop spells the keys by hand");
  // what stays a literal in feed.ts on purpose (pinned there; asserted equal above): the two CSS default orders, the sort
  // line and the grouped gate. Their consumption is phase five's (sub-sorts) and phase four's (a board with no grouping).
  assert.match(FEED, /const ROW_DEFAULT = \["asks", "needsInput", "completed"\];/);
});

// the reviewed table of each kind's ids (the plan's section 2): a descriptor that over-lists a section or action for a kind
// (a parked kind given Background, or Approve) reads red here, before phase four gates rendering on the descriptors
const KIND_TABLE: Record<string, { sections: string[]; actions: string[]; menu: string[] }> = {
  goal: { sections: ["bg", "summary", "subgoals", "stall", "tasks"], actions: ["clear", "followUp", "checkStatus", "continue", "retry", "login", "capSwitch", "bell"], menu: ["notify", "browse"] },
  placeholder: { sections: ["tasks"], actions: ["clear", "bell"], menu: ["notify", "browse"] },
  parked: { sections: [], actions: ["clear", "revive", "bell"], menu: ["notify", "browse"] },
  quarantine: { sections: [], actions: ["approve", "deny", "bell"], menu: ["notify", "browse"] },
  notice: { sections: ["body", "attachment"], actions: ["stored", "clear", "bell"], menu: ["notify", "browse"] },
};
// a top-level function's own source: from its declaration to the next top-level declaration
function fnBody(name: string): string {
  const at = FEED.indexOf("\nfunction " + name + "(");
  assert.ok(at >= 0, "feed.ts has function " + name);
  const next = FEED.indexOf("\nfunction ", at + 1);
  return FEED.slice(at, next < 0 ? FEED.length : next);
}

test("the kinds describe the card builder: each kind lists exactly the reviewed ids, and every labelled button is a literal inside the function its descriptor names", () => {
  assert.deepEqual(Object.keys(FEED_KINDS).sort(), Object.keys(KIND_TABLE).sort());
  for (const [id, kind] of Object.entries(FEED_KINDS)) {
    assert.equal(kind.id, id);
    assert.deepEqual(kind.sections.map((d) => d.id), KIND_TABLE[id].sections, id + ": sections");
    assert.deepEqual(kind.actions.map((d) => d.id), KIND_TABLE[id].actions, id + ": actions");
    assert.deepEqual(kind.menu.map((d) => d.id), KIND_TABLE[id].menu, id + ": menu");
    for (const d of [...kind.sections, ...kind.actions, ...kind.menu]) {
      const body = fnBody(d.via);
      if (d.label !== null) {
        const lit = d.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        assert.match(body, new RegExp('(?:textContent = |label = |label: )"' + lit + '"'), id + "." + d.id + ": the label is minted inside " + d.via);
      }
    }
  }
  assert.match(FEED, /"bg" \| "summary" \| "subgoals" \| "tasks" \| "stall"/, "the same five section ids in the renderer's choice type");
});

test("kindOf discriminates by the flavour, as the renderer does", () => {
  assert.equal(kindOf({ notice: { producer: "cli" } }), "notice");
  assert.equal(kindOf({ blocked: { state: "quarantine" } }), "quarantine");
  assert.equal(kindOf({ blocked: { state: "parkedHandoff" } }), "parked");
  assert.equal(kindOf({ provisional: true }), "placeholder");
  assert.equal(kindOf({ blocked: { state: "apiError" } }), "goal", "an API error is a goal card with a chip, never a kind");
  assert.equal(kindOf({}), "goal");
});

test("the schema check passes the feed's constant and refuses a copy with one member changed, naming it", () => {
  assert.equal(boardCheck(FEED_BOARD, { allowReserved: true }), null);
  assert.match(boardCheck(FEED_BOARD) || "", /code-defined board/, "the door refuses the reserved id");
  assert.deepEqual([...RESERVED_BOARD_IDS], ["feed"]);
  const copy = () => JSON.parse(JSON.stringify(FEED_BOARD));
  const refused = (mut: (b: any) => void, want: RegExp) => { const b = copy(); mut(b); const err = boardCheck(b, { allowReserved: true }); assert.match(err || "(accepted)", want); };
  refused((b) => { b.categories[1].chip = "red"; }, /chip must be one of working, blocked, completed, neutral/);
  refused((b) => { b.categories.push(...Array.from({ length: 6 }, (_, i) => ({ id: "c" + i, title: "C", chip: "neutral" }))); }, /1 to 8/);
  refused((b) => { b.categories[0].id = "Working"; }, /category id must match/);
  refused((b) => { b.categories[2].id = "working"; }, /repeats/);
  refused((b) => { b.defaultCategory = "done"; }, /defaultCategory must name/);
  refused((b) => { b.rules = [{ when: { needsYou: true }, category: "done" }]; }, /rule's category must name/);
  refused((b) => { b.rules = [{ when: { owner: "x" }, category: "working" }]; }, /predicate has an unknown member "owner"/);
  refused((b) => { b.rules = [{ when: {}, category: "working" }]; }, /at least one member/);
  refused((b) => { b.sort = { key: "age", dir: "asc" }; }, /sort\.key must be one of t, session, owner, title/);
  refused((b) => { b.subSorts = Array.from({ length: 7 }, () => ({ key: "t", dir: "asc" })); }, /0 to 6/);
  refused((b) => { b.groupBy = "owner"; }, /groupBy must be/);
  refused((b) => { b.order = ["newestPinned"]; }, /order rules must be from ownerRank/);
  refused((b) => { b.notify = ["done"]; }, /notify names a category the board does not have/);
  refused((b) => { b.needsYou = "done"; }, /needsYou must be one of/);
  refused((b) => { b.kinds = ["card"]; }, /kinds must be from/);
  refused((b) => { b.colour = "blue"; }, /unknown member "colour"/);
  refused((b) => { b.title = ""; }, /title must be 1 to 40/);
  refused((b) => { b.id = "Feed"; }, /id must match/);
  assert.match(boardCheck("feed") || "", /must be a JSON object/);
  assert.deepEqual([...CHIPS], ["working", "blocked", "completed", "neutral"]);
  assert.deepEqual([...SORT_FIELDS], ["t", "session", "owner", "title"]);
});

test("a first-use board takes the plan's defaults and passes the check", () => {
  const b = defaultBoard("notes", "new");
  assert.equal(boardCheck(b), null);
  assert.deepEqual(b, { id: "notes", title: "Notes", categories: [{ id: "new", title: "New", chip: "neutral" }], defaultCategory: "new",
    rules: [], sort: { key: "t", dir: "desc" }, subSorts: [], groupBy: null, order: [], notify: [], needsYou: null, kinds: ["notice"] });
  assert.equal(defaultBoard("scratch").categories[0].id, "notes", "no category named: one called notes");
  assert.match(BOARD_SRC, /^export function boardCheck\(/m, "one validator, the client half");
});

test("the feed board's order rule is the owner rank feed.ts applies: the definition says what the code does", () => {
  // PR 1831: the owner-less notice run ranks before every session run (a stable sort after the time sort in both modes, and a
  // rank before the session order in grouped mode); the definition names the rule the code applies, never a rank of its own
  assert.deepEqual([...FEED_BOARD.order], ["ownerRank"]);
  assert.match(FEED, /const ownerRank = \(sid: string\): number => isOwnerless\(sid\) \? 0 : 1;/, "the rank helper");
  assert.match(FEED, /buckets\[k\]\.sort\(\(x, y\) => ownerRank\(entrySid\(x\)\) - ownerRank\(entrySid\(y\)\)\);/, "applied to every column after the time sort");
  assert.match(FEED, /return isOwnerless\(s\) \? -1 : rank\.has\(s\)/, "and before the session order in grouped mode");
  assert.ok(ORDER_RULES.includes("ownerRank"), "the rule is one the schema names");
});

// ── phase three: the data-defined boards the frame carries ──────────────────────────────────────────────────────────────
const NOTES = { id: "notes", title: "Notes", categories: [{ id: "new", title: "New", chip: "neutral" }, { id: "kept", title: "Kept", chip: "working" }],
  defaultCategory: "new", rules: [{ when: { needsYou: true }, category: "new" }], sort: { key: "t", dir: "desc" }, subSorts: [], groupBy: null, order: [],
  notify: ["new"], needsYou: "new", kinds: ["notice"] };

test("the frame's boards are held only after the client half of the check; a reserved id, a refused definition or a mismatched id is skipped", () => {
  assert.equal(adoptBoards(null), 0); assert.equal(adoptBoards([NOTES]), 0, "a list is no map");
  const n = adoptBoards({ notes: NOTES, feed: { ...NOTES, id: "feed" }, bad: { ...NOTES, id: "bad", categories: [] }, other: { ...NOTES, id: "notes2" } });
  assert.equal(n, 1, "notes alone: the feed is code, bad fails the check (no categories), other names another id");
  assert.deepEqual(knownBoards().map((b) => b.id), ["feed", "notes"], "code first");
  assert.equal(boardOf({ board: "notes" }), knownBoards()[1]);
  assert.equal(boardOf({ board: "feed" }), FEED_BOARD); assert.equal(boardOf({}), FEED_BOARD); assert.equal(boardOf({ board: "gone" }), FEED_BOARD, "an unknown id reads as the feed's");
  assert.equal(adoptBoards({}), 0, "the map is replaced whole: a frame without boards clears the held set");
  assert.equal(boardOf({ board: "notes" }), FEED_BOARD);
  assert.match(FEED, /^  adoptBoards\(m\.boards\);/m, "the frame handler's read: every frame's word, a frame without the field holding none");
});

test("the federation merge folds every host's boards, the local host's definition winning on an id", () => {
  const remoteNotes = { ...NOTES, title: "Remote notes" };
  const merged = mergeHostFeeds({ "": { type: "feed", now: 7, boards: { notes: NOTES } }, HOSTA: { type: "feed", now: 8, boards: { notes: remoteNotes, lab: { ...NOTES, id: "lab" } } } },
                                ["", "HOSTA"], [], [], { "": 3, HOSTA: 9 });
  assert.deepEqual(Object.keys(merged.boards).sort(), ["lab", "notes"]);
  assert.equal(merged.boards.notes.title, "Notes", "the local definition wins");
  assert.equal(merged.boards.lab.id, "lab", "a remote-only board keeps the remote's definition");
  const bare = mergeHostFeeds({ "": { type: "feed", now: 7 } }, [""]);
  assert.deepEqual(bare.boards, {}, "no host shipped boards: an empty map, never undefined");
});

test("the cardPredict fan-back reads the card through askColumn (the 1837 round-two read, low 1)", () => {
  assert.match(FEED, /if \(top && askColumn\(top\) !== "asks"\) \{ optimisticFollowMove\(top\.itemId, kind\); moved = true; \}/);
  assert.doesNotMatch(FEED, /top\.column !== "working"/);
});
