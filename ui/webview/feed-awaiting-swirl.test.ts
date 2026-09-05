// Spinning-swirl + caption on "in motion, not on you" cards (the user 2026-06-29): a small romp swirl spins in
// the card body, beside the distiller takeaway/decision-brief, with a couple words saying what's happening.
// The LADDER itself (which case wins, what it says) moved to ./spin-caption and is EXECUTED by
// spin-caption.test.ts — a source-regex pin let it go silently wrong once (the user 2026-07-21). What's left
// here is the surface the ladder drives: the DOM element, the wiring, the tooltip text, and the CSS.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const SPIN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "spin-caption.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the swirl element is built in the body, right after the distiller line, and registered", () => {
  assert.match(FEED, /const awaitSpin = el\("div", "fask-awaiting"\); awaitSpin\.style\.display = "none";/);
  assert.match(FEED, /const awaitGlyph = el\("span", "fask-awaiting-swirl"\)/);
  // distill now rides inside the takeaway section (takeSec), with the background section above it (2026-07-02)
  assert.match(FEED, /main\.append\(row1, row2, row3, secs, qbody, awaitSpin, checklist, delegations\)/);
  assert.match(FEED, /a\._awaitSpin = awaitSpin; a\._awaitWhy = awaitWhy;/);
});

test("the swirl is driven by spinFor's caption — shown when there is one, else hidden", () => {
  assert.match(FEED, /import \{ spinFor, waitedSuffix, awaitWord, groupRows, GROUP_TITLE, ROW_KIND_OF_LEGACY, type AwaitRow \} from "\.\/spin-caption";/);   // the rows' vocabulary too (slice 2)
  assert.match(FEED, /const spin = spinFor\(it, distillPending\(dCompleted, dBlocked, it\.summary, it\.blockSummary, !!it\.blocked\),/);
  assert.match(FEED, /const spinCaption = spin\.caption, spinTip = spin\.tip, awaitingBg = spin\.awaitingBg;/);
  assert.match(FEED, /import \{ distillText, distillInputs, applyDistillLine, distillPending, distillStaleNote \} from "\.\/distiller-line";/);
  assert.match(FEED, /a\._awaitSpin\.style\.display = spinCaption \? "" : "none";/);
  assert.match(FEED, /\} else a\._awaitWhy\.textContent = spinCaption;\n\s*a\._awaitSpin\.title = spinTip \|\| spinCaption;/);
});

test("a bg-task wait wears the compact 'Awaiting task' pill that expands the task list (the user 2026-07-13)", () => {
  // the pill joins the mutually-exclusive card sections (bg / summary / subgoals / tasks), swirl inside
  assert.match(FEED, /const taskBtn = el\("button", "fask-secbtn fask-taskbtn"\)/);
  assert.match(FEED, /"bg" \| "summary" \| "subgoals" \| "tasks" \| "stall" \| "none"/);
  // "Awaiting task", never "Waiting on task": one word per state across every surface — the chat chip
  // and timeline badge already say Awaiting for this exact state (the user 2026-08-13)
  // …worded by the ONE rule the chat chip and box use (awaitWord, slice 2 2026-09-05): "Awaiting
  // agent" / "Awaiting 3 agents" / "Awaiting 4" for mixed kinds; a single named peer → its name
  assert.match(FEED, /pillLbl\.replaceChildren\("Awaiting "\);/);
  assert.match(FEED, /\} else pillLbl\.append\(pillWord\);/);
  assert.doesNotMatch(FEED, /"Waiting on task"/);
  // the pill carries the wait's elapsed time, same readout as the awaiting box (the user 2026-08-23)
  assert.match(FEED, /pillLbl\.append\(pillWaited\);/);   // appended after the (possibly coloured) word since slice 2
  assert.match(FEED, /taskBtn\.onclick = pick\("tasks"\);/);
  // expanded rows render in the checklist spot, same view as Sub-goals, the swirl as each row's mark
  assert.match(FEED, /if \(choice === "tasks"\) \{[\s\S]*?el\("div", "fcheck ftask"\)[\s\S]*?ftask-swirl/);
  assert.match(CSS, /\.fask-taskbtn \{ display: inline-flex/);
  assert.match(CSS, /\.ftask-swirl\.fask-awaiting-swirl/);
});

test("each case carries a concise tooltip on the swirl (hover → the key idea, not an essay)", () => {
  // tooltips are short and plain-spoken (the user 2026-06-29): the key idea, no LLM-essay phrasing, no em dashes
  assert.match(SPIN, /"A new prompt, still running\. Sorted into a goal once this stretch of work finishes\."/);
  assert.match(SPIN, /"This stretch of work finished; the judge is sorting it into a goal\."/);
  assert.match(SPIN, /tip: "You followed up\. Reopened to Working; the judge will resolve it or re-block it\.",/);
  assert.match(SPIN, /tip: "You replied on this thread\. Moved to Working while the reply runs; it comes back if the judge re-confirms the block\.",/);
  // no em dashes anywhere in the swirl tooltips (JLD + the user's house style ban them)
  assert.doesNotMatch(SPIN, /tip: "[^"]*—/);
  assert.match(FEED, /a\._awaitSpin\.title = spinTip \|\| spinCaption;/);
});

