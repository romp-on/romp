// CSS brace balance (the user 2026-08-24, screenshot: math never went green while prose did). An
// UNMATCHED stray `}` — left at styles.css:2612 by an earlier block removal — makes Chromium's error
// recovery EAT the rule that follows it: the combined code/katex busy rule sat right after one and
// was dead on arrival, verified by a browser A/B (withStray → the next rule's declaration lost;
// without → applied). A selector can pass every source pin and still be inert this way, so the guard
// is structural: every stylesheet parses with balanced braces, no stray closes anywhere.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SHEETS = ["styles.css", "feed.css", "fleet-pane.css"];

function scan(css: string): { finalDepth: number; strayCloseLines: number[] } {
  let depth = 0, line = 1, i = 0;
  const stray: number[] = [];
  while (i < css.length) {
    if (css.startsWith("/*", i)) { const j = css.indexOf("*/", i) + 2; line += (css.slice(i, j).match(/\n/g) || []).length; i = j; continue; }
    const c = css[i];
    if (c === "\n") line++;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth < 0) { stray.push(line); depth = 0; } }
    i++;
  }
  return { finalDepth: depth, strayCloseLines: stray };
}

for (const f of SHEETS) {
  test(f + " has balanced braces — a stray } silently kills the rule after it", () => {
    const p = path.resolve(process.cwd(), "..", "ui", "webview", f);
    if (!fs.existsSync(p)) return;   // sheet renamed/retired → nothing to scan
    const r = scan(fs.readFileSync(p, "utf8"));
    assert.deepEqual(r.strayCloseLines, [], f + ": stray closing braces at lines " + r.strayCloseLines.join(", "));
    assert.equal(r.finalDepth, 0, f + ": unclosed blocks at EOF");
  });
}
