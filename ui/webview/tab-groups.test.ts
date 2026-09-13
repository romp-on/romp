// TAB GROUPS ARE TAGS (the user 2026-09-04): the chat tab strip sections by tag — every tag a
// session carries (T264b, 2026-09-08; the home-tag rule is retired) — with the untagged trailing, per-browser on/off and
// per-section fold state under romp:tabgroups, and the section headers draggable to reorder
// tagOrder (the kernel-persisted union order the timeline's pill drag writes too). Executed tests on
// the pure module + source pins on render.ts / tag-menu.ts / styles.css (the tab-order.ts pattern;
// no jsdom for render.ts). Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewTagUnion, type TagUnion } from "./session-views";
import * as TG_MOD from "./tab-groups";
import { sectionTabs, anySectioned, parseTabGroups, readTabGroups, writeTabGroups, isSectionCollapsed,
         toggleSectionCollapsed, setSectionCollapsed, planStrip, reorderTagOrder, applyTagOrder, TABGROUPS_KEY,
         DEFAULT_COLLAPSED, sectionRef, isPinned, setPinned, togglePinned, prunePinned, reachableFrom, tagRenames, followTagRenames, followAdoption,
         sameTagNames, headWords, homeSectionOf, neighborOfFolded, type TabSection, type SectionRef, type TabGroupsState } from "./tab-groups";
