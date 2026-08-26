// The romp VS Code extension — a THIN CLIENT of the romp web kernel.
//
// All host logic (transcript parsing, session mirroring, the feed fold, tmux
// driving, record-file IO) lives in the kernel (bin/romp-kernel, spawned via
// bin/romp-serve). This extension only:
//   1. ensures a kernel is running (spawn-or-attach on the default port,
//      restarting a stale one once after a VSIX update),
//   2. hosts the four webview surfaces — chat, feed, and outline/fleet
//      (editor panels) plus the timeline (a native bottom-panel view) — and
//      pipes their postMessage traffic over the kernel's WS protocol verbatim,
//   3. supplies the few genuinely CLIENT-side capabilities: opening files in
//      the editor, the OS file picker, the clipboard, external links, and
//      panel reveal/focus orchestration.
//
// The browser pages the kernel serves are this same pipe minus VS Code — both
// front ends are clients of one kernel, sharing tabs with per-client focus.
import * as vscode from "vscode";
import * as fs from "fs";
import * as http from "http";
import * as path from "path";
import * as os from "os";
import { execFile } from "child_process";
import WebSocket from "ws";
import { chatBody, FEED_BODY, FLEET_BODY, TIMELINE_BODY, ATTACH_TITLE_VSCODE } from "./page-skeleton";
import { ensureThenAttach, parseHealthz, warnAfter } from "./kernel-attach";
import { intentOp } from "./pipe-intent";
import { routeViewMessage } from "./view-routing";
import { deriveStatus, freshNeedsYou, renderStatusBar, statusTooltipLines, FleetStatus } from "./fleet-status";
import { citeText, sessionsForWorkspace, SessionInfo } from "./workspace-sessions";
import { parsePorcelain } from "./session-diff";
import { buildMenu, usageSummary } from "./romp-menu";
import { resolveInstallScript, driftNotice, UPDATE_ACTION, COPY_ACTION, INSTALL_COMMAND } from "./update-target";

const HOST = "127.0.0.1";

// The kernel serve token — required on EVERY kernel request, loopback included (Jupyter's model:
// the 0600 state file, not the socket, is the same-user trust boundary; /healthz and /version stay
// exempt). Same resolution order as the kernel's _load_token: env override, else the state file.
// Read per call (a tiny local file): a freshly minted token is picked up on the next
// fetch/reconnect without a window reload.
function serveToken(): string {
  const env = (process.env.ROMP_SERVE_TOKEN || "").trim();
  if (env) return env;
  try {
    const base = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
    const root = process.env.ROMP_STATE_DIR || path.join(base, "romp");   // per-kernel state root (plans/multi-kernel.md)
    return fs.readFileSync(path.join(root, "serve-token"), "utf8").trim();
  } catch {
    return "";
  }
}

// Build-drift banner (the user 2026-07-13, who wanted a banner when anything gets out of sync).
// __ROMP_BUILD__ is baked by esbuild.js at bundle time (epoch seconds); every kernel keepalive carries
// `dv`, the kernel's current dist token (newest dist/*.js mtime, same clock). dv newer than this bundle
// means the shared webview sources were rebuilt after this VSIX was packaged — the panes are rendering
// live kernel payloads with outdated code. Prompt ONCE per window; a webview reload can't fix it (the
// code is baked into the on-disk VSIX), so unlike the browser's Reload the prompt offers a real
// "Update extension" that rebuilds + reinstalls the VSIX for the user (updateExtension below). The
// passive status pipe never calls this (it must not toast — vscode-four-surfaces).
declare const __ROMP_BUILD__: number;
const BUILD_STAMP: number = typeof __ROMP_BUILD__ === "number" ? __ROMP_BUILD__ : 0;
let buildNotified = false;
// The drift TOAST is native VS Code chrome — it can't be dragged out of the way, and dismissing it
// used to lose the update affordance entirely (the user 2026-08-18: "I need to access something
// behind it while I'm waiting"). So the toast is only the one-time attention grab; a status-bar item
// carries the same action persistently until the update succeeds, making the toast safe to dismiss.
let driftStatusItem: vscode.StatusBarItem | null = null;
function showDriftStatusItem(hasTarget: boolean): void {
  if (driftStatusItem) return;
  driftStatusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  driftStatusItem.text = "$(sync) romp update";
  driftStatusItem.tooltip = hasTarget
    ? "A newer romp build is available — click to rebuild and reinstall the extension."
    : "A newer romp build is available — click to copy the install command (this VSIX can't rebuild itself).";
  driftStatusItem.command = hasTarget ? "rompChat.updateExtension" : "rompChat.copyInstallCommand";
  driftStatusItem.show();
}
function clearDriftStatusItem(): void {
  if (!driftStatusItem) return;
  driftStatusItem.dispose();
  driftStatusItem = null;
}
function maybeBuildNotice(dv: unknown): void {
  if (buildNotified || !BUILD_STAMP || typeof dv !== "number" || dv <= BUILD_STAMP) return;
  buildNotified = true;
  // WHICH buttons the toast offers is decided by a LOCAL resolution (this VSIX's own path or ROMP_DIR
  // from our own environment — update-target.ts), never off the wire. A copy that is a real checkout can
  // rebuild, so it gets the one-click Update; one that can't (a packaged VSIX — the ordinary case) is
  // offered the client-side "copy the install command" and told the remedy in the message, instead of a
  // button that could only end in an error toast (driftNotice). updateExtension RE-RESOLVES at click
  // time, deliberately — the filesystem can change under a toast still on screen, and only the click may
  // shell out.
  const target = resolveInstallScript(ctx?.extensionPath || "", process.env.ROMP_DIR, (p) => fs.existsSync(p));
  const notice = driftNotice(target);
  showDriftStatusItem(!!target);
  void vscode.window.showInformationMessage(notice.message, ...notice.actions).then((choice) => {
    if (choice === UPDATE_ACTION) void updateExtension();
    else if (choice === COPY_ACTION) void vscode.env.clipboard.writeText(INSTALL_COMMAND);
  });
}

