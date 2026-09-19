// The board definition schema (plans/card-boards.md, phase one): a board is a definition, a card names its board and
// category, and the renderer is a function of the two. ONE schema for the code-defined boards and, from phase three,
// the data-defined ones a producer posts through the door; the feed is the first definition, FEED_BOARD below, written
// down exactly as feed.ts renders it today (section 2 of the plan), and feed.ts reads it at the sites section 4 names.
// Pure and DOM-free: the feed bundle imports it, and the tests import it directly.
//
// What the schema expresses (labelled categories with a chip from the four state colours, a default category, post-time
// rules from a fixed predicate set, sort keys from a fixed set, grouping by session, the owner rank, the notification set,
// the badge category, the kinds) is what a data-defined board can say; anything past it (a new section body, a new action,
// a fifth chip colour, a new sort or predicate) is a release, for the feed and for every other board alike.

export type Chip = "working" | "blocked" | "completed" | "neutral";   // the state-chip classes styles.css has; neutral = the dim .chip
export const CHIPS: readonly Chip[] = ["working", "blocked", "completed", "neutral"];
export type SortField = "t" | "session" | "owner" | "title";
export const SORT_FIELDS: readonly SortField[] = ["t", "session", "owner", "title"];
export interface SortKey { key: SortField; dir: "asc" | "desc"; }
export interface Predicate { needsYou?: boolean; producer?: string; keyPrefix?: string; }   // every present member must hold
export const PREDICATE_KEYS: readonly (keyof Predicate)[] = ["needsYou", "producer", "keyPrefix"];
export interface Rule { when: Predicate; category: string; }
export type OrderRule = "ownerRank";
export const ORDER_RULES: readonly OrderRule[] = ["ownerRank"];
export type KindId = "goal" | "placeholder" | "parked" | "quarantine" | "notice";
export const KIND_IDS: readonly KindId[] = ["goal", "placeholder", "parked", "quarantine", "notice"];

export interface Category { id: string; title: string; chip: Chip; }

export interface Board {
  id: string;
  title: string;
  categories: readonly Category[];
  defaultCategory: string;
  rules: readonly Rule[];
  sort: SortKey;
  subSorts: readonly SortKey[];
  groupBy: "session" | null;
  order: readonly OrderRule[];
  notify: readonly string[];
  needsYou: string | null;
  kinds: readonly KindId[];
}

/** The feed's column keys as feed.ts, feed.css and the view state spell them today (the renderer's local keys). The
 *  category ids are the kernel's raw column values; this table is the feed definition's own mapping between the two, and
 *  it stays until the sweep that renames the CSS keys (not this change). */
export type FeedColumnKey = "asks" | "needsInput" | "completed";
/** The kernel's raw column values, the feed board's category ids (AskItem.column is typed to them). */
export type FeedCategory = "working" | "needs_input" | "completed";
// a Map, never a plain object: a category is producer-facing from phase three, and a prototype-named one ("toString",
// "constructor") read a plain object's prototype member where the old ternary read the default (the 1834 read, low 1)
export const FEED_LOCAL_KEY: ReadonlyMap<string, FeedColumnKey> = new Map([["working", "asks"], ["needs_input", "needsInput"], ["completed", "completed"]]);

/** Today's feed, as one definition. Every value is a literal feed.ts or kernel.py carries now; board-def.test.ts asserts
 *  them against the source, so this constant cannot drift from what renders. */
export const FEED_BOARD: Board = {
  id: "feed",
  title: "Feed",
  categories: [
    { id: "working", title: "Working", chip: "working" },          // the user's rename, 2026-06-11: every card is an ask being worked
    { id: "needs_input", title: "Blocked", chip: "blocked" },      // needs the user's input to move on
    { id: "completed", title: "Completed", chip: "completed" },    // done, ready for review and clear
  ],
  defaultCategory: "working",
  rules: [],                                  // the feed's category rule is code: the kernel's expression in build_feed
  sort: { key: "t", dir: "asc" },             // oldest at the top (the user 2026-06-27); the newestFirst preference flips it
  subSorts: [],
  groupBy: "session",                         // grouped mode, default on (the user 2026-07-13)
  order: ["ownerRank"],                       // what feed.ts does (PR 1831): the owner-less run first, then the session order, then time
  notify: ["needs_input", "completed"],       // kernel.py _NOTIFY_COLUMNS
  needsYou: "needs_input",                    // kernel.py _needs_you_count
  kinds: ["goal", "placeholder", "parked", "quarantine", "notice"],
};

