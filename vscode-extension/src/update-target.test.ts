// The self-update's exec target comes from THIS host, never from the kernel.
//
// `bash <dir>/install.sh` runs whatever <dir> holds, and <dir> used to be the rompDir a kernel
// reported on /version — an auth-exempt route, so any listener that owned the port (one that grabbed
// it before the real kernel) chose the directory a shell command ran from, then raised the drift
// prompt that gets it clicked. These pin the replacement: candidates are the extension's own
// installed path and ROMP_DIR from our own environment, each accepted only when it looks like a romp
// checkout, and a copy that isn't one resolves to nothing so the caller can say so out loud.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "path";
import {
  CANT_REBUILD, CHECKOUT_MARKERS, COPY_ACTION, INSTALL_COMMAND, MANUAL_REMEDY, UPDATE_ACTION,
  driftNotice, installCandidates, resolveInstallScript,
} from "./update-target";

// A tiny fake filesystem: the set of paths that "exist".
function fsWith(...dirs: { dir: string; files: string[] }[]) {
  const present = new Set<string>();
  for (const d of dirs) for (const f of d.files) present.add(path.join(d.dir, f));
  return (p: string) => present.has(p);
}

const CHECKOUT = path.join("/opt", "notes-api", "romp", "vscode-extension");   // a real source tree
const INSTALLED = path.join("/opt", "editor", "extensions", "romp.romp-chat-view-0.4.337");
const HOSTILE = path.join("/tmp", "attacker", "vscode-extension");             // what a squatter would name

test("a hostile rompDir never becomes the exec target — it is not even a candidate", () => {
  // The squatter's directory is a picture-perfect checkout; the only thing it lacks is being ours.
  const exists = fsWith({ dir: HOSTILE, files: CHECKOUT_MARKERS }, { dir: CHECKOUT, files: CHECKOUT_MARKERS });
  const cands = installCandidates(CHECKOUT, path.join("/opt", "notes-api", "romp"));
  assert.ok(!cands.some((c) => c.includes("attacker")), "nothing kernel-reported reaches the candidate list");
  const t = resolveInstallScript(CHECKOUT, path.join("/opt", "notes-api", "romp"), exists);
  assert.equal(t?.script, path.join(CHECKOUT, "install.sh"));
  assert.ok(!t!.script.includes("attacker") && !t!.dir.includes("attacker"));
});

test("an installed VSIX resolves to nothing rather than to someone else's install.sh", () => {
  // The packaged copy ships install.sh but not esbuild.js (.vscodeignore), so it cannot rebuild —
  // and with the hostile tree sitting right there, "nothing" is the only safe answer.
  const exists = fsWith({ dir: INSTALLED, files: ["install.sh", "package.json"] },
                        { dir: HOSTILE, files: CHECKOUT_MARKERS });
  assert.equal(resolveInstallScript(INSTALLED, undefined, exists), null);
  assert.equal(resolveInstallScript(INSTALLED, "", exists), null);
});

test("the extension's own path wins; ROMP_DIR from our own environment backs it up", () => {
  const repo = path.join("/opt", "notes-api", "romp");
  const exists = fsWith({ dir: CHECKOUT, files: CHECKOUT_MARKERS }, { dir: INSTALLED, files: ["install.sh"] });
  // Run from the checkout: the extension dir IS vscode-extension/.
  assert.deepEqual(resolveInstallScript(CHECKOUT, repo, exists),
    { dir: CHECKOUT, script: path.join(CHECKOUT, "install.sh") });
  // Installed copy, but this host was launched from a shell that exports ROMP_DIR — same trust as
  // whoever started VS Code, so the update still works where it legitimately can.
  const envOnly = fsWith({ dir: INSTALLED, files: ["install.sh"] }, { dir: path.join(repo, "vscode-extension"), files: CHECKOUT_MARKERS });
  assert.equal(resolveInstallScript(INSTALLED, repo, envOnly)?.dir, path.join(repo, "vscode-extension"));
});

test("both checkout markers are required — install.sh alone proves nothing", () => {
  for (const only of CHECKOUT_MARKERS) {
    assert.equal(resolveInstallScript(CHECKOUT, undefined, fsWith({ dir: CHECKOUT, files: [only] })), null,
      `${only} on its own must not qualify as a checkout`);
  }
  assert.ok(CHECKOUT_MARKERS.includes("install.sh") && CHECKOUT_MARKERS.includes("esbuild.js"));
});

test("an empty extension path contributes no candidate (no probing of /install.sh)", () => {
  assert.deepEqual(installCandidates("", undefined), []);
  assert.equal(resolveInstallScript("", undefined, () => true), null);
});

// ---- the drift toast only offers what can actually work here ----
// Resolving locally made "nothing to rebuild from" the ordinary case: .vscodeignore keeps the build
// inputs out of the VSIX and ROMP_DIR is exported into the kernel/service environment, not into the
// shell or GUI that launches VS Code. The toast kept its "Update extension" button anyway, so on
// essentially every installation its one action was guaranteed to fail (the review, 2026-08-05).

test("no resolvable checkout: the toast offers no action that would fail, and says what to run", () => {
  const n = driftNotice(null);
  assert.ok(!n.actions.includes(UPDATE_ACTION), "an Update button here can only end in an error toast");
  assert.deepEqual(n.actions, [COPY_ACTION], "the one offered action is client-side and cannot fail");
  // The remedy is in the MESSAGE too — a toast fades, and the user should not need the button to
  // learn what to do.
  assert.ok(n.message.includes(INSTALL_COMMAND), "names the command to run");
  assert.ok(n.message.includes("terminal"), "says where to run it");
  assert.ok(n.message.includes(CANT_REBUILD) && n.message.includes(MANUAL_REMEDY));
  assert.ok(n.message.includes("newer romp build"), "still leads with the drift itself");
});

test("a resolvable checkout keeps the one-click update, with nothing added to distract from it", () => {
  const dir = CHECKOUT;
  const n = driftNotice({ dir, script: path.join(dir, "install.sh") });
  assert.deepEqual(n.actions, [UPDATE_ACTION], "the existing one-click flow is unchanged");
  assert.equal(n.message, "A newer romp build is available — these panes run an older extension bundle.");
  assert.ok(!n.message.includes(INSTALL_COMMAND), "no terminal instructions where the button works");
});

test("the notice's actions follow the SAME resolution the click will do — no second opinion", () => {
  // Toast time and click time must agree about this host; a copy that resolves gets the button, one
  // that doesn't never sees it. (They are separate CALLS, deliberately: the filesystem can change
  // under a toast that is still on screen, and only the click-time check may shell out.)
  const repo = path.join("/opt", "notes-api", "romp");
  const checkout = fsWith({ dir: CHECKOUT, files: CHECKOUT_MARKERS });
  const packaged = fsWith({ dir: INSTALLED, files: ["install.sh", "package.json"] });
  for (const [ext, env, exists, want] of [
    [CHECKOUT, undefined, checkout, [UPDATE_ACTION]],
    [INSTALLED, undefined, packaged, [COPY_ACTION]],
    [INSTALLED, repo, packaged, [COPY_ACTION]],       // ROMP_DIR set but no checkout under it
  ] as [string, string | undefined, (p: string) => boolean, string[]][]) {
    assert.deepEqual(driftNotice(resolveInstallScript(ext, env, exists)).actions, want);
  }
});
