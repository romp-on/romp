// The comment highlight's KaTeX pairing, pinned against REAL katex output (the user 2026-08-24: the
// hand-approximated fixture is how the dead selector shipped — the math stayed yellow while prose
// greened). katex here is the exact package the bundle renders with (math.ts, output:"html").
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import katex from "katex";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("real katex output matches the marking code's structural assumptions", () => {
  const html = katex.renderToString("\\Sigma_a v = \\lambda \\Sigma_b v", { output: "html", throwOnError: false });
  assert.match(html, /^<span class="katex">/, "the host root the marker's closest('.katex') finds");
  assert.ok(/>[^<>]+</.test(html), "glyph text nodes exist inside — sliceRanges can wrap marks there");
  assert.doesNotMatch(html, /katex-mathml/, "html-only output — no MathML twin for a mark to double-cover");
});

test("the busy/settled pairing keys on the HOST carrying a busy mark, and the rule is alive (not brace-eaten)", () => {
  assert.match(RENDER, /const kat = segs\[i\]\.parentElement\?\.closest\("\.katex"\) as HTMLElement \| null;/);
  assert.match(RENDER, /if \(kat\) kat\.classList\.toggle\("cmt-hl-host", th\.status !== "resolved" && th\.status !== "merged"\);/);
  assert.match(CSS, /\.md \.katex\.cmt-hl-host:has\(mark\.cmt-hl\.busy\) \{/);
  // the rule directly above the combined selector must be a CLOSED comment, never a stray } (the
  // brace-eater — css-balance.test.ts guards the whole class; this pins the one that shipped)
  const at = CSS.indexOf(".md :not(pre) > code.cmt-hl-host:has(mark.cmt-hl.busy),");
  const before = CSS.slice(Math.max(0, at - 220), at);
  assert.doesNotMatch(before, /\}\s*\n\s*\}/, "no doubled close ahead of the combined busy rule");
});
