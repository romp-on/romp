// T218's render half (the manager's 87-pair anchor study): the summary deep link now carries the
// distiller's located supporting SPAN, and the landing scrolls to and highlights the sentence
// inside the (often long, multi-topic) cited message. Pins: the payload gate, the click's quote,
// the kernel focus passthrough, the highlight's zero-DOM-surgery mechanism with honest fallbacks,
// and the paint. The judge half (labels on substantive non-prose atoms, the QUOTE protocol, the
// write-time locate) lives in tests/test_cite_substance.py + the distill goldens.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const JUDGE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "judge.py"), "utf8");

test("the span rides the payload only while the cited atom IS the landing", () => {
  assert.match(KERNEL, /"summaryAnchorQuote": \(nodes\[nid\]\.get\("summaryQuote"\)\s*\n\s*if _sa_u and _sa_u == nodes\[nid\]\.get\("summaryAnchor"\) else None\)/,
    "a fallback-tier anchor lands elsewhere — its quote would highlight the wrong text");
  assert.match(FEED, /summaryAnchorQuote\?: string \| null;/);
  assert.match(FEED, /anchorUuid: it\.summaryAnchorUuid, quote: it\.summaryAnchorQuote \|\| undefined/,
    "the click carries the span");
  assert.match(KERNEL, /f\["anchorQuote"\] = str\(msg\["quote"\]\)\[:300\]/, "the focus frame passes it through");
});

test("the landing highlights with zero DOM surgery, and falls back honestly", () => {
  assert.match(RENDER, /pendingAnchorQuote = typeof \(m as \{ anchorQuote\?: string \}\)\.anchorQuote === "string"/);
  assert.match(RENDER, /if \(pendingAnchorQuote\) \{ highlightCiteSpan\(target, pendingAnchorQuote\); pendingAnchorQuote = null; \}/,
    "consumed exactly at the successful landing — an honest-fail never strands a stale span");
  const fn = RENDER.slice(RENDER.indexOf("function highlightCiteSpan"), RENDER.indexOf("function landOn"));
  assert.match(fn, /CSS as unknown as \{ highlights\?: Map<string, unknown> \}/,
    "the CSS Custom Highlight API — the ever-re-rendering turn list is never mutated");
  assert.match(fn, /if \(!H \|\| typeof Highlight === "undefined"\) return;/, "no API → today's whole-message landing");
  assert.match(fn, /if \(!m\) return;\s*\/\/ unfindable in the rendered text → no highlight, no guess/);
  assert.match(fn, /scrollIntoView\(\{ block: "center", behavior: "auto" \}\)/, "land ON the sentence, not the message top");
  assert.match(CSS, /::highlight\(cite-span\) \{ background-color: color-mix\(in srgb, var\(--accent\) 30%, transparent\);/,
    "accent-tinted, never a status colour (via the token, so the light theme re-inks it)");
});

test("substantive non-prose atoms are citable — the study's convicted classes", () => {
  assert.match(JUDGE, /isinstance\(a\.get\("author"\), dict\)/, "a PEER postal report takes a label at the prose floor");
  assert.match(JUDGE, /_PR_LINK_RE = re\.compile\(r"https:\/\/github\\\.com\/\\S\+\/\(\?:pull\|commit\|compare\)\/\\S\+"\)/,
    "a PR/commit-link tool result is substance by construction");
  assert.match(JUDGE, /out\.append\("RESULTS: " \+ " \| "\.join\(results\[:4\]\)\)/);
  assert.match(JUDGE, /def _store_cited_span\(nd, marks, src, quote\):/, "the span stores only with a RESOLVED citation");
});

test("per-paragraph landings (T220): the user's ruling, wired end to end with honest fallbacks", () => {
  // payload: aligned entries gated on the cited tier holding authority; absent on old stores forever
  assert.match(KERNEL, /"summaryAnchorsPara": \(\[\(\{"u": e\["a"\], \*\*\(\{"q": e\["q"\]\} if e\.get\("q"\) else \{\}\)\} if e else None\)/);
  assert.match(KERNEL, /if \(nodes\[nid\]\.get\("summaryAnchors"\)\s*\n\s*and _sa_u and _sa_u == nodes\[nid\]\.get\("summaryAnchor"\)\) else None\)/,
    "the T153 outrun gate covers the whole per-paragraph list");
  // renderer: the model's own citation beats the T153 tree-row mapping; count drift drops, never mis-maps
  assert.match(FEED, /const cited = anchOk \? pAnchors!\[i\] : null;/);
  assert.match(FEED, /const anchOk = !!\(pAnchors && paras\.length === pAnchors\.length\);/,
    "a re-split that disagrees with the stored alignment drops the anchors — never a mis-mapped click");
  assert.match(FEED, /anchorUuid: u, quote: aq/, "the paragraph's click carries ITS span — the T218 landing highlights it");
  // the hover affordance: exactly the hovered paragraph highlights
  const FEEDCSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");
  assert.match(FEEDCSS, /\.fask-para-link:hover \{ background: rgba\(255, 255, 255, 0\.07\);/);
  // judge: the parser + store helpers exist with the honest-gap discipline
  assert.match(JUDGE, /def _split_sources\(text\):/);
  assert.match(JUDGE, /def _store_para_cites\(nd, marks, body, para_cites\):/);
  assert.match(JUDGE, /nd\["summaryAnchors"\] = anchors if any_set else None/,
    "nothing valid stores None — old single-anchor stores read identically forever, no migration");
});
