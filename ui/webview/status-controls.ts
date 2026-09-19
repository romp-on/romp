// THE STATUS LINE'S CONTROLS, ONE RENDERER (T415 part two, the user 2026-09-14: the settings card's status line preview drew words
// and a boxed number in place of the line's controls, so it previewed nothing; the preview now draws through this module, the same
// builders the chat's line and the comment popovers use, over a demo status). What lives here is the DOM of the mode, model, effort
// and fast badges (metaButton / syncMetaControls) with their tints (metaColor: the kernel-shipped colormap RGB by capability and
// effort rank, the yatharth tone on that theme, re-encoded readable on the light theme), and the context battery (ctxBar /
// setCtxBar). What stays in render.ts is the chat's LIVE half, handed in as hooks: the click that opens a picker (onPress), the
// pending-pick heuristic (pending), the battery's /compact click (onClick) and the compaction sweep's colormap animation (sweep).
// With no hooks the controls are inert, which is what a preview wants. The colormap arithmetic (rampOn, the kernel's cm.ramp) and
// the kernel's rank rule for the demo's tints (MODEL_FAMILIES / EFFORT_LEVELS, pinned to kernel.py's choice lists in
// settings-previews.test.ts) live here too, so the demo's Opus 5 at high and 62% wear exactly the colours a real session with those
// values gets on the selected map. The bodies below moved from render.ts unchanged but for the hooks; their tests moved with them.
import { pickTone, readableRgb, ctxFallbackColor, CTX_WARN, CTX_DANGER } from "./ctx-color";
import { setTip } from "./tip";
export { pickTone };   // the theme pick (the tone on a yatharth theme, else the classic colour), for a caller drawing a status of its own

