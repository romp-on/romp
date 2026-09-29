// A comment thread's mail is off until it is broken out (T356, the user 2026-09-11): the thread's whole surface is
// the comment popover, so the popover says so; the tab hover and the Sessions pane show a session's mail state, so
// a promoted thread's flip shows where a session is looked at; the kernel's frames carry the effective state.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const COMMENTS = ui("webview", "comments.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const POSTAL = fs.readFileSync(path.resolve(process.cwd(), "..", "postal", "postal_service.py"), "utf8");

test("the popover says the thread's mail is off, and the promoted view says it is on now", () => {
  assert.match(COMMENTS, /mailOff\?: boolean;/, "the frame's field on the thread type");
  // the user's ruling (2026-09-11, 3:05 PM PT): the comment box says nothing about mail being off; a line only for held mail
  assert.doesNotMatch(RENDER, /Mail off: this thread neither sends nor receives peer mail/);
  assert.match(RENDER, /if \(th && th\.mailOff && \(th\.heldMail \|\| 0\) > 0\) \{[\s\S]{0,700}?mail\.textContent = held \+ \(held === 1 \? " message waits in its box and lands" : " messages wait in its box and land"\) \+ " once it is broken out and its mail is on\.";/);
  assert.doesNotMatch(RENDER, /" at the break-out\."/, "under an isolating master the broken-out session's mail stays off, and so does the held mail");
  assert.match(RENDER, /note\.textContent = "The discussion continues there\.";\s*\n\s*pop\.appendChild\(note\);[\s\S]{0,600}mailOn\.textContent = !th\.mailOff[\s\S]{0,80}\? "Its mail is on now: peers can reach it and it can send\."/,
               "said once, in the promoted view, from the effective state");
  assert.match(CSS, /\.cmt-note\.cmt-mail \{ opacity: 0\.6; font-size: 0\.86em; \}/);
  // the follow-up: the promoted line reads the EFFECTIVE state (a mailbox toggled off since says so), and both lines
  // count the mail held in the box
  assert.match(COMMENTS, /heldMail\?: number;/);
  assert.match(RENDER, /mailOn\.textContent = !th\.mailOff\s*\n\s*\? "Its mail is on now[^\n]*\n\s*: th\.mailOffWhy === "unreadable" \? "Its mail is held: this session's record cannot be read, and mail flows again once the record is repaired\."\s*\n\s*: th\.mailOffWhy === "flags" \? "Its mail is held: the session settings file cannot be read, and mail flows again once it is written\."\s*\n\s*: th\.mailOffWhy === "master" \? "Its mail is off by the master default: the lane's mailbox toggle opts it back in\."\s*\n\s*: "Its mailbox is off: the lane's mailbox toggle turns peer mail back on\."/,
               "the promoted line reads the reason: an unreadable record is no mailbox toggle's to clear");
  assert.match(COMMENTS, /mailOffWhy\?: string;/);
  assert.match(KERNEL, /mail_why = _mail_off_why_k\(tsid\)\s*\n\s*threads\.append\(\{/, "the comments frame derives the reason once, before the row");
  assert.match(KERNEL, /"mailOff": bool\(mail_why\),[^\n]*\n\s*"mailOffWhy": mail_why,/, "…and both fields read that one derivation");
  assert.match(RENDER, /" in a moment\."/);
});

test("the tab hover and the Sessions pane show a session's mail state", () => {
  // the value is a glance since 2026-09-16: a check mark on the accent when mail is on, the bare word off or held when it
  // is not, no explanation inline; the reasons live in the Sessions pane's mail mark title, where a hover
  // works, and the two held states get one dim sub-line under the row (round two of PR 1803: this rich tip is pointer-inert,
  // so a value tip could never show)
  assert.match(RENDER, /rows\.push\(\["Mail", !s\.postalServiceOff \? "\\u2713" : \(s\.mailOffWhy === "unreadable" \|\| s\.mailOffWhy === "flags"\) \? "held" : "off",\s*\n\s*!s\.postalServiceOff \? "var\(--accent\)" : undefined\]\);/,
               "the row's value: the accent check, or off, or held, bare");
  assert.match(RENDER, /if \(s\.postalServiceOff && \(s\.mailOffWhy === "unreadable" \|\| s\.mailOffWhy === "flags" \|\| s\.mailOffWhy === "master"\)\) \{\s*\n\s*rows\.push\(\["", s\.mailOffWhy === "unreadable" \? "its record cannot be read; mail waits until it is repaired"\s*\n\s*: s\.mailOffWhy === "master" \? "by the master default; its mailbox toggle opts it back in"\s*\n\s*: "its settings file cannot be read; mail waits until it is written again", "var\(--dim\)"\]\);/,
               "the dim sub-line: the held states, and the master's off naming its opt-in; a hand-toggled off gets none");
  assert.doesNotMatch(RENDER, /rows\.push\(\["Mail", !s\.postalServiceOff \? "on"/, "no explanation inline");
  assert.doesNotMatch(RENDER, /setTip\(ve, /, "no tip on a value inside the pointer-inert tab tip");
  assert.match(RENDER, /mailOffWhy: \("mailOffWhy" in msg\) \? String\(msg\.mailOffWhy \|\| ""\) : \(prev \? prev\.mailOffWhy : undefined\),/, "the reason rides the session frame");
  assert.match(FLEET, /mo\.textContent = \(s\.mailOffWhy === "unreadable" \|\| s\.mailOffWhy === "flags"\) \? "mail held" : "mail off";/, "the Sessions pane tag says held for an unreadable record and for the unreadable settings file");
  assert.match(FLEET, /postalServiceOff\?: boolean;/, "the Sessions pane row type carries it");
  assert.match(FLEET, /if \(s\.postalServiceOff && s\.mailOffWhy !== MAIL_WHY_MASTER\) \{[\s\S]*?const mo = el\("span", "fl-mail-off"\);\s*\n\s*mo\.textContent = \(s\.mailOffWhy === "unreadable" \|\| s\.mailOffWhy === "flags"\) \? "mail held" : "mail off";/);
  assert.match(CSS, /\.fl-mail-off \{/);
});

test("the master's sessions share one pane note, counted over the rows shown and marked on each", () => {
  // the note counts the rows this render shows (the survivors of Show completed, the cutoff, search and the lens),
  // never every session in the payload; the executed twin is in fleet-live-clock.test.ts
  assert.match(FLEET, /const masterNote = masterMailNote\(survivors\.map\(\(x\) => x\.ctx\.s\)\);[^\n]*\n\s*if \(masterNote\) list\.appendChild\(masterNote\);/,
               "one note above the list, in both views, over the shown rows");
  assert.doesNotMatch(FLEET, /masterMailNote\(sessions\)/, "not over every session");
  assert.match(FLEET, /const n = shown\.filter\(mailOffByMaster\)\.length;\s*\n\s*if \(!n\) return null;/,
               "no note without a shown master-isolated row");
  assert.match(FLEET, /const MAIL_WHY_MASTER = "master";/);
  assert.match(FLEET, /return !!s\.postalServiceOff && s\.mailOffWhy === MAIL_WHY_MASTER;/);
  // each counted row wears the note's mark, in both views: the grouped head and the flat view's session label
  assert.match(FLEET, /const mm = masterMailMark\(s\); if \(mm\) head\.appendChild\(mm\);/);
  assert.match(FLEET, /const mm = masterMailMark\(s\); if \(mm\) tag\.appendChild\(mm\);/);
  assert.match(FLEET, /const badge = el\("span", "fl-mail-master"\);\s*\n\s*badge\.textContent = MASTER_MAIL_MARK;\s*\n\s*badge\.title = MASTER_MAIL_ROW_TIP;/);
  assert.match(FLEET, /MASTER_MAIL_ROW_TIP = "mail off by the master default: listed for peers, but not reachable";/);
  assert.match(FLEET, /note\.textContent = MASTER_MAIL_MARK \+ " Mail off by the master default for " \+ n/);
  assert.match(CSS, /\.fl-mail-master \{/);
  // the tip names the master and the opt-in, and no control that does not exist
  assert.match(FLEET, /MASTER_MAIL_TIP = "these sessions' mail is off by the master default \(the \* key in session-flags\.json\): peers still see them listed but cannot reach them; a lane's mailbox toggle opts one back in";/);
  assert.doesNotMatch(FLEET, /clear the master/);
});

test("the kernel and the bus derive the same default from the thread's reg and the fresh key", () => {
  assert.match(KERNEL, /def _thread_mail_off\(sid, flags=None\):[\s\S]*?if not sid or not _thread_reg\(sid\)\.get\("threadOf"\):\s*\n\s*return False\s*\n\s*f = \(_session_flags\(\) if flags is None else flags\)\.get\(sid\)\s*\n\s*return not \(isinstance\(f, dict\) and f\.get\("threadMail"\) is True\)/,
               "literal True only (the flip-a-default rule)");
  assert.match(KERNEL, /def _mail_off_why_k\(sid\):[\s\S]*?if _reg_unreadable\(sid\):\s*\n\s*return "unreadable"\s*\n\s*flags = _session_flags\(\)[^\n]*\n\s*if _thread_mail_off\(sid, flags\):\s*\n\s*return "thread"\s*\n\s*if _flags_unknown_cold\(\):\s*\n\s*return "flags"[^\n]*\n\s*return _postal_isolation_why\(flags, sid\)/,
               "the kernel's reasons: unreadable first (the bus holds everything for it), then the thread default, then the mailbox flag, read before the flags-unknown door closes (2026-09-14)");
  assert.match(KERNEL, /def _postal_isolated\(sid\):[\s\S]*?return bool\(_mail_off_why_k\(sid\)\)/);
  // the session's own key decides either way; absent one, the master default under POSTAL_ALL_KEY does —
  // the same order the bus reads over the same file, which is what keeps the two answers equal
  assert.match(KERNEL, /def _postal_isolation_why\(flags, sid\):[\s\S]*?for flag in \("postalServiceOff", "postalOff"\):\s*\n\s*if own\.get\(flag\) is not None:\s*\n\s*return "isolation" if own\[flag\] else ""\s*\n\s*master = flags\.get\(POSTAL_ALL_KEY\)/);
  assert.match(POSTAL, /for key in \("postalServiceOff", "postalOff"\)/, "the bus reads the own keys in the kernel's order (the master: tests/test_postal_isolation.py)");
  assert.match(KERNEL, /"mailOff": bool\(mail_why\),/, "the comments frame carries it (one derivation with the reason)");
  assert.match(KERNEL, /\*\*_mail_off_fields\(m\["id"\]\),/, "the Sessions pane rows carry it, with the reason, from one derivation (T356 fifth follow-up)");
  assert.match(KERNEL, /def _mail_off_fields\(sid\):[\s\S]*?why = _mail_off_why_k\(sid\)\s*\n\s*return \{"postalServiceOff": bool\(why\), "mailOffWhy": why\}/, "the one derivation behind both fields");
  assert.match(POSTAL, /t = _thread_of\(sid\)\s*\n\s*if t == THREAD_REG_UNREADABLE:\s*\n\s*return "unreadable"[^\n]*\n\s*if t and not \(isinstance\(f, dict\) and f\.get\("threadMail"\) is True\):\s*\n\s*return "thread"/,
               "an unreadable record is closed under its own reason, then the literal-True key");
  assert.match(POSTAL, /agents, listing_answered = local_agents_checked\(threads=True\)/, "the relay lists thread rows, so a thread recipient bounces instead of retrying forever");
  assert.match(POSTAL, /refused = _sender_refusal\(frm_id\)[^\n]*\n\s*if refused:\s*\n\s*return self\._send\(\{"error": refused\[1\]\}, refused\[0\]\)/, "the thread's own send");
  assert.match(POSTAL, /return 403, \{"thread": THREAD_MAIL_OFF_SENDER, "unreadable": UNREADABLE_REG_SENDER\}\.get\(why, ISOLATION_SENDER\)/, "…in the thread's own words");
  assert.match(POSTAL, /whys = \{_mail_off_why\(a\["id"\]\) for a in direct_all\}[\s\S]{0,300}?_all_off_refusal\(to, whys, tunneled\)/, "a send to the thread, and to a session whose record cannot be read, each with its own words");
  assert.match(POSTAL, /def _all_off_refusal\(to, whys, tunneled\):[\s\S]{0,300}?if "unreadable" in whys:[\s\S]{0,200}?if "thread" in whys:/);
});
