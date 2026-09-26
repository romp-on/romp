// esbuild.js's build stamp: one clock reading, taken when the script loads. Its whole second is baked into the
// extension bundle as __ROMP_BUILD__; the reading in full is the mtime of every output it writes. So the kernel's
// dist token (kernel.py's _dist_ver: the newest dist/*.js mtime in whole seconds, the `dv` on every keepalive)
// EQUALS the stamp of the build that wrote dist/, and the extension's "newer romp build" prompt (a dv above its
// own stamp) fires only for a build that came after its own; and the kernel's source-vs-dist staleness checks
// (_ensure_bundles, _dist_converge_check: fractional source mtimes against dist) read a source saved in the same
// second before the build began as older than dist, not newer.
//
// Before, the stamp was read when the script loaded and the outputs carried the second they landed, one or two
// later for the webview bundles, so a fresh build's dv was above its own stamp: the VSIX install.sh had just
// produced warned about itself in every window, and running install.sh again reproduced it (the user
// 2026-09-26: a build stamped 1790407780 whose dist read 1790407781). These tests drive buildAll with a start
// no write of today could carry by accident, and `node esbuild.js` itself, on a stub tree, in both profiles.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

// npm test runs in vscode-extension/, where esbuild.js lives. Required at run time rather than imported so
// the bundler leaves it alone: it loads the real esbuild package (a native binary) from node_modules.
const ESBUILD_JS = path.join(process.cwd(), "esbuild.js");
const script = createRequire(__filename)(ESBUILD_JS);
const { buildAll, BUILD_START_MS, BUILD_STAMP } = script;

function scratch(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "romp-esbuild-stamp-"));
}

// A minimal bundle config in the shape of esbuild.js's own: bundled, silent, output under <dir>/dist.
function cfg(dir: string, entry: string, out: string, extra: Record<string, unknown> = {}) {
  return { entryPoints: [path.join(dir, entry)], bundle: true, format: "iife", platform: "browser",
           outfile: path.join(dir, "dist", out), logLevel: "silent", ...extra };
}

// A file's mtime in whole milliseconds: utimes takes fractional seconds and stat gives mtimeMs back as a float,
// so the two are compared at the millisecond, the unit the build's reading has.
const mtimeMs = (p: string) => Math.round(fs.statSync(p).mtimeMs);

// kernel.py's _dist_ver: int(max(p.stat().st_mtime for p in DIST.glob("*.js"))), 0 without a dist/*.js.
function distVer(dist: string): number {
  const ms = fs.readdirSync(dist).filter((n) => n.endsWith(".js")).map((n) => mtimeMs(path.join(dist, n)));
  return ms.length ? Math.floor(Math.max(...ms) / 1000) : 0;
}

// Every file under dir (recursively), with its mtime in ms.
function mtimes(dir: string): Record<string, number> {
  const out: Record<string, number> = {};
  const walk = (d: string) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      if (fs.statSync(p).isDirectory()) walk(p);
      else out[path.relative(dir, p)] = mtimeMs(p);
    }
  };
  walk(dir);
  return out;
}

// A source that logs its __ROMP_BUILD__, so the bundle built from it holds `console.log(<stamp>)`.
const LOGS_STAMP = "declare const __ROMP_BUILD__: number;\nconsole.log(__ROMP_BUILD__);\n";

// The __ROMP_BUILD__ a bundle of LOGS_STAMP was built with.
function bakedStamp(bundle: string): number {
  const m = /console\.log\((\d+)\)/.exec(fs.readFileSync(bundle, "utf8"));
  assert.ok(m, "the bundle holds the baked stamp: " + bundle);
  return Number(m![1]);
}

test("BUILD_STAMP is the whole second of BUILD_START_MS, one reading taken when the script loaded, and the extension config bakes it", () => {
  assert.equal(typeof BUILD_START_MS, "number");
  assert.equal(BUILD_START_MS, Math.floor(BUILD_START_MS), "whole milliseconds: Date.now()");
  assert.ok(Math.abs(Date.now() - BUILD_START_MS) < 600_000, "read moments ago: " + BUILD_START_MS);
  assert.equal(BUILD_STAMP, Math.floor(BUILD_START_MS / 1000), "whole seconds, the kernel token's unit");
  assert.equal(script.extension.define.__ROMP_BUILD__, String(BUILD_STAMP), "the define is the same reading");
});