import { sectionPipTitle } from "./tab-state";
import { DEFAULT_SETTINGS } from "./settings";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const GEAR = ui("webview", "gear.js");
const SETTINGS = ui("webview", "settings.ts");
const MENU = ui("webview", "tag-menu.ts");
const VIEWS = ui("webview", "session-views.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const GUIDE = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "guide.md"), "utf8");

// the notes-api demo world: web + api in "infra", tests in "qa", a loose one; api ALSO in qa
const V = {
  active: "all",
  tags: [
    { id: "g1", name: "qa", color: "#DD42FF", members: ["tests", "api"] },
    { id: "g2", name: "infra", color: "#4EC9B0", members: ["web", "api"] },
    { id: "g3", name: "empty", color: "#e0af68", members: [] },
  ],
  remoteTags: [{ id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "remotepool", color: "#7aa2f7", members: ["TESTHOST-A:m1"] }],
};

test("executed: the every-tag rule (T264b) — a section per tag holding EVERY visible member it carries; sections in union order; the untagged trail", () => {
  // the user 2026-09-08: tags are equivalent, so a session under two tags appears under both — the
  // home-tag rule (first holder in tagOrder) is retired
  const unions = viewTagUnion(V);
  const secs = sectionTabs(["web", "api", "loose", "tests"], unions);
  assert.deepEqual(secs.map((s) => [s.name, s.ids]),
    [["qa", ["api", "tests"]], ["infra", ["web", "api"]], [null, ["loose"]]],
    "api holds qa AND infra — it appears under both; tabs keep strip order inside; the loose one trails");
  assert.equal(secs[0].color, "#DD42FF", "the section wears its tag's colour");
  assert.equal(typeof (TG_MOD as Record<string, unknown>).homeTag, "undefined", "homeTag is retired: nothing sections by a first holder any more");
  // revealIn (session-views.ts) adds the session's FIRST holder to the lens — any holder reveals it now that every tag shows it
  assert.match(VIEWS, /const holder = unions\.find\(\(u\) => u\.members\.includes\(id\)\);/);
  // reorder the tags and the sections follow — this is why reordering is first-class; api stays in both
  const flipped = viewTagUnion({ ...V, tagOrder: ["infra", "qa"] });
  assert.deepEqual(sectionTabs(["web", "api", "tests"], flipped).map((s) => [s.name, s.ids]),
    [["infra", ["web", "api"]], ["qa", ["api", "tests"]]]);
  // the trail holds only ids in NO tag
  assert.deepEqual(sectionTabs(["api", "loose"], unions).map((s) => [s.name, s.ids]), [["qa", ["api"]], ["infra", ["api"]], [null, ["loose"]]]);
  // a tag with no visible member yields no section; a remote-homed tag sections by NAME like any other
  assert.ok(!secs.some((s) => s.name === "empty"));
  assert.deepEqual(sectionTabs(["TESTHOST-A:m1"], unions).map((s) => s.name), ["remotepool"]);
});

test("executed: sectioning is on by default exactly when some tag holds a visible tab", () => {
  const unions = viewTagUnion(V);
  assert.equal(anySectioned(["web"], unions), true);
  assert.equal(anySectioned(["loose", "other"], unions), false, "an untagged world keeps the flat strip");
  assert.equal(anySectioned([], unions), false);
  assert.deepEqual(sectionTabs(["loose"], unions), [{ name: null, localId: null, color: "", ids: ["loose"] }]);
});

test("executed: per-browser state — on by default, archived starts folded, toggles remember, junk reads as the default", () => {
  const d = parseTabGroups(null);
  assert.deepEqual(d, { on: true, collapsed: [], expanded: [], pinned: [] });
  assert.equal(isSectionCollapsed(d, "infra"), false);
  assert.equal(isSectionCollapsed(d, "archived"), true, "the archived tag exists to put sessions away — folded until opened");
  assert.ok(DEFAULT_COLLAPSED.has("archived"));
  const folded = toggleSectionCollapsed(d, "infra");
  assert.equal(isSectionCollapsed(folded, "infra"), true);
  assert.equal(isSectionCollapsed(toggleSectionCollapsed(folded, "infra"), "infra"), false, "toggling back opens it");
  const opened = toggleSectionCollapsed(d, "archived");
  assert.equal(isSectionCollapsed(opened, "archived"), false, "opening a default-folded section is remembered…");
  assert.deepEqual(opened.expanded, ["archived"]);
  assert.equal(isSectionCollapsed(toggleSectionCollapsed(opened, "archived"), "archived"), true, "…and folding it again drops the memory");
  assert.deepEqual(parseTabGroups('{"on":false,"collapsed":["qa",3],"expanded":"x"}'), { on: false, collapsed: ["qa"], expanded: [], pinned: [] },
    "wrong-typed entries drop; on=false is the one way off");
  assert.deepEqual(parseTabGroups("not json"), d, "a corrupt entry costs the preference, never the dashboard");
  assert.deepEqual(parseTabGroups("[1,2]"), d);
});

test("executed: write → read round-trips through localStorage under romp:tabgroups (the view-order two-path idiom)", () => {
  const store = new Map<string, string>();
  const g: any = globalThis;
  const savedLS = g.localStorage;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  try {
    writeTabGroups({ on: false, collapsed: ["qa"], expanded: ["archived"], pinned: [{ sid: "web", name: "infra", id: "g2" }] });
    assert.ok(store.has(TABGROUPS_KEY), "persisted under the one key");
    assert.deepEqual(readTabGroups(), { on: false, collapsed: ["qa"], expanded: ["archived"], pinned: [{ sid: "web", name: "infra", id: "g2" }] }, "the pins ride the same blob");
  } finally {
    g.localStorage = savedLS;
  }
  assert.equal(TABGROUPS_KEY, "romp:tabgroups");
});

test("executed: reorderTagOrder — the dragged group takes the target's slot, in the FULL union order", () => {
  const names = ["qa", "infra", "empty", "remotepool"];
  assert.deepEqual(reorderTagOrder(names, "remotepool", "infra"), ["qa", "remotepool", "infra", "empty"], "dragged up: lands before the target");
  assert.deepEqual(reorderTagOrder(names, "qa", "empty"), ["infra", "empty", "qa", "remotepool"], "dragged down: lands after the target");
  assert.deepEqual(reorderTagOrder(names, "qa", "qa"), names, "onto itself: unchanged");
  assert.deepEqual(reorderTagOrder(names, "nope", "qa"), names, "an unknown name: unchanged");
  assert.deepEqual(reorderTagOrder(["a", "a", "b"], "b", "a"), ["b", "a"], "duplicates collapse");
});

test("executed: applyTagOrder writes tagOrder AND re-sorts the local tags array — the timeline's pill-drag contract, so both surfaces agree", () => {
  const nv = applyTagOrder(V, ["infra", "remotepool", "qa", "empty"]);
  assert.deepEqual(nv.tagOrder, ["infra", "remotepool", "qa", "empty"], "the whole union order, remote-homed names included");
  assert.deepEqual(nv.tags!.map((t) => t.name), ["infra", "qa", "empty"], "locals re-sort to match (remote names simply aren't in it)");
  assert.deepEqual(viewTagUnion({ ...nv, remoteTags: V.remoteTags }).map((u) => u.name), ["infra", "remotepool", "qa", "empty"],
    "a rebuild from the posted blob renders the dragged order");
  assert.deepEqual(V.tags.map((t) => t.name), ["qa", "infra", "empty"], "the input blob is untouched");
  assert.deepEqual(applyTagOrder(null, ["a"]), { tagOrder: ["a"], tags: [] }, "a null blob is a fresh one");
});

test("the strip renders sections when the switch is on and some tag holds a visible tab (source pins)", () => {
  assert.match(RENDER, /const unions = viewTagUnion\(effViews\(\)\);\s*\n\s*const plan = planStrip\(visibleIds, unions, readTabGroups\(unions\), activeId, phoneLayout\(\),/,
    "the pure module owns the rule; the phone layout and the create in flight are its inputs");
  assert.match(RENDER, /collapsedTabIds = plan\.folded;/);
  // the break ahead of a header is emitted under the one-group-per-row setting (stripGroupRows, on by default)
  assert.match(RENDER, /if \("head" in item\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(settings\.stripGroupRows && item\.head\.name !== null && bar\.childElementCount\) bar\.appendChild\(makeRowBreak\(false\)\);\s*\n\s*bar\.appendChild\(makeGroupHead\(item\.head, item\.folded, item\.active, item\.hidden\)\);\s*\n\s*copyGroup = item\.head\.name;\s*\n\s*continue;\s*\n\s*\}/,
    "a header paints from the plan (T264: on its own line under the setting, opened by the break ahead of it; T264b: it names the group the copies below sit in)");
});

test("executed: planStrip — sections + folds; the flat strip when off or untagged; the ACTIVE tab's section folds like any other, marked", () => {
  const unions = viewTagUnion({ ...V, tags: [...V.tags, { id: "g4", name: "archived", color: "#6b7280", members: ["old1", "old2"] }] });
  const st = parseTabGroups(null);
  const p = planStrip(["web", "old1", "loose", "old2"], unions, st, "web", false);
  assert.equal(p.sectioned, true);
  assert.deepEqual(p.items, [
    { head: { name: "infra", localId: "g2", color: "#4EC9B0", ids: ["web"] }, folded: false, active: true, hidden: [] }, { id: "web" },
    { head: { name: "archived", localId: "g4", color: "#6b7280", ids: ["old1", "old2"] }, folded: true, active: false, hidden: ["old1", "old2"] },
    { head: { name: null, localId: null, color: "", ids: ["loose"] }, folded: false, active: false, hidden: [] }, { id: "loose" },
  ], "archived starts folded: its header alone stands in for old1/old2; infra holds the active tab");
  assert.deepEqual([...p.folded], ["old1", "old2"], "the ids keyboard cycling skips");
  const active = planStrip(["web", "old1", "loose"], unions, st, "old1", false);
  assert.deepEqual(active.items[2], { head: { name: "archived", localId: "g4", color: "#6b7280", ids: ["old1"] }, folded: true, active: true, hidden: ["old1"] },
    "the active tab's section folds as the store says and is marked as holding it: the header is the hidden tab's stand-in");
  assert.deepEqual([...active.folded], ["old1"], "the active id is folded away too: visibleOrder drops it, the header takes its place");
  assert.equal(active.items.some((i) => "id" in i && i.id === "old1"), false, "no tab node for it: nothing hidden takes focus");
  assert.ok(!planStrip(["web", "old1"], unions, st, null, false).items.some((i) => "head" in i && i.active), "no active tab: no section holds it");
  const off = planStrip(["web", "old1"], unions, { ...st, on: false }, null, false);
  assert.deepEqual(off.items, [{ id: "web" }, { id: "old1" }], "switch off: the flat strip");
  assert.equal(off.sectioned, false);
  assert.deepEqual(planStrip(["loose"], unions, st, null, false).items, [{ id: "loose" }], "an untagged world: flat");
});

test("executed: the PHONE layout renders the flat strip — every visible id, nothing folded, whatever the store says (sectioning is desktop-only)", () => {
  // the kernel's phone chat page hides #tabs and builds its session list by scraping every rendered
  // tab; it has no header to unfold and no switch, so a folded section there (archived, by default)
  // made its sessions unreachable from the only switcher
  const unions = viewTagUnion({ ...V, tags: [...V.tags, { id: "g4", name: "archived", color: "#6b7280", members: ["old1", "old2"] }] });
  for (const st of [parseTabGroups(null), { on: true, collapsed: ["infra", "qa"], expanded: [], pinned: [] }]) {
    const p = planStrip(["web", "old1", "loose", "old2", "tests"], unions, st, null, true);
    assert.equal(p.sectioned, false);
    assert.deepEqual(p.items, [{ id: "web" }, { id: "old1" }, { id: "loose" }, { id: "old2" }, { id: "tests" }], "the flat strip, in strip order");
    assert.deepEqual([...p.folded], [], "visibleOrder excludes nothing on the phone");
  }
  // render.ts decides "phone" by the SAME media rule the kernel's page uses to swap the strip for its
  // list (_CHAT_MOBILE_CSS) — one string on each side, pinned equal here, so they cannot drift
  const media = RENDER.match(/const PHONE_LAYOUT_MEDIA = "([^"]+)";/)![1];
  assert.equal(media, "(pointer:coarse) and (max-width:1024px)");
  assert.ok(KERNEL.includes('"@media ' + media + '{"'), "the kernel's phone CSS gate is the very same rule");
  assert.match(RENDER, /function phoneLayout\(\): boolean \{\s*\n\s*try \{ return window\.matchMedia\(PHONE_LAYOUT_MEDIA\)\.matches; \} catch \{ return false; \}/);
  // …and the phone layout offers no switch, on either mount
  assert.match(RENDER, /\.\.\.\(phoneLayout\(\) \? \{\} : \{\s*\n\s*groupToggle: \{ label: "Group tabs by tag"/);
  // crossing the boundary (an iPad rotation) re-plans the strip: the CSS side of the same rule flips
  // the instant the media query does, so a plan sampled per render only went stale under the phone
  // list (folded tabs absent from the scrape) until the next push. The flip IS the event — one
  // listener on the same MediaQueryList, beside the fold-state listeners; no resize polling.
  assert.match(RENDER, /try \{ window\.matchMedia\(PHONE_LAYOUT_MEDIA\)\.addEventListener\("change", \(\) => renderTabs\(\)\); \} catch \{/);
  assert.ok(RENDER.indexOf("window.addEventListener(TABGROUPS_EVENT, () => renderTabs());") < RENDER.indexOf('matchMedia(PHONE_LAYOUT_MEDIA).addEventListener("change"'),
    "installed once at module scope with the other strip listeners, never inside a render");
});

test("executed + pinned: the section holding the ACTIVE tab folds like any other: marked, its header the hidden tab's stand-in for focus and the arrows", () => {
  // planStrip folds it as the store says and marks its header; render.ts gives that header the same fold click
  // as the rest, and folded, the header is where focus and the arrows go for the hidden tab
  const unions = viewTagUnion(V);
  const st = parseTabGroups(null);
  const marks = (activeId: string | null) => planStrip(["web", "api", "tests", "loose"], unions, st, activeId, false).items
    .filter((i): i is { head: TabSection; folded: boolean; active: boolean; hidden: string[] } => "head" in i).map((i) => [i.head.name, i.active]);
  assert.deepEqual(marks("web"), [["qa", false], ["infra", true], [null, false]], "exactly the section holding the active tab");
  assert.deepEqual(marks("tests"), [["qa", true], ["infra", false], [null, false]]);
  assert.deepEqual(marks(null), [["qa", false], ["infra", false], [null, false]]);
  // a user-folded section holding the active tab: FOLDED and marked; its hidden set holds the active id
  const folded = setSectionCollapsed(st, "infra", true);
  const plan = planStrip(["web", "api", "tests"], unions, folded, "web", false);   // qa: api, tests | infra(folded): web, api (T264b: api is under both)
  const inf = plan.items.find((i) => "head" in i && i.head.name === "infra") as { head: TabSection; folded: boolean; active: boolean; hidden: string[] };
  assert.deepEqual([inf.folded, inf.active, inf.hidden], [true, true, ["web", "api"]], "the fold hides every copy inside it (api's infra copy too; its qa copy is on screen)");
  assert.deepEqual([...plan.folded], ["web"], "api keeps its place in the keyboard order through its qa copy");
  // a pinned active tab shows through the fold like any pinned member: then it is on screen and no stand-in is needed
  const pinnedActive = planStrip(["web", "api", "tests"], unions, { ...folded, pinned: [{ sid: "web", name: "infra", id: "g2" }] }, "web", false);
  assert.ok(pinnedActive.items.some((i) => "id" in i && i.id === "web"), "the pinned active tab renders");
  assert.deepEqual([...pinnedActive.folded], []);
  // the stand-in rules, pure: the header a folded id is homed in, and where the arrows land from it
  assert.deepEqual(homeSectionOf(plan.items, "web")?.name, "infra");
  assert.deepEqual(homeSectionOf(plan.items, "tests")?.name, "qa", "an id on screen has a home too: the same lookup");
  assert.equal(homeSectionOf(plan.items, "nope"), null);
  assert.deepEqual(plan.items.map((i) => ("head" in i ? "#" + i.head.name : i.id)), ["#qa", "api", "tests", "#infra"]);
  assert.equal(neighborOfFolded(plan.items, "web", 1), "api", "right from the folded infra header (last on the strip): wraps round to the first tab");
  assert.equal(neighborOfFolded(plan.items, "web", -1), "tests", "left from it: the last tab before it");
  const two = planStrip(["tests", "web", "api", "loose"], unions, folded, "web", false).items;   // qa: tests, api | infra(folded): web | sep | loose
  assert.equal(neighborOfFolded(two, "web", 1), "loose", "the untagged separator is a head too: skipped, like any header");
  assert.equal(neighborOfFolded(two, "web", -1), "api");
  assert.equal(neighborOfFolded(two, "nope", 1), null, "no header holds it");
  assert.equal(neighborOfFolded(planStrip(["web"], unions, folded, "web", false).items, "web", 1), null, "no tab on screen at all");
  assert.equal(neighborOfFolded(planStrip(["web", "api"], unions, { ...st, on: false }, "web", false).items, "web", 1), null, "the flat strip: no headers");
  // render.ts: EVERY named header is the fold button (one data-act, one role, the fold state on it); the one
  // holding the active tab adds aria-current and the CSS mark, and keeps its pointer cursor: it folds
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
  assert.match(head, /function makeGroupHead\(sec: TabSection, collapsed: boolean, holdsActive: boolean, hidden: readonly string\[\]\): HTMLElement \{/);
  assert.match(head, /head\.dataset\.act = "toggle-group";/);
  assert.ok(!head.includes("group-active"), "the no-op action is gone");
  assert.match(head, /if \(holdsActive\) head\.setAttribute\("aria-current", "true"\);/);
  assert.match(head, /\+ \(holdsActive \? " holds-active" : ""\)\);/);
  assert.match(head, /head\.draggable = !settings\.tabsLocked;/, "it still drags to reorder the groups, unless the tabs are locked (T395)");
  assert.equal(headWords("infra", 1, 0, false, true).title, "infra — 1 session; holds the tab you are reading; click to fold this group and see its sessions at a glance; drag to reorder the groups");
  assert.match(head, /const words = headWords\(name, total, hidden\.length, collapsed, holdsActive, back, shown\);\s*\n\s*head\.title = words\.title;/, "the words are the pure module's");
  assert.ok(!RENDER.includes('"group-active"'), "no delegate handler for a no-op either");
  assert.doesNotMatch(CSS, /\.tab-group-head\.holds-active \{ cursor: default; \}/, "the header folds, so its cursor promises the click");
  // the holding row wears no mark of its own (T322, the user 2026-09-10: the chip's accent underline read as a second
  // selection); the class stays for aria-current and the folded stand-in's focus and arrows
  assert.doesNotMatch(CSS, /\.tab-group-head\.holds-active \.tab-group-chip \{ text-decoration: underline/, "no underline on the holding row's chip");
  assert.doesNotMatch(CSS, /\.tab-group-head\.holds-active[^\n]*\{[^}]*(background|border|outline|text-decoration)/, "no dress of any kind on the holding row");
  // the hidden active tab's stand-in in render.ts: focus lands on the header, the arrows step from it, and a
  // pick of a folded-away session opens its section (tab-snapshot-pane.test.ts runs these)
  assert.match(RENDER, /const home = activeId \? homeSectionOf\(lastStripItems, activeId\) : null;\s*\n\s*if \(!home \|\| home\.name === null \|\| !bar\) return;\s*\n\s*Array\.from\(bar\.querySelectorAll<HTMLElement>\("\.tab-group-head"\)\)\.find\(\(h\) => h\.dataset\.group === home\.name\)\?\.focus\(\);/,
    "focusActiveTab falls back to the header");
  assert.match(RENDER, /const nb = neighborOfFolded\(lastStripItems, activeId, dir\);\s*\n\s*if \(nb\) \{ setActive\(nb\); focusActiveTab\(\); \}/, "onTabKey (a focused tab's arrows)");
  assert.match(RENDER, /function unfoldSectionOf\(id: string\): void \{\s*\n\s*const holder = lastStripItems\.find\(\(it\) => "head" in it && it\.head\.name !== null && it\.folded && it\.head\.ids\.includes\(id\)\);\s*\n\s*if \(holder && "head" in holder && holder\.head\.name !== null\) writeTabGroups\(setSectionCollapsed\(tabGroups\(\), holder\.head\.name, false\)\);/,
    "the first folded holder opens (per holder: a session under several tags has a copy under each)");
  assert.match(RENDER, /collapsedTabIds = plan\.folded;\s*\n\s*lastStripItems = plan\.items;/, "the plan the stand-in rules read is the one the strip rendered");
});

test("executed: a create in flight sections under EVERY requested tag from the first paint (T264b)", () => {
  // the kernel tags a new session before its first push so the tab never lands untagged and jumps;
  // the client's provisional tab used to land in the untagged trail (a client-minted id in no
  // union) and move into its group when the frame arrived — the very jump the kernel avoids
  const unions = viewTagUnion(V);
  const st = parseTabGroups(null);
  const p = planStrip(["web", "prov1", "loose"], unions, st, "prov1", false, { id: "prov1", tags: ["infra", "qa"] });
  assert.deepEqual(p.items.map((i) => ("head" in i ? "#" + i.head.name : i.id)), ["#qa", "prov1", "#infra", "web", "prov1", "#null", "loose"],
    "the provisional tab appears under BOTH requested tags from the first paint (the every-tag rule, T264b)");
  const none = planStrip(["web", "prov1"], unions, st, "prov1", false, { id: "prov1", tags: [] });
  assert.deepEqual(none.items.map((i) => ("head" in i ? "#" + i.head.name : i.id)), ["#infra", "web", "#null", "prov1"], "no tags asked → the untagged trail");
  assert.deepEqual(V.tags.map((t) => t.members), [["tests", "api"], ["web", "api"], []], "the unions themselves are untouched");
  assert.match(RENDER, /provisionalTags = req\.tags\?\.slice\(\) \?\? \[\];/, "openProvisional keeps the request's tags…");
  assert.match(RENDER, /provisionalId \? \{ id: provisionalId, tags: provisionalTags \} : null\);/, "…and the plan sections the provisional under them");
  const drop = RENDER.slice(RENDER.indexOf("function dropProvisional("), RENDER.indexOf("function adoptProvisional("));
  assert.match(drop, /provisionalTags = \[\];/, "retired with the provisional");
});

test("executed: the header click sets the fold state from what it RENDERED — never a toggle of the stored bit", () => {
  // the active tab's section renders open whatever the store says; a stored toggle there inverted
  // the click: "fold" on default-folded archived (rendered open for the active tab) stored it OPEN
  // for good, and nothing visible changed
  const d = parseTabGroups(null);
  // archived: stored folded, rendered OPEN (active tab inside) → the click says "fold" → still folded, stored minimally
  assert.deepEqual(setSectionCollapsed(d, "archived", true), { on: true, collapsed: [], expanded: [], pinned: [] });
  assert.equal(isSectionCollapsed(setSectionCollapsed(d, "archived", true), "archived"), true);
  assert.deepEqual(toggleSectionCollapsed(d, "archived"), { on: true, collapsed: [], expanded: ["archived"], pinned: [] },
    "…where the stored toggle would have OPENED it");
  // infra folded by the user, then rendered open (active tab inside), click "fold" → stays folded
  const folded = setSectionCollapsed(d, "infra", true);
  assert.deepEqual(setSectionCollapsed(folded, "infra", true), folded);
  assert.deepEqual(setSectionCollapsed(folded, "infra", false), d, "and a rendered-folded click opens it");
  assert.deepEqual(setSectionCollapsed(setSectionCollapsed(d, "archived", false), "archived", true), d, "re-folding a default-folded section drops the memory");
  // the header carries its rendered state; the delegate reads it
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
  assert.match(head, /head\.dataset\.folded = collapsed \? "1" : "0";/);
  assert.match(RENDER, /if \(!name\) return;\s*\n\s*snapView = name;\s*\n\s*writeTabGroups\(setSectionCollapsed\(tabGroups\(\), name, el\.dataset\.folded !== "1"\)\);/,
    "rendered open → fold; rendered folded → open (and the section shows in the pane: snapView)");
});

test("a folded section renders its header alone with the folded-away count and one MEMBER-derived pip after it — a label, not a session; cycling skips folded ids", () => {
  assert.match(RENDER, /function visibleOrder\(\): string\[\] \{ return order\.filter\(\(id\) => tabInView\(id\) && !collapsedTabIds\.has\(id\)\); \}/,
    "every cycling path walks visibleOrder (session-views.test pins the three callers)");
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
  assert.match(head, /n\.textContent = words\.count;/, "the count: every member when open, the hidden members when folded (headWords, executed below)");
  // the pip is the MEMBERS' (a hidden one blocked/waiting/working/retrying), never the header's own status
  // (the user 2026-09-06: no session-tab affordances on a header — but a fold must still say a hidden
  // member needs you); over the hidden members, after the count
  assert.match(head, /const kind = sectionPip\(hidden\.map\(\(id\) => sessions\.get\(id\)\?\.status\)\);/,
    "one summary pip, classified by tab-state.ts — the same rule the tab itself wears (tab-state.test)");
  assert.match(head, /pip\.title = sectionPipTitle\(kind, sectionPipMembers\(kind, hidden\.map\(\(id\) => sessions\.get\(id\)\)\)\);/, "the tooltip names the sessions");
  assert.ok(head.indexOf('el("span", "tab-group-count")') < head.indexOf("sectionPip("), "after the count (the row's last child when folded, T284)");
  assert.ok(!head.includes("tabStateClass("), "the header itself wears no state class");
  assert.ok(!head.includes('"tab-dot"'), "never a .tab-dot — the kernel's mobile scrape keys on the tab pips' vocabulary");
  // the trail is never a header: its own row with no chip under the one-group-per-row setting (T264), a 13px
  // divider with the setting off
  assert.match(head, /if \(sec\.name === null\) return settings\.stripGroupRows \? makeRowBreak\(true\) : makeTrailSep\(\);/,
    "the untagged trail is UNLABELED (the ruling): never a header; its own row with no chip under stripGroupRows (T264), the divider otherwise");
  // the tab's own class comes from the same function
  assert.match(RENDER, /const stateCls = tabStateClass\(s\.status\);\s*\n\s*if \(stateCls\) tab\.classList\.add\(stateCls\);/);
  assert.match(CSS, /\.tab-group-pip \{ flex: 0 0 auto; width: 6px; height: 6px; border-radius: 50%; background: var\(--st-working-bg\); \}/, "small: subordinate to the label");
  assert.match(CSS, /\.tab-group-pip\.blocked \{ background: var\(--st-blocked-bg\); \}/, "status colours keep their meaning");
  assert.match(CSS, /\.tab-group-pip\.retrying \{ background: var\(--st-retrying-bg\); \}/, "amber — the retrying STATUS token (2026-09-08; it sat raw here and on the tab)");
});

test("row hairlines count section headers as row members (T134's floating look must not return), never the row breaks", () => {
  // a wrapped row made only of folded headers got no line: the painter grouped `.tab` children only
  const painter = RENDER.slice(RENDER.indexOf("function paintTabRowLines("), RENDER.indexOf("let tabRowObserver"));
  assert.match(painter, /if \(!\(t\.classList\.contains\("tab"\) \|\| t\.classList\.contains\("tab-group-head"\)\)\) continue;/);
  // the zero-height row breaks (T264) are not rows: counting one drew a hairline at the strip's top edge
  assert.doesNotMatch(painter, /tab-group-sep|tab-group-break/);
});

test("the picker's Tags row is live for every offered backend, and the create always carries the selected chips (T331)", () => {
  // the terminal backend, whose create took no tags, is no longer offered: no disabled state, no note, one payload
  assert.doesNotMatch(RENDER, /backendTakesTags|picker-tags-note/, "the predicate and the note went with the tmux pick");
  const sync = RENDER.slice(RENDER.indexOf("function syncPickerTags("), RENDER.indexOf("function syncPickerAuth("));
  assert.match(sync, /\.forEach\(\(b\) => \{ b\.disabled = false; \}\);/, "every chip stays live");
  assert.doesNotMatch(sync, /classList\.toggle\("disabled"/);
  assert.match(RENDER, /const tags = Array\.from\(tgWrap\.querySelectorAll<HTMLElement>\("\.picker-be-opt\.sel"\)\)\.map\(\(x\) => x\.dataset\.tag \|\| ""\)\.filter\(Boolean\);/,
    "the create handler sends the selected chips for every backend");
  assert.match(KERNEL, /_create_codex_session\(nm, cwd, client=client,\s+parent=psid or "", tags=ctags\)/,
    "the premise: the kernel's createSession op applies tags on a Codex create");
  assert.match(RENDER, /beWrap\.addEventListener\("click", \(\) => \{ syncPickerAuth\(\); syncPickerTags\(\); \}\);/, "re-painted on every backend toggle");
  assert.match(RENDER, /:not\(\.picker-host\):not\(\.picker-auth\):not\(\.picker-tags\) \.picker-be-opt\.sel/, "a selected tag chip never reads as the backend pick");
  assert.doesNotMatch(CSS, /\.picker-tags\.disabled/, "the greyed-row rule is gone with the state");
});

test("headers are click-safe: data-act on the node, the action on the stable #tabs delegate, one render path via the event", () => {
  assert.match(RENDER, /head\.dataset\.act = "toggle-group";/);
  // the click derives the next fold state from the one the header RENDERED (data-folded), never from the store, and
  // shows the section in the pane (snapView; tab-snapshot-pane.test.ts)
  assert.match(RENDER, /"toggle-group": \(el\) => \{\s*\n\s*const name = el\.dataset\.group;\s*\n\s*if \(!name\) return;\s*\n\s*snapView = name;\s*\n\s*writeTabGroups\(setSectionCollapsed\(tabGroups\(\), name, el\.dataset\.folded !== "1"\)\);/);
  assert.match(RENDER, /window\.addEventListener\(TABGROUPS_EVENT, \(\) => renderTabs\(\)\);/, "the same-window delivery");
  assert.match(RENDER, /window\.addEventListener\("storage", \(e\) => \{ if \(e\.key === TABGROUPS_KEY\) renderTabs\(\); \}\);/, "…and a sibling pane's");
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf('"toggle-group": (el) => {'), RENDER.indexOf('"toggle-group": (el) => {') + 300), /renderTabs\(\)/,
    "the toggle does not render itself — the event does, so a local toggle and a sibling pane's take one path");
});

test("dragging a header reorders tagOrder through the views path — the store the timeline's pill drag writes (source pins)", () => {
  assert.match(RENDER, /head\.draggable = !settings\.tabsLocked;/, "a header drags unless the tabs are locked (T395)");
  assert.match(RENDER, /draggedGroup = name;/);
  const drop = RENDER.slice(RENDER.indexOf('tabs.addEventListener("drop"'), RENDER.indexOf("tabDragCommitted = true;"));
  assert.match(drop, /if \(draggedGroup\) \{/);
  assert.match(drop, /postTagOrder\(reorderTagOrder\(viewTagUnion\(effViews\(\)\)\.map\(\(u\) => u\.name\), draggedGroup, to\)\);/,
    "the FULL union order as a LENS write: the store's blob plus tagOrder, never the pending copy; setTimelineViews underneath (postTagOrder → postLens)");
  const over = RENDER.slice(RENDER.indexOf('tabs.addEventListener("dragover"'), RENDER.indexOf("if (!draggedId || !dragGeom) return;"));
  assert.match(over, /target\.classList\.add\("drop-target"\)/, "the target section wears the insertion cue");
  assert.doesNotMatch(over, /setTimeout|Date\.now/, "no time-based logic — the drop is the event");
  assert.match(CSS, /\.tab-group-head\.drop-target \{ box-shadow: inset 2px 0 0 var\(--accent\); \}/);
});

test("the switch lives at the foot of the chat tag-lens menu beside Configure tags…, desktop mount only", () => {
  assert.match(MENU, /groupToggle\?: \{ label: string; on: \(\) => boolean; toggle: \(\) => void \};/);
  assert.match(MENU, /if \(opts\.groupToggle\)\s*\n\s*row\(opts\.groupToggle\.label, opts\.groupToggle\.on\(\), true\)\.addEventListener\("click", \(\) => \{ opts\.groupToggle!\.toggle\(\); build\(\); \}\);/,
    "✓-marked when on; flips and repaints in place like the tag rows");
  assert.ok(MENU.indexOf("if (opts.groupToggle)") < MENU.indexOf('row("Configure tags…"'), "beside — above — Configure tags…");
  assert.match(RENDER, /groupToggle: \{ label: "Group tabs by tag", on: \(\) => readTabGroups\(\)\.on,/);
  const mobile = RENDER.slice(RENDER.indexOf('const mslot = document.getElementById("mtag-slot")'), RENDER.indexOf("paintTabRowLines(bar);"));
  assert.ok(!mobile.includes("groupToggle"), "the phone page hides the strip itself, so its mount offers no switch");
});

test("the picker's Tags row: prefilled from the ACTIVE tab, visible and editable, posted as `tags` on createSession", () => {
  assert.match(RENDER, /interface CreateReq \{ name: string; backend: string; dir: string; host: string; auth\?: string; tags\?: string\[\] \}/);
  assert.match(RENDER, /const tgWrap = el\("div", "picker-backend picker-tags"\);/);
  assert.match(RENDER, /const preset = new Set\(activeId \? unions\.filter\(\(u\) => u\.members\.includes\(activeId!\)\)\.map\(\(u\) => u\.name\) : \[\]\);/,
    "the active tab's tags pre-select — never a silent inherit");
  assert.match(RENDER, /b\.addEventListener\("click", \(\) => \{ b\.classList\.toggle\("sel"\); paintPickerTagChip\(b, u\); \}\);/, "multi-select: each chip on its own, repainted as the shared chip (T321)");
  assert.match(RENDER, /\.\.\.\(tags\.length \? \{ tags \} : \{\}\) \}\);/, "absent when nothing is picked (the kernel's not-asked contract)");
  assert.match(RENDER, /tgWrapEl\.style\.display = pick \|\| !unions\.length \? "none" : "";/, "hidden with no tags to offer, and in pick-mode");
});

test("the section chrome is a LABEL's (the user 2026-09-06): the surface's sub-line size, letter-spaced, ONE size for its text, a bar not a dot", () => {
  const head = CSS.match(/\.tab-group-head \{[^}]*\}/)![0];
  assert.match(head, /font-size: 0\.82em;/, "0.82em is already on the surface (the menus' sub-lines, the ctx-item sub-line) — no new size (ui/CLAUDE.md)");
  assert.match(head, /letter-spacing: 0\.04em;/);
  assert.match(head, /color: var\(--dim\);/);
  assert.match(CSS, /\.tab-group-count \{ opacity: 0\.7; \}/, "the count inherits the header's size — no em nested inside an em");
  // T251 (the user 2026-09-07): the swatch+name pair retired for THE CHIP the tag wears everywhere —
  // the shared tag-menu builder's pill, bold like the name it replaces, sized by the header
  assert.match(CSS, /\.tab-group-chip \{ flex: 0 0 auto; line-height: 1\.2; \}/, "no weight on the host: the chip is 400 everywhere (T321)");
  assert.doesNotMatch(CSS, /\.tab-group-swatch|\.tab-group-name \{/, "the bar and the plain name are gone");
  assert.doesNotMatch(CSS, /\.tab-group-dot/, "the dot is gone");
  const sizes = new Set(Array.from(CSS.matchAll(/\n\.tab-group-[^{\n]*\{[^}]*font-size: ([^;]+);/g)).map((m) => m[1]));
  assert.deepEqual([...sizes], ["0.82em"], "one font-size across every section rule");
  // the divider the trail stands behind with the one-group-per-row setting off is scoped off the row break,
  // which wears .tab-group-sep too (the boundary sectionHeadOf reads) and must keep no box
  assert.doesNotMatch(CSS, /\.tab-group-sep \{/, "no unscoped separator rule: the break's boundary class must not give it a box");
  assert.match(CSS, /\.tab-group-sep:not\(\.tab-group-break\) \{ flex: 0 0 auto; box-sizing: border-box; width: 13px; padding: 8px 6px; background: var\(--box-border\); background-clip: content-box; \}/,
    "the divider's rule: a 13px box whose gutters are padding");
});

// ── T264 (the user 2026-09-08): every tag group on its own line, under the stripGroupRows setting (on by default) ──
test("under the setting (on by default), every group opens a new line: a zero-height full-width break ahead of each header, the trail's break doubling as the boundary (T264)", () => {
  // the strip read as one long concatenation; now the chip sits at the left edge, its tabs follow it
  // and wrap onto further rows as they need, and the next group starts a fresh line. The breaks are
  // emitted under the one-group-per-row setting (the next test runs the gate at both values)
  const loop = RENDER.slice(RENDER.indexOf("collapsedTabIds = plan.folded;"), RENDER.indexOf("const id = item.id;"));
  assert.match(loop, /if \(settings\.stripGroupRows && item\.head\.name !== null && bar\.childElementCount\) bar\.appendChild\(makeRowBreak\(false\)\);\s*\n\s*bar\.appendChild\(makeGroupHead\(item\.head, item\.folded, item\.active, item\.hidden\)\);/,
    "under the setting, a break before every header but the strip's first item (which already opens the first row)");
  const brk = RENDER.slice(RENDER.indexOf("function makeRowBreak("), RENDER.indexOf("function makeTrailSep("));
  assert.match(brk, /el\("div", "tab-group-break" \+ \(untagged \? " tab-group-sep" : ""\)\)/,
    "the untagged trail's break keeps the .tab-group-sep class — the boundary sectionHeadOf reads");
  assert.match(brk, /brk\.setAttribute\("aria-hidden", "true"\);/, "layout only: nothing to read aloud");
  assert.doesNotMatch(brk, /\.title = /, "no tooltip on a zero-height item");
  assert.match(CSS, /\.tab-group-break \{ flex: 0 0 100%; height: 0; margin: 0; padding: 0; pointer-events: none; \}/,
    "a full-row, zero-height item: the next item wraps; no rhythm of its own; takes no drop and no hover");
  // sectionHeadOf still stops at the untagged boundary and walks past a plain break
  const sh = RENDER.slice(RENDER.indexOf("function sectionHeadOf("), RENDER.indexOf("function makePlaceholderTab("));
  assert.match(sh, /if \(h\.classList\.contains\("tab-group-sep"\)\) return null;/);
  // the header's own rule is unchanged: flex-none, the chip first in its row
  assert.match(CSS, /\.tab-group-head \{ display: flex; flex: 0 0 auto; align-items: center;/);
});

// ── the one-group-per-row setting (stripGroupRows, on by default): off, the groups follow one another across the strip ──
test("executed + pinned: the one-group-per-row setting is on by default; off, the strip emits NO break, a group's head and tabs stay contiguous and the trail stands behind a divider; the gear's row", () => {
  // the setting and its default (executed: the real defaults object)
  assert.equal(DEFAULT_SETTINGS.stripGroupRows, true, "on by default: every tag group on its own row");
  assert.match(SETTINGS, /stripGroupRows: boolean;/);
  // the loop's head branch appends the break and the header, nothing else, and the break is the ONE gated append;
  // the gate itself is executed here off the source text: on, every head but the first gets a break; off, none
  const loop = RENDER.slice(RENDER.indexOf("for (const item of plan.items) {"), RENDER.indexOf("const id = item.id;"));
  assert.equal((loop.match(/makeRowBreak\(/g) ?? []).length, 1, "one break site in the loop");
  assert.equal((loop.match(/bar\.appendChild\(/g) ?? []).length, 2, "the head branch appends the break (gated) and the header; nothing else sits between a head and its tabs");
  const gate = loop.match(/if \((settings\.stripGroupRows && item\.head\.name !== null && bar\.childElementCount)\) bar\.appendChild\(makeRowBreak\(false\)\);/);
  assert.ok(gate, "the break is gated on the setting");
  const breaks = new Function("settings", "item", "bar", "return !!(" + gate![1] + ");") as (s: unknown, it: unknown, b: unknown) => boolean;
  const heads = [{ head: { name: "web" } }, { head: { name: "api" } }, { head: { name: null } }];
  assert.deepEqual(heads.map((it, i) => breaks({ stripGroupRows: true }, it, { childElementCount: i })), [false, true, false], "on: a break ahead of every header but the strip's first item; the trail's header is itself the break");
  assert.deepEqual(heads.map((it, i) => breaks({ stripGroupRows: false }, it, { childElementCount: i })), [false, false, false], "off: no break ahead of any header, first or later, nor the trail's");
  // the plan's items are contiguous per group (executed on the pure module): a head, then its tabs, then the next
  // head; with no break appended the DOM is the plan in order, so the groups follow one another and wrap
  const unions = viewTagUnion({ tags: [{ id: "t-infra", name: "infra", color: "#1EA1EB", members: ["web", "api"] },
                                       { id: "t-qa", name: "qa", color: "#54B204", members: ["tests"] }] });
  const plan = planStrip(["web", "api", "tests", "loose"], unions, parseTabGroups(null, unions), "web", false);
  assert.deepEqual(plan.items.map((it) => ("head" in it ? "#" + (it.head.name ?? "null") : it.id)), ["#infra", "web", "api", "#qa", "tests", "#null", "loose"],
    "each group's head is followed by its tabs and then the next head; the trail's head last");
  // with the setting off the trail's boundary is a divider: a real 13px box wearing .tab-group-sep (the boundary
  // sectionHeadOf reads and the drop's group edge), titled, no aria noise
  const sep = RENDER.slice(RENDER.indexOf("function makeTrailSep("), RENDER.indexOf("function sectionHeadOf("));
  assert.match(sep, /const sep = el\("div", "tab-group-sep"\);\s*\n\s*sep\.title = "sessions in no tag";\s*\n\s*return sep;/);
  const drop = RENDER.slice(RENDER.indexOf('tabs.addEventListener("drop"'), RENDER.indexOf("tabDragCommitted = true; }"));
  assert.match(drop, /const edge = \(n: Element\) => n\.classList\.contains\("tab-group-head"\) \|\| n\.classList\.contains\("tab-group-break"\) \|\| n\.classList\.contains\("tab-group-sep"\);/,
    "the divider bounds the trail for the drop's in-group neighbour walk, as the break does under the setting");
  // a gear flip repaints at once: the setting rides the strip's rebuild signature, and the settings listener calls renderTabs
  assert.match(RENDER, /settings\.tabCtx, settings\.stripGroupRows, settings\.tabsLocked, settings\.theme, settings\.colormap,/, "in the strip's signature");
  assert.match(RENDER, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\);/);
  // the gear's row: a checkbox like Show git branch's, checked by default, saved through the same save() and filled at open
  assert.match(GEAR, /<label class=rs-row><input type=checkbox id=rs-striprows checked>' \+\s*\n\s*'<span><b>One tag group per row in the tab strip<\/b>/);
  assert.match(GEAR, /sr\.addEventListener\('change', function \(\) \{ var s = load\(\); s\.stripGroupRows = sr\.checked; save\(s\); \}\);/);
  assert.match(GEAR, /if \(sr\) sr\.checked = s\.stripGroupRows !== false;/, "the default-ON fill: only an explicit false unchecks");
  assert.equal((GEAR.match(/stripGroupRows: true/g) ?? []).length, 2, "the gear's defaults mirror agrees: on, in both of load()'s literals");
  // the guide says so in one sentence (matched across its line wraps)
  const sentence = "Every group starts on its own row; turning off the gear's **One tag group per row in the tab strip** lets the groups follow one another across the strip and wrap as they need, with the untagged sessions behind a thin divider, so a strip with many tags stays short.";
  assert.match(GUIDE, new RegExp(sentence.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+")));
  assert.match(GUIDE, /the untagged sessions on a row of their\s+own at the end\./, "the paragraph's opening describes the default layout");
});

test("executed: the drop's in-group neighbour walk stops at the trail's divider, as it stops at a break or a header", () => {
  // the walk's own source, lifted from the drop handler and run over a fake sibling chain; the replaces strip the
  // body's TypeScript annotations, leaving plain JS for new Function
  const drop = RENDER.slice(RENDER.indexOf('tabs.addEventListener("drop"'), RENDER.indexOf("tabDragCommitted = true; }"));
  const src = drop.slice(drop.indexOf("const own = "), drop.indexOf("// committed only when"))
    .replace(/: \(x: Element\) => Element \| null/g, "").replace(/\): HTMLElement \| null =>/g, ") =>")
    .replace(/ as HTMLElement \| null/g, "").replace(/\(n as HTMLElement\)/g, "n")
    .replace(/: Element \| null/g, "").replace(/: Element\)/g, ")").replace(/: boolean\)/g, ")");
  assert.match(src, /const edge = /); assert.match(src, /const next = /);
  const neighbours = new Function("dragged", "draggedId", src + "\nreturn { prev: prev?.dataset?.id ?? null, next: next?.dataset?.id ?? null };") as
    (dragged: unknown, draggedId: string) => { prev: string | null; next: string | null };
  type Fake = { classList: { contains(c: string): boolean }; dataset: { id?: string }; previousElementSibling: Fake | null; nextElementSibling: Fake | null };
  const strip = (specs: { cls: string; id?: string }[]): Fake[] => {
    const els = specs.map((s): Fake => ({ classList: { contains: (c) => s.cls.split(" ").includes(c) }, dataset: s.id ? { id: s.id } : {},
                                          previousElementSibling: null, nextElementSibling: null }));
    els.forEach((e, i) => { e.previousElementSibling = els[i - 1] ?? null; e.nextElementSibling = els[i + 1] ?? null; });
    return els;
  };
  // the strip with the setting off: [#infra] a b | loose1 loose2 [+]; loose1 dropped where it stands, at the trail's head
  const inline = [{ cls: "tab-group-head" }, { cls: "tab", id: "a" }, { cls: "tab", id: "b" }, { cls: "tab-group-sep" },
                  { cls: "tab", id: "loose1" }, { cls: "tab", id: "loose2" }, { cls: "tab tab-add" }];
  const bar = strip(inline);
  assert.deepEqual(neighbours(bar[4], "loose1"), { prev: null, next: "loose2" },
    "the divider bounds the trail: the drop anchors before the trail's next tab, never after the tagged group's last");
  // under the setting the same boundary is the break, wearing both classes: the same edge
  const rows = strip(inline.map((s) => (s.cls === "tab-group-sep" ? { cls: "tab-group-break tab-group-sep" } : s)));
  assert.deepEqual(neighbours(rows[4], "loose1"), { prev: null, next: "loose2" }, "the trail's break is the edge under the setting");
  // inside a group the walk names the tab before, and the group's end bounds the tab after
  assert.deepEqual(neighbours(bar[2], "b"), { prev: "a", next: null }, "b sits after a; the divider ends its group");
});

test("the tab drag's virtual layout wraps where the strip wraps: headers after a break and the trail's break open rows (T264)", () => {
  const over = RENDER.slice(RENDER.indexOf('tabs.addEventListener("dragover"'), RENDER.indexOf('tabs.addEventListener("drop"'));
  assert.match(over, /const isBreak = \(n: Element \| null\) => !!n && n\.classList\.contains\("tab-group-break"\);/);
  assert.match(over, /const before = \(t: HTMLElement\) => \{ let p = t\.previousElementSibling; while \(p && p === dragged\) p = p\.previousElementSibling; return p; \};/,
    "the box before, skipping the dragged tab (it is out of the virtual layout)");
  assert.match(over, /w: isBreak\(t\) \? 0 : /, "the trail's break is a zero-width row opener, not a full-row box");
  assert.match(over, /br: isBreak\(t\) \|\| \(t\.classList\.contains\("tab-group-head"\) && isBreak\(before\(t\)\)\) \}\)\);/,
    "a header after a break, and the break itself, open a row; the trail's first tab after its break does not (the break did)");
  assert.match(over, /if \(ref && ref\.classList\.contains\("tab-group-head"\) && isBreak\(before\(ref as HTMLElement\)\)\) ref = before\(ref as HTMLElement\);/,
    "the slot before a header is the end of the previous row — never between the break and the chip");
  const DS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "dragslot.ts"), "utf8");
  assert.match(DS, /const needsWrap = row !== null && cx > 0 && \(b\.br === true \|\| cx \+ b\.w > containerW\);/, "dragslot honours br (executed in dragslot.test.ts)");
});

test("executed: a session under two tags is placed under BOTH — the user's ruling (T264b); folded away only when every copy is", () => {
  // the user 2026-09-08: tags are equivalent; neither takes precedence, so the session appears under
  // every tag it carries (the T264 once-per-session pin flipped to this rule)
  const unions = viewTagUnion({ tags: [{ id: "t-infra", name: "infra", color: "#1EA1EB", members: ["web", "api"] },
                                       { id: "t-qa", name: "qa", color: "#54B204", members: ["api", "tests"] }] });
  const st = parseTabGroups(null, unions);
  const p = planStrip(["web", "api", "tests"], unions, st, "web", false);
  const tabs = p.items.filter((i) => "id" in i).map((i) => (i as { id: string }).id);
  assert.deepEqual(tabs, ["web", "api", "api", "tests"], "api has a copy in each of its groups");
  const heads = p.items.map((i) => ("head" in i ? { name: i.head.name, ids: i.head.ids } : null)).filter(Boolean);
  assert.deepEqual(heads.map((h) => [h!.name, h!.ids]), [["infra", ["web", "api"]], ["qa", ["api", "tests"]]], "counts per group count the group's own copies");
  // fold qa: tests is folded away (its only copy), api is NOT (its infra copy is on screen) — the keyboard order keeps it
  const folded = planStrip(["web", "api", "tests"], unions, setSectionCollapsed(st, "qa", true), "web", false);
  assert.deepEqual([...folded.folded], ["tests"], "an id leaves the keyboard order only when every copy is folded");
  assert.deepEqual(folded.items.filter((i) => "id" in i).map((i) => (i as { id: string }).id), ["web", "api"]);
});


test("the header's structure and gestures read as a label: the tag's chip, then the chevron (flips with the fold) and the count at the right; a keyboard button; hover/focus say fold, never open; tokens only (the user 2026-09-06)", () => {
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
  const at = (t: string) => { const i = head.indexOf(t); assert.ok(i >= 0, "present: " + t); return i; };
  assert.ok(at('tagChip(name, sec.color, { inheritSize: true })') < at('el("span", "tab-group-caret")')
    && at('el("span", "tab-group-caret")') < at('el("span", "tab-group-count")'), "the tag's CHIP, then chevron and count at the right (T284)");
  // T284 (the user 2026-09-09): the chip, then the caret and the count right after it, the feed's grouped
  // headers' order (name at the left, caret and count together at the right); the folded gist's pip comes
  // last, so the folded and the open row share one shape and the caret is always the chip's neighbour.
  // Read from the builder's appends (the only way a child joins the head).
  const appends = [...head.matchAll(/head\.appendChild\((\w+)\)/g)].map((m) => m[1]);
  assert.deepEqual(appends, ["chip", "caret", "n", "pip"], "chip, caret, count, then the folded pip: " + appends.join(","));
  // the pin reads named appendChild calls, so every other way a child could join the head is ruled out (the
  // review's find: an inline appendChild(el(...)), a prepend or an insertBefore would have slipped past it)
  assert.equal((head.match(/head\.appendChild\(/g) || []).length, appends.length, "every appendChild passes one of the named children");
  assert.ok(!/head\.(append|prepend|insertBefore|insertAdjacentElement|insertAdjacentHTML|replaceChildren)\(/.test(head),
    "children join one at a time through appendChild, which the pin above reads");
  assert.match(head, /caret\.textContent = "▸";/);
  assert.match(CSS, /\.tab-group-head:not\(\.collapsed\) \.tab-group-caret \{ transform: rotate\(90deg\); \}/,
    "the fold state flips it — the sheet's fold-caret idiom, a CSS transition, no timer");
  assert.match(CSS, /\.tab-group-caret \{[^}]*transition: transform 0\.12s ease;/);
  assert.match(head, /tagChip\(name, sec\.color, \{ inheritSize: true \}\)/, "the tag's color from the views store, on the SHARED chip (T251)");
  // none of a tab's affordances
  assert.ok(!head.includes("tab-close") && !head.includes("tabStateClass(") && !head.includes("tab-dot") && !head.includes("tabCtxGauge("),
    "no close, no state class of its own, no tab pip, no gauge");
  // keyboard: a button to the keyboard, through the same click → delegate path as the pointer, on EVERY named
  // header, the one holding the tab being read too (it folds; aria-current marks it; the accessibility test
  // below). The stand-in's own keys come first in the handler (tab-snapshot-pane.test.ts runs them).
  assert.match(head, /head\.setAttribute\("role", "button"\);\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(!back\) head\.setAttribute\("aria-expanded", collapsed \? "false" : "true"\);\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(holdsActive\) head\.setAttribute\("aria-current", "true"\);\s*\n\s*head\.tabIndex = 0;\s*\n\s*head\.addEventListener\("keydown", \(e\) => \{/,
    "role, expanded state, the current mark, tab stop and key handler together");
  assert.match(head, /if \(e\.key === "Enter" \|\| e\.key === " "\) \{ e\.preventDefault\(\); head\.click\(\); \}\s*\n\s*\}\);/, "Enter and Space press the header");
  // a push mid-read must not kick focus off the header: renderTabs re-focuses the same group after the rebuild
  assert.match(RENDER, /const focusedGroup = \(focusedEl\?\.closest\("\.tab-group-head"\) as HTMLElement \| null\)\?\.dataset\.group;\s*\n\s*const focusedLock = !!focusedEl\?\.closest\("\.tab-lock"\);[^\n]*\n\s*const refocusTab = bar\.contains\(document\.activeElement\);/,
    "captured before the tab rule (chat-focus-model.test pins that rule's two-line shape)");
  assert.match(RENDER, /if \(h && h\.tabIndex >= 0\) h\.focus\(\); else focusActiveTab\(\);/,
    "…falling back to the active tab when the group is gone or now holds it");
  assert.doesNotMatch(CSS, /\.tab-group-head:hover/, "no hover lift on a tag row: it is not a tab (T322)");
  assert.doesNotMatch(CSS, /\.tab-group-head:hover \.tab-group-chip/, "no brightness lift on hover either (T322)");
  {
    // the RENDERED outcome, not the sheet's text: the chip's inline style is what the shown row's colour rule
    // cannot reach (inline beats class), and the row-hosted chip takes the row's size. Run the real builder
    // against a stub document and read the style it writes.
    const created: { tag: string; attrs: Record<string, string>; kids: unknown[] }[] = [];
    const g = globalThis as any;
    const saved = g.document;
    const savedWin = g.window;
    g.document = {
      createElement: (tag: string) => { const n = { tag, attrs: {} as Record<string, string>, kids: [] as unknown[],
        setAttribute(k: string, v: string) { this.attrs[k] = v; }, appendChild(c: unknown) { this.kids.push(c); },
        addEventListener() { /* unused by tagChip */ } }; created.push(n); return n; },
      createTextNode: (t: string) => ({ text: t }),
      addEventListener() { /* the module wires its closers + the menu-echo writer at load (T107) */ },
    };
    g.window = { addEventListener() { /* the storage listener at load */ } };
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { tagChip } = require("./tag-menu");
      const chip = tagChip("infra", "#54B204", { inheritSize: true });
      const style: string = chip.attrs.style;
      assert.match(style, /color:#54B204;/, "the identity colour rides inline — the shown row's --fg rule cannot recolour it");
      assert.ok(!/font-size/.test(style), "and the header-hosted chip inherits the header's size");
    } finally { g.document = saved; g.window = savedWin; }
  }
  // a tag row is not a tab (T322): no hover dress at all — no lift of the words, the caret or the chip, no wash — only
  // the keyboard's focus ring; and it takes a tab's box of space (the same padding and transparent border as .tab)
  assert.doesNotMatch(CSS, /\n\.tab-group-head:hover/, "no hover rule on the row");
  assert.match(CSS, /\n\.tab-group-head \{[^}]*padding: 6px 7px;[^}]*border-radius: 6px 6px 0 0;/s, "a tab's allotment: .tab's padding around the chip, the caret and the count");
  assert.match(CSS, /\n\.tab \{[^}]*padding: 6px 7px;/s, "…the tab's own");
  assert.doesNotMatch(CSS.match(/\n\.tab-group-head \{([^}]*)\}/)![1], /border:|background/, "no border, no fill: empty around the parts");
  assert.match(CSS, /\.tab-group-head:focus-visible \{ outline: 1px solid var\(--accent\); outline-offset: -1px; \}/);
  assert.match(head, /head\.draggable = !settings\.tabsLocked;/, "still drags to reorder the groups, unless the tabs are locked (T395)");
  assert.match(head, /head\.dataset\.act = "toggle-group";/, "…and still folds through the delegate");
  // theme: every section rule resolves through tokens — no raw hex or rgba — so the light theme needs no
  // override, and the tokens are ones the strip already wears (theme-parity.test.ts checks --fg/--dim/
  // --accent against --bg in both themes); --accent-wash is the shown section's header (the pane shows it:
  // .snap-shown), the wash the picker's active row already wears
  const rules = Array.from(CSS.matchAll(/\n(\.tab-group-[^{\n]*)\{([^}]*)\}/g));
  assert.ok(rules.length >= 12, "the section rules were found: " + rules.length);   // the hover dress rules left with T322
  for (const [, sel, body] of rules) {
    // no exception any more (2026-09-08): the retrying amber is a token, --st-retrying-bg, on the pip AND the tab
    assert.doesNotMatch(body.replace(/var\([^)]*\)/g, "V"), /#[0-9a-fA-F]{3,8}\b|rgba?\(/, "a raw color in " + sel.trim());
  }
  assert.equal(CSS.match(/\.tab-group-pip\.retrying \{ background: (var\(--st-retrying-bg\)); \}/)![1], CSS.match(/\.tab\.tab-retrying \{ --state: (var\(--st-retrying-bg\)); \}/)![1],
    "the pip's retrying amber IS the tab's — the same status token");
  const toks = new Set((rules.map((m) => m[2]).join(" ").match(/var\((--[a-z-]+)/g) || []).map((m) => m.slice(4)));
  for (const t of toks) assert.ok(["--fg", "--dim", "--accent", "--accent-wash", "--box-border", "--st-working-bg", "--st-blocked-bg", "--st-retrying-bg", "--tab-active-bg", "--chip-bg"].includes(t), "a token the strip does not already wear: " + t);
});

// SHOW WHEN FOLDED (the user 2026-09-06): a member pinned to its section keeps its tab on the strip
// under the folded header, in strip order; the header stands in for the HIDDEN members alone — its
// count reads those, never a tab already on screen. A view preference like the
// fold, stored beside it under romp:tabgroups: ONE entry per tab and section — the sid, the section's
// displayed name at pin time, and the local tag's id when the section has one — matched under either
// the name or the id (PinnedRef), written per section, carried across a tag's rename by the views
// adoption (tagRenames / followTagRenames). The toggle is a row in the tab menu's Tags flyout beside
// the "Move to" rows. Every case below asserts what the strip shows, through the real planStrip.
const VP = { ...V, tags: [...V.tags, { id: "g4", name: "archived", color: "#6b7280", members: ["old1", "old2", "old3"] }] };
const ARCH: SectionRef = { name: "archived", localId: "g4" };
const headsOf = (p: ReturnType<typeof planStrip>) =>
  p.items.filter((i): i is { head: TabSection; folded: boolean; active: boolean; hidden: string[] } => "head" in i);
const MAKE_HEAD = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
/** the strip as the user reads it — headers as #name, (folded) when folded, tabs by id — and the ids folded away */
const strip = (visible: readonly string[], unions: readonly TagUnion[], st: TabGroupsState, active: string) => {
  const p = planStrip(visible, unions, st, active, false);
  return [p.items.map((i) => ("head" in i ? `#${i.head.name}${i.folded ? "(folded)" : ""}` : i.id)), [...p.folded]] as const;
};
/** a remote host's tag as the kernel lists it (id = host:tagid, members already viewer-relative) */
const rt = (host: string, tid: string, name: string, members: string[]) => ({ id: `${host}:${tid}`, host, name, color: "#4EC9B0", members });
const foldAll = (st: TabGroupsState, ...names: string[]) => names.reduce((s, n) => setSectionCollapsed(s, n, true), st);
const secOf = (unions: readonly TagUnion[], name: string) => sectionRef(unions.find((u) => u.name === name)!);
/** the remote hosts the demo world has attached with the tunnel up — the prune judges their sessions' entries */
const HOSTS: ReadonlySet<string> = new Set(["TESTHOST-A", "TESTHOST-B"]);

test("executed: a folded section with one PINNED member renders the header, then that member; the hidden ids alone fold away", () => {
  const unions = viewTagUnion(VP);
  const st = setPinned(parseTabGroups(null), ARCH, "old2", true);
  assert.equal(isPinned(st, ARCH, "old2"), true);
  assert.equal(isPinned(st, ARCH, "old1"), false, "the sid must match — a pin is one member's, not the tag's");
  assert.equal(isPinned(st, { name: "infra", localId: "g2" }, "old2"), false, "…and the section must: a move to another group starts unpinned there");
  assert.deepEqual(st.pinned, [{ sid: "old2", name: "archived", id: "g4" }], "the section's name and its local tag's id, and the sid");
  assert.deepEqual(secOf(unions, "archived"), ARCH, "the menu row's section is the union's name and local id");
  const p = planStrip(["web", "old1", "old2", "old3", "loose"], unions, st, "web", false);
  assert.deepEqual(p.items.map((i) => ("head" in i ? `#${i.head.name}${i.folded ? "(folded)" : ""}` : i.id)),
    ["#infra", "web", "#archived(folded)", "old2", "#null", "loose"], "the header, then the pinned member in its place; old1/old3 stay folded");
  const arch = headsOf(p).find((h) => h.head.name === "archived")!;
  assert.deepEqual(arch.hidden, ["old1", "old3"], "what the header stands in for");
  assert.deepEqual([...p.folded], ["old1", "old3"], "keyboard cycling skips the hidden ones only — the pinned tab is reachable");
  assert.deepEqual([arch.head.name, arch.head.localId], ["archived", "g4"], "the plan's section carries what a pin matches against (the same fields sectionRef yields)");
  // a second member's pin is its own: setting or clearing one leaves the other (a filter on the section
  // alone would drop every pin under it)
  const two = setPinned(st, ARCH, "old3", true);
  assert.deepEqual(two.pinned, [{ sid: "old2", name: "archived", id: "g4" }, { sid: "old3", name: "archived", id: "g4" }]);
  assert.deepEqual(setPinned(two, ARCH, "old3", false).pinned, [{ sid: "old2", name: "archived", id: "g4" }]);
  assert.deepEqual(setPinned(two, ARCH, "old2", true).pinned, [{ sid: "old3", name: "archived", id: "g4" }, { sid: "old2", name: "archived", id: "g4" }], "set on again: one entry, moved to the end");
  // an open section: nothing hidden, pins irrelevant (archived starts folded, so it is opened first: the
  // active tab's section folds like any other now)
  assert.deepEqual(headsOf(planStrip(["old1", "old2"], unions, setSectionCollapsed(st, "archived", false), "old1", false))[0].hidden, []);
  // the untagged trail has no fold: nothing pins there
  assert.deepEqual(setPinned(st, { name: null, localId: null }, "loose", true), st);
});

test("executed: LOCAL ONLY — the entry carries the name and the id; a rename the client watched follows by name, one it missed matches by id; delete, a member moved out and a closed session prune it", () => {
  const unions = viewTagUnion(VP);
  const infra = secOf(unions, "infra");
  assert.deepEqual(infra, { name: "infra", localId: "g2" });
  let st = foldAll(parseTabGroups(null), "infra", "platform");
  st = setPinned(st, infra, "web", true);
  assert.deepEqual(st.pinned, [{ sid: "web", name: "infra", id: "g2" }]);
  assert.deepEqual(strip(["web", "tests", "loose"], unions, st, "loose"), [["#qa", "tests", "#infra(folded)", "web", "#null", "loose"], []], "pinned: on the strip under the folded header");
  // the tag renamed, the client watching: the entry follows the name (the id stays)
  const V1 = { ...VP, tags: VP.tags.map((t) => (t.id === "g2" ? { ...t, name: "platform" } : t)) };
  const renames = tagRenames(VP, V1);
  assert.deepEqual(renames, [{ id: "g2", from: "infra", to: "platform", local: true, members: ["web", "api"] }]);
  const u1 = viewTagUnion(V1);
  const st1 = followTagRenames(st, renames, u1);
  assert.deepEqual(st1.pinned, [{ sid: "web", name: "platform", id: "g2" }], "rewritten to the new name; no same-named tag on the other side, so no second entry");
  assert.deepEqual(strip(["web", "tests", "loose"], u1, st1, "loose"), [["#qa", "tests", "#platform(folded)", "web", "#null", "loose"], []], "pinned under the renamed group");
  assert.equal(isPinned(st1, secOf(u1, "platform"), "web"), true, "the menu row reads on there");
  assert.equal(isPinned(st1, { name: "infra", localId: "g8" }, "web"), false, "another local tag that later takes the old name: not this pin");
  // the rename missed (this browser had no page open): the stored id still finds the section
  assert.deepEqual(strip(["web", "tests", "loose"], u1, st, "loose"), [["#qa", "tests", "#platform(folded)", "web", "#null", "loose"], []], "matched by id, name notwithstanding");
  assert.deepEqual(tagRenames(null, V1), [], "no previous blob: nothing to compare, the id carries it");
  // the prune, on the pin row's write: the tag deleted, the member moved out, the session closed
  const known = new Set(["web", "api", "tests", "old1", "old2", "old3", "TESTHOST-A:m1", "loose"]);
  assert.equal(prunePinned(st1, u1, known, HOSTS), st1, "live: the same object");
  assert.deepEqual(prunePinned(st1, viewTagUnion({ ...V1, tags: V1.tags.filter((t) => t.id !== "g2") }), known, HOSTS).pinned, [], "the tag deleted: no union of that name or id holds web");
  assert.deepEqual(prunePinned(st1, viewTagUnion({ ...V1, tags: V1.tags.map((t) => (t.id === "g2" ? { ...t, members: ["api"] } : t)) }), known, HOSTS).pinned, [], "web moved out of the tag");
  assert.deepEqual(prunePinned(st1, u1, new Set([...known].filter((k) => k !== "web")), HOSTS).pinned, [], "the session closed");
});

test("executed: REMOTE ONLY — the entry is the name alone; it holds through a host detaching and a local same-name tag appearing; the host's rename follows when a client watched, a rename none watched is the stated limit; a same-named tag on another host with other members is not touched", () => {
  const A = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m1"]);
  const B = rt("TESTHOST-B", "t2", "infra", ["TESTHOST-B:m1"]);
  const bothV = { active: "all", tags: [], remoteTags: [A, B] };
  const both = viewTagUnion(bothV);
  assert.deepEqual([both[0].ids, both[0].localId], [["TESTHOST-A:t1", "TESTHOST-B:t2"], null], "one section, two hosts' tags, no local id");
  assert.deepEqual(secOf(both, "infra"), { name: "infra", localId: null });
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(both, "infra"), "TESTHOST-B:m1", true);
  assert.deepEqual(st.pinned, [{ sid: "TESTHOST-B:m1", name: "infra" }], "the name, and no id — a remote host's tag id is never stored");
  const vis = ["TESTHOST-A:m1", "TESTHOST-B:m1", "loose"];
  assert.deepEqual(strip(vis, both, st, "loose"), [["#infra(folded)", "TESTHOST-B:m1", "#null", "loose"], ["TESTHOST-A:m1"]]);
  // host A detaches: the section is B's alone, the pin stands; A's member, in no tag now, trails
  assert.deepEqual(strip(vis, viewTagUnion({ active: "all", tags: [], remoteTags: [B] }), st, "loose"), [["#infra(folded)", "TESTHOST-B:m1", "#null", "TESTHOST-A:m1", "loose"], []]);
  // a local infra appears (g7, holding web): the section gains a local id, the name-stored pin still matches
  const localToo = viewTagUnion({ active: "all", tags: [{ id: "g7", name: "infra", color: "#4EC9B0", members: ["web"] }], remoteTags: [A, B] });
  assert.deepEqual(secOf(localToo, "infra"), { name: "infra", localId: "g7" });
  assert.deepEqual(strip(["web", ...vis], localToo, st, "loose"), [["#infra(folded)", "TESTHOST-B:m1", "#null", "loose"], ["web", "TESTHOST-A:m1"]]);
  // host B renames its tag, the client watching: the entry follows the name (no id to attach — the tag is B's)
  const renamedV = { active: "all", tags: [], remoteTags: [A, { ...B, name: "ops" }] };
  const renames = tagRenames(bothV, renamedV);
  assert.deepEqual(renames, [{ id: "TESTHOST-B:t2", from: "infra", to: "ops", local: false, members: ["TESTHOST-B:m1"] }]);
  const ru = viewTagUnion(renamedV);
  const st1 = followTagRenames(st, renames, ru);
  assert.deepEqual(st1.pinned, [{ sid: "TESTHOST-B:m1", name: "ops" }], "the new name; A's infra does not hold B's member, so nothing stays under the old name");
  assert.deepEqual(strip(vis, ru, st1, "loose"), [["#infra(folded)", "#ops(folded)", "TESTHOST-B:m1", "#null", "loose"], ["TESTHOST-A:m1"]], "pinned under the renamed group");
  // host A renames ITS infra instead: A's tag does not hold B's member, so the pin is not A's to move
  const aRenamed = { active: "all", tags: [], remoteTags: [{ ...A, name: "ops" }, B] };
  const aFollowed = followTagRenames(st, tagRenames(bothV, aRenamed), viewTagUnion(aRenamed));
  assert.deepEqual(aFollowed.pinned, st.pinned, "untouched: not A's to move");
  assert.deepEqual(aFollowed.followed, { "TESTHOST-A:t1": "ops" }, "…and the rename is remembered as followed all the same (a pin made under ops later is no late pane's to split)");
  assert.deepEqual(strip(vis, viewTagUnion(aRenamed), st, "loose"), [["#ops(folded)", "#infra(folded)", "TESTHOST-B:m1", "#null", "loose"], ["TESTHOST-A:m1"]]);
  // THE LIMIT: B's rename while no client of this browser watched — no previous blob, no id stored —
  // leaves the entry under the old name, and the member folds away until the user pins it again
  assert.deepEqual(tagRenames(null, renamedV), []);
  assert.deepEqual(strip(vis, ru, st, "loose"), [["#infra(folded)", "#ops(folded)", "#null", "loose"], ["TESTHOST-A:m1", "TESTHOST-B:m1"]], "the stated limit");
  const again = prunePinned(setPinned(st, secOf(ru, "ops"), "TESTHOST-B:m1", true), ru, new Set([...vis]), HOSTS);
  assert.deepEqual(again.pinned, [{ sid: "TESTHOST-B:m1", name: "ops" }], "pinned again under ops; the pin row's prune drops the old-name entry, which no union of that name holds");
  assert.deepEqual(strip(vis, ru, again, "loose"), [["#infra(folded)", "#ops(folded)", "TESTHOST-B:m1", "#null", "loose"], ["TESTHOST-A:m1"]]);
});

test("executed: SHARED — a member a local tag and a same-named remote tag both hold: one entry, name and id; the local delete leaves the name to match; the local rename watched rewrites it AND keeps the old-name half while the remote tag holds the tab, so either drag order finds the pin; off is per section", () => {
  // local infra (g7) holds web and api; host A's infra holds web too (a remote host tagging one of ours)
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]);
  const local = (name: string, members = ["web", "api"]) => ({ id: "g7", name, color: "#4EC9B0", members });
  const mixedV = { active: "all", tags: [local("infra")], remoteTags: [A] };
  const mixed = viewTagUnion(mixedV);
  assert.deepEqual([mixed.length, mixed[0].members, mixed[0].localId], [1, ["web", "api"], "g7"], "one section");
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(mixed, "infra"), "web", true);
  assert.deepEqual(st.pinned, [{ sid: "web", name: "infra", id: "g7" }]);
  const vis = ["web", "api", "loose"];
  assert.deepEqual(strip(vis, mixed, st, "loose"), [["#infra(folded)", "web", "#null", "loose"], ["api"]]);
  // the local tag deleted: the section is the remote-only infra (no local id) — the name matches
  const deleted = viewTagUnion({ active: "all", tags: [], remoteTags: [A] });
  assert.deepEqual(strip(vis, deleted, st, "loose"), [["#infra(folded)", "web", "#null", "api", "loose"], []], "web still pinned under the folded header; api, in no tag now, trails");
  assert.equal(prunePinned(st, deleted, new Set(vis), HOSTS), st, "and the prune keeps it: a union named infra holds web");
  // the local tag renamed, the client watching: the entry follows to ops (by id), and because A's infra
  // still holds web — the section SPLIT — an entry for the infra half stays beside it
  const renamedV = { active: "all", tags: [local("ops")], remoteTags: [A] };
  const renames = tagRenames(mixedV, renamedV);
  const ru = viewTagUnion(renamedV);
  assert.deepEqual(ru.map((u) => u.name), ["ops", "infra"], "the default order after a rename: local unions first");
  const st2 = followTagRenames(st, renames, ru);
  assert.deepEqual(st2.pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "infra" }], "the rewritten entry, and the old-name half");
  assert.equal(followTagRenames(st2, renames, ru), st2, "idempotent: a second pane adopting the same frame after the first has written finds nothing to change — the same object, no second write");
  assert.deepEqual(strip(vis, ru, st2, "loose"), [["#ops(folded)", "web", "#infra(folded)", "web", "#null", "loose"], ["api"]], "web shows under ops AND infra (every-tag rule, T264b), pinned in both");
  // the user had dragged infra into place before the rename: the kernel leaves tagOrder alone, so infra
  // keeps its slot and ops falls behind — web homes in the remote-only infra, and the kept half matches
  const infraFirst = viewTagUnion({ ...renamedV, tagOrder: ["infra"] });
  assert.deepEqual(infraFirst.map((u) => u.name), ["infra", "ops"]);
  assert.deepEqual(strip(vis, infraFirst, st2, "loose"), [["#infra(folded)", "web", "#ops(folded)", "web", "#null", "loose"], ["api"]], "web pinned under infra; api under ops");
  assert.equal(isPinned(st2, secOf(ru, "ops"), "web"), true, "the menu row reads on under ops…");
  assert.equal(isPinned(st2, secOf(infraFirst, "infra"), "web"), true, "…and under infra");
  // the prune keeps both halves while both hold web, and drops the infra half once the remote tag lets go
  const known = new Set(vis);
  assert.equal(prunePinned(st2, ru, known, HOSTS), st2);
  assert.equal(prunePinned(st2, infraFirst, known, HOSTS), st2);
  const letGo = viewTagUnion({ ...renamedV, remoteTags: [{ ...A, members: [] }] });
  assert.deepEqual(prunePinned(st2, letGo, known, HOSTS).pinned, [{ sid: "web", name: "ops", id: "g7" }], "no union named infra holds web: that half is dead");
  // the same rename with no client watching: the id half is found; the infra half is only the kept one
  assert.deepEqual(strip(vis, ru, st, "loose"), [["#ops(folded)", "web", "#infra(folded)", "web", "#null", "loose"], ["api"]], "missed: the id finds ops, the stored name finds infra — web shows under both");
  assert.deepEqual(strip(vis, infraFirst, st, "loose"), [["#infra(folded)", "web", "#ops(folded)", "web", "#null", "loose"], ["api"]], "missed, infra first: the stored name IS infra — the section the user pinned in");
  // OFF IS PER SECTION: off under ops clears the ops entry alone; the infra pin is the infra row's to
  // clear, and the row there reads on
  const off = setPinned(st2, secOf(ru, "ops"), "web", false);
  assert.deepEqual(off.pinned, [{ sid: "web", name: "infra" }]);
  assert.deepEqual(strip(vis, ru, off, "loose"), [["#ops(folded)", "#infra(folded)", "web", "#null", "loose"], ["api"]], "web folded away under ops, shown under infra where its pin stands");
  assert.deepEqual(strip(vis, infraFirst, off, "loose"), [["#infra(folded)", "web", "#ops(folded)", "#null", "loose"], ["api"]], "infra dragged first: web shows through infra's fold — the pin set there stands until cleared there");
  assert.equal(isPinned(off, secOf(infraFirst, "infra"), "web"), true, "the row under infra reads on");
  assert.deepEqual(setPinned(off, secOf(infraFirst, "infra"), "web", false).pinned, [], "off under infra: cleared");
  assert.deepEqual(togglePinned(togglePinned(off, secOf(ru, "ops"), "web"), secOf(ru, "ops"), "web"), off, "the row's toggle, on then off, lands back where it started");
  assert.deepEqual(togglePinned(off, secOf(ru, "ops"), "web").pinned, [{ sid: "web", name: "infra" }, { sid: "web", name: "ops", id: "g7" }], "on under ops: the ops entry, name and id");
});

