// The Continue button's reply renders in the FOLLOW-UP gesture grammar (the user 2026-08-17;
// supersedes the 2026-08-15 slash-command dress — its ✦ mark said nothing, and the boxed chip +
// trailing caret made a second grammar next to ↩ Follow-up's): caret, "→ Continue" in the accent,
// then the SENT text's own first line with the rest one click deeper — expanding only ever reveals
// MORE of the same words, never different ones. The judges still file it as the user's reply. Keyed
// on the kernel's romp-canned marker (event-based), never on text-matching the copy. Source pins (no
// jsdom for the chat renderer), plus the kernel side of the contract.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the canned Continue wears the Follow-up grammar: caret, → Continue, the sent text's first line", () => {
  assert.match(RENDER, /canned\?: string/, "the user event carries the kernel's lift");
  assert.match(RENDER, /\} else if \(!romp && !injected && !tagged && ev\.md && ev\.canned === "continue"\) \{/,
    "keyed on the lifted marker, never on text-matching the canned copy");
  // ONE gesture grammar (the user 2026-08-17): the follow-up header's own classes, no ✦, no boxed chip
  assert.match(RENDER, /const head = el\("div", "followup-tag cont-tag"\);/);
  assert.match(RENDER, /const lbl = el\("span", "followup-lbl"\); lbl\.textContent = "→ Continue";/);
  assert.match(RENDER, /const g = el\("span", "followup-goal"\); g\.textContent = clipped;/,
    "the gist wears the follow-up header's own dim ellipsis dress");
  assert.doesNotMatch(RENDER, /slash-cmd-chip"\); chip\.textContent = "Continue"/, "the boxed chip is gone");
  // the collapsed line is the SENT text itself (first non-quote line, clipped) — never a paraphrase,
  // so expanding reveals more of the same words rather than different ones
  assert.match(RENDER, /const first = lines\.find\(\(l\) => l && !l\.startsWith\(">"\)\) \|\| lines\.find\(\(l\) => l\) \|\| raw;/);
  assert.match(RENDER, /const clipped = first\.length > 90 \? first\.slice\(0, 88\)\.replace\(\/\\s\+\\S\*\$\/, ""\) \+ "…" : first;/);
  assert.doesNotMatch(RENDER, /Continue — keep going; open calls are yours/, "the paraphrased label is gone");
  // the same fold machinery nudges use: keyed expand, the stable body delegate, never a per-render listener
  assert.match(RENDER, /const ckey = ev\.uuid \? "cont:" \+ ev\.uuid : undefined;/);
  assert.match(RENDER, /bubble\.classList\.add\("nudge-collapsible"\);\s*\n\s*bubble\.dataset\.act = "nudgetoggle";\s*\n?\s*\/\/ the stable body delegate/);
});

test("the row sheds the bubble, keeps the header when expanded, and the caret flips in CSS", () => {
  assert.match(CSS, /\.user-bubble\.nudge-collapsible \{ cursor: pointer; \}/);
  // the LIGHT BLUE box (the user 2026-08-18, superseding the bare row): blue = "from you", gray =
  // "from romp" — pale blue says "your gesture, standardized words", never posing as typed prose
  assert.match(CSS, /\.user-bubble\.cont-row \{\s*\n\s*max-width: 72%;\s*\n\s*background: color-mix\(in srgb, var\(--you\) 16%, transparent\); border: 1px solid color-mix\(in srgb, var\(--you\) 42%, transparent\);/);
  assert.match(CSS, /\.user-bubble\.cont-row \.cont-tri::before \{ content: "▸"; \}/);
  assert.match(CSS, /\.user-bubble\.cont-row\.expanded \.cont-tri::before \{ content: "▾"; \}/,
    "the same .expanded class the fold delegate flips also turns the caret");
  assert.match(CSS, /\.user-bubble\.cont-row \.nudge-full \{ display: none; color: var\(--dim\); margin-top: 4px; max-width: 640px; \}/);
  assert.match(CSS, /\.user-bubble\.cont-row\.expanded \.nudge-full \{ display: block; \}/,
    "the header stays while the full words unfold beneath it — the follow-up ctx idiom");
});

test("kernel: the marker is stamped at the ONE cont send and lifted for human turns only", () => {
  assert.match(KERNEL, /\(CONTINUE_TEXT \+ "\\n\\n<!-- romp-canned: continue -->"\) if msg\.get\("cont"\) else str\(msg\["text"\]\)/);
  assert.match(KERNEL, /if author == "human" and "<!-- romp-canned: continue -->" in text:/);
  assert.match(KERNEL, /ev\["canned"\] = "continue"/);
});
