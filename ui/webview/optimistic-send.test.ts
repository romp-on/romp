// A composer send clears the box instantly, but the message only reappears in the chat once the kernel
// round-trips it back as its own provisional. Sending to a busy/slow thread, that provisional could briefly
// VANISH in the server-side echo→landed gap — so a just-sent message looked lost for a beat (the user
// 2026-07-15: "showing up as a provisional message ... and it disappeared"). Fix: a CLIENT-side optimistic
// bubble injected at the tail the moment you hit Enter, re-asserted on every push until the kernel's payload
// demonstrably carries the message, then retired.
//
// Reshaped 2026-08-09 (the user, who watched sends vanish again and suspected the fix was hollow): the
// retire test was a bare substring scan, so (A) a resend — or any short message that substrings an older
// bubble — retired its own entry in the very call that created it and showed nothing, and (B) the kernel's
// own PROVISIONAL copy (queued bubble / "echo:" atom) counted as landed and deleted the entry one-way, so
// when that provisional blinked in the echo→landed handoff nothing was left to cover the gap. Now: a
// per-send `base` count makes only NEW landed user atoms retire it, kernel provisionals merely SUPPRESS
// injection for the push they're visible on, and the TTL stays the backstop.
// render.ts has import-time DOM side effects → source pins + an executed replica of the reconcile decision
// (user-img-dedup.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the plain send registers an optimistic bubble; follow-up/quote sends keep their own kernel echo", () => {
  // only the PLAIN sendMessage branch registers — a citation follow-up/quote has its own kernel-side
  // echo (the branch lives in routeUserMessage since the staged flush, 2026-08-15)
  assert.match(RENDER, /else \{ vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text \}\); registerOptimistic\(sid, text, imgPaths\); \}/);
  // registerOptimistic shows it NOW (before any push) via reconcile + appendActive
  assert.match(RENDER, /function registerOptimistic\(id: string, text: string, imgPaths\?: string\[\]\): void/);   // + the dragged-image paths → echo thumbnails (2026-08-25)
  assert.match(RENDER, /if \(v\) v\.stale = true;\s*\n\s*if \(id === activeId\) \{\s*\n\s*appendActive\(\);/);
});

test("your OWN send always reveals itself — the >80px stick rule never hides it below the fold", () => {
  // appendActive keeps the viewport still when the reader is scrolled up; a send made from there
  // painted below the fold and looked like it never appeared (the user 2026-08-09). Enter = intent
  // to see the message, so registerOptimistic scrolls to the bottom, once, at send time.
  assert.match(RENDER, /appendActive\(\);[\s\S]{0,500}if \(content\) content\.scrollTop = content\.scrollHeight;\s*\n\s*\}\s*\n\}/);
});

// The reconcile's two IN-PLACE tail mutations — merging into an existing queued group (a busy session
// already showing queued messages) and pop+push on a repeat send — leave s.events.length unchanged, so
// syncView's no-op fast path (rendered === len && !stale) concluded "nothing changed" and skipped the
// repaint: the bubble waited for the NEXT kernel push, a visible beat after Enter (the user 2026-08-07).
// Only the length-growing case (first send, bare tail) painted on the keystroke. registerOptimistic now
// marks the view stale before appendActive, so every send takes the stale window re-render immediately.
test("a send paints on ITS OWN keystroke even when the tail mutates in place (no length change)", () => {
  // the stale mark sits between the reconcile and the repaint, so appendActive can't hit the fast path
  assert.match(RENDER, /reconcileOptimistic\(s\);[\s\S]{0,700}const v = views\.get\(id\);\s*\n\s*if \(v\) v\.stale = true;/);
  // the fast path it defeats keys on length + staleness — stale must veto the skip
  assert.match(RENDER, /if \(v\.rendered === len && !v\.stale && v\.el\.childNodes\.length > 0\) return v;/);
  // executed replica: the fast-path predicate must not skip once the view is marked stale, even though
  // the in-place merge keeps the length equal to what was last rendered
  const skips = (rendered: number, len: number, stale: boolean, children: number) =>
    rendered === len && !stale && children > 0;
  assert.equal(skips(50, 50, false, 50), true, "length-neutral mutation without the mark: skipped (the bug)");
  assert.equal(skips(50, 50, true, 50), false, "the stale mark forces the repaint on the same keystroke");
});

