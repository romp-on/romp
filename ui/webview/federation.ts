// Federated dashboard — merge sessions from MANY kernels in the browser.
//
// Each attached kernel gets its own WebSocket. Messages from a remote kernel carry that kernel's own
// session ids (UUIDs); to keep them distinct in one merged dashboard we PREFIX every session id with the
// host on the way IN (`gpu1:‹uuid›`), and STRIP it + route to the owning connection on the way OUT. The
// panes (render.ts / feed.ts / fleet.ts) treat `host:‹uuid›` as an opaque id — they read it from a
// `data-id` and echo it back — so they need (almost) no changes; all the host-awareness lives here.
//
// This module is split into PURE functions (prefix / route / merge — fully unit-tested in
// multi-kernel-merge.test.ts) and a thin FederationManager that wires them to WebSockets + the DOM. The
// local kernel is just connection #0 with the empty-string host key, so its messages pass through
// unprefixed and the single-kernel path is byte-for-byte unchanged.

import { adoptArrivals, applyViewOrder, applyViewOrderTo, churnSwaps, healOrder, pruneViewOrder,
         readViewOrder, writeViewOrder, VIEW_ORDER_KEY, VIEW_ORDER_EVENT } from "./view-order";
import { adoptViews, capsAdopts, announcedSeq, announcedAfter } from "./views-writes";
import { hostOf, bareId, hostDialLive } from "./host-prefix";
import { installPerfTelemetry, classifyFrame, type RompPerf } from "./perf-telemetry";

export const SEP = ":";
export const LOCAL = ""; // the local kernel's host key — no prefix, so the single-kernel path is untouched

/** `host:id` for a remote host; the bare id unchanged for the local host. */
export function prefixId(host: string, id: string): string {
  return host ? host + SEP + id : id;
}

// hostOf/bareId live in host-prefix.ts (the side-effect-free helper module) so that OTHER modules can
// read a host prefix WITHOUT importing this file — importing federation.ts boots a FederationManager
// (the module-tail bootstrap below), and a second copy bundled into a pane emitted remote-only merged
// feeds in alternation with the real manager's (the user 2026-07-31: every local card blinking).
// Re-exported here so this module's own consumers (the merge tests) keep one import surface.
export { hostOf, bareId };

/** This dashboard's window id, resolved exactly as the pane shim resolves it (the kernel's `_shim`): the
 *  host's `?wid=` when it supplies one, else the shell's per-tab sessionStorage id, which same-origin
 *  iframes share. It rides every REMOTE connect too, so a remote kernel can aim a per-viewer message at
 *  the dashboard that asked. "" when neither exists — the kernel falls back to broadcasting, which is the
 *  pre-wid behaviour, so an older shell or a storage-blocked context degrades to what it did before. */
export function pickWid(search: string, stored: string): string {
  let q = "";
  try { q = new URLSearchParams(search || "").get("wid") || ""; } catch (e) { /* malformed query */ }
  return q || stored || "";
}

function dashboardWid(): string {
  let stored = "";
  try { stored = window.sessionStorage.getItem("romp:wid") || ""; } catch (e) { /* storage blocked */ }
  return pickWid(location.search, stored);
}

// The shapes a kernel→browser message can carry a session id in. Kept generic (by field name, not by
// message type) so a new message type that reuses these field names is covered automatically:
const SCALAR_ID = ["id", "sid"]; //               a single session id
// `skeleton` (2026-09-07): the tab strip's "listed but not re-sent" sids after a reconnect — ids like
// the order, so they must arrive prefixed like the order or the pane's set never matches its tabs.
// `live` (T258): the sids the kernel affirms live this build, the pane's omission guard.
const ARRAY_ID = ["order", "names", "working", "awaiting", "stateUnknown", "live", "skeleton"]; // an array of session ids
const OBJ_SID = ["asks", "items", "ledgers", "sessions"]; //  an array of objects keyed by `.sid`
const OBJ_ID = ["tabs"]; //                       an array of objects keyed by `.id`

// Gear settings the KERNEL acts on, which each kernel stores its own copy of: they describe how romp
// behaves towards the sessions it is running, so a machine that never hears the change goes on behaving
// the old way. setUpdateMode belongs here too: each kernel runs its own boot release check, so a machine
// that misses the change keeps handling new releases under the old policy (ask/auto/off). The distilling
// pair rides like the other judge tiers (the user 2026-08-14: everything kernel-side stays in sync; the
// gear's mixed marks surface any machine that disagrees rather than overwriting it silently), and the
// default-comment trio (setCommentModel/Effort/Fast, the user 2026-08-29) rides the same way. Broadcast in
// routeOutbound rather than routed. setFileEditing is the viewer's edit opt-in (the user 2026-08-22:
// one consent popup answers for the mesh — every kernel's save route gates on its own copy, so the
// broadcast is what makes the one yes reach them all). setCompactSuggest rides the same way (T248, the
// user 2026-09-07: it shipped per-install and a session on an attached machine, whose kernel's own copy
// was on, received the suggestion while their gear showed the box off with the mixed mark — they want
// no mixed state for this setting, ever; one click writes every machine, so the mark can only be
// transient). Deliberately NOT here: setDefaultDir (a path on one machine, meaningless on another),
// setColormap/setPalette (the viewer's display prefs, which the local kernel persists for this browser)
// and the PER-INSTALL gear rows — the Thinking summaries toggle (whoever reads the summaries turns it on
// where they read them) and the User todos switch (off by default; each kernel's answer is its own — its
// sub-copy says "this kernel keeps its own copy"): their ops post to the local kernel only, are never
// queued for or broadcast to another, and are not named anywhere in this file (gear.test.ts's
// PER_INSTALL pin holds that absence).
const KERNEL_SETTING = new Set(["setAutoNudge", "setJudgeModel", "setIndexModel",
                                "setJudgeEffort", "setIndexEffort", "setUpdateMode",
                                "setJudgeConcurrency",   // T277: the judges' pool width, one value across machines
                                "setDistillModel", "setDistillEffort", "setFileEditing",
                                "setCompactSuggest",
                                "setCommentModel", "setCommentEffort", "setCommentFast",
                                "setTmuxBackend",   // T288: the tmux backend's offer, one value across machines
                                "setJudgeFast", "setDistillFast", "setIndexFast"]);   // Fast mode per judge tier, one value across machines

// ── what a send to a host whose relay socket is NOT open does, by message class (2026-09-10) ─────────
// Three classes, decided by an EXPLICIT list — never guessed from the type's spelling at run time:
// - a KERNEL_SETTING (above) queues on the conn, latest per type, and flushes on the socket's open;
// - the pane's own BOOKKEEPING (this map) queues the same way, latest per KEY, and never toasts. These
//   are messages the pane emits on its own — a hint, a fetch it dedupes itself, a read watermark, a
//   metric row, a pointer position — so the user made no gesture that this message is the outcome of,
//   and a toast about it names a message they never sent (the user 2026-09-10: on the phone, a fresh
//   page whose active tab was a remote session posted activeTab before the relay socket to that host had
//   opened, and the tap they had just made, which had in fact worked, was answered with a warning that
//   "activeTab" was not delivered). HELD rather than dropped because the drop is what gagged the pane:
//   needFull's awaitingFull, imgRequest's imgRequested, loadEpisode's episodePendingKey and loadOlder's
//   loadingOlder each hold their re-ask until a reply lands, and a dead socket's never does;
// - everything else is a GESTURE — the message IS the action (sendMessage, createSession, renameSession,
//   askClear, a tag edit…) and the remote kernel doing it is the whole point, so a drop is said to the
//   user (dropWarn's toast) and never replayed later (a stale action can be worse than a dropped one).
//   An UNKNOWN type takes this arm too: a toast about a message that did not matter costs a glance; a
//   silent drop of one that did loses the thread.
// The value is the queue KEY, so "latest wins per key" mirrors the settings queue's per-type dedupe with
// the granularity each message needs: one active tab per pane, so activeTab keys by type alone and the
// newest supersedes; a fetch keys by what it fetches, so asks for two sessions both stand (a type-only
// key would flush one and leave the other's pane-side dedupe holding forever); a pointer position keys
// by type, since only the newest means anything and a leave clears; a metric or audit row keys by type,
// so a long outage holds one row and not an unbounded backlog — a row lost to an outage is a metric,
// never a gesture. A key function may answer null for an INSTANCE that is a gesture after all: the
// feed's showAskPath is the card hover's glow, but with `jump` it is the click that lands the chat on
// that card, and a jump minutes later would put the user somewhere they no longer asked to be.
const K = "\u001f";   // the key separator: a control character no session id, path or slot name contains
export const BOOKKEEPING: ReadonlyMap<string, (m: any) => string | null> = new Map<string, (m: any) => string | null>([
  ["activeTab",      ()  => "activeTab"],                          // render.ts notifyActive: the tab this pane is looking at
  ["needFull",       (m) => "needFull" + K + m.id],                // render.ts requestFullSession: a session's re-send (gap / nobase / skeleton / prefetch)
  ["needSlot",       (m) => "needSlot" + K + m.slot],              // fleet.ts: a view slot's re-send after a rejected delta
  ["loadOlder",      (m) => "loadOlder" + K + m.id],               // render.ts: the head's older page on a scroll-up or a deep link into it
  ["loadEpisode",    (m) => "loadEpisode" + K + m.id],             // render.ts noticeOpened: a clear notice's conversation on first expand
  ["imgRequest",     (m) => "imgRequest" + K + m.id + K + m.path], // render.ts: an inline image's bytes, asked on render
  ["commentSeen",    (m) => "commentSeen" + K + m.id + K + m.tid], // render.ts: a thread's read watermark as its popover opens or a reply lands in it
  ["dotHover",       ()  => "dotHover"],                           // render.ts: the chat's hovered dot (a bare dotHover is the leave)
  ["hoverHighlight", ()  => "hoverHighlight"],                     // feed.ts: the hovered card's rows on the timeline
  ["showAskPath",    (m) => (m.jump ? null : "showAskPath")],      // feed.ts: the hovered / pinned card's path glow (`off` clears); `jump` is the click
  ["timelineHover",  ()  => "timelineHover"],                      // timeline-boot.ts: the hovered lane segment (`off` clears, broadcast)
  ["dirComplete",    ()  => "dirComplete"],                        // render.ts: the + picker's typed-ahead path query (its reply carries reqId; a stale one is dropped there)
  ["cardOpened",     ()  => "cardOpened"],                         // feed.ts: the open-metric row
  ["locateDiag",     ()  => "locateDiag"],                         // render.ts: a chat landing attempt's audit row
  ["orderAudit",     ()  => "orderAudit"],                         // render.ts auditTabOrder: a tab-order permutation's audit row
]);

/** The key a held bookkeeping message dedupes under on the conn's queue, or null when the message is a
 *  gesture (an instance the list's function declines, a type not listed, or no type at all): the loud arm. */
export function bookkeepingKey(msg: any): string | null {
  if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return null;
  const f = BOOKKEEPING.get(msg.type);
  return f ? f(msg) : null;
}

/** Return a COPY of an inbound message with every session-id field prefixed by `host`. The local host
 *  ("") is the identity transform, so local messages are untouched. Unknown fields pass through. */
export function prefixInbound(host: string, msg: any): any {
  if (!host || !msg || typeof msg !== "object" || Array.isArray(msg)) return msg;
  const out: any = { ...msg };
  for (const k of SCALAR_ID)
    if (typeof out[k] === "string") out[k] = prefixId(host, out[k]);
  for (const k of ARRAY_ID)
    if (Array.isArray(out[k])) out[k] = out[k].map((x: any) => (typeof x === "string" ? prefixId(host, x) : x));
  for (const k of OBJ_SID)
    if (Array.isArray(out[k]))
      out[k] = out[k].map((o: any) => _prefixIdBearing(host, o, "sid"));
  for (const k of OBJ_ID)
    if (Array.isArray(out[k]))
      out[k] = out[k].map((o: any) => _prefixIdBearing(host, o, "id"));
  // A `name` is DISPLAY text, not an address — prefix it too (on session-bearing messages) so a remote
  // session reads "host:name" everywhere it surfaces (chat tab + header), never colliding visually with a
  // local same-named one. Guarded by a co-present id/sid so we never touch an unrelated `name` field.
  if (typeof out.name === "string" && (typeof out.id === "string" || typeof out.sid === "string"))
    out.name = prefixId(host, out.name);
  // The + picker's session list (the user 2026-07-29: switching Host should list THAT machine's sessions,
  // so a remote one can be reopened or revived from here). Its rows are keyed `id`, not `sid`, so the
  // generic passes above leave them alone — and prefixing them is what makes a click route back: the row
  // posts openSession with the id, and routeOutbound sends it to the host in the prefix. Stamped with
  // the source host too, so the picker can drop a late reply for a host it is no longer showing.
  // the path checker's answer, stamped with the machine that gave it: the picker drops a verdict that
  // arrives for a host it is no longer on, instead of showing one machine's answer about another's disk.
  if (out.type === "dirCompletions") out.host = host;
  // a remote kernel's answer to a card-move prediction: its ids are goal ids (globally unique, never
  // prefixed), but its buildId only means something on THAT kernel's counter — stamp the host so the
  // feed pane compares it against the same host's frame in the merged payload, never the local counter
  if (out.type === "cardMoveAck" || out.type === "cardPredict") out.host = host;
  // a remote kernel's stand-down reply to a settings gesture (settingStale) cannot name its own host;
  // the gear folds one flush's N refusals into one toast and names the refusing machines. The local
  // host ("") took the identity exit above, so a local frame has no host key: the gear words it as
  // this machine. The echoed `gesture` inside passes through untouched — it is re-issued as-is.
  if (out.type === "settingStale") out.host = host;
  if (out.type === "sessionList" && Array.isArray(out.items)) {
    out.items = out.items.map((it: any) => (it && typeof it === "object" && typeof it.id === "string"
      ? { ...it, id: prefixId(host, it.id),
          name: typeof it.name === "string" ? prefixId(host, it.name) : it.name }
      : it));
    out.host = host;
  }
  // the cross-surface chat glow (glowTurns): its groups are keyed by sid, so a remote kernel's glow
  // must arrive prefixed or the merged chat's views.get("host:sid") lookup misses and a remote
  // session's rows never light (the user 2026-08-03 — feed-card hover; the timeline-bar hover took
  // the same silent miss). The uuids stay bare: atom uuids are globally unique, like the hover ids.
  if (out.type === "glowTurns" && Array.isArray(out.groups))
    out.groups = out.groups.map((g: any) =>
      (g && typeof g === "object" && typeof g.sid === "string" ? { ...g, sid: prefixId(host, g.sid) } : g));
  // the feed's user-todo map (plans/user-todos.md) is keyed BY sid — a map, not an id-bearing array,
  // so the generic passes above can't reach its keys; unprefixed they'd never match the merged asks'
  // prefixed sids and a remote session's marker would silently not render.
  if (out.type === "feed" && out.userTodos && typeof out.userTodos === "object" && !Array.isArray(out.userTodos)) {
    const ut: Record<string, number> = {};
    for (const [k, v] of Object.entries(out.userTodos)) ut[prefixId(host, k)] = v as number;
    out.userTodos = ut;
  }
  // timeline payloads: the lanes skeleton nests everything under `data`; the bars detail is top-level.
  if (out.type === "data" && out.data && typeof out.data === "object") out.data = prefixTimelineData(host, out.data);
  else if (out.type === "bars") return { ...out, ..._prefixTimelineDetail(host, out) };
  if (out.activeChat && typeof out.activeChat === "object") out.activeChat = _prefixActiveChat(host, out.activeChat);
  return out;
}

