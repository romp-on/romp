// The network popover lists PREVIOUSLY ATTACHED hosts as persistent rows, and every status/control
// explains itself on hover (the user 2026-07-22: "I shouldn't have to do anything from the command
// line ... all of the information can be learned from hovering over tooltips within the application").
//
// There are TWO copies of this popover — the web shell's (_LANDING_REMOTES_JS, inline in kernel.py)
// and the VS Code strip's (strip.ts). They must stay in step, so both are pinned here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");

test("web popover renders remembered hosts with re-attach + forget", () => {
  // pmode rides along as a PARAM: it is computed in refresh()'s callback, and reading it as a free
  // variable in render() threw ReferenceError on every draw (see tests/test_remotes_panel_render.py)
  assert.match(KERNEL, /function render\(ts,known,pmode,via,rholds,tiers\)/, "render takes the known list + pmode + via-reach + remote holds + peer tiers");
  // refresh passes them through — via the cached-args form, which also lets a pairs answer repaint
  assert.match(KERNEL, /_lastArgs=\[ts,\(d&&d\.known\)\|\|\[\],pmode,\(d&&d\.viaReach\)\|\|\[\],\(d&&d\.remoteHolds\)\|\|\[\],\(d&&d\.peerTiers\)\|\|\{\}\];/);
  assert.match(KERNEL, /if\(!back\.hidden\)\{render\.apply\(null,_lastArgs\);refreshPairs\(\);\}/);
  assert.match(KERNEL, /Held for approval elsewhere/, "remote holds get their own section");
  assert.match(KERNEL, /rnet-from/, "the add box offers which machine owns the tunnel (attach-on-behalf)");
  assert.match(KERNEL, /Reachable via relay/, "via-reach hosts get their own section (trust-by-origin rows)");
  assert.match(KERNEL, /Previously attached/);
  assert.match(KERNEL, /data-ra=/, "a Re-attach control keyed by host");
  assert.match(KERNEL, /data-fg=/, "a Forget control keyed by host");
  assert.match(KERNEL, /\/tunnels\/forget/, "Forget hits the kernel route");
  // the empty state must not swallow the remembered rows
  assert.match(KERNEL, /if\(!ts\.length&&!known\.length\)/);
});

test("VS Code strip renders remembered hosts with re-attach + forget", () => {
  assert.match(STRIP, /function renderList\(ts: any\[\], known: any\[\] = \[\]\)/);
  assert.match(STRIP, /lastList = \[ts, \(d && d\.known\) \|\| \[\]\];\s*\n\s*renderList\(lastList\[0\], lastList\[1\]\);/);
  assert.match(STRIP, /Previously attached/);
  assert.match(STRIP, /"Re-attach"/);
  assert.match(STRIP, /"Forget"/);
  assert.match(STRIP, /\/tunnels\/forget/);
  assert.match(STRIP, /if \(!ts\.length && !known\.length\)/);
});

test("both copies explain every status on hover, with the same wording", () => {
  // one TIP map per copy, covering every status the LBL map can show
  for (const [name, src] of [["kernel", KERNEL], ["strip", STRIP]] as const) {
    for (const status of ["up", "authorizing", "connecting", "no-kernel", "down", "error"]) {
      assert.ok(new RegExp(`['"]?${status}['"]?\\s*:`).test(src), `${name}: TIP covers ${status}`);
    }
    // the wording that makes each status actionable
    assert.ok(src.includes("ssh tunnel is open"), `${name}: 'up' says what connected means`);
    assert.ok(src.includes("no romp kernel is answering"), `${name}: 'no-kernel' says what to do`);
    // 'down' says the retrying is UNCONDITIONAL again (the user 2026-07-29) — an attached host is a
    // standing instruction to hold the link, so the copy promises it plainly instead of hedging.
    assert.ok(src.includes("romp keeps retrying on its own"), `${name}: 'down' promises the retry`);
    assert.ok(src.includes("waiting longer between tries"), `${name}: ...and says the backoff widens`);
    assert.ok(!src.includes("still retrying for now"), `${name}: the hedged budget copy is gone`);
  }
});