test("every push entry point re-asserts (or retires) the optimistic tail", () => {
  // update(), chatTail(), and upsert() each call reconcileOptimistic after setting s.events
  const calls = RENDER.match(/reconcileOptimistic\(s\);/g) || [];
  assert.ok(calls.length >= 4, "reconcile wired into send + all three push paths, got " + calls.length);
});

test("retire needs a NEW landed atom (base count); kernel provisionals only suppress", () => {
  // base is stamped on the first reconcile after the send: pre-existing matches are background
  assert.match(RENDER, /arr\.push\(\{ text, ts: Date\.now\(\), base: -1, imgPaths \}\);/);
  assert.match(RENDER, /for \(const p of list\) if \(p\.base < 0\) p\.base = landedCount\(p\.text\);/);
  // a landed atom is a user event WITHOUT the backend's "echo:" uuid prefix
  assert.match(RENDER, /&& !String\(\(e as any\)\.uuid \|\| ""\)\.startsWith\("echo:"\)\)\.length;/);
  // retire = TTL or growth past base; suppression is a separate, non-destructive filter
  assert.match(RENDER, /const keep = list\.filter\(\(p\) => now - p\.ts < OPT_TTL_MS && landedCount\(p\.text\) <= p\.base\);/);
  assert.match(RENDER, /const inject = keep\.filter\(\(p\) => !shownProvisional\(p\.text\)\);/);
});