/** Prefix an object's id field (`sid`/`id`) AND its display `name`, returning a copy (or the object
 *  unchanged if it isn't a prefixable object). */
function _prefixIdBearing(host: string, o: any, idKey: string): any {
  if (!o || typeof o !== "object" || typeof o[idKey] !== "string") return o;
  const out: any = { ...o, [idKey]: prefixId(host, o[idKey]) };
  if (typeof out.name === "string") out.name = prefixId(host, out.name);
  // A feed card's delegation origin (asks[].origin): peerHost empty means the SENDER is local to the
  // card's own kernel — attribute it to that host, and prefix peerSid so the click routes there. A
  // set peerHost means the sender lives on some OTHER host (that kernel recorded which); keep it,
  // and keep peerSid bare — the viewer may be that very host, where the bare uuid opens directly.
  if (out.origin && typeof out.origin === "object" && typeof out.origin.peerSid === "string" && !out.origin.peerHost)
    out.origin = { ...out.origin, peerHost: host, peerSid: prefixId(host, out.origin.peerSid) };
  // The awaiting box's delegation peers (asks[].awaiting.peers) — same rule as origin: a peer the
  // card's own kernel resolved (host "") is LOCAL TO THAT KERNEL, so attribute it here and prefix
  // its sid for routing; an already-hosted peer passes through untouched (the user 2026-08-23).
  if (out.awaiting && typeof out.awaiting === "object" && Array.isArray(out.awaiting.peers))
    out.awaiting = { ...out.awaiting, peers: out.awaiting.peers.map((p: Record<string, unknown>) =>
      p && typeof p === "object" && typeof p.sid === "string" && !p.host
        ? { ...p, host, sid: prefixId(host, p.sid) } : p) };
  // a timeline lane's fork parent (sessions[].branch.fromId): the view looks it up against PREFIXED
  // lane ids (vidx), so an unprefixed remote parent silently missed and the branch connector never
  // drew for remote lanes (found 2026-08-17 auditing the merge)
  if (out.branch && typeof out.branch === "object" && typeof out.branch.fromId === "string")
    out.branch = { ...out.branch, fromId: prefixId(host, out.branch.fromId) };
  return out;
}

// ── timeline payloads ────────────────────────────────────────────────────────────────────────────
// The timeline rides the same WS as everything else (app=timeline): a {type:"data", data:{…}} lanes
// skeleton and a heavy {type:"bars"} detail message. Both are per-sid keyed, so a remote host's copy
// needs the same prefixing as the flat messages above — but nested.

/** Prefix the per-sid DETAIL shared by {type:"data"}.data and {type:"bars"}: `turns` (an object keyed
 *  by sid whose bars carry `tid` = their sid), postal `messages` (fromId/toId), and the `judging` +
 *  Event uuids (bar id/promptId/workId, hover ids) are globally unique already
 *  and stay bare. */
function _prefixTimelineDetail(host: string, d: any): any {
  const out: any = { ...d };
  if (out.turns && typeof out.turns === "object" && !Array.isArray(out.turns)) {
    const turns: any = {};
    for (const [sid, bars] of Object.entries(out.turns))
      turns[prefixId(host, sid)] = Array.isArray(bars)
        ? bars.map((b: any) => (b && typeof b === "object" && typeof b.tid === "string" ? { ...b, tid: prefixId(host, b.tid) } : b))
        : bars;
    out.turns = turns;
  }
  if (Array.isArray(out.messages))
    out.messages = out.messages.map((m: any) => {
      if (!m || typeof m !== "object") return m;
      const c: any = { ...m };
      if (typeof c.fromId === "string") c.fromId = prefixId(host, c.fromId);
      if (typeof c.toId === "string") c.toId = prefixId(host, c.toId);
      return c;
    });
  // judging rides PER LANE, compact (T278c: {sid: [{k, t, t1, j, ...}]}), so its keys are prefixed like the
  // lanes'; an older kernel's flat list (entries carrying `sid`) is converted to that shape first, so the
  // merge below and the pane's expander see one shape whatever build each host runs
  if (out.judging !== undefined) {
    const jw = judgingToWire(out.judging);
    const j: any = {};
    for (const [sid, lane] of Object.entries(jw)) j[prefixId(host, sid)] = lane;
    out.judging = j;
  }
  return out;
}

/** The compact per-lane judging shape from either shape: a flat list of {judge, sid, t, t1, kind, text, ms, in, out,
 *  sent, recv, open} (a kernel older than T278c) is grouped by sid into the kernel's compact entries; a map passes
 *  through. Mirrors kernel/kernel.py _compact_judging. */
export function judgingToWire(j: any): Record<string, any[]> {
  if (!Array.isArray(j)) return (j && typeof j === "object") ? j : {};
  const out: Record<string, any[]> = {};
  for (const e of j) {
    if (!e || typeof e !== "object") continue;
    const c: any = { k: `${e.t}\u001f${e.judge}`, t: e.t, j: e.judge };   // (t, judge): the kernel's key, stable for an in-flight run
    if (e.t1 !== undefined && e.t1 !== null) c.t1 = e.t1;
    if (e.kind && e.kind !== "run") c.kd = e.kind;
    if (e.text) c.x = String(e.text).slice(0, 90);
    for (const f of ["ms", "in", "out"]) if (e[f]) c[f] = e[f];
    if (e.sent !== undefined && e.sent !== null) c.s = e.sent;
    if (e.recv !== undefined && e.recv !== null) c.r = e.recv;
    if (e.open) c.u = true;
    (out[String(e.sid ?? "")] ||= []).push(c);
  }
  return out;
}

/** The active-chat cue: `tid` is the chat's transcript sid (matched against bars' prefixed `tid`) and
 *  `name` its display name — prefix both so a remote kernel's cue lights its own (prefixed) lane. */
function _prefixActiveChat(host: string, ac: any): any {
  if (!ac || typeof ac !== "object") return ac;
  const out: any = { ...ac };
  if (typeof out.tid === "string") out.tid = prefixId(host, out.tid);
  if (typeof out.name === "string") out.name = prefixId(host, out.name);
  return out;
}

/** Prefix a full timeline lanes payload ({type:"data"}.data): sessions (id + display name) plus the
 *  shared detail fields. */
export function prefixTimelineData(host: string, d: any): any {
  if (!host || !d || typeof d !== "object") return d;
  const out = _prefixTimelineDetail(host, d);
  if (Array.isArray(out.sessions)) out.sessions = out.sessions.map((s: any) => _prefixIdBearing(host, s, "id"));
  if (out.activeChat) out.activeChat = _prefixActiveChat(host, out.activeChat);
  return out;
}

export interface Route {
  host: string; // "" = the local kernel
  msg: any; // a copy with this host's ids stripped back to bare
}

/** Decide which kernel(s) an OUTBOUND (browser→kernel) message goes to, stripping the host prefix off the
 *  ids for that kernel. Most messages target one session → one route. A reorder (an `order[]` that can mix
 *  hosts after a cross-host drag) fans out to one route PER host, each carrying only its own sids in their
 *  relative order. A message with no session id (a global pref like setColormap, or `ready`) → local.
 *
 *  `knownHosts` (the manager passes its attached set) enables two extra routings that need to know which
 *  hosts exist: NAME-addressed messages (the timeline's compact/sendCommand target a session by display
 *  name, which inbound prefixing made `host:name`) route to a KNOWN host only — a local name that happens
 *  to contain ":" must never misroute — and messages with no sid that mean the same thing on every kernel
 *  (a hover CLEAR, the gear's kernel-side settings) fan out to all of them. */
/** `id` without `host`'s own prefix when it carries it ("host:rest" → "rest"), else unchanged: the strip a remote
 *  route applies to what it sends, so an id that never had the prefix (a card id "sid:gN") keeps every part. The
 *  first-colon cut (bareId) is right only for ids KNOWN to be prefixed, and an outbound message mixes both. */
export function stripHost(host: string, id: string): string {
  return host && typeof id === "string" && id.startsWith(host + ":") ? id.slice(host.length + 1) : id;
}

export function routeOutbound(msg: any, knownHosts?: ReadonlySet<string>): Route[] {
  if (!msg || typeof msg !== "object") return [{ host: LOCAL, msg }];

  // redial ALWAYS stays LOCAL, `host` INTACT: it asks the kernel THIS page talks to for a fresh dial of its
  // tunnel to `host` (the composer's refusal on a downed host, render.ts). The explicit-host rule below
  // carried it to that very host — down, so it dropped with a toast about "redial" on top of the refusal's
  // own copy — with the field stripped, which the kernel's handler requires (2026-09-10).
  if (msg.type === "redial") return [{ host: LOCAL, msg }];

  // an explicit `host` field wins (the + modal's createSession picks the target kernel): route there with
  // the field stripped — the kernel's handlers are host-blind.
  if (typeof msg.host === "string") {
    const { host, ...rest } = msg;
    return [{ host: host || LOCAL, msg: rest }];
  }

  // The VIEWS store is per kernel and a dashboard edits only its LOCAL one: a tag edit or a whole-blob
  // views write goes to the local socket whatever fields it carries (the 2026-09-05 review: the tag
  // op's fields rode at the top level, so a tag `name` that happened to look like a remote lane's
  // display name would have taken the name-addressed route below to that host — tag names and
  // session names share a field name, not a meaning).
  if (msg.type === "tagEdit" || msg.type === "setTimelineViews") return [{ host: LOCAL, msg }];

  // order[] (reorderTabs / the timeline's writeOrder): split across the hosts it touches.
  if (Array.isArray(msg.order) && msg.order.some((x: any) => typeof x === "string")) {
    const byHost = new Map<string, string[]>();
    for (const x of msg.order) {
      if (typeof x !== "string") continue;
      const h = hostOf(x);
      if (!byHost.has(h)) byHost.set(h, []);
      byHost.get(h)!.push(bareId(x));
    }
    return [...byHost.entries()].map(([host, order]) => ({ host, msg: { ...msg, order } }));
  }

  // a hover CLEAR has no session id — broadcast so every kernel drops its highlight.
  if (msg.type === "timelineHover" && msg.off) return [LOCAL, ...(knownHosts || [])].map((h) => ({ host: h, msg }));

  // The gear's kernel-side settings mean the same thing on every attached machine, and carry no session
  // id to route by — so the fall-through at the bottom sent them to the LOCAL kernel alone. Every other
  // kernel silently kept its old setting while the gear, which fills from the local /version, showed the
  // change as applied everywhere: Auto Nudge switched off in the dashboard, still nudging the sessions
  // running on the other machine (the user 2026-08-14, whose two kernels had been disagreeing for days
  // with nothing on screen to say so). Broadcast, like the hover clear above.
  if (KERNEL_SETTING.has(msg.type)) return [LOCAL, ...(knownHosts || [])].map((h) => ({ host: h, msg }));

  // The BOARD-WIDE Clear all (the feed footer's, T286; the session header's Clear all is askClearMany, routed
  // by its session id below) carries no session id, so it fell through to the local kernel alone and a merged
  // board's Clear all left every remote card standing. Broadcast, local first: each kernel clears its own
  // feed's cards and appends its own ledger rows, so each side's Undo stays whole (FederationManager.outbound
  // remembers the hosts and sends undoClear to the same ones). Deliberately NOT a KERNEL_SETTING: a setting
  // queues on a down socket and replays on reconnect, and a Clear all replayed minutes later would clear
  // cards the user never saw; a kernel that is down at the click misses it and its cards stay.
  if (msg.type === "clearAll") return [LOCAL, ...(knownHosts || [])].map((h) => ({ host: h, msg }));

  // openFolder ALWAYS stays LOCAL, `id` UNSTRIPPED (the user 2026-07-03): unlike every other id-bearing
  // message, this one means "open a window on the machine the BROWSER is running on" — routing it to a
  // remote kernel would open a folder/terminal on that headless machine's own (unwatched) screen. The
  // local kernel needs the host prefix INTACT to know which remote machine to SSH into instead of treating
  // the path as local (see bin/romp-kernel's openFolder handler + _split_host_id).
  if (msg.type === "openFolder") return [{ host: LOCAL, msg }];

  // a scalar session id picks the owning host.
  let host = LOCAL;
  for (const k of SCALAR_ID) {
    if (typeof msg[k] === "string") {
      const h = hostOf(msg[k]);
      if (h) { host = h; break; }
    }
  }
  if (host !== LOCAL) {
    const out: any = { ...msg };
    for (const k of SCALAR_ID) if (typeof out[k] === "string") out[k] = stripHost(host, out[k]);
    // a batched clear (askClearMany) carries the session's card ids too — the remote kernel wants them without
    // the host prefix. ONLY the host prefix (T287, the user 2026-09-09): a card id is "sid:gN" and prefixInbound
    // prefixes the ask's sid, not its item ids, so the first-colon cut bareId makes turned "sid:g448" into
    // "g448"; the owning kernel then recorded a node id with no session, cleared nothing, and the cards the
    // laptop's session-header Clear all had crossed off came back with the next payload and every restart.
    if (Array.isArray(out.itemIds)) out.itemIds = out.itemIds.map((x: any) => typeof x === "string" ? stripHost(host, x) : x);
    return [{ host, msg: out }];
  }

  // name-addressed (compact/sendCommand `name`, deepLink `session`): a remote lane's display name is
  // host-prefixed — route to that KNOWN host with the prefix stripped. Only the field that decided the
  // route is stripped (e.g. renameSession's `name` is the user's new title, untouched — it routed by id).
  if (knownHosts && knownHosts.size) {
    for (const k of ["name", "session"]) {
      if (typeof msg[k] === "string") {
        const h = hostOf(msg[k]);
        if (h && knownHosts.has(h)) return [{ host: h, msg: { ...msg, [k]: bareId(msg[k]) } }];
      }
    }
  }
  return [{ host: LOCAL, msg }];
}