// Self-update: rebuild + repackage + reinstall the VSIX so a drifted pane heals with a click (the user
// 2026-07-14: "I want a button that does this for me, like the web view has" — the browser's drift
// banner just reloads, but VS Code loads bundled code from the on-disk VSIX, so a reload changes
// nothing; the fix is the SAME vscode-extension/install.sh a user would run by hand). We run it from the
// extension HOST, not the kernel: the host carries VS Code's resolved shell environment (the reliable
// PATH with node/npm/npx/code), whereas a launchd/manager-spawned kernel often does not.
//
// WHERE it runs is decided LOCALLY — this VSIX's own path, else ROMP_DIR from our own environment
// (update-target.ts). It used to be whatever the kernel reported as rompDir on /version, an
// AUTH-EXEMPT route: anything answering on the kernel port could then choose the directory we ran a
// shell command from, and drive the prompt that invites the click besides. When this copy isn't a
// checkout it can't rebuild anything, so we say so and point at the terminal rather than running some
// other install.sh. Reload stays a user click, never automatic (prefer-reload-banner-not-auto).
let updating = false;
async function updateExtension(): Promise<void> {
  if (updating) return;                                    // one run per host (double-click, or toast + palette)
  const target = resolveInstallScript(ctx?.extensionPath || "", process.env.ROMP_DIR, (p) => fs.existsSync(p));
  if (!target) {
    void vscode.window.showErrorMessage(
      "romp: this copy of the extension can't rebuild itself — it runs from a packaged VSIX, not a romp checkout. " +
      "Run vscode-extension/install.sh in your romp checkout from a terminal, then reload this window.");
    return;
  }
  const extDir = target.dir;
  const script = target.script;
  updating = true;
  try {
    // ProgressLocation.Window = a status-bar spinner: the minute-long rebuild+reinstall covers
    // NOTHING while the user waits (a Notification-located progress toast sits over the editor and
    // cannot be moved — native chrome; the user 2026-08-18).
    const out = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: "romp: updating the extension…", cancellable: false },
      () => runInstall(script, extDir));
    // install.sh exits 0 even when it SKIPS (no node / no editor CLI found) — so a clean exit is NOT
    // proof it worked. The real success markers are that it packaged the VSIX AND installed into a CLI.
    // Anything else is a failure we surface loudly with the manual remedy (fail loudly, don't degrade).
    const ok = out.code === 0 && /packaged romp-chat-view\.vsix/.test(out.text) && /install into:/.test(out.text);
    if (ok) {
      clearDriftStatusItem();
      void vscode.window.showInformationMessage(
        "romp extension updated — reload this window to apply.", "Reload window").then((choice) => {
          if (choice === "Reload window") void vscode.commands.executeCommand("workbench.action.reloadWindow");
        });
    } else {
      void vscode.window.showErrorMessage(
        "romp: the extension update didn't complete — " + updateHint(out) +
        " You can run vscode-extension/install.sh in a terminal.");
    }
  } finally {
    updating = false;
  }
}

// Run vscode-extension/install.sh via bash, inheriting the host's resolved env (its PATH finds
// node/npm/npx/code). install.sh cd's to its own dir; capture stdout+stderr and the exit code so the
// caller can tell a real install from a graceful skip. 5-minute cap (npm install + vsce package).
function runInstall(script: string, cwd: string): Promise<{ code: number; text: string }> {
  return new Promise((resolve) => {
    execFile("bash", [script], { cwd, env: process.env, maxBuffer: 16 * 1024 * 1024, timeout: 300000 },
      (err, stdout, stderr) => {
        const text = String(stdout || "") + String(stderr || "");
        const code = err && typeof (err as { code?: unknown }).code === "number"
          ? ((err as { code: number }).code) : err ? 1 : 0;
        resolve({ code, text });
      });
  });
}

// A short, human reason the update didn't land — surfaced in the failure toast.
function updateHint(out: { code: number; text: string }): string {
  const t = out.text;
  if (/node not found/.test(t)) return "node isn't on the extension host's PATH.";
  if (/No VS Code-family editor CLI found/.test(t)) return "no editor CLI was found to install into.";
  const last = t.trim().split(/\r?\n/).filter((l) => l.trim()).slice(-1)[0] || "";
  return last ? "the build reported: " + last.slice(0, 200) : "see the terminal for details.";
}

// Ports are CONFIGURABLE so different VS Code windows can attach to different kernels (each kernel
// scopes its own group of agents). Precedence: the VS Code setting (if set) → env var → default.
function cfgPort(key: "kernelPort" | "managerPort", env: string | undefined, dflt: number): number {
  const v = vscode.workspace.getConfiguration("romp").get<number>(key);
  if (typeof v === "number" && v > 0) return v;
  return Number(env) || dflt;
}
// Either spelling of the kernel's listen port (bin/romp-serve owns that seam), so a window opened
// from a shell that exported only the documented ROMP_KERNEL_PORT attaches to that kernel instead
// of silently trying the default one.
function kernelPort(): number { return cfgPort("kernelPort", process.env.ROMP_SERVE_PORT || process.env.ROMP_KERNEL_PORT, 29855); }
function managerPort(): number { return cfgPort("managerPort", process.env.ROMP_MANAGER_PORT, 7432); }

let ctx: vscode.ExtensionContext;
let extUri: vscode.Uri;
let panel: vscode.WebviewPanel | undefined;
let feedPanel: vscode.WebviewPanel | undefined;
let fleetPanel: vscode.WebviewPanel | undefined;
let chatPipe: KernelPipe | undefined;
let feedPipe: KernelPipe | undefined;
let timelinePipe: KernelPipe | undefined;
let fleetPipe: KernelPipe | undefined;
// Webview-cold replays: a deep link / picker-open that arrived before the chat
// webview signalled "ready" is re-sent when the ready flows past us.
let pendingToWebview: any[] = [];
// Resolver for an in-flight rompChat.pickSession() call (cross-extension picker).
type PickValue = { id: string; name: string } | { createNew: true };
let pendingPick: ((v: PickValue | undefined) => void) | null = null;

