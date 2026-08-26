// A mentioned image/PDF renders FULL-SIZE in the chat (the user 2026-07-20, who wanted not even a thumbnail
// but a rendered image, similar to how it renders the user messages), AT its mention — the figure
// follows the block whose prose names it (the user 2026-08-15) — absolute OR relative path; the
// kernel resolves a relative one against the session's cwd exactly like click-to-open. Per surface:
// web renders via previewFull (kernel /file bytes → <img> at the user-image scale / a PDF card;
// kernel-verified paths fail LOUDLY with a retry chip; unverified ones hide but stay healable); the VS Code
// webview can't reach the kernel origin from an <img>, so images ride the SAME host data-URL flow the
// user-message pictures use (imgRequest, now carrying the session id) and PDFs keep the click-to-open
// link. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("previewFull renders the image itself; a PDF is a click-to-view CARD, never an auto-loading frame", () => {
  assert.match(PREVIEW, /export function previewFull\(path: string, sid\?: string \| null, verified = false, pin\?: string\): HTMLElement \| null/);
  assert.match(PREVIEW, /img\.className = "path-full-img";/);
  // NO inline <iframe> for PDFs (2026-07-20): a browser set to "Download PDFs" saved a fresh copy on
  // EVERY chat re-render — the Downloads folder silently filled. The fetch must be user-initiated.
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  assert.doesNotMatch(pf, /createElement\("iframe"\)/, "no auto-loading PDF frame in the chat strip");
  assert.match(pf, /box\.classList\.add\("path-full-pdfcard"\);/);
  assert.match(pf, /box\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openLightbox\(path, sid\); \};/);
  // the HEAD probe (headers only — never a download) HIDES a failed UNVERIFIED card and keeps it
  // registered for the heal events (2026-08-24 — self-removal erased the spot until a send); a
  // kernel-verified card skips the probe — the kernel already stat'd the file
  assert.match(pf, /const probe = \(\) => fetch\(fileUrl\(path, sid\), \{ method: "HEAD" \}\)/);
});

test("a kernel-VERIFIED preview fails LOUDLY: a retry chip holds the figure's spot, never silent removal", () => {
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  // unverified (a file:// mention, an old kernel's no-pathLinks payload) now rides the SAME retry
  // machinery hidden instead of self-removing (2026-08-24): one transient failure used to erase the
  // figure until a send's re-render minted a fresh box — preview-heal.test.ts pins the sentinel
  assert.match(pf, /if \(!verified\) box\.style\.display = "none";/);
  assert.match(pf, /chip\.className = "path-full-retry";/);
  assert.match(pf, /chip\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); autoRetries = 3; ackTap\(ev\); build\(true\); \};/,
    "a tap re-arms persistence, acknowledges even mid-attempt, then retries");
  assert.match(pf, /headers: got > 0 \? \{ Range: "bytes=" \+ got \+ "-" \} : \{\}/,
    "a retry RESUMES from the bytes already received (kernel /file honors the suffix range)");
  // the render layer feeds the verdict: spacePaths and pathLinks hits are kernel-stat'd paths
  assert.match(RENDER, /const kernelVerified = new Set<string>\(\);/);
  assert.match(RENDER, /if \(!isUri && typeof fixed === "string"\) kernelVerified\.add\(open\);/);
  assert.match(CSS, /\.path-full-retry \{ display: inline-flex;/, "visible chrome — the chip has chat-sheet css");
});

test("the failure chip narrates what happens next, escalates on repeat, and a retry's swirl is perceivable", () => {
  // the user 2026-08-16, on a slow connection: a dead-end "unavailable" that later healed itself read
  // as broken UI, and a tap whose instant failure re-rendered an identical chip read as ignored.
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  // copy names the armed auto-heal while bounded retries remain; plain tap-to-retry once spent
  assert.match(pf, /"⚠ still unavailable" : "⚠ preview unavailable"/);
  // while auto-retries remain the box KEEPS its loading persona (swirl + note, whole box tappable);
  // the ⚠ chip is the GIVE-UP state only (the user 2026-08-16, third report: state bouncing between
  // "trying" and "unavailable" on every retry cycle read as impatient even when it eventually loaded)
  assert.match(pf, /if \(autoRetries > 0 \|\| transient\) \{\s*\n\s*if \(!transient\) autoRetries--;\s*\n\s*failedPreviews\.set\(box, \(\) => build\(true\)\);/);
  assert.match(pf, /\+ " — retrying · tap to retry now";/);
  assert.match(pf, /wait\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); autoRetries = 3; ackTap\(ev\); build\(true\); \};/,
    "the whole retrying box is the tap target — a tap re-arms persistence and acknowledges");
  assert.match(pf, /"⚠ preview unavailable"\)\)\s*\n\s*\+ " — tap to retry";/,
    "the chip exists only once the budget is spent, so it carries no retrying-automatically claim");
  // a repeat failure pulses the chip on swap-in — the acknowledge-every-click rule
  assert.match(pf, /chip\.classList\.add\("path-retry-flash"\);/);
  assert.match(CSS, /\.path-full-retry\.path-retry-flash, \.path-load-note\.path-retry-flash \{ animation: path-retry-flash/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.path-full-retry\.path-retry-flash, \.path-load-note\.path-retry-flash \{ animation: none;/);
  // a manual retry that dies instantly holds the swirl a perceivable beat before the chip returns
  assert.match(PREVIEW, /const MIN_RETRY_SPIN_MS = 400;/);
  assert.match(pf, /const left = MIN_RETRY_SPIN_MS - \(Date\.now\(\) - started\);/);
  assert.match(pf, /if \(left > 0\) setTimeout\(showChip, left\); else showChip\(\);/);
  // the delayed swap yields to a re-rendered turn — a fresh box owns the spot by then
  assert.match(pf, /if \(!box\.isConnected\) return;/);
});