/** Merge per-host tab orders into ONE list for the merged strip: each host's order verbatim, concatenated in
 *  `hostSeq` order (local first, then attach order), and then arranged by the VIEWER's own order (the user
 *  2026-07-31 — see ./view-order for why that moved out of the kernel). Values are already prefixed by
 *  prefixInbound. Deduped; non-strings dropped.
 *
 *  The concatenation is the SEED, not the answer: it decides where a session the viewer has never arranged
 *  goes, and nothing more. With no arrangement stored, `applyViewOrder` is the identity and this returns the
 *  host-blocked concatenation that shipped before — the single-kernel path is untouched either way. */
export function mergeHostOrder(perHost: Record<string, readonly string[]>, hostSeq: readonly string[],
                               view: readonly string[] = []): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const h of hostSeq) {
    for (const id of perHost[h] || []) {
      if (typeof id === "string" && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
    }
  }
  return applyViewOrder(out, view);
}

/** Merge per-host feed snapshots into ONE payload. The `feed` message is a WHOLE-feed snapshot that the
 *  pane wholesale-replaces its state from — so without merging, the local kernel's snapshot and each
 *  remote's snapshot (both pushed ~every 2s) alternate and clobber each other, and the feed visibly flips
 *  back and forth ("repeatedly reloading"). Concatenate the arrays (items/asks/working) in hostSeq order
 *  (local first); keep the scalar chrome fields (now, dismissedCount, flags) from the LOCAL host, since the
 *  dashboard's own controls are local-authoritative. Ids are already prefixed by prefixInbound.
 *
 *  A pane's clock anchor is a PAIR that travels with the frame: `now` (the emitting kernel's clock) and
 *  `nowAt` (the local ms when that frame ARRIVED from the wire, `arrivedAt[host]`). The merge is emitted
 *  not only on a wire arrival but on a view-order write, on every remote host's frame and on a detach, and
 *  a pane that runs its ages on the kernel's clock (feed-age.ts liveNow) sets that clock from the frame it
 *  is handed: anchoring `now` on the EMIT time would move every age back by however long the local board
 *  had been quiet. The pair comes from the LOCAL host's frame; with no local frame yet, from the newest
 *  remote arrival that carries a clock — the same host for both halves, so they always describe one frame.
 *  Absent `arrivedAt` (a caller with no wire), no `nowAt` is set and the pane anchors on its own arrival. */
/** Apply the viewer's foreign cleared ids (bare, from the local payload) over REMOTE rows of a merged feed:
 *  remote asks/items they name are dropped; remote archived tops they name read cleared, rolled down to the
 *  subtree (the live tree's top-only cross-off), except a cleared top whose only completion is the copied
 *  status, which leaves the list with its subtree, as the owning kernel's overlay (_ledger_cleared_overlay)
 *  drops it: a cleared flag rolls up to status cleared, so a copied "completed" beside a clear is a stale copy,
 *  not a completion. A top with its own verdict (`derived` false) or a takeaway (a non-blank `summary`) stays
 *  listed and reads cleared. Rewrites `merged.asks`/`merged.items` and each remote ledger entry's archivedTops
 *  with fresh row objects; the host payloads' own rows are not mutated. No-op without ids. */
export function applyViewerClears(merged: any, ledgers: any[], clearedForeign: any): void {
  const foreign = new Set<string>(Array.isArray(clearedForeign) ? clearedForeign.filter((x: any) => typeof x === "string") : []);
  if (!foreign.size) return;
  // Remoteness is the ROW's sid (prefixInbound prefixes it: "host:sid"); the item and node ids are unprefixed
  // ("sid:gN") and compare as they are against the bare foreign ids (T287: reading the id's own first colon as
  // a host took the uuid for a host and compared "gN", so nothing ever matched).
  const remote = (sid: any) => typeof sid === "string" && hostOf(sid) !== LOCAL;
  const hit = (sid: any, id: any) => remote(sid) && typeof id === "string" && foreign.has(id);
  merged.asks = merged.asks.filter((a: any) => !hit(a?.sid, a?.itemId));
  merged.items = merged.items.filter((c: any) => !hit(c?.sid, c?.itemId));
  ledgers.forEach((l: any, i: number) => {
    if (!l || !remote(l.sid)) return;
    const tops = l.ledger?.archivedTops;
    if (!Array.isArray(tops) || !tops.length) return;
    let rootCleared = false, drop = false, changed = false;
    const out: any[] = [];
    for (const n of tops) {
      if (n?.depth === 0) {
        rootCleared = !!n.cleared || (typeof n.id === "string" && foreign.has(n.id));
        // The owning kernel's rule, read from the same projection fields: at depth 0 `derived` means the copied
        // status or a summary, never an ancestor (a root has none), so derived with a blank summary is a root
        // whose only completion is the copied status. Cleared, it is dropped rather than marked; the viewer's
        // ids stand in for the rows the owning kernel's own overlay would have read, so the outcome matches.
        drop = rootCleared && !!n.derived && !String(n.summary || "").trim();
      }
      if (drop) { changed = true; continue; }   // a dropped root's descendants follow it in the flat list; they go with it
      const c = !!n?.cleared || (typeof n?.id === "string" && foreign.has(n.id)) || (n?.depth !== 0 && rootCleared);
      if (c === !!n?.cleared) { out.push(n); continue; }
      changed = true;
      out.push({ ...n, cleared: c });
    }
    // a fresh ENTRY too, never the host payload's own object: the merge pushed those by reference
    if (changed) ledgers[i] = { ...l, ledger: { ...l.ledger, archivedTops: out } };
  });
}

export function mergeHostFeeds(perHost: Record<string, any>, hostSeq: readonly string[],
                               view: readonly string[] = [], deadHosts: readonly string[] = [],
                               arrivedAt: Record<string, number> = {}): any {
  const local = perHost[LOCAL] || {};
  const merged: any = { ...local, type: "feed", items: [], asks: [], working: [], awaiting: [], stateUnknown: [], order: [], sessions: [], userTodos: {} };
  let anchor = typeof local.now === "number" ? LOCAL : null;
  if (anchor === null) {
    for (const h of hostSeq) {
      const f = perHost[h];
      if (h === LOCAL || !f || typeof f.now !== "number" || typeof arrivedAt[h] !== "number") continue;
      if (anchor === null || arrivedAt[h] > arrivedAt[anchor]) anchor = h;
    }
  }
  if (anchor !== null) {
    merged.now = perHost[anchor].now;
    if (typeof arrivedAt[anchor] === "number") merged.nowAt = arrivedAt[anchor];
    else delete merged.nowAt;
  }
  // `ledgers` drives the FLEET pane (it rides the same feed message). Only include it once at least one host
  // has actually BUILT its ledgers — else the fleet's loader-gate (needs an array) would drop onto an empty
  // pane. Kept undefined until then so the loader holds, exactly like the single-kernel path.
  let anyLedgers = false;
  const ledgers: any[] = [];
  // dismissed/undo chrome spans hosts: the count SUMS and undo is possible when ANY kernel can undo —
  // clearing a remote card must light the local Undo button (the clear routed to that kernel).
  let dismissed = 0, anyDismissed = false, canUndo = false;
  // remote kernels' automatic-sync outcomes (self-updates, pushes, pulls) reach the local Log too
  // (the user 2026-08-15: a devbox updating itself all day left no trace on the laptop dashboard —
  // mergeHostFeeds kept only the local host's scalar chrome, dropping remote syncNotices on the
  // floor). Remote rows are host-prefixed like every other remote surface; the SIG is host-scoped so
  // two kernels' ring sequences can never collide in the seen-set.
  const syncs: any[] = [];
  // Every kernel counts feed builds on its OWN counter, so `merged.buildId` (the local scalar kept by
  // the spread above) says nothing about any REMOTE host's frame. The per-host map is what lets the
  // feed pane compare a payload against a cardMoveAck on the SAME counter (the user 2026-08-15, whose
  // reply to a remote card bounced Working → Completed → Working: the local buildId, large after days
  // of uptime, "outranked" the remote ack's small post-restart buildId on the first merged emission,
  // dropping the prediction while the cached remote frame still predated the reopen).
  const buildIds: Record<string, number> = {};
  for (const h of hostSeq) {
    const f = perHost[h];
    if (!f) continue;
    if (typeof f.buildId === "number") buildIds[h] = f.buildId;
    if (Array.isArray(f.syncNotices)) {
      for (const r of f.syncNotices) {
        if (!r || !r.sig) continue;
        syncs.push(h === LOCAL ? r : { ...r, sig: h + "|" + r.sig, text: h + ": " + (r.text || "") });
      }
    }
    if (Array.isArray(f.items)) merged.items.push(...f.items);
    if (Array.isArray(f.asks)) merged.asks.push(...f.asks);
    if (Array.isArray(f.working)) merged.working.push(...f.working);
    if (Array.isArray(f.awaiting)) merged.awaiting.push(...f.awaiting);   // await-green awaiting dots ride like working
    // the unreadable-state list rides the same way; a host too old to send it contributes to
    // NEITHER list, so its sessions stay blank (= "quiet") rather than reading falsely as unknown
    if (Array.isArray(f.stateUnknown)) merged.stateUnknown.push(...f.stateUnknown);
    if (Array.isArray(f.order)) merged.order.push(...f.order);   // grouped-mode session rank: local first, ids pre-prefixed
    if (Array.isArray(f.sessions)) merged.sessions.push(...f.sessions);   // the tab-strip session list (footer filter menu), sid+name pre-prefixed
    if (f.userTodos && typeof f.userTodos === "object" && !Array.isArray(f.userTodos))
      Object.assign(merged.userTodos, f.userTodos);   // sid-keyed open user-todo counts, keys pre-prefixed (the quiet card marker; a host too old to send it contributes nothing)
    if (Array.isArray(f.ledgers)) { anyLedgers = true; ledgers.push(...f.ledgers); }
    if (typeof f.dismissedCount === "number") { anyDismissed = true; dismissed += f.dismissedCount; }
    if (f.canUndoClear) canUndo = true;
  }
  // the grouped feed ranks its session runs off `order`, so it takes the viewer's arrangement like the tab
  // strip does — the two surfaces have to agree or the feed's groups and the tabs read in different orders.
  merged.order = applyViewOrder(merged.order, view);
  // The VIEWER's ledger over remote rows (review find, 2026-09-09): a remote kernel's feed and its archive
  // projection read only their own cleared.jsonl, and the local ledger can hold a remote card's clear (a
  // gesture taken while the owner was unreachable, a ledger copied between machines, an older client). The
  // local payload carries those ids (kernel: clearedForeign, bare); a remote ask or item they name is
  // dropped, and a remote archived top they name reads cleared, its subtree with it, exactly as the owning
  // kernel's own overlay would have read them: a cleared top whose only completion is the copied status
  // leaves the list with its subtree (the kernel's _ledger_cleared_overlay drops it, since a cleared flag
  // rolls up to status cleared and the copied "completed" is a stale copy); one with its own verdict or a
  // takeaway stays listed, struck through. Local rows are untouched: the local kernel already applied its
  // ledger to them.
  applyViewerClears(merged, ledgers, local.clearedForeign);
  if (anyLedgers) merged.ledgers = ledgers;
  else delete merged.ledgers;
  if (anyDismissed) merged.dismissedCount = dismissed;
  if ("canUndoClear" in merged || canUndo) merged.canUndoClear = canUndo;
  if (syncs.length) merged.syncNotices = syncs;
  else delete merged.syncNotices;
  merged.buildIds = buildIds;
  // Hosts ATTACHED but yet to contribute a feed payload (the user 2026-08-25: after attaching, the
  // sessions land via the faster tabOrder/timeline channels while the cards trail with no cue) —
  // the sessions-shown/cards-pending window, named per host so the board can say cards are coming.
  // Presence in perHost is the event: an EMPTY contribution is a valid arrival (the host had nothing
  // to send) and retires the hint; a detach deletes the entry (dropHost), so a reattach re-pends and
  // the hint re-arms identically — no timers anywhere in this signal. The local kernel never pends.
  merged.pendingHosts = hostSeq.filter((h) => h !== LOCAL && !(h in perHost));
  // …and which of those waits are on a DEAD link (the caller knows its sockets): the board says
  // THAT instead of an open-ended wait — the fail-loudly rule; still retired only by the real
  // events (a payload arriving, or the detach dropping the host from hostSeq).
  merged.pendingDead = merged.pendingHosts.filter((h: string) => deadHosts.includes(h));
  return merged;
}