export type MetaKind = "mode" | "model" | "effort" | "fast";
/** the slice of a session's status the controls read (render.ts's Status satisfies it structurally; so does the demo status below) */
export interface MetaStatus {
  mode?: string; model?: string; effort?: string; fast?: string;
  modelPending?: boolean; effortPending?: boolean;   // a /model switch resolving, an /effort switch reconnecting: the badge shows switching-dots
  modelColor?: number[] | null; effortColor?: number[] | null;   // the kernel's classic colormap RGB by rank
  modelTone?: number[] | null; effortTone?: number[] | null;     // the single-hue tones the yatharth themes pick instead
  backend?: string;
}
/** the chat's live half, handed in; absent = an inert badge (a preview's) */
export interface MetaHooks {
  onPress?: (kind: MetaKind, btn: HTMLElement, forSid: string | null) => void;   // the chat: toggleMetaMenu (the picker); with it the badge wears its tip
  pending?: (kind: MetaKind, st: MetaStatus) => boolean;                          // the chat: isMetaPending (the sub-second before the server's own flag)
}
function el(tag: string, cls?: string): HTMLElement { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

// Permission mode. A Claude Code session sets it outright over the control channel (set_permission_mode),
// which is what makes Bypass offerable there and only there (a Codex session has its own vocabulary and
// cannot express it). `sdkOnly` is the filter, applied in toggleMetaMenu.
// Permission-mode GLYPHS (the user 2026-08-28): each mode gets a small line icon beside its text —
// the statusline badge and the picker rows carry it, always WITH the label (an icon alone is a
// riddle). House icon style (the tag-glyph convention): 16-unit viewBox, stroke currentColor 1.4,
// round caps/joins. The vocabulary: the GATE is a shield — Normal is the shield as-is, Bypass is
// the shield slashed (the gate removed); Accept edits is the pencil (edits pre-approved); Auto is
// the bolt (it decides at speed); Plan is the route pin-to-pin (look before touching); Don't ask
// (renderable, not offerable) is the crossed speech bubble (it will never raise a question).
export const MODE_ICONS: Record<string, string> = {
  default: '<path d="M8 2 L13 4 V8 C13 11.4 10.8 13.2 8 14 C5.2 13.2 3 11.4 3 8 V4 Z"/>',
  acceptedits: '<path d="M3.5 12.5 L4.1 10.1 L10.9 3.3 A1.35 1.35 0 0 1 12.8 5.2 L6 12 L3.5 12.5 Z"/><path d="M9.9 4.3 L11.8 6.2"/>',
  auto: '<path d="M8.8 2 L4.2 9 H7.4 L6.9 14 L11.8 6.8 H8.3 Z"/>',
  plan: '<circle cx="4" cy="12" r="1.5"/><circle cx="12" cy="4" r="1.5"/><path d="M5.2 10.8 C7.5 9.5 8.5 6.5 10.8 5.2" stroke-dasharray="2 1.6"/>',
  bypasspermissions: '<path d="M8 2 L13 4 V8 C13 11.4 10.8 13.2 8 14 C5.2 13.2 3 11.4 3 8 V4 Z"/><path d="M3.2 13 L12.8 3"/>',
  dontask: '<path d="M3 3.5 H13 V10 H8.5 L5.5 12.8 V10 H3 Z"/><path d="M3.2 12.6 L12.8 2.6"/>',
};
// the modes that REMOVE the gate rather than move it read in a red hue on the yatharth themes
// (the user 2026-08-31) — CSS-scoped to .chat-theme-yatharth so classic renders untouched
export function riskyMode(mode: string | undefined): boolean {
  const k = (mode || "").toLowerCase().replace(/[\u2019' -]/g, "");
  return k === "bypasspermissions" || k === "bypass" || k === "dontask";
}
export function modeIconSvg(mode: string | undefined): string {
  // accepts wire values AND display labels (metaButton receives prettyMode's text)
  const raw = (mode || "default").toLowerCase().replace(/[\u2019' -]/g, "");
  const k = raw === "normal" || raw === "" ? "default" : raw;
  const body = MODE_ICONS[k] ?? MODE_ICONS.default;
  return '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
}
// Per-session billing (the user 2026-08-08) — the Claude login vs the API key behind Claude Code's
// apiKeyHelper — is no longer a statusline badge: the SWITCHING control lives in the tab's
// right-click menu (showTabMenu's Billing flyout, the user 2026-08-09), on every SDK session since
// 2026-09-08 (both choices listed, the one this box cannot bill greyed with the reason; it was gated
// on st.authBoth before), and still labelled plainly 'API key' — no fragment of the key, not even a
// last-4 tail, is shipped or shown (2026-08-08, evening). The tab hover's Billing row keeps carrying
// the fact everywhere.
// the fast-mode state ("on"/"off"/"cooldown") → the badge label. ONE WORD (the user 2026-08-10, on a
// phone-width statusline), but the WORD carries the state: off reads "Slow", not a second "Fast" —
// tint alone (orange on, dim off) didn't say which side the toggle was on (the user 2026-08-11).
// ON keeps the CLI's fast orange (metaColor); the picker's ✓ names the state on click.
export function prettyFast(f: string | undefined): string {
  const s = (f || "").toLowerCase();
  return s === "cooldown" ? "Cooldown"   // rate-limited: the CLI resumes fast mode when the limit resets
    : s === "on" ? "Fast" : "Slow";
}
// Whether the session's MODEL can run fast mode at all (the CLI's /fast is an Opus-only research
// preview). Gated HERE, on the model, because the CLI is no help: fast_mode_state arrives "off" with
// an EMPTY fast_mode_disabled_reason on a non-Opus session (verified 2026-08-10 against 2.1.226 on a
// fable session), so state alone would leave a dead toggle on every model /fast refuses (the user
// 2026-08-10). Unknown/default stays visible: the account default may be Opus, and hiding a live
// control is worse than a rare dead one.
export function fastAvailable(st: MetaStatus): boolean {
  const m = (st.model || "").toLowerCase();
  return !m || m === "default" || m.includes("opus");
}
// the @claude-permission-mode var → a short readable badge label
export function prettyMode(m: string | undefined): string {
  switch ((m || "").toLowerCase()) {
    case "plan": return "Plan";
    case "acceptedits": return "Accept";   // one word everywhere the mode renders (T140)
    case "auto": return "Auto";
    case "dontask": return "Don’t ask";
    case "bypasspermissions": return "Bypass";
    case "sandboxed": return "Sandboxed";
    default: return "Normal";   // default / normal / unknown
  }
}
// the badge's word for a meta kind of the active session: its live value, or for a Codex session with no effort picked the
// bare kind (effortBadgeText); callers compare it with itself (the pending check), never send it as a value
export function metaCurrent(kind: MetaKind, st: MetaStatus): string {
  return (kind === "model" ? st.model : kind === "effort" ? effortBadgeText(st) : kind === "fast" ? st.fast
    : st.mode) || "";
}
/** the effort badge's word: the session's effort, or the bare kind for a Codex session that has picked none yet.
 * A Codex session is born with no effort of its own (the registry holds "" and the engine applies its default; the
 * kernel reports no level for it), and the badge used to appear only once a level was set, so the menu that sets
 * one had no button to open it (2026-09-16). Codex takes a pick at any time (set_effort), so the badge stands from
 * the start and reads "effort" until a level lands. An SDK session keeps the old rule: its effort is never empty (the
 * registry's level, else the backend's default, set when the session object is built), so its badge already stands
 * from the first status, and an empty SDK effort is the backend's failed-row placeholder, where a badge would offer a
 * pick no session is there to take. */
export function effortBadgeText(st: MetaStatus): string {
  return st.effort || (st.backend === "codex" ? "effort" : "");
}
// Three pulsing accent-blue dots shown IN the model badge while a /model switch resolves (the user
// 2026-07-03) — the romp loader's dot motif, so a wait always reads as "something's happening, it's
// romp". Cleared the instant syncMetaControls sees modelPending drop and the real name lands.
export function metaDots(): HTMLElement {
  const d = el("span", "meta-dots");
  d.appendChild(el("i"));
  d.appendChild(el("i"));
  d.appendChild(el("i"));
  return d;
}
export function metaButton(kind: MetaKind, text: string, forSid: string | null | undefined, hooks: MetaHooks): HTMLElement {
  const btn = el("span", "meta-btn");
  btn.dataset.kind = kind;
  if (forSid) btn.dataset.sid = forSid;   // a popover's badges name their thread; the chat's carry no session (metaAnchor)
  if (kind === "mode") {   // the permission glyph, always beside its text (never instead of it)
    const ico = el("span", "meta-ico mode-ico");
    ico.innerHTML = modeIconSvg(text);   // refreshed by the sync loop below from st.mode
    btn.appendChild(ico);
    btn.classList.toggle("mode-risky", riskyMode(text));   // kept live by the sync loop
  }
  const label = el("span", "meta-label");
  label.textContent = text;
  btn.appendChild(label);
  const caret = el("span", "meta-caret");
  caret.textContent = "▾";
  btn.appendChild(caret);
  if (hooks.onPress) {   // the chat's badge: the styled tip (tip.ts, the one .romp-tip dress) and the picker; a preview's badge carries neither
    setTip(btn, kind === "model" ? "change model (sends /model)"
      : kind === "effort" ? "change thinking effort (sends /effort)"
      : kind === "fast" ? "toggle fast mode (sends /fast)"
      : "change permission mode (shift+tab cycle)");
    const press = hooks.onPress;
    btn.addEventListener("click", (e) => { e.stopPropagation(); press(kind, btn, forSid ?? null); });
  }
  return btn;
}
export function metaColor(kind: MetaKind, st: MetaStatus): string {
  // fast ON wears the CLI's own fast-mode orange (--fast, a status color) so the badge reads the same
  // here as in the Claude Code TUI; off/cooldown stay the default gray.
  if (kind === "fast") return (st.fast || "").toLowerCase() === "on" ? "var(--fast)" : "";
  const c0 = kind === "model" ? pickTone(st.modelColor, st.modelTone)
    : kind === "effort" ? pickTone(st.effortColor, st.effortTone) : undefined;
  const c = c0 && c0.length === 3 ? readableRgb(c0) : c0;
  return (c && c.length === 3) ? `rgb(${c[0]},${c[1]},${c[2]})` : "";
}
// Build or refresh the model/effort buttons inside #spinner-meta. Called from
// updateStatusline (fresh container) and the 1s ticker (label refresh in place).
export function syncMetaControls(meta: HTMLElement, st: MetaStatus, forSid: string | null | undefined, hooks: MetaHooks): void {
  // order left→right: mode · model · effort · fast — the mode selector sits LEFT of the model name
  // (the user 2026-06-16); fast exists only when the session reports it (SDK init) AND the model can
  // run it (fastAvailable). Billing moved to the tab's right-click menu (the user 2026-08-09) — no
  // badge here.
  const fast = st.fast && fastAvailable(st) ? st.fast : "";   // reported AND the model can run it — else no dead control
  const effort = effortBadgeText(st);   // a Codex session's badge stands before any level is picked (see effortBadgeText)
  const want = [st.mode ? "mode" : "", st.model ? "model" : "", effort ? "effort" : "", fast ? "fast" : ""].filter(Boolean).join();
  const btns = Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[];
  if (btns.map((b) => b.dataset.kind).join() !== want) {
    meta.replaceChildren();
    if (st.mode) meta.appendChild(metaButton("mode", prettyMode(st.mode), forSid, hooks));
    if (st.model) meta.appendChild(metaButton("model", st.model, forSid, hooks));
    if (effort) meta.appendChild(metaButton("effort", effort, forSid, hooks));
    if (fast) meta.appendChild(metaButton("fast", prettyFast(fast), forSid, hooks));
  }
  for (const b of Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[]) {
    const kind = b.dataset.kind as MetaKind;
    const disp = kind === "mode" ? prettyMode(st.mode) : kind === "fast" ? prettyFast(st.fast)
      : metaCurrent(kind, st);
    const label = b.querySelector(".meta-label") as HTMLElement | null;
    if (kind === "mode") {
      const ico = b.querySelector(".mode-ico") as HTMLElement | null;
      if (ico) ico.innerHTML = modeIconSvg(st.mode);
      b.classList.toggle("mode-risky", riskyMode(st.mode));
    }
    // A switching MODEL shows animated dots, not the stale/premature name (the user 2026-07-03): the
    // server drives it (st.modelPending) — event-based, cleared the instant the new model actually lands —
    // and the local click heuristic (isMetaPending) covers the sub-second before the first server push.
    // model resolves live; effort reconnects to apply (--effort is connect-time) — both drive the switching-
    // dots from the server (st.modelPending / st.effortPending), with isMetaPending covering the sub-second
    // before the first server push (the user 2026-07-06).
    const pending = (kind === "model" && !!st.modelPending) || (kind === "effort" && !!st.effortPending)
      || !!(hooks.pending && hooks.pending(kind, st));   // the chat's sub-second heuristic, when the chat is the caller
    const showDots = pending && (kind === "model" || kind === "effort");   // both apply via a resolve/reconnect the server tracks
    if (label) {
      if (showDots) {
        if (!label.querySelector(".meta-dots")) label.replaceChildren(metaDots());
      } else if (label.textContent !== disp || label.firstElementChild) {
        label.textContent = disp;
      }
      label.style.color = showDots ? "" : metaColor(kind, st);   // tint the model name / effort by the colormap rank
    }
    b.classList.toggle("meta-pending", pending);
  }
}
// Context "battery": a small bar that FILLS with the context-used %, recolors as it
// fills (green → amber → red), with the % written inside. Replaces the plain "40%".
// CLICK → /compact the session, same as the timeline's battery click.
export function ctxBar(onClick?: (bar: HTMLElement) => void): HTMLElement {
  const bar = el("span", "ctx-bar");   // no id here: the chat's wrapper names its one live bar, a preview's stays anonymous
  bar.appendChild(el("span", "ctx-fill"));
  bar.appendChild(el("span", "ctx-text"));
  bar.appendChild(el("span", "ctx-scan"));   // compacting: teal rectangle whose right edge compresses leftward (as on the timeline)
  if (onClick) {   // the chat's /compact; a preview's battery does nothing, and says nothing about clicking (setCtxBar reads the mark)
    bar.dataset.compacts = "1";
    bar.addEventListener("click", () => onClick(bar));
  }
  return bar;
}
export function setCtxBar(bar: HTMLElement, ctxStr: string | undefined, compacting = false, ctxColor?: number[] | null, ctxOver = false, sweep?: (scan: HTMLElement, fresh: boolean) => void): void {
  // Compacting: hide the fill/% (the number is about to be wrong anyway) and run
  // the scanning bar instead, mirroring the timeline's battery. No ctx% needed.
  bar.classList.toggle("ctx-compacting", compacting);
  if (compacting) {
    bar.classList.remove("ctx-clicked");   // the click's pulse cue did its job
    bar.style.display = "";
    bar.title = "compacting context…";
    const scan = bar.querySelector(".ctx-scan") as HTMLElement | null;
    if (scan) {
      // setCtxBar runs on BOTH the fresh bar updateStatusline builds AND the reused #ctx-bar the lighter
      // in-place refresh keeps — so phase-sync ONLY a fresh scan (no `swept` flag yet); re-seeding a reused
      // one every refresh restarted its animation, the jump the user saw (2026-07-02). The gradient still
      // (re)applies either way (recolors without restarting).
      const fresh = !scan.dataset.swept;
      if (fresh) scan.dataset.swept = "1";
      if (sweep) sweep(scan, fresh);   // the chat's colormap sweep (applyCompactSweep, 3.2s); a preview never compacts
    }
    return;
  }
  // left compacting → clear the arm flag so the NEXT episode re-seeds the phase on this (possibly reused) bar
  const scanOff = bar.querySelector(".ctx-scan") as HTMLElement | null;
  if (scanOff) delete scanOff.dataset.swept;
  if (!ctxStr) { bar.style.display = "none"; return; }
  bar.style.display = "";
  const pct = Math.max(0, Math.min(100, parseInt(ctxStr, 10) || 0));
  const fill = bar.querySelector(".ctx-fill") as HTMLElement | null;
  const txt = bar.querySelector(".ctx-text") as HTMLElement | null;
  // The GLOBAL colormap (the user 2026-06-26): the kernel computes the fill color server-side (ctxColor =
  // ramp(context%) on the selected map, bright = full) so the chat battery matches the timeline + usage bars.
  // Fall back to the old traffic-light if an older kernel didn't ship a color.
  const fillBg = (ctxColor && ctxColor.length === 3) ? `rgb(${ctxColor.join(",")})`
    : ctxFallbackColor(pct);   // theme-aware pair; fills stay un-re-encoded (see tabCtxGauge's note)
  if (fill) { fill.style.width = pct + "%"; fill.style.background = fillBg; }
  // ctxOver: the kernel clamps the CLI's "0-100+" percentage at 100 — past it the tokens exceed the
  // CURRENT model's window (a 1M→200k model switch does this instantly). Say so: a silent 100% right
  // after picking a smaller model reads as a broken gauge (the user 2026-09-02).
  if (txt) txt.textContent = ctxOver ? "100%+" : pct + "%";
  if (bar.dataset.compacts) bar.title = ctxOver   // the click-to-compact tooltip belongs to a bar that compacts (the chat's); an inert one keeps none (round three)
    ? "context exceeds this model's window — the next turn compacts or trims; click to /compact now"
    : `context ${pct}% used — click to /compact`;
  else if (bar.dataset.inertWhy) bar.title = ctxOver   // a bar that cannot compact says why (a Codex session's, marked by the chat where it fills the bar; 2026-09-19)
    ? `context exceeds this model's window — ${bar.dataset.inertWhy}`
    : `context ${pct}% used — ${bar.dataset.inertWhy}`;
  else bar.removeAttribute("title");
}

// ── the colormap arithmetic and the kernel's rank rule, for a status drawn without a kernel (the settings card's demo) ──

/** Python's round: the nearest integer, a tie (an exact .5) to the even neighbour. The kernel rounds every channel this way, and
 *  Math.round rounds ties up, so 11 of 777 samples differed by one unit (viridis at 0.62: 173 for the kernel's 172; round three, low a).
 *  Exact on doubles: the fraction is read off the floor, never through floor(x + 0.5). */
export function roundHalfEven(x: number): number {
  const f = Math.floor(x), d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}
/** v in [0,1] → the interpolated RGB across `stops` (v=0 the first, dark; v=1 the last, bright): bin/romp_colormap.py ramp, byte for
 *  byte, the rounding included, so a client-side sample equals the kernel's for the same map (render.ts ramp calls this over the selected map). */
export function rampOn(v: number, stops: ReadonlyArray<readonly [number, number, number]>): [number, number, number] {
  v = Math.max(0, Math.min(1, v));
  const x = v * (stops.length - 1), i = Math.floor(x), fr = x - i;
  if (i >= stops.length - 1) return [stops[stops.length - 1][0], stops[stops.length - 1][1], stops[stops.length - 1][2]];
  const a = stops[i], b = stops[i + 1];
  return [roundHalfEven(a[0] + (b[0] - a[0]) * fr), roundHalfEven(a[1] + (b[1] - a[1]) * fr), roundHalfEven(a[2] + (b[2] - a[2]) * fr)];
}
// THE TONES (bin/romp_colormap.py tone_rgb / context_rgb, the user 2026-08-27): each quantity owns one hue, saturation and lightness
// carrying the rank (more reads as more vivid); the kernel ships them beside the classic colours and the yatharth themes pick them
// (ctx-color.ts pickTone). Mirrored here, arithmetic and rounding included, so the card's demo carries the tones a real session's status
// carries and the preview follows the page's theme as the line does (round three: it equalled the line on classic only).
export type ToneFamily = "model" | "effort" | "context";
const TONE_HUES: Record<ToneFamily, number> = { model: 28, effort: 258, context: 200 };
const TONE_L: Record<ToneFamily, [number, number]> = { model: [0.52, 0.16], effort: [0.66, 0.12], context: [0.52, 0.16] };
/** the kernel's _hsl_to_rgb: hue in degrees, saturation and lightness in [0,1], each channel rounded half to even. The two modulos are
 *  Python's (a negative remainder shifted up once, never a roundtrip through +1 that drops low bits: round four found 48 triples off by
 *  one unit under the old ((t % 1) + 1) % 1); the cross-language sweep in status-controls.test.ts holds the parity. */
export function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const hm = h % 360;
  h = (hm < 0 ? hm + 360 : hm) / 360;
  if (s <= 0) { const v = roundHalfEven(l * 255); return [v, v, v]; }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const ch = (t: number): number => {
    t = t < 0 ? t % 1 + 1 : t % 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [roundHalfEven(ch(h + 1 / 3) * 255), roundHalfEven(ch(h) * 255), roundHalfEven(ch(h - 1 / 3) * 255)];
}
/** tone_rgb(family, v): v in [0,1] → the family's hue at saturation 0.42 + 0.48 v and lightness l0 + l1 v */
export function toneRgb(family: ToneFamily, v: number): [number, number, number] {
  v = Math.max(0, Math.min(1, v));
  const [l0, l1] = TONE_L[family];
  return hslToRgb(TONE_HUES[family], 0.42 + 0.48 * v, l0 + l1 * v);
}
/** context_rgb(pct): the calm teal tone by fullness until the warn line, then the shared amber, then the shared red */
export function contextRgb(pct: number): [number, number, number] {
  const p = Math.max(0, Math.min(100, pct || 0));
  if (p >= CTX_DANGER) return [192, 57, 43];
  if (p >= CTX_WARN) return [215, 162, 58];
  return toneRgb("context", p / 100);
}
// kernel.py MODEL_CHOICES (values) and EFFORT_CHOICES, in their order: _ramp_ranks spreads them evenly on [0,1], the models DESCENDING
// (the most capable family brightest, fable 1.0 … haiku 0.0) and the efforts ASCENDING (low 0.0 … the last 1.0). Pinned to kernel.py
// by settings-previews.test.ts, so a family or a level added there fails here until this list follows.
export const MODEL_FAMILIES = ["fable", "opus", "sonnet", "haiku"];
export const EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max", "ultracode"];
/** a model's rank: the first family word its name contains, as the kernel's _model_color reads it; null for an unknown model */
export function modelRank(model: string): number | null {
  const m = (model || "").toLowerCase(), n = MODEL_FAMILIES.length, d = Math.max(1, n - 1);
  for (let i = 0; i < n; i++) if (m.includes(MODEL_FAMILIES[i])) return (n - 1 - i) / d;
  return null;
}
/** an effort's rank, low 0.0 … the last level 1.0; null for an unknown level */
export function effortRank(effort: string): number | null {
  const i = EFFORT_LEVELS.indexOf((effort || "").trim().toLowerCase());
  return i < 0 ? null : i / Math.max(1, EFFORT_LEVELS.length - 1);
}
export interface DemoStatus extends MetaStatus { ctx: string; ctxColor: number[]; ctxTone: number[] }
/** The status the settings card's preview draws (T415 part two): the words the card used to print, now a status the shared renderer
 *  takes, its tints the kernel's rule on the given map (the card passes the selected colormap's stops) AND the kernel's tones (the
 *  same pairs a real status ships, round three), so the preview equals the chat's line for a session at these values on every theme:
 *  classic reads the colours, the yatharth themes the tones, yatharth-light the tones re-encoded readable, all through pickTone. */
export function demoStatus(stops: ReadonlyArray<readonly [number, number, number]>): DemoStatus {
  const rankColor = (v: number | null) => (v === null ? null : rampOn(v, stops));
  const modelV = modelRank("Opus 5"), effortV = effortRank("high"), ctxPct = 62;
  return { mode: "auto", model: "Opus 5", effort: "high", ctx: ctxPct + "%",
           modelColor: rankColor(modelV), effortColor: rankColor(effortV), ctxColor: rampOn(ctxPct / 100, stops),
           modelTone: modelV === null ? null : toneRgb("model", modelV), effortTone: effortV === null ? null : toneRgb("effort", effortV), ctxTone: contextRgb(ctxPct) };
}