export function activate(context: vscode.ExtensionContext) {
  ctx = context;
  extUri = context.extensionUri;
  context.subscriptions.push(
    vscode.window.registerWebviewPanelSerializer("rompChat", {
      async deserializeWebviewPanel(webviewPanel) { wirePanel(webviewPanel); },
    }),
    vscode.window.registerWebviewPanelSerializer("rompFeed", {
      async deserializeWebviewPanel(webviewPanel) { wireFeedPanel(webviewPanel); },
    }),
    vscode.window.registerWebviewPanelSerializer("rompFleet", {
      async deserializeWebviewPanel(webviewPanel) { wireFleetPanel(webviewPanel); },
    }),
    // Timeline is a native VIEW (bottom panel by default — the user can drag
    // it anywhere), resolved lazily when first shown. Outline is an editor
    // TAB like chat/feed (the sidebar home proved undiscoverable, 2026-07-13).
    vscode.window.registerWebviewViewProvider("rompTimeline",
      { resolveWebviewView: (v) => wireTimelineView(v) },
      { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.window.registerUriHandler({ handleUri: onDeepLink }),
    vscode.commands.registerCommand("rompChat.open", async () => {
      // Purely idempotent (the user 2026-07-14): open/reveal the surfaces and
      // NOTHING else — no session picker on re-click (the strip's quick-opens
      // and the + tab cover adding sessions).
      openPanel();
      const chatCol = panel?.viewColumn;
      // Outline rides as a TAB in the chat's group; feed gets the group to the
      // right (the user 2026-07-13). Reveal order leaves the chat tab active.
      openFleetPanel(true, chatCol);
      openFeedPanel(true, chatCol !== undefined ? ((chatCol as number) + 1) as vscode.ViewColumn : undefined);
      // Bring the timeline up without stealing focus from the chat panel.
      try { await vscode.commands.executeCommand("rompTimeline.focus", { preserveFocus: true }); } catch { /* view unavailable */ }
      panel?.reveal(panel.viewColumn ?? vscode.ViewColumn.Beside, false);
    }),
    vscode.commands.registerCommand("rompChat.openFeed", () => openFeedPanel()),
    vscode.commands.registerCommand("rompChat.openTimeline", () => vscode.commands.executeCommand("rompTimeline.focus")),
    vscode.commands.registerCommand("rompChat.openFleet", () => openFleetPanel()),
    vscode.commands.registerCommand("rompChat.menu", rompMenu),
    // Rebuild + reinstall the VSIX from source, then offer a reload — the clickable form of the
    // drift toast's remedy, always reachable (a faded toast leaves nothing to click). See updateExtension.
    vscode.commands.registerCommand("rompChat.updateExtension", updateExtension),
    vscode.commands.registerCommand("rompChat.copyInstallCommand",
      () => vscode.env.clipboard.writeText(INSTALL_COMMAND)),
    // The webviews scale to the editor font (uiZoom) — re-render them when it changes.
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("editor.fontSize")) refreshWebviewHtml();
    }),
    vscode.commands.registerCommand("rompChat.addSession", () => { openPanel(); toWebview({ type: "openPicker", pick: false }); }),
    vscode.commands.registerCommand("rompChat.pickSession", (arg?: unknown) =>
      pickSessionExternal(
        typeof arg === "string" ? { prompt: arg }
          : arg && typeof arg === "object" ? (arg as { prompt?: string; allowNew?: boolean })
          : {})),
    vscode.commands.registerCommand("rompChat.nextTab", () => panel?.webview.postMessage({ type: "nextTab" })),
    vscode.commands.registerCommand("rompChat.prevTab", () => panel?.webview.postMessage({ type: "prevTab" })),
    vscode.commands.registerCommand("rompChat.openCurrent", () => {
      const ed = vscode.window.activeTextEditor;
      if (ed && ed.document.fileName.endsWith(".jsonl")) {
        openPanel();
        chatPipe?.send({ type: "openTranscript", file: ed.document.fileName });
      } else {
        vscode.window.showWarningMessage("romp: open a .jsonl transcript first.");
      }
    }),
    vscode.commands.registerCommand("rompChat.citeInComposer", citeInComposer),
    // HIGHLIGHT-TO-REPLY from an editor (the user 2026-07-13): selecting text in a real file seeds the
    // chat composer's quote chip, exactly like highlighting text inside the chat transcript — the chip
    // carries the highlight + its file:lines origin, and the send wraps both around the typed message.
    // Event-based; only non-empty selections in file-scheme documents qualify; a COLLAPSE (deselect /
    // click away) posts editorSelectionCleared so the webview drops that chip — an abandoned highlight
    // shouldn't leave stale context (the user 2026-07-14). The webview clears ONLY the editor-seeded chip
    // and only while the composer is empty, so an in-progress reply keeps its quote and a
    // transcript-highlight chip is never touched. Clicking into the chat webview doesn't fire this event
    // (a webview isn't a text editor), so the "click into chat to type" flow keeps the chip. A selection
    // never SUMMONS the panel — no open chat, no seed. (citeInComposer above stays the manual
    // path-citation command; this quotes the CONTENT.)
    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (!panel || e.textEditor.document.uri.scheme !== "file") return;
      const sel = e.selections[0];
      if (!sel || sel.isEmpty) { toWebview({ type: "editorSelectionCleared" }); return; }   // deselect → drop the chip
      const text = e.textEditor.document.getText(sel);
      if (!text.trim()) return;
      const rel = vscode.workspace.asRelativePath(e.textEditor.document.uri, false);
      // a selection ending at column 0 visually excludes that line (same rule as citeInComposer)
      const endLine = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
      const src = rel + ":" + (sel.start.line + 1) + (endLine > sel.start.line + 1 ? "-" + endLine : "");
      toWebview({ type: "editorSelection", text: text.slice(0, 4000), src });
    }),
    vscode.commands.registerCommand("rompChat.openSessionWorktree", openSessionWorktree),
    vscode.commands.registerCommand("rompChat.diffSessionChanges", diffSessionChanges),
    // HEAD side of the session-diff editor: romp-git:/<rel>?<json {dir,rel}>
    vscode.workspace.registerTextDocumentContentProvider("romp-git", {
      provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
        try {
          const q = JSON.parse(uri.query);
          if (!q.rel) return Promise.resolve("");   // untracked: no HEAD side
          return gitIn(String(q.dir), ["show", `HEAD:${String(q.rel)}`]).catch(() => "");
        } catch { return Promise.resolve(""); }
      },
    }),
  );
  startFleetStatus(context);
}

export function deactivate() {
  // Attach-only: VS Code does NOT own the kernel (the `romp up` manager does), so there's nothing to
  // reap here — closing/reloading VS Code just drops our attach; the kernel keeps running.
  chatPipe?.dispose();
  feedPipe?.dispose();
  timelinePipe?.dispose();
  fleetPipe?.dispose();
  statusPipe?.dispose();
}

// Post into the chat webview, deferring until its "ready" if it's still cold
// (a freshly-created webview silently drops postMessage).
function toWebview(msg: any) {
  if (!panel) return;
  if (chatPipe?.webviewReady) panel.webview.postMessage(msg);
  else pendingToWebview.push(msg);
}

// Same deferral for the feed webview (the menu's Settings opens the gear in a
// possibly just-created feed panel).
let pendingToFeed: any[] = [];
function toFeedWebview(msg: any) {
  if (!feedPanel) return;
  if (feedPipe?.webviewReady) feedPanel.webview.postMessage(msg);
  else pendingToFeed.push(msg);
}

// The romp strip's feed-over-chat rule (the user 2026-07-13): both panes carry
// the strip, but when the feed panel is VISIBLE the chat hides its copy — one
// strip on screen, the feed's preferred. Event-driven: feed create/dispose/
// view-state changes and the chat's ready all re-derive it.
function openPaneByKey(pane: string) {
  if (pane === "chat") openPanel(false);
  else if (pane === "fleet") openFleetPanel();
  else if (pane === "feed") openFeedPanel(false);
}

function updateStrips() {
  toWebview({ type: "stripShow", show: !(feedPanel && feedPanel.visible) });
  // Quick-open labels show only for panes NOT on screen (the user 2026-07-13).
  const hidden = {
    chat: !(panel && panel.visible),
    fleet: !(fleetPanel && fleetPanel.visible),
    feed: !(feedPanel && feedPanel.visible),
  };
  toWebview({ type: "stripPanes", hidden });
  toFeedWebview({ type: "stripPanes", hidden });
}

// A gear save in one webview → every OTHER surface applies it. Each VS Code
// webview has its own synthetic origin, so its own localStorage: the browser's
// storage-event sync between panes simply doesn't exist here, and a compact
// toggle made in the feed's gear left the chat transcript unchanged (the user
// 2026-07-14). gear.js posts {settingsSync} on every save; this fans it out.
function broadcastSettings(settings: unknown, from?: vscode.Webview) {
  for (const w of [panel?.webview, feedPanel?.webview, fleetPanel?.webview, timelineView?.webview]) {
    if (w && w !== from) w.postMessage({ type: "settingsSync", settings });
  }
}

