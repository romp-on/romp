// Two bundles: the extension host (Node/CJS) and the webview (browser/IIFE),
// plus the webview stylesheet. esbuild only strips types — run `npm run
// typecheck` for real type checking.
const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");

const production = process.argv.includes("--production");
const watch = process.argv.includes("--watch");
const tests = process.argv.includes("--tests");

/** @type {import('esbuild').BuildOptions} */
const extension = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node18",
  outfile: "dist/extension.js",
  external: ["vscode", "bufferutil", "utf-8-validate"],   // ws optional native addons
  sourcemap: !production,
  minify: production,
  logLevel: "info",
};

/** @type {import('esbuild').BuildOptions} */
const webview = {
  // The browser UI sources live in the top-level ui/ dir (consolidated out of
  // chat-view/src/webview/). This extension package still owns the build + dist,
  // so we reach up into ../ui/webview and add chat-view/node_modules to the
  // resolver (nodePaths) — ui/ is outside this package, so marked/dompurify/
  // highlight.js wouldn't resolve by the normal upward walk otherwise.
  entryPoints: [
    "../ui/webview/render.ts",
    "../ui/webview/styles.css",
    "../ui/webview/feed.ts",
    "../ui/webview/feed.css",
    "../ui/webview/fleet.ts",
  ],
  nodePaths: [path.join(__dirname, "node_modules")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2020",
  outdir: "dist",
  // Leave media url()s verbatim — they're served from chat-view/media at runtime (kernel
  // /media or VS Code localResourceRoot), NOT bundled. `../media/x.png` is correct relative
  // to the emitted dist/feed.css; esbuild must not try to resolve it against the source tree.
  external: ["*.png", "*.svg"],
  sourcemap: !production,
  minify: production,
  logLevel: "info",
};

// Unit tests for the pure modules (src/*.test.ts): bundled to out-tests/ and
// run with the built-in `node --test` runner — no extra test framework.
function testBuild() {
  // Tests live beside their sources: host tests in src/, the UI tests under
  // ../ui (timeline + quote) and ../ui/webview (feed/render/etc.). out-tests/
  // keeps each tree's structure (esbuild's outbase = the common ancestor), and
  // `node --test 'out-tests/**/*.test.js'` finds them recursively.
  const dirs = ["src", "src/dev", "../ui", "../ui/webview"];
  const entries = dirs.flatMap((dir) => {
    const abs = path.join(__dirname, dir);
    if (!fs.existsSync(abs)) return [];
    return fs.readdirSync(abs)
      .filter((f) => f.endsWith(".test.ts"))
      .map((f) => dir + "/" + f);
  });
  /** @type {import('esbuild').BuildOptions} */
  return {
    entryPoints: entries,
    nodePaths: [path.join(__dirname, "node_modules")],
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node18",
    packages: "external",
    outdir: "out-tests",
    sourcemap: "inline",
    logLevel: "info",
  };
}

async function main() {
  if (tests) {
    // Clean out-tests/ first so a DELETED test source can't leave an orphaned .js behind that `node --test`
    // would still run — a renamed/removed .test.ts otherwise fails forever against the new implementation (the
    // user 2026-06-29: 8 phantom failures from feed-donewhy/feed-distiller-summary .js whose sources were gone).
    fs.rmSync(path.join(__dirname, "out-tests"), { recursive: true, force: true });
    await esbuild.build(testBuild());
  } else if (watch) {
    const a = await esbuild.context(extension);
    const b = await esbuild.context(webview);
    await Promise.all([a.watch(), b.watch()]);
    console.log("watching…");
  } else {
    await esbuild.build(extension);
    await esbuild.build(webview);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