// ── the kinds: the card families feed.ts renders, described ────────────────────────────────────────────────────────────
// A kind's sections and actions are code (the plan's section 1); the descriptors below name each by its id, the label the
// button wears (null when the label is computed per card) and the feed.ts function whose body mints the element that wears
// it (`via`), so the test can hold each label to that function's own source and each kind's list to the reviewed table. The elements are minted by makeAskCard in one fixed order for every kind
// and shown or hidden by updateAskCard and applySections; nothing here changes that.
export interface Descriptor { id: string; label: string | null; via: string; }
export interface CardKind { id: KindId; sections: readonly Descriptor[]; actions: readonly Descriptor[]; menu: readonly Descriptor[]; }

const CLEAR: Descriptor = { id: "clear", label: "Clear", via: "clearButton" };
const BELL: Descriptor = { id: "bell", label: null, via: "setCardNotify" };
const MENU: readonly Descriptor[] = [
  { id: "notify", label: null, via: "showCardMenu" },            // "Notify me" / "Stop notifying", by the card's armed state
  { id: "browse", label: "Browse files", via: "showCardMenu" },  // web only (canPreview)
];

export const FEED_KINDS: Readonly<Record<KindId, CardKind>> = {
  goal: {
    id: "goal",
    sections: [                                                   // the toggles makeAskCard mints; applySections wires them
      { id: "bg", label: "Background", via: "makeAskCard" },
      { id: "summary", label: "Summary", via: "makeAskCard" },
      { id: "subgoals", label: null, via: "makeAskCard" },        // "N sub-goals", the count in the label (applySections)
      { id: "stall", label: "Stalled", via: "makeAskCard" },
      { id: "tasks", label: null, via: "makeAskCard" },           // the awaiting pill's word (spin-caption awaitWord)
    ],
    actions: [
      CLEAR,
      { id: "followUp", label: "Follow up", via: "renderModalNow" },
      { id: "checkStatus", label: "Check status", via: "renderModalNow" },
      { id: "continue", label: "Continue", via: "makeAskCard" },
      { id: "retry", label: "Retry", via: "makeAskCard" },
      { id: "login", label: "Log in…", via: "makeAskCard" },
      { id: "capSwitch", label: null, via: "makeAskCard" },
      BELL,
    ],
    menu: MENU,
  },
  placeholder: { id: "placeholder", sections: [{ id: "tasks", label: null, via: "makeAskCard" }], actions: [CLEAR, BELL], menu: MENU },
  parked: { id: "parked", sections: [], actions: [CLEAR, { id: "revive", label: "Revive", via: "makeAskCard" }, BELL], menu: MENU },
  quarantine: {
    id: "quarantine", sections: [],
    actions: [{ id: "approve", label: "Approve", via: "makeAskCard" }, { id: "deny", label: "Deny", via: "makeAskCard" }, BELL],
    menu: MENU,
  },
  notice: {
    id: "notice",
    sections: [{ id: "body", label: null, via: "noticeBodyNodes" }, { id: "attachment", label: null, via: "updateAskCard" }],
    actions: [{ id: "stored", label: null, via: "updateAskCard" }, CLEAR, BELL],   // the record's own actions (noticeAction)
    menu: MENU,
  },
};

/** The card's kind, by the flavour that discriminates the families today (no `kind` field on the record). */
export function kindOf(card: { notice?: unknown; provisional?: unknown; blocked?: { state?: string } | null }): KindId {
  if (card.notice) return "notice";
  const st = card.blocked && card.blocked.state;
  if (st === "quarantine") return "quarantine";
  if (st === "parkedHandoff") return "parked";
  if (card.provisional) return "placeholder";
  return "goal";
}

