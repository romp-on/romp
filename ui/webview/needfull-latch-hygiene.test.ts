// The page's one-shot full ask (render.ts requestFullSession) latches a sid in awaitingFull until the full session frame
// that answers it lands in upsert, or the local socket reopens (romp:wsup). Three asks can never be answered on that road,
// and before 2026-09-19 each held its tab at its last applied content until a reload, silently: a delta refused while the
// sid was latched left no row; a dismissed tab kept its latch (a tab that left the strip gets no answer); a remote host's
// ask parked against a dead relay socket stayed latched through the relay's reopen, which fires romp:hostRelayUp and never
// romp:wsup. The mechanism (one ask per desync) stands; these tests pin the row and the two clears. No jsdom for the
// webview: the latch block is lifted from render.ts with esbuild (the chat-exact-tail-exec.test.ts pattern) and driven over
// stubs, and dismissSession, which cannot be lifted whole, is pinned at the source. Synthetic ids only (the notes-api demo
// world: sessions web/api/tests; hosts peer/other).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { hostOf } from "./host-prefix";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

type Posted = { type: string; [k: string]: unknown };
type LatchApi = {
  requestFullSession: (id: string, why: string) => void;
  clearAsksForHost?: (h: string) => void;
  awaitingFull: Set<string>;
  pendingFullWhy: Map<string, string>;
};

/** The latch block (the two stores, requestFullSession and its per-host clear) lifted from render.ts and closed over a
 *  recording vscodeApi and the real hostOf. The socket-open clears that follow the block are pinned elsewhere. */
