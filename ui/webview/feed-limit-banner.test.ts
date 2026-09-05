// A judge layer down on a USAGE LIMIT says so loudly (the user 2026-08-18, whose judges failed
// quietly into ~22,400 doomed retries over two days while the Fable window sat at 100%): the
// kernel ships the judge-limit latch on the feed payload, and the feed renders a compact banner
// above the columns — for a Fable-window exhaustion it offers switching analysis to Opus (cheaper
// per token); for a general exhaustion it states the account is full and when it resumes. The
// banner is built ONCE and updated in place (the click-safety rule), and the button acknowledges
// before the round-trip. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const JUDGE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "judge.py"), "utf8");

test("the kernel ships the latch and the judge gate writes it model-aware", () => {
  assert.match(KERNEL, /"judgeLimit": _judge_limit_view\(\),/,
    "the latch rides enriched — WHO the window touches joins it (2026-08-28)");
  assert.match(KERNEL, /def _judge_limit_view\(\):/);
  assert.match(JUDGE, /_buckets = \["five_hour", "seven_day"\] \+ \(\["fable"\] if "fable" in str\(model\)\.lower\(\) else \[\]\)/,
    "the gated buckets follow the CALL'S model — a fable pin gates on the fable window");
  assert.match(JUDGE, /_limit_mark\(_b, _lim\.get\("pct"\), _lim\.get\("resets_at"\), model\)/);
  assert.match(JUDGE, /_limit_clear\(\) {8,}# \.\.\.and the usage-limit latch|_limit_clear\(\)/, "a success clears it");
  assert.match(JUDGE, /jd\._USAGE_REFRESH_FN|_USAGE_REFRESH_FN = None/, "the idle-stale poke hook exists");
  assert.match(KERNEL, /jd\._USAGE_REFRESH_FN = getattr\(_sdk_backend, "refresh_usage", None\)/,
    "…and the kernel wires it (getattr: the hook is best-effort, so a stub backend can't break the build)");
});

test("the banner is build-once, acknowledges, and offers Opus only for the Fable window", () => {
  assert.match(FEED, /function ensureJudgeLimit\(\): HTMLElement/);
  assert.match(FEED, /let b = document\.getElementById\("judge-limit-banner"\);\s*\n\s*if \(b\) return b;/,
    "built once — the button survives re-renders (click-safety)");
  assert.match(FEED, /btn\.textContent = "Switching…";\s*\n\s*\/\/ ?.*|btn\.textContent = "Switching…";/,
    "acknowledges before the kernel round-trip");
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "setJudgeModel", model: "opus", gt: Date\.now\(\) \}\)/,
    "the switch is a settings gesture like any gear pick — stamped so the kernel can order it");
  assert.match(FEED, /btn\.style\.display = fable \? "" : "none";/, "the switch offer is Fable-specific");
  assert.match(FEED, /The account's usage window is full/, "general exhaustion states it plainly");
  assert.match(FEED, /paintJudgeLimit\(\);   \/\/ the usage-limit banner above the columns/,
    "painted on every feed render");
  assert.match(CSS, /\.judge-limit-banner \{/);
});

