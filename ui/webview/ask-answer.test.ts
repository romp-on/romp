// An ANSWERED AskUserQuestion turn renders as a distinct blue "you answered Claude's question" box —
// styled like a sent message, but still mirroring the dialog (the prompt + the option list with the
// chosen answer highlighted) so the scrollback shows it was a reply to a popup, not a typed message
// (the user 2026-06-16). render.ts prefers the kernel's structured askAnswer array (Option B: it rides
// the existing kind:"tool" AskUserQuestion event) and falls back to the raw input/output parse until the
// kernel emits it. While the question is still pending (empty chosen) it stays a neutral question card.
// The chat renderer has no jsdom harness, so — like render-rail.test.ts — pin it at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("renderAsk reads the kernel's structured askAnswer, falling back to the raw parse", () => {
  assert.match(RENDER, /\(ev\.askAnswer && ev\.askAnswer\.length\) \? ev\.askAnswer : parseAskRaw\(ev\)/);
  // the tool event type carries the structured array, shaped by AskAnswerBlock
  assert.match(RENDER, /askAnswer\?: AskAnswerBlock\[\]/);
  assert.match(RENDER, /type AskAnswerBlock = \{ question: string; header\?: string; options:/);
});

test("the turn flips to the blue 'answered' box only once an answer is recorded", () => {
  // answered = at least one block has a non-empty chosen (pending = empty chosen → plain card)
  assert.match(RENDER, /const answered = blocks\.some\(\(b\) => b\.chosen && b\.chosen\.length > 0\)/);
  assert.match(RENDER, /"turn turn-ask" \+ \(answered \? " answered" : ""\)/);
  assert.match(RENDER, /"ask-card" \+ \(answered \? " ask-answered" : ""\)/);
  // a blue user dot matches the box when answered; a neutral ring while pending
  assert.match(RENDER, /dot\(answered \? "user" : "ring"\)/);
  // the marker line names it as the user's reply to Claude
  assert.match(RENDER, /ask-answered-tag/);
  assert.match(RENDER, /answered Claude/);
});

test("a PENDING question renders COMPACT — head + title only, no options (the user 2026-07-22)", () => {
  // The pending turn used to duplicate the entire picker (question + every option) that the red live-ask
  // box already shows directly below it, so the same question appeared twice. Now the option rows are
  // emitted only once ANSWERED; while pending the card is just the "Question" head + the question title,
  // and it fills in to the full question+chosen box on answer.
  assert.match(RENDER, /if \(answered\) \{\s*\n\s*const opts = Array\.isArray\(b\.options\) \? b\.options : \[\];/);
  // the title is rendered UNCONDITIONALLY, before (and outside) the answered-only option block
  const body = RENDER.split("for (const b of blocks) {")[1].split("card.appendChild(qel);")[0];
  assert.ok(body.includes("qel.appendChild(qt);"), "the question title is still rendered");
  assert.ok(body.indexOf("qel.appendChild(qt);") < body.indexOf("if (answered) {"),
    "the title must be emitted outside/before the answered-only option block");
  // and the pending head chip ("Question" / "N questions") is unchanged
  assert.match(RENDER, /head\.textContent = blocks\.length > 1 \? `\$\{blocks\.length\} questions` : "Question"/);
});

test("a free-text answer matching no option renders as an 'Other' row with the verbatim text", () => {
  // chosen entries that name an option highlight it; the rest surface as free-text "Other" rows
  assert.match(RENDER, /const picked = new Set\(chosen\.filter\(\(c\) => labels\.includes\(c\)\)\)/);
  assert.match(RENDER, /const others = chosen\.filter\(\(c\) => !labels\.includes\(c\)\)/);
  assert.match(RENDER, /el\("div", "ask-opt chosen ask-other"\)/);
  assert.match(RENDER, /ask-answer-text/);
});

test("the answered box is styled as a blue, right-aligned 'your reply' box", () => {
  assert.match(CSS, /\.turn-ask\.answered \{[^}]*align-items: flex-end/);
  assert.match(CSS, /\.ask-card\.ask-answered \{[^}]*var\(--you\)/);
  assert.match(CSS, /\.ask-answered-tag \{/);
});
