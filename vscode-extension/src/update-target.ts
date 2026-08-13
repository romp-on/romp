// update-target — WHERE the one-click self-update runs, decided LOCALLY and never off the wire.
//
// updateExtension() shells out to `bash <dir>/install.sh`, so <dir> is an EXECUTION target and may
// only come from something we already trust: this VSIX's own installed location
// (context.extensionPath) or ROMP_DIR out of the extension host's own environment — both set by
// whoever launched VS Code, i.e. the user. It used to come from `rompDir` on the kernel's /version,
// which is the wrong kind of source: /version is auth-exempt, so anything answering on the kernel
// port (a local process that grabbed it before the real kernel, say) got to name the directory a
// shell command ran from — and that same listener's keepalive `dv` raises the "newer build" prompt
// that invites the click. A path that arrives over a socket is not a path to run.
//
// A candidate must look like a romp CHECKOUT, not merely a directory holding an install.sh:
// .vscodeignore drops the build inputs (esbuild.js, src/**, and install.sh itself since 2026-08-05),
// and a copy without them can't rebuild anything — copies packaged before that date still carry
// install.sh with no esbuild.js beside it, which is exactly the shape the pair of markers rejects.
// Requiring BOTH is what separates "running from a checkout" — where the update genuinely works —
// from "installed from a .vsix", which resolves to nothing so the caller can say so plainly and
// point at the terminal (fail loudly, don't degrade).
import * as path from "path";

// Present together only in a real vscode-extension/ source dir: install.sh does the build+package,
// esbuild.js is the build it invokes. A packaged install carries the first without the second.
export const CHECKOUT_MARKERS = ["install.sh", "esbuild.js"];

export interface InstallTarget {
  dir: string;      // the vscode-extension/ dir to run in (install.sh cd's here itself)
  script: string;   // <dir>/install.sh
}

// The dirs worth probing, best first. Both are this host's own knowledge of itself; nothing here
// consults the kernel.
export function installCandidates(extensionPath: string, rompDirEnv?: string): string[] {
  const out: string[] = [];
  const ext = (extensionPath || "").trim();
  if (ext) out.push(ext);                                        // run from a checkout: the extension dir IS vscode-extension/
  const repo = (rompDirEnv || "").trim();
  if (repo) out.push(path.join(repo, "vscode-extension"));       // a host launched from a romp shell/service knows the repo
  return out;
}

// The first candidate that is a romp checkout, or null when this copy can't rebuild itself.
export function resolveInstallScript(
  extensionPath: string,
  rompDirEnv: string | undefined,
  exists: (p: string) => boolean,
): InstallTarget | null {
  for (const dir of installCandidates(extensionPath, rompDirEnv)) {
    if (CHECKOUT_MARKERS.every((m) => exists(path.join(dir, m)))) {
      return { dir, script: path.join(dir, "install.sh") };
    }
  }
  return null;
}

// ---- what the drift toast is allowed to OFFER ----
//
// Resolving locally made "no target" the COMMON case, not the exotic one: on any install that
// vscode-extension/install.sh produces there is no checkout under the VSIX, and ROMP_DIR is exported
// into the kernel/service environment (bin/romp-serve, bin/romp-service) — never into the shell or
// the GUI session that launches VS Code. The drift toast kept offering "Update extension" anyway, so
// its one button was guaranteed to fail on nearly every installation (an adversarial review of that
// change, 2026-08-05). An action that cannot succeed is worse than no action: it costs a click, a
// wait and an error toast to learn what the message could have said outright. So the notice picks its
// buttons from a LIVE resolution and, when there is no target, offers the one thing that always
// works — putting the command on the clipboard — with the remedy spelled out in the message itself.
export const UPDATE_ACTION = "Update extension";
export const COPY_ACTION = "Copy install command";

// What to paste into a terminal, run from the top of a romp checkout.
export const INSTALL_COMMAND = "bash vscode-extension/install.sh";
export const CANT_REBUILD =
  "This copy of the extension runs from a packaged VSIX, not a romp checkout, so it can't rebuild itself.";
export const MANUAL_REMEDY =
  `Run ${INSTALL_COMMAND} from your romp checkout in a terminal, then reload this window.`;

export interface DriftNotice {
  message: string;
  actions: string[];   // toast buttons, in order — never one that cannot succeed on this host
}

// The build-drift toast, given what this host resolved. Pure, so the wiring in extension.ts (which
// can't be imported under node --test — it pulls in vscode) is a one-line hand-off.
export function driftNotice(target: InstallTarget | null): DriftNotice {
  const lead = "A newer romp build is available — these panes run an older extension bundle.";
  if (target) return { message: lead, actions: [UPDATE_ACTION] };
  return { message: `${lead} ${CANT_REBUILD} ${MANUAL_REMEDY}`, actions: [COPY_ACTION] };
}