// ── cross-host clock re-basing (the user 2026-08-15) ─────────────────────────────────────────────
// Every kernel stamps its payload times with ITS OWN clock, and the merges keep the LOCAL kernel as
// the clock authority — so an attached machine whose clock runs ahead painted its bars and marks
// shifted right, and a postal connector could touch a sender's lane AFTER that lane's last bar (the
// screenshot: a send apparently fired by a stopped session — impossible, and false). Each payload
// carries its emitting kernel's `now`; the delta against the LOCAL payload's `now` in the same merge
// IS that host's offset — re-measured every merge, so drift self-corrects, and a sub-second delta is
// measurement jitter, not skew: left alone, so pixels never move without new information. A host
// whose payload carries no `now` (older kernel) is unknown and never guessed — its times pass
// through untouched, exactly the not-reporting rule everywhere else in this file.
// One field is DELIBERATELY cross-clock: a connector's `exec` is the RECIPIENT machine's event (the
// read receipt carries the reader's clock into the sender's log — bin/romp-postal-service), so exec
// re-bases by the offset of the host the toId lane lives on, knowable only after the stitch resolves
// foreign endpoints onto lanes (rebaseExecs). A connector still pending (hasExec false) carries a
// sender-clock COPY of sent in exec, which therefore shifts with its emitter like sent itself.
const SKEW_FLOOR_S = 1;
const BAR_TIMES = ["start", "end"] as const;      // turns[sid] bars
const MARK_TIMES = ["t"] as const;                // nudge marks
const JUDGING_TIMES = ["t", "t1", "s", "r"] as const;   // a compact judging entry's clocks (T278c)
const LANE_TIMES = ["since"] as const;            // session rows

export function hostOffsets(perHost: Record<string, any>): Record<string, number> {
  const local = perHost[LOCAL];
  const ln = local && typeof local.now === "number" ? local.now : null;
  const out: Record<string, number> = {};
  if (ln == null) return out;
  for (const [h, d] of Object.entries(perHost)) {
    if (h === LOCAL || !d || typeof d.now !== "number") continue;
    const off = ln - d.now;
    if (Math.abs(off) >= SKEW_FLOOR_S) out[h] = off;
  }
  return out;
}

function shiftRow(r: any, keys: readonly string[], off: number): any {
  if (!r || typeof r !== "object") return r;
  const c: any = { ...r };
  for (const k of keys) if (typeof c[k] === "number") c[k] += off;
  return c;
}

/** Re-base ONE host's payload times onto the local clock (a copy; zero offset returns it untouched). */
export function rebaseHostTimes(d: any, off: number): any {
  if (!off || !d || typeof d !== "object") return d;
  const c: any = { ...d };
  if (Array.isArray(c.sessions)) c.sessions = c.sessions.map((s: any) => shiftRow(s, LANE_TIMES, off));
  if (c.turns && typeof c.turns === "object") {
    const t: any = {};
    for (const [sid, bars] of Object.entries(c.turns))
      t[sid] = Array.isArray(bars) ? bars.map((b: any) => shiftRow(b, BAR_TIMES, off)) : bars;
    c.turns = t;
  }
  if (Array.isArray(c.nudges)) c.nudges = c.nudges.map((m: any) => shiftRow(m, MARK_TIMES, off));
  if (c.judging !== undefined) {
    const jw = judgingToWire(c.judging);
    const j: any = {};
    for (const [sid, lane] of Object.entries(jw)) j[sid] = Array.isArray(lane) ? lane.map((e: any) => shiftRow(e, JUDGING_TIMES, off)) : lane;
    c.judging = j;
  }
  if (Array.isArray(c.messages))
    c.messages = c.messages.map((m: any) => {
      const s = shiftRow(m, ["sent"], off);
      // pending exec is the emitter's copy of sent — it moves with the emitter; a REAL exec is the
      // recipient's clock and waits for rebaseExecs (post-stitch, when the recipient lane is known)
      if (s && typeof s === "object" && typeof s.exec === "number" && !s.hasExec) s.exec += off;
      return s;
    });
  return c;
}

/** The exec pass, post-stitch: shift each delivered connector's exec by the RECIPIENT lane's host
 *  offset (a local recipient, or an unmeasured host, shifts nothing). */
export function rebaseExecs(messages: any[], offsets: Record<string, number>): any[] {
  if (!messages.length) return messages;
  return messages.map((m: any) => {
    if (!m || typeof m !== "object" || typeof m.exec !== "number" || !m.hasExec) return m;
    const off = offsets[hostOf(String(m.toId || ""))] || 0;
    return off ? { ...m, exec: m.exec + off } : m;
  });
}

/** Stitch CROSS-HOST postal connectors onto merged lanes. Each kernel emits a connector when at least
 *  one end is its own lane; the FOREIGN end's sid is bare in its log (a kernel knows nothing of host
 *  prefixes) and inbound prefixing blindly prefixed it with the EMITTING host — so on the merged board
 *  it matches no lane. Re-point any endpoint that isn't a lane id at the lane whose BARE sid matches
 *  (uuids — no cross-host collisions), fill a missing display name from that lane, then dedupe the two
 *  kernels' copies of the same message (by id) — preferring the one that knows the real delivery time
 *  (hasExec: the recipient's kernel binds exec to its own transcript; the sender's can't). */
export function stitchMessages(messages: any[], sessions: readonly any[]): any[] {
  if (!messages.length) return messages;
  const laneIds = new Set(sessions.map((s: any) => s && s.id));
  const byBare = new Map(sessions.filter((s: any) => s && typeof s.id === "string").map((s: any) => [bareId(s.id), s]));
  const best = new Map<string, any>();
  const out: any[] = [];
  for (const m of messages) {
    if (!m || typeof m !== "object") { out.push(m); continue; }
    const c: any = { ...m };
    for (const [idKey, nameKey] of [["fromId", "from"], ["toId", "to"]] as const) {
      const v = c[idKey];
      if (typeof v !== "string" || laneIds.has(v)) continue;
      const lane = byBare.get(bareId(v));
      if (lane) {
        c[idKey] = lane.id;
        if (!c[nameKey]) c[nameKey] = lane.name; // the emitting kernel never knew the foreign name
      }
    }
    const key = typeof c.id === "string" ? c.id : null;
    if (!key) { out.push(c); continue; }
    const prev = best.get(key);
    if (!prev) { best.set(key, c); out.push(c); continue; }
    if (c.hasExec && !prev.hasExec) Object.assign(prev, c); // upgrade in place — keeps sent-order
  }
  return out;
}

/** Merge per-host timeline lanes payloads ({type:"data"}.data, already prefixed) into ONE. Sessions
 *  concatenate in hostSeq order (local lanes first, each remote host's group below — the view draws a
 *  half-row gap at each host boundary via the `host` field stamped here); `turns` (keyed by prefixed sid)
 *  union; the marks arrays concatenate, with cross-host connectors stitched onto the merged lanes.
 *  Scalar chrome (now/usage/focus/hover/cmapGrad…) stays LOCAL — the browser's own kernel is the
 *  clock + chrome authority, same as the feed merge. */
export function mergeHostTimelines(perHost: Record<string, any>, hostSeq: readonly string[],
                                   view: readonly string[] = [], deadHosts: readonly string[] = []): any {
  const local = perHost[LOCAL] || {};
  const offsets = hostOffsets(perHost);   // each host's clock vs the local authority, this merge
  const merged: any = { ...local, sessions: [], turns: {}, messages: [], judging: {} };
  for (const h of hostSeq) {
    const d = rebaseHostTimes(perHost[h], offsets[h] || 0);
    if (!d) continue;
    if (Array.isArray(d.sessions)) merged.sessions.push(...d.sessions.map((s: any) => ({ ...s, host: h })));
    if (d.turns && typeof d.turns === "object") Object.assign(merged.turns, d.turns);
    if (Array.isArray(d.messages)) merged.messages.push(...d.messages);
    if (d.judging !== undefined) Object.assign(merged.judging, judgingToWire(d.judging));   // per lane, like turns (T278c)
  }
  // lanes are the third surface reading this order (chat strip, feed groups, timeline lanes): arrange them
  // the same way, before the message stitch, which pairs postal arrows against the lane list.
  merged.sessions = applyViewOrderTo(merged.sessions, view, (x: any) => String((x && x.id) || ""));
  merged.messages = rebaseExecs(stitchMessages(merged.messages, merged.sessions), offsets);
  // Hosts ATTACHED but yet to contribute a lanes payload — the feed merge's pendingHosts rule, applied
  // to the lanes channel (the user 2026-09-02: after a restart or a phone re-foreground the remote
  // hosts' lanes were simply absent for two minutes, with nothing on the board saying they were
  // coming). Presence in perHost is the event: the host's first lanes payload (an empty one included)
  // retires it, a detach deletes the entry, and the view draws a placeholder row per name until then.
  // No timers anywhere in this signal. The local kernel never pends.
  merged.pendingHosts = hostSeq.filter((h) => h !== LOCAL && !(h in perHost));
  merged.pendingDead = merged.pendingHosts.filter((h: string) => deadHosts.includes(h));
  return merged;
}

/** Merge per-host {type:"bars"} detail messages (already prefixed) into ONE. The panel's applyBars
 *  wholesale-replaces its turns/marks, so per-host bars MUST be merged here or each host's push would
 *  clobber the others' bars (the same clobber the feed had). `now` stays LOCAL (clock authority).
 *  `sessions` (the merged lane list, from mergeHostTimelines) enables the cross-host connector stitch —
 *  the bars message itself carries no lanes. */
export function mergeHostBars(perHost: Record<string, any>, hostSeq: readonly string[],
                              sessions: readonly any[] = []): any {
  const local = perHost[LOCAL] || {};
  const offsets = hostOffsets(perHost);   // each host's clock vs the local authority, this merge
  const merged: any = { ...local, type: "bars", turns: {}, messages: [], judging: {}, warming: false };
  for (const h of hostSeq) {
    const b = rebaseHostTimes(perHost[h], offsets[h] || 0);
    if (!b) continue;
    if (b.turns && typeof b.turns === "object") Object.assign(merged.turns, b.turns);
    for (const k of ["messages", "nudges"]) if (Array.isArray(b[k]) && Array.isArray(merged[k])) merged[k].push(...b[k]);
    if (b.judging !== undefined) Object.assign(merged.judging, judgingToWire(b.judging));   // per lane, like turns (T278c)
    if (b.warming) merged.warming = true;   // still warming if ANY host's build is the cold partial (keep the loader)
  }
  merged.messages = rebaseExecs(stitchMessages(merged.messages, sessions), offsets);
  return merged;
}

// ── remote-socket liveness (the user 2026-09-02) ─────────────────────────────────────────────────
// The kernel heartbeats EVERY WebSocket it serves — `ka` frames every KEEPALIVE_S (10s) — and those
// frames ride the /remote/<host>/ws relay too, so a remote socket that is truly open hears from its
// kernel at least every ~10s. The pane shim already keys a watchdog on exactly that beat for the LOCAL
// socket (STALE_MS): a half-open socket fires no onclose, ever, so a missing heartbeat is the only
// evidence there is. The remote sockets here had NO such check: on the audited phone re-foreground
// both relay sockets OPENED (handshake complete) and then delivered nothing — dead on arrival — and
// nothing bounded the wait until TCP gave up ~104s later (close 1006), so two attached, healthy hosts
// rendered as simply absent for two minutes, which the user read as wiped state. Same bounds as the
// shim, byte for byte: 30s of silence on an OPEN socket, 15s stuck CONNECTING, 8s CLOSED with no
// fresh attempt (a lost retry timer — a throttled tab).
export const REMOTE_STALE_MS = 30000;
export const REMOTE_CONNECT_MS = 15000;
export const REMOTE_REDIAL_MS = 8000;
// A resumed keep is PROVISIONAL (review find, 2026-09-08), the pane shim's PROVISIONAL_MS byte for byte: 1.5
// kernel keepalive periods (KEEPALIVE_S is 10 s, so 15 s: one beat may be in flight, two missing is silence).
// The `resume` stamp (resumed() below) re-bases a socket the browser still holds OPEN, but the far end can
// have died without a FIN reaching the browser (a laptop sleep across a network change, a tunnel whose local
// end stays open), and only the kernel's next frame can tell; until one lands the watchdog runs at this bound
// instead of REMOTE_STALE_MS.
export const REMOTE_PROVISIONAL_MS = 15000;

/** What the watchdog should do about ONE remote socket, from its state alone (pure, unit-tested):
 *  "close" — force-close so the onclose→redial chain runs (open but silent past the keepalive bound,
 *  or a hung handshake); "redial" — CLOSED with no fresh attempt: dial directly; "" — leave it be.
 *  `lastRecv` is stamped at open and on every frame, so an open socket's silence is measured from
 *  its own open, never from an earlier socket's traffic. `staleMs` is the silence bound for an OPEN
 *  socket: REMOTE_STALE_MS, or REMOTE_PROVISIONAL_MS while a resumed keep awaits its confirming frame. */
export function socketVerdict(readyState: number, lastRecv: number, connT: number, now: number, staleMs = REMOTE_STALE_MS): "close" | "redial" | "" {
  if (readyState === 1) return now - (lastRecv || connT) > staleMs ? "close" : "";
  if (readyState === 0) return now - connT > REMOTE_CONNECT_MS ? "close" : "";
  if (readyState === 3) return now - connT > REMOTE_REDIAL_MS ? "redial" : "";
  return "";
}

