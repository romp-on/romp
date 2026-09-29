// The Task tracking master switch's UI (T404 PR 2, the user 2026-09-13): the gear's row at the top of Task tracking, the
// dressing of every control that depends on it (rs-off, one tooltip, inert inputs), the kernel message and the shell's
// message, the mixed mark and the stale map, the two pane scripts' off frame handling and their notice's button, the
// federation set the setting joins, and the shell's body class with its rail rule. Source pins on the executed parts of
// tests/test_task_tracking_switch.py (the kernel) and the served lab (the surface); the pane scripts' frame handling is
// pinned here and executed by the lab's off frame.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const GEAR = read("ui", "webview", "gear.js");
const GEAR_CSS = read("ui", "webview", "gear.css");
const FEED = read("ui", "webview", "feed.ts");
const FLEET = read("ui", "webview", "fleet.ts");
const FED = read("ui", "webview", "federation.ts");
const KERNEL = read("kernel", "kernel.py");

test("the gear: the master row opens Task tracking, checked by default, with the mixed mark and the one sub-copy", () => {
  const tasks = GEAR.slice(GEAR.indexOf("'<div class=rs-pane data-pane=tasks hidden>' +"), GEAR.indexOf("'<div class=rs-pane data-pane=debug hidden>' +"));
  assert.match(tasks, /<div class='rs-sec rs-sec-first'>Task tracking<\/div>" \+\s*\n\s*"<label class='rs-row'><input type=checkbox id=rs-tasktrack checked>" \+\s*\n\s*'<span><b>Task tracking<\/b><span class=rs-mixed hidden><\/span>'/);
  assert.match(tasks, /Off, the judges do not run and spend nothing, the feed and the outline are not shown/);
  assert.ok(tasks.indexOf("id=rs-tasktrack") < tasks.indexOf("<div class=rs-sec>Judges</div>"), "the switch above the Judges");
});

test("the gear: the flip posts setTaskTracking with a stamp and nothing more; the kernel's echo dresses the dependents and tells the shell", () => {
  // round two, medium 4: the gear and the shell went off on the click while the kernel had refused the write
  assert.match(GEAR, /if \(tk\) tk\.addEventListener\('change', function \(\) \{ post\(scoped\(\{ type: 'setTaskTracking', enabled: tk\.checked, gt: gclock\.stamp\('task-tracking'\) \}\)\); \}\);/,
    "the post is gesture-stamped; scoped() adds the machine selector's pick and nothing else (phase two)");
  assert.doesNotMatch(GEAR, /gclock\.stamp\('task-tracking'\) \}\); tellShellTracking/, "no shell message on the click");
  assert.match(GEAR, /if \(!m \|\| m\.type !== 'taskTracking' \|\| typeof m\.on !== 'boolean'\) return;[^\n]*\n\s*if \(tk\) tk\.checked = m\.on;\s*\n\s*dressTracking\(m\.on\);\s*\n\s*tellShellTracking\(m\.on\);/,
    "the echo frame sets the box, dresses, and tells the shell");
  assert.match(KERNEL, /_reply\(client, \{"type": "taskTracking", "on": bool\(enabled\), "gt": stamp\}\)/, "the kernel answers an applied flip on the delivering socket");
  assert.match(GEAR, /'task-tracking': 'Task tracking',/, "the stale toast names the setting (round two, low 1)");
  assert.match(GEAR, /function tellShellTracking\(on\) \{ try \{ \(window\.parent !== window \? window\.parent : window\)\.postMessage\(\{ romp: 'taskTracking', on: !!on \}, '\*'\); \} catch \(e\) \{\} \}/);
  assert.match(GEAR, /'task-tracking': 'setTaskTracking'/, "the stale map names the type (a stood-down gesture re-issues it)");
  assert.match(GEAR, /\['compactSuggest', csg\], \['taskTracking', tk\],/, "the mixed mark rides the generic kernel-setting comparison");
  assert.match(GEAR, /if \(tk\) \{ tk\.checked = v\.taskTracking !== false; dressTracking\(tk\.checked\); \}/, "the fill reads the kernel's answer; absent reads on");
});