test("both copies explain the destructive/confusing controls on hover", () => {
  for (const [name, src] of [["kernel", KERNEL], ["strip", STRIP]] as const) {
    // Push: the two things that actually surprise people — uncommitted work isn't sent, and it refuses
    assert.ok(src.includes("Uncommitted local edits are not sent"), `${name}: Push warns about uncommitted work`);
    // Detach: says the host is REMEMBERED, so it doesn't read as destructive
    assert.ok(src.includes("previously-attached host"), `${name}: Detach says the host is remembered`);
    // Forget: says it doesn't touch the host itself
    assert.ok(src.includes("does not touch the host itself"), `${name}: Forget scopes itself`);
    // Re-attach: says the trust level comes back
    assert.ok(src.includes("restoring its remembered trust level"), `${name}: Re-attach mentions trust restore`);
  }
});

// Reconnect NEVER gives up (the user 2026-07-29, reversing the bounded budget of 2026-07-22). The
// panel still has to SAY what is happening — the 07-22 objection was a silent forever-retry that looked
// identical to an idle row — so a down row names the next dial and offers to skip the wait.
test("a down row says romp is still dialing, when next, and offers to try now", () => {
  for (const [name, src] of [["web", KERNEL], ["strip", STRIP]] as const) {
    assert.doesNotMatch(src, /gave-up/, `${name}: the give-up state is gone`);
    assert.doesNotMatch(src, /no longer dialing it in the background/, `${name}: and its copy with it`);
  }
  assert.match(KERNEL, /next try in /, "the row counts down to the next dial");
  assert.match(KERNEL, /romp keeps dialing '\+t\.host\+' on its own/, "and says it is unconditional");
  assert.match(KERNEL, /data-ra=\\"'\+t\.host\+'\\" title=/, "the web row offers the re-dial action");
  // per-state name (the user 2026-08-23): beside the countdown it reads as skip-the-wait (Try now);
  // on a no-kernel row there is no countdown and it sat beside Start as a lookalike — there it is
  // named for what it does (Re-dial). Same button, same action, one wording rule.
  assert.match(KERNEL, /\(wedged\?'Re-dial':'Try now'\)\+'<\/button>'/, "named for what it does, per state");
  assert.match(KERNEL, /var wedged=\(t\.status==='no-kernel'\);/);
  assert.match(KERNEL, /s!=='no-kernel';/, "web busyStatus no longer knows the state");
  assert.match(STRIP, /s !== "no-kernel";/, "strip busy() no longer knows it");
  assert.match(KERNEL, /t\.status==='down'\)\?'background:#8a8a8a'/, "web dot is gray");
  assert.match(STRIP, /t\.status === "down"\) \? "#8a8a8a"/, "strip dot is gray");
});