// ── the wiring: WebSockets per kernel + the attach UI ────────────────────────────────────────────
// Thin glue over the pure functions above. The LOCAL kernel stays the shim's existing single WS — this
// manager only ADDS connections to attached remote kernels, so with no remotes attached the dashboard is
// byte-for-byte the single-kernel path. The shim calls window.__rompFed.inbound("", msg) for local frames
// and window.__rompFed.outbound(m) for sends (both no-ops when this module isn't loaded, e.g. the timeline
// pane), and exposes window.__rompLocalSend + window.__rompApp.

/** A pane's frame handler: the window "message" listener's signature, so one function serves both paths. */
export type FrameHandler = (e: MessageEvent) => void;

/** Report a subscriber's exception the way the DOM reports a listener's: through reportError where the page has
 *  it (window.onerror / the console, as an uncaught exception), else console.error. Never throws. */
export function reportListenerError(e: unknown): void {
  const g: any = globalThis;
  try {
    if (typeof g.reportError === "function") { g.reportError(e); return; }
  } catch (_) { /* fall through to the console */ }
  try { console.error(e); } catch (_) { /* nothing left to report to */ }
}

interface Conn {
  host: string;
  ws: WebSocket | null;
  url: string;
  closed: boolean;
  live: boolean; // kernel reports this tunnel "up" — the only state in which its port is dialed
  lastRecv: number; // epoch ms of the last frame on the CURRENT socket (keepalives count); 0 = none yet
  resumeProvisional: number; // the `resume` stamp lastRecv rests on until a frame confirms it (the watchdog runs at REMOTE_PROVISIONAL_MS meanwhile); 0 = confirmed, or no stamp
  connT: number;    // when the current socket's connect() attempt started — the watchdog's reference point
  // KERNEL_SETTING messages (newest per type) and the pane's own BOOKKEEPING (newest per key, see
  // BOOKKEEPING) that arrived while this host's socket was down — flushed on the socket's open event
  // (sendRemote/flushPending). Bounded by construction: one entry per setting type, per bookkeeping
  // key. Lives on the CONN, not the socket, so it survives every re-dial — the onclose retry, the
  // poll's, and the liveness watchdog's abandon-and-dial (watchdog()).
  pending: Map<string, any>;
}

/** The TYPES held on a conn's queue, for the hostconn rows (flush-halt's `held`, detach's `pendingDropped`):
 *  a bookkeeping key carries the sid it is per, and the journal names what, not which. */
function pendingTypes(c: Conn): string[] {
  return [...c.pending.values()].map((m) => (m && typeof m.type === "string" ? m.type : ""));
}

export class FederationManager {
  app = "chat";
  private conns = new Map<string, Conn>();
  private frozeAt = 0;   // the Page Lifecycle `freeze` before the current thaw: a socket already overdue at that moment is not stamped by resumed()
  private perHostOrder: Record<string, string[]> = {};
  private perHostTabs: Record<string, any[]> = {};
  private perHostLive: Record<string, string[]> = {};   // each host's affirmed-live sids (T258): the merged tabOrder carries their union
  // Each host's `skeleton` slice of its tab strip (the user 2026-09-07): after a reconnect the local kernel
  // lists the sessions it is NOT re-sending in full, and the chat pane draws those as "loads on your
  // click" instead of from the stale copy it still holds. inbound REBUILDS the strip it hands the pane
  // from these stores, so without a store of its own the key was dropped and every stale session read
  // as loaded. Same rule as the pane's: an array REPLACES the slice; an absent key KEEPS it, pruned to
  // that host's new order (the kernel omits the key only when its set is empty, but the pane shim's
  // FIFO replaces a queued strip with a newer one, so absent means "no news", never "none").
  private perHostSkeleton: Record<string, string[]> = {};
  private localViews: any = null;   // the LOCAL kernel's session-views blob, carried on merged tabOrder re-emits
  private localViewsRejected: any = null;   // the last LOCAL tabOrder blob the seq gate turned away since it last adopted one — the caps frame adopts it (inbound)
  private tlViewsRejected: any = null;      // the same for the LOCAL lanes payload's blob (perHostTl[LOCAL].views)
  private localViewsAnnounced: number | null = null;   // the seq the last LOCAL caps frame announced as the kernel's current store when the tabOrder store adopted no kept blob — a LATER blob at exactly that seq is adopted below the stored one (announcedSeq); cleared by the next adoption that changes the stored blob (announcedAfter)
  private tlViewsAnnounced: number | null = null;      // the same for the lanes payload's store
  private localSelfHost = "";       // the LOCAL kernel's own name (its tabOrder frame's selfHost), carried the same way
  private perHostSids: Record<string, Set<string>> = {};
  private perHostFeed: Record<string, any> = {}; // last feed snapshot per host — merged so they don't clobber
  private perHostFeedAt: Record<string, number> = {}; // host -> local ms its snapshot ARRIVED: the merged frame's clock anchor (mergeHostFeeds `nowAt`), so a re-emit anchors exactly as the arrival did
  private perHostTl: Record<string, any> = {}; //   last timeline lanes payload ({type:"data"}.data) per host
  private perHostTlBars: Record<string, any> = {}; // last timeline {type:"bars"} detail per host
  private hostSeq: string[] = [LOCAL]; // local first, then attach order — fixes the group order in the strip
  private downHosts = new Set<string>(); // attached, but its tunnel isn't up: what's on screen is a memory
  private dialingHosts = new Set<string>(); // the kernel is dialing or health-checking these right now (the row's `dialing`)
  // each host's recovery counter as last seen (/tunnels upSeq, T291b): the kernel bumps it when a row that had
  // missed polls answers again, so a link that failed a request while its status never left "up" still has a
  // recovery event; a change is treated as that host coming back (hostUp). A first observation is not a bump.
  private upSeq = new Map<string, number>();
  private lastSeen: Record<string, number> = {}; // host -> epoch secs of its last `up` poll
  // the page's performance collector (ui/webview/perf-telemetry.ts), set by start(); inbound() times its own
  // merge and dispatch through it as fed:<type>, nested outside the pane's handler. Public so a test can hand
  // it a stand-in.
  perf: RompPerf | null = null;
  // The pane's frame handlers, registered through onFrame (window.__rompFed.onFrame): the merged frames this
  // layer emits reach them by direct call, never as a window "message" event. See emit() for why.
  private frameSubs: FrameHandler[] = [];

  /** Register a handler for the merged frames this manager emits (`feed`, `tabOrder`, `data`, `bars`); returns
   *  the unsubscribe. The pane registers the SAME wrapped handler it installs on window (frame-listener.ts), so
   *  the perf brackets nest as before and every frame reaches it exactly once: federation picks one path per
   *  frame (emit). Every other frame — the passthrough, `closed`, `hostUp`, `warn`, the shell's `romp:` posts —
   *  still arrives through the window listener. */
  onFrame(handler: FrameHandler): () => void {
    this.frameSubs.push(handler);
    return () => {
      const i = this.frameSubs.indexOf(handler);
      if (i >= 0) this.frameSubs.splice(i, 1);
    };
  }

  /** Hand one merged frame to the pane. With a subscriber registered: ONE MessageEvent, called into each handler
   *  directly, in registration order, over a snapshot of the list. Here the direct path deliberately differs from
   *  dispatchEvent on removal: the DOM skips a listener removed during dispatch, while a handler unsubscribed by
   *  an earlier handler in the same frame still runs once here (the snapshot is simpler, and nobody unsubscribes
   *  mid-delivery today). With none: window.dispatchEvent, exactly as before, so a pane bundle that predates the
   *  registry keeps working.
   *
   *  Why not always dispatch on window: Blink hands a same-world listener the event's data object itself, but a
   *  "message" listener in ANOTHER JavaScript world — a browser extension's content script on the pane's page —
   *  that reads event.data receives a STRUCTURED CLONE of it, made synchronously inside dispatchEvent. For a
   *  merged feed of several megabytes that is tens of milliseconds and as many megabytes of garbage per frame, in
   *  every feed-consuming pane, counted under this layer's fed:<type> bracket (its own work is under a
   *  millisecond); a probe measured 35-46 ms per dispatch of a 7 MB frame and 0 ms for a direct call. Nothing
   *  outside romp's bundles receives a merged frame now, so nothing can clone it.
   *
   *  A throwing handler is reported and the rest still run — the DOM's report-and-continue for event listeners.
   *  Without this a throw would propagate through inbound into the shim's socket callback and skip this layer's
   *  remaining work (the bars emission after a detach's lanes emission). */
  private emit(data: any): void {
    const ev = new MessageEvent("message", { data });
    const subs = this.frameSubs;
    if (!subs.length) { window.dispatchEvent(ev); return; }
    for (const h of subs.slice()) {
      try { h(ev); } catch (e) { reportListenerError(e); }
    }
  }

  start(): void {
    const w = window as any;
    this.app = w.__rompApp || "chat";
    // the page's performance collector (perf-telemetry.ts), published as window.__rompPerf: the pane bundle
    // installs its own on load, but the kernel-served timeline page has no bundle beyond this one, and its
    // inline boot (kernel.py _TIMELINE_BOOT) wraps its message listener through the window slot
    this.perf = installPerfTelemetry(this.app);
    w.__rompFed = {
      inbound: (h: string, m: any) => this.inbound(h, m),
      outbound: (m: any) => this.outbound(m),
      onFrame: (h: FrameHandler) => this.onFrame(h),   // the pane's direct-delivery registration (emit)
      hosts: () => this.hostSeq.filter((h) => h !== LOCAL), // attached hosts (the + modal's host picker)
      // Hosts that are attached but NOT reachable right now. Their sessions stay on screen — dropping
      // them would lose the thread — but every surface that shows one has to say the link is down, or
      // you are reading a transcript that stopped updating with nothing telling you (the user
      // 2026-07-29). `lastSeen` dates what is on screen.
      down: () => [...this.downHosts],
      // the attached hosts THIS pane is still waiting on, by its own channel (pendingFor): attached, and up
      // as far as the kernel knows, but their sessions are not on this pane's screen yet — the chat's pin
      // prune leaves their entries alone until they are (render.ts reachableHosts); the shell's network
      // panel says "loading sessions…" from the same set
      pending: () => this.pendingFor(),
      lastSeen: (h: string) => this.lastSeen[h] || 0,
      // is a dial attempt to this host in flight right now? The host-down notice's swirl spins on exactly
      // this (host-prefix.ts hostDialLive: the socket's CONNECTING state), and romp:hostDial below says
      // when it changes — on the dial, the open and the close, never on a timer
      dialing: (h: string) => { const c = this.conns.get(h); return hostDialLive(this.dialingHosts.has(h), c && c.ws ? c.ws.readyState : null); },
    };
    // A drag in ANY pane rewrites the arrangement; every other pane hears it through `storage` (which fires
    // only in other same-origin contexts) and this one through the writer's own CustomEvent. Both land here,
    // and re-emitting all three merged payloads is what moves the tabs, lanes and feed groups together.
    // ...and it RE-EMITS ONLY: reacting to an arrangement by rewriting it is what made a drag fail to
    // stick. The old gc-on-emit dropped ids the reporting hosts no longer list, judged against THIS
    // context's session lists — so any pane holding a stale list (a dashboard window whose socket died, a
    // surface that never got the newest session's push) answered another pane's drag by pruning the very
    // tab that had just moved, and the writer obeyed the write-back. On the audited drag the strip
    // permuted correctly and reverted in the same second, twice per attempt, so the tab looked like it
    // never moved at all — and it was always the NEWEST tab, the one a stale list is most likely to be
    // missing (the user 2026-08-02). A view arrangement is not new information about what exists; only a
    // host's own report is, so only an inbound tabOrder push may touch the store (absorbHostReport).
    const reorder = () => { this.emitMergedOrder(); this.emitMergedFeed(); this.emitMergedTimeline(false); };
    w.addEventListener("storage", (e: StorageEvent) => { if (!e.key || e.key === VIEW_ORDER_KEY) reorder(); });
    w.addEventListener(VIEW_ORDER_EVENT, reorder);
    // The kernel-served timeline page boots from an inline script that cannot import this module, so the
    // one implementation of the write is published here for it (its VS Code twin imports it directly).
    w.__rompWriteOrder = (order: unknown) =>
      writeViewOrder(Array.isArray(order) ? order.filter((x: unknown): x is string => typeof x === "string") : []);
    this.poll();
    setInterval(() => this.poll(), 4000); // converge on attach/detach made from the shell's network panel
    // the remote sockets' liveness watchdog (socketVerdict above) — the shim's 5s tick, for the relay side
    setInterval(() => this.watchdog(Date.now()), 5000);
    // …and the shim's visibility fast-path, for the relay side: a backgrounded tab's timers are
    // throttled and its sockets may have died in its sleep, so the instant it is foregrounded every
    // remote socket that is not open, or has gone quiet past the bound, is closed and redialed now
    // rather than waited out — the phone's re-foreground is exactly the audited case.
    try {
      this.watchLifecycle(document);
    } catch (e) { /* no document — the node tests construct the manager bare */ }
  }

  /** The Page Lifecycle listeners on the document (public and parameterised so the tests install them
   *  on a fake and fire the events in the browser's order). `freeze` → frozeAt; `resume` → resumed(); `visibilitychange`
   *  to visible → the foreground watchdog pass, exactly as before. */
  watchLifecycle(doc: { addEventListener(type: string, listener: () => void): void; readonly visibilityState: string }): void {
    doc.addEventListener("freeze", () => { this.frozeAt = Date.now(); });
    doc.addEventListener("resume", () => this.resumed(Date.now()));
    doc.addEventListener("visibilitychange", () => { if (doc.visibilityState === "visible") this.watchdog(Date.now(), true); });
  }