// The optimistic colour echo rides the same fan-out (the user 2026-08-08): a tab-menu swatch pick in
// the chat pane repaints the FEED's cards immediately, instead of waiting out the kernel's next feed
// rebuild. The kernel still gets setSessionColor and its re-broadcast reconciles; this is display-only.
function broadcastColorSync(m: { sid?: unknown; bg?: unknown }, from?: vscode.Webview) {
  if (typeof m.sid !== "string" || typeof m.bg !== "string") return;
  for (const w of [panel?.webview, feedPanel?.webview, fleetPanel?.webview, timelineView?.webview]) {
    if (w && w !== from) w.postMessage({ type: "colorSync", sid: m.sid, bg: m.bg });
  }
}

// ---- the kernel: ENSURE-THEN-ATTACH (the manager owns it; we never spawn) ----
// VS Code does NOT spawn the kernel. It attaches to a manager-owned kernel on romp.kernelPort; if none
// is there, it asks the `romp up` manager to ENSURE one (the manager spawns + owns it), waits for it,
// and attaches. A second front-end spawner would fight the manager for the port and re-create the
// invisible-orphan problem — so the only spawner is ever the manager (the user's 2026-06-13 ruling).
// The decision sequence lives in ./kernel-attach (headless-testable); ensureKernel just supplies the
// VS Code-flavoured deps (real healthz, a manager POST, real sleep) and turns failures into a toast.

function healthz(): Promise<{ ok: boolean; version?: string }> {
  return new Promise((resolve) => {
    const req = http.get({ host: HOST, port: kernelPort(), path: "/healthz", timeout: 1500 }, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => resolve(parseHealthz(res.statusCode, body)));
    });
    req.on("timeout", () => { req.destroy(); resolve({ ok: false }); });
    req.on("error", () => resolve({ ok: false }));
  });
}

let ensuring: Promise<boolean> | null = null;
let notRunningWarned = false;
let ensureFails = 0;   // consecutive failed rounds — one is a transient (a kernel restart), see warnAfter
function ensureKernel(): Promise<boolean> {
  if (ensuring) return ensuring;
  ensuring = (async () => {
    const res = await ensureThenAttach({
      healthz: async () => (await healthz()).ok,
      ensureViaManager: () => askManagerEnsure(kernelPort()),
      delay: (ms) => new Promise((r) => setTimeout(r, ms)),
    });
    if (res.ok) { notRunningWarned = false; ensureFails = 0; return true; }
    ensureFails++;
    // Point the user at the fix, once (not on every panel mount / reconnect
    // poll) — and only when the failure PERSISTS across rounds: attaching in
    // the middle of a `romp refresh` fails one round and self-heals on the
    // pipes' retry, and that transient must not toast (the user 2026-07-13).
    if (!notRunningWarned && warnAfter(ensureFails)) {
      notRunningWarned = true;
      const port = kernelPort();
      vscode.window.showErrorMessage(
        res.reason === "no-manager"
          ? `romp: no kernel on port ${port} and no manager on :${managerPort()} — start it with \`romp up\` in a terminal.`
          : `romp: the manager couldn't bring up a kernel on port ${port} — is that port already in use? Check \`romp status\`.`,
      );
    }
    return false;
  })();
  const p = ensuring;
  void p.finally(() => { if (ensuring === p) ensuring = null; });
  return p;
}

// POST the manager's /ensure?port=N so it spawns+owns a kernel there. Resolves true iff a manager
// answered (i.e. one is running) — we never spawn the kernel ourselves.
function askManagerEnsure(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      { host: HOST, port: managerPort(), path: `/ensure?port=${port}`, method: "POST", timeout: 4000 },
      (res) => { res.resume(); resolve((res.statusCode ?? 500) < 400); });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
    req.end();
  });
}

// ---- the pipe: one WebSocket per panel, postMessage in both directions ----

class KernelPipe {
  private ws: WebSocket | null = null;
  private queue: { s: string; intent: boolean }[] = [];
  private alive = true;
  private everConnected = false;
  webviewReady = false;
  constructor(
    private app: "chat" | "feed" | "timeline" | "fleet",
    private onDown: (m: any) => void,
    private onReconnect: () => void,
    private onState?: (up: boolean, queuedIntents?: number) => void,
    // A passive pipe OBSERVES: it polls healthz and attaches when a kernel is
    // there, but never asks the manager to spawn one and never toasts — the
    // ambient status bar must not resurrect a kernel the user turned off.
    private passive = false,
  ) {
    void this.connect();
  }
  queuedIntents(): number {
    return this.queue.reduce((n, q) => n + (q.intent ? 1 : 0), 0);
  }
  send(m: any) {
    const s = JSON.stringify(m);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(s);
    else {
      // Held, not dropped — and the pane is told, so a message sent into a
      // down pipe never reads as delivered while it sits in limbo.
      this.queue.push({ s, intent: intentOp(m?.type) });
      this.onState?.(false, this.queuedIntents());
    }
  }
  private async connect() {
    if (!this.alive) return;
    const ok = this.passive ? (await healthz()).ok : await ensureKernel();
    if (!this.alive) return;
    if (!ok) { this.onState?.(false, this.queuedIntents()); setTimeout(() => void this.connect(), 5000); return; }
    // One window-group id per VS Code window: the kernel routes a feed click's
    // focus to THIS window's chat panel (same mechanism as the combined
    // browser page's panes).
    const ws = new WebSocket(`ws://${HOST}:${kernelPort()}/ws?app=${this.app}&wid=${encodeURIComponent(vscode.env.sessionId)}&token=${encodeURIComponent(serveToken())}`);
    this.ws = ws;
    ws.on("open", () => {
      if (!this.alive) { ws.close(); return; }
      this.onState?.(true);
      if (this.everConnected) {
        // A reconnect after a kernel restart: the kernel lost this client's
        // view state, so reload the webview — its fresh "ready" resyncs
        // everything. USER INTENT still delivers first: a typed message or an
        // explicit pick queued while the pipe was down is the user's work, and
        // wiping it with the view chatter silently ate a card reply sent
        // during a restart window (the user 2026-07-21, roof).
        const keep = this.queue.filter((q) => q.intent);
        this.queue = [];
        for (const q of keep) ws.send(q.s);
        this.webviewReady = false;
        this.onReconnect();
      } else {
        this.everConnected = true;
        for (const q of this.queue) ws.send(q.s);
        this.queue = [];
      }
    });
    ws.on("message", (data) => {
      if (!this.alive) return;
      let m: any;
      try { m = JSON.parse(String(data)); } catch { return; }
      // keepalive carries the kernel's dist build token — drift vs this bundle's stamp → one banner.
      // Panel pipes only: the passive status pipe observes and never toasts.
      if (m && m.type === "ka" && !this.passive) maybeBuildNotice(m.dv);
      this.onDown(m);
    });
    const reconnect = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.onState?.(false, this.queuedIntents());
      if (this.alive) setTimeout(() => void this.connect(), 1500);
    };
    ws.on("close", reconnect);
    ws.on("error", reconnect);
  }
  dispose() {
    this.alive = false;
    try { this.ws?.close(); } catch { /* ignore */ }
    this.ws = null;
  }
}

// ---- panels ----

function openPanel(preserveFocus = false) {
  if (panel) {
    panel.reveal(panel.viewColumn ?? vscode.ViewColumn.Beside, preserveFocus);
    return;
  }
  const p = vscode.window.createWebviewPanel(
    "rompChat",
    "romp chat",
    { viewColumn: vscode.ViewColumn.Beside, preserveFocus },
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
    },
  );
  wirePanel(p);
  vscode.commands.executeCommand("workbench.action.lockEditorGroup");
}