test("the check-in control is named for what it does, not for what it is not", () => {
  // It publishes THIS machine to the remote. Called "keep connected" it read as the reconnect setting so
  // plainly that the tooltip had to spend a sentence denying that, and the user still went hunting for a
  // reconnect switch that was never there. The label now says the direction out loud.
  assert.doesNotMatch(KERNEL, /this attach auto-reconnects/);
  assert.doesNotMatch(KERNEL, /keep connected<\/label>/, "the misleading label is gone");
  assert.match(KERNEL, /Share my sessions there<\/label>/, "it says which way the sharing goes");
  assert.match(KERNEL, /Publish this machine to '\+t\.host/, "and the tooltip leads with that");
});

test("the panel opens on the host list, with adding a host one click away", () => {
  // Progressive disclosure (CLAUDE.md): the panel's subject is the machines you have, so the list comes
  // first and the always-on ssh-config dropdown becomes a + that opens an input (the user 2026-07-22).
  assert.match(KERNEL, /<button id=rnet-plus class=rnet-plus>\+ Add a host<\/button>/);
  assert.match(KERNEL, /<div class=rnet-add id=rnet-add hidden>/, "the form starts collapsed");
  assert.doesNotMatch(KERNEL, /<select id=rnet-host>/, "no permanent dropdown");
});

test("the host box takes any ssh target, with ~/.ssh/config only suggesting", () => {
  // ssh reaches a machine whether or not it has a config entry, so the config supplies completions and
  // romp's own remembered hosts supply the rest. A <datalist> is exactly that: suggestions over free text.
  assert.match(KERNEL, /<input id=rnet-host list=rnet-hosts placeholder='hostname or user@host'/);
  assert.match(KERNEL, /<datalist id=rnet-hosts><\/datalist>/);
  assert.match(KERNEL, /var h=\(hostIn\.value\|\|''\)\.trim\(\);/, "the typed value is what gets attached");
  // and a typo has to be reported, which was impossible to make when the only way in was a dropdown
  assert.match(KERNEL, /alert\('Could not attach '\+h/, "a refused attach says so");
});

test("a host row wraps on a phone so Detach is reachable", () => {
  // the row packs trust + keep + Push + Start + Detach, all flex:0 0 auto; with no wrap it overflowed a
  // ~360px panel and Detach sat off-screen, so mobile looked like it offered only Attach
  assert.match(KERNEL, /@media \(pointer:coarse\),\(max-width:560px\)\{/);
  assert.match(KERNEL, /\.rnet-row\{flex-wrap:wrap\}/);
  assert.match(KERNEL, /\.rnet-row \.nm\{flex:1 0 100%\}/);
  // The settings line wraps UNCONDITIONALLY, not just under that query: both of its controls carry
  // white-space:nowrap, and 94% of a small desktop window is narrow enough to cut the second one off.
  assert.match(KERNEL, /\.rnet-row2\{display:flex;align-items:center;flex-wrap:wrap;/);
});

test("both copies fail LOUDLY when the tunnels refresh throws", () => {
  // The web popover used to `.catch(function(){schedule(3000);})` — swallowing every error and just
  // rescheduling. When the refresh threw, render() never ran and the list sat empty, so a BROKEN panel
  // looked exactly like one with no hosts attached, surviving any number of reloads and kernel restarts
  // (the user 2026-07-22, who could not tell the two apart for hours). strip.ts already did this right.
  assert.doesNotMatch(KERNEL, /\}\)\.catch\(function\(\)\{schedule\(3000\);\}\);\}/, "no silent swallow");
  assert.match(KERNEL, /console\.error\('romp: remotes refresh failed'/, "names it in the console");
  assert.match(KERNEL, /Could not load remotes: /, "...and in the panel itself");
  // the VS Code copy's existing loud path stays
  assert.match(STRIP, /Couldn't reach the kernel/);
  assert.match(STRIP, /Fail loudly/);
});

test("the How-it-works fold explains the security posture and every per-host setting", () => {
  // A dropdown reading "directed" cannot say that it is a boundary rather than a preference, and the
  // panel is where the decision is actually made, so the explanation belongs here and not only in the
  // guide (the user 2026-07-23). Kept faithful to docs/guide.md, which stays authoritative.
  const i = KERNEL.indexOf("<div class=rnet-sub id=rnet-sub hidden>");
  assert.ok(i > 0, "the fold exists");
  const fold = KERNEL.slice(i, KERNEL.indexOf("</div>", KERNEL.indexOf("Share my sessions there</b>", i)));
  // 1. what the connection is worth: what protects it, and what it cannot protect against
  assert.match(fold, /never crosses the network in the open/, "what the tunnel + token buy you");
  assert.match(fold, /Root on a machine can read any /, "...and the limit of that");
  assert.match(fold, /including that token, so federate boxes whose root you trust/);
  // 2. each mail level, with WHEN to use it (a definition alone does not settle the choice)
  assert.match(fold, /<b>trusted<\/b> is full two-way postal, for a machine you control/);
  // (the kernel source splits this sentence across adjacent python string literals, so match the halves)
  assert.match(fold, /<b>directed<\/b> lets you send work to its sessions/);
  assert.match(fold, /shared compute: you can drive the box, it cannot drive you/, "when to reach for it");
  assert.match(fold, /<b>isolated<\/b> is no postal at all/);
  assert.match(fold, /It is the default/, "which one you get if you never touch it");
  // 3. the check-in box, which points the OTHER way and is the easiest to misread
  assert.match(fold, /<b>Share my sessions there<\/b> publishes this machine to that host/);
  assert.match(fold, /Leave it off for a box you only want to watch/, "when NOT to use it");
});

test("the fold stays folded: the panel still opens on one gist line", () => {
  // Progressive disclosure (CLAUDE.md) — the explanation got longer, so this matters more, not less.
  assert.match(KERNEL, /<div class=rnet-sub id=rnet-sub hidden>/, "hidden until asked for");
  assert.match(KERNEL, /<div class=rnet-gist>Another machine's romp sessions, in your tabs and timeline\./);
  assert.match(KERNEL, /\.rnet-sub p\{margin:0 0 7px\}/, "and its paragraphs are spaced for 11.5px text");
});