  /** The Page Lifecycle `resume` event (the user 2026-09-07, whose dashboard froze every time they came
   *  back to its tab): stamp every OPEN relay socket's lastRecv to now. A Chromium tab left in the
   *  background is FROZEN — no JS runs at all — so no frame could stamp lastRecv even though the socket
   *  stayed open and the kernel kept heartbeating. lastRecv therefore measured "JS did not run", not
   *  "the socket went silent", and the foreground pass (watchdog(now, true), fired by visibilitychange
   *  right after the thaw) read the frozen stretch as 30s+ of silence and abandoned+redialed EVERY
   *  attached host on EVERY return — each redial a full resend from that kernel. Chromium fires
   *  `resume` before `visibilitychange`, so the stamp lands first and socketVerdict (unchanged) sees a
   *  fresh socket and keeps it; the frames queued during the freeze then dispatch on the same socket.
   *  This re-BASES the measurement, it does not disarm it: a socket that stays silent after the thaw is
   *  still put down by the regular tick, and sooner than REMOTE_STALE_MS (review find, 2026-09-08): an
   *  OPEN readyState says only that the browser has seen no FIN, and a peer that died while the tab was
   *  frozen leaves the socket looking exactly like a healthy one, so the stamp is PROVISIONAL
   *  (resumeProvisional: the watchdog runs at REMOTE_PROVISIONAL_MS until a frame confirms it), and a
   *  socket already overdue BEFORE the freeze is not stamped at all: its silence began while JS was
   *  running, so that gap is real and the foreground pass redials it as it did before the stamp
   *  existed. Only readyState 1 is stamped — a socket
   *  still CONNECTING is a handshake the frozen tab never finished, and the foreground pass still kills
   *  it. Where no `resume` fires (Firefox, Safari, a hidden-but-running tab) stale lastRecv IS real
   *  silence, and today's instant abandon on foreground is unchanged. */
  resumed(now: number): void {
    for (const c of this.conns.values()) {
      if (!c.ws || c.ws.readyState !== 1) continue;
      if (this.frozeAt && this.frozeAt - (c.lastRecv || c.connT) > REMOTE_STALE_MS) continue;   // overdue before the freeze: real silence, no stamp
      c.lastRecv = now;
      c.resumeProvisional = now;
    }
  }

  /** One pass of the remote-socket watchdog (public so the tests can tick it with their own clock):
   *  apply socketVerdict to every dialed connection. `foreground` (the visibility fast-path) also
   *  kills a socket still CONNECTING, whatever its age — a handshake the sleeping tab never finished. */
  watchdog(now: number, foreground = false): void {
    for (const c of this.conns.values()) {
      if (c.closed || !c.ws) continue;
      const rs = c.ws.readyState;
      let v = socketVerdict(rs, c.lastRecv, c.connT, now, c.resumeProvisional ? REMOTE_PROVISIONAL_MS : REMOTE_STALE_MS);
      if (foreground && rs === 0) v = "close";
      if (v === "close") {
        // the same breadcrumb family as open/close/detach, so a "cards came back late" report reads
        // WHICH socket the watchdog put down and how long it had been silent
        this.diag("hostconn", { host: c.host, ev: "watchdog-close", why: rs === 1 ? "quiet" : "connecting",
                                quietMs: c.lastRecv ? now - c.lastRecv : -1, foreground });
        // ABANDON it, don't wait for it: close() on a socket whose far side is gone starts a closing
        // handshake nobody answers, and the browser holds CLOSING for ~60s before onclose fires (the
        // audited panes came back 64s after their own watchdog-close for exactly this reason; the
        // served-page harness reproduced it against a silent far end). Detach its handlers — its
        // eventual onclose is nobody's event, and must not redial a second time — close it for
        // hygiene, and dial a fresh socket NOW. connect() gates on `live` and on `closed` as always.
        const dead = c.ws;
        dead.onopen = dead.onmessage = dead.onclose = dead.onerror = null;
        try { dead.close(); } catch (e) { /* already dying */ }
        c.ws = null;
        this.dialEvent(c.host, false);   // its onclose is detached above, so the attempt's end is said HERE (the swirl must not spin on a dead dial)
        this.connect(c);   // settings queued on the conn meanwhile ride the fresh socket's open (flushPending)
      } else if (v === "redial") {
        this.connect(c);
      }
    }
  }

  // kernel → browser: prefix this host's ids, merge tab orders, hand the rest to the panes.
  inbound(host: string, msg: any): void {
    // timed as fed:<wire type>: the prefixing, delta application and merge this layer does on a frame before
    // the pane's own handler runs (that handler is timed under <type>, nested inside; the collector records
    // each level's own time, so the two add up to the frame's cost). No collector: the plain path.
    const p = this.perf;
    if (!p) { this.inboundNow(host, msg); return; }
    p.timed("fed:" + classifyFrame(msg), () => this.inboundNow(host, msg));
  }

  private inboundNow(host: string, msg: any): void {
    const m = prefixInbound(host, msg);
    if (m && m.type === "session" && typeof m.id === "string") {
      (this.perHostSids[host] ||= new Set()).add(m.id);
    }
    // a kernel's `caps` frame describes THAT kernel; the panes hold only the LOCAL kernel's (its views
    // store is the one they write). A remote's would read as the local kernel's — dropped here.
    if (m && m.type === "caps" && host !== LOCAL) return;
    // The local kernel's caps frame is the reconnect event: each replayed views store adopts the blob its
    // gate last turned away when the frame names it (the 2026-09-05 review; capsAdopts),
    // as the panes do — the kernel sends its connect push before this frame and `viewsSeq` is the seq of
    // the views blob that push served, so a push a restarted kernel served under an OLDER seq (a store
    // restored while it was down) was rejected a frame ago and is adopted here; a healthy reconnect's push
    // was adopted, nothing is kept, and the stores stand; a pusher frame kept because it arrived between
    // the push and this frame carries a seq the frame does not name and is discarded. A store that adopted
    // RE-EMITS before the caps frame is handed on: the panes see the local blob only through these re-emits
    // (a rejected push reached them wearing the stored blob), so the restored blob must meet their own gate
    // — and be turned away there — before their caps door adopts it. Nothing is re-emitted otherwise. When a
    // store kept nothing the frame names (the connect push carried no blob for it — a sentinel cycle sends no
    // tabOrder), the frame's viewsSeq is remembered as the kernel's announced store for that store, and the
    // later blob carrying exactly that seq is adopted below the stored one on arrival (the review;
    // announcedSeq): one slot per store, overwritten by each local caps frame, cleared by the next adoption
    // that CHANGES the stored blob and never by a re-arrival of the blob already stored (announcedAfter);
    // null (no store at all) and a missing field announce nothing. The panes hold the same slot from the same
    // frame, handed on below; the merged re-emits between that frame and the pusher's next one (a remote host's
    // push, a `closed` frame, a storage event, a host drop) hand them the STORED blob at their own held seq,
    // which leaves their slot standing by the same rule, so the re-emit of that later adoption meets an open
    // door there too.
    if (m && m.type === "caps") {
      if (capsAdopts(this.localViewsRejected, m.viewsSeq)) {
        this.localViews = this.localViewsRejected; this.localViewsAnnounced = null;
        this.emitMergedOrder();
      } else this.localViewsAnnounced = announcedSeq(m.viewsSeq);
      this.localViewsRejected = null;
      const tl = this.perHostTl[LOCAL];
      if (tl && capsAdopts(this.tlViewsRejected, m.viewsSeq)) {
        this.perHostTl[LOCAL] = { ...tl, views: this.tlViewsRejected }; this.tlViewsAnnounced = null;
        this.emitMergedTimeline(false);
      } else this.tlViewsAnnounced = announcedSeq(m.viewsSeq);
      this.tlViewsRejected = null;
    }
    // A kernel's `closed` frame is ITS OWN report that the session is gone — the one other writer allowed
    // to touch the per-host store (T233, the user 2026-09-03). The 2026-08-02 rule below forbids
    // ARRANGEMENT writes on a re-emit (a stale pane pruning another pane's drag); a `closed` frame is new
    // information about EXISTENCE from the owning host, exactly the evidence class absorbHostReport acts
    // on. Without this, every synthetic re-emit between the kill and that host's next tabOrder push (a
    // view-order storage event, a host attach or drop) re-served the stored slice WITH the dead id, and
    // past the chat's 15s close backstop that read as a refused close — the false "Couldn't close X"
    // toast. Fold it out of the slices, hand the pane its teardown frame, then re-emit a merged order
    // without it: that re-emit CONFIRMS the close (absence) but, flagged synthetic, can never toast.
    if (m && m.type === "closed" && typeof m.id === "string") {
      const gone = m.id;
      if (this.perHostOrder[host]) this.perHostOrder[host] = this.perHostOrder[host].filter((x) => x !== gone);
      if (this.perHostTabs[host]) this.perHostTabs[host] = this.perHostTabs[host].filter((t: any) => !(t && t.id === gone));
      if (this.perHostLive[host]) this.perHostLive[host] = this.perHostLive[host].filter((x) => x !== gone);
      if (this.perHostSkeleton[host]) this.perHostSkeleton[host] = this.perHostSkeleton[host].filter((x) => x !== gone);
      this.perHostSids[host]?.delete(gone);
      window.dispatchEvent(new MessageEvent("message", { data: m }));
      this.emitMergedOrder();
      return;
    }
    if (m && m.type === "tabOrder") {
      const prevOrder = this.perHostOrder[host] || [];
      const prevTabs = this.perHostTabs[host] || [];
      this.perHostOrder[host] = Array.isArray(m.order) ? m.order.filter((x: any) => typeof x === "string") : [];
      this.perHostTabs[host] = Array.isArray(m.tabs) ? m.tabs : [];
      this.perHostLive[host] = Array.isArray(m.live) ? m.live.filter((x: any) => typeof x === "string") : [];
      // the skeleton slice: array → replace; absent → keep, pruned to the order this frame just set
      // (a sid that left the strip left the set with it; see the store's comment for why absent ≠ none)
      if (Array.isArray(m.skeleton)) {
        this.perHostSkeleton[host] = m.skeleton.filter((x: any) => typeof x === "string");
      } else if (this.perHostSkeleton[host]) {
        const listed = new Set(this.perHostOrder[host]);
        this.perHostSkeleton[host] = this.perHostSkeleton[host].filter((x) => listed.has(x));
      }
      // session VIEWS (the user 2026-08-18): the blob is the LOCAL kernel's viewer pref (ids arrive
      // host-prefixed inside it already) — remote kernels' copies are their own dashboards' prefs.
      // Without this passthrough the merged re-emit silently dropped the field and the browser
      // dashboard's chat never learned the views at all. Kept ONLY when its write sequence is at
      // least the stored one (2026-09-05): the re-emit below replays this copy on every merged order,
      // and a frame the kernel built before a write must not roll the replayed blob back behind an
      // ack the pane already adopted. The last blob turned away is kept for the caps frame (above), and a
      // blob at the seq the last caps frame announced is adopted below the stored one.
      if (host === LOCAL && m.views && typeof m.views === "object") {
        if (adoptViews(this.localViews, m.views, this.localViewsAnnounced)) { this.localViewsAnnounced = announcedAfter(this.localViews, m.views, this.localViewsAnnounced); this.localViews = m.views; this.localViewsRejected = null; }
        else this.localViewsRejected = m.views;
      }
      // the LOCAL kernel's own name rides its tabOrder frame too (selfHost): the chat reads a postal card's
      // sender host against it, and a remote kernel's frame names ITSELF, so only the local one is kept
      if (host === LOCAL && typeof m.selfHost === "string" && m.selfHost) this.localSelfHost = m.selfHost;
      this.ensureHost(host);
      this.absorbHostReport(host, prevOrder, prevTabs);   // a host just reported its sessions → the one
      this.emitMergedOrder(true, host);                   //   moment the stored arrangement may be touched
      return;
    }
    if (m && m.type === "feed") {
      this.perHostFeed[host] = m;
      this.perHostFeedAt[host] = Date.now();   // the wire arrival: the one moment the frame's `now` was current
      this.ensureHost(host);
      this.emitMergedFeed();
      return;
    }
    // timeline snapshots replace the panel's state wholesale (update/applyBars) — merge per host like the feed.
    if (m && m.type === "data" && m.data && typeof m.data === "object") {
      // the LOCAL lanes payload carries the views blob the merged re-emit replays: a payload whose blob
      // has a LOWER write sequence than the stored one keeps the stored blob (its lanes still land) —
      // the same rule the tabOrder store applies above (2026-09-05), the same keep of the last blob
      // turned away, for the caps frame, the same door for the blob at the announced seq, and the same
      // slot rule on adoption (a re-arrival of the stored blob leaves the slot)
      const held = host === LOCAL ? this.perHostTl[LOCAL] : null;
      if (held && held.views && m.data.views && !adoptViews(held.views, m.data.views, this.tlViewsAnnounced)) {
        this.perHostTl[host] = { ...m.data, views: held.views }; this.tlViewsRejected = m.data.views;
      } else {
        this.perHostTl[host] = m.data;
        if (host === LOCAL && m.data.views) { this.tlViewsRejected = null; this.tlViewsAnnounced = announcedAfter(held && held.views, m.data.views, this.tlViewsAnnounced); }
      }
      this.ensureHost(host);
      this.emitMergedTimeline(false);
      return;
    }
    if (m && m.type === "bars") {
      this.perHostTlBars[host] = m;
      this.ensureHost(host);
      this.emitMergedTimeline(true);
      return;
    }
    window.dispatchEvent(new MessageEvent("message", { data: m }));
  }

  // The viewer's own session order, re-read per emit. It is a handful of strings out of localStorage and
  // it must never be cached: another PANE of this dashboard writes the same key when you drag a tab there,
  // and the storage event below re-emits — reading fresh is what makes all three surfaces agree.
  private view(): string[] {
    return readViewOrder();
  }

  private lastFeedCounts = "";   // last per-host ask-count signature — breadcrumb only on change

