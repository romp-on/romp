// Subagent transcripts (plans/subagent-transcripts.md, 2026-09-05): the pure half of the viewer —
// tab ids, labels, the live-preview rows and the two line icons. render.ts owns the DOM (the arrow on
// the Agent head and the bg-task row, the peek-tab viewer, the header) and the kernel protocol
// (openSubagent / closeSubagent ↔ {type:"subagent"} frames); this module is what the source pins and
// the executable tests exercise without a DOM.
//
// The viewer TAB ID is `<parentId>/agent/<agentId>` — the parent's id (host-prefixed under federation,
// "host:sid") plus a colon-free suffix. host-prefix.ts reads the FIRST colon as the host marker, so a
// `sub:<sid>:<agentId>` shape would have named a phantom host "sub" everywhere hostOf() is consulted
// (the offline mark, the strip's host dimming, outbound routing); a suffix keeps hostOf(subId) ===
// hostOf(parentId) for free. The suffix never reaches the kernel as a session id (openSubagent carries
// the parent id + agentId), and a bare "/agent/" cannot occur in a uuid or a host name.

export const SUB_SEP = "/agent/";

export function subTabId(parentId: string, agentId: string): string { return parentId + SUB_SEP + agentId; }
export function isSubId(id: string | null | undefined): boolean { return typeof id === "string" && id.includes(SUB_SEP); }
export function subParts(id: string): { parentId: string; agentId: string } | null {
  if (typeof id !== "string") return null;
  const i = id.indexOf(SUB_SEP);
  if (i <= 0) return null;
  const agentId = id.slice(i + SUB_SEP.length);
  return agentId ? { parentId: id.slice(0, i), agentId } : null;
}

export interface SubMeta { agentType?: string; description?: string; spawnDepth?: number | null; toolUseId?: string; }
export interface AgentGistRow { tool: string; desc: string; ts?: string; }
export interface AgentGist { recent: AgentGistRow[]; calls: number; since?: string | null; last?: string | null; }

// The tab label: the sidecar's description (what the parent asked for), clipped to a tab's worth; else
// the agent type; else the bare word. Never the agent id — a hex string says nothing at a glance.
export const SUB_LABEL_MAX = 28;
export function subLabel(meta: SubMeta | null | undefined): string {
  const d = (meta?.description || "").trim();
  if (d) return d.length > SUB_LABEL_MAX ? d.slice(0, SUB_LABEL_MAX - 1).trimEnd() + "…" : d;
  const t = (meta?.agentType || "").trim();
  return t || "subagent";
}

// Elapsed since an ISO stamp, in the statusline timer's own vocabulary ("40s", "2m 5s", "1h 3m") —
// the same shape elapsedMs() prints beside the working chip, so the preview's clock reads like the
// pane's other clocks. "" when the stamp is unreadable.
export function elapsedSince(iso: string | null | undefined, nowMs: number): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!isFinite(t)) return "";
  const s = Math.max(0, Math.floor((nowMs - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// The preview's rows: the agent's recent tool calls in the head vocabulary (`<tool> <desc>`), newest
// LAST (the kernel ships them oldest→newest), with the trailing count/elapsed on the last row —
// "· 12 tool calls · 40s". Only while the kernel ships a gist (the agent is running); the caller shows
// nothing otherwise. Pure, so the shape is testable without a DOM.
export interface GistLine { tool: string; desc: string; meta: string; }
export function gistLines(g: AgentGist | null | undefined, nowMs: number): GistLine[] {
  if (!g || !Array.isArray(g.recent) || !g.recent.length) return [];
  const rows = g.recent.slice(-3);
  const n = Math.max(0, g.calls | 0);
  const parts: string[] = [];
  if (n) parts.push(`${n} tool call${n === 1 ? "" : "s"}`);
  const el = elapsedSince(g.since, nowMs);
  if (el) parts.push(el);
  const meta = parts.length ? "· " + parts.join(" · ") : "";
  return rows.map((r, i) => ({ tool: String(r.tool || "tool"), desc: String(r.desc || ""), meta: i === rows.length - 1 ? meta : "" }));
}

// The viewer header's one line: "subagent of <parent> · <agentType> · running|finished". The parent
// name is rendered by the caller as a link (hostNameNodes), so this returns the pieces, not a string.
export function subHeadParts(meta: SubMeta | null | undefined, running: boolean): { type: string; state: "running" | "finished" } {
  return { type: (meta?.agentType || "").trim() || "agent", state: running ? "running" : "finished" };
}

// The two line icons, in the house style every glyph in render.ts wears (16-unit viewBox, currentColor
// stroke 1.4, round caps/joins): the OPEN arrow (a corner box with an arrow leaving it — "open this
// elsewhere") and the PIN (a pushpin: head, collar, needle). Trusted constant markup.
const ICON_OPEN = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
  + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M7 3.5H4a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V9"/>'
  + '<path d="M9.5 3h3.5v3.5"/><path d="M13 3 7.5 8.5"/></svg>';
const ICON_PIN = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
  + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M6 2.5h4v4.2l1.8 2.3H4.2L6 6.7z"/><path d="M8 9v4.5"/></svg>';
export function openIconSvg(): string { return ICON_OPEN; }
export function pinIconSvg(): string { return ICON_PIN; }