test("the banner names who the window actually touches — authoritative billing, fail-loud unknowns", () => {
  // the judges bill ONE credential: card movement pauses board-wide, and ONLY the sessions billing
  // this account rate-limit on their own turns (the user 2026-08-28). The kernel reads the CLI's own
  // authLive report first, the picked intent second — the Billing tooltip's exact sources — and a
  // session whose billing it cannot know is listed as unknown, never silently omitted.
  assert.match(KERNEL, /b = str\(tm\.get\("authLive"\) or tm\.get\("auth"\) or ""\)/);
  assert.match(KERNEL, /loginSessions=sorted\(billed, key=key\), billingUnknown=sorted\(unknown, key=key\)/);
  assert.ok(!/board-wide/.test(FEED), "the corrected scope (2026-08-28): a judge bills the judged session — key-billed analysis never pauses");
  assert.match(FEED, /"Analysis and turns pause for the sessions billing it: "/);
  assert.match(FEED, /"\. Other sessions' analysis continues on their own billing\."/);
  assert.match(FEED, /No live session bills this account/, "the empty list is stated, not blank");
  assert.match(FEED, /const many = names\.length > 3;/, "inline when few…");
  assert.match(FEED, /names\.slice\(0, 2\) : names;/, "…two named + a count when many");
  assert.match(FEED, /" · billing unknown for "/, "the fail-loud row");
  // the standard session chip: bold, identity colour, the shared host-prefix treatment
  assert.match(FEED, /const c = el\("b", "jl-chip"\);\s*\n\s*c\.replaceChildren\(\.\.\.hostPartsNodes\(p\.host, p\.name\)\);\s*\n\s*if \(p\.color && p\.color\.bg\) c\.style\.color = p\.color\.bg;/);
  assert.match(KERNEL, /billed\.append\(_peer_identity\(sid\)\)/, "identities via the ONE ladder");
});

test("the gate, the clear, and the envelope mark all scope to LOGIN-billed calls (2026-08-28)", () => {
  // usage.json's windows are the login account's; a judge call bills the JUDGED session's account
  // (the 2026-08-12 rule) — so a key-billed call (pay-per-token, no windows) is never gated, its
  // success never clears the login latch, and its limit-shaped 429 never mints one.
  const run = JUDGE.slice(JUDGE.indexOf("def _judge_run(")).split("\ndef ", 1)[0];
  const billing = run.indexOf('auth = "codex" if engine == "codex" else _judge_auth(fsid)');
  const gate = run.indexOf('u = json.loads((STATE / "usage.json").read_text()) if auth == "login" else {}');
  assert.ok(billing >= 0 && gate > billing,
    "billing resolves BEFORE the login-only gate, including the Codex bypass");
  assert.match(run, /if auth == "login":\s*\n\s*# only a LOGIN-billed success is evidence/);
  assert.match(run, /if auth == "login":\s*\n\s{24}_limit_mark\("account", None, None, model\)/,
    "the envelope mark too (the manager's ruling: it carries no resets_at, so a false one sticks)");
});

test("the '+N more' expand is keyed and delegated — click-safe across the per-push repaints", () => {
  assert.match(FEED, /let jlSessOpen = false;/, "module state, survives re-renders");
  assert.match(FEED, /if \(t && t\.dataset && t\.dataset\.act === "jl-more"\) \{ jlSessOpen = !jlSessOpen; paintJudgeLimit\(\); \}/,
    "the toggle is rebuilt per paint, so its action rides the build-once banner root");
});

test("dismiss latches to THIS episode and re-arms only on a NEW one — no timers", () => {
  assert.match(FEED, /const jlEpisodeKey = \(j: \{ bucket\?: string; resets_at\?: number \} \| null\) =>\s*\n\s*\(j\?\.bucket \|\| ""\) \+ ":" \+ \(j\?\.resets_at \|\| 0\);/,
    "the episode's identity IS bucket + resets_at");
  assert.match(FEED, /localStorage\.setItem\("romp:jlDismiss", jlEpisodeKey\(judgeLimit\)\)/,
    "stored, so the dismissal survives re-renders and reloads for the episode's lifetime");
  assert.match(FEED, /if \(dismissed === jlEpisodeKey\(judgeLimit\)\) \{ b\.style\.display = "none"; return; \}/,
    "a NEW episode has a new key and shows again — the deciding event, never a timer");
  assert.match(FEED, /paintJudgeLimit\(\);\s*\n\s*\};\s*\n\s*b\.appendChild\(x\);/,
    "the hide is the immediate acknowledgment, on the build-once button");
  assert.match(CSS, /\.judge-limit-banner \.jl-dismiss \{/);
});
