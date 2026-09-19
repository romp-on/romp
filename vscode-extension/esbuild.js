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
  // Bundle build stamp (epoch seconds — the same clock as the kernel's dist token). The extension
  // compares it against the `dv` on kernel keepalives to raise the "newer romp build" prompt when the
  // installed VSIX predates a rebuild of the shared webview sources.
  define: { __ROMP_BUILD__: String(Math.floor(Date.now() / 1000)) },
  sourcemap: !production,
  minify: production,
  logLevel: "info",
};

/** @type {import('esbuild').BuildOptions} */
const webview = {
  // The browser UI sources live in the top-level ui/ dir (consolidated out of
  // the old chat-view/src/webview/). This extension package still owns the build + dist,
  // so we reach up into ../ui/webview and add vscode-extension/node_modules to the
  // resolver (nodePaths) — ui/ is outside this package, so marked/dompurify/
  // highlight.js wouldn't resolve by the normal upward walk otherwise.
  entryPoints: [
    "../ui/webview/render.ts",
    "../ui/webview/styles.css",
    "../ui/webview/feed.ts",
    "../ui/webview/feed.css",
    "../ui/webview/fleet.ts",
    "../ui/webview/fleet-pane.css",      // fleet page layout — the kernel reads the same file live
    "../ui/webview/files.ts",            // the Files pane: the file viewer as its own column (kernel /files)
    "../ui/webview/files-pane.css",      // its page layout + the viewer's pane-resident variant; the kernel reads the same file live
    "../ui/webview/timeline-main.ts",    // VS Code timeline view: boot glue + ui/romp-timeline-view.js inlined
    "../ui/webview/timeline-pane.css",   // timeline wrapper styles — the kernel reads the same file live
    "../ui/webview/strip.css",           // the romp strip (VS Code-only bottom rail stand-in)
    "../ui/webview/gear.css",            // the settings modal (linked by the kernel settings page + VS Code chat/feed)
    "../ui/webview/settings-page.ts",    // the kernel's /settings page: the gear on a page of its own (the shell's hidden #f-settings iframe)
    "../ui/webview/federation.ts",   // multi-kernel manager: loaded after the shim on chat/feed/fleet pages
    "../ui/webview/age-color-global.ts",   // window.__rompAgeColor for the kernel's inline shell scripts (bell panel)
    "../ui/webview/api-health-global.ts",   // window.__rompApiHealthMerge: the API-health merge + reading rules for the shell's rail (T301)
    "../ui/webview/palette-main.ts",   // command palette + Cmd/Ctrl+O/P hotkeys for the kernel's shell page
    "../ui/webview/panedock-main.ts",  // the pane docking engine (plans/pane-docking.md): a shell bundle, inert unless the gear's paneDocking switch is on
    "../ui/webview/pane-grab.ts",      // the pane pages' grab detector: injected into each pane document by the engine while the kit is on (section 3, the empty space inside a pane)
    "../ui/webview/shell-perf.ts",     // the shell page's performance collector (a pane's long animation frames are reported to the top-level window)
    "../ui/webview/editor-chunk.ts",   // CodeMirror editing substrate — ON-DEMAND (file-view loads it by
                                       // script tag on first edit); nothing else may import it, so the
                                       // main bundles stay byte-stable for people who never edit
  ],
  nodePaths: [path.join(__dirname, "node_modules")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2020",
  outdir: "dist",
  // Leave media url()s verbatim — they're served from vscode-extension/media at runtime (kernel
  // /media or VS Code localResourceRoot), NOT bundled. `../media/x.png` is correct relative
  // to the emitted dist/feed.css; esbuild must not try to resolve it against the source tree.
  // .woff/.ttf stay external too: KaTeX's @font-face lists woff2 first and every current
  // engine takes it, so those fallback urls are never fetched and bundling them would
  // triple the font payload for nothing. The Inter faces are media assets like the pngs —
  // external by PATH (the extension-wide woff2 file-loader below is for KaTeX's, which do bundle).
  external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"],
  // KaTeX fonts (referenced by katex.min.css, @imported from styles.css) are copied to
  // dist/fonts/ and the css urls rewritten relative to dist — served by the kernel's /dist/
  // route and the VS Code webview's dist resource root alike.
  loader: { ".woff2": "file" },
  assetNames: "fonts/[name]-[hash]",
  sourcemap: !production,
  // Whitespace and syntax are minified for a release; identifiers are NOT: the browser's
  // long-animation-frame reports name the callback they ran by its compile-time name, and perf-telemetry.ts
  // puts that name in its attribution key, so keeping identifiers keeps the function name readable across
  // rebuilds (a mangled build reports `feed.js:Qe`, a letter that changes with every rebuild; the key's
  // character position moves with any edit to the bundle either way). Stack traces and DevTools profiles
  // read better for the same reason. Measured cost (esbuild 0.21.5): +27% raw over all webview bundles
  // (feed.js 368 KB to 460 KB, render.js 801 KB to 1026 KB) and +13% gzipped, which is what the kernel
  // serves (feed.js 117 KB to 132 KB, render.js 247 KB to 280 KB).
  minifyWhitespace: production,
  minifySyntax: production,
  minifyIdentifiers: false,
  logLevel: "info",
};

// Unit tests for the pure modules (src/*.test.ts): bundled to out-tests/ and
// run with the built-in `node --test` runner — no extra test framework.
function testBuild() {
  // Tests live beside their sources: host tests in src/, the UI tests under
  // ../ui (timeline + quote) and ../ui/webview (feed/render/etc.). out-tests/
  // keeps each tree's structure (esbuild's outbase = the common ancestor), and
  // `node --test 'out-tests/**/*.test.js'` finds them recursively.
  const entries = ["src", "../ui", "../ui/webview"].flatMap((dir) =>
    fs
      .readdirSync(path.join(__dirname, dir))
      .filter((f) => f.endsWith(".test.ts"))
      .map((f) => dir + "/" + f),
  );
  /** @type {import('esbuild').BuildOptions} */
  return {
    entryPoints: entries,
    nodePaths: [path.join(__dirname, "node_modules")],
    bundle: true,
    // the compiler stays a runtime require: the writer census (ui/webview/writer-census.ts) parses render.ts with it, and
    // bundling it would add its whole source to every test file that imports the census
    external: ["typescript"],
    format: "cjs",
    platform: "node",
    target: "node18",
    outdir: "out-tests",
    sourcemap: "inline",
    logLevel: "info",
  };
}

// Every bundle of a one-shot build (`node esbuild.js`, with or without --production) is built into memory and
// dist/ is written only once ALL of them have built. esbuild writes each build()'s outputs as that build
// finishes, so with the extension bundle first and the webview bundle second, a webview failure left
// dist/extension.js rewritten and every webview bundle old.
// The common failure has exactly that shape: a merge imports a package this checkout's node_modules predates
// (the 2026-08-10 katex import broke every restart's rebuild for a day). The kernel's cache token is the
// newest mtime under dist/, so the half-written dist bumped it: every open dashboard was told a newer build
// existed and reloaded into the same stale bundles, and the kernel's drift pass then read dist as newer than
// the sources it had just failed to build. A failed build now leaves dist/ byte-for-byte as it was, so "the
// rebuild failed" and "dist is older than the sources" stay true together. Watch mode and the test build
// write directly as before: a dev loop wants each rebuild on disk at once.
//
// Each output then reaches its served name by RENAME, not by a write into it. The kernel serves /dist/ from
// this directory on other threads while it rebuilds, and a write into the served path truncates the file
// first and fills it afterwards, so a request in that window got an empty or partial bundle (longest for
// render.js, the largest output) and the page that loaded it ran a bundle cut mid-statement until a reload.
// So each output is written whole to a hidden sibling in its own directory (`.<name>.tmp-<pid>-<n>`: the same
// filesystem, so the rename is atomic; a name ending in neither .js nor .css nor .map, so the kernel's
// newest-mtime token (dist/*.js) never counts it and its /dist route never types it) and then renamed over
// the served name, and a reader gets the old bytes or the new, complete either way. Every output is staged
// before any is renamed, so an fs error while staging (no space, a permission) removes the staged files and
// leaves dist/ unchanged. A staging file an earlier run left behind (killed between its write and its rename)
// is removed once that run's pid is gone, and kept while the pid runs: two builds on one dist/ each rename
// their own.
const STAGING = /^\..+\.tmp-(\d+)-\d+$/;

function stagingPath(final, n) {
  return path.join(path.dirname(final), "." + path.basename(final) + ".tmp-" + process.pid + "-" + n);
}

function pidRunning(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { return !(e && e.code === "ESRCH"); }
}

function removeStaleStaging(dir) {
  for (const name of fs.readdirSync(dir)) {
    const m = STAGING.exec(name);
    if (m && !pidRunning(Number(m[1]))) fs.rmSync(path.join(dir, name), { force: true });
  }
}

async function buildAll(configs) {
  const results = [];
  for (const cfg of configs) results.push(await esbuild.build({ ...cfg, write: false }));
  const outputs = results.flatMap((r) => r.outputFiles);
  for (const dir of new Set(outputs.map((f) => path.dirname(f.path)))) {
    fs.mkdirSync(dir, { recursive: true });
    removeStaleStaging(dir);
  }
  const staged = [];
  try {
    for (const f of outputs) {
      const tmp = stagingPath(f.path, staged.length);
      fs.writeFileSync(tmp, f.contents);
      staged.push(tmp);
    }
    outputs.forEach((f, i) => fs.renameSync(staged[i], f.path));
  } catch (e) {
    for (const tmp of staged) fs.rmSync(tmp, { force: true });
    throw e;
  }
  return outputs.map((f) => f.path);
}

// The messages of an esbuild BuildFailure, each already printed with its code frame, or null for anything
// else: an fs error, a bug here, or an error object whose `errors` list is empty, which esbuild's API never
// yields (another library's error that carries an `errors` array). failureSummary and main().catch both read
// it, so such an error is treated like any other: its message is the line and its stack is printed, rather
// than read for a first message it has not got.
function buildErrors(e) {
  return e && Array.isArray(e.errors) && e.errors.length ? e.errors : null;
}

// The LAST line on stderr is the one the kernel shows: its in-place rebuild puts the tail of this process's
// stderr into the notice that tells the person the served UI is stale, and the tail of an esbuild
// BuildFailure printed whole is its stack through esbuild's own transport (`at Socket.emit`,
// `errors: [Getter/Setter]`), which names neither the failing import nor what to do about it. esbuild has
// already printed each error with its code frame (logLevel "info"), so this names the cause in one line of
// at most 300 characters (the kernel's tail: _rebuild_dist in kernel/kernel.py) and, for the common cause,
// the fix: an unresolvable package, a bare specifier or a node_modules/ path that is not a file of this
// checkout, is a dependency this node_modules lacks, and `npm install` is what fixes it. A relative import or
// a syntax error gets its text and no npm install line. `untouched` names the output dir the failed build
// left unchanged, when that is what failed.
//
// Every line goes out through `fit`: cut to 300 characters from the END, so the head (what failed, what is
// unchanged) is what the kernel shows and the tail of the last clause is what goes. The parts are sized so a
// plausible line fits whole (specifiers cut at 60, the text at 120, two named and the rest counted), so the
// cut is reached only by a long text over a long location path (the location is never shortened, since a cut
// path names no file) and by a non-esbuild error's message, which is whatever Node wrote. Without it, the
// kernel's `[-300:]` drops the head instead (src/esbuild-failure-cap.test.ts).
function failureSummary(e, untouched) {
  const fit = (s) => s.length > 300 ? s.slice(0, 297) + "..." : s;
  const errors = buildErrors(e);
  if (!errors) return fit("esbuild.js: build failed: " + (e && e.message ? e.message : String(e)));
  const n = errors.length;
  let line = "esbuild.js: build failed with " + n + (n === 1 ? " error" : " errors") +
             (untouched ? "; " + untouched + " is unchanged" : "") + ".";
  const unresolved = [];
  for (const x of errors) {
    const m = /^Could not resolve "([^"]+)"/.exec(x.text || "");
    if (m) unresolved.push(m[1]);
  }
  const isDep = (spec) => spec.startsWith("node_modules/") ||
    (!/^[./]/.test(spec) && !path.isAbsolute(spec) && !fs.existsSync(path.resolve(spec.split("/")[0])));
  if (unresolved.length) {
    const quote = (s) => JSON.stringify(s.length > 60 ? s.slice(0, 57) + "..." : s);
    const shown = unresolved.slice(0, 2).map(quote).join(", ") +
                  (unresolved.length > 2 ? " (+" + (unresolved.length - 2) + " more)" : "");
    line += unresolved.some(isDep)
      ? " Unresolved: " + shown +
        ", not in this checkout's node_modules; run npm install in vscode-extension/ and rebuild."
      : " Unresolved: " + shown + ".";
  } else {
    const x = errors[0];
    const where = x.location && x.location.file ? " (" + x.location.file + ":" + x.location.line + ")" : "";
    const text = (x.text || "").length > 120 ? (x.text || "").slice(0, 117) + "..." : (x.text || "");
    line += " " + text + where;
  }
  return fit(line);
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
    await buildAll([extension, webview]);
  }
}

// Exported for the tests: src/esbuild-build.test.ts and its siblings drive buildAll and failureSummary against
// synthetic entries and stub the real configs' entry points. The build runs only when this file is the script
// (`node esbuild.js`), never on require.
module.exports = { buildAll, failureSummary, extension, webview, testBuild };

if (require.main === module) {
  main().catch((e) => {
    // Not an esbuild BuildFailure (an fs error, a bug here): its stack is the diagnosis, and esbuild
    // printed nothing for it. A BuildFailure's errors are already on stderr with their code frames.
    if (!buildErrors(e)) console.error(e);
    console.error(failureSummary(e, tests || watch ? null : "dist/"));
    process.exit(1);
  });
}
