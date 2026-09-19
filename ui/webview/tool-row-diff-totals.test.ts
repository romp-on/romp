// An expanded tool group's rows print each edit's totals as the diff fold's toggle ("+A -R"), and until 2026-09-18 that was plain
// dim text while the folded group's summary wore the diff colours (tool-plus green, tool-minus red). The user asked for one
// dress: both places build their numbers through one helper, diffTotals, so the row's toggle carries the same two classes and
// the same theme tokens, never a literal colour. The chat renderer has no jsdom harness (like the other render-*.test.ts), so the
// row's DOM is EXECUTED here through the helper's own source over a minimal element stand-in, and the wiring is pinned at the
// source: the row's fold call passes diffTotals, inlineFold appends an element label, appendTotals shares the helper.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

// a minimal element: className, textContent, children, append/appendChild; enough to run diffTotals and read the DOM it builds
class FakeEl {
  tag: string; className = ""; textContent = ""; children: (FakeEl | string)[] = [];
  constructor(tag: string) { this.tag = tag; }
  appendChild(c: FakeEl | string) { this.children.push(c); return c; }
  append(...cs: (FakeEl | string)[]) { for (const c of cs) this.children.push(c); }
  get text(): string { return this.children.length ? this.children.map((c) => (typeof c === "string" ? c : c.text)).join("") : this.textContent; }
}
function runDiffTotals(add: number, del: number): FakeEl {
  const m = RENDER.match(/function diffTotals\(add: number, del: number\): HTMLElement \{[\s\S]*?\n\}/);
  assert.ok(m, "render.ts defines diffTotals, the one builder of an edit's totals");
  const src = m![0].replace(/: HTMLElement|: number/g, "");   // the TypeScript annotations off, the body as written
  const el = (tag: string, cls?: string) => { const e = new FakeEl(tag); if (cls) e.className = cls; return e; };
  const fn = new Function("el", src + "\nreturn diffTotals;")(el) as (a: number, d: number) => FakeEl;
  return fn(add, del);
}

test("a row's edit totals are two spans, tool-plus and tool-minus, inside tool-totals, reading +A -R (executed over the helper's source)", () => {
  const tot = runDiffTotals(12, 3);
  assert.equal(tot.className, "tool-totals");
  const spans = tot.children.filter((c) => typeof c !== "string") as FakeEl[];
  assert.deepEqual(spans.map((s) => [s.className, s.textContent]), [["tool-plus", "+12"], ["tool-minus", "-3"]],
    "the plus count green-classed, the minus count red-classed (the base printed the toggle as plain text)");
  assert.equal(tot.text, "+12 -3", "the approved shape, a hyphen minus, one space between");
});

test("the expanded row's diff fold toggle is built from diffTotals, and inlineFold takes the element as its label", () => {
  assert.match(RENDER, /inlineFold\(head, turn, diffTotals\(add, del\), pre, fkey\);/, "the row's fold call passes the dressed totals");
  assert.match(RENDER, /function inlineFold\(head: HTMLElement, turn: HTMLElement, label: string \| HTMLElement, content: HTMLElement, key\?: string\)/, "a string or an element label");
  assert.match(RENDER, /if \(typeof label === "string"\) toggle\.textContent = label;[^\n]*\n\s*else toggle\.appendChild\(label\);/, "an element label is appended, a string stays text");
  assert.doesNotMatch(RENDER, /inlineFold\(head, turn, `\+\$\{add\} -\$\{del\}`/, "no plain-text totals toggle survives");
});

test("the folded group's summary shares the helper, and the classes resolve through the theme tokens, never a literal colour", () => {
  assert.match(RENDER, /function appendTotals\(line: HTMLElement, add: number, del: number\): void \{\s*\n\s*if \(!add && !del\) return;\s*\n\s*line\.append\(" ", diffTotals\(add, del\)\);/, "one dress for both places");
  assert.match(CSS, /\.tool-plus \{ color: var\(--green\); \}\s*\n\.tool-minus \{ color: var\(--err\); \}/, "the tokens the folded summary already wore");
  assert.doesNotMatch(CSS, /\.tool-fold-toggle \.tool-(plus|minus)/, "no toggle-scoped override: the spans' own rules dress them inside the toggle too");
});