test("executed: a LOCAL-ONLY member of a MIXED union — the remote same-named tag does not hold it, so its rename follows the local tag alone, under either drag order", () => {
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]);
  const local = (name: string) => ({ id: "g7", name, color: "#4EC9B0", members: ["web", "api"] });
  const mixedV = { active: "all", tags: [local("infra")], remoteTags: [A] };
  const mixed = viewTagUnion(mixedV);
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(mixed, "infra"), "api", true);
  assert.deepEqual(st.pinned, [{ sid: "api", name: "infra", id: "g7" }]);
  assert.deepEqual(strip(["web", "api", "loose"], mixed, st, "loose"), [["#infra(folded)", "api", "#null", "loose"], ["web"]]);
  const renamedV = { active: "all", tags: [local("ops")], remoteTags: [A] };
  const st2 = followTagRenames(st, tagRenames(mixedV, renamedV), viewTagUnion(renamedV));
  assert.deepEqual(st2.pinned, [{ sid: "api", name: "ops", id: "g7" }], "no infra half: A's infra does not hold api");
  assert.deepEqual(strip(["web", "api", "loose"], viewTagUnion(renamedV), st2, "loose"), [["#ops(folded)", "api", "#infra(folded)", "#null", "loose"], ["web"]]);
  assert.deepEqual(strip(["web", "api", "loose"], viewTagUnion({ ...renamedV, tagOrder: ["infra"] }), st2, "loose"), [["#infra(folded)", "#ops(folded)", "api", "#null", "loose"], ["web"]],
    "infra dragged first: web homes in the remote infra (unpinned there), api in ops, pinned");
});