test("the swirl's Analyzing caption REPLACES the '↩ re-judging' chip (no double-labeling)", () => {
  assert.match(FEED, /if \(spinCaption === "Analyzing…"\) a\._followedup\.style\.display = "none";/);
});

test("the awaiting case gets a rounded box, its swirl SPINS, and its caption wraps to two lines (the user 2026-07-04)", () => {
  // the awaiting-background-agents case wears the box (its distinct read); the class marks it so
  assert.match(FEED, /a\._awaitSpin\.classList\.toggle\("await-paused", awaitingBg\);/);
  // rounded outline box
  assert.match(CSS, /\.fask-awaiting\.await-paused \{[\s\S]*?border: 1px solid var\(--box-border\); border-radius: 8px;/);
  // the swirl now SPINS here too (the user 2026-07-04) — no per-box animation:none override remains
  assert.doesNotMatch(CSS, /\.fask-awaiting\.await-paused \.fask-awaiting-swirl \{ animation: none/);
  // the caption WRAPS to up to two lines instead of truncating with an ellipsis, so the full message reads
  assert.match(CSS, /\.fask-awaiting-why \{[\s\S]*?-webkit-line-clamp: 2;/);
  assert.doesNotMatch(CSS, /\.fask-awaiting-why \{[^}]*text-overflow: ellipsis; white-space: nowrap;/);
  // the box top-aligns so a two-line caption sits cleanly beside the glyph
  assert.match(CSS, /\.fask-awaiting\.await-paused \{[\s\S]*?align-items: flex-start;/);
});

test("the swirl uses the shared glyph, spins SLOWER (calmer) + reverse like the loader, and respects reduced-motion", () => {
  assert.match(CSS, /\.fask-awaiting-swirl \{[\s\S]*?url\(\.\.\/media\/romp-swirl-glyph\.svg\)/);
  assert.match(CSS, /animation: fask-swirl-spin 2\.4s linear infinite;/);   // slowed from 1.4s (the user 2026-06-29)
  assert.match(CSS, /@keyframes fask-swirl-spin \{ to \{ transform: rotate\(-360deg\); \} \}/);   // reverse, like rl-spin
  assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{ \.fask-awaiting-swirl \{ animation: none; \} \}/);
});

test("a DELEGATION wait names its peers like the ↪ from line — identity colour, quiet host: prefix (the user 2026-08-23)", () => {
  // the kernel ships structured identities on the delegation arm (awaiting.peers); the box renders
  // them with hostPartsNodes + the peer's identity colour instead of a colourless "Awaiting peer"
  assert.match(FEED, /const awPeers = \(awaitingBg && it\.awaiting && it\.awaiting\.peers\) \|\| \[\];/);
  assert.match(FEED, /nm\.replaceChildren\(\.\.\.hostPartsNodes\(p\.host, p\.name\)\);/);
  assert.match(FEED, /if \(p\.color && p\.color\.bg\) nm\.style\.color = p\.color\.bg;/);
  // the elapsed readout survives the structured path (the pill/box parity rule of 2026-08-23)
  assert.match(FEED, /a\._awaitWhy\.append\(waitedSuffix\(it\.awaiting && it\.awaiting\.since, Date\.now\(\) \/ 1000\)\);/);
  // older kernels ship no peers → the ladder's plain caption is the fallback
  assert.match(FEED, /\} else a\._awaitWhy\.textContent = spinCaption;/);
  // federation attributes a kernel-local peer to that kernel and prefixes its sid, exactly as it
  // does for origin.peerSid — a merged card's peer name keeps its host and its click routes home
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(out\.awaiting && typeof out\.awaiting === "object" && Array\.isArray\(out\.awaiting\.peers\)\)/);
  assert.match(FED, /\? \{ \.\.\.p, host, sid: prefixId\(host, p\.sid\) \} : p\) \};/);
});
