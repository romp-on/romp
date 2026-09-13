// A kernel-served pane whose federation manager never came up (2026-09-10). One chat column of a split dashboard
// loaded without federation.js: the shim's deliver() fell to its window-dispatch branch, the pane consumed the
// kernel's RAW frames with no arrangement applied (the strip showed the kernel's seed order and reshuffled on every
// push), and every drag in that column wrote the seed over the browser's arrangement for the other column. Both
// fallbacks were silent, against the fail-loudly rule. The pure decision and the load-entry reader (frame-listener.ts)
// are executed here; the pane's boot check, its guards on the arrangement writer and the drag, and the banner are
// pinned at source, the repo's convention where there is no DOM (tests/test_federation_missing_served.py drives the
// real page with the bundle aborted).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { federationMissing, federationLoadEntry, fedRetryKey } from "./frame-listener";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const CSS = read("styles.css");

test("the manager is MISSING only where the shim is present and the slot is not: a VS Code webview is not that", () => {
  const send = () => {};
  assert.equal(federationMissing({ __rompLocalSend: send }), true, "a kernel page: shim yes, manager no");
  assert.equal(federationMissing({ __rompLocalSend: send, __rompFed: { inbound() {} } }), false, "a healthy kernel page");
  assert.equal(federationMissing({}), false, "a VS Code webview has no shim and no manager: nothing is missing");
  assert.equal(federationMissing({ __rompFed: {} }), false);
  assert.equal(federationMissing({ __rompLocalSend: "not a function" }), false, "the shim publishes a function; anything else is not the shim");
});

test("the load entry names federation.js by its served path and reports the four numbers that tell a failed fetch from a failed evaluation", () => {
  const entries = [
    { name: "http://127.0.0.1:1/dist/render.js?v=1", duration: 12.4, transferSize: 900000, encodedBodySize: 899000, responseStatus: 200 },
    { name: "http://127.0.0.1:1/dist/federation.js?v=1", duration: 3600.6, transferSize: 0, encodedBodySize: 0, responseStatus: 0 },
  ];
  assert.deepEqual(federationLoadEntry(entries), { duration: 3601, transferSize: 0, encodedBodySize: 0, responseStatus: 0 }, "a fetch that never came back");
  assert.deepEqual(federationLoadEntry([{ name: "http://h/dist/federation.js", duration: 40, transferSize: 120000, encodedBodySize: 119000, responseStatus: 200 }]),
    { duration: 40, transferSize: 120000, encodedBodySize: 119000, responseStatus: 200 }, "a body that arrived: the failure was in evaluating it");
  assert.deepEqual(federationLoadEntry([{ name: "http://h/dist/federation.js" }]), { duration: 0, transferSize: 0, encodedBodySize: 0, responseStatus: -1 }, "an older browser records no status: -1, never a guess");
  assert.equal(federationLoadEntry([{ name: "http://h/dist/federation.js.map", duration: 1 }]), null, "the map is not the bundle");
  assert.equal(federationLoadEntry([]), null);
});

test("render.ts: the boot check fails loudly — one reload, then one diag row per incident, the marker consumed, and a banner IN FLOW above the strip", () => {
  assert.match(RENDER, /import \{ listenForFrames, federationMissing, federationLoadEntry, fedRetryKey \} from "\.\/frame-listener";/);
  const boot = RENDER.slice(RENDER.indexOf("const fedMissing = federationMissing(window as any);"), RENDER.indexOf("listenForFrames(perfFrameHandler("));
  assert.ok(boot.length > 0 && boot.length < 5000, "the check sits right ahead of the frame listener");
  assert.match(boot, /const FED_RETRY = fedRetryKey\(location\);/, "the marker is per document: two chat columns of one tab never share a retry");
  assert.match(boot, /const entry = fedMissing \? federationLoadEntry\(typeof performance !== "undefined" \? \(performance\.getEntriesByType\("resource"\) as any\[\]\) : \[\]\) : null;/);
  // the retry: counted in the marker WITH the first pass's entry, and taken only when it could be counted (blocked storage → no reload: cannot loop)
  assert.match(boot, /if \(fedMissing && !first\) \{\n\s*let counted = false;\n\s*try \{ sessionStorage\.setItem\(FED_RETRY, JSON\.stringify\(\{ load: entry \}\)\); counted = true; \} catch \{[^}]*\}\n\s*if \(counted\) location\.reload\(\);/);
  // the row is filed by the pass that stays up: the failed retry (both entries), which then CONSUMES the marker so the next load gets its own retry
  assert.match(boot, /what: "federation-missing", data: \{ load: entry, first: first \? first\.load : null \} \}\);\n\s*try \{ sessionStorage\.removeItem\(FED_RETRY\); \}/);
  // …or the recovered boot (the first), which clears it too
  assert.match(boot, /if \(!fedMissing && first\) \{[^\n]*\n\s*vscodeApi\?\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "federation-missing", data: \{ recovered: true, first: first\.load \} \}\);\n\s*try \{ sessionStorage\.removeItem\(FED_RETRY\); \}/);
  // the banner: plain words about what the user sees, the reload in hand, and IN FLOW ahead of the strip — never a fixed bar over it
  assert.match(boot, /id: "rfed"/);
  assert.match(boot, /Part of this pane failed to load, so its session tabs are out of order and can't be dragged\./);
  assert.match(boot, /textContent: "Reload"/);
  assert.match(boot, /const strip = document\.getElementById\("tabbar"\);\n\s*if \(strip && strip\.parentNode\) strip\.parentNode\.insertBefore\(bar, strip\); else document\.body\.prepend\(bar\);/);
  assert.doesNotMatch(boot, /body\.appendChild\(/, "not appended after the composer, where a fixed rule would put it over the strip");
});

