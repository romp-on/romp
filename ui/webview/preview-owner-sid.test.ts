// The owning-session id behind every baked file/preview URL (the user 2026-08-24, the recurring
// inline-preview failure): transcript DOM is built for BACKGROUND sessions too — update() marks a
// hidden view stale and the idle prebuild renders it — and previewFull/buildPathImg used to bake
// the global activeId into their URLs and retry closures at that moment. For a federated session
// built while a local one was active, every figure then asked the WRONG host's kernel, whose
// truthful "not found" looped forever (retries and heals re-fetch the closure's captured URL);
// only the next send's tail re-render, which runs with activeId now correct, ever fixed it. The
// mechanism: renderingOwnerSid — the SESSION whose DOM is being built, host prefix included —
// distinct from renderingSid, the fold KEY the comment popover retargets to its thread id.
import { test } from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("renderingOwnerSid exists, documented as the session whose DOM is being built", () => {
  assert.match(RENDER, /let renderingOwnerSid: string \| null = null;/);
  // the two-variable split is deliberate: the fold key can be a thread id, the owner never is
  assert.match(RENDER, /Distinct from renderingSid, which is a fold KEY/);
});

test("every render entry point sets the owner (and the out-of-sync ones restore it)", () => {
  // syncView: the per-tab build — prebuild included, since prebuild calls syncView per planned tab
  assert.match(RENDER, /renderingSid = id; {10}\/\/ so renderSystem can key[\s\S]{0,200}renderingOwnerSid = id;/);
  // prebuild restores both after its loop, so nothing later keys off the last pre-built tab
  assert.match(RENDER, /const savedRenderingSid = renderingSid;[\s\S]{0,120}const savedOwnerSid = renderingOwnerSid;/);
  assert.match(RENDER, /renderingSid = savedRenderingSid;\s*\n\s*renderingOwnerSid = savedOwnerSid;/);
  // the comment popover renders a thread: fold keys per-thread, URLs per the thread's SESSION
  assert.match(RENDER, /function fillCommentMsgs\(list: HTMLElement, th: CommentThread, sid: string\)/);
  assert.match(RENDER, /renderingSid = th\.tid;\s*\n\s*renderingOwnerSid = sid;/);
  assert.match(RENDER, /renderingSid = saved;\s*\n\s*renderingOwnerSid = savedOwner;/);
  // chatEpisode fills in the MESSAGE handler, outside any sync — it pins both to the episode's session
  assert.match(RENDER, /renderingSid = sid;\s*\n\s*renderingOwnerSid = sid;\s*\n\s*try \{[\s\S]{0,220}fillClearBody/);
});

test("the baked URLs and host image asks carry the owner, never the active tab", () => {
  // the inline figure family: previewFull (web <img>/PDF card, retry closures) + buildPathImg (VS Code)
  assert.match(RENDER, /previewFull\(p, renderingOwnerSid \?\? activeId, kernelVerified\.has\(p\)/);
  assert.match(RENDER, /buildPathImg\(p, renderingOwnerSid \?\? activeId\)/);
  // user-turn path images ride the same owner
  assert.match(RENDER, /buildPathImg\(im\.src\.slice\(5\), renderingOwnerSid \?\? activeId\)/);
  // buildPathImg itself sends the sid it was handed
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "imgRequest", path: p, id: sid \}\);/);
  // …and the reconnect heal re-asks for the OWNING session, read off the chip's own minted sid
  assert.match(RENDER, /const own = e\.dataset\.imgsid \|\| activeId;/);
});

test("the whole imgRequest ride is keyed by (session, path), never the bare path", () => {
  // the same relative path string in two sessions names two different files (each cwd its own): a
  // bare-path cache let the FIRST asker's answer fill every session's chips, and a first-ask failure
  // parked them all (the adversarial review of this fix, 2026-08-24)
  assert.match(RENDER, /const imgKey = \(sid: string \| null, p: string\): string => \(sid \|\| ""\) \+ "\\u0000" \+ p;/);
  assert.match(RENDER, /const imgUrlCache = new Map<string, string>\(\); {3}\/\/ \(sid,path\) → dataURL/);
  assert.match(RENDER, /wrap\.dataset\.imgsid = sid \|\| "";/);
  // …and the kernel echoes the asking session on the reply, so answers land only on their session's chips
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /\{"type": "imgData", "path": p, "sid": str\(msg\.get\("id"\) or ""\),/);
  // an older kernel's sid-less reply still fills by path alone — sharing at worst, never a dead chip
  assert.match(RENDER, /const bySid = typeof sid === "string";/);
});