  private emitMergedFeed(): void {
    // MERGE-INPUT TRIPWIRE (the user 2026-07-31): one breadcrumb whenever any host's contribution to
    // the merged feed CHANGES SIZE — so a card blinking out is attributable to the host snapshot that
    // shrank (a kernel push without it / a detach) vs. the render layer (the feed pane's own tripwire).
    const counts: Record<string, number> = {};
    for (const h of this.hostSeq) {
      const f = this.perHostFeed[h];
      if (f) counts[h || "local"] = Array.isArray(f.asks) ? f.asks.length : -1;   // -1 = a feed msg with NO asks array
    }
    const sig = JSON.stringify(counts);
    if (sig !== this.lastFeedCounts) {
      this.lastFeedCounts = sig;
      this.diag("feedmerge", { counts });
    }
    this.publishPending();
    const dead = this.deadHosts();
    this.emit(mergeHostFeeds(this.perHostFeed, this.hostSeq, this.view(), dead, this.perHostFeedAt));
  }

  // the hosts whose link is DOWN right now (this manager knows its sockets) — the merges' pendingDead input
  private deadHosts(): string[] {
    return this.hostSeq.filter((h) => {
      if (h === LOCAL) return false;
      const c = this.conns.get(h);
      return !c || !c.ws || c.ws.readyState === 3;   // no socket / closed = a dead link right now
    });
  }

  private lastPendingSig = "";

  /** Which attached hosts THIS pane is still waiting on, by the channel it renders: the chat reads the
   *  tab list, the feed and fleet the feed payload, the timeline the lanes skeleton. */
  private pendingFor(): string[] {
    const src = this.app === "timeline" ? this.perHostTl : this.app === "chat" ? this.perHostOrder : this.perHostFeed;
    return this.hostSeq.filter((h) => h !== LOCAL && !(h in src));
  }

  // Tell the SHELL which hosts this pane has not heard from yet (the user 2026-09-02): the network
  // panel reads the kernel's /tunnels, where a host whose tunnel is fine says "connected" — while the
  // board behind it shows no trace of that host. The shell folds every pane's list and the panel row
  // says "connected · loading sessions…" until this pane's first payload from that host retires it.
  // Posted on CHANGE only, and only to a same-origin parent (a cross-origin host has no network panel).
  private publishPending(): void {
    const hosts = this.pendingFor();
    const sig = hosts.join("\u0000");
    if (sig === this.lastPendingSig) return;
    this.lastPendingSig = sig;
    try {
      if (window.parent && window.parent !== window) window.parent.postMessage({ romp: "hostsPending", app: this.app, hosts }, "*");
    } catch (e) { /* a cross-origin parent throws on access — nothing there to tell */ }
  }

  private emitMergedTimeline(bars: boolean): void {
    this.publishPending();   // before the holds: a pane still waiting on its LOCAL lanes is waiting on the remotes too
    // HOLD until the LOCAL lanes snapshot exists. The merges take `now` (the clock authority) from the
    // local payload, so a remote host winning the connect race would emit now:undefined — which the
    // panel's fitWindow turned into a permanently-NaN window (every bar/axis x = NaN; the "stub lane
    // lines, no bars" bug, 2026-07-15). The local kernel pushes on connect, so the hold is momentary,
    // and the local arrival itself emits (event-based, no timer).
    if (!(LOCAL in this.perHostTl)) return;
    // The BARS emission holds for the LOCAL bars snapshot too (2026-08-17, the after-attach "most of
    // my sessions vanished" report): at page boot with hosts already attached, a remote's bars can
    // land before the local kernel's — and the panel's applyBars REPLACES turns wholesale, so the
    // merged-without-local emission blanked every local lane until the next local push. Same
    // discipline as the lanes hold above: the local kernel pushes bars on connect, so the hold is
    // momentary, and the local arrival itself emits.
    if (bars && !(LOCAL in this.perHostTlBars)) return;
    const data = bars
      // the bars message carries no lanes — hand the merged lane list in for the connector stitch
      ? mergeHostBars(this.perHostTlBars, this.hostSeq, mergeHostTimelines(this.perHostTl, this.hostSeq, this.view()).sessions)
      : { type: "data", data: mergeHostTimelines(this.perHostTl, this.hostSeq, this.view(), this.deadHosts()) };
    this.emit(data);
  }

  // Every caller re-emits WITHOUT touching the stored arrangement — a drag landing here through the
  // storage / CustomEvent path must never be answered by a rewrite (the 2026-08-02 revert bug: a pane
  // holding a stale session list pruned the very tab another pane had just moved). Only an inbound
  // tabOrder push mutates the store, in absorbHostReport below, because only a host's own report is
  // evidence about what exists.
  private emitMergedOrder(fresh = false, freshHost: string = LOCAL): void {
    const order = mergeHostOrder(this.perHostOrder, this.hostSeq, this.view());
    const tabs = this.hostSeq.flatMap((h) => this.perHostTabs[h] || []);
    const live = this.hostSeq.flatMap((h) => this.perHostLive[h] || []);   // T258: the union the pane's omission guard reads
    this.publishPending();
    // `skeleton` rides EVERY merged strip, an array even when empty: the pane's rule is "array → replace,
    // absent → keep", and this frame is the union of every host's slice — the authority the pane must
    // replace from. Leaving the key off an empty union would tell the pane "no news" and let a set the
    // kernel has since emptied (a `closed`, a release) linger as skeleton chips over loaded sessions.
    const data: any = { type: "tabOrder", order, tabs, live, views: this.localViews ?? undefined, selfHost: this.localSelfHost || undefined };
    data.skeleton = this.hostSeq.flatMap((h) => this.perHostSkeleton[h] || []);
    // Provenance for the chat's close backstop (T233): a FRESH emission is driven by one host's own
    // tabOrder push and names that host (`freshHost`) — only ITS ids are that kernel's current word; the
    // other hosts' slices ride along from the store. A SYNTHETIC re-emit (a view-order storage event, a
    // host attach or drop, a `closed` fold) is re-served entirely from the store and says `reemit` — it
    // confirms a close by absence like any order, but is never evidence that a kernel still has a tab.
    if (fresh) data.freshHost = freshHost;
    else data.reemit = true;
    this.emit(data);
  }

  // Fold a host's OWN report — the one moment with fresh evidence about what exists — into the stored
  // arrangement, in three steps, one conditional write:
  // 1. HEAL fsid churn: a /clear or relaunch mints a new transcript fsid for the SAME logical session,
  //    and the kernel's own list inherits the old slot by the stable session NAME (`_ordered`, the
  //    2026-06-29 fix). The arrangement inherits the same way (churnSwaps matches vanished→appeared ids
  //    by display name within this host's report), or the relaunched session would read as brand-new.
  // 2. PRUNE ids the reporting hosts no longer list (the old gcView rule, unchanged): event-based, never
  //    aged out. A detached / unreachable host reports nothing and its ids are left entirely alone —
  //    pruning against a tunnel blip would flatten every remote session's placement and stack them all
  //    at the end of the strip when the host came back.
  // 3. ADOPT arrivals: every id the viewer has never placed appends at the very END of the arrangement,
  //    so a NEW session lands at the end of the whole strip — exactly where its provisional tab already
  //    rendered — not at the end of its host's block, mid-strip in front of another host's sessions
  //    (the user 2026-08-10, who watched the new tab pop from last place to second-to-last). Writing the
  //    placement down is what makes it hold: an unadopted id was re-derived from the host-blocked seed
  //    on every merge and every reload.
  private absorbHostReport(host: string, prevOrder: readonly string[], prevTabs: readonly any[]): void {
    const names = (tabs: readonly any[]) => {
      const byId = new Map<string, string>();
      for (const t of tabs) if (t && typeof t.id === "string") byId.set(t.id, String(t.name || ""));
      return byId;
    };
    const cur = this.view();
    const healed = healOrder(cur, churnSwaps(prevOrder, names(prevTabs),
                                             this.perHostOrder[host] || [], names(this.perHostTabs[host] || [])));
    const reporting = new Set(Object.keys(this.perHostOrder));
    const live = new Set<string>();
    for (const h of reporting) for (const id of this.perHostOrder[h] || []) live.add(id);
    const seed: string[] = [];
    for (const h of this.hostSeq) seed.push(...(this.perHostOrder[h] || []));
    const next = adoptArrivals(pruneViewOrder(healed, hostOf, reporting, live), seed);
    if (next.length !== cur.length || next.some((id, i) => id !== cur[i])) writeViewOrder(next);
  }

  private lastClearHosts: string[] = [LOCAL]; // where the most recent clear routed (one kernel for a card or a
  //                                             session's batch, every attached kernel for the board-wide Clear
  //                                             all, T286) — undoClear follows it to each of them

  // browser → kernel: route each message to the owning kernel, prefix stripped.
  outbound(m: any): void {
    // undoClear undoes the LAST clear on every kernel that took it: a remote card's clear went to that kernel
    // alone, the board-wide Clear all went to all of them. (Each kernel keeps its own cleared.jsonl; only the
    // kernel that took a clear can undo it, and it undoes its own newest batch.)
    if (m && m.type === "undoClear") {
      const hosts = this.lastClearHosts.length ? this.lastClearHosts : [LOCAL];
      this.lastClearHosts = [LOCAL];
      for (const h of hosts) this.sendTo(h, m);
      return;
    }
    const routes = routeOutbound(m, new Set(this.hostSeq.filter((h) => h !== LOCAL)));
    if (m && (m.type === "askClear" || m.type === "askClearMany" || m.type === "clearAll")) {
      this.lastClearHosts = routes.length ? routes.map((r) => r.host) : [LOCAL];
    }
    for (const r of routes) this.sendTo(r.host, r.msg);
  }

  /** One send to one kernel: the local one through the page's own socket, a remote one through its conn. */
  private sendTo(host: string, msg: any): void {
    if (host === LOCAL) {
      const s = (window as any).__rompLocalSend;
      if (typeof s === "function") s(msg);
    } else {
      this.sendRemote(host, msg);
    }
  }

  // The ONE remote send path (the user 2026-08-28, whose mid-restart Edit consent never reached the
  // file's kernel: during a kernel restart every socket churns for a few seconds, and a send in that
  // window used to drop with only a toast). The liveness watchdog opens the same window on purpose —
  // it abandons a quiet socket and dials a fresh one, CONNECTING for a moment — so this path covers a
  // redial from any cause. When the socket isn't OPEN:
  // - a KERNEL_SETTING queues on the conn, LATEST per type — settings are latest-wins by nature, so
  //   the reconnect must never replay a stale older value over the one the user chose last — and
  //   flushes on the socket's open event, before any post-reconnect traffic (flushPending).
  //   "Latest per type" is latest per TAB only: another dashboard may pick again while this one is
  //   frozen, so every KERNEL_SETTING carries `gt` (epoch ms minted at the user's gesture, where
  //   the message is built) and the KERNEL orders applies by it, standing stale flushes down. The
  //   queue's contract here is to deliver the message UNCHANGED — never re-stamp at send or flush
  //   time, which would forge freshness onto an hours-old pick. Queueing is journaled (`sendqueue`),
  //   so a later disagreement between machines is attributable to this tab holding the pick while
  //   the host was down, and to the older pick of the same type it replaced;
  // - the pane's own BOOKKEEPING (the BOOKKEEPING list, above) holds the same way, latest per key, and
  //   never toasts: the user sent nothing for a toast to be about (2026-09-10);
  // - anything else — a GESTURE, or an unknown type — keeps its behavior (replaying an arbitrary action
  //   minutes later can be worse than dropping it — a deliberate non-goal) but the drop lands a
  //   client-diag breadcrumb naming the type and host, beside the existing warn toast: a drop is never
  //   silent.
  private sendRemote(host: string, msg: any): void {
    const c = this.conns.get(host);
    if (c && c.ws && c.ws.readyState === 1) {
      c.ws.send(JSON.stringify(msg));
      return;
    }
    if (c && msg && typeof msg.type === "string" && KERNEL_SETTING.has(msg.type)) {
      // not dropped — it rides the next open, so no toast; but never silent either: the breadcrumb
      // names the host, the type, the gesture's own stamp, the socket's state at queue time (rs; -1
      // = not dialed) and, when it replaced an older queued pick of the same type, that pick's stamp
      // (superseded). The kernel stamps the journal line's time — nothing is minted here.
      const prev = c.pending.get(msg.type);
      c.pending.set(msg.type, msg);
      this.diag("sendqueue", { host, msgType: msg.type, gt: typeof msg.gt === "number" ? msg.gt : 0,
                               rs: c.ws ? c.ws.readyState : -1,
                               ...(prev ? { superseded: typeof prev.gt === "number" ? prev.gt : true } : {}) });
      return;
    }
    const key = bookkeepingKey(msg);
    if (key !== null) {
      // the pane's own bookkeeping (BOOKKEEPING): held for the open, never toasted — the user sent
      // nothing for a toast to be about. A host this page holds no conn for (known from a frame, never
      // dialed, or detached since) has nothing to hold it on: dropped with the breadcrumb alone. Journaled
      // once per KEY, at the not-held → held transition, in the hostconn family: a hover held per pointer
      // move would otherwise write a row per move; the open row names everything that flushed.
      if (!c) { this.diag("senddrop", { host, msgType: msg.type, why: "no-conn" }); return; }
      if (!c.pending.has(key)) this.diag("hostconn", { host, ev: "hold", msgType: msg.type, rs: c.ws ? c.ws.readyState : -1 });
      c.pending.set(key, msg);
      return;
    }
    this.diag("senddrop", { host, msgType: (msg && msg.type) || "" });
    this.dropWarn(host, msg);
  }