// The optimistic echo rides the QUEUED idiom (the user 2026-07-16): to the reader an unconfirmed send and a
// queued one are the same state, so they wear the same dashed bubble — and the look then only ever moves
// provisional→settled. It first shipped as a 0.6-opacity SOLID bubble, which invented a third look and made a
// queued send flip solid→dashed (backwards, as if it had un-landed).
test("an optimistic echo is a tail-appended, kernel-invisible QUEUED event — never a solid user bubble", () => {
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(RENDER, /const OPT_PREFIX = "optimistic:";/);
  assert.match(RENDER, /const mk = \(p: \{ text: string; imgPaths\?: string\[\] \}\) => \(\{ md: p\.text, optimistic: true, cancelable: false, imgPaths: p\.imgPaths \}\);/);   // the echo carries its dragged-image paths (2026-08-25)
  // stale ones pop cheaply off the end (always tail-appended)
  assert.match(RENDER, /while \(s\.events\.length && isOptimistic\(s\.events\[s\.events\.length - 1\]\)\) s\.events\.pop\(\);/);
  // the abandoned dim/pending idiom is FULLY gone: render, guard fields, and stylesheet — the last
  // leftovers (a `!e.pending` guard on a field no event carries, and a stylesheet paragraph describing
  // the deleted rendering as if it shipped) misdirected the 2026-08-09 bug hunt and are pinned out
  assert.doesNotMatch(RENDER, /ev\.pending/);
  assert.doesNotMatch(RENDER, /!e\.pending/);
  assert.doesNotMatch(CSS, /\.user-bubble\.pending \{/);
  assert.doesNotMatch(CSS, /renders EXACTLY like a landed/);
  // it reuses the chat's ONE provisional look rather than adding CSS of its own
  assert.match(CSS, /\.queued-bubble \{[\s\S]*?border: 1px dashed/);
});

test("nothing known-queued → a BARE dashed bubble (no 'N queued' header we can't back)", () => {
  assert.match(RENDER, /s\.events\.push\(\{ kind: "queued", bare: true, texts: inject\.map\(mk\), uuid: OPT_PREFIX \+ inject\[0\]\.ts \}\);/);
  assert.match(RENDER, /if \(!ev\.bare\) \{/, "renderQueued skips the header for a bare group");
});

test("something IS queued → ours merges into that group, counted under its header", () => {
  assert.match(RENDER, /s\.events\[qj\] = \{ \.\.\.q, texts: \[\.\.\.q\.texts, \.\.\.inject\.map\(mk\)\] \};/);
  // and the extension is undone before the counts run, so reconcile only ever reads kernel truth
  assert.match(RENDER, /if \(q\.texts\.some\(\(t\) => t\.optimistic\)\) s\.events\[qi\] = \{ \.\.\.q, texts: q\.texts\.filter\(\(t\) => !t\.optimistic\) \};/);
});

test("an unconfirmed echo gets its own tooltip and never an ✕ (nothing confirmed to cancel)", () => {
  assert.match(RENDER, /if \(t\.optimistic\) bubble\.title = "sent just now — romp hasn't confirmed the session has it yet";/);
  // cancelable:false → the ✕ branch (which needs cancelable AND an idx/park handle) can't fire for ours
  assert.match(RENDER, /if \(t\.cancelable && \(t\.idx !== undefined \|\| t\.park !== undefined\)\) \{/);
});

test("chatTail speaks the KERNEL's coordinates — the injected tail is not part of its space", () => {
  // counting the injected bubble in the gap check masked a genuine 1-event desync (the repair never
  // fired) and let a delta land PAST the bubble, freezing it into resident events as fake history
  assert.match(RENDER, /let kernelLen = s\.events\.length;\s*\n\s*while \(kernelLen > 0 && isOptimistic\(s\.events\[kernelLen - 1\]\)\) kernelLen--;/);
  assert.match(RENDER, /if \(from > kernelLen\) \{/);
});

// Executed replica of reconcileOptimistic's decision, synced to the reshaped semantics: three outcomes
// per entry per push — inject (payload has nothing), suppress (a kernel PROVISIONAL is visible: its
// queued bubble or its "echo:" atom), retire (a NEW landed user atom beyond base, or the TTL).
test("reconcile: inject on nothing, suppress on kernel provisionals, retire only on NEW landings", () => {
  type Ev = { kind: string; md?: string; uuid?: string; texts?: { md: string }[] };
  const OPT_TTL_MS = 20_000, OPT_TAIL_SCAN = 30;
  type P = { text: string; ts: number; base: number };
  const reconcile = (events: Ev[], list: P[], now: number) => {
    const tail = events.slice(-OPT_TAIL_SCAN);
    const landedCount = (t: string) => tail.filter((e) =>
      e.kind === "user" && typeof e.md === "string" && e.md.includes(t)
      && !String(e.uuid || "").startsWith("echo:")).length;
    const shownProvisional = (t: string) => tail.some((e) =>
      (e.kind === "queued" && Array.isArray(e.texts) && e.texts.some((x) => typeof x.md === "string" && x.md.includes(t))) ||
      (e.kind === "user" && typeof e.md === "string" && String(e.uuid || "").startsWith("echo:") && e.md.includes(t)));
    for (const p of list) if (p.base < 0) p.base = landedCount(p.text);
    const keep = list.filter((p) => now - p.ts < OPT_TTL_MS && landedCount(p.text) <= p.base);
    return { keep, inject: keep.filter((p) => !shownProvisional(p.text)) };
  };
  const T0 = 1_000_000;
  const fresh = (): P[] => [{ text: "continue", ts: T0, base: -1 }];

  // gap: the payload carries nothing for it → keep AND inject
  let r = reconcile([{ kind: "assistant", md: "working on the prior turn" }], fresh(), T0 + 500);
  assert.equal(r.inject.length, 1);

  // DEFECT A (the resend): an OLDER identical message sits in the tail — base counts it as background,
  // so the new send still injects instead of retiring itself in the call that created it
  r = reconcile([{ kind: "user", md: "continue", uuid: "u-old" }], fresh(), T0 + 500);
  assert.equal(r.inject.length, 1, "a resend must still show its own bubble");

  // …and the same protection for a short message that substrings an older bubble
  r = reconcile([{ kind: "user", md: "test the continue button", uuid: "u-old" }], fresh(), T0 + 500);
  assert.equal(r.inject.length, 1, "substring-of-history must not count as landed");

  // kernel shows its QUEUED bubble → suppressed for this push, but NOT retired…
  const p = fresh();
  r = reconcile([{ kind: "queued", texts: [{ md: "continue" }] }], p, T0 + 500);
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 0, "no double render beside the kernel's own copy");
  // …same for the kernel's unlanded echo atom (uuid keeps the backend's echo: prefix)
  r = reconcile([{ kind: "user", md: "continue", uuid: "echo:abc123" }], p, T0 + 800);
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 0);
  // DEFECT B (the flash-out): the provisional blinks away in the echo→landed handoff — the entry
  // survived the suppression, so ours steps straight back in and the message never disappears
  r = reconcile([{ kind: "assistant", md: "…" }], p, T0 + 1_100);
  assert.equal(r.inject.length, 1, "the kept entry covers the kernel's own gap");
  // the real landing (a user atom with a real uuid, beyond base) finally retires it
  r = reconcile([{ kind: "user", md: "continue", uuid: "u-new" }], p, T0 + 1_400);
  assert.equal(r.keep.length, 0, "a NEW landed atom is the one retire event");

  // TTL backstop: nothing ever surfaced, but past the window we stop asserting a possibly-dropped send
  r = reconcile([{ kind: "assistant", md: "…" }], fresh(), T0 + OPT_TTL_MS + 1);
  assert.equal(r.keep.length, 0);
});

test("the echo renders dragged-image THUMBNAILS — composer → provisional → landed, one continuum", () => {
  // the user 2026-08-25: the composer showed the thumbnail, the provisional dropped to path-only
  // text, the landing brought the thumbnail back — a flash in the middle. The echo now carries the
  // send's image paths and renders them through the LANDED form's own component (userImage with the
  // exact "path:" shape), so buildPathImg's (sid,path)-keyed cache serves the landed bubble the same
  // bytes and the reconcile swap never re-fetches or flickers.
  assert.match(RENDER, /if \(t\.imgPaths && t\.imgPaths\.length\) \{\s*\n\s*for \(const ip of t\.imgPaths\) bubble\.appendChild\(userImage\(\{ src: "path:" \+ ip, path: ip \}, true\)\);/);
  // the paths ride the send at every register site (deliver, staged flush, the provisional hold)
  assert.match(RENDER, /routeUserMessage\(activeId, text, cites, attached\.filter\(\(p\) => previewKind\(p\) === "img"\)\);/);
  // …and ONLY image-kind attachments mint thumbs — a dropped .csv stays the path text it always was
  assert.doesNotMatch(RENDER, /registerOptimistic\(sid, text, attached\)/);
});

test("the landing SWAP repaints even when it replaces the echo 1:1 — no lingering dashed bubble", () => {
  // upsert hands reconcileOptimistic a FRESH events array, and a landing frame that nets zero count
  // change (its user atom in, our bubble out) left syncView's rendered===len fast path skipping the
  // swap — the dashed echo lingered past its own landing until some later push (the 2026-08-25
  // continuity harness caught it). The per-sid signature survives the frame and marks the view stale
  // exactly when the visible echo set changes; the pop-and-reinject-same pass stays a no-op.
  assert.match(RENDER, /const echoShownSig = new Map<string, string>\(\);/);
  assert.match(RENDER, /if \(\(echoShownSig\.get\(s\.id\) \|\| ""\) !== sig\) \{/);
  assert.match(RENDER, /if \(sig\) echoShownSig\.set\(s\.id, sig\); else echoShownSig\.delete\(s\.id\);/);
  const fn = RENDER.split("function reconcileOptimistic(")[1].split("\nfunction ")[0];
  const settles = (fn.match(/settle\(/g) || []).length;
  assert.ok(settles >= 3, "every exit settles the signature (early returns included), got " + settles);
});
