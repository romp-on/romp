// DOM acceptance: bundle the REAL webview entry (render.ts) for the browser,
// load it in jsdom against the real chatBody skeleton, then drive every fixture
// scene through the exact postMessage protocol the host uses — asserting the
// bundle loads with no error and actually paints. This is the test that would
// have caught the "render.ts imported transcript.ts → Buffer in the browser
// bundle → blank chat" regression: a Node global leaking into the webview throws
// on load here, and every scene that fails to render trips an assertion.
import { test, before } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "node:path";
import * as esbuild from "esbuild";
import { JSDOM, VirtualConsole } from "jsdom";
import { chatBody } from "../page-skeleton";
import { SCENES } from "./fixtures";

let bundle = "";

before(async () => {
  // Bundle the source (not dist) so the test never goes stale against a build.
  const out = await esbuild.build({
    entryPoints: [path.resolve(process.cwd(), "../ui/webview/render.ts")],
    nodePaths: [path.join(process.cwd(), "node_modules")],
    bundle: true, format: "iife", platform: "browser", target: "es2020",
    external: ["*.png", "*.svg"],
    write: false, logLevel: "silent",
  });
  bundle = out.outputFiles[0].text;
});

// Fresh jsdom per scene: skeleton + the render bundle, capturing any load/runtime
// error. Returns the window, collected errors, and a close() — render.ts has a
// top-level setInterval that keeps jsdom's event loop alive, so each window MUST
// be closed or the test process never exits.
function mount() {
  const errors: string[] = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e: any) => errors.push(String(e?.detail?.stack || e?.stack || e?.message || e)));
  vc.on("error", (...a: any[]) => errors.push("console.error: " + a.join(" ")));
  const fetchStub = `window.fetch = function() { return Promise.resolve({ json: function() { return Promise.resolve({}); } }); };`;
  const dom = new JSDOM(
    `<!doctype html><html><body>${chatBody("test")}<script>${fetchStub}<\/script><script>${bundle}<\/script></body></html>`,
    { runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc },
  );
  dom.window.addEventListener("error", (e: any) => errors.push(String(e?.error?.stack || e?.message)));
  return { window: dom.window as any, errors, close: () => dom.window.close() };
}

const drive = (window: any, scene: { messages: any[] }) =>
  scene.messages.forEach((m) => window.postMessage(m, "*"));

const settle = () => new Promise((r) => setTimeout(r, 50));

test("the webview bundle loads in a browser with no error (no Node globals leak)", async () => {
  const { window, errors, close } = mount();
  try {
    await settle();
    assert.deepEqual(errors, [], `render.ts bundle threw on load:\n${errors.join("\n")}`);
    assert.ok(window.document.getElementById("content"), "skeleton present");
  } finally { close(); }
});

test("every transcript scene paints into #content", async () => {
  for (const scene of SCENES.filter((s) => s.group === "Transcript")) {
    const { window, errors, close } = mount();
    try {
      await settle();
      drive(window, scene);
      await settle();
      assert.deepEqual(errors, [], `scene ${scene.id} errored:\n${errors.join("\n")}`);
      const content = window.document.getElementById("content");
      assert.ok(content.textContent.trim().length > 0, `scene ${scene.id} rendered nothing into #content`);
    } finally { close(); }
  }
});

test("every permission scene paints the live-ask popup", async () => {
  for (const scene of SCENES.filter((s) => s.group === "Permission popups")) {
    const { window, errors, close } = mount();
    try {
      await settle();
      drive(window, scene);
      await settle();
      assert.deepEqual(errors, [], `scene ${scene.id} errored:\n${errors.join("\n")}`);
      const live = window.document.getElementById("live-ask");
      assert.equal(live.style.display, "", `scene ${scene.id}: popup hidden`);
      assert.ok(live.textContent.trim().length > 0, `scene ${scene.id}: popup empty`);
    } finally { close(); }
  }
});

test("MCP tool popup: the tool line is the command, the prose peels into a dimmed description", async () => {
  const scene = SCENES.find((s) => s.id === "perm-mcp")!;
  const { window, close } = mount();
  try {
    await settle();
    drive(window, scene);
    await settle();
    const cmd = window.document.querySelector("#live-ask .ask-cmd");
    const desc = window.document.querySelector("#live-ask .ask-body-desc");
    assert.ok(cmd, "MCP popup must show a command/target line");
    assert.ok(desc, "MCP popup must show a separate description");
    assert.ok(cmd!.textContent!.includes("list_agents"), "tool name stays in the command box");
    assert.ok(!cmd!.textContent!.includes("avoid collisions"), "description must NOT be in the command box");
    assert.ok(desc!.textContent!.includes("avoid collisions"), "description text lands in .ask-body-desc");
    assert.ok(desc!.textContent!.includes("(yours is marked)"), "parenthetical stays with the description");
  } finally { close(); }
});

test("a permission popup with a 'tell Claude what to do differently' option shows the redirect field", async () => {
  const scene = SCENES.find((s) => s.id === "perm-edit")!;
  const { window, close } = mount();
  try {
    await settle();
    drive(window, scene);
    await settle();
    const field = window.document.querySelector("#live-ask .ask-redirect-input");
    assert.ok(field, "the decline-and-redirect input must render when the option is present");
  } finally { close(); }
});

test("a plain-No permission popup (perm-write) STILL shows the redirect field", async () => {
  // Real Edit/Write/Notebook prompts label the decline row just "No" — the field
  // must appear anyway (the bug was requiring "tell Claude…" wording).
  const scene = SCENES.find((s) => s.id === "perm-write")!; // options: Yes / allow-all / No
  const { window, close } = mount();
  try {
    await settle();
    drive(window, scene);
    await settle();
    assert.ok(window.document.querySelector("#live-ask .ask-redirect-input"), "plain-No permission prompt shows the field");
  } finally { close(); }
});

test("an AskUserQuestion choice (not a permission prompt) shows NO redirect field", async () => {
  const scene = SCENES.find((s) => s.id === "ask-single")!; // highlight.js / shiki / Type something
  const { window, close } = mount();
  try {
    await settle();
    drive(window, scene);
    await settle();
    assert.ok(!window.document.querySelector("#live-ask .ask-redirect-input"), "no redirect field on AskUserQuestion");
  } finally { close(); }
});

test("the edit-permission popup actually renders a red/green diff block", async () => {
  const scene = SCENES.find((s) => s.id === "perm-edit")!;
  const { window, close } = mount();
  try {
    await settle();
    drive(window, scene);
    await settle();
    const diff = window.document.querySelector("#live-ask .diff-fold");
    assert.ok(diff, "edit popup must contain a .diff-fold block");
    assert.ok(diff.textContent.includes("+") && diff.textContent.includes("-"), "diff shows +/- lines");
  } finally { close(); }
});