test("every output carries the build's start as its mtime: the kernel's token equals the baked stamp, and a source saved just before the build reads older than dist", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "ext.ts"), LOGS_STAMP);
    fs.writeFileSync(path.join(d, "note.txt"), "asset\n");
    fs.writeFileSync(path.join(d, "web.ts"), 'import u from "./note.txt";\nconsole.log(u);\n');
    fs.writeFileSync(path.join(d, "web.css"), "body { margin: 0; }\n");
    // the build began an hour ago, three quarters into a second: no file written now carries that time unless
    // the build set it, and the fraction is what the whole-second stamp drops
    const stamp = BUILD_STAMP - 3600;
    const startMs = stamp * 1000 + 750;
    // a source saved a quarter second BEFORE the build began, in the same second (a checkout, then the
    // kernel's converge rebuilding at once)
    const src = path.join(d, "web.ts");
    fs.utimesSync(src, (startMs - 250) / 1000, (startMs - 250) / 1000);

    const written: string[] = await buildAll([
      cfg(d, "ext.ts", "ext.js", { define: { __ROMP_BUILD__: String(stamp) }, sourcemap: true }),
      // the KaTeX-font shape: a file-loader asset in a subdirectory of dist, and a sourcemap
      cfg(d, "web.ts", "web.js", { loader: { ".txt": "file" }, assetNames: "fonts/[name]-[hash]", sourcemap: true }),
      cfg(d, "web.css", "web.css"),
    ], startMs);
    assert.equal(written.length, 6, "two bundles, two maps, an asset, a stylesheet: " + written.join(","));
    const dist = path.join(d, "dist");
    for (const [rel, ms] of Object.entries(mtimes(dist))) assert.equal(ms, startMs, rel + " carries the build's start");
    assert.equal(bakedStamp(path.join(dist, "ext.js")), stamp, "the bundle was built with the start's whole second");
    assert.equal(distVer(dist), stamp,
                 "the kernel's dv for this dist/ is the bundle's own stamp: not above it, so the extension built here is not told of itself");
    // kernel.py's _ensure_bundles and _dist_converge_check: a source with an mtime above dist's means a rebuild
    const srcMs = mtimeMs(src);
    assert.ok(srcMs <= mtimeMs(path.join(dist, "web.js")), "the source the build began after is not newer than what it built");
    assert.ok(srcMs > stamp * 1000, "against a dist rounded down to the stamp's second it WOULD read newer, and a current build be rebuilt");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("a build driven without a start lands at BUILD_START_MS, whose whole second the extension config's define holds", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "one.ts"), "console.log(1);\n");
    const written: string[] = await buildAll([cfg(d, "one.ts", "one.js")]);
    assert.equal(written.length, 1);
    assert.equal(mtimeMs(written[0]), BUILD_START_MS);
    assert.equal(distVer(path.join(d, "dist")), BUILD_STAMP);
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("a later build into the same dist/ lifts the token above an earlier stamp: a bundle from the earlier build IS told of it", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "ext.ts"), LOGS_STAMP);
    fs.writeFileSync(path.join(d, "web.ts"), "console.log('web');\n");
    const dist = path.join(d, "dist");
    const first = BUILD_STAMP - 3600;
    const firstMs = first * 1000 + 750;
    await buildAll([cfg(d, "ext.ts", "ext.js", { define: { __ROMP_BUILD__: String(first) } }), cfg(d, "web.ts", "web.js")], firstMs);
    const installed = bakedStamp(path.join(dist, "ext.js"));   // what a VSIX packaged from this build carries
    assert.equal(installed, first);
    assert.ok(!(distVer(dist) > installed), "its own build: no prompt");

    // a rebuild of the webview sources alone, five seconds on (the shape a kernel converge takes)
    const secondMs = firstMs + 5000;
    fs.writeFileSync(path.join(d, "web.ts"), "console.log('web, rebuilt');\n");
    await buildAll([cfg(d, "web.ts", "web.js")], secondMs);
    assert.equal(mtimeMs(path.join(dist, "web.js")), secondMs);
    assert.equal(mtimeMs(path.join(dist, "ext.js")), firstMs, "an output the later build did not write keeps its own build's start");
    assert.equal(distVer(dist), first + 5);
    assert.ok(distVer(dist) > installed, "the newest dist/*.js is from a later build than the installed bundle: the prompt is owed");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

// A stub source tree for the script's real configs: every entry point they name, relative to a cwd that stands
// in for vscode-extension/ (the webview entries reach up into ../ui/webview). The stubs import nothing, so
// `node esbuild.js` builds them with the real settings; the extension entry logs its __ROMP_BUILD__.
function stubTree(root: string): string {
  const cwd = path.join(root, "vscode-extension");
  for (const c of [script.extension, script.webview]) {
    for (const e of c.entryPoints) {
      const rel: string = typeof e === "string" ? e : e.in;
      const p = path.resolve(cwd, rel);
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, rel.endsWith(".css") ? "body { margin: 0; }\n"
                         : rel === "src/extension.ts" ? LOGS_STAMP : `console.log(${JSON.stringify(rel)});\n`);
    }
  }
  return cwd;
}

test("node esbuild.js: every file under dist/ carries one time, this run's start, whose whole second is the __ROMP_BUILD__ baked into dist/extension.js — in both build profiles", () => {
  const root = scratch();
  try {
    const cwd = stubTree(root);
    const dist = path.join(cwd, "dist");
    // the dev-knob build (ROMP_EXT_DEV_BUILD set, or a hand-run `node esbuild.js`) and the production build
    // (every kernel rebuild, in place and at boot, and install.sh: the one that packages the VSIX). Each into a
    // dist/ of its own: production writes no sourcemaps and removes none, so the dev build's maps would
    // otherwise stand beside its outputs with the earlier run's time.
    for (const args of [[], ["--production"]]) {
      fs.rmSync(dist, { recursive: true, force: true });
      const beforeMs = Date.now();
      const r = spawnSync(process.execPath, [ESBUILD_JS, ...args], { cwd, encoding: "utf8" });
      const afterMs = Date.now();
      assert.equal(r.status, 0, "node esbuild.js " + args.join(" ") + " (signal " + r.signal + "); stderr: " + r.stderr);
      const all = mtimes(dist);
      assert.ok(Object.keys(all).some((rel) => rel !== "extension.js" && rel.endsWith(".js")), "webview bundles were written too");
      const times = new Set(Object.values(all));
      assert.equal(times.size, 1, "one time for every file under dist/ (" + args.join(" ") + "): " + JSON.stringify(all));
      const [startMs] = [...times];
      assert.ok(startMs >= beforeMs && startMs <= afterMs, "the run's own clock reading: " + startMs + " in [" + beforeMs + ", " + afterMs + "]");
      const stamp = bakedStamp(path.join(dist, "extension.js"));
      assert.equal(stamp, Math.floor(startMs / 1000), "the baked stamp is that reading's whole second");
      assert.equal(distVer(dist), stamp, "the kernel's dv for this build equals its bundle's stamp: it is not told of itself");
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