// ── the data-defined boards the frame carries (plans/card-boards.md, phase three) ────────────────────────────────────
// The kernel ships the definitions a producer or the user made through the door under the frame's `boards` field, data
// boards only; the renderer holds the code constants itself and merges the two with code winning on an id (the door
// refuses a reserved id, so the case never arises from a well-behaved kernel; a hostile file is skipped there too). Every
// shipped definition passes the client half of the check before it is held, so a frame from an older or a foreign kernel
// can never hand the renderer a board outside the schema.
const dataBoards = new Map<string, Board>();
/** Take the frame's `boards` (or a merged frame's): the map replaced whole; returns how many definitions were held. */
export function adoptBoards(raw: unknown): number {
  dataBoards.clear();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return 0;
  for (const [id, defn] of Object.entries(raw as Record<string, unknown>)) {
    if (RESERVED_BOARD_IDS.includes(id)) continue;
    if (boardCheck(defn) !== null) continue;
    const b = defn as Board;
    if (b.id !== id) continue;
    dataBoards.set(id, b);
  }
  return dataBoards.size;
}
/** Every board the renderer knows, code first. */
export function knownBoards(): Board[] { return [FEED_BOARD, ...dataBoards.values()]; }
/** The board a card names, or the feed's for a card naming none or an id this renderer does not know. */
export function boardOf(card: { board?: string | null }): Board {
  return (card.board && card.board !== FEED_BOARD.id && dataBoards.get(card.board)) || FEED_BOARD;
}

// ── the reads feed.ts makes ───────────────────────────────────────────────────────────────────────────────────────────

/** The renderer's local column key for a category id; an unknown id files under the board's default category, which is
 *  what the old mapping did for anything but the two named values. */
export function columnOf(board: Board, category: string): FeedColumnKey {
  return FEED_LOCAL_KEY.get(category) ?? FEED_LOCAL_KEY.get(board.defaultCategory) ?? "asks";
}

/** Whether a card in `category` is one the board's badge counts (the interrupt rule every lens lets through). */
export function isNeedsYou(board: Board, category: string): boolean {
  return board.needsYou !== null && category === board.needsYou;
}

/** The board's columns in its order, as the local keys the layout and the view state use. */
export function feedColumns(board: Board): readonly FeedColumnKey[] {
  return board.categories.map((c) => columnOf(board, c.id));
}

/** The header triples ensureCols iterates: [local key, title, chip class suffix], in the board's order. */
export function columnTable(board: Board): ReadonlyArray<readonly [FeedColumnKey, string, string]> {
  return board.categories.map((c) => [columnOf(board, c.id), c.title, c.chip] as const);
}

// ── the validator, client half ────────────────────────────────────────────────────────────────────────────────────────
// One rule set for the code constants (asserted by the test) and for the definitions the frame will carry (phase three).
// A refusal is prose naming the member and the rule it broke; an unknown member refuses, so a typo never silently
// defaults. The kernel's half applies the same rules at the door.

const ID_RE = /^[a-z][a-z0-9_-]{0,31}$/;
const MEMBERS = ["id", "title", "categories", "defaultCategory", "rules", "sort", "subSorts", "groupBy", "order", "notify", "needsYou", "kinds"];
export const RESERVED_BOARD_IDS: readonly string[] = ["feed"];   // the code-defined ids: a define of one is refused

function isObj(x: unknown): x is Record<string, unknown> { return !!x && typeof x === "object" && !Array.isArray(x); }

function checkSort(s: unknown, where: string): string | null {
  if (!isObj(s)) return where + " must be an object {key, dir}";
  for (const k of Object.keys(s)) if (k !== "key" && k !== "dir") return where + " has an unknown member " + JSON.stringify(k);
  if (!SORT_FIELDS.includes(s.key as SortField)) return where + ".key must be one of " + SORT_FIELDS.join(", ");
  if (s.dir !== "asc" && s.dir !== "desc") return where + ".dir must be asc or desc";
  return null;
}