function wirePanel(p: vscode.WebviewPanel) {
  panel = p;
  p.webview.options = {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
  };
  p.iconPath = vscode.Uri.joinPath(extUri, "media", "romp-swirl.svg");
  p.webview.html = buildHtml(p.webview);
  const pipe = new KernelPipe(
    "chat",
    (m) => {
      if (m.type === "kernelToast") { vscode.window.setStatusBarMessage(`romp: ${m.text}`, 5000); return; }
      p.webview.postMessage(m);
    },
    () => { pendingToWebview = []; p.webview.html = buildHtml(p.webview); },
    // Pipe state → the pane's own banner: while the socket is down the webview
    // must say so (and how many typed messages are held), never sit silently
    // frozen on its last frame (the user 2026-07-21, roof).
    (up, queued) => { void p.webview.postMessage({ type: "pipeState", up, queued: queued ?? 0 }); },
  );
  chatPipe = pipe;
  p.webview.onDidReceiveMessage((m) => {
    if (!m) return;
    // CLIENT capabilities — VS Code does these locally; the browser shim has
    // its own versions. Everything else goes to the kernel verbatim.
    if (m.type === "openFile" && m.path) { openFileInEditor(String(m.path), m.line); return; }
    if (m.type === "openLink" && typeof m.href === "string") { openLink(String(m.href)); return; }
    if (m.type === "openPane") { openPaneByKey(String(m.pane)); return; }   // strip quick-open
    if (m.type === "settingsSync") { broadcastSettings(m.settings, p.webview); return; }   // gear save → other panes
    if (m.type === "colorSync") { broadcastColorSync(m, p.webview); return; }   // tab swatch pick → feed repaints now
    if (m.type === "pickFile") { void pickFileForComposer(p); return; }
    if (m.type === "readClipboard") {
      vscode.env.clipboard.readText().then(
        (text) => p.webview.postMessage({ type: "clipboardText", text }),
        () => p.webview.postMessage({ type: "clipboardText", text: "" }));
      return;
    }
    if (m.type === "pickResult") { resolvePick(m.createNew ? { createNew: true } : m.id ? { id: String(m.id), name: String(m.name ?? "") } : undefined); return; }
    if (m.type === "ready") {
      pipe.webviewReady = true;
      pipe.send(m);
      for (const q of pendingToWebview.splice(0)) p.webview.postMessage(q);
      updateStrips();
      return;
    }
    const r = routeViewMessage("chat", m);
    if (r.revealFeed) openFeedPanel(r.revealFeed.preserveFocus);
    pipe.send(m);
  });
  p.onDidChangeViewState(() => updateStrips());
  p.onDidDispose(() => {
    pipe.dispose();
    if (chatPipe === pipe) chatPipe = undefined;
    panel = undefined;
    pendingToWebview = [];
    resolvePick(undefined);
    updateStrips();
  });
}

function openFeedPanel(preserveFocus = false, column?: vscode.ViewColumn) {
  if (feedPanel) {
    let col = feedPanel.viewColumn ?? vscode.ViewColumn.Beside;
    if (column !== undefined && feedPanel.viewColumn !== undefined && feedPanel.viewColumn === panel?.viewColumn)
      col = column;
    feedPanel.reveal(col, preserveFocus);
    return;
  }
  const p = vscode.window.createWebviewPanel(
    "rompFeed",
    "romp feed",
    { viewColumn: column ?? vscode.ViewColumn.Beside, preserveFocus },
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
    },
  );
  wireFeedPanel(p);
}

function wireFeedPanel(p: vscode.WebviewPanel) {
  feedPanel = p;
  p.webview.options = {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
  };
  p.iconPath = vscode.Uri.joinPath(extUri, "media", "romp-swirl.svg");
  p.webview.html = buildFeedHtml(p.webview);
  const pipe = new KernelPipe(
    "feed",
    (m) => {
      if (m.type === "kernelToast") { vscode.window.setStatusBarMessage(`romp: ${m.text}`, 5000); return; }
      p.webview.postMessage(m);
    },
    () => { p.webview.html = buildFeedHtml(p.webview); },
    // Same pipe-down banner contract as the chat panel (the user 2026-07-21).
    (up, queued) => { void p.webview.postMessage({ type: "pipeState", up, queued: queued ?? 0 }); },
  );
  feedPipe = pipe;
  p.webview.onDidReceiveMessage((m) => {
    if (!m) return;
    if (m.type === "ready") {
      pipe.webviewReady = true;
      for (const q of pendingToFeed.splice(0)) p.webview.postMessage(q);
      updateStrips();
    }
    if (m.type === "openPane") { openPaneByKey(String(m.pane)); return; }   // strip quick-open
    if (m.type === "settingsSync") { broadcastSettings(m.settings, p.webview); return; }   // gear save → other panes
    // Clicking into a session (or locating a card's chat turn) should bring
    // the CHAT panel forward — panel reveal is this host's job; the kernel
    // opens/focuses the tab itself. The rules live in view-routing.ts.
    const r = routeViewMessage("feed", m);
    if (r.revealChat) openPanel(r.revealChat.preserveFocus);
    pipe.send(m);
  });
  p.onDidChangeViewState(() => updateStrips());
  p.onDidDispose(() => {
    pipe.dispose();
    if (feedPipe === pipe) feedPipe = undefined;
    feedPanel = undefined;
    pendingToFeed = [];
    updateStrips();
  });
  updateStrips();
}

// ---- fleet status: the ambient status bar item + needs-you notifications ----
// One host-held feed pipe (independent of the feed panel, which may be closed)
// keeps the status bar live in every window: working / needs-you counts from
// the kernel's authoritative feed frames, "offline" while the socket is down.
// A needs-you card APPEARING is the one event worth a native notification —
// "interrupt only when the human is the bottleneck"; existing cards on
// (re)connect are status, not news, and never notify.

let statusPipe: KernelPipe | undefined;
let statusItem: vscode.StatusBarItem | undefined;
let statusSeen: Set<string> | null = null;   // needs-you itemIds already seen (null = baseline pending)
let statusOffline = true;
let lastStatus: FleetStatus | null = null;
let lastFrame: any = null;                   // last feed frame (tooltip detail)
let lastUsage: any = null;                   // /usage payload (fed by the timeline view when open)
let sessionDirs: SessionInfo[] = [];         // /sessions cache for the "this window" tooltip line
let statusCompKey = "";                      // fleet composition key: refetch dirs only when it changes

function startFleetStatus(context: vscode.ExtensionContext) {
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusItem.name = "romp";
  statusItem.command = "rompChat.menu";   // opens romp when closed; the dropdown when open
  context.subscriptions.push(statusItem);
  paintStatus();
  statusItem.show();
  statusPipe = new KernelPipe(
    "feed",
    (m) => onStatusFrame(m),
    () => { statusSeen = null; },              // kernel restarted: re-baseline, don't replay old asks
    (up) => {
      statusOffline = !up;
      if (!up) statusSeen = null;
      // No webview behind this pipe, so announce readiness ourselves — the
      // kernel pushes the full feed state in response.
      else statusPipe?.send({ type: "ready" });
      paintStatus();
    },
    true,                                      // passive: observe only, never spawn/toast
  );
}