test("the gear: dressTracking greys the judge rows, the Outline and Feed pane toggles and the Judging-bands boxes, with the one tooltip and inert inputs", () => {
  assert.match(GEAR, /var TT_OFF_TIP = 'Enable task tracking to use this \(Settings, Task tracking\)\.';/);
  assert.match(GEAR, /var rows = Array\.prototype\.slice\.call\(document\.querySelectorAll\('#rsettings \.rs-pane\[data-pane=tasks\] \.rs-row'\)\)\.filter\(function \(r\) \{ return !r\.querySelector\('#rs-tasktrack, #rs-prstatus'\); \}\);[^\n]*\n\s*\[pn\.fleet, pn\.feed, jix, jtr\]\.forEach/, "the pane's rows but the switch's own and PR status, which governs gh reads");
  assert.match(GEAR, /row\.classList\.toggle\('rs-off', !on\);\s*\n\s*if \(on\) row\.removeAttribute\('title'\); else row\.title = TT_OFF_TIP;\s*\n\s*Array\.prototype\.forEach\.call\(row\.querySelectorAll\('input, select, button'\), function \(c\) \{ c\.disabled = !on; \}\);/);
  // the Automation rows say what waits while the switch is off, and what still goes out
  assert.match(GEAR, /<span class=rs-note id=rs-autonudge-tt hidden>While task tracking is off, the goal nudges wait: the judges no longer update the goals they are about\. Only the reminders about unanswered messages from other sessions still go out\.<\/span>/);
  assert.match(GEAR, /<span class=rs-note id=rs-suggestcompact-tt hidden>Task tracking off changes nothing here: the suggestion reads the context size, not the judges\.<\/span>/);
  assert.match(GEAR, /if \(an1\) an1\.hidden = !!on;\s*\n\s*if \(sc1\) sc1\.hidden = !!on;/);
  assert.match(GEAR_CSS, /#rsettings \.rs-row\.rs-off, #rsettings \.rs-jrow\.rs-off \{ color: var\(--text-faint, #6e7681\); cursor: default; \}/);
  assert.match(GEAR_CSS, /#rsettings \.rs-row\.rs-off > \*, #rsettings \.rs-jrow\.rs-off > \* \{ pointer-events: none; \}/, "the row keeps its hover (the tooltip); its controls take no pointer");
});

test("the panes: the feed frame's off flag shows the kernel's notice in place of the list, drops the loader, and applies nothing; the button opens the settings through openGear, or the dashboard standalone", () => {
  for (const [src, list] of [[FEED, "feed-list"], [FLEET, "fleet-list"]] as const) {
    assert.match(src, new RegExp('const ttOff = document\\.getElementById\\("tt-off"\\), ttList = document\\.getElementById\\("' + list + '"\\);\\s*\\n\\s*if \\(ttOff\\) ttOff\\.hidden = !m\\.off;\\s*\\n\\s*if \\(ttList\\) ttList\\.hidden = !!m\\.off;\\s*\\n\\s*if \\(m\\.off\\) \\{'));
    // round two, medium 2: the bare post to the pane's own window reached nothing on the standalone page, the only place the notice shows
    assert.match(src, /document\.getElementById\("tt-off-btn"\)\?\.addEventListener\("click", \(\) => \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(!openGear\(window, \{ tab: "tasks" \}\)\) window\.location\.assign\("\/" \+ window\.location\.search \+ "#settings=tasks"\);\s*\n\s*\}\);/);
    assert.doesNotMatch(src, /postMessage\(\{ romp: "openSettings", tab: "tasks" \}/, "no hand-rolled relay past gear-host");
  }
  // round two, medium 1: the loader sat over the notice (the outline's to forever, _keepLoader re-asserting it past the failsafe)
  assert.match(FEED, /if \(m\.off\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*document\.getElementById\("pane-spin"\)\?\.classList\.add\("gone"\);\s*\n(?:\s*\/\/[^\n]*\n)*\s*mirrorBadges\(\[\], Array\.isArray\(m\.clearNotices\) \? m\.clearNotices : \[\], Array\.isArray\(m\.sdkNotices\) \? m\.sdkNotices : \[\], Array\.isArray\(m\.syncNotices\) \? m\.syncNotices : \[\], \{ cardsUnknown: true \}\);[\s\S]{0,260}?return;\s*\n\s*\}/,
    "the feed's off branch drops the loader, mirrors the notice rings to the shell's bell (round four: the error center is not task tracking), and returns");
  assert.match(FLEET, /if \(m\.off\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*offNotice = true;\s*\n\s*document\.getElementById\("pane-spin"\)\?\.classList\.add\("gone"\);\s*\n\s*return;\s*\n\s*\}\s*\n\s*offNotice = false;/,
    "the outline says the notice stands in, never that it is loaded (round three, low 2): a later frame with no ledgers brings the loader back");
  assert.match(FLEET, /if \(loaded\) \{ clearInterval\(_keepLoader\); return; \}\s*\n\s*if \(offNotice\) return;/, "_keepLoader stands down while the notice shows and resumes when it goes");
  assert.doesNotMatch(FLEET, /if \(m\.off\) \{[^}]*loaded = true;/, "the off frame never claims loaded");
  assert.match(FLEET, /^import \{ openGear \} from "\.\/gear-host";$/m);
  assert.ok(FEED.indexOf("if (m.off) {") < FEED.indexOf("if (freezeKey || tabScopeKey) { pendingFeedPayload = m;"), "the off check precedes the hover-freeze queue: an off frame is never queued as a payload");
  assert.ok(FLEET.indexOf("if (m.off) {") < FLEET.indexOf("if (m.views && typeof m.views === \"object\") fleetViews"), "…and precedes the outline's reads of the payload");
});

test("the landing opens the settings named in the URL's hash, once, and drops the hash (a standalone page's road to Task tracking)", () => {
  assert.match(KERNEL, /if\(location\.hash\.indexOf\('#settings'\)===0\)\{var sh=location\.hash\.slice\(9\);if\(sh\.charAt\(0\)==='='\)sh=sh\.slice\(1\);/);
  assert.match(KERNEL, /history\.replaceState\(null,'',location\.pathname\+location\.search\);/);
  assert.match(KERNEL, /var so=function\(\)\{window\.__rompOpenSettings\(sh\|\|undefined\);\};if\(document\.readyState==='complete'\)setTimeout\(so,0\);else window\.addEventListener\('load',so\);/);
});
test("federation: setTaskTracking is a KERNEL_SETTING (one value across machines, queued per host, flushed on reconnect)", () => {
  const set = FED.slice(FED.indexOf("const KERNEL_SETTING = new Set(["), FED.indexOf("]);", FED.indexOf("const KERNEL_SETTING = new Set([")));
  assert.match(set, /"setTaskTracking"/);
});

test("the shell: body.no-task-tracking hides the Outline and Feed buttons and phone tabs, closes an open pane of theirs, and follows /version and the gear's message", () => {
  assert.match(KERNEL, /body\.no-task-tracking \.rail-btn\[data-pane=\\"fleet\\"\],body\.no-task-tracking \.rail-btn\[data-pane=\\"feed\\"\],body\.no-task-tracking #mtabs button\[data-pane=\\"fleet\\"\],body\.no-task-tracking #mtabs button\[data-pane=\\"feed\\"\]\{display:none\}/, "quoted values: the rail-order test indexes the first data-pane=fleet in the page, which must be the rail's button, not a rule");
  assert.match(KERNEL, /function taskTracking\(\)\{return window\.__rompTaskTracking!==false;\}/, "absent reads on");
  assert.match(KERNEL, /var tt=taskTracking\(\);\s*\n\s*document\.body\.classList\.toggle\('no-task-tracking',!tt\);/);
  assert.match(KERNEL, /if\(!tt\)\{\['fleet','feed'\]\.forEach\(function\(k\)\{if\(po\[k\]\)po\[k\]=false;\}\);/, "an open Outline or Feed pane closes on the same apply");
  assert.match(KERNEL, /if\(typeof v\.taskTracking==='boolean'\)\{window\.__rompTaskTracking=v\.taskTracking;if\(window\.__rompApplyPanes\)window\.__rompApplyPanes\(\);\}/, "noteVersion: the boot read and the update poll");
  assert.match(KERNEL, /if\(m\.romp==='taskTracking'&&typeof m\.on==='boolean'\)\{window\.__rompTaskTracking=m\.on;if\(window\.__rompApplyPanes\)window\.__rompApplyPanes\(\);\}/, "the gear's flip, ahead of the next /version read");
  assert.match(KERNEL, /window\.__rompApplyPanes=function\(\)\{apply\(\);\};/);
});

test("the kernel's pages carry the notice: one sentence, the button, hidden while on", () => {
  assert.match(KERNEL, /def _tt_off_notice\(pane\):/);
  assert.match(KERNEL, /Task tracking is off, so there is no %s to show: turn it on in Settings, Task tracking\./);
  assert.match(KERNEL, /<button id=tt-off-btn type=button class=notice-act>Open Task tracking settings<\/button>/);
  assert.match(KERNEL, /_tt_off_notice\("feed"\) \+ '<div id="feed-head">/);
  assert.match(KERNEL, /_tt_off_notice\("outline"\), _pane_spin\("fleet-list"\)/);
});
