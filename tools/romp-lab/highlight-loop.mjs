// The scripted highlight loop (T106): drive the REAL dashboard through the user's exact flow and
// assert the comment-mark state at every event boundary of the T102 contract. Synthetic content
// only. Exits non-zero naming the first diverging phase, so a fix → re-run cycle loops cleanly.
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const require2 = createRequire(path.join(ROOT, "vscode-extension", "package.json"));
const { chromium } = require2("playwright");

const PORT = process.env.PORT, TOKEN = process.env.TOKEN, LAB = process.env.LAB_DIR, PROJ = process.env.PROJECT_DIR;
const MODEL = process.env.LAB_MODEL || "Haiku";
const PASSAGE = "the moon has no weather to speak of";
const shots = path.join(LAB, "shots");
let phaseN = 0;
const fails = [];

const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1280, height: 850 } });
page.on("pageerror", (e) => console.log("PAGEERR", String(e)));
const shot = async (name) => page.screenshot({ path: path.join(shots, `${String(++phaseN).padStart(2, "0")}-${name}.png`) });
const busy = () => page.evaluate(() => !!document.querySelector("mark.cmt-hl.busy"));
const marked = () => page.evaluate(() => !!document.querySelector("mark.cmt-hl"));
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
  if (!ok) fails.push(name);
};
// continuous flicker sampling between two moments: the pulse must not blip through thread-open
let flickerStop = null;
const sampleFlicker = () => {
  const seen = { falses: 0, samples: 0 };
  const t = setInterval(async () => {
    try { seen.samples++; if (!(await busy())) seen.falses++; } catch { /* page busy */ }
  }, 60);
  flickerStop = () => { clearInterval(t); return seen; };
};

await page.goto(`http://127.0.0.1:${PORT}/chat?token=${TOKEN}`);
await page.waitForSelector("#composer-input", { timeout: 20000 });

// ── create the lab session through the + picker, exactly as a user would ──
await page.click(".tab.tab-add", { timeout: 8000 });
await page.waitForSelector("#picker-search", { timeout: 8000 });
await shot("picker");
await page.fill("#picker-search", "lab-moon");
await page.fill("#picker-dir", PROJ);
await page.click("#picker-new-btn");
await page.waitForSelector(".tab", { timeout: 30000 });
// wait until the session is REAL (composer live, statusline shows a state chip)
await page.waitForSelector("#statusline .chip, #statusline .compacting-line", { timeout: 60000 });
await shot("session-open");

// ── drop the model to the lab default (cheapest) via the statusline menu ──
await page.waitForSelector('#statusline .meta-btn[data-kind="model"]', { timeout: 60000 });
const dropped = await page.evaluate(async (want) => {
  const btn = document.querySelector('#statusline .meta-btn[data-kind="model"]');
  if (!btn) return "no-model-badge";
  btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  const item = Array.from(document.querySelectorAll(".meta-menu .meta-item"))
    .find((i) => i.textContent.toLowerCase().includes(want.toLowerCase()));
  if (!item) return "no-menu-item";
  item.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  return "ok";
}, MODEL);
console.log("model drop:", dropped);

// ── a REAL turn: ask for a stable, selectable sentence ──
await page.fill("#composer-input", `Reply with exactly this sentence and nothing else: ${PASSAGE}`);
await page.keyboard.press("Enter");
await page.waitForFunction((p) => Array.from(document.querySelectorAll(".turn-assistant .md"))
  .some((e) => e.textContent.includes(p)), PASSAGE, { timeout: 180000 });
await shot("reply-landed");

// ── the COMMENT flow: select the passage → context menu → Comment → send ──
await page.evaluate((p) => {
  const el = Array.from(document.querySelectorAll(".turn-assistant .md")).find((e) => e.textContent.includes(p));
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let tn; while ((tn = walker.nextNode())) if (tn.data.includes(p)) break;
  const r = document.createRange();
  const off = tn.data.indexOf(p);
  r.setStart(tn, off); r.setEnd(tn, off + p.length);
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
  document.dispatchEvent(new Event("selectionchange"));
  el.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 420, clientY: 300 }));
}, PASSAGE);
await page.waitForSelector(".ctx-menu", { timeout: 5000 });
await page.evaluate(() => {
  for (const it of document.querySelectorAll(".ctx-menu .ctx-item")) if (it.textContent === "Comment") it.dispatchEvent(new MouseEvent("click", { bubbles: true }));
});
await page.waitForSelector(".cmt-pop .cmt-input", { timeout: 5000 });
await page.fill(".cmt-pop .cmt-input", "Why is that the case? One short sentence.");
const preSend = await busy();
await page.keyboard.press("Enter");
// PHASE 1: the send gesture latches the pulse IMMEDIATELY — before any thread exists
await page.waitForTimeout(250);
check("1-latch-at-send", (await busy()) === true, `pre-send busy was ${preSend}`);
await shot("send-latched");
sampleFlicker();

// PHASE 2+3: hold through thread-open, clear exactly when the reply text renders in the popover
await page.waitForFunction(() => {
  const msgs = document.querySelector(".cmt-pop .cmt-msgs");
  return msgs && Array.from(msgs.querySelectorAll(".turn-assistant, .cmt-msg.agent"))
    .some((e) => e.textContent.trim().length > 0);
}, undefined, { timeout: 240000 });
const flick = flickerStop();
check("2-no-flicker-through-thread-open", flick.falses === 0, `${flick.falses}/${flick.samples} false samples before the reply`);
await shot("reply-in-thread");
// the clear rides the same frame that rendered the reply — allow one push
await page.waitForFunction(() => !document.querySelector("mark.cmt-hl.busy"), undefined, { timeout: 12000 })
  .then(() => check("3-clear-on-reply-record", true))
  .catch(async () => check("3-clear-on-reply-record", false, "still busy 12s after the reply rendered"));
await shot("settled-yellow");

// PHASE 4: a follow-up re-latches until ITS reply
await page.fill(".cmt-pop .cmt-input", "And one more short sentence about it?");
await page.keyboard.press("Enter");
await page.waitForTimeout(250);
check("4-follow-up-relatch", (await busy()) === true);
await shot("followup-latched");
const agentTurns = () => page.evaluate(() => document.querySelectorAll(".cmt-pop .cmt-msgs .turn-assistant, .cmt-pop .cmt-msgs .cmt-msg.agent").length);
const before = await agentTurns();
await page.waitForFunction((n) => document.querySelectorAll(".cmt-pop .cmt-msgs .turn-assistant, .cmt-pop .cmt-msgs .cmt-msg.agent").length > n, before, { timeout: 240000 });
await page.waitForFunction(() => !document.querySelector("mark.cmt-hl.busy"), undefined, { timeout: 12000 })
  .then(() => check("4b-follow-up-clears-on-its-reply", true))
  .catch(async () => check("4b-follow-up-clears-on-its-reply", false, "still busy after the follow-up's reply"));
await shot("followup-settled");

// PHASE 5: nothing sticks — sample well past several pushes
await page.waitForTimeout(10000);
check("5-nothing-sticks", (await busy()) === false, "busy after 10s quiet");
check("mark-still-present", await marked(), "the settled yellow mark must remain");
await shot("final");

await b.close();
if (fails.length) { console.log("DIVERGED:", fails.join(", ")); process.exit(1); }
console.log("ALL PHASES GREEN");