test("a failed preview heals on the next kernel push — the kernel-is-back event, not a tap or a timer", () => {
  // the 2026-08-15 report: the fetch died in a converge-restart window, and delta-send never rebuilds
  // an old turn's DOM, so the chip sat until a human tapped it. Any incoming kernel message proves the
  // kernel is reachable again; no pushes arrive while it's down, so retry-on-push can't spam.
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  assert.match(pf, /let autoRetries = 3;/, "bounded — a genuinely-dead file settles on the tap chip");
  // a failure naming the LINK, not the image, never spends the budget (the user 2026-08-17: the
  // kernel-restart tunnel window burned all three attempts right before the link came back)
  assert.match(pf, /const transient = \/tunnel to \.\* is not answering\|no attached host\|re-dialing\/i\.test\(lastErr\);/);
  assert.match(pf, /if \(autoRetries > 0 \|\| transient\) \{\s*\n\s*if \(!transient\) autoRetries--;\s*\n\s*failedPreviews\.set\(box, \(\) => build\(true\)\);/);
  assert.match(PREVIEW, /export function retryFailedPreviews\(\): void/);
  assert.match(PREVIEW, /if \(box\.isConnected\) rebuild\(\);/, "a re-rendered turn's fresh box supersedes the old");
  assert.match(RENDER, /retryFailedPreviews\(\);/, "called from the kernel message handler");
});

test("the chat uses the FULL render on web, and the host data-URL flow for images in VS Code", () => {
  assert.match(RENDER, /const full = canPreview\(\) \? previewFull\(p, renderingOwnerSid \?\? activeId, kernelVerified\.has\(p\), \(pathPins \|\| \{\}\)\[p\]\)\s*\n\s*: previewKind\(p\) === "img" \? buildPathImg\(p, renderingOwnerSid \?\? activeId\) : null;/);
  assert.doesNotMatch(RENDER, /previewThumb/, "the chat no longer renders mention thumbnails — full renders now");
});

test("figures render AT their mention: after the block naming them; same-block figures share a strip", () => {
  // path → first mention element, captured in BOTH linkify passes (space paths and the token walker)
  assert.match(RENDER, /const mentionAt = new Map<string, HTMLElement>\(\);/);
  assert.match(RENDER, /mentionAt\.set\(tok, code\);/, "the space-path pass anchors on its code span");
  assert.match(RENDER, /mentionAt\.set\(open, link\);/, "the token walker anchors on the link it minted");
  assert.match(RENDER, /const BLOCK_SEL = "p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th";/);
  assert.match(RENDER, /anchor\.insertAdjacentElement\("afterend", strip\);/, "a paragraph's figure lands right after it");
  assert.match(RENDER, /\/\^\(LI\|TD\|TH\)\$\/\.test\(anchor\.tagName\)/, "a list item keeps its figure inside, under its bullet");
  assert.match(RENDER, /previewable\.slice\(0, 4\)/, "the wallpaper cap stays");
});

test("VS Code's pending image chip pulses while the host round-trip is in flight; a failed one doesn't", () => {
  assert.match(RENDER, /"user-img-path" \+ \(imgFailed\.has\(imgKey\(sid, p\)\) \? "" : " img-pending"\)/);
  assert.match(CSS, /\.user-img-path\.img-pending::after \{ content: " ···";/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.user-img-path\.img-pending::after \{ animation: none;/);
});

test("imgRequest carries the OWNING session id so relative paths resolve against that session's cwd", () => {
  // the caller-passed sid, never activeId: a background prebuild renders hidden tabs while another
  // session is active, and baking activeId asked the wrong session (often the wrong HOST) for the bytes
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "imgRequest", path: p, id: sid \}\);/);
  assert.match(KERNEL, /_img_data_url\(_resolve_open_path\(p, msg\.get\("id"\)\)\)/);
});