function onStatusFrame(m: any) {
  const st = deriveStatus(m);
  if (!st) return;                             // ka frames and other chatter
  lastStatus = st;
  lastFrame = m;
  const { seen, fresh } = freshNeedsYou(statusSeen, m);
  statusSeen = seen;
  paintStatus();
  notifyNeedsYou(fresh);
  // Refresh the session→dir map only when the fleet's composition changes
  // (each frame is already an event; composition is the part the tooltip's
  // "this window" line depends on).
  const key = JSON.stringify([m.working || [], (m.asks || []).map((a: any) => a.itemId)]);
  if (key !== statusCompKey) {
    statusCompKey = key;
    void fetchSessions().then((s) => { sessionDirs = s; paintStatus(); });
  }
}

function paintStatus() {
  if (!statusItem) return;
  const r = renderStatusBar(statusOffline, lastStatus);
  statusItem.text = r.text;
  statusItem.backgroundColor = r.warn ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
  if (statusOffline) {
    statusItem.tooltip = "The romp kernel is unreachable — start it with `romp up`.";
    return;
  }
  const lines = lastFrame ? statusTooltipLines(lastFrame) : [];
  const here = sessionsForWorkspace(sessionDirs, workspaceFolderPaths()).map((s) => s.name);
  if (here.length) lines.unshift(`This window: ${here.join(", ")}`);
  const u = usageSummary(lastUsage);
  if (u) lines.push(`Usage: ${u}`);
  statusItem.tooltip = lines.join("\n") || "romp fleet";
}

function notifyNeedsYou(fresh: any[]) {
  if (!fresh.length) return;
  if (panel?.active) return;                   // already looking at the romp chat — the card is on screen
  const first = fresh[0];
  const msg = fresh.length === 1
    ? `romp: ${first.name} needs you — ${String(first.text || "").slice(0, 120)}`
    : `romp: ${fresh.length} sessions need you (${[...new Set(fresh.map((a) => a.name))].join(", ")})`;
  void vscode.window.showInformationMessage(msg, "Open").then((choice) => {
    if (choice !== "Open") return;
    openPanel(false);
    chatPipe?.send({ type: "openSession", id: String(first.sid) });
  });
}

// ---- native views (timeline + fleet/outline) ----
// WebviewViews resolve lazily when first shown and are re-resolved if the user
// drags them to another container — wire a fresh pipe each time. Same relay as
// the panels: the host holds the kernel WS, the webview never opens a socket.

let timelineView: vscode.WebviewView | undefined;
function wireTimelineView(v: vscode.WebviewView) {
  timelinePipe?.dispose();
  timelineView = v;
  timelinePipe = wireView(v, "timeline", buildTimelineHtml, (p) => {
    if (timelinePipe === p) timelinePipe = undefined;
    if (timelineView === v) timelineView = undefined;
  });
}

// The webviews scale to the editor font — re-render every open surface when it
// changes (same full-reload path a kernel-restart reconnect takes).
function refreshWebviewHtml() {
  if (panel) panel.webview.html = buildHtml(panel.webview);
  if (feedPanel) feedPanel.webview.html = buildFeedHtml(feedPanel.webview);
  if (fleetPanel) fleetPanel.webview.html = buildFleetHtml(fleetPanel.webview);
  if (timelineView) timelineView.webview.html = buildTimelineHtml(timelineView.webview);
}

// Outline: an editor tab like chat/feed (same pipe pattern, app=fleet).
function openFleetPanel(preserveFocus = false, column?: vscode.ViewColumn) {
  if (fleetPanel) {
    fleetPanel.reveal(column ?? fleetPanel.viewColumn ?? vscode.ViewColumn.Beside, preserveFocus);
    return;
  }
  const p = vscode.window.createWebviewPanel(
    "rompFleet",
    "romp outline",
    { viewColumn: column ?? vscode.ViewColumn.Beside, preserveFocus },
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
    },
  );
  wireFleetPanel(p);
}

function wireFleetPanel(p: vscode.WebviewPanel) {
  fleetPanel = p;
  p.webview.options = {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
  };
  p.iconPath = vscode.Uri.joinPath(extUri, "media", "romp-swirl.svg");
  p.webview.html = buildFleetHtml(p.webview);
  const pipe = new KernelPipe(
    "fleet",
    (m) => {
      if (m.type === "kernelToast") { vscode.window.setStatusBarMessage(`romp: ${m.text}`, 5000); return; }
      p.webview.postMessage(m);
    },
    () => { p.webview.html = buildFleetHtml(p.webview); },
  );
  fleetPipe = pipe;
  p.webview.onDidReceiveMessage((m) => {
    if (!m) return;
    if (m.type === "ready") pipe.webviewReady = true;
    const r = routeViewMessage("fleet", m);
    if (r.revealChat) openPanel(r.revealChat.preserveFocus);
    if (r.forward) pipe.send(m);
  });
  p.onDidChangeViewState(() => updateStrips());
  p.onDidDispose(() => {
    pipe.dispose();
    if (fleetPipe === pipe) fleetPipe = undefined;
    fleetPanel = undefined;
    updateStrips();
  });
  updateStrips();
}

function wireView(
  v: vscode.WebviewView,
  app: "timeline" | "fleet",
  build: (w: vscode.Webview) => string,
  onGone: (p: KernelPipe) => void,
): KernelPipe {
  v.webview.options = {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(extUri, "dist"), vscode.Uri.joinPath(extUri, "media")],
  };
  v.webview.html = build(v.webview);
  const pipe = new KernelPipe(
    app,
    (m) => {
      if (m.type === "kernelToast") { vscode.window.setStatusBarMessage(`romp: ${m.text}`, 5000); return; }
      v.webview.postMessage(m);
    },
    () => { v.webview.html = build(v.webview); },
  );
  v.webview.onDidReceiveMessage((m) => {
    if (!m) return;
    if (m.type === "ready") pipe.webviewReady = true;
    // The timeline view forwards the /usage payload to the host's chrome (the
    // status-bar item + its menu), like it feeds the web shell's rail.
    if (app === "timeline" && m.type === "usageData") {
      lastUsage = m.usage || null;
      paintStatus();
      // The strips ride the same live event the web rail does.
      const push = { type: "usage", usage: lastUsage };
      if (chatPipe?.webviewReady) panel?.webview.postMessage(push);
      if (feedPipe?.webviewReady) feedPanel?.webview.postMessage(push);
    }
    const r = routeViewMessage(app, m);
    if (r.revealChat) openPanel(r.revealChat.preserveFocus);
    if (r.openLinkLocally) openLink(r.openLinkLocally);
    if (r.forward) pipe.send(m);
  });
  v.onDidDispose(() => { pipe.dispose(); onGone(pipe); });
  return pipe;
}

// ---- the romp menu: the status-bar button's dropdown when romp is open ----
// Closed (no chat panel) or kernel offline → the click just opens romp, as
// before. Open → a QuickPick with the surfaces, the editor actions, the
// kernel's settings, and the account usage windows up top (the user
// 2026-07-13). Menu construction is pure (romp-menu.ts).

