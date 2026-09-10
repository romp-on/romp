// The USER TODOS feature switch (the user 2026-09-03): "Waiting on you" is switchable, DEFAULT OFF,
// per install. Source pins, like the other webview tests (no jsdom harness):
//  - the GEAR row: a per-install kernel-side checkbox beside Thinking summaries, honest copy (what it
//    turns on, off by default, per machine), stamped through the gesture clock under its own store
//    name like every kernel setting the gear emits, filled from /version, named in the stale-gesture
//    toast and re-issuable from it (STALE_TYPE);
//  - PER-INSTALL: deliberately NOT in federation's KERNEL_SETTING set, so it never queues for or
//    reaches another machine's kernel (gear.test.ts's PER_INSTALL pin covers the class);
//  - the CLIENT needs no gate of its own: the kernel ships no rows while the switch is off — the
//    one gated read is _open_user_todos, which every payload field the card / tab glyph / feed
//    marker reads derives from — so the existing "renders only with open todos" pins ARE the off
//    behavior (user-todos-card.test.ts, tab-usertodo.test.ts, feed-user-todos.test.ts);
//  - every OFF surface refuses loudly: the kernel routes (409), the drive ops (a warn frame), the
//    postal bus (the pair leaves tools/list; a call anyway is refused), the SessionStart hook
//    (enabled:false means no output at all).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const GEAR = read("ui", "webview", "gear.js");
const FED = read("ui", "webview", "federation.ts");
const KERNEL = read("kernel", "kernel.py");
const BUS = read("postal", "postal_service.py");
const HOOK = read("hooks", "romp-usertodo-context.sh");