  /** Deliver the settings that arrived while this host's socket was down, on the open event itself —
   *  synchronously, so they precede ANY post-reconnect traffic. The file viewer's consent flow relies
   *  on exactly that: its setFileEditing broadcast must land before the save that follows the yes
   *  (the same-socket ordering the save route's gate assumes), now across a down-socket window too.
   *  Each entry clears AS IT IS DELIVERED: a send that throws mid-flush leaves only the undelivered
   *  ones for the next open. A whole-map clear after the loop would replay the delivered ones too,
   *  and a replay is one gesture delivered twice (a kernel without gesture ordering applies it twice;
   *  a current one logs an echo or stand-down line per copy). The halt is journaled in the hostconn
   *  family (`flush-halt`, naming what went and what is held) because a partial flush is never
   *  silent; the catch swallows on purpose so the open handler still stamps lastRecv and dispatches
   *  romp:hostRelayUp, and the dead socket's own onclose redials. Returns the flushed types for the
   *  open breadcrumb. */
  private flushPending(conn: Conn): string[] {
    if (!conn.pending.size || !conn.ws || conn.ws.readyState !== 1) return [];
    const flushed: string[] = [];
    for (const [k, m] of conn.pending) {   // deleting the current entry mid-iteration is spec-safe on a Map
      try {
        conn.ws.send(JSON.stringify(m));
      } catch (e) {
        this.diag("hostconn", { host: conn.host, ev: "flush-halt", flushed: [...flushed], held: pendingTypes(conn) });
        break;
      }
      conn.pending.delete(k);
      flushed.push(m && typeof m.type === "string" ? m.type : k);   // by TYPE: a bookkeeping key carries the sid it is per
    }
    return flushed;
  }

  // A GESTURE routed to a host whose socket isn't open would otherwise VANISH — creating a session on an
  // unreachable remote gave no feedback at all (the user 2026-07-10). Surface the drop as a local
  // `warn` (render.ts toasts it), naming the host and the action so the user knows what didn't land.
  // Only for a gesture: the pane's own bookkeeping is held instead (BOOKKEEPING), since a toast about a
  // message the user never sent reads as a failure of the tap they did make (2026-09-10).
  private dropWarn(host: string, msg: any): void {
    window.dispatchEvent(new MessageEvent("message", { data: { type: "warn",
      text: `${host} is unreachable (its kernel isn't answering) — “${(msg && msg.type) || "action"}” was not delivered` } }));
  }

  private ensureHost(h: string): void {
    if (!this.hostSeq.includes(h)) this.hostSeq.push(h);
  }

  private async poll(): Promise<void> {
    let tunnels: any[] = [];
    try {
      const r = await fetch("/tunnels", { cache: "no-store" });
      tunnels = (await r.json()).tunnels || [];
    } catch (e) {
      return;
    }
    // `hasToken`, never the token: the kernel publishes whether a remote's credential EXISTS, and the
    // relay it dials through (/remote/<host>/ws, _remote_ws) injects that credential itself — so the
    // page never holds a remote's reusable secret (2026-09-08). A row without one has no admin path
    // to that kernel and is not dialed.
    const want = new Map<string, any>(tunnels.filter((t) => t.hasToken && t.localPort).map((t) => [t.host, t]));
    let opened = false;
    for (const [host, t] of want) if (!this.conns.has(host)) { this.openRemote(host, t.status === "up"); opened = true; }
    for (const host of [...this.conns.keys()]) if (!want.has(host)) this.closeRemote(host);
    // A host just ATTACHED is pending from this moment, not from the next push that happens to land:
    // re-emit the merged payloads so the placeholders appear at the attach event. Each emission holds
    // until the LOCAL payload exists (the feed's hold is here; the timeline's is its own), so a page
    // still booting never gets an empty merged feed dropped onto its loader.
    if (opened) {
      if (LOCAL in this.perHostFeed) this.emitMergedFeed();
      this.emitMergedTimeline(false);
      this.publishPending();
    }
    // The kernel's tunnel state gates dialing: it health-checks its own ssh tunnels, so "up" is
    // authoritative for whether anything listens on the local port at all. Blind 2s retries against
    // a dead tunnel port feed the browser's per-host WebSocket failure backoff (Firefox delays
    // re-admission after failures), which then holds the LOCAL panes' reconnects hostage after a
    // kernel restart — the stuck "Disconnected — reconnecting…" banner. A down tunnel is not
    // dialed at all; this poll re-dials within one cycle of the kernel reporting it up.
    for (const [host, t] of want) {
      const c = this.conns.get(host);
      if (!c) continue;
      c.live = t.status === "up";
      if (c.live && (!c.ws || c.ws.readyState === 3)) this.connect(c);
    }
    // Publish reachability for the panes. The kernel's own tunnel health is the authority (it dials and
    // health-checks the ssh), and it keeps retrying, so this flips back on its own when the host returns.
    const down = new Set([...want.keys()].filter((h) => want.get(h).status !== "up"));
    // lastOk comes from the KERNEL's row (when it last had that host answering end to end), so the
    // "last seen" a pane shows survives a page reload and doesn't restart with the browser.
    for (const [host, t] of want) if (typeof t.lastOk === "number" && t.lastOk) this.lastSeen[host] = t.lastOk;
    const changed = down.size !== this.downHosts.size || [...down].some((h) => !this.downHosts.has(h));
    // A host coming BACK is the recovery event failed previews wait for. The message-driven heal
    // (render.ts's listener re-runs retryFailedPreviews on any kernel message) never ticks on an
    // idle session — no traffic flows — so a relay-failed figure sat as a chip until the user's
    // next send generated pushes (the user 2026-08-17). This poll is the authority on tunnel
    // state; the down→up transition is the exact moment the relay works again, so it dispatches
    // through the same message path and the heal fires with zero chat traffic.
    const recovered = [...this.downHosts].filter((h) => want.has(h) && !down.has(h));
    // …and the kernel's recovery counter (T291b): a bump while the row reads "up" is a link that answered again
    // after missing, which the status alone never showed; one hostUp per bump, never one per poll or per push
    for (const [host, t] of want) {
      const seq = Number(t.upSeq) || 0, prev = this.upSeq.get(host);
      this.upSeq.set(host, seq);
      if (prev !== undefined && seq !== prev && !down.has(host) && !recovered.includes(host)) recovered.push(host);
    }
    for (const host of [...this.upSeq.keys()]) if (!want.has(host)) this.upSeq.delete(host);
    if (recovered.length) window.dispatchEvent(new MessageEvent("message", { data: { type: "hostUp", hosts: recovered } }));
    this.downHosts = down;
    if (changed) window.dispatchEvent(new Event("romp-hosts"));   // panes repaint their disconnected marks
    // …and whether the kernel is TRYING right now (the row's `dialing`, kernel.py _row_dialing): the host-down
    // notice's swirl spins on it. Published on a change only, through the same event the relay socket's own
    // dial transitions use, so one listener sees every reason the state can move
    const dialing = new Set([...want.keys()].filter((h) => want.get(h).dialing === true));
    for (const h of new Set([...dialing, ...this.dialingHosts])) if (dialing.has(h) !== this.dialingHosts.has(h)) this.dialEvent(h, dialing.has(h));
    this.dialingHosts = dialing;
  }

  private openRemote(host: string, live: boolean): void {
    // Dial the remote through THIS kernel's /remote/<host>/ws relay, on the same origin that served
    // the page — never at 127.0.0.1:<forwarded port>, which only exists on the kernel's machine:
    // from a phone reading the dashboard over `tailscale serve`, that address is the phone itself,
    // and every remote host silently vanished with no disconnected mark (the user 2026-07-30).
    // Same-origin also means the local auth cookie rides the upgrade; the remote kernel's own
    // credential is added by the relay (_remote_ws), so this URL carries no token at all.
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    // …carrying this dashboard's `wid`, exactly as the pane's own local socket does. Without it a remote
    // kernel sees every federated viewer as one anonymous client and BROADCASTS its per-viewer messages,
    // so one dashboard's jump to a remote session yanked every other open dashboard to that tab — the
    // very cross-window yank the local path fixed (the user 2026-07-29).
    const w = dashboardWid();
    const url = `${proto}${location.host}/remote/${encodeURIComponent(host)}/ws?app=${encodeURIComponent(this.app)}`
      + (w ? `&wid=${encodeURIComponent(w)}` : "");
    const conn: Conn = { host, ws: null, url, closed: false, live, lastRecv: 0, resumeProvisional: 0, connT: 0, pending: new Map() };
    this.conns.set(host, conn);
    this.ensureHost(host);
    this.connect(conn);
  }

  // HOST-CONNECTION TRIPWIRE (the user 2026-07-31, remote cards blinking in and out): every remote
  // socket open/close and every detach lands one breadcrumb in the same client-diag journal the
  // feed's tripwires write, so a blink is attributed to the connection layer (a drop, a /tunnels
  // flap) or ruled out of it — instead of re-guessed from pixels. Rides the LOCAL kernel socket.
  private diag(what: string, data: any): void {
    const s = (window as any).__rompLocalSend;
    if (typeof s === "function") s({ type: "clientDiag", surface: "federation", what, data });
  }

  private connect(conn: Conn): void {
    if (conn.closed || !conn.live) return;
    if (conn.ws && (conn.ws.readyState === 0 || conn.ws.readyState === 1)) return; // already connecting/open
    let ws: WebSocket;
    conn.connT = Date.now();
    conn.lastRecv = 0;
    conn.resumeProvisional = 0;   // a fresh socket starts unmarked: the provisional rule was the resumed socket's
    try {
      ws = new WebSocket(conn.url);
    } catch (e) {
      setTimeout(() => this.connect(conn), 2000);
      return;
    }
    conn.ws = ws;
    this.dialEvent(conn.host, true);   // a dial attempt is in flight: the host-down notice's swirl spins
    ws.onopen = () => {
      this.dialEvent(conn.host, false);
      // settings queued while the socket was down go out FIRST — on the open event itself, never a
      // timer — so nothing sent after the reconnect can overtake them (see flushPending). That is
      // also why the relay-up dispatch below comes AFTER the flush: the chat's upload re-ship rides
      // that event, and a re-shipped dropFile must not get ahead of a queued setting on this socket.
      const flushed = this.flushPending(conn);
      this.diag("hostconn", flushed.length ? { host: conn.host, ev: "open", flushed }
                                           : { host: conn.host, ev: "open" });
      conn.lastRecv = Date.now();   // the watchdog measures this socket's silence from ITS open
      // this host's owed replies just became reachable again — the chat re-ships its pending
      // uploads on exactly this event (T215 review finding 2026-09-01: a remote kernel's restart
      // redials HERE, firing neither romp:wsup nor hostUp, so nothing else could heal them).
      // Fired on every open, not just re-opens: at boot the listener's map is empty (a no-op),
      // and a detach/re-add mints a fresh Conn whose FIRST open is that heal event.
      try {
        window.dispatchEvent(new CustomEvent("romp:hostRelayUp", { detail: { host: conn.host } }));
      } catch (e) { /* dispatch must never break the relay */ }
    };
    ws.onmessage = (ev: MessageEvent) => {
      conn.lastRecv = Date.now();   // every frame counts, the keepalive included — that is the heartbeat
      conn.resumeProvisional = 0;   // and any frame, the keepalive included, confirms a resumed keep
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg && msg.type === "ka") return;
      this.inbound(conn.host, msg);
    };
    ws.onclose = (ev: CloseEvent) => {
      this.dialEvent(conn.host, false);   // the attempt ended (refused, or the socket dropped): still until the redial
      this.diag("hostconn", { host: conn.host, ev: "close", code: ev.code, clean: ev.wasClean, detached: conn.closed });
      if (!conn.closed) setTimeout(() => this.connect(conn), 2000); // reconnect a dropped remote
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (e) {}
    };
  }

  /** One host's dial state changed: its relay socket's dial began (CONNECTING) or ended (open, closed, or
   *  abandoned by the watchdog), or the kernel's /tunnels poll reported its `dialing` flipping. The chat's
   *  host-down notice repaints its swirl on this event alone (render.ts syncHostOfflineFoot); the state
   *  itself is read back through __rompFed.dialing, so a listener that missed an event still paints the
   *  truth. Dispatch must never break the relay. */
  private dialEvent(host: string, dialing: boolean): void {
    try {
      window.dispatchEvent(new CustomEvent("romp:hostDial", { detail: { host, dialing } }));
    } catch (e) { /* nothing to do */ }
  }

  private closeRemote(host: string): void {
    const c = this.conns.get(host);
    if (!c) return;
    // /tunnels no longer lists it → its cards drop NOW. A detach also discards any settings still
    // queued for the host (the user removed it from the mesh; a later reattach re-syncs through the
    // gear's mixed marks) — named in the breadcrumb, because a drop is never silent.
    this.diag("hostconn", c.pending.size ? { host, ev: "detach", pendingDropped: pendingTypes(c) }
                                         : { host, ev: "detach" });
    c.closed = true;
    try {
      c.ws && c.ws.close();
    } catch (e) {}
    this.conns.delete(host);
    this.hostSeq = this.hostSeq.filter((h) => h !== host);
    // drop that host's tabs from the panes (else they linger stale), then re-emit the merged order.
    for (const sid of this.perHostSids[host] || []) {
      // Stamped `hostDrop`: this is NOT the session's end — its kernel is simply out of reach — so the pane
      // keeps the session's unsent draft for its return instead of clearing it like a close (T236).
      window.dispatchEvent(new MessageEvent("message", { data: { type: "closed", id: sid, hostDrop: true } }));
    }
    delete this.perHostOrder[host];
    delete this.perHostTabs[host];
    delete this.perHostLive[host];
    delete this.perHostSkeleton[host];   // with its order: a re-attach's first strip must not inherit a stale set
    delete this.perHostSids[host];
    delete this.perHostFeed[host];
    delete this.perHostFeedAt[host];
    const hadTl = host in this.perHostTl || host in this.perHostTlBars;
    delete this.perHostTl[host];
    delete this.perHostTlBars[host];
    this.emitMergedOrder();
    this.emitMergedFeed(); // drop the detached host's feed items so they don't linger
    if (hadTl) { this.emitMergedTimeline(false); this.emitMergedTimeline(true); } // …and its lanes/bars
  }
}

// Bootstrap on the browser only (the node test imports the pure functions above; this never runs there).
if (typeof window !== "undefined" && typeof document !== "undefined") {
  new FederationManager().start();
}