function liftLatch(): { api: LatchApi; posts: Posted[] } {
  const a = RENDER.indexOf("const awaitingFull = new Set<string>();");
  const b = RENDER.indexOf("// A reconnect mints a FRESH kernel-side client", a);
  assert.ok(a > 0 && b > a, "the latch block's anchors moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
  const posts: Posted[] = [];
  const vscodeApi = { postMessage: (m: Posted) => { posts.push(m); } };
  const make = new Function("vscodeApi", "hostOf", js + `
    return { requestFullSession, awaitingFull, pendingFullWhy,
             clearAsksForHost: typeof clearAsksForHost === "function" ? clearAsksForHost : undefined };`) as (v: unknown, h: unknown) => LatchApi;
  return { api: make(vscodeApi, hostOf), posts };
}

const WEB = "web", API = "api";

const row = (sid: string, why: string) => ({ type: "clientDiag", surface: "chat", what: "delta-refused", data: { sid, why } });
const rows = (posts: Posted[]) => posts.filter((m) => m.type === "clientDiag");

test("a delta refused by the latch files one clientDiag row naming the sid and the why, for each of the three delta reasons; the ask itself is not repeated", () => {
  // A skeleton tab latched by a click whose answer never comes (a status frame where the full was owed leaves the tab a
  // skeleton on both sides): every later delta for it arrives as skeleton-delta. The one arm no test drove before 2026-09-19.
  const click = liftLatch();
  click.api.requestFullSession(API, "skeleton-click");
  click.api.requestFullSession(API, "skeleton-delta");
  click.api.requestFullSession(API, "skeleton-delta");
  assert.deepEqual(click.posts.filter((m) => m.type === "needFull"), [{ type: "needFull", id: API, why: "skeleton-click" }], "the click's one ask stands");
  assert.deepEqual(rows(click.posts), [row(API, "skeleton-delta"), row(API, "skeleton-delta")],
    "a delta for a tab still held as a skeleton is a refused delta and leaves its row (2026-09-19)");
  // A gap ask, then the pusher's next deltas while the answer is outstanding.
  const { api, posts } = liftLatch();
  api.requestFullSession(WEB, "gap");
  assert.deepEqual(posts, [{ type: "needFull", id: WEB, why: "gap" }], "the first ask goes out");
  api.requestFullSession(WEB, "gap");      // the pusher's next delta, still latched
  api.requestFullSession(WEB, "nobase");
  assert.equal(posts.filter((m) => m.type === "needFull").length, 1, "one ask per desync stands");
  assert.deepEqual(rows(posts), [row(WEB, "gap"), row(WEB, "nobase")],
    "every refused delta leaves a row: the live number a latch whose answer never comes was missing (2026-09-19)");
  assert.equal(api.pendingFullWhy.get(WEB), "gap", "a refused ask never overwrites the pending reason");
  // The vocabulary, once: the same reason twice on one sid. A delta reason's repeat is a refused delta and files a row; a
  // click's or the prefetch's repeat is the dedup by design and files nothing.
  const filed: Record<string, number> = {};
  for (const why of ["gap", "nobase", "skeleton-delta", "skeleton-click", "prefetch"]) {
    const twice = liftLatch();
    twice.api.requestFullSession(WEB, why); twice.api.requestFullSession(WEB, why);
    filed[why] = rows(twice.posts).length;
  }
  assert.deepEqual(filed, { gap: 1, nobase: 1, "skeleton-delta": 1, "skeleton-click": 0, prefetch: 0 },
    "the row is about deltas the page could not apply: the three delta reasons and no other (2026-09-19)");
});

test("a click's or the prefetch's dedup on the latch is by design and files nothing; an empty id asks nothing and files nothing", () => {
  const { api, posts } = liftLatch();
  api.requestFullSession(API, "skeleton-click");
  api.requestFullSession(API, "skeleton-click");
  api.requestFullSession(API, "prefetch");
  api.requestFullSession("", "gap");
  assert.deepEqual(posts, [{ type: "needFull", id: API, why: "skeleton-click" }],
    "the row is about deltas the page could not apply; a repeated click and the idle chain dedup by design");
});

test("dismissSession forgets the dismissed tab's parked ask beside onDismiss: a tab that left the strip gets no answer", () => {
  const i = RENDER.indexOf("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {");
  assert.ok(i > 0, "dismissSession not found");
  const fn = RENDER.slice(i, RENDER.indexOf("\n}\n", i));
  assert.match(fn, /onDismiss\(skeletonTabs, id\);[^\n]*\n\s*awaitingFull\.delete\(id\); pendingFullWhy\.delete\(id\);/,
    "the latch and its reason go with the tab, right after the skeleton set forgets it (2026-09-19)");
});

test("the relay's reopen forgets that host's parked asks and leaves the local kernel's, which the local socket's own reopen clears", () => {
  assert.ok(RENDER.includes("function clearAsksForHost(h: string): void {"), "the per-host clear exists beside the latch (2026-09-19)");
  const { api } = liftLatch();
  for (const sid of [API, "peer:web", "peer:tests", "other:api"]) api.requestFullSession(sid, "gap");
  api.clearAsksForHost!("");
  assert.deepEqual([...api.awaitingFull].sort(), [API, "other:api", "peer:tests", "peer:web"],
    "an empty host is the local kernel: never cleared here (the local socket's romp:wsup does that)");
  api.clearAsksForHost!("peer");
  assert.deepEqual([...api.awaitingFull].sort(), [API, "other:api"], "only the reopened host's sids leave the latch");
  assert.deepEqual([...api.pendingFullWhy.keys()].sort(), [API, "other:api"], "…and their reasons with them");
  api.requestFullSession("peer:web", "gap");
  assert.ok(api.awaitingFull.has("peer:web"), "a freed sid can ask again on the fresh relay");
});

test("the romp:hostRelayUp listener is where the per-host clear runs, inside the existing listener", () => {
  const m = RENDER.match(/window\.addEventListener\("romp:hostRelayUp", \(e\) => \{([\s\S]*?)\n\}\);/);
  assert.ok(m, "the romp:hostRelayUp listener exists");
  assert.match(m![1], /\n\s*clearAsksForHost\(h\);/, "the relay's open clears that host's parked asks (2026-09-19)");
  assert.equal((RENDER.match(/clearAsksForHost\(/g) || []).length, 2, "the definition and the one call: no other event clears by host");
});