test("the gear has a User todos checkbox beside Thinking summaries, gesture-stamped, filled from /version", () => {
  assert.ok(GEAR.includes("id=rs-usertodos"), "the checkbox exists in the gear markup");
  const at = GEAR.indexOf("id=rs-usertodos");
  assert.ok(GEAR.indexOf("id=rs-thinksum") < at && at < GEAR.indexOf("id=rs-fileedit"),
    "…in the same section as Thinking summaries, between it and File editing");
  const row = GEAR.slice(at, at + 700);
  assert.match(row, /<b>User todos<\/b>/);
  assert.ok(/flag a decision or an input it needs from you/.test(row), "says what it turns on");
  assert.ok(/Waiting on you/.test(row) && /card at the bottom/.test(row), "…and where it shows");
  assert.ok(/Off by default/.test(row), "says it is off by default");
  assert.ok(/this kernel keeps its own copy/.test(row) && /per machine/.test(row), "…and per machine");
  assert.ok(GEAR.includes("post({ type: 'setUserTodos', enabled: utd.checked, gt: gclock.stamp('user-todos') })"),
    "the click posts the kernel's designed message, stamped through the gesture clock under its own store name");
  assert.ok(GEAR.includes("utd.checked = !!v.userTodos"),
    "the box always shows the kernel's persisted answer, never a page default");
  assert.match(GEAR, /STALE_LABELS = \{[\s\S]*?'user-todos': 'User todos'/,
    "a stood-down gesture toasts under the row's own name");
  assert.match(GEAR, /STALE_TYPE = \{[\s\S]*?'user-todos': 'setUserTodos'/,
    "…and the toast's Apply anyway may re-issue exactly this one setting");
});

test("User todos is per-install: not a KERNEL_SETTING, so it never propagates", () => {
  const setSrc = FED.match(/const KERNEL_SETTING = new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(setSrc, "federation.ts's KERNEL_SETTING set located");
  assert.ok(!setSrc![1].includes("setUserTodos"),
    "the set must not carry it — this kernel keeps its own copy (the sub-copy says so)");
  assert.ok(!FED.includes("setUserTodos"), "…and no other federation path names it either");
  const comment = FED.slice(Math.max(0, FED.indexOf("const KERNEL_SETTING") - 1800), FED.indexOf("const KERNEL_SETTING"));
  assert.ok(/PER-INSTALL gear rows/.test(comment)
    && /the User todos switch \(off by default; each kernel's answer is its own/.test(comment),
    "the set's own comment names the row as deliberately absent (by row, never by op literal)");
  assert.ok(!KERNEL.includes('"userTodos", _set_user_todos'),
    "…nor the /judge-settings propagation table on the kernel side");
  const at = KERNEL.indexOf('msg.get("type") == "setUserTodos"');
  assert.ok(at > 0, "the kernel handles the op");
  assert.ok(/NOT in federation\.ts's KERNEL_SETTING/.test(KERNEL.slice(at, at + 900)),
    "the handler says so where the op is handled");
});

test("the kernel ships no rows while the switch is off — the client needs no logic of its own", () => {
  const helper = KERNEL.slice(KERNEL.indexOf("def _open_user_todos(sid):"), KERNEL.indexOf("def _user_todo_session_ended"));
  assert.match(helper, /if not _user_todos_on\(\):\n        return \[\]/, "the one gated read");
  // every payload field a UI surface reads derives from it: the session field + split-card event,
  // the feed's sid-keyed map (marker + badge), the escalation floor's arming read, the push latch
  assert.match(KERNEL, /_user_todos_open = _open_user_todos\(sid\)/);
  assert.match(KERNEL, /_ut_open = _open_user_todos\(fsid\)/);
  assert.match(KERNEL, /_todo_standdown = bool\(_open_user_todos\(sid\)\)/);
  assert.match(KERNEL, /frozenset\(t\["id"\] for t in _open_user_todos\(str\(sid\)\)\)/);
  // the switch file is its own file — the store keeps every row for the day it flips back on
  assert.match(KERNEL, /USER_TODOS_SWITCH_FILE = "user-todos-enabled\.json"/);
  assert.ok(!/USER_TODOS_SWITCH_FILE = "user-todos\.json"/.test(KERNEL), "never the store's own file");
  // no switch logic reached the renderer
  const RENDER = read("ui", "webview", "render.ts");
  assert.ok(!/userTodos_on|_user_todos_on|userTodosEnabled|userTodosOn/.test(RENDER), "no switch logic in the renderer");
});

test("each OFF surface refuses loudly, never a silent no-op", () => {
  // the kernel routes: a 409 with a one-line reason; the drive ops: a warn frame naming the switch
  assert.match(KERNEL, /_USER_TODOS_OFF_ERR = "user todos are turned off on this machine"/);
  assert.ok((KERNEL.match(/self\._send\(409, json\.dumps\(\{"ok": False, "error": _USER_TODOS_OFF_ERR\}\)/g) || []).length >= 2,
    "both POST /usertodo and /usertodo/withdraw answer 409");
  assert.match(KERNEL, /elif t == "userTodoAnswer"[\s\S]*?if not _user_todos_on\(\):\n\s+client\["send"\]\(json\.dumps\(\{"type": "warn", "text": _USER_TODOS_OFF_WARN\}\)\)/,
    "userTodoAnswer warns with the switch's own text, ahead of the settled-row gate");
  assert.match(KERNEL, /elif t == "userTodoDismiss"[\s\S]*?"type": "warn", "text": _USER_TODOS_OFF_WARN/,
    "userTodoDismiss warns");
  // the postal bus: the pair leaves tools/list, and a call anyway is refused before any post
  assert.match(BUS, /USER_TODOS_SWITCH = STATE\.parent \/ "user-todos-enabled\.json"/);
  assert.match(BUS, /"tools": _tools_offered\(\)/);
  assert.match(BUS, /USER_TODO_TOOLS = \("add_user_todo", "withdraw_user_todo"\)/);
  // the SessionStart hook: the kernel's enabled:false means no output at all
  assert.match(HOOK, /block = "" if d\.get\("enabled"\) is False else/);
  assert.match(KERNEL, /"enabled": _on,\n\s+"block": _user_todo_context_block\(sid\) if _on else ""/);
});