test("executed: TWO TAGS WITH DIFFERENT NAMES holding one tab — a pin under each is its own; the drag order picks the home, each home's pin stands, and off clears one", () => {
  // tags a (gA) and b (gB) both hold web; both folded. An earlier cut: an on under b replaced
  // the entry set under a, and web folded away under a after the user's last gesture on it was on.
  const ab = (order?: string[]) => viewTagUnion({ active: "all", tags: [
    { id: "gA", name: "a", color: "#4EC9B0", members: ["web", "x1"] },
    { id: "gB", name: "b", color: "#DD42FF", members: ["web", "x2"] },
  ], ...(order ? { tagOrder: order } : {}) });
  const aFirst = ab(), bFirst = ab(["b", "a"]);
  const vis = ["web", "x1", "x2", "loose"];
  let st = foldAll(parseTabGroups(null), "a", "b");
  st = setPinned(st, secOf(aFirst, "a"), "web", true);   // 1. Show when folded under a
  assert.deepEqual(st.pinned, [{ sid: "web", name: "a", id: "gA" }]);
  assert.deepEqual(strip(vis, aFirst, st, "loose"), [["#a(folded)", "web", "#b(folded)", "#null", "loose"], ["x1", "x2"]]);
  // 2. b dragged first: web shows under b too (every-tag rule), where nothing pins it — hidden there, shown under a; the b row reads off
  assert.deepEqual(strip(vis, bFirst, st, "loose"), [["#b(folded)", "#a(folded)", "web", "#null", "loose"], ["x2", "x1"]]);
  assert.equal(isPinned(st, secOf(bFirst, "b"), "web"), false);
  // 3. Show when folded under b: b's entry is added; a's stands
  st = setPinned(st, secOf(bFirst, "b"), "web", true);
  assert.deepEqual(st.pinned, [{ sid: "web", name: "a", id: "gA" }, { sid: "web", name: "b", id: "gB" }]);
  assert.deepEqual(strip(vis, bFirst, st, "loose"), [["#b(folded)", "web", "#a(folded)", "web", "#null", "loose"], ["x2", "x1"]]);
  // 4. a dragged first again: the pin set under a is still there
  assert.deepEqual(strip(vis, aFirst, st, "loose"), [["#a(folded)", "web", "#b(folded)", "web", "#null", "loose"], ["x1", "x2"]], "no gesture turned it off");
  // off under a clears a's entry alone
  const off = setPinned(st, secOf(aFirst, "a"), "web", false);
  assert.deepEqual(off.pinned, [{ sid: "web", name: "b", id: "gB" }]);
  assert.deepEqual(strip(vis, aFirst, off, "loose"), [["#a(folded)", "#b(folded)", "web", "#null", "loose"], ["x1", "x2"]], "folded away under a, shown under b where its pin stands — so not skipped by the keyboard");
  assert.deepEqual(strip(vis, bFirst, off, "loose"), [["#b(folded)", "web", "#a(folded)", "#null", "loose"], ["x2", "x1"]], "still pinned under b");
  assert.equal(prunePinned(off, aFirst, new Set(vis), HOSTS), off, "both tags still hold web: nothing dead");
});

test("executed: MIRROR A — pinned BEFORE the second holder: a remote host's same-named tag later holds the tab; the local tag's delete and its rename (either drag order) keep the pin", () => {
  const local = (name: string) => ({ id: "g7", name, color: "#4EC9B0", members: ["web", "api"] });
  const aloneV = { active: "all", tags: [local("infra")], remoteTags: [] };
  const alone = viewTagUnion(aloneV);
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(alone, "infra"), "web", true);
  assert.deepEqual(st.pinned, [{ sid: "web", name: "infra", id: "g7" }], "the local tag alone holds web: name and id all the same");
  const vis = ["web", "api", "loose"];
  // host A's infra comes to hold web (the kernel lists a remote tag holding one of ours under the bare sid)
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]);
  const mixedV = { active: "all", tags: [local("infra")], remoteTags: [A] };
  const mixed = viewTagUnion(mixedV);
  assert.deepEqual(tagRenames(aloneV, mixedV), [], "a tag arriving is no rename");
  assert.deepEqual(strip(vis, mixed, st, "loose"), [["#infra(folded)", "web", "#null", "loose"], ["api"]], "still pinned in the mixed section");
  // the local tag deleted: web's home is the remote-only infra — the name matches
  assert.deepEqual(strip(vis, viewTagUnion({ active: "all", tags: [], remoteTags: [A] }), st, "loose"), [["#infra(folded)", "web", "#null", "api", "loose"], []]);
  // renamed to ops, the client watching, with infra dragged first: web homes in the remote infra
  const renamedV = { active: "all", tags: [local("ops")], remoteTags: [A], tagOrder: ["infra"] };
  const ru = viewTagUnion(renamedV);
  const st2 = followTagRenames(st, tagRenames(mixedV, renamedV), ru);
  assert.deepEqual(st2.pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "infra" }]);
  assert.deepEqual(strip(vis, ru, st2, "loose"), [["#infra(folded)", "web", "#ops(folded)", "web", "#null", "loose"], ["api"]], "pinned under infra, the home the drag order gives it");
  assert.deepEqual(strip(vis, viewTagUnion({ ...renamedV, tagOrder: [] }), st2, "loose"), [["#ops(folded)", "web", "#infra(folded)", "web", "#null", "loose"], ["api"]], "and under ops, the default home");
});