async function rompMenu() {
  if (statusOffline || !panel) {
    await vscode.commands.executeCommand("rompChat.open");
    return;
  }
  const usage = (await fetchJson("/usage")) || lastUsage;
  if (usage) { lastUsage = usage; paintStatus(); }
  const items = buildMenu(usage, Math.floor(Date.now() / 1000)).map((mi) => ({
    label: mi.label, description: mi.description, action: mi.action,
  }));
  const pick = await vscode.window.showQuickPick(items, { placeHolder: "romp" });
  if (!pick) return;
  switch (pick.action) {
    case "usage": return;                                    // informational row
    case "openChat": openPanel(false); return;
    case "openFeed": openFeedPanel(); return;
    case "openTimeline": void vscode.commands.executeCommand("rompTimeline.focus"); return;
    case "openFleet": openFleetPanel(); return;
    case "cite": void vscode.commands.executeCommand("rompChat.citeInComposer"); return;
    case "worktree": void vscode.commands.executeCommand("rompChat.openSessionWorktree"); return;
    case "diff": void vscode.commands.executeCommand("rompChat.diffSessionChanges"); return;
    case "update": void vscode.commands.executeCommand("rompChat.updateExtension"); return;
    case "settings":
      // The romp-styled settings modal (the gear) lives in the feed bundle —
      // the SAME one the browser renders (the user 2026-07-13, over a native
      // QuickPick). Open the feed and ask it to raise the modal.
      openFeedPanel(false);
      toFeedWebview({ romp: "openSettings" });
      return;
  }
}

// GET a kernel JSON endpoint; null on any failure (callers surface it).
function fetchJson(path: string): Promise<any | null> {
  return new Promise((resolve) => {
    const req = http.get({ host: HOST, port: kernelPort(), path, timeout: 2500,
                           headers: { "X-Romp-Token": serveToken() } }, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => {
        try { resolve(res.statusCode === 200 ? JSON.parse(body) : null); } catch { resolve(null); }
      });
    });
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.on("error", () => resolve(null));
  });
}

// ---- workspace integration: sessions ↔ the folders this window has open ----

// The kernel's /sessions endpoint — the authoritative unified session list
// (id, name, dir per session). Empty on any failure; callers surface that.
function fetchSessions(): Promise<SessionInfo[]> {
  return fetchJson("/sessions").then((j) =>
    Array.isArray(j) ? j.map((s: any) => ({ id: String(s.id), name: String(s.name), dir: String(s.dir || "") })) : []);
}

function workspaceFolderPaths(): string[] {
  return (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
}

function gitIn(dir: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile("git", ["-C", dir, ...args], { maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
      if (err) reject(err);
      else resolve(stdout);
    });
  });
}

async function pickKernelSession(placeHolder: string): Promise<SessionInfo | undefined> {
  const sessions = await fetchSessions();
  if (!sessions.length) {
    vscode.window.showWarningMessage("romp: no sessions (is the kernel running? `romp up`).");
    return undefined;
  }
  const folders = workspaceFolderPaths();
  const here = new Set(sessionsForWorkspace(sessions, folders).map((s) => s.id));
  const pick = await vscode.window.showQuickPick(
    sessions.map((s) => ({
      label: s.name,
      description: s.dir + (here.has(s.id) ? "  (this window)" : ""),
      session: s,
    })),
    { placeHolder },
  );
  return pick?.session;
}

// Insert the active file (with the selected line range) into the chat
// composer — the cheapest editor → agent handoff. Rides the same droppedPath
// message a file drop uses, so the composer treats both identically.
function citeInComposer() {
  const ed = vscode.window.activeTextEditor;
  if (!ed || ed.document.uri.scheme !== "file") {
    vscode.window.showWarningMessage("romp: open a file to cite it.");
    return;
  }
  const sel = ed.selection;
  // A selection ending at column 0 visually excludes that line.
  const endLine = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
  const text = citeText(ed.document.uri.fsPath, sel.start.line + 1, endLine, !sel.isEmpty);
  openPanel(true);
  toWebview({ type: "droppedPath", path: text });
}

async function openSessionWorktree() {
  const s = await pickKernelSession("Open a session's working directory");
  if (!s || !s.dir) return;
  if (sessionsForWorkspace([s], workspaceFolderPaths()).length) {
    vscode.window.showInformationMessage(`romp: ${s.name}'s directory is already open in this window (${s.dir}).`);
    return;
  }
  await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(s.dir), { forceNewWindow: true });
}

// Review what a session changed without leaving this window: pick a session,
// pick one of its uncommitted files, open the native diff (HEAD vs working).
async function diffSessionChanges() {
  const s = await pickKernelSession("Diff a session's uncommitted changes");
  if (!s || !s.dir) return;
  let files;
  try {
    files = parsePorcelain(await gitIn(s.dir, ["status", "--porcelain"]));
  } catch {
    vscode.window.showWarningMessage(`romp: ${s.dir} is not a git repository (or git failed).`);
    return;
  }
  if (!files.length) {
    vscode.window.showInformationMessage(`romp: ${s.name} has no uncommitted changes in ${s.dir}.`);
    return;
  }
  const pick = await vscode.window.showQuickPick(
    files.map((f) => ({ label: f.path, description: f.status, file: f })),
    { placeHolder: `${s.name}: uncommitted changes in ${s.dir}` },
  );
  if (!pick) return;
  const f = pick.file;
  const right = vscode.Uri.file(`${s.dir}/${f.path}`);
  const left = vscode.Uri.from({
    scheme: "romp-git",
    path: "/" + f.path,
    query: JSON.stringify({ dir: s.dir, rel: f.untracked ? null : f.renamedFrom || f.path }),
  });
  await vscode.commands.executeCommand("vscode.diff", left, right, `${f.path} — HEAD vs working (${s.name})`);
}

// ---- client capabilities ----

// Open a file (that a tool touched) in the real editor — in the main group,
// NOT the locked romp group beside it. {type:"openFile", path, line?} (1-based).
function openFileInEditor(file: string, line?: number) {
  try {
    const uri = vscode.Uri.file(file);
    const opts: vscode.TextDocumentShowOptions = { preview: true, viewColumn: vscode.ViewColumn.One };
    if (typeof line === "number" && line > 0) {
      const pos = new vscode.Position(line - 1, 0);
      opts.selection = new vscode.Range(pos, pos);
    }
    vscode.window.showTextDocument(uri, opts).then(undefined, () => {
      vscode.window.showWarningMessage(`romp: couldn't open ${file}`);
    });
  } catch { /* ignore */ }
}

// A link clicked inside a chat webview. Deep links addressed to THIS extension
// skip the OS round-trip; everything else goes to the OS.
function openLink(href: string) {
  let uri: vscode.Uri;
  try { uri = vscode.Uri.parse(href, true); } catch { return; }
  if (uri.scheme === "vscode" && uri.authority.toLowerCase() === "romp.romp-chat-view") { onDeepLink(uri); return; }
  vscode.env.openExternal(uri);
}

