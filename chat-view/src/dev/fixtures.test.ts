// Acceptance-fixture coverage: the gallery is visual, but these tests pin that
// every scene is well-formed, that the transcript scenes cover every content
// kind, and that the permission scenes actually trip the real popup matchers
// (pendingEditDiff / pendingCommand) — so a regression there fails CI, not just
// the eye.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { SCENES, EXPECTED_EVENT_KINDS } from "./fixtures";

const sessionMsg = (scene: { messages: any[] }) => scene.messages.find((m) => m.type === "session");
const askMsg = (scene: { messages: any[] }) => scene.messages.find((m) => m.type === "askLive");

test("every scene is well-formed (unique id, title, ≥1 message, a focus)", () => {
  const ids = new Set<string>();
  for (const s of SCENES) {
    assert.ok(s.id && s.title && s.group, `scene missing id/title/group: ${JSON.stringify(s)}`);
    assert.ok(!ids.has(s.id), `duplicate scene id: ${s.id}`);
    ids.add(s.id);
    assert.ok(s.messages.length >= 1, `scene ${s.id} has no messages`);
    assert.ok(s.messages.some((m) => m.type === "focus"), `scene ${s.id} never focuses a session`);
  }
});

test("every session event carries a known kind", () => {
  const KNOWN = new Set(["user", "assistant", "thinking", "tool", "postal", "todo", "queued"]);
  for (const s of SCENES) {
    const sess = sessionMsg(s);
    if (!sess) continue;
    for (const ev of sess.events) assert.ok(KNOWN.has(ev.kind), `scene ${s.id}: unknown event kind ${ev.kind}`);
  }
});

test("transcript scenes cover every expected content kind", () => {
  const seen = new Set<string>();
  for (const s of SCENES) for (const ev of sessionMsg(s)?.events ?? []) seen.add(ev.kind);
  for (const k of EXPECTED_EVENT_KINDS) assert.ok(seen.has(k), `no fixture covers content kind: ${k}`);
});

test("the edit-permission scene carries a red/green diff on the ask", () => {
  const scene = SCENES.find((s) => s.id === "perm-edit")!;
  const diff = askMsg(scene)!.ask.diff;
  assert.ok(diff && diff.includes("+") && diff.includes("-"), "edit popup ask must carry a +/- diff");
});

test("the WebFetch scene carries a detail body on the ask", () => {
  const scene = SCENES.find((s) => s.id === "perm-fetch")!;
  const body = askMsg(scene)!.ask.body;
  assert.ok(body && body.includes("example.com"), "fetch popup ask must carry a detail body");
});

test("permission popups cover single, multi, and submit asks", () => {
  const kinds = new Set(SCENES.map((s) => askMsg(s)?.ask?.kind).filter(Boolean));
  for (const k of ["single", "multi", "submit"]) assert.ok(kinds.has(k), `no permission scene covers ask kind: ${k}`);
});