test("executed: MIRROR B — a remote pin, then a local same-name tag comes to hold the tab, then that tag's rename: the entry follows the local tag and gains its id, and the old-name half stays while the host's tag holds the tab", () => {
  const A = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m1"]);
  const remoteV = { active: "all", tags: [], remoteTags: [A] };
  let st = foldAll(parseTabGroups(null), "infra", "ops", "platform");
  st = setPinned(st, secOf(viewTagUnion(remoteV), "infra"), "TESTHOST-A:m1", true);
  assert.deepEqual(st.pinned, [{ sid: "TESTHOST-A:m1", name: "infra" }]);
  const vis = ["web", "TESTHOST-A:m1", "loose"];
  // a local infra (g9) appears and holds A's member too (the CLI's `romp tag infra --add`, by name)
  const mixedV = { active: "all", tags: [{ id: "g9", name: "infra", color: "#4EC9B0", members: ["web", "TESTHOST-A:m1"] }], remoteTags: [A] };
  const mixed = viewTagUnion(mixedV);
  assert.deepEqual(strip(vis, mixed, st, "loose"), [["#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]], "the name-stored pin matches the mixed section");
  // the local tag renamed to ops, the client watching
  const renamedV = { active: "all", tags: [{ id: "g9", name: "ops", color: "#4EC9B0", members: ["web", "TESTHOST-A:m1"] }], remoteTags: [A] };
  const ru = viewTagUnion(renamedV);
  const st2 = followTagRenames(st, tagRenames(mixedV, renamedV), ru);
  assert.deepEqual(st2.pinned, [{ sid: "TESTHOST-A:m1", name: "ops", id: "g9" }, { sid: "TESTHOST-A:m1", name: "infra" }],
    "the renamed tag holds the tab, so the no-id entry follows it and gains the id; A's infra still holds the tab, so the infra half stays");
  assert.deepEqual(strip(vis, ru, st2, "loose"), [["#ops(folded)", "TESTHOST-A:m1", "#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]], "default order: home ops, pinned");
  assert.deepEqual(strip(vis, viewTagUnion({ ...renamedV, tagOrder: ["infra"] }), st2, "loose"), [["#infra(folded)", "TESTHOST-A:m1", "#ops(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]], "infra dragged first: home infra, pinned");
  // the id it gained: a second rename, watched by no client, is found by id
  const againV = { ...renamedV, tags: [{ ...renamedV.tags[0], name: "platform" }] };
  assert.deepEqual(strip(vis, viewTagUnion(againV), st2, "loose"), [["#platform(folded)", "TESTHOST-A:m1", "#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]]);
  // a same-named tag on another host holding another member is not this rename's to move
  const B = rt("TESTHOST-B", "t2", "infra", ["TESTHOST-B:m1"]);
  const other = setPinned(foldAll(parseTabGroups(null), "infra"), { name: "infra", localId: null }, "TESTHOST-B:m1", true);
  const withB = { ...mixedV, remoteTags: [A, B] }, withBRenamed = { ...renamedV, remoteTags: [A, B] };
  assert.deepEqual(followTagRenames(other, tagRenames(withB, withBRenamed), viewTagUnion(withBRenamed)).pinned, other.pinned, "g9 does not hold B's member: untouched");
});

test("executed: a rename is followed ONCE PER BROWSER — the store remembers, by tag id, the name each renamed tag's pins were carried to, so a pane adopting the frame late (after the user turned a pin off), or a later frame from a stale base, or a same-seq frame carrying a remote rename already followed, changes nothing; a rename back and then forth follows each time; a frame whose renames match no pin is remembered too; the memory is pruned to live tags per host and round-trips", () => {
  // local infra (g7) holds web and api; host A's infra holds web too — the rename SPLITS the section
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]);
  const local = (name: string, members = ["web", "api"]) => ({ id: "g7", name, color: "#4EC9B0", members });
  const V0 = { active: "all", tags: [local("infra")], remoteTags: [A], seq: 4 };
  const V1 = { ...V0, tags: [local("ops")], seq: 5 };                                // the rename frame
  const V2 = { ...V1, tags: [local("ops", ["web", "api", "tests"])], seq: 6 };       // a later frame: a member added
  const u1 = viewTagUnion(V1), u2 = viewTagUnion(V2);
  const vis = ["web", "api", "loose"];
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(viewTagUnion(V0), "infra"), "web", true);
  // pane 1 adopts the rename frame: the entries are carried, and the memory says g7's pins went to ops
  const st1 = followTagRenames(st, tagRenames(V0, V1), u1);
  assert.deepEqual(st1.pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "infra" }]);
  assert.deepEqual(st1.followed, { g7: "ops" });
  assert.equal(followTagRenames(st1, tagRenames(V0, V1), u1), st1, "pane 2 adopting the same frame right after: the same object, no second write");
  // THE LATE-PANE BUG (the no-id face): the user turns the pin off under ops; then pane 2's socket redials and
  // it adopts the rename frame late, computing the rename against its stale base — web came back under ops
  const off = setPinned(st1, secOf(u1, "ops"), "web", false);
  assert.deepEqual(off.pinned, [{ sid: "web", name: "infra" }]);
  assert.deepEqual(off.followed, { g7: "ops" }, "the memory rides every write");
  assert.equal(followTagRenames(off, tagRenames(V0, V1), u1), off, "the late pane stands down: this browser already carried g7's pins to ops");
  assert.deepEqual(strip(vis, u1, off, "loose"), [["#ops(folded)", "#infra(folded)", "web", "#null", "loose"], ["api"]], "web stays folded away under ops, as the user left it — and shows under infra, where the kept pin stands");
  assert.equal(followTagRenames(off, tagRenames(V0, V2), u2), off, "…and a LATER frame adopted from the stale base — the same rename, coalesced — stands down too (a seq gate on the frame would have let it through)");
  // the id face: infra dragged first, the user turns the pin off under the kept infra half instead
  const offInfra = setPinned(st1, secOf(viewTagUnion({ ...V1, tagOrder: ["infra"] }), "infra"), "web", false);
  assert.deepEqual(offInfra.pinned, [{ sid: "web", name: "ops", id: "g7" }]);
  assert.equal(followTagRenames(offInfra, tagRenames(V0, V1), u1), offInfra, "the late pane would have matched the ops entry by id and re-added the infra half: it stands down");
  // a remote host's rename rides the local blob with NO seq change (remoteTags are the kernel's cached read of
  // the host; seq counts the local store's writes): the memory is the rename's, so that frame is once-per-browser too
  const R1 = { ...V1, remoteTags: [{ ...A, name: "platform" }] };   // seq still 5
  const stR = followTagRenames(st1, tagRenames(V1, R1), viewTagUnion(R1));
  assert.deepEqual(stR.pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "platform" }], "the infra half follows A's tag to platform");
  assert.deepEqual(stR.followed, { g7: "ops", "TESTHOST-A:t1": "platform" });
  const offR = setPinned(stR, secOf(viewTagUnion({ ...R1, tagOrder: ["platform"] }), "platform"), "web", false);
  assert.equal(followTagRenames(offR, tagRenames(V1, R1), viewTagUnion(R1)), offR, "a stale pane adopting that same-seq frame late stands down");
  // renamed BACK, then forth again: each is to a name the memory does not hold for the tag, so each follows
  const V3 = { ...V1, tags: [local("infra")], seq: 7 };
  const stBack = followTagRenames(st1, tagRenames(V1, V3), viewTagUnion(V3));
  assert.deepEqual([stBack.pinned, stBack.followed], [[{ sid: "web", name: "infra", id: "g7" }, { sid: "web", name: "infra" }], { g7: "infra" }], "back to infra: the ops entry follows by id; the remote half already names infra");
  const V4 = { ...V3, tags: [local("ops")], seq: 8 };
  const stForth = followTagRenames(stBack, tagRenames(V3, V4), viewTagUnion(V4));
  assert.deepEqual([stForth.pinned, stForth.followed], [[{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "infra" }], { g7: "ops" }], "and to ops again — followed, not mistaken for the first time");
  // a frame whose renames match no pin is remembered too, so a pin made under the new name AFTER it is not
  // split by a late pane (whose computed rename matches the entry by id while a same-named remote tag holds the tab)
  const none = foldAll(parseTabGroups(null), "infra", "ops");
  const seen = followTagRenames(none, tagRenames(V0, V1), u1);
  assert.deepEqual([seen.pinned, seen.followed], [[], { g7: "ops" }]);
  const later = setPinned(seen, secOf(u1, "ops"), "web", true);
  assert.equal(followTagRenames(later, tagRenames(V0, V1), u1), later, "stands down");
  assert.deepEqual(followTagRenames(setPinned(none, secOf(u1, "ops"), "web", true), tagRenames(V0, V1), u1).pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "infra" }],
    "(without the memory, the late pane adds an infra half the user never set)");
  // a permuted store changes nothing either: the memory, not the entries' order, is what the late pane reads
  const permuted = { ...st1, pinned: [...st1.pinned].reverse() };
  assert.equal(followTagRenames(permuted, tagRenames(V0, V1), u1), permuted);
  // the memory is pruned to the tags the blob still has, per host (the next test): a local id the store lacks and a
  // remote id its host no longer lists go; a host in no union at all keeps its entries
  const stale = { ...st1, followed: { g7: "ops", g99: "gone", "TESTHOST-A:t9": "gone", "TESTHOST-Z:t9": "away" } };
  assert.deepEqual(followTagRenames(stale, tagRenames(V1, R1), viewTagUnion(R1)).followed, { g7: "ops", "TESTHOST-A:t1": "platform", "TESTHOST-Z:t9": "away" },
    "g99 is in no union and A lists no t9: dropped; Z contributes no tag — detached, or not yet read — so its memory waits with it");
  // it persists beside the pins, reads back, and junk drops
  assert.deepEqual(parseTabGroups('{"followed":{"g7":"ops","g8":3,"x":null}}').followed, { g7: "ops" });
  assert.equal(parseTabGroups('{"followed":["g7"]}').followed, undefined);
  assert.equal(parseTabGroups('{"followed":{}}').followed, undefined, "an empty memory is no memory");
  const store = new Map<string, string>();
  const g: any = globalThis;
  const savedLS = g.localStorage;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  try {
    writeTabGroups(st1);
    assert.deepEqual(JSON.parse(store.get(TABGROUPS_KEY)!).followed, { g7: "ops" });
    assert.deepEqual(readTabGroups(u1), st1);
    writeTabGroups(parseTabGroups(null));
    assert.equal("followed" in JSON.parse(store.get(TABGROUPS_KEY)!), false, "not written when there is none");
  } finally {
    g.localStorage = savedLS;
  }
});

test("executed: the follow's memory is pruned PER HOST — a detached host's tag keeps its memory through the renames followed while it is away, so a stale pane adopting the reattach frame stands down; a down host's (tags cached) too; a deleted tag's goes, local or on a host the blob still lists", () => {
  // local infra (g7) holds web and api, local x (g8) holds tests; host A's infra (t1) holds web too
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]);
  const g7 = { id: "g7", name: "infra", color: "#4EC9B0", members: ["web", "api"] };
  const g8 = (name: string) => ({ id: "g8", name, color: "#e0af68", members: ["tests"] });
  const V0 = { active: "all", tags: [g7, g8("x")], remoteTags: [A], seq: 4 };
  const R1 = { ...V0, remoteTags: [{ ...A, name: "platform" }] };                        // A renames t1 (a remote rename: no seq change)
  const D = { ...R1, remoteTags: [] };                                                    // A detaches: the kernel pops its cached tags
  const D2 = { ...D, tags: [g7, g8("y")], seq: 5 };                                      // while A is away, a local rename: x → y
  const V4 = { ...D2, remoteTags: [{ ...A, name: "platform" }], tagOrder: ["platform", "infra", "y"] };   // A is back, still platform, dragged first
  const vis = ["web", "api", "tests", "loose"];
  let st = foldAll(parseTabGroups(null), "infra", "platform", "y");
  st = setPinned(st, secOf(viewTagUnion(V0), "infra"), "web", true);
  // pane 1 follows A's rename; the user turns the platform half off
  const st1 = followTagRenames(st, tagRenames(V0, R1), viewTagUnion(R1));
  assert.deepEqual(st1.pinned, [{ sid: "web", name: "infra", id: "g7" }, { sid: "web", name: "platform" }]);
  const off = setPinned(st1, secOf(viewTagUnion({ ...R1, tagOrder: ["platform"] }), "platform"), "web", false);
  assert.deepEqual([off.pinned, off.followed], [[{ sid: "web", name: "infra", id: "g7" }], { "TESTHOST-A:t1": "platform" }]);
  assert.equal(followTagRenames(off, tagRenames(R1, D), viewTagUnion(D)), off, "the detach frame renames nothing");
  // THE DETACH BUG: the x → y follow, with A away, pruned the memory to the blob's tags — and A's went with them
  const away = followTagRenames(off, tagRenames(D, D2), viewTagUnion(D2));
  assert.deepEqual(away.pinned, off.pinned, "no pin names x");
  assert.deepEqual(away.followed, { "TESTHOST-A:t1": "platform", g8: "y" }, "A's memory stands: the blob carries none of A's tags, so it cannot say the tag is gone");
  // A reattaches. A background pane whose held blob predates A's rename adopts the frame and computes
  // infra → platform again — already followed, so it stands down, and web stays folded away under platform
  assert.equal(followTagRenames(away, tagRenames(V0, V4), viewTagUnion(V4)), away, "the stale pane stands down");
  assert.deepEqual(strip(vis, viewTagUnion(V4), away, "loose"), [["#platform(folded)", "#infra(folded)", "web", "#y(folded)", "#null", "loose"], ["api", "tests"]]);
  assert.deepEqual(followTagRenames({ ...away, followed: { g8: "y" } }, tagRenames(V0, V4), viewTagUnion(V4)).pinned, [{ sid: "web", name: "infra", id: "g7" }, { sid: "web", name: "platform" }],
    "(without A's memory, the late pane re-applies the pin the user turned off)");
  // A DOWN instead of detached: the kernel keeps a down host's cached tags in the blob, so its ids are live and the memory stood already
  const downV = { ...R1, tags: [g7, g8("y")], seq: 5 };
  assert.deepEqual(followTagRenames(off, tagRenames(R1, downV), viewTagUnion(downV)).followed, { "TESTHOST-A:t1": "platform", g8: "y" });
  // a deleted tag's memory goes — a local id the store lacks, a remote id its host (in the blob) no longer lists —
  // while a host in no union keeps every entry of its own
  const stale = { ...away, followed: { ...away.followed, g99: "gone", "TESTHOST-A:t9": "gone", "TESTHOST-Z:t9": "away" } };
  const V5 = { ...V4, tags: [g7, g8("z")], seq: 6 };
  assert.deepEqual(followTagRenames(stale, tagRenames(V4, V5), viewTagUnion(V5)).followed, { "TESTHOST-A:t1": "platform", g8: "z", "TESTHOST-Z:t9": "away" },
    "g99 is not in the local store and A lists no t9: dropped; Z contributes no tag, so its memory waits with it");
});

test("executed: the follow's memory is checked against EVERY adopted blob — a remembered tag the blob names by another name was renamed on while no client watched, and the pins under the remembered name are carried to the blob's name, the rename this browser owes (an earlier cut dropped the entry, and the pin stayed under a name no tag had), so the tag's next rename is followed as watched; a tag under the remembered name keeps its entry (the late pane still stands down); an absent host's entries still wait; a DOWN host's rename back is watched; with no memory of the tag the limit stands", () => {
  // host A's tag t1 (web) holds A:m1; the user pins A:m1 under web — a remote-only entry, nothing to follow by id
  const t1 = (name: string) => rt("TESTHOST-A", "t1", name, ["TESTHOST-A:m1"]);
  const g8 = (name: string) => ({ id: "g8", name, color: "#e0af68", members: ["tests"] });
  const V0 = { active: "all", tags: [g8("x")], remoteTags: [t1("web")], tagOrder: ["web", "api", "x", "y"], seq: 4 };
  const V1 = { ...V0, remoteTags: [t1("api")] };                 // A renames t1 web → api, watched (no seq change: a remote rename)
  const D = { ...V1, remoteTags: [] };                          // A detaches: its cached tags leave the blob
  const D2 = { ...D, tags: [g8("y")], seq: 5 };                 // while A is away, a local rename x → y
  const V4 = { ...D2, remoteTags: [t1("web")] };                // A reattaches — t1 renamed BACK to web while detached, unwatched
  const V5 = { ...V4, remoteTags: [t1("api")] };                // A renames web → api again, watched
  const vis = ["TESTHOST-A:m1", "tests", "loose"];
  const HOSTS = new Set(["TESTHOST-A"]);
  let st = foldAll(parseTabGroups(null), "web", "api", "x", "y");
  st = setPinned(st, secOf(viewTagUnion(V0), "web"), "TESTHOST-A:m1", true);
  const st1 = followTagRenames(st, tagRenames(V0, V1), viewTagUnion(V1));
  assert.deepEqual([st1.pinned, st1.followed], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api" }]);
  // the late pane: the user turns the pin off under api, and a pane adopting the rename frame late stands down —
  // t1 is under api, where its pins were carried
  const off = setPinned(st1, secOf(viewTagUnion(V1), "api"), "TESTHOST-A:m1", false);
  assert.deepEqual(off.pinned, []);
  assert.equal(followTagRenames(off, tagRenames(V0, V1), viewTagUnion(V1)), off, "the late pane stands down");
  // the detach: A's memory waits with its host, through a local rename followed while A is away
  assert.equal(followTagRenames(st1, tagRenames(V1, D), viewTagUnion(D)), st1, "the detach frame: the memory stands, nothing is written");
  const away = followTagRenames(st1, tagRenames(D, D2), viewTagUnion(D2));
  assert.deepEqual([away.pinned, away.followed], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api", g8: "y" }]);
  // THE STALE-MEMORY BUG: the reattach frame names t1 `web` — the tag moved on while no client watched (t1 was not in
  // the held blob, so the frame names no rename), and the memory of its pins' last home, api, is stale. Kept, it
  // read the next rename to api as already followed (the counterfactual below). Dropped alone, it left
  // the pin under api, where it matched nothing and the tab folded away until the user pinned it again — the
  // memory names the rename this browser OWES, api → web, and the pin is carried on this adoption
  assert.deepEqual(tagRenames(D2, V4), [], "(a tag absent from the held blob is no rename)");
  const back = followTagRenames(away, tagRenames(D2, V4), viewTagUnion(V4));
  assert.deepEqual([back.pinned, back.followed], [[{ sid: "TESTHOST-A:m1", name: "web" }], { "TESTHOST-A:t1": "web", g8: "y" }], "the owed rename is followed, and the memory re-stamped");
  assert.deepEqual(strip(vis, viewTagUnion(V4), back, "loose"), [["#web(folded)", "TESTHOST-A:m1", "#y(folded)", "#null", "loose"], ["tests"]], "m1 stays on the strip under web, no gesture needed");
  // a pin write meanwhile changes nothing: the entry already names web
  const repin = prunePinned(setPinned(back, secOf(viewTagUnion(V4), "web"), "TESTHOST-A:m1", true), viewTagUnion(V4), new Set(vis), HOSTS);
  assert.deepEqual(repin.pinned, [{ sid: "TESTHOST-A:m1", name: "web" }]);
  // A renames web → api again, watched: to a name the memory does not hold for t1 — followed, and the tab stays on the strip
  const again = followTagRenames(repin, tagRenames(V4, V5), viewTagUnion(V5));
  assert.deepEqual([again.pinned, again.followed], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api", g8: "y" }]);
  assert.deepEqual(strip(vis, viewTagUnion(V5), again, "loose"), [["#api(folded)", "TESTHOST-A:m1", "#y(folded)", "#null", "loose"], ["tests"]]);
  const kept = followTagRenames({ ...repin, followed: away.followed }, tagRenames(V4, V5), viewTagUnion(V5));
  assert.deepEqual(kept.pinned, repin.pinned, "(had the memory survived the reattach frame unchecked, the rename read as already followed …");
  assert.deepEqual(strip(vis, viewTagUnion(V5), kept, "loose"), [["#api(folded)", "#y(folded)", "#null", "loose"], ["TESTHOST-A:m1", "tests"]], "… and the tab folded away with no gesture on it)");
  // no detach at all — the PAGE was closed while A renamed t1 back to web (the same class, wider): the reopened
  // page's first frame has no held blob to name a rename, and the memory alone names the one owed
  const V1w = { ...V1, remoteTags: [t1("web")] };
  const reopened = followTagRenames(st1, tagRenames(null, V1w), viewTagUnion(V1w));
  assert.deepEqual([reopened.pinned, reopened.followed], [[{ sid: "TESTHOST-A:m1", name: "web" }], { "TESTHOST-A:t1": "web" }]);
  assert.deepEqual(followTagRenames(reopened, tagRenames(V1w, V1), viewTagUnion(V1)).pinned, [{ sid: "TESTHOST-A:m1", name: "api" }], "web → api after the reopen: followed");
  // THE LIMIT as it stands: with NO memory of the tag (no rename of it followed by this browser), the page closed
  // across web → api leaves the pin under web, matching nothing, until the user pins the tab again under api —
  // and that pin's renames are followed from then on, watched or, through the memory the first makes, owed
  const noMem = followTagRenames(st, tagRenames(null, V1), viewTagUnion(V1));
  assert.equal(noMem, st, "nothing to carry, nothing to drop: no write");
  assert.deepEqual(strip(vis, viewTagUnion(V1), noMem, "loose"), [["#api(folded)", "#x(folded)", "#null", "loose"], ["TESTHOST-A:m1", "tests"]], "m1 folded away under api: the pin names web");
  const repin2 = prunePinned(setPinned(noMem, secOf(viewTagUnion(V1), "api"), "TESTHOST-A:m1", true), viewTagUnion(V1), new Set(vis), HOSTS);
  assert.deepEqual(repin2.pinned, [{ sid: "TESTHOST-A:m1", name: "api" }], "the write prunes the web entry: no union of the name holds the tab");
  const V1o = { ...V1, remoteTags: [t1("ops")] };
  const watched = followTagRenames(repin2, tagRenames(V1, V1o), viewTagUnion(V1o));
  assert.deepEqual([watched.pinned, watched.followed], [[{ sid: "TESTHOST-A:m1", name: "ops" }], { "TESTHOST-A:t1": "ops" }]);
  assert.deepEqual(followTagRenames(watched, tagRenames(null, V1), viewTagUnion(V1)).pinned, [{ sid: "TESTHOST-A:m1", name: "api" }], "renamed back while the page was closed: owed through the memory, and carried");
  // A DOWN instead of detached: the kernel serves A's cached tags while it is unreachable, so the rename back
  // lands in the blob as A answers again — WATCHED, api → web — and the entry follows it; no memory is stale
  const downV = { ...V1, tags: [g8("y")], seq: 5 };              // A down: t1 still api in the blob; x → y followed meanwhile
  const dAway = followTagRenames(st1, tagRenames(V1, downV), viewTagUnion(downV));
  assert.deepEqual(dAway.followed, { "TESTHOST-A:t1": "api", g8: "y" }, "t1 is under api, as remembered: the memory stands");
  const upV = { ...downV, remoteTags: [t1("web")] };            // A answers again: the supervisor's fresh read shows web
  const dBack = followTagRenames(dAway, tagRenames(downV, upV), viewTagUnion(upV));
  assert.deepEqual([dBack.pinned, dBack.followed], [[{ sid: "TESTHOST-A:m1", name: "web" }], { "TESTHOST-A:t1": "web", g8: "y" }], "the rename back was watched: the entry follows it, and the memory is re-stamped");
  const upApi = { ...upV, remoteTags: [t1("api")] };
  const dAgain = followTagRenames(dBack, tagRenames(upV, upApi), viewTagUnion(upApi));
  assert.deepEqual([dAgain.pinned, dAgain.followed], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api", g8: "y" }]);
  // a memory whose host is absent still waits, and a deleted tag's still goes, on a frame with no renames too
  const waiting = { ...back, followed: { g8: "y", "TESTHOST-Z:t9": "away", "TESTHOST-A:t9": "gone", g99: "gone" } };
  assert.deepEqual(followTagRenames(waiting, [], viewTagUnion(V4)).followed, { g8: "y", "TESTHOST-Z:t9": "away" });
  const stands = { ...back, followed: { g8: "y", "TESTHOST-Z:t9": "away" } };
  assert.equal(followTagRenames(stands, [], viewTagUnion(V4)), stands, "…and nothing to drop is no write: the same object");
});

test("executed: a stale memory is a rename this browser OWES — a pane whose held blob predates TWO renames of a remote tag (web → api, followed by another pane, which carried the pin to api; then api → ops) computes the coalesced web → ops and carries the pin from api to ops itself, so the watching pane's api → ops stands down with the pin under ops in EITHER adoption order; a pin turned off under api is re-applied by neither; a local tag's pin follows by id either way; the carry reads the renamed tag's OWN members", () => {
  const t1 = (name: string) => rt("TESTHOST-A", "t1", name, ["TESTHOST-A:m1"]);
  const g8 = { id: "g8", name: "x", color: "#e0af68", members: ["tests"] };
  const V0 = { active: "all", tags: [g8], remoteTags: [t1("web")], tagOrder: ["web", "api", "ops", "x"], seq: 4 };
  const V1 = { ...V0, remoteTags: [t1("api")] };   // A renames web → api: pane P watches it; pane B's socket is dead
  const V2 = { ...V0, remoteTags: [t1("ops")] };   // A renames api → ops; B reconnects inside the pusher cycle in flight
  const vis = ["TESTHOST-A:m1", "tests", "loose"];
  const u2 = viewTagUnion(V2);
  const onStrip = [["#ops(folded)", "TESTHOST-A:m1", "#x(folded)", "#null", "loose"], ["tests"]];
  let st = foldAll(parseTabGroups(null), "web", "api", "ops", "x");
  st = setPinned(st, secOf(viewTagUnion(V0), "web"), "TESTHOST-A:m1", true);
  const st1 = followTagRenames(st, tagRenames(V0, V1), viewTagUnion(V1));
  assert.deepEqual([st1.pinned, st1.followed], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api" }], "P: the pin is carried to api");
  const rB = tagRenames(V0, V2), rP = tagRenames(V1, V2);
  assert.deepEqual([rB, rP].map((rs) => rs.map((r) => `${r.from}→${r.to}`)), [["web→ops"], ["api→ops"]], "B's frame coalesces the two renames; P's carries the second");
  // B FIRST (the bug): B's connect push hands it the ops blob before P's cycle frame. web → ops matches nothing
  // (the pin is under api); the memory says api and the blob says ops — the rename B owes, followed here
  const b = followTagRenames(st1, rB, u2);
  assert.deepEqual([b.pinned, b.followed], [[{ sid: "TESTHOST-A:m1", name: "ops" }], { "TESTHOST-A:t1": "ops" }], "B carries api → ops itself");
  assert.equal(followTagRenames(b, rP, u2), b, "P's frame: api → ops is followed, and it stands down");
  assert.deepEqual(strip(vis, u2, b, "loose"), onStrip, "the tab stays on the strip (an earlier cut left the pin under api: folded away, no gesture on it)");
  // P FIRST: the watched rename is followed, and B's coalesced one reads as followed
  const p = followTagRenames(st1, rP, u2);
  assert.deepEqual([p.pinned, p.followed], [[{ sid: "TESTHOST-A:m1", name: "ops" }], { "TESTHOST-A:t1": "ops" }]);
  assert.equal(followTagRenames(p, rB, u2), p, "B stands down");
  assert.deepEqual(strip(vis, u2, p, "loose"), onStrip);
  // the pin turned off under api between the renames: the owed rename carries nothing and re-applies nothing, either order
  const off = setPinned(st1, secOf(viewTagUnion(V1), "api"), "TESTHOST-A:m1", false);
  const offB = followTagRenames(off, rB, u2), offP = followTagRenames(off, rP, u2);
  assert.deepEqual([offB.pinned, offB.followed], [[], { "TESTHOST-A:t1": "ops" }], "B: the memory is re-stamped, no pin appears");
  assert.equal(followTagRenames(offB, rP, u2), offB, "P stands down");
  assert.deepEqual([offP.pinned, offP.followed], [[], { "TESTHOST-A:t1": "ops" }]);
  assert.equal(followTagRenames(offP, rB, u2), offP, "B stands down");
  // a LOCAL tag's pin carries the id and followed by it before this round; it still does, either order
  const L0 = { active: "all", tags: [{ id: "g1", name: "web", color: "#fff", members: ["m1"] }], tagOrder: ["web", "api", "ops"], seq: 4 };
  const L1 = { ...L0, tags: [{ ...L0.tags[0], name: "api" }], seq: 5 }, L2 = { ...L0, tags: [{ ...L0.tags[0], name: "ops" }], seq: 6 };
  const l0 = setPinned(foldAll(parseTabGroups(null), "web", "api", "ops"), secOf(viewTagUnion(L0), "web"), "m1", true);
  const l1 = followTagRenames(l0, tagRenames(L0, L1), viewTagUnion(L1));
  const lb = followTagRenames(l1, tagRenames(L0, L2), viewTagUnion(L2)), lp = followTagRenames(l1, tagRenames(L1, L2), viewTagUnion(L2));
  assert.deepEqual([lb.pinned, lb.followed], [[{ sid: "m1", name: "ops", id: "g1" }], { g1: "ops" }]);
  assert.equal(followTagRenames(lb, tagRenames(L1, L2), viewTagUnion(L2)), lb);
  assert.deepEqual(lp.pinned, lb.pinned);
  assert.equal(followTagRenames(lp, tagRenames(L0, L2), viewTagUnion(L2)), lp);
  // the owed rename reaches the tabs ITS tag holds, as a watched one does (tagRenames reads the tag's members) — not
  // the union's, which merges same-named tags: host A's infra holds A:m2, pinned id-less before any local infra
  // existed; local g9 goes site → infra, watched (it holds web); the page closes; g9 goes infra → ops, and host B's
  // own ops holds A:m2. On reopen the owed infra → ops is g9's: web's pins follow, A:m2's does not — A's infra still
  // holds it under infra, and an ops half on g9 (which never held it) would be a pin the user never made
  const inA = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m2"]), opsB = rt("TESTHOST-B", "t5", "ops", ["TESTHOST-A:m2"]);
  const g9 = (name: string) => ({ id: "g9", name, color: "#4EC9B0", members: ["web"] });
  const M0 = { active: "all", tags: [g9("site")], remoteTags: [inA], tagOrder: ["infra", "site", "ops"], seq: 4 };
  const M1 = { ...M0, tags: [g9("infra")], seq: 5 };
  const M2 = { ...M0, tags: [g9("ops")], remoteTags: [inA, opsB], seq: 6 };
  let m = setPinned(foldAll(parseTabGroups(null), "infra", "site", "ops"), secOf(viewTagUnion(M0), "infra"), "TESTHOST-A:m2", true);
  assert.deepEqual(m.pinned, [{ sid: "TESTHOST-A:m2", name: "infra" }], "id-less: the section had no local tag");
  m = followTagRenames(m, tagRenames(M0, M1), viewTagUnion(M1));
  assert.deepEqual([m.pinned, m.followed], [[{ sid: "TESTHOST-A:m2", name: "infra" }], { g9: "infra" }], "site → infra: no pin under site; remembered");
  const u = viewTagUnion(M2).find((x) => x.name === "ops")!;
  assert.deepEqual([u.members, u.locals.map((t) => t.id), u.remotes.map((t) => t.id)], [["web", "TESTHOST-A:m2"], ["g9"], ["TESTHOST-B:t5"]], "the union merges g9 and B's ops; the tags keep their own members");
  const re = followTagRenames(m, tagRenames(null, M2), viewTagUnion(M2));
  assert.deepEqual([re.pinned, re.followed], [[{ sid: "TESTHOST-A:m2", name: "infra" }], { g9: "ops" }], "the pin stays in A's infra: g9 never held the tab; the memory is re-stamped");
  const withWeb = followTagRenames({ ...m, pinned: [...m.pinned, { sid: "web", name: "infra", id: "g9" }] }, tagRenames(null, M2), viewTagUnion(M2));
  assert.deepEqual(withWeb.pinned, [{ sid: "TESTHOST-A:m2", name: "infra" }, { sid: "web", name: "ops", id: "g9" }], "…while g9's own pin is carried, by id");
});

test("executed: a stale pane's re-adoption of its held blob is NO NEWS — followAdoption returns the state untouched when the blob names every tag as the held one does, so a pane whose local socket is dead, re-adopting its stale blob on every router re-emit after a fresher pane carried the pin, neither carries it back nor re-stamps; and the memory's stamp orders the evidence — a blob older than the stamp for a tag's store stands down on that tag, so a late intermediate frame moves nothing, while a newer blob proceeds; the stamp persists, migrates, and drops junk", () => {
  // host A's t1 (web) holds A:m1, pinned under web; A's own store seq rides each remoteTag row (the kernel's)
  const t1 = (name: string, seq?: number) => ({ ...rt("TESTHOST-A", "t1", name, ["TESTHOST-A:m1"]), ...(seq !== undefined ? { seq } : {}) });
  const g8 = { id: "g8", name: "x", color: "#e0af68", members: ["tests"] };
  const V0 = { active: "all", tags: [g8], remoteTags: [t1("web", 10)], tagOrder: ["web", "api", "ops", "x"], seq: 4 };
  const V1 = { ...V0, remoteTags: [t1("api", 11)] };   // A renames web → api: pane P watches; pane B's local socket is dead
  const V2 = { ...V0, remoteTags: [t1("ops", 12)] };   // A renames api → ops
  const vis = ["TESTHOST-A:m1", "tests", "loose"];
  const u0 = viewTagUnion(V0), u1 = viewTagUnion(V1);
  let st = foldAll(parseTabGroups(null), "web", "api", "ops", "x");
  st = setPinned(st, secOf(u0, "web"), "TESTHOST-A:m1", true);
  // P adopts V1: the pin is carried to api, and the memory stamped with A's seq
  const p1 = followAdoption(st, V0, V1);
  assert.deepEqual([p1.pinned, p1.followed, p1.followedSeq], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api" }, { "TESTHOST-A:t1": 11 }]);
  assert.deepEqual(strip(vis, u1, p1, "loose"), [["#api(folded)", "TESTHOST-A:m1", "#x(folded)", "#null", "loose"], ["tests"]]);
  // THE RE-ADOPTION FLAP: B re-adopts V0 — a view-order storage event, a remote host's push, a `closed` frame, a host drop: the
  // router re-emits its stored blob and an equal seq is admitted — three times. Memory api against blob web read as
  // the rename B owed, api → web, and each re-adoption carried the pin back and re-stamped; P's strip folded m1 away
  // until P's next adoption carried it forward: six writes over three rounds, the pin moving each time
  for (let i = 0; i < 3; i++) assert.equal(followAdoption(p1, V0, V0), p1, `re-adoption ${i + 1}: no write, no move`);
  assert.equal(followAdoption(p1, V1, V1), p1, "P re-adopting its own blob: the same");
  assert.equal(followAdoption(p1, V0, V1), p1, "B's socket back, adopting V1: web → api is followed already");
  assert.equal(sameTagNames(V0, V0) && sameTagNames(V0, { ...V0, seq: 9, tagOrder: ["x"] }) && !sameTagNames(V0, V1) && !sameTagNames(V0, { ...V0, remoteTags: [] }), true,
    "the names: the same ids under the same names, whatever else the blob changed; a rename, or a host gone, is news");
  // the two halves apart — (a) the name check alone, on blobs with no seq anywhere (an older kernel and an older host)
  const B0 = { active: "all", tags: [g8], remoteTags: [t1("web")], tagOrder: V0.tagOrder }, B1 = { ...B0, remoteTags: [t1("api")] };
  const q1 = followAdoption(st, B0, B1);
  assert.deepEqual([q1.pinned, q1.followed, q1.followedSeq], [[{ sid: "TESTHOST-A:m1", name: "api" }], { "TESTHOST-A:t1": "api" }, undefined], "no seq: no stamp");
  assert.equal(followAdoption(q1, B0, B0), q1, "the re-adoption is caught by the names alone");
  assert.deepEqual(followTagRenames(q1, [], viewTagUnion(B0), B0).pinned, [{ sid: "TESTHOST-A:m1", name: "web" }],
    "(the check run on it regardless, with nothing to order by, carries the pin back — the behavior the name check now guards)");
  // (b) the stamp alone: the check run on B's stale blob stands down on t1, A's seq 10 being older than the stamp 11
  assert.equal(followTagRenames(p1, [], u0, V0), p1, "B's blob is older than the memory's evidence for t1: nothing follows, nothing is owed, nothing is re-stamped");
  // the late INTERMEDIATE frame — the same class with no re-emit: P is on V2 (memory ops @12); B, held V0, adopts V1
  const p2 = followAdoption(p1, V1, V2);
  assert.deepEqual([p2.pinned, p2.followed, p2.followedSeq], [[{ sid: "TESTHOST-A:m1", name: "ops" }], { "TESTHOST-A:t1": "ops" }, { "TESTHOST-A:t1": 12 }]);
  assert.equal(followAdoption(p2, V0, V1), p2, "B's V1 is older than the memory's evidence: web → api is not followed, ops-against-api is no rename owed (before: the pin went to api, P folded m1 away, and B's V2 carried it back)");
  assert.equal(followAdoption(p2, V1, V2), p2, "B's V2: api → ops is followed already");
  assert.equal(followAdoption(p2, V0, V2), p2, "or coalesced, web → ops: followed already");
  // a NEWER blob is news and proceeds: A renames ops → web again (seq 13) while no pane watched (the page closed)
  const V3 = { ...V0, remoteTags: [t1("web", 13)] };
  const back = followAdoption(p2, null, V3);
  assert.deepEqual([back.pinned, back.followed, back.followedSeq], [[{ sid: "TESTHOST-A:m1", name: "web" }], { "TESTHOST-A:t1": "web" }, { "TESTHOST-A:t1": 13 }], "the owed rename is carried and the stamp advances");
  assert.equal(followAdoption(back, V1, V2), back, "a stale pane's V2 (ops @12) against web @13: stood down, not owed in reverse");
  assert.equal(followAdoption(back, V2, V3), back, "B catches up: ops → web is followed already");
  // the stand-down is PER TAG: a stale blob for host A still follows a local rename it carries, and a deleted local tag's
  // memory still goes on it
  const W0 = { ...V0, tags: [g8], seq: 20 }, W1 = { ...V0, tags: [{ ...g8, name: "y" }], seq: 21 };
  const w = followAdoption({ ...p2, followed: { ...p2.followed, g8: "x", g99: "gone" }, followedSeq: { ...p2.followedSeq, g8: 20 } }, W0, W1);
  assert.deepEqual([w.pinned, w.followed, w.followedSeq], [p2.pinned, { "TESTHOST-A:t1": "ops", g8: "y" }, { "TESTHOST-A:t1": 12, g8: 21 }], "A's tag stands (its blob is older); g8's rename is followed and stamped with the local seq; g99 goes");
  // the LOCAL face: the flip was invisible (isPinned matches by id) but the memory flapped and the store was written each time
  const L0 = { active: "all", tags: [{ id: "g1", name: "web", color: "#fff", members: ["m1"] }], tagOrder: ["web", "api", "ops"], seq: 4 };
  const L1 = { ...L0, tags: [{ ...L0.tags[0], name: "api" }], seq: 5 };
  const L2 = { ...L0, tags: [{ ...L0.tags[0], name: "ops" }], seq: 6 };
  const l0 = setPinned(foldAll(parseTabGroups(null), "web", "api", "ops"), secOf(viewTagUnion(L0), "web"), "m1", true);
  const l1 = followAdoption(l0, L0, L1);
  assert.deepEqual([l1.pinned, l1.followed, l1.followedSeq], [[{ sid: "m1", name: "api", id: "g1" }], { g1: "api" }, { g1: 5 }], "stamped with the local store's seq");
  assert.equal(followAdoption(l1, L0, L0), l1, "B re-adopts its stale blob: no write (before: memory api → web, a write per re-emit)");
  assert.equal(followTagRenames(l1, [], viewTagUnion(L0), L0), l1, "…and the stamp alone stands it down: seq 4 is older than 5");
  const l2 = followAdoption(l1, L1, L2);
  assert.equal(followAdoption(l2, L0, L1), l2, "the late intermediate frame: stood down, the pin under ops");
  assert.equal(followAdoption(l2, L2, { ...L2, tagOrder: ["ops", "api", "web"], seq: 7 }), l2, "a local write that renames nothing bumps the seq and changes no name: no news, no write");
  // an entry with NO stamp (a store from before it) stands no blob down: the check runs as it did, and the first blob
  // that moves the entry stamps it — the late-intermediate flap remains for such an entry until then (THE LIMITS)
  const legacy: TabGroupsState = { on: l1.on, collapsed: l1.collapsed, expanded: l1.expanded, pinned: l1.pinned, followed: l1.followed };
  const moved = followTagRenames(legacy, [], viewTagUnion(L0), L0);
  assert.deepEqual([moved.pinned, moved.followed, moved.followedSeq], [[{ sid: "m1", name: "web", id: "g1" }], { g1: "web" }, { g1: 4 }], "unstamped: the older blob is not known to be older, and its carry stamps the entry");
  assert.equal(followAdoption(legacy, L0, L0), legacy, "…while the same blob re-adopted is caught by the names, stamp or none");
  // the stamp persists beside the memory, reads back, and junk drops; a store from before it reads as unstamped
  assert.deepEqual(parseTabGroups('{"followed":{"g1":"api","g2":"x"},"followedSeq":{"g1":5,"g2":0,"g9":3,"z":"7","g3":-1}}').followedSeq, { g1: 5 }, "remembered tags only, positive numbers only");
  assert.equal(parseTabGroups('{"followed":{"g1":"api"}}').followedSeq, undefined, "a store from before the stamp");
  assert.equal(parseTabGroups('{"followedSeq":{"g1":5}}').followedSeq, undefined, "a stamp without a memory is nothing");
  assert.equal("followedSeq" in parseTabGroups('{"followed":{"g1":"api"},"followedSeq":{"g1":0}}'), false, "and an all-zero one is absent, not empty");
  const store = new Map<string, string>();
  const g: any = globalThis;
  const savedLS = g.localStorage;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  try {
    writeTabGroups(l1);
    assert.deepEqual(JSON.parse(store.get(TABGROUPS_KEY)!).followedSeq, { g1: 5 });
    assert.deepEqual(readTabGroups(viewTagUnion(L1)), l1, "round-trips");
    writeTabGroups(l0);
    assert.equal("followedSeq" in JSON.parse(store.get(TABGROUPS_KEY)!), false, "not written when there is none");
  } finally {
    g.localStorage = savedLS;
  }
});

test("executed: a NEWER blob with the held names is news — two panes over one store, adoptBase mirrored: the tag renamed BACK to the name a stale pane holds, after another pane carried the pin from a blob it never saw, has the owed rename carried (before: the no-news shortcut answered first, the pin stayed under the stale name and the tab folded away until the tag's next rename); the shortcut yields only to a stamped entry the blob names otherwise under a seq past the stamp, so the no-news scenarios — the re-adoption flap, the late intermediate frame, a members-only change, a local seq bump, an unstamped host — still write nothing", () => {
  // the two-pane simulator: one shared store, each pane adopting as render.ts adoptBase does
  const store = new Map<string, string>();
  let writes = 0;
  const g: any = globalThis;
  const savedLS = g.localStorage;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); writes++; } };
  class Pane {
    held: any = null;
    adopt(v: any): boolean {
      const prev = this.held;
      this.held = v;
      const unions = viewTagUnion(v);
      const st = readTabGroups(unions);
      const next = followAdoption(st, prev, v, unions);
      if (next !== st) writeTabGroups(next);
      return next !== st;
    }
  }
  const stored = () => { const o = JSON.parse(store.get(TABGROUPS_KEY)!); return [o.pinned, o.followed, o.followedSeq]; };
  const reset = (st: TabGroupsState) => { store.clear(); writeTabGroups(st); writes = 0; };
  const M1 = "TESTHOST-A:m1", vis = [M1, "tests", "loose"];
  const t1 = (name: string, seq?: number) => ({ ...rt("TESTHOST-A", "t1", name, [M1]), ...(seq !== undefined ? { seq } : {}) });
  const g8 = { id: "g8", name: "x", color: "#e0af68", members: ["tests"] };
  const V0 = { active: "all", tags: [g8], remoteTags: [t1("web", 10)], tagOrder: ["web", "api", "ops", "qa", "x"], seq: 4 };
  const V1 = { ...V0, remoteTags: [t1("api", 11)] };   // A renames web → api
  const V2 = { ...V0, remoteTags: [t1("ops", 12)] };   // api → ops
  const V3 = { ...V0, remoteTags: [t1("api", 13)] };   // ops → api: BACK to the name a stale pane holds
  const V4 = { ...V0, remoteTags: [t1("qa", 14)] };    // api → qa
  const base = setPinned(foldAll(parseTabGroups(null), "web", "api", "ops", "qa", "x"), secOf(viewTagUnion(V0), "web"), M1, true);
  try {
    // D — THE FINDING. P and B both adopt V0 and V1 (pin under api @11); B's local socket dies. P adopts V2 (pin → ops @12) and
    // closes. A renames ops → api (V3 @13). B redials and adopts V3 against its held V1: the same names, so the no-news
    // shortcut answered "no news" and returned the state — the memory ops @12 against the blob's api @13 never read as the
    // rename B owed. A remote-only pin has no id: nothing matched it under ops, and the tab folded away.
    reset(base);
    let P = new Pane(), B = new Pane();
    P.adopt(V0); B.adopt(V0);
    assert.equal(P.adopt(V1), true); assert.equal(B.adopt(V1), false, "B: web → api is followed already");
    assert.equal(P.adopt(V2), true);
    assert.deepEqual(stored(), [[{ sid: M1, name: "ops" }], { "TESTHOST-A:t1": "ops" }, { "TESTHOST-A:t1": 12 }], "P carried the pin to ops and stamped A's seq");
    assert.equal(sameTagNames(V1, V3), true, "the shortcut's premise: B's held blob and the new one name every tag alike");
    assert.equal(B.adopt(V3), true, "…and yet the blob is newer than the memory's evidence for t1 (13 > 12) and names it otherwise: the check runs");
    assert.deepEqual(stored(), [[{ sid: M1, name: "api" }], { "TESTHOST-A:t1": "api" }, { "TESTHOST-A:t1": 13 }], "the owed rename ops → api is carried and the stamp advances");
    const u3 = viewTagUnion(V3);
    assert.equal(isPinned(readTabGroups(u3), secOf(u3, "api"), M1), true, "the pin shows under the section the tag makes now");
    assert.deepEqual(strip(vis, u3, readTabGroups(u3), "loose"), [["#api(folded)", M1, "#x(folded)", "#null", "loose"], ["tests"]],
      "the tab stays on the strip under its folded section (before: folded away with no gesture on it)");
    assert.equal(B.adopt(V4), true); assert.deepEqual(stored()[0], [{ sid: M1, name: "qa" }], "the next rename is followed as before");
    // D2 — the same with P alive: P adopts V3 first and carries the pin; B's shortcut then holds (the memory agrees with the blob)
    reset(base);
    P = new Pane(); B = new Pane();
    P.adopt(V0); B.adopt(V0); P.adopt(V1); B.adopt(V1); P.adopt(V2);
    assert.equal(P.adopt(V3), true); assert.equal(B.adopt(V3), false, "nothing owed: P carried it");
    assert.deepEqual(stored(), [[{ sid: M1, name: "api" }], { "TESTHOST-A:t1": "api" }, { "TESTHOST-A:t1": 13 }]);
    // the rule's edges, on the state alone: at the stamp is not past it; an unstamped entry keeps the shortcut
    const ops13: TabGroupsState = { ...base, pinned: [{ sid: M1, name: "ops" }], followed: { "TESTHOST-A:t1": "ops" }, followedSeq: { "TESTHOST-A:t1": 13 } };
    assert.equal(followAdoption(ops13, V1, V3), ops13, "a blob AT the memory's seq is not newer: the same names, no write (the equal-seq path is a stated limit)");
    assert.notEqual(followAdoption({ ...ops13, followedSeq: { "TESTHOST-A:t1": 12 } }, V1, V3), ops13, "past it, the check runs");
    const unstamped: TabGroupsState = { ...base, pinned: [{ sid: M1, name: "ops" }], followed: { "TESTHOST-A:t1": "ops" } };
    assert.equal(followAdoption(unstamped, V1, V3), unstamped, "an unstamped entry orders nothing: the shortcut holds");
    // the LOCAL face of D: the pin carries the id, so it never left the strip — but the memory was left stale (ops @6 against api @7)
    const L0 = { active: "all", tags: [{ id: "g1", name: "web", color: "#fff", members: ["m1"] }], tagOrder: ["web", "api", "ops"], seq: 4 };
    const L1 = { ...L0, tags: [{ ...L0.tags[0], name: "api" }], seq: 5 }, L2 = { ...L0, tags: [{ ...L0.tags[0], name: "ops" }], seq: 6 };
    const L3 = { ...L0, tags: [{ ...L0.tags[0], name: "api" }], seq: 7 };
    reset(setPinned(foldAll(parseTabGroups(null), "web", "api", "ops"), secOf(viewTagUnion(L0), "web"), "m1", true));
    P = new Pane(); B = new Pane();
    P.adopt(L0); B.adopt(L0); P.adopt(L1); B.adopt(L1); P.adopt(L2);
    assert.equal(B.adopt(L3), true);
    assert.deepEqual(stored(), [[{ sid: "m1", name: "api", id: "g1" }], { g1: "api" }, { g1: 7 }], "the memory follows the tag");
    // UNCHANGED — S1, the re-adoption flap: B's socket is dead; it re-adopts V0 on every router re-emit while P follows
    reset(base);
    P = new Pane(); B = new Pane();
    P.adopt(V0); B.adopt(V0); P.adopt(V1);
    const w1 = writes;
    assert.deepEqual([B.adopt(V0), B.adopt(V0), B.adopt(V0)], [false, false, false], "memory api @11 against the re-emitted web @10: not newer, no carry-back");
    P.adopt(V2);
    assert.deepEqual([B.adopt(V0), B.adopt(V0)], [false, false]);
    assert.equal(B.adopt(V2), false, "B catches up: followed already");
    assert.equal(writes - w1, 1, "P's one follow is the only write");
    assert.deepEqual(stored(), [[{ sid: M1, name: "ops" }], { "TESTHOST-A:t1": "ops" }, { "TESTHOST-A:t1": 12 }]);
    // S7, the late intermediate frame: P on V2; B, held V0, adopts V1 late, then V2
    reset(base);
    P = new Pane(); B = new Pane();
    P.adopt(V0); B.adopt(V0); P.adopt(V1); P.adopt(V2);
    const w7 = writes;
    assert.deepEqual([B.adopt(V1), B.adopt(V2), writes - w7], [false, false, 0], "stood down, then followed already");
    // E, members changed and names not: a tab joins t1 (A @13, a local lens write @5) — no news, the stamp kept
    reset(base);
    P = new Pane();
    P.adopt(V0); P.adopt(V1); P.adopt(V2);
    const wE = writes;
    assert.equal(P.adopt({ ...V0, remoteTags: [{ ...t1("ops", 13), members: [M1, "TESTHOST-A:m2"] }], seq: 5 }), false);
    assert.deepEqual([writes - wE, stored()[2]], [0, { "TESTHOST-A:t1": 12 }]);
    // a local seq bump on a stale pane's blob (the held names, A still @10) against the memory ops @12: A's seq is not past the stamp
    reset(base);
    P = new Pane(); B = new Pane();
    P.adopt(V0); B.adopt(V0); P.adopt(V1); P.adopt(V2);
    const wH = writes;
    assert.deepEqual([B.adopt({ ...V0, seq: 9 }), writes - wH, stored()[0]], [false, 0, [{ sid: M1, name: "ops" }]]);
    // an UNSTAMPED host (a kernel from before the stamp): the memory has no stamp, so no blob is newer than it — B's stale
    // blob, re-emitted or under a bumped local seq, keeps the shortcut and carries nothing back (THE LIMITS' unstamped case)
    const U = (name: string) => ({ ...V0, remoteTags: [t1(name)] });
    reset(base);
    P = new Pane(); B = new Pane();
    P.adopt(U("web")); B.adopt(U("web")); P.adopt(U("api"));
    const wU = writes;
    assert.deepEqual([B.adopt(U("web")), B.adopt({ ...U("web"), seq: 9 }), writes - wU], [false, false, 0]);
    assert.deepEqual(stored(), [[{ sid: M1, name: "api" }], { "TESTHOST-A:t1": "api" }, undefined]);
  } finally {
    g.localStorage = savedLS;
  }
});

test("executed: the owed rename against a MIXED pin — a pin under a mixed section turned off is re-applied by neither adoption order; the remote half unpinned after a watched split stands through the remote tag's next owed rename with the local-id entry untouched; and a local-id entry whose name went stale is not the pin a watched rename of a NEWER same-named local tag moves (the `!x.local` clause)", () => {
  const t1 = (name: string) => rt("TESTHOST-A", "t1", name, ["TESTHOST-A:m1"]);
  const g9 = { id: "g9", name: "api", color: "#fff", members: ["TESTHOST-A:m1"] };
  const V0 = { active: "all", tags: [g9], remoteTags: [t1("web")], tagOrder: ["api", "web", "ops", "qa"], seq: 4 };
  const V1 = { ...V0, remoteTags: [t1("api")] }, V2 = { ...V0, remoteTags: [t1("ops")] }, V3 = { ...V0, remoteTags: [t1("qa")] };
  const u1 = viewTagUnion(V1), u2 = viewTagUnion(V2), u3 = viewTagUnion(V3);
  // A's web → api joins the local api: the section is mixed, and a pin made there carries g9's id
  let st = foldAll(parseTabGroups(null), "api", "web", "ops", "qa");
  st = setPinned(st, secOf(u1, "api"), "TESTHOST-A:m1", true);
  assert.deepEqual(st.pinned, [{ sid: "TESTHOST-A:m1", name: "api", id: "g9" }]);
  const st1 = followTagRenames(st, tagRenames(V0, V1), u1);
  assert.deepEqual(st1.followed, { "TESTHOST-A:t1": "api" });
  const rB = tagRenames(V0, V2), rP = tagRenames(V1, V2);
  // the mixed pin turned off; then A renames api → ops (owed for B, watched for P): nothing is re-applied, either order
  const off = setPinned(st1, secOf(u1, "api"), "TESTHOST-A:m1", false);
  assert.deepEqual(off.pinned, []);
  const offB = followTagRenames(off, rB, u2), offP = followTagRenames(off, rP, u2);
  assert.deepEqual([offB.pinned, offB.followed], [[], { "TESTHOST-A:t1": "ops" }], "B: the memory is re-stamped, no pin appears");
  assert.equal(followTagRenames(offB, rP, u2), offB, "P stands down");
  assert.deepEqual([offP.pinned, offP.followed], [[], { "TESTHOST-A:t1": "ops" }]);
  assert.equal(followTagRenames(offP, rB, u2), offP, "B stands down");
  // left on, the owed rename splits the pin as the watched one does: the local-id entry stays, the ops half is added
  const onB = followTagRenames(st1, rB, u2), onP = followTagRenames(st1, rP, u2);
  assert.deepEqual(onP.pinned, [{ sid: "TESTHOST-A:m1", name: "api", id: "g9" }, { sid: "TESTHOST-A:m1", name: "ops" }]);
  assert.deepEqual(onB.pinned, onP.pinned, "the owed carry and the watched one agree");
  // the user unpins the ops half; A then renames ops → qa while the page is closed (owed on reopen): the local-id entry
  // stands and nothing is re-applied
  const half = setPinned(onP, secOf(u2, "ops"), "TESTHOST-A:m1", false);
  assert.deepEqual(half.pinned, [{ sid: "TESTHOST-A:m1", name: "api", id: "g9" }]);
  const qa = followTagRenames(half, tagRenames(null, V3), u3);
  assert.deepEqual([qa.pinned, qa.followed], [[{ sid: "TESTHOST-A:m1", name: "api", id: "g9" }], { "TESTHOST-A:t1": "qa" }], "the memory is re-stamped; the pin the user kept is not touched");
  // `!x.local`: a local-id entry {m1, api, g9} whose name went stale — g9 renamed to ops with no memory of it, so the
  // reopen frame names no rename and the entry is found by id (prunePinned keeps it) — is not moved by a WATCHED rename
  // of a NEWER local tag g10 that took the name api and holds m1: two local tags never share a name, so g10's api → qa
  // is not g9's pin's to follow. Without the clause the entry gained a {m1, qa, g10} half the user never made
  const L = (a: string, b: string) => ({ active: "all", tags: [{ id: "g9", name: a, color: "#fff", members: ["m1"] }, { id: "g10", name: b, color: "#000", members: ["m1"] }], tagOrder: ["api", "ops", "qa"], seq: 7 });
  const P0 = { active: "all", tags: [{ id: "g9", name: "api", color: "#fff", members: ["m1"] }], tagOrder: ["api", "ops", "qa"], seq: 5 };
  const c = setPinned(foldAll(parseTabGroups(null), "api", "ops", "qa"), secOf(viewTagUnion(P0), "api"), "m1", true);
  const X0 = L("ops", "api");
  const reopened = followTagRenames(c, tagRenames(null, X0), viewTagUnion(X0));
  assert.equal(reopened, c, "no memory of g9: the reopen frame moves nothing (THE LIMITS' local face — the id still finds the section)");
  const pruned = prunePinned(reopened, viewTagUnion(X0), new Set(["m1"]), HOSTS);
  assert.deepEqual(pruned.pinned, [{ sid: "m1", name: "api", id: "g9" }], "kept: g9 holds m1, found by id");
  const X1 = L("ops", "qa");
  assert.deepEqual(tagRenames(X0, X1), [{ id: "g10", from: "api", to: "qa", local: true, members: ["m1"] }]);
  const w = followTagRenames(pruned, tagRenames(X0, X1), viewTagUnion(X1));
  assert.deepEqual([w.pinned, w.followed], [[{ sid: "m1", name: "api", id: "g9" }], { g10: "qa" }], "g10's rename is remembered and moves nothing: the entry is g9's");
});

test("executed: a REMOTE host's rename of a MIXED section — the entry carries the local id, and the remote tag's new name gains a half while the local tag holds the tab under the old, as a local rename keeps the old-name half: pinned under either drag order, and when the new name already sits ahead in tagOrder the tab's home moves on the frame and stays on the strip", () => {
  // local infra (g9) and host A's infra both hold A:m1; the user pins A:m1 under the mixed section
  const A = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m1"]);
  const g9 = { id: "g9", name: "infra", color: "#4EC9B0", members: ["web", "TESTHOST-A:m1"] };
  const mixedV = { active: "all", tags: [g9], remoteTags: [A] };
  const mixed = viewTagUnion(mixedV);
  let st = foldAll(parseTabGroups(null), "infra", "ops");
  st = setPinned(st, secOf(mixed, "infra"), "TESTHOST-A:m1", true);
  assert.deepEqual(st.pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }]);
  const vis = ["web", "TESTHOST-A:m1", "loose"];
  // host A renames its tag to ops, the client watching: the entry stays (g9 still holds the tab under infra)
  // and the ops half is added — the mirror of the local rename's kept old-name half. An earlier cut left the entry
  // alone here, and A:m1 folded away under ops once ops was its home.
  const renamedV = { active: "all", tags: [g9], remoteTags: [{ ...A, name: "ops" }] };
  const ru = viewTagUnion(renamedV);
  const st2 = followTagRenames(st, tagRenames(mixedV, renamedV), ru);
  assert.deepEqual(st2.pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }, { sid: "TESTHOST-A:m1", name: "ops" }], "the kept entry, and the remote half — no id: the tag is A's");
  assert.deepEqual(strip(vis, ru, st2, "loose"), [["#infra(folded)", "TESTHOST-A:m1", "#ops(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]], "default order: shown under infra AND ops (every-tag rule), pinned in both");
  const opsFirst = viewTagUnion({ ...renamedV, tagOrder: ["ops"] });
  assert.deepEqual(strip(vis, opsFirst, st2, "loose"), [["#ops(folded)", "TESTHOST-A:m1", "#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["web"]], "ops dragged first: home ops, pinned there too");
  assert.equal(isPinned(st2, secOf(opsFirst, "ops"), "TESTHOST-A:m1"), true);
  assert.equal(prunePinned(st2, opsFirst, new Set(vis), HOSTS), st2, "both halves hold the tab: both stand");
  // the same start with the LOCAL tag renamed instead — the mirror, as before
  const localRenamedV = { active: "all", tags: [{ ...g9, name: "ops" }], remoteTags: [A] };
  assert.deepEqual(followTagRenames(st, tagRenames(mixedV, localRenamedV), viewTagUnion(localRenamedV)).pinned, [{ sid: "TESTHOST-A:m1", name: "ops", id: "g9" }, { sid: "TESTHOST-A:m1", name: "infra" }]);
  // no gesture needed when the new name already sits ahead: a local ops (g3) precedes infra, both folded; A
  // renames infra→ops and A:m1's home moves to the ops union on that very frame — and it is pinned there
  const g3 = { id: "g3", name: "ops", color: "#e0af68", members: ["x"] };
  const beforeV = { active: "all", tags: [g3, g9], remoteTags: [A] };
  const afterV = { active: "all", tags: [g3, g9], remoteTags: [{ ...A, name: "ops" }] };
  const au = viewTagUnion(afterV);
  const vis2 = ["x", "web", "TESTHOST-A:m1", "loose"];
  assert.deepEqual(strip(vis2, viewTagUnion(beforeV), st, "loose"), [["#ops(folded)", "#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["x", "web"]]);
  const st3 = followTagRenames(st, tagRenames(beforeV, afterV), au);
  assert.deepEqual(st3.pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }, { sid: "TESTHOST-A:m1", name: "ops" }]);
  assert.deepEqual(strip(vis2, au, st3, "loose"), [["#ops(folded)", "TESTHOST-A:m1", "#infra(folded)", "TESTHOST-A:m1", "#null", "loose"], ["x", "web"]], "home ops now — the union g3 and A's tag make — and on the strip");
  assert.equal(isPinned(st3, secOf(au, "ops"), "TESTHOST-A:m1"), true, "the section's local id is g3, the entry's name is ops: matched by name");
});

test("executed: TWO same-named tags both holding the tab, renamed in ONE frame — every matching rename is followed, one entry per new name, so the one-frame result equals the two-frames result (a remote-only pin, and one carrying a local id)", () => {
  // hosts A and B both tag one of ours (web) infra; the user pins web under the remote-only section
  const A = rt("TESTHOST-A", "t1", "infra", ["web"]), B = rt("TESTHOST-B", "t2", "infra", ["web"]);
  const V0 = { active: "all", tags: [], remoteTags: [A, B] };
  let st = foldAll(parseTabGroups(null), "infra", "ops", "platform");
  st = setPinned(st, secOf(viewTagUnion(V0), "infra"), "web", true);
  assert.deepEqual(st.pinned, [{ sid: "web", name: "infra" }]);
  const vis = ["web", "api", "loose"];
  // remote tags refresh once a minute per host, and a reconnect coalesces every frame since the held blob:
  // A→ops and B→platform ride ONE frame
  const V2 = { active: "all", tags: [], remoteTags: [{ ...A, name: "ops" }, { ...B, name: "platform" }] };
  const u2 = viewTagUnion(V2);
  const once = followTagRenames(st, tagRenames(V0, V2), u2);
  assert.deepEqual(once.pinned, [{ sid: "web", name: "ops" }, { sid: "web", name: "platform" }], "both halves — an earlier cut followed only the first rename, and web folded away under platform");
  const V1 = { active: "all", tags: [], remoteTags: [{ ...A, name: "ops" }, B] };
  const twice = followTagRenames(followTagRenames(st, tagRenames(V0, V1), viewTagUnion(V1)), tagRenames(V1, V2), u2);
  assert.deepEqual(twice.pinned, once.pinned, "the same two renames a frame apart: the same entries");
  assert.deepEqual(once.followed, twice.followed);
  assert.deepEqual(strip(vis, viewTagUnion({ ...V2, tagOrder: ["platform"] }), once, "loose"), [["#platform(folded)", "web", "#ops(folded)", "web", "#null", "api", "loose"], []], "platform first: pinned there");
  assert.deepEqual(strip(vis, u2, once, "loose"), [["#ops(folded)", "web", "#platform(folded)", "web", "#null", "api", "loose"], []], "ops first: pinned there");
  // the id face: local g7 infra and A's infra both hold web, both renamed in one frame
  const g7 = (name: string) => ({ id: "g7", name, color: "#4EC9B0", members: ["web", "api"] });
  const M0 = { active: "all", tags: [g7("infra")], remoteTags: [A] };
  const M2 = { active: "all", tags: [g7("ops")], remoteTags: [{ ...A, name: "platform" }] };
  const m2 = viewTagUnion(M2);
  const pinned = setPinned(foldAll(parseTabGroups(null), "infra", "ops", "platform"), secOf(viewTagUnion(M0), "infra"), "web", true);
  const onceM = followTagRenames(pinned, tagRenames(M0, M2), m2);
  assert.deepEqual(onceM.pinned, [{ sid: "web", name: "ops", id: "g7" }, { sid: "web", name: "platform" }], "its own tag's rename by id, A's by the old name — one entry each; no infra half, nothing is named infra now");
  const M1 = { active: "all", tags: [g7("ops")], remoteTags: [A] };
  assert.deepEqual(followTagRenames(followTagRenames(pinned, tagRenames(M0, M1), viewTagUnion(M1)), tagRenames(M1, M2), m2).pinned, onceM.pinned, "a frame apart: the same");
  assert.deepEqual(strip(vis, viewTagUnion({ ...M2, tagOrder: ["platform"] }), onceM, "loose"), [["#platform(folded)", "web", "#ops(folded)", "web", "#null", "loose"], ["api"]]);
});

test("executed: tagRenames — a tag that keeps its id under a new name, local or a remote host's; a new tag, a deleted one, an unchanged name and a missing blob yield none; followTagRenames rewrites by id whatever the stored name, collapses duplicates, and returns the same state when no rename is new", () => {
  const A = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m1"]);
  const prev = { active: "all", tags: [{ id: "g1", name: "qa", color: "", members: ["tests"] }, { id: "g2", name: "infra", color: "", members: ["web"] }], remoteTags: [A] };
  const next = { active: "all", tags: [{ id: "g1", name: "qa", color: "", members: ["tests"] }, { id: "g2", name: "platform", color: "", members: ["web", "api"] }, { id: "g5", name: "infra", color: "", members: [] }],
                 remoteTags: [{ ...A, name: "ops" }] };
  assert.deepEqual(tagRenames(prev, next), [
    { id: "g2", from: "infra", to: "platform", local: true, members: ["web", "api"] },
    { id: "TESTHOST-A:t1", from: "infra", to: "ops", local: false, members: ["TESTHOST-A:m1"] },
  ], "by id: g5 is new (no rename), qa unchanged, and the members are the NEXT blob's");
  assert.deepEqual(tagRenames(prev, { ...prev, tags: prev.tags.slice(0, 1) }), [], "a deleted tag is no rename");
  assert.deepEqual(tagRenames(prev, prev), []);
  assert.deepEqual(tagRenames(null, next), []); assert.deepEqual(tagRenames(prev, null), []); assert.deepEqual(tagRenames(undefined, undefined), []);
  assert.deepEqual(tagRenames({ tags: [{ id: "g2" }] }, { tags: [{ id: "g2", name: "x" }] }), [{ id: "g2", from: "tag", to: "x", local: true, members: [] }], "a nameless tag is the union's default name");
  // followTagRenames: an entry with the id follows by id even under a stale name (a rename this client missed)
  const unions = viewTagUnion(next);
  const stale = { ...parseTabGroups(null), pinned: [{ sid: "web", name: "old", id: "g2" }] };
  assert.deepEqual(followTagRenames(stale, tagRenames(prev, next), unions).pinned, [{ sid: "web", name: "platform", id: "g2" }]);
  // duplicates collapse: a name-only entry and an id entry for the same tab and tag become one
  const two = { ...parseTabGroups(null), pinned: [{ sid: "web", name: "infra" }, { sid: "web", name: "infra", id: "g2" }] };
  assert.deepEqual(followTagRenames(two, tagRenames(prev, next), unions).pinned, [{ sid: "web", name: "platform", id: "g2" }], "no infra half: g5's infra holds nothing");
  // nothing named: the pins stand, the renames are remembered (once per browser — see the memory's test);
  // no renames at all, or none new: the same object; the fold state rides through a rewrite untouched
  const other = { ...foldAll(parseTabGroups(null), "qa"), pinned: [{ sid: "tests", name: "qa", id: "g1" }] };
  const kept = followTagRenames(other, tagRenames(prev, next), unions);
  assert.deepEqual([kept.pinned, kept.followed], [other.pinned, { g2: "platform", "TESTHOST-A:t1": "ops" }]);
  assert.equal(followTagRenames(kept, tagRenames(prev, next), unions), kept);
  assert.equal(followTagRenames(stale, [], unions), stale);
  const moved = followTagRenames({ ...stale, collapsed: ["qa"], expanded: ["archived"], on: false }, tagRenames(prev, next), unions);
  assert.deepEqual([moved.on, moved.collapsed, moved.expanded], [false, ["qa"], ["archived"]]);
});

test("executed: prunePinned drops the pins of tags and sessions that no longer exist, and of members moved out — on the pin row's write, never per render", () => {
  const unions = viewTagUnion(VP);   // qa g1, infra g2, empty g3, archived g4, remotepool (remote-only)
  const st = { ...parseTabGroups(null), pinned: [
    { sid: "old2", name: "archived", id: "g4" },              // stands: archived, a known session
    { sid: "web", name: "infra" },                            // stands: a union's name (one with a local id too)
    { sid: "TESTHOST-A:m1", name: "remotepool" },             // stands: a remote-only union, by name
    { sid: "old3", name: "was-archived", id: "g4" },          // stands: a stale name, a live id — the tag renamed while no client watched
    { sid: "old1", name: "gone", id: "g9" },                  // a deleted tag
    { sid: "closed1", name: "archived", id: "g4" },           // a closed session
    { sid: "TESTHOST-A:m1", name: "TESTHOST-A:r1" },          // an older store's pin under a remote id, migrated as a name: no section answers to it
    { sid: "web", name: "archived", id: "g4" },               // a tag that exists and a session that exists — but web is not archived's member (moved out)
    { sid: "old1", name: "qa" },                              // the same by name
  ] };
  const known = new Set(["web", "api", "tests", "old1", "old2", "old3", "TESTHOST-A:m1", "loose"]);
  const pruned = prunePinned(st, unions, known, HOSTS);
  assert.deepEqual(pruned.pinned, [{ sid: "old2", name: "archived", id: "g4" }, { sid: "web", name: "infra" }, { sid: "TESTHOST-A:m1", name: "remotepool" }, { sid: "old3", name: "was-archived", id: "g4" }],
    "judged per entry: the session is a known tab AND a member of a union the entry names by name or id — a live tag and a live session that no longer meet is a dead entry");
  assert.deepEqual([pruned.on, pruned.collapsed, pruned.expanded], [st.on, st.collapsed, st.expanded], "the fold state rides through untouched");
  assert.equal(prunePinned(pruned, unions, known, HOSTS), pruned, "nothing to drop: the same object");
  // render.ts: the pin row's write is the ONE prune site, over every tab the strip knows (a view-hidden
  // session still exists); the plan reads pins and never rewrites them — a prune per render could act
  // on a transient frame (a views blob mid-write, a host's tags not yet arrived) and put a tab away
  assert.equal(RENDER.split("prunePinned(").length - 1, 1, "one call site");
  assert.match(RENDER, /writeTabGroups\(prunePinned\(setPinned\(tabGroups\(\), sec, id, !on\), unionFor\(\), knownTabIds\(\), reachableHosts\(\)\)\); build\(\);/,
    "the row SETS the state it rendered (!on) — a toggle would flip whatever a re-render stored between the render and the click");
  assert.match(RENDER, /function knownTabIds\(\): Set<string> \{ return new Set<string>\(\[\.\.\.order, \.\.\.tabMeta\.keys\(\)\]\); \}/);
  const TG = ui("webview", "tab-groups.ts");
  const plan = TG.slice(TG.indexOf("export function planStrip("), TG.indexOf("export function reorderTagOrder("));
  assert.ok(!plan.includes("prunePinned") && !plan.includes("followTagRenames"), "the plan never prunes or rewrites");
});

test("executed: a host DETACHED, DOWN, or PENDING (attached and up, its tab list not yet in this pane) takes its sessions out of the strip's knowledge, and the pin row's prune leaves their entries untouched — remote-only and mixed — so the pins render again when the host's tabs arrive; a reachable host's closed session and a local one still prune; LIMIT: a local tab's pin under a section only the host's tag made", () => {
  // host A's infra holds A:m1, as does local infra (g9); A's review holds web (the host tagged one of ours);
  // host B's pool holds B:m1; local qa (g1) holds tests
  const A = rt("TESTHOST-A", "t1", "infra", ["TESTHOST-A:m1"]);
  const AR = rt("TESTHOST-A", "t3", "review", ["web"]);
  const B = rt("TESTHOST-B", "t2", "pool", ["TESTHOST-B:m1"]);
  const local = [{ id: "g1", name: "qa", color: "#DD42FF", members: ["tests"] }, { id: "g9", name: "infra", color: "#4EC9B0", members: ["TESTHOST-A:m1"] }];
  const allV = { active: "all", tags: local, remoteTags: [A, AR, B] };
  const all = viewTagUnion(allV);
  const vis = ["tests", "web", "TESTHOST-A:m1", "TESTHOST-B:m1", "loose"];
  const known = new Set(vis);
  let st = foldAll(parseTabGroups(null), "qa", "infra", "review", "pool");
  st = setPinned(st, secOf(all, "infra"), "TESTHOST-A:m1", true);   // mixed: name and local id
  st = setPinned(st, secOf(all, "pool"), "TESTHOST-B:m1", true);    // remote-only: the name alone
  st = setPinned(st, secOf(all, "review"), "web", true);            // a local tab under a remote-only section
  assert.deepEqual(st.pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }, { sid: "TESTHOST-B:m1", name: "pool" }, { sid: "web", name: "review" }]);
  assert.deepEqual(strip(vis, all, st, "loose"), [["#qa(folded)", "#infra(folded)", "TESTHOST-A:m1", "#review(folded)", "web", "#pool(folded)", "TESTHOST-B:m1", "#null", "loose"], ["tests"]]);
  assert.equal(prunePinned(st, all, known, HOSTS), st, "every host reachable, every pin held: nothing to drop");
  // A DETACHES: its sessions leave the strip (closeRemote dismisses each), its tags leave the blob, and it
  // leaves the host list; the local g9 still lists A's member. Hours later the user toggles tests' pin under qa.
  const detachedV = { active: "all", tags: local, remoteTags: [B] };
  const detached = viewTagUnion(detachedV);
  const knownD = new Set(["tests", "web", "TESTHOST-B:m1", "loose"]);
  const pruned = prunePinned(togglePinned(st, secOf(detached, "qa"), "tests"), detached, knownD, new Set(["TESTHOST-B"]));
  assert.deepEqual(pruned.pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }, { sid: "TESTHOST-B:m1", name: "pool" }, { sid: "tests", name: "qa", id: "g1" }],
    "A's member's entry stands untouched — its host is in no list, so it is not judged (an earlier cut dropped it); B's is judged and stands; web's under review is THE LIMIT: a local sid, judged, and no union named review holds it");
  assert.deepEqual(strip(["tests", "web", "TESTHOST-B:m1", "loose"], detached, pruned, "loose"), [["#qa(folded)", "tests", "#pool(folded)", "TESTHOST-B:m1", "#null", "web", "loose"], []], "meanwhile: web, in no tag, trails");
  // A REATTACHES: its tabs and tags are back, and A:m1 renders pinned with no gesture on it
  assert.deepEqual(strip(vis, all, pruned, "loose"), [["#qa(folded)", "tests", "#infra(folded)", "TESTHOST-A:m1", "#review(folded)", "#pool(folded)", "TESTHOST-B:m1", "#null", "loose"], ["web"]],
    "A's member pinned under infra as before; web folds under review — the limit, pinned again by hand");
  assert.equal(prunePinned(pruned, all, known, HOSTS), pruned, "judged for real on the next pin write: every entry stands");
  // A DOWN (attached, tunnel not up) on a page loaded during the outage: its tabs never arrived, while its cached
  // tags still ride the blob (the kernel keeps a down host's) — not judged either
  assert.equal(prunePinned(st, all, knownD, new Set(["TESTHOST-B"])), st, "hosts = attached less down: nothing dropped");
  // …and on a page that was open when A went down its tabs stay known and the cached tags hold them: judged, and kept
  assert.equal(prunePinned(st, all, known, new Set(["TESTHOST-B"])), st);
  // a REACHABLE host's session the host reports gone IS judged and goes, as a local closed session does
  assert.deepEqual(prunePinned(st, all, new Set(["tests", "web", "TESTHOST-A:m1", "loose"]), HOSTS).pinned, [{ sid: "TESTHOST-A:m1", name: "infra", id: "g9" }, { sid: "web", name: "review" }], "B:m1 closed on a reachable B");
  assert.deepEqual(prunePinned(st, all, new Set(["tests", "TESTHOST-A:m1", "TESTHOST-B:m1", "loose"]), HOSTS).pinned.map((p) => p.sid), ["TESTHOST-A:m1", "TESTHOST-B:m1"], "web closed: a local sid is always judged");
  // the hosts the pin row's write passes (render.ts reachableHosts → reachableFrom): the router's attached hosts
  // less the down ones, less the ones whose tab list has not reached THIS pane (its pending set): an attached, up
  // host in that window lists sessions none of which is a known tab here yet, so judged, every pin on them would
  // drop
  const lists = { hosts: () => ["TESTHOST-A", "TESTHOST-B", "TESTHOST-C"], down: () => ["TESTHOST-C"], pending: () => ["TESTHOST-B"] };
  assert.deepEqual([...reachableFrom(lists)], ["TESTHOST-A"], "attached, less down, less pending");
  assert.deepEqual([...reachableFrom({ hosts: () => ["TESTHOST-A"] })], ["TESTHOST-A"], "a router without the other lists: attached is reachable");
  assert.deepEqual([...reachableFrom(undefined)], [], "no router (a single-kernel page): no remote host");
  // the page reloaded with B attached and up; the local tab list landed, B's is a relay hop behind, and the user
  // toggles tests' pin in that window: B's entry is not judged — B's tabs are not here to judge it by
  const knownEarly = new Set(["tests", "web", "TESTHOST-A:m1", "loose"]);
  const early = reachableFrom({ hosts: () => ["TESTHOST-A", "TESTHOST-B"], down: () => [], pending: () => ["TESTHOST-B"] });
  assert.deepEqual(prunePinned(togglePinned(st, secOf(all, "qa"), "tests"), all, knownEarly, early).pinned.map((p) => p.sid), ["TESTHOST-A:m1", "TESTHOST-B:m1", "web", "tests"], "B:m1's pin waits for B's tabs");
  assert.deepEqual(prunePinned(togglePinned(st, secOf(all, "qa"), "tests"), all, knownEarly, HOSTS).pinned.map((p) => p.sid), ["TESTHOST-A:m1", "web", "tests"], "(judged by attached-and-up alone, it dropped)");
  // …and the same window on A's REATTACH, whose entries the detach left standing: A:m1 not yet known, A pending
  const knownReattach = new Set(["tests", "web", "TESTHOST-B:m1", "loose"]);
  const reattach = reachableFrom({ hosts: () => ["TESTHOST-A", "TESTHOST-B"], down: () => [], pending: () => ["TESTHOST-A"] });
  assert.equal(prunePinned(pruned, all, knownReattach, reattach), pruned, "A's entry stands through the handshake");
  assert.deepEqual(prunePinned(pruned, all, knownReattach, HOSTS).pinned.map((p) => p.sid), ["TESTHOST-B:m1", "tests"], "(judged before its tabs arrived, it dropped — and folded away when they did)");
  // render.ts reads the router's lists through the helper; federation.ts publishes all three on __rompFed
  const RH = RENDER.slice(RENDER.indexOf("function reachableHosts("), RENDER.indexOf("function tabGroups("));
  assert.match(RH, /^function reachableHosts\(\): Set<string> \{ return reachableFrom\(\(window as any\)\.__rompFed\); \}/m);
  const FED = ui("webview", "federation.ts");
  assert.match(FED, /hosts: \(\) => this\.hostSeq\.filter\(\(h\) => h !== LOCAL\),/);
  assert.match(FED, /down: \(\) => \[\.\.\.this\.downHosts\],/);
  assert.match(FED, /pending: \(\) => this\.pendingFor\(\),/, "the pending set the shell's network panel reads, published for the panes too");
});

test("executed: the pin persists with the fold state under romp:tabgroups, survives the fold writes, junk entries drop, and the store's earlier shape migrates on read; unpin hides the tab again", () => {
  const d = parseTabGroups(null);
  const on = togglePinned(d, ARCH, "old2");
  assert.deepEqual(on, { on: true, collapsed: [], expanded: [], pinned: [{ sid: "old2", name: "archived", id: "g4" }] });
  assert.deepEqual(setSectionCollapsed(on, "infra", true).pinned, on.pinned, "a fold write carries the pins through");
  assert.deepEqual(toggleSectionCollapsed(on, "archived").pinned, on.pinned);
  const off = togglePinned(on, ARCH, "old2");
  assert.deepEqual(off, d, "toggling back drops the entry — the menu's one way off");
  assert.deepEqual(togglePinned(off, ARCH, "old2"), on, "and on again");
  assert.deepEqual(setPinned(setPinned(d, ARCH, "old2", true), ARCH, "old2", true).pinned, [{ sid: "old2", name: "archived", id: "g4" }], "set on twice: one entry");
  assert.deepEqual(parseTabGroups('{"pinned":[{"sid":"old2","name":"archived","id":"g4"},{"sid":"x","name":3},"junk",null,{"name":"qa"},{"sid":"old1"},{"sid":"old3","name":"archived","id":7},{"sid":"a","name":"b","extra":1}]}').pinned,
    [{ sid: "old2", name: "archived", id: "g4" }, { sid: "old3", name: "archived" }, { sid: "a", name: "b" }],
    "a string sid and name survive a read, a string id rides along, anything else drops (a non-string id drops alone; unknown fields never ride)");
  assert.deepEqual(parseTabGroups('{"pinned":"nope"}').pinned, []);
  // THE MIGRATION: the branch's earlier entries were {tag, sid}, `tag` a local id or a union's name — read
  // against the current unions, a local id becomes the id beside its union's name, anything else a name
  const unions = viewTagUnion(VP);
  const old = '{"collapsed":["infra"],"pinned":[{"tag":"g4","sid":"old2"},{"tag":"remotepool","sid":"TESTHOST-A:m1"},{"tag":"g9","sid":"old1"},{"tag":"infra","sid":"web"},{"tag":5,"sid":"x"}]}';
  const migrated = parseTabGroups(old, unions);
  assert.deepEqual(migrated.pinned, [{ sid: "old2", name: "archived", id: "g4" }, { sid: "TESTHOST-A:m1", name: "remotepool" }, { sid: "old1", name: "g9" }, { sid: "web", name: "infra" }],
    "g4 is archived's id; remotepool and infra are names; g9 names no current tag (a name that matches nothing, the prune's to drop); a non-string tag drops");
  assert.deepEqual(migrated.collapsed, ["infra"], "the fold state reads as before");
  assert.deepEqual(strip(["web", "old1", "old2", "old3", "loose"], unions, migrated, "loose")[0], ["#infra(folded)", "web", "#archived(folded)", "old2", "#null", "loose"], "the migrated pins render");
  assert.deepEqual(prunePinned(migrated, unions, new Set(["web", "old1", "old2", "old3", "TESTHOST-A:m1", "loose"]), HOSTS).pinned.map((p) => p.sid), ["old2", "TESTHOST-A:m1", "web"], "the dead one goes on the next pin write");
  assert.deepEqual(parseTabGroups(old).pinned[0], { sid: "old2", name: "g4" }, "with no unions (a read before the first frame) a local id can only be kept as a name — the write sites pass the unions they have");
  // round trip through the store, the unions given
  const store = new Map<string, string>();
  const g: any = globalThis;
  const savedLS = g.localStorage;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  try {
    writeTabGroups(on);
    assert.deepEqual(readTabGroups(unions).pinned, [{ sid: "old2", name: "archived", id: "g4" }]);
    assert.equal(JSON.parse(store.get(TABGROUPS_KEY)!).pinned[0].tag, undefined, "written in the new shape");
    store.set(TABGROUPS_KEY, old);
    assert.deepEqual(readTabGroups(unions).pinned[0], { sid: "old2", name: "archived", id: "g4" }, "an old store reads migrated");
    writeTabGroups(off);
    assert.deepEqual(readTabGroups(unions).pinned, [], "unpinned: gone from the store");
  } finally {
    g.localStorage = savedLS;
  }
  // unpin → the plan hides the tab again
  const shown = planStrip(["web", "old1", "old2"], unions, on, "web", false);
  assert.ok(shown.items.some((i) => "id" in i && i.id === "old2"), "pinned: on the strip");
  const hidden = planStrip(["web", "old1", "old2"], unions, off, "web", false);
  assert.ok(!hidden.items.some((i) => "id" in i && i.id === "old2"), "unpinned: folded away again");
  assert.deepEqual([...hidden.folded], ["old1", "old2"]);
});

test("the toggle is a row in the tab menu's Tags flyout beside the Move-to rows: the home tag's chip, ✓ when on, per-section copy, the fold's own write and render path; the views adoption carries the pins across a rename (source pins)", () => {
  const fly = RENDER.slice(RENDER.indexOf('const sub = el("div", "ctx-menu ctx-sub ctx-sub-tags");'), RENDER.indexOf("// New tag… — an inline input"));
  const pin = fly.slice(fly.indexOf("// SHOW WHEN FOLDED"));
  assert.ok(fly.indexOf('lb.append("Move to ", named())') < fly.indexOf("// SHOW WHEN FOLDED"), "after the Move-to rows, before New tag…");
  assert.match(pin, /if \(home\) \{\s*\n\s*const sec = sectionRef\(home\);\s*\n\s*const on = isPinned\(tabGroups\(\), sec, id\);/,
    "only with a home tag (there is no fold to show through otherwise); the section as the plan keys it (sectionRef: name and local id); the store read with the unions (the migration)");
  assert.doesNotMatch(pin, /isPinned\(tabGroups\(\), home\.name|togglePinned\(tabGroups\(\), home\.name|home\.localId, id\)|readTabGroups\(\)/,
    "never the bare name or the bare id, and never a store read without the unions on a path that writes");
  assert.match(pin, /const row = el\("div", "ctx-item ctx-item-toggle ctx-item-pin" \+ \(on \? " current" : ""\)\);/, "the menus' ✓ mark when on");
  assert.doesNotMatch(pin, /ctx-tag-dot|chip\.style\.background/, "no swatch on the row (T321): the sub-line names the home tag in words, and a tag shows only as the one chip");
  assert.match(pin, /lb\.textContent = "Show when folded";/);
  assert.match(pin, /sb2\.textContent = on \? `stays on the strip while \$\{home\.name\} is folded` : `keep this tab on the strip while \$\{home\.name\} is folded`;/,
    "the copy speaks of the home section alone — and the write is per section, so it is the whole truth");
  assert.match(pin, /writeTabGroups\(prunePinned\(setPinned\(tabGroups\(\), sec, id, !on\), unionFor\(\), knownTabIds\(\), reachableHosts\(\)\)\); build\(\);/,
    "the write prunes, notifies (TABGROUPS_EVENT → renderTabs) and the flyout repaints its ✓ — no renderTabs() call of its own");
  assert.doesNotMatch(pin, /renderTabs\(\)|setTimeout/);
  // every store read on a path that WRITES passes the unions, so an entry in the earlier shape is migrated
  // faithfully before it is written back; the plan reads with the unions it plans by
  assert.match(RENDER, /function tabGroups\(\) \{ return readTabGroups\(viewTagUnion\(effViews\(\)\)\); \}/);
  assert.match(RENDER, /const unions = viewTagUnion\(effViews\(\)\);\s*\n\s*const plan = planStrip\(visibleIds, unions, readTabGroups\(unions\), activeId, phoneLayout\(\),/);
  assert.match(RENDER, /toggle: \(\) => \{ const st = tabGroups\(\); writeTabGroups\(\{ \.\.\.st, on: !st\.on \}\); \}/);
  assert.equal((RENDER.match(/writeTabGroups\(setSectionCollapsed\(tabGroups\(\)/g) || []).length, 2, "the fold writes: the header's click (toggle-group) and the pick of a folded-away tab (unfoldSectionOf), which opens its holder");
  assert.equal((RENDER.match(/readTabGroups\(/g) || []).length, 5, "five reads in all…");
  assert.equal((RENDER.match(/readTabGroups\(unions\)/g) || []).length, 2, "…the adoption's and the plan's, with the unions in hand…");
  assert.equal((RENDER.match(/readTabGroups\(viewTagUnion\(effViews\(\)\)\)/g) || []).length, 1, "…tabGroups() for every write path…");
  assert.deepEqual(RENDER.match(/readTabGroups\(\)\.on/g), ["readTabGroups().on", "readTabGroups().on"], "…and the two bare reads look at `.on` alone (the switch's mark, the flyout's home) and write nothing");
  // the views adoption: the ONE base-assignment site reads the renames against the blob it replaces,
  // moves the base, and rewrites the store only when an entry or the memory changed (views-writes.test
  // pins that both adoption paths reach it)
  const adopt = RENDER.slice(RENDER.indexOf("function adoptBase("), RENDER.indexOf("function captureViews("));
  assert.match(adopt, /const prev = sessionViews;\s*\n\s*sessionViews = v;\s*\n\s*const unions = viewTagUnion\(v\);\s*\n\s*const st = readTabGroups\(unions\);\s*\n\s*const next = followAdoption\(st, prev, v, unions\);\s*\n\s*if \(next !== st\) writeTabGroups\(next\);/,
    "…and runs the follow on EVERY adoption, renames or none — the memory check needs the blob that names a remembered tag otherwise — through followAdoption, with the held blob: a blob that is no news about tag names returns the state untouched, and the adopted blob carries the seqs the memory is stamped with");
  assert.doesNotMatch(adopt, /if \(!renames\.length\) return;/, "no early return on a frame without renames");
  assert.equal(RENDER.split("followAdoption(").length - 1, 1, "one call site: the adoption");
  assert.equal(RENDER.split("followTagRenames(").length - 1, 0, "…and the follow itself is reached only through it");
  // the phone layout's flat strip has no fold to show through: the plan ignores pins there (no-op by construction)
  const p = planStrip(["web", "old1", "old2"], viewTagUnion(VP), setPinned(parseTabGroups(null), ARCH, "old2", true), "web", true);
  assert.deepEqual(p.items, [{ id: "web" }, { id: "old1" }, { id: "old2" }]);
  // docs: the guide says the setting survives the group's rename
  assert.match(GUIDE, /A tab set to show when folded keeps that setting when its\s+group is renamed\./);
});

test("executed: a folded section whose EVERY member is pinned stays folded — the chevron tells the truth — and its header says so: the total, not 0, and why nothing is hidden", () => {
  // pin both of infra's members, fold infra: the header read "▸ infra 0" over two visible tabs, and the
  // click flipped it to "▾ infra 2" with nothing else changing
  const unions = viewTagUnion({ ...V, tagOrder: ["infra", "qa"] });   // infra first, so api homes there: infra = web, api
  const infra: SectionRef = { name: "infra", localId: "g2" };
  let st = setSectionCollapsed(parseTabGroups(null), "infra", true);
  st = setPinned(setPinned(st, infra, "web", true), infra, "api", true);
  const p = planStrip(["web", "api", "tests"], unions, st, "tests", false);
  const h = headsOf(p).find((x) => x.head.name === "infra")!;
  assert.deepEqual([h.folded, h.hidden], [true, []], "folded — the stored state the click acts on, so the header still opens it — with nothing hidden");
  assert.deepEqual(p.items.map((i) => ("head" in i ? `#${i.head.name}${i.folded ? "(folded)" : ""}` : i.id)), ["#infra(folded)", "web", "api", "#qa", "api", "tests"]);
  assert.deepEqual([...p.folded], [], "every tab reachable");
  // the words render.ts paints for that header
  assert.deepEqual(headWords("infra", 2, 0, true, false), {
    count: "2", label: "infra, 2 sessions, folded, all shown",
    title: "infra — folded, but all 2 sessions are set to show when folded, so none is hidden; click to open and see its sessions at a glance",
  }, "the total, never a 0 beside two visible tabs; the title names the menu row that did it (and the click's other effect: the section in the pane)");
  assert.equal(headWords("infra", 1, 0, true, false).title, "infra — folded, but its one session is set to show when folded, so none is hidden; click to open and see its sessions at a glance");
  // …and for the ordinary folded, part-pinned, open and active headers
  assert.deepEqual(headWords("infra", 2, 2, true, false), { count: "2", label: "infra, 2 sessions folded", title: "infra — 2 sessions folded; click to open and see its sessions at a glance" });
  assert.deepEqual(headWords("infra", 2, 1, true, false), { count: "1", label: "infra, 1 session folded", title: "infra — 1 session folded; click to open and see its sessions at a glance" },
    "one pinned: the hidden count, the pinned tab shows itself");
  // while the pane already shows the section (`shown`) the title names the fold alone; the open, shown header
  // holding the tab being read is the way back (`back`) and its spoken label names the action (render.ts
  // drops that header's aria-expanded, so a reader would otherwise hear a plain button with no word about it)
  assert.equal(headWords("infra", 2, 2, true, false, false, true).title, "infra — 2 sessions folded; click to open this group");
  assert.equal(headWords("infra", 2, 0, false, false, false, true).title, "infra — 2 sessions; click to fold this group; drag to reorder the groups");
  assert.deepEqual(headWords("infra", 3, 0, false, true, true, true), { count: "3", label: "infra, 3 sessions, holds the tab you are reading; back to the transcript",
    title: "infra — 3 sessions; holds the tab you are reading; click to go back to the transcript; drag to reorder the groups" });
  assert.deepEqual(headWords("infra", 3, 0, false, true, false, false), headWords("infra", 3, 0, false, true), "the defaults are the ordinary open header");
  assert.deepEqual(headWords("infra", 2, 0, false, false), { count: "2", label: "infra, 2 sessions", title: "infra — 2 sessions; click to fold this group and see its sessions at a glance; drag to reorder the groups" });
  assert.deepEqual(headWords("infra", 3, 0, false, true), { count: "3", label: "infra, 3 sessions, holds the tab you are reading", title: "infra — 3 sessions; holds the tab you are reading; click to fold this group and see its sessions at a glance; drag to reorder the groups" });
  assert.match(MAKE_HEAD, /const words = headWords\(name, total, hidden\.length, collapsed, holdsActive, back, shown\);\s*\n\s*head\.title = words\.title;/);
  assert.match(MAKE_HEAD, /n\.textContent = words\.count;/);
  assert.ok(!MAKE_HEAD.includes("hidden.length : total"), "no second count rule beside the pure one");
  assert.match(GUIDE, /when every tab in a section is set to\s+show, the folded header shows the full count and its tooltip says nothing is hidden\./);
});

test("assistive tech hears a label: decoration is aria-hidden, the header's name is words (name, count, the pip's phrase), and the active section's header is the same button as the rest, marked current", () => {
  // a real accessibility tree read the folded header as a button named "▸ archived 2" — the caret glyph
  // folded into the name — and the active section's header as "button, expanded" with no focus and a
  // no-op click
  assert.match(MAKE_HEAD, /caret\.setAttribute\("aria-hidden", "true"\);/, "the chevron is decoration");
  // T251: the color bar retired for the tag's CHIP, which carries the NAME — words, not decoration, so it is
  // never aria-hidden (the header's explicit aria-label still outranks name-from-content)
  assert.match(MAKE_HEAD, /const chip = tagChip\(name, sec\.color, \{ inheritSize: true \}\);/, "the chip carries the name");
  assert.ok(!/chip\.setAttribute\("aria-hidden"/.test(MAKE_HEAD), "…and is words, never hidden decoration");
  assert.match(MAKE_HEAD, /pip\.setAttribute\("aria-hidden", "true"\);[^\n]*\n\s*spoken \+= "; " \+ pip\.title;/, "the pip too — its phrase rides the label instead");
  assert.match(MAKE_HEAD, /let spoken = words\.label;/, "the label starts as headWords' (name and count, in words — executed above)");
  assert.match(MAKE_HEAD, /head\.setAttribute\("aria-label", spoken\);\s*\n\s*head\.draggable = !settings\.tabsLocked;/, "set once, after the pip; an aria-label outranks name-from-content, so the header says what was appended and nothing that leaked in");
  assert.equal(headWords("archived", 2, 2, true, false).label + "; " + sectionPipTitle("blocked", ["api", "tests"]),
    "archived, 2 sessions folded; 2 sessions in this group are blocked or waiting on you: api, tests",
    "the spoken label of a folded header wearing the pip");
  assert.ok(!MAKE_HEAD.includes('label.setAttribute("aria-hidden"'), "the name is the name: not hidden");
  // the header holding the tab being read is the same button as the rest (it folds; the section at a glance is the
  // pane's answer to a folded active tab), marked current: no header is a no-op "labeled group" any more
  assert.ok(!MAKE_HEAD.includes('"role", "group"'), "no header is a no-op group: every one folds");
  assert.equal(MAKE_HEAD.split('"aria-expanded"').length - 1, 1, "aria-expanded once, on the one fold button every named header is (left off only while its press puts the transcript back)");
  assert.equal(MAKE_HEAD.split('"role", "button"').length - 1, 1);
  assert.match(MAKE_HEAD, /if \(!back\) head\.setAttribute\("aria-expanded", collapsed \? "false" : "true"\);[\s\S]{0,400}if \(holdsActive\) head\.setAttribute\("aria-current", "true"\);/, "the header holding the tab being read says so");
  assert.match(MAKE_HEAD, /head\.tabIndex = 0;/, "…and takes focus like the rest: it is the hidden tab's stand-in");
  assert.equal(headWords("archived", 2, 2, true, false).label, "archived, 2 sessions folded");
  assert.equal(headWords("archived", 2, 2, true, true).label, "archived, 2 sessions folded, holds the tab you are reading", "the stand-in says whose it is");
});


test("the group header wears the SHARED tag chip — one vocabulary with the tags bar and the feed (T251)", () => {
  const MENU = ui("webview", "tag-menu.ts");
  assert.match(MENU, /export function tagChip\(label: string, color\?: string \| null, opts\?: \{ inheritSize\?: boolean; off\?: boolean \}\): HTMLElement \{/,
    "the chip is a named builder, not a lookalike");
  assert.match(MENU, /const chip = tagChip\(c\.label, c\.color\);/, "the tags bar builds its chips through it");
  assert.match(MENU, /\("var\(--dim, " \+ TAG_BTN_GRAY \+ "\)"\)/, "the uncoloured fallback is a THEME TOKEN — the light theme is never handed a dark gray");
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
  assert.match(head, /chip\.classList\.add\("tab-group-chip"\);/);
  assert.ok(!/font-size/.test(head), "the header sets no size of its own — the chip inherits the header's 0.82em (no nested em)");
});

// ── T264b (the user 2026-09-08): a session under several tags has a copy in every group ──────────────────
test("every copy is the full tab of the ONE session, and the by-copy readers tell copies apart (T264b)", () => {
  const loop = RENDER.slice(RENDER.indexOf("collapsedTabIds = plan.folded;"), RENDER.indexOf('const close = el("span", "tab-close");'));
  // the active highlight, the state class and the dot come from the same per-item loop, so every copy wears them
  assert.match(loop, /const tab = el\("div", "tab" \+ \(id === activeId \? " active" : ""\)\);/);
  assert.match(loop, /if \(copyGroup !== undefined\) tab\.dataset\.copy = copyGroup \?\? "";/, "data-copy names the copy's group (sectioned strip only)");
  assert.match(loop, /const ph = makePlaceholderTab\(id\);\s*\n\s*if \(copyGroup !== undefined\) ph\.dataset\.copy = copyGroup \?\? "";/, "…on a placeholder copy too (a create in flight under two tags), so flipTabs keys never collide");
  assert.match(loop, /tab\.dataset\.act = "select";/, "a click on any copy selects the session through the #tabs delegate, by id");
  // the ✕ on a copy ends THE session, and its tip says so
  const close = RENDER.slice(RENDER.indexOf('const close = el("span", "tab-close");'), RENDER.indexOf('close.dataset.act = "close";'));
  assert.match(close, /const copies = plan\.items\.reduce\(\(n, it\) => n \+ \("id" in it && it\.id === id \? 1 : 0\), 0\);/);
  assert.match(close, /copies > 1 \? "End session \(it is the one session, shown in every group it is tagged with\)" : "End session"/);
  // FLIP animates each copy from ITS rect: keyed per copy, not per id
  const flip = RENDER.slice(RENDER.indexOf("function flipTabs("), RENDER.indexOf("function reorderTo("));
  assert.match(flip, /const key = \(t: HTMLElement\) => t\.dataset\.id \+ "\\0" \+ \(t\.dataset\.copy \?\? ""\);/);
  assert.match(flip, /before\.set\(key\(t\), t\.getBoundingClientRect\(\)\)/);
  assert.match(flip, /before\.get\(key\(t\)\)/);
});

test("the tab drag moves THIS copy and reorders within its group's neighbours (T264b)", () => {
  // the dragged element rides beside the id — the id alone names every copy
  assert.match(RENDER, /let draggedEl: HTMLElement \| null = null;/);
  assert.match(RENDER, /draggedId = id; draggedEl = tab; tabDragCommitted = false;/, "set at dragstart");
  assert.match(RENDER, /draggedId = null; draggedEl = null; tabDragCommitted = false;/, "cleared at dragend");
  assert.equal((RENDER.match(/const dragged = draggedEl && draggedEl\.isConnected \? draggedEl : null;/g) || []).length, 2, "dragover and drop both move the very copy under the pointer");
  assert.doesNotMatch(RENDER, /tabs\.querySelector<HTMLElement>\(`\.tab\[data-id="\$\{CSS\.escape\(draggedId\)\}"\]`\)/, "never the first tab wearing the id");
  // the drop's neighbours skip the session's own copy in the group next door — reorderTo against itself moves nothing
  const drop = RENDER.slice(RENDER.indexOf('tabs.addEventListener("drop"'), RENDER.indexOf("tabDragCommitted = true; }"));
  assert.match(drop, /const own = \(n: Element \| null\) => !!n && \(n as HTMLElement\)\.dataset\?\.id === draggedId;/);
  // the neighbours come from the dragged copy's OWN group first: the walk stops at a header, a row break or
  // the trail's divider (its boundary with the one-group-per-row setting off), and only a group holding no
  // other tab falls back to the nearest tab across groups (a drop at a group's head used to anchor on the
  // group above's last tab, so the session's other copy jumped)
  assert.match(drop, /const edge = \(n: Element\) => n\.classList\.contains\("tab-group-head"\) \|\| n\.classList\.contains\("tab-group-break"\) \|\| n\.classList\.contains\("tab-group-sep"\);/);
  assert.match(drop, /while \(n && \(!\(n as HTMLElement\)\.dataset\?\.id \|\| own\(n\)\)\) \{ if \(inGroup && edge\(n\)\) return null; n = step\(n\); \}/);
  assert.match(drop, /const prev = prevIn \?\? \(nextIn \? null : walk\(dragged\.previousElementSibling, back, false\)\);/);
  assert.match(drop, /const next = nextIn \?\? \(prevIn \? null : walk\(dragged\.nextElementSibling, fwd, false\)\);/);
  assert.doesNotMatch(drop, /\n\s*tabDragCommitted = true;\s*\/\//, "no unconditional commit: a drop that moved nothing takes dragend's cancel path");
  // a drop still changes no membership: a copy dragged into another group's row re-sections home on the next render
  const over = RENDER.slice(RENDER.indexOf('tabs.addEventListener("dragover"'), RENDER.indexOf('tabs.addEventListener("drop"'));
  assert.match(over, /A drop changes\s*\n?\s*\/\/ no membership/);
});

test("executed: the keyboard order keeps a session while any copy is on screen (T264b)", () => {
  const unions = viewTagUnion({ tags: [{ id: "t-a", name: "alpha", color: "#1EA1EB", members: ["web", "api"] },
                                       { id: "t-b", name: "beta", color: "#54B204", members: ["api"] }] });
  const st = setSectionCollapsed(parseTabGroups(null, unions), "alpha", true);
  const p = planStrip(["web", "api"], unions, st, null, false);
  assert.deepEqual([...p.folded], ["web"], "web's only copy is folded; api's beta copy is on screen");
  const both = planStrip(["web", "api"], unions, setSectionCollapsed(st, "beta", true), null, false);
  assert.deepEqual([...both.folded].sort(), ["api", "web"], "every copy folded → skipped");
});

test("↑/↓ measure from the focused copy and never land on the session's own other copy (T264b review)", () => {
  const fn = RENDER.slice(RENDER.indexOf("function tabInAdjacentRow("), RENDER.indexOf("// ---- session picker overlay"));
  assert.match(fn, /const focused = document\.activeElement as HTMLElement \| null;/);
  assert.match(fn, /focused && focused\.classList\.contains\("tab"\) && focused\.dataset\.id === id && bar\?\.contains\(focused\)\s*\n\s*\? focused : bar\?\.querySelector/,
    "the origin is the copy the user is on, else the first copy");
  assert.match(fn, /if \(t\.dataset\.id === id\) continue;/, "the session's own other copy is never the answer (setActive would no-op: a dead key)");
});

test("the tab menu speaks for the right-clicked copy's group: Move to drops THAT tag, Show when folded pins THAT section, Rename edits THAT copy (T264b review)", () => {
  assert.match(RENDER, /showTabMenu\(e, id, tab\.dataset\.copy\); \}\);/, "the copy's group rides the contextmenu call");
  assert.match(RENDER, /function showTabMenu\(e: MouseEvent, id: string, copy\?: string\)/);
  assert.match(RENDER, /const home0 = readTabGroups\(\)\.on \? \(\(copy !== undefined \? holding\(\)\.find\(\(g\) => g\.name === copy\) : undefined\) \?\? holding\(\)\[0\]\) : undefined;/,
    "the copy's own group, else the first holder (the flat strip names no copy)");
  assert.match(RENDER, /startTabRename\(id, copy\)/);
  assert.match(RENDER, /function startTabRename\(id: string, copy\?: string\)/);
  assert.match(RENDER, /t\.dataset\.id === id && \(copy === undefined \|\| t\.dataset\.copy === copy\)\)\s*\n\s*\?\? Array\.from\(bar\.children\)\.find\(\(t\): t is HTMLElement => t instanceof HTMLElement && t\.dataset\.id === id\)\);/,
    "the right-clicked copy edits in place, the first copy when that one is gone");
  assert.match(RENDER, /plus\.title = "add this tag too \(the session keeps its other tags\)" \+ \(settings\.tabsLocked \? ": adding is not a move, so the lock does not hold it" : ""\);/);   // the tab lock (T395) adds its clause
});

test("executed: activating a session under several tags springs no fold: a holder showing a copy is marked; all folded, the first is the stand-in and nothing opens (T264b)", () => {
  // web=[s1,s2], archived=[s2,s3,s4], archived folded by default: activating s2 (a live session also
  // tagged archived to put it away later) must not spring the whole archived row open
  const unions = viewTagUnion({ tags: [{ id: "t-web", name: "web", color: "#1EA1EB", members: ["s1", "s2"] },
                                       { id: "t-arch", name: "archived", color: "#4EA8A9", members: ["s2", "s3", "s4"] }] });
  const st = parseTabGroups(null, unions);
  const marks = (active: string | null, state = st) => planStrip(["s1", "s2", "s3", "s4"], unions, state, active, false).items
    .map((i) => ("head" in i ? `#${i.head.name}${i.active ? "(active)" : ""}${i.folded ? "(folded)" : ""}` : i.id));
  assert.deepEqual(marks("s2"), ["#web(active)", "s1", "s2", "#archived(folded)"], "archived stays folded: s2 shows under web");
  assert.deepEqual(marks("s1"), ["#web(active)", "s1", "s2", "#archived(folded)"]);
  assert.deepEqual([...planStrip(["s1", "s2", "s3", "s4"], unions, st, "s2", false).folded], ["s3", "s4"], "s2 is on screen (under web): not skipped");
  // both holders folded, nothing pinned: NOTHING springs open (the active tab's section folds like any other);
  // exactly one holder, the first in tagOrder, is marked: the header that stands in for the tab
  const both = setSectionCollapsed(st, "web", true);
  assert.deepEqual(marks("s2", both), ["#web(active)(folded)", "#archived(folded)"], "both folds stand; web's header is s2's stand-in");
  assert.deepEqual([...planStrip(["s1", "s2", "s3", "s4"], unions, both, "s2", false).folded], ["s1", "s2", "s3", "s4"], "every copy off the strip: s2 is folded away too");
  // a copy pinned through a fold counts as shown: that holder is the one marked, and the pinned copy carries focus
  const pinned = setPinned(both, { name: "archived", localId: "t-arch" }, "s2", true);
  assert.deepEqual(marks("s2", pinned), ["#web(folded)", "#archived(active)(folded)", "s2"], "both folds stand; s2 shows through archived's fold, whose header is marked");
  // a single holder: marked whatever the fold, folded as the store says
  assert.deepEqual(marks("s3", both), ["#web(folded)", "#archived(active)(folded)"]);
});

test("the guide states the every-tag rule (T264b)", () => {
  assert.match(GUIDE, /A session with several tags appears under each of them; every copy is the same\s+session/);
  assert.doesNotMatch(GUIDE, /sits under the first of them in your tag order/, "the retired home-tag sentence is gone");
});

test("the guide's small-dot sentence says the dot shows only in the pip's three states and names them in the rule's order (tab-state.ts sectionPip): red for blocked or waiting on you, else gold for working, else amber for an API retry", () => {
  const prose = (t: string) => new RegExp(t.replace(/[.()]/g, "\\$&").split(" ").join("\\s+"));   // the guide wraps its lines
  assert.match(GUIDE, prose("a small dot after it shows when one of them is busy or needs you: red when one is blocked or waiting on you, otherwise gold when one is working, otherwise amber when one hit an API error and is retrying on its own (hover it for their names)."));
  assert.doesNotMatch(GUIDE, prose("a small dot after it says when one of them is working or waiting on you"), "the two-state sentence is gone");
});

test("the guide lists the header's parts in the built order: the tag's color and name, then the chevron and the count (T284)", () => {
  assert.match(GUIDE, /Each header shows the tag's color and name, then a chevron and a\s+member count\./);
  assert.doesNotMatch(GUIDE, /Each header shows a chevron, the tag's color/, "the pre-T284 caret-first sentence is gone");
  // The guide names the parts in the order the builder appends them (the structure pin above), and that is
  // the order the reader sees only while the header's flex row keeps DOM order: no rule on the header or on
  // one of its parts may reorder them (headerReorderRules below names the forms).
  const rules = headerRules(CSS);
  assert.ok(rules.length >= 5, "the header's rules are read: " + rules.length);
  assert.deepEqual(headerReorderRules(CSS), [], "no rule reorders the header");
});

/** The stylesheet's rules on the header or one of its parts, as [selector, body] pairs. */
function headerRules(css: string): Array<[string, string]> {
  return [...css.matchAll(/(?:^|\n)([^{}\n]*\.tab-group-(?:head|caret|chip|count|pip)[^{}\n]*)\{([^}]*)\}/g)].map((m) => [m[1].trim(), m[2]]);
}
/** A body that would show the header's parts in another order than the DOM's: an order, a flex-direction,
 *  a flex-flow whose value carries row-reverse or column-reverse (the wrap may come first), direction: rtl,
 *  a margin-left: auto push; in any case, as CSS reads property names and keywords. The anchors keep
 *  `border:` from reading as `order:` and a custom property such as `--head-direction:` from reading as
 *  `direction:`. */
const REORDERS = /(?:^|[\s;])order\s*:|flex-direction\s*:|flex-flow\s*:[^;}]*(?:row|column)-reverse|(?:^|[\s;])direction\s*:\s*rtl|margin-left\s*:\s*auto/i;
/** The selectors of the header rules the guard rejects. */
function headerReorderRules(css: string): string[] {
  return headerRules(css).filter(([, body]) => REORDERS.test(body)).map(([sel]) => sel);
}

test("executed: the header-order guard rejects a flex-flow with a reverse value, wrap first or last, in any case; a wrap alone passes", () => {
  assert.deepEqual(headerReorderRules(".tab-group-head { flex-flow: row-reverse; }"), [".tab-group-head"], "the shorthand's row-reverse is a flex-direction: row-reverse");
  assert.deepEqual(headerReorderRules(".tab-group-head { flex-flow: column-reverse wrap; }"), [".tab-group-head"], "column-reverse, with a wrap after it");
  assert.deepEqual(headerReorderRules(".tab-group-head { flex-flow: wrap column-reverse; }"), [".tab-group-head"], "the wrap may come first");
  assert.deepEqual(headerReorderRules(".tab-group-head { FLEX-FLOW: ROW-REVERSE; }"), [".tab-group-head"], "CSS reads property names and keywords in any case");
  assert.deepEqual(headerReorderRules(".tab-group-head {\n  display: flex;\n  flex-flow : row-reverse nowrap;\n}"), [".tab-group-head"], "spaced and multi-line, like the stylesheet");
  assert.deepEqual(headerReorderRules(".tab-group-head { flex-flow: row wrap; }"), [], "a wrap alone keeps the order");
});

test("executed: the header-order guard rejects direction: rtl on the header, and accepts ltr and a custom property whose name ends in direction", () => {
  assert.deepEqual(headerReorderRules(".tab-group-head { direction: rtl; }"), [".tab-group-head"], "rtl lays the row out from the right");
  assert.deepEqual(headerReorderRules(".tab-group-head { color: var(--fg); direction:rtl }"), [".tab-group-head"], "no space after the colon, no semicolon after the value");
  assert.deepEqual(headerReorderRules(".tab-group-head { direction: ltr; }"), [], "ltr is the order the DOM has");
  assert.deepEqual(headerReorderRules(".tab-group-head { --head-direction: rtl; }"), [], "a custom property is not the direction");
});

test("executed: the header-order guard's earlier forms still reject (order, flex-direction, a margin-left: auto push) and a border, a fixed margin or an unrelated rule passes", () => {
  assert.deepEqual(headerReorderRules(".tab-group-head { order: 1; }"), [".tab-group-head"]);
  assert.deepEqual(headerReorderRules(".tab-group-count { order: -1; }"), [".tab-group-count"], "a part can be reordered too");
  assert.deepEqual(headerReorderRules(".tab-group-head { flex-direction: row-reverse; }"), [".tab-group-head"]);
  assert.deepEqual(headerReorderRules(".tab-group-caret { margin-left: auto; }"), [".tab-group-caret"], "the push that sends a part to the far end");
  assert.deepEqual(headerReorderRules(".tab-group-head { display: flex; align-items: center; gap: 6px; border: 1px solid var(--box-border); }"), [], "a border is not an order");
  assert.deepEqual(headerReorderRules(".tab-group-chip { margin-left: 4px; }"), [], "a fixed margin is not a push");
  assert.deepEqual(headerReorderRules(".tab-group-head.dragging { opacity: 0.5; }\n.tab-strip { order: 2; }"), [], "only the header and its parts are read");
  assert.deepEqual(headerReorderRules(".tab-group-head { order: 1; }\n.tab-group-pip { flex-direction: column; }"), [".tab-group-head", ".tab-group-pip"], "every offending rule is named, in stylesheet order");
});