test("full-size images wear the user-image scale — one size per information type", () => {
  assert.match(CSS, /\.path-full-img \{[^}]*max-height: 320px/);
  assert.match(CSS, /\.user-img \{[^}]*max-height: 320px/);
  assert.match(CSS, /\.path-full-pdfcard \{/);
});

test("the feed's artifact strips keep their compact thumbnails (cards stay glanceable)", () => {
  assert.match(FEED, /previewThumb\(/);
  assert.match(PREVIEW, /export function previewThumb\(path: string, sid\?: string \| null\): HTMLElement \| null/);
});

test("a flaky link finishes the picture ACROSS retries: resume, narrate progress, hold the layout", () => {
  // the user 2026-08-16, on flaky wifi: every retry restarted the transfer from byte 0 (the pictures
  // never arrived), the swirl said nothing about progress, and the chip/swirl height swaps thrashed
  // the chat scroll by about a line.
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  // the managed retry reads the stream and narrates real progress under the swirl
  assert.match(pf, /note\.textContent = "fetching… " \+ fmtBytes\(got, total\);/);
  assert.match(PREVIEW, /function fmtBytes\(got: number, total: number\): string/);
  // a 206 continues the partial; a plain 200 restarts it cleanly
  assert.match(pf, /if \(r\.status === 206\)/);
  assert.match(pf, /parts = \[\]; got = 0;/, "a full-body reply resets the partial, never appends");
  // an attempt that made progress refills the retry budget — forward motion proves the link works
  assert.match(pf, /if \(got > gotBefore\) autoRetries = 3;/);
  // a cut stream is an error that resumes next attempt, never a truncated picture
  assert.match(pf, /if \(total && got < total\) throw new Error\("cut at " \+ got\);/);
  // the finished bytes are remembered for the page life — re-renders must not re-pull them over
  // the very link that struggled to deliver them once
  assert.match(PREVIEW, /const resolvedUrls = new Map<string, string>\(\);/);
  assert.match(pf, /rememberResolved\(url, objUrl\);/);
  // the chip names the byte it died at, so the wait visibly advances across attempts
  assert.match(pf, /"⚠ connection dropped at " \+ fmtBytes\(got, total\)/);
  // swirl and chip share one fixed-footprint wait box — retry churn cannot shift the scroll
  assert.match(PREVIEW, /function mkWait\(box: HTMLElement\): HTMLElement/);
  assert.match(CSS, /\.path-full-wait \{ display: inline-flex; flex-direction: column;/);
  assert.match(CSS, /\.path-load-note \{ font-size: 0\.85em;/);
  // no artificial deadline anywhere: patience is the point — only a real error ends an attempt
  assert.doesNotMatch(pf, /AbortController|setTimeout\([^,]*abort/i, "no client-side fetch deadline");
  // the kernel side: /file honors the one suffix form and the federation relay passes 206 through
  assert.match(KERNEL, /re\.match\(r"\^bytes=\(\\d\+\)-\$", \(getattr\(self, "headers", None\) or \{\}\)\.get\("Range"\) or ""\)/);
  assert.match(KERNEL, /self\.send_header\("Content-Range", "bytes %d-%d\/%d" % \(rng, size - 1, size\)\)/);
  assert.match(KERNEL, /hdrs\["Range"\] = _rng/, "the relay forwards the browser's range to the remote");
  assert.match(KERNEL, /re\.match\(r"\^bytes \\d\+-\\d\+\/\\d\+\$", crange\)/, "and mirrors only byte arithmetic back");
});

test("an instant server failure names its reason, and a tap genuinely re-arms", () => {
  // the user 2026-08-16 (fourth report, live on a broken tunnel): retries "failed immediately" with
  // no reason — the image was fine, the LINK to its host was down, and the kernel's 502 body said so
  // ("tunnel to <host> is not answering") while the UI showed a generic "unavailable". And after the
  // budget spent, each tap bought exactly ONE feeble attempt — "a click makes it seemingly not try
  // so hard". The error body's first line now rides the note and the chip verbatim, and a tap
  // refills the auto-retry budget (a human gesture is new information; the kernel-push auto-heal
  // path deliberately does NOT refill, or the bound would be infinite).
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  assert.match(pf, /why = \(\(await r\.text\(\)\) \|\| ""\)\.split\("\\n"\)\[0\]\.slice\(0, 120\);/);
  assert.match(pf, /throw new Error\(why \|\| "http " \+ r\.status\);/);
  assert.match(pf, /: lastErr \|\| "connection dropped"\)/, "the retrying note carries the server's reason");
  assert.match(pf, /: lastErr \? "⚠ " \+ lastErr/, "so does the give-up chip");
  assert.match(pf, /if \(lastErr\.startsWith\("cut at "\)\) lastErr = "";/, "mid-stream cuts narrate via byte progress instead");
});
