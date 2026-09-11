// The command registry behind the palette (ui/webview/commands.ts) — real unit tests.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { registerCommand, unregisterCommand, commandList, runCommand } from "./commands";

test("registers and lists in registration order (the palette's empty-query order)", () => {
  registerCommand({ id: "t.one", title: "First thing", run: () => {} });
  registerCommand({ id: "t.two", title: "Second thing", run: () => {} });
  const ids = commandList().map((c) => c.id);
  assert.ok(ids.indexOf("t.one") >= 0 && ids.indexOf("t.two") >= 0);
  assert.ok(ids.indexOf("t.one") < ids.indexOf("t.two"));
});

test("re-registering an id replaces the command instead of duplicating it", () => {
  let hits = 0;
  registerCommand({ id: "t.re", title: "Old title", run: () => { hits = 1; } });
  registerCommand({ id: "t.re", title: "New title", run: () => { hits = 2; } });
  const matches = commandList().filter((c) => c.id === "t.re");
  assert.equal(matches.length, 1);
  assert.equal(matches[0].title, "New title");
  assert.ok(runCommand("t.re"));
  assert.equal(hits, 2);
});

test("runCommand runs the handler and reports unknown ids", () => {
  let ran = false;
  registerCommand({ id: "t.run", title: "Run me", run: () => { ran = true; } });
  assert.equal(runCommand("t.run"), true);
  assert.equal(ran, true);
  assert.equal(runCommand("t.nope"), false);
});

test("unregisterCommand removes a command from the list and the runner, and reports an unknown id", () => {
  registerCommand({ id: "t.gone", title: "Soon gone", run: () => {} });
  assert.ok(commandList().some((c) => c.id === "t.gone"));
  assert.equal(unregisterCommand("t.gone"), true);
  assert.ok(!commandList().some((c) => c.id === "t.gone"));
  assert.equal(runCommand("t.gone"), false);
  assert.equal(unregisterCommand("t.gone"), false, "already gone");
});
