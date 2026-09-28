// The model pickers' FAMILY rows name the version a click runs, and the version submenu's Latest row names what the
// alias resolves to (the user 2026-09-28: Opus 5.5 was listed only inside the hover submenu, and "Opus" — the CLI's
// alias — ran Opus 5 a week after 5.5 shipped). Both read the kernel's /models: `default` (the family's pin, else its
// alias) and `resolves` (the version the kernel hands the CLI for the bare alias: the catalog's newest it has not
// refused). The chat menu (ui/webview/render.ts) and the timeline lane menu (ui/romp-timeline-view.js) carry twin
// helpers, executed here as written (the current-meta-tick idiom), and each menu's wiring of them is pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function fnSource(src: string, name: string): string {
  const i = src.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} is defined`);
  const j = src.indexOf("\n}\n", i);
  return src.slice(i, j + 3);
}
type Label = (c: any) => string;
type Pair = { runs: Label; latest: Label };
const tl = new Function(fnSource(TL, "familyRunsLabel") + fnSource(TL, "latestVersionLabel")
  + "\nreturn { runs: familyRunsLabel, latest: latestVersionLabel };")() as Pair;
const chat = new Function(
  fnSource(RENDER, "familyRunsLabel").replace("(c: MetaChoice): string {", "(c) {")
  + fnSource(RENDER, "latestVersionLabel").replace("(c: MetaChoice): string {", "(c) {")
  + "\nreturn { runs: familyRunsLabel, latest: latestVersionLabel };")() as Pair;
const both: Array<[string, Pair]> = [["chat", chat], ["timeline", tl]];

const versions = [{ value: "claude-opus-5-5", label: "Opus 5.5" }, { value: "claude-opus-5", label: "Opus 5" },
                  { value: "claude-opus-4-8", label: "Opus 4.8" }];
// a floating family: the click sends the alias (`default` IS the value), the kernel resolves it to 5.5
const floating = { value: "opus", label: "Opus", default: "opus", resolves: "claude-opus-5-5", versions };

test("a floating family's row names the version its alias runs, and so does Latest, on both surfaces", () => {
  for (const [name, m] of both) {
    assert.equal(m.runs(floating), "Opus 5.5", name + ": the family row");
    assert.equal(m.latest(floating), "Opus 5.5", name + ": the Latest row");
  }
});

test("a pinned family's row names its pin as such; Latest still names what unpinning would run", () => {
  const pinned = { ...floating, default: "claude-opus-5" };
  for (const [name, m] of both) {
    assert.equal(m.runs(pinned), "Opus 5 · pinned", name);
    assert.equal(m.latest(pinned), "Opus 5.5", name);
  }
});

test("no resolution, no line: an older kernel without the field, a resolution the list does not carry, a one-version family", () => {
  const older = { value: "opus", label: "Opus", default: "opus", versions };
  const unlisted = { ...floating, resolves: "claude-opus-9-9" };
  const one = { value: "haiku", label: "Haiku", default: "haiku", resolves: "claude-haiku-4-5",
                versions: [{ value: "claude-haiku-4-5", label: "Haiku 4.5" }] };
  for (const [name, m] of both) {
    assert.equal(m.runs(older), "", name + ": older kernel");
    assert.equal(m.latest(older), "", name + ": older kernel");
    assert.equal(m.runs(unlisted), "", name + ": unlisted resolution");
    assert.equal(m.latest(unlisted), "", name + ": unlisted resolution");
    assert.equal(m.runs(one), "", name + ": one version has no submenu and nothing to add");
    assert.equal(m.runs({ value: "opus", label: "Opus" }), "", name + ": no versions at all");
  }
});

test("a pin the list does not carry names nothing rather than the alias's resolution", () => {
  // the family click would send the stale pin (the kernel's read filters it to the alias on its next /models, and
  // the picker re-reads on that frame); until then the row promises nothing it cannot name
  const stale = { ...floating, default: "claude-opus-1-0" };
  for (const [name, m] of both) assert.equal(m.runs(stale), "", name);
});

test("each menu wires the helpers: the family row's sub-line and the Latest row's tail", () => {
  assert.match(RENDER, /const subText = c\.sub \|\| \(kind === "model" \? familyRunsLabel\(c as MetaChoice\) : ""\);/,
               "the chat row's sub-line is the family's version when the row has none of its own");
  assert.match(RENDER, /const now = latestVersionLabel\(c\);/, "the chat Latest row reads the resolution");
  assert.match(RENDER, /\+ c\.label \+ \(now \? " — " \+ now \+ " now" : ""\);/, "…and appends it to its sub-line");
  assert.match(TL, /const runs = kind === 'model' \? familyRunsLabel\(c\) : '';/, "the timeline row's sub-line");
  assert.match(TL, /const now = latestVersionLabel\(c\);/, "the timeline Latest row reads the resolution");
  assert.match(TL, /\+ c\.label \+ \(now \? ' — ' \+ now \+ ' now' : ''\) \}\);/, "…and appends it");
  // the MetaChoice type carries the field the kernel serves
  assert.match(RENDER, /resolves\?: string \| null \}/, "the picker's choice type names `resolves`");
});