/** null when `defn` is a board in the schema; else the refusal. `allowReserved` lets the code constants pass their own ids. */
export function boardCheck(defn: unknown, opts: { allowReserved?: boolean } = {}): string | null {
  if (!isObj(defn)) return "a board definition must be a JSON object";
  for (const k of Object.keys(defn)) if (!MEMBERS.includes(k)) return "unknown member " + JSON.stringify(k) + " (the schema's members are " + MEMBERS.join(", ") + ")";
  const id = defn.id;
  if (typeof id !== "string" || !ID_RE.test(id)) return "id must match [a-z][a-z0-9_-]{0,31}";
  if (!opts.allowReserved && RESERVED_BOARD_IDS.includes(id)) return "id " + JSON.stringify(id) + " is a code-defined board and cannot be defined";
  if (typeof defn.title !== "string" || !defn.title.length || defn.title.length > 40) return "title must be 1 to 40 characters";
  const cats = defn.categories;
  if (!Array.isArray(cats) || cats.length < 1 || cats.length > 8) return "categories must hold 1 to 8 entries";
  const ids = new Set<string>();
  for (const c of cats) {
    if (!isObj(c)) return "each category must be an object {id, title, chip}";
    for (const k of Object.keys(c)) if (k !== "id" && k !== "title" && k !== "chip") return "category has an unknown member " + JSON.stringify(k);
    if (typeof c.id !== "string" || !ID_RE.test(c.id)) return "category id must match [a-z][a-z0-9_-]{0,31}";
    if (ids.has(c.id)) return "category id " + JSON.stringify(c.id) + " repeats";
    ids.add(c.id);
    if (typeof c.title !== "string" || !c.title.length || c.title.length > 40) return "category " + c.id + ": title must be 1 to 40 characters";
    if (!CHIPS.includes(c.chip as Chip)) return "category " + c.id + ": chip must be one of " + CHIPS.join(", ");
  }
  if (typeof defn.defaultCategory !== "string" || !ids.has(defn.defaultCategory)) return "defaultCategory must name one of the board's categories";
  const rules = defn.rules;
  if (!Array.isArray(rules) || rules.length > 16) return "rules must hold 0 to 16 entries";
  for (const r of rules) {
    if (!isObj(r) || !isObj(r.when)) return "each rule must be an object {when, category}";
    for (const k of Object.keys(r)) if (k !== "when" && k !== "category") return "rule has an unknown member " + JSON.stringify(k);
    for (const k of Object.keys(r.when)) if (!PREDICATE_KEYS.includes(k as keyof Predicate)) return "rule predicate has an unknown member " + JSON.stringify(k) + " (the predicates are " + PREDICATE_KEYS.join(", ") + ")";
    if (!Object.keys(r.when).length) return "a rule's predicate must name at least one member";
    if ("needsYou" in r.when && typeof r.when.needsYou !== "boolean") return "rule predicate needsYou must be a boolean";
    if ("producer" in r.when && typeof r.when.producer !== "string") return "rule predicate producer must be a string";
    if ("keyPrefix" in r.when && typeof r.when.keyPrefix !== "string") return "rule predicate keyPrefix must be a string";
    if (typeof r.category !== "string" || !ids.has(r.category)) return "a rule's category must name one of the board's categories";
  }
  const sortErr = checkSort(defn.sort, "sort");
  if (sortErr) return sortErr;
  const subs = defn.subSorts;
  if (!Array.isArray(subs) || subs.length > 6) return "subSorts must hold 0 to 6 entries";
  for (let i = 0; i < subs.length; i++) { const e = checkSort(subs[i], "subSorts[" + i + "]"); if (e) return e; }
  if (defn.groupBy !== "session" && defn.groupBy !== null) return "groupBy must be \"session\" or null";
  const order = defn.order;
  if (!Array.isArray(order) || order.length > 4) return "order must hold 0 to 4 entries";
  for (const o of order) if (!ORDER_RULES.includes(o as OrderRule)) return "order rules must be from " + ORDER_RULES.join(", ");
  const notify = defn.notify;
  if (!Array.isArray(notify)) return "notify must be a list of category ids";
  for (const n of notify) if (typeof n !== "string" || !ids.has(n)) return "notify names a category the board does not have: " + JSON.stringify(n);
  if (defn.needsYou !== null && (typeof defn.needsYou !== "string" || !ids.has(defn.needsYou))) return "needsYou must be one of the board's categories or null";
  const kinds = defn.kinds;
  if (!Array.isArray(kinds) || !kinds.length) return "kinds must hold at least one entry";
  for (const k of kinds) if (!KIND_IDS.includes(k as KindId)) return "kinds must be from " + KIND_IDS.join(", ");
  return null;
}

/** A data-defined board's defaults (the plan's section 1 and the door's "created on first use"): the id's title, one
 *  category, neutral dress, newest first, no grouping, no notifications, the notice kind. Pure; the kernel applies the
 *  same defaults at the door. */
export function defaultBoard(id: string, category = "notes"): Board {
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return {
    id, title: cap(id).slice(0, 40),
    categories: [{ id: category, title: cap(category).slice(0, 40), chip: "neutral" }],
    defaultCategory: category, rules: [], sort: { key: "t", dir: "desc" }, subSorts: [], groupBy: null, order: [],
    notify: [], needsYou: null, kinds: ["notice"],
  };
}