// The reliable way to get a file path into the composer: OS drags onto the
// webview are swallowed by the workbench's editor drop overlay, so the 📎
// button runs a native open dialog and inserts each picked path via the same
// droppedPath message an in-webview drop uses.
async function pickFileForComposer(p: vscode.WebviewPanel) {
  const picks = await vscode.window.showOpenDialog({
    canSelectMany: true,
    canSelectFiles: true,
    canSelectFolders: false,
    openLabel: "Insert path",
    title: "Attach file — inserts its path into the message",
  });
  if (!picks?.length) return;
  for (const uri of picks) p.webview.postMessage({ type: "droppedPath", path: uri.fsPath });
}

// External deep-link: vscode://romp.romp-chat-view/open?session=<id>&anchor=<uuid>.
// Reveal the panel, then let the kernel resolve the session (fork-aware) and
// focus/scroll this client.
function onDeepLink(uri: vscode.Uri) {
  const q = new URLSearchParams(uri.query);
  const session = (q.get("session") || "").trim();
  if (!session) {
    vscode.window.showWarningMessage("romp: deep-link is missing ?session=");
    return;
  }
  const preserveFocus = q.get("focus") === "0";
  openPanel(preserveFocus);
  chatPipe?.send({
    type: "deepLink",
    session,
    anchor: (q.get("anchor") || "").trim() || undefined,
    anchorT: Number(q.get("anchorT") || "") || undefined,
    anchorKind: (q.get("anchorKind") || "").trim() || undefined,
    compose: q.get("compose") === "1",
  });
}

// Cross-extension picker (vscode-trackchanges' Cmd+M): open the colored
// in-webview picker in "return the selection" mode.
function pickSessionExternal(opts: { prompt?: string; allowNew?: boolean } = {}): Promise<PickValue | undefined> {
  if (pendingPick) { pendingPick(undefined); pendingPick = null; }
  openPanel();
  return new Promise((resolve) => {
    pendingPick = resolve;
    toWebview({ type: "openPicker", pick: true, prompt: opts.prompt, allowNew: !!opts.allowNew });
  });
}

function resolvePick(v: PickValue | undefined) {
  if (pendingPick) { pendingPick(v); pendingPick = null; }
}

// ---- webview HTML (the bundles ship in the VSIX; the kernel pipe replaces
// the host logic, not the rendering) ----

// The bundles' JS-created <img>/<image> assets resolve through
// window.__rompMediaBase (ui/webview/media.ts + the timeline view's mediaUrl):
// the kernel serves /media on the web origin, but this webview's synthetic
// origin needs the asWebviewUri form — without it the src 404s and e.g. the
// loader's broken-image icon spins on the rl-o animation (the user 2026-07-13).
function mediaBaseTag(webview: vscode.Webview, n: string): string {
  const base = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "media"));
  return `<script nonce="${n}">window.__rompMediaBase=${JSON.stringify(String(base))};</script>`;
}

// Scale every romp surface to the EDITOR font (the user 2026-07-13, who wanted it at least
// as big as a file's text). The bundles set absolute px sizes around a 13px
// base, so a uniform zoom is the scale knob: editor.fontSize / 13, never
// below 1 (a small editor font keeps romp at its designed size). Re-applied
// on editor.fontSize changes via refreshWebviewHtml().
function uiZoom(): number {
  const fs = vscode.workspace.getConfiguration("editor").get<number>("fontSize") || 12;
  return Math.max(1, Math.min(2, fs / 13));
}

function zoomStyle(): string {
  const z = uiZoom();
  return z === 1 ? "" : `<style>body{zoom:${z.toFixed(4)};}</style>`;
}

function buildHtml(webview: vscode.Webview): string {
  const js = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "render.js"));
  const css = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "styles.css"));
  const stripCss = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "strip.css"));
  const gearCss = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "gear.css"));
  const n = nonce();
  // The romp strip's initial /usage fetch goes straight to the kernel.
  const kernelBase = `http://${HOST}:${kernelPort()}`;
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `connect-src ${kernelBase}`,
    `script-src 'nonce-${n}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  ${zoomStyle()}
  <link href="${css}" rel="stylesheet" />
  <link href="${stripCss}" rel="stylesheet" />
  <link href="${gearCss}" rel="stylesheet" />
  <title>romp</title>
</head>
<body>
${chatBody(ATTACH_TITLE_VSCODE)}
  ${mediaBaseTag(webview, n)}
  <script nonce="${n}">window.__rompKernelBase=${JSON.stringify(kernelBase)};window.__rompKernelToken=${JSON.stringify(serveToken())};window.__rompShowStrip=true;</script>
  <script nonce="${n}" src="${js}"></script>
</body>
</html>`;
}

function buildFeedHtml(webview: vscode.Webview): string {
  const js = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "feed.js"));
  const css = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "feed.css"));
  const n = nonce();
  // The gear modal (in the feed bundle) fetches /models, /palette, /version,
  // /analytics straight from the kernel: allow that origin and tell the bundle
  // where it is (the browser serves the feed FROM the kernel, so its base is
  // ''; this webview's synthetic origin needs the explicit one).
  const kernelBase = `http://${HOST}:${kernelPort()}`;
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `connect-src ${kernelBase}`,
    `script-src 'nonce-${n}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  ${zoomStyle()}
  <link href="${css}" rel="stylesheet" />
  <link href="${webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "strip.css"))}" rel="stylesheet" />
  <link href="${webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "gear.css"))}" rel="stylesheet" />
  <title>romp feed</title>
</head>
<body>
${FEED_BODY}
  ${mediaBaseTag(webview, n)}
  <script nonce="${n}">window.__rompKernelBase=${JSON.stringify(kernelBase)};window.__rompKernelToken=${JSON.stringify(serveToken())};window.__rompShowStrip=true;</script>
  <script nonce="${n}" src="${js}"></script>
</body>
</html>`;
}

function buildTimelineHtml(webview: vscode.Webview): string {
  const js = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "timeline-main.js"));
  const css = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "timeline-pane.css"));
  const n = nonce();
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `script-src 'nonce-${n}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  ${zoomStyle()}
  <link href="${css}" rel="stylesheet" />
  <title>romp timeline</title>
</head>
<body>
${TIMELINE_BODY}
  ${mediaBaseTag(webview, n)}
  <script nonce="${n}" src="${js}"></script>
</body>
</html>`;
}

function buildFleetHtml(webview: vscode.Webview): string {
  const js = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "fleet.js"));
  // styles.css first (the .ledger-* goal-tree styling), fleet-pane.css after it
  // (the page layout) — same order as the kernel's /fleet page.
  const cssBase = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "styles.css"));
  const cssPane = webview.asWebviewUri(vscode.Uri.joinPath(extUri, "dist", "fleet-pane.css"));
  const n = nonce();
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `script-src 'nonce-${n}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  ${zoomStyle()}
  <link href="${cssBase}" rel="stylesheet" />
  <link href="${cssPane}" rel="stylesheet" />
  <title>romp outline</title>
</head>
<body>
${FLEET_BODY}
  ${mediaBaseTag(webview, n)}
  <script nonce="${n}" src="${js}"></script>
</body>
</html>`;
}

function nonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 24; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}