test("render.ts: for as long as the page is in this state every drag starter and the arrangement writer stand down, and a refused drop is a cancelled drag", () => {
  assert.match(RENDER, /function commitTabOrder\(\) \{\n\s*if \(fedMissing\) return;/, "one-writer principle: an order that never passed through the arrangement is not one");
  assert.match(RENDER, /function reorderTo\(dragId: string, targetId: string, after: boolean\): boolean \{[^\n]*\n\s*if \(fedMissing \|\| settings\.tabsLocked\) return false;/, "a drop is refused, and says so");
  assert.match(RENDER, /if \(prev\?\.dataset\?\.id\) tabDragCommitted = reorderTo\(draggedId, prev\.dataset\.id, true\);\n\s*else if \(next\?\.dataset\?\.id\) tabDragCommitted = reorderTo\(draggedId, next\.dataset\.id, false\);/,
    "the drop is committed only when the reorder happened: a refused one FLIPs the strip home on dragend");
  assert.match(RENDER, /tab\.addEventListener\("dragstart", \(e\) => \{\n\s*if \(fedMissing \|\| settings\.tabsLocked\) \{ e\.preventDefault\(\); return; \}/, "the one drag starter refuses first (skeleton tabs and rename-restored tabs included)");
  // every draggable flag agrees (the pinned-tabs peer branch adds its own clause to the strip's, hence the optional group)
  assert.match(RENDER, /tab\.draggable = !s\.sub && (?:!pinned && )?!fedMissing && !isProvisionalId\(id\) && !settings\.tabsLocked;/, "the strip's tabs (a create in flight has no session to move either: the chat split, 2026-09-11)");
  assert.equal((RENDER.match(/tab\.draggable = !fedMissing && !settings\.tabsLocked;/g) || []).length, 2, "the skeleton tab and the rename's restore (and never while the tabs are locked, T395)");
  assert.doesNotMatch(RENDER, /tab\.draggable = true;/, "no starter is unconditionally draggable any more");
});

test("render.ts: a session frame's id joins the order through adoptArrival (executed in tab-order.test.ts), and the revive skeleton's push is guarded too", () => {
  assert.match(RENDER, /adoptArrival\(order, msg\.id, existed\);/);
  assert.doesNotMatch(RENDER, /if \(!existed\) order\.push\(msg\.id\);/);
  assert.match(RENDER, /if \(!order\.includes\(id\)\) order\.push\(id\);/, "showReviveLoader");
});

test("frame-listener.ts: the retry marker's key names the document — two chat columns differ, one column is stable across its reloads", () => {
  assert.equal(fedRetryKey({ pathname: "/chat", search: "" }), "romp:fed-retry:/chat");
  assert.equal(fedRetryKey({ pathname: "/chat", search: "?col=2" }), "romp:fed-retry:/chat?col=2");
  assert.notEqual(fedRetryKey({ pathname: "/chat", search: "" }), fedRetryKey({ pathname: "/chat", search: "?col=2" }));
  assert.equal(fedRetryKey({ pathname: "/chat", search: "?col=2" }), fedRetryKey({ pathname: "/chat", search: "?col=2" }), "the same column, the same key, reload after reload");
});

test("styles.css: the banner wears the pipe-down bar's dress but sits IN FLOW (a flex-column child), never fixed over the strip", () => {
  assert.match(CSS, /#rfed \{ flex: 0 0 auto; text-align: center;\n\s*background: #2b2d30; color: #e6e6e6; border-bottom: 1px solid #4a4d51; padding: 7px 14px; \}/);
  assert.doesNotMatch(CSS, /#rfed \{[^}]*position: fixed/, "the #rhostoff mistake: a fixed bar across the top hid the strip's working controls");
  assert.match(CSS, /#rfed button \{ margin-left: 8px; font: inherit;/);
});
