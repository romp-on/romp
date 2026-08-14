// Shared, persisted webview settings (the user 2026-06-14): one global settings store, surfaced via a
// gear → modal. localStorage-backed so same-origin views (the browser's /chat, /feed, /timeline tabs)
// share ONE setting, and a `storage` event live-syncs a change across the other open tabs. Keep this
// DOM-light: load/save are pure over localStorage (unit-tested); only the subscribe helper touches window.

export interface RompSettings {
  compact: boolean;   // chat transcript: collapse consecutive tool uses, hide thinking
  colormap: string;   // feed recency tint colormap (the user 2026-06-16): hawaii | viridis | magma | inferno | plasma | cividis | aurora
  subgoals: boolean;      // feed CARDS: show the inline sub-goal checklist (the user 2026-06-17); toggled from the feed FOOTER (the user 2026-06-18); the MODAL is unaffected
  // The timeline's judging band, split into its two judge SETS (the user 2026-06-29): index = the captioner +
  // archiver; triage = planner/grouper/closer/distiller/courier. Each toggle shows its set's rows on the band.
  // Replaces the old single `debug` toggle (kept optional below for migration). Both OFF by default.
  showIndexJudges: boolean;
  showTriageJudges: boolean;
  debug?: boolean;    // LEGACY (the user 2026-06-17): the old single judging-band toggle; read as the migration fallback for the two judge-set toggles when those are unset. The ↻ restart button is always-visible (decoupled).
  backend: "tmux" | "sdk";   // which backend a NEWLY-created session uses (the user 2026-06-22): "tmux" (terminal) or "sdk" (Agent SDK). Both coexist; this is only the default for the + button and the hive tray. Read at createSession time (render.ts / hive.ts). Default tmux (the user 2026-08-13, who works in the terminal sessions and wanted every surface to make the same one).
  defaultDir: string;        // default working directory PREFILLED in the new-session field (the user 2026-06-22). A session's dir is fixed at creation. Empty → the kernel's serve dir. ~ / $VAR expanded server-side.
  showBranch: boolean;       // chat bottom-bar: show the session's git branch (if any) beside the dir (the user 2026-06-23). OFF by default (the user 2026-08-10, trimming the statusline for narrow panes; an explicit stored true keeps showing it).
  tabCtx: TabCtxMode;        // chat tabs: WHEN the context gauge shows beside each session name (the user 2026-08-08) — "over50" (default: only once half full, so quiet tabs stay clean), "always", or "never".
}
// When the tab strip's context gauge shows. "over50" is the default (the user 2026-08-08): a gauge
// on every tab is clutter while nothing is filling up — it should appear only when it has news.
export type TabCtxMode = "always" | "over50" | "never";
// The gauge shipped for a few hours as a boolean toggle (2026-08-08) — normalize a stored
// true/false (or anything else unrecognized) into the mode enum: false was an explicit "hide"
// → never; true was the shipped default nobody chose → the new default. loadSettings applies
// this, so consumers always see a mode.
export function tabCtxMode(v: unknown): TabCtxMode {
  return v === "always" || v === "never" ? v : v === false ? "never" : "over50";
}
// NOTE: the old `explanations` pref is GONE (the user 2026-06-18) — cards no longer show the planner's
// hand-written "why" as their line; they show the distiller's summary instead (the why demotes to a hover).
// compact defaults ON (the user 2026-07-14): a fresh install reads the tidy transcript
// (thinking hidden, tool runs folded); the gear opts back into the full stream.
export const DEFAULT_SETTINGS: RompSettings = { compact: true, colormap: "aurora", subgoals: true, showIndexJudges: false, showTriageJudges: false, backend: "tmux", defaultDir: "", showBranch: false, tabCtx: "over50" };
const KEY = "romp:settings";

export function loadSettings(): RompSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const s = { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
      s.tabCtx = tabCtxMode(s.tabCtx);   // a store written by the boolean-era gear holds true/false
      return s;
    }
  } catch { /* corrupt / unavailable → defaults */ }
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(patch: Partial<RompSettings>): RompSettings {
  const next = { ...loadSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* ignore */ }
  return next;
}

// Fire `cb` when the settings change ANYWHERE they can change:
// - another same-origin tab (the browser views share localStorage → `storage` event);
// - THIS document (the gear modal now lives in the same page — VS Code's chat and feed
//   each host their own copy — and a same-document write never fires `storage`, which
//   left the compact toggle dead in the VS Code chat; gear.js's save() dispatches the
//   'romp:settings' window event instead, the user 2026-07-14).
// No-op where there's no window (tests, headless).
export function onExternalSettingsChange(cb: (s: RompSettings) => void): void {
  if (typeof window === "undefined") return;
  window.addEventListener("storage", (e: StorageEvent) => { if (e.key === KEY) cb(loadSettings()); });
  window.addEventListener("romp:settings", () => cb(loadSettings()));
}

// VS Code cross-pane settings sync, inbound side: each webview owns a separate
// localStorage, so a gear save in one pane reaches the others as a host-relayed
// {settingsSync} message (gear.js save() posts it; extension.ts fans it out).
// Applying = write our copy of the store, then raise the same-document signal so
// every consumer above reacts. Never re-posts — the host already broadcast it.
export function installSettingsSync(): void {
  if (typeof window === "undefined") return;
  window.addEventListener("message", (ev: MessageEvent) => {
    const m = ev.data;
    if (!m || m.type !== "settingsSync" || !m.settings) return;
    try { localStorage.setItem(KEY, JSON.stringify(m.settings)); } catch { /* ignore */ }
    try { window.dispatchEvent(new Event("romp:settings")); } catch { /* ignore */ }
  });
}
