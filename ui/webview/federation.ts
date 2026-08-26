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
import { hostOf, bareId } from "./host-prefix";

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
const ARRAY_ID = ["order", "names", "working", "awaiting", "stateUnknown"]; // an array of session ids
const OBJ_SID = ["asks", "items", "ledgers", "sessions"]; //  an array of objects keyed by `.sid`
const OBJ_ID = ["tabs"]; //                       an array of objects keyed by `.id`

// Gear settings the KERNEL acts on, which each kernel stores its own copy of: they describe how romp
// behaves towards the sessions it is running, so a machine that never hears the change goes on behaving
// the old way. setUpdateMode belongs here too: each kernel runs its own boot release check, so a machine
// that misses the change keeps handling new releases under the old policy (ask/auto/off). The distilling
// pair rides like the other judge tiers (the user 2026-08-14: everything kernel-side stays in sync; the
// gear's mixed marks surface any machine that disagrees rather than overwriting it silently). Broadcast in
// routeOutbound rather than routed. setFileEditing is the viewer's edit opt-in (the user 2026-08-22:
// one consent popup answers for the mesh — every kernel's save route gates on its own copy, so the
// broadcast is what makes the one yes reach them all). Deliberately NOT here: setDefaultDir (a path on
// one machine, meaningless on another) and setColormap/setPalette (the viewer's display prefs, which the
// local kernel persists for this browser).
const KERNEL_SETTING = new Set(["setAutoNudge", "setJudgeModel", "setIndexModel",
                                "setJudgeEffort", "setIndexEffort", "setUpdateMode",
                                "setDistillModel", "setDistillEffort", "setFileEditing"]);

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
  for (const k of ["judging"])
    if (Array.isArray(out[k]))
      out[k] = out[k].map((e: any) => (e && typeof e === "object" && typeof e.sid === "string" ? { ...e, sid: prefixId(host, e.sid) } : e));
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
export function routeOutbound(msg: any, knownHosts?: ReadonlySet<string>): Route[] {
  if (!msg || typeof msg !== "object") return [{ host: LOCAL, msg }];

  // an explicit `host` field wins (the + modal's createSession picks the target kernel): route there with
  // the field stripped — the kernel's handlers are host-blind.
  if (typeof msg.host === "string") {
    const { host, ...rest } = msg;
    return [{ host: host || LOCAL, msg: rest }];
  }

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
    for (const k of SCALAR_ID) if (typeof out[k] === "string") out[k] = bareId(out[k]);
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
 *  dashboard's own controls are local-authoritative. Ids are already prefixed by prefixInbound. */
export function mergeHostFeeds(perHost: Record<string, any>, hostSeq: readonly string[],
                               view: readonly string[] = [], deadHosts: readonly string[] = []): any {
  const local = perHost[LOCAL] || {};
  const merged: any = { ...local, type: "feed", items: [], asks: [], working: [], awaiting: [], stateUnknown: [], order: [], sessions: [] };
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
    if (Array.isArray(f.ledgers)) { anyLedgers = true; ledgers.push(...f.ledgers); }
    if (typeof f.dismissedCount === "number") { anyDismissed = true; dismissed += f.dismissedCount; }
    if (f.canUndoClear) canUndo = true;
  }
  // the grouped feed ranks its session runs off `order`, so it takes the viewer's arrangement like the tab
  // strip does — the two surfaces have to agree or the feed's groups and the tabs read in different orders.
  merged.order = applyViewOrder(merged.order, view);
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
const MARK_TIMES = ["t"] as const;                // judging / nudge marks
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
  for (const k of ["judging", "nudges"] as const)
    if (Array.isArray(c[k])) c[k] = c[k].map((m: any) => shiftRow(m, MARK_TIMES, off));
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
                                   view: readonly string[] = []): any {
  const local = perHost[LOCAL] || {};
  const offsets = hostOffsets(perHost);   // each host's clock vs the local authority, this merge
  const merged: any = { ...local, sessions: [], turns: {}, messages: [], judging: [] };
  for (const h of hostSeq) {
    const d = rebaseHostTimes(perHost[h], offsets[h] || 0);
    if (!d) continue;
    if (Array.isArray(d.sessions)) merged.sessions.push(...d.sessions.map((s: any) => ({ ...s, host: h })));
    if (d.turns && typeof d.turns === "object") Object.assign(merged.turns, d.turns);
    for (const k of ["messages", "judging"]) if (Array.isArray(d[k])) merged[k].push(...d[k]);
  }
  // lanes are the third surface reading this order (chat strip, feed groups, timeline lanes): arrange them
  // the same way, before the message stitch, which pairs postal arrows against the lane list.
  merged.sessions = applyViewOrderTo(merged.sessions, view, (x: any) => String((x && x.id) || ""));
  merged.messages = rebaseExecs(stitchMessages(merged.messages, merged.sessions), offsets);
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
  const merged: any = { ...local, type: "bars", turns: {}, messages: [], judging: [], warming: false };
  for (const h of hostSeq) {
    const b = rebaseHostTimes(perHost[h], offsets[h] || 0);
    if (!b) continue;
    if (b.turns && typeof b.turns === "object") Object.assign(merged.turns, b.turns);
    for (const k of ["messages", "judging", "nudges"]) if (Array.isArray(b[k])) merged[k].push(...b[k]);
    if (b.warming) merged.warming = true;   // still warming if ANY host's build is the cold partial (keep the loader)
  }
  merged.messages = rebaseExecs(stitchMessages(merged.messages, sessions), offsets);
  return merged;
}

// ── the wiring: WebSockets per kernel + the attach UI ────────────────────────────────────────────
// Thin glue over the pure functions above. The LOCAL kernel stays the shim's existing single WS — this
// manager only ADDS connections to attached remote kernels, so with no remotes attached the dashboard is
// byte-for-byte the single-kernel path. The shim calls window.__rompFed.inbound("", msg) for local frames
// and window.__rompFed.outbound(m) for sends (both no-ops when this module isn't loaded, e.g. the timeline
// pane), and exposes window.__rompLocalSend + window.__rompApp.

interface Conn {
  host: string;
  ws: WebSocket | null;
  url: string;
  closed: boolean;
  live: boolean; // kernel reports this tunnel "up" — the only state in which its port is dialed
}

export class FederationManager {
  app = "chat";
  private conns = new Map<string, Conn>();
  private perHostOrder: Record<string, string[]> = {};
  private perHostTabs: Record<string, any[]> = {};
  private localViews: any = null;   // the LOCAL kernel's session-views blob, carried on merged tabOrder re-emits
  private perHostSids: Record<string, Set<string>> = {};
  private perHostFeed: Record<string, any> = {}; // last feed snapshot per host — merged so they don't clobber
  private perHostTl: Record<string, any> = {}; //   last timeline lanes payload ({type:"data"}.data) per host
  private perHostTlBars: Record<string, any> = {}; // last timeline {type:"bars"} detail per host
  private hostSeq: string[] = [LOCAL]; // local first, then attach order — fixes the group order in the strip
  private downHosts = new Set<string>(); // attached, but its tunnel isn't up: what's on screen is a memory
  private lastSeen: Record<string, number> = {}; // host -> epoch secs of its last `up` poll

  start(): void {
    const w = window as any;
    this.app = w.__rompApp || "chat";
    w.__rompFed = {
      inbound: (h: string, m: any) => this.inbound(h, m),
      outbound: (m: any) => this.outbound(m),
      hosts: () => this.hostSeq.filter((h) => h !== LOCAL), // attached hosts (the + modal's host picker)
      // Hosts that are attached but NOT reachable right now. Their sessions stay on screen — dropping
      // them would lose the thread — but every surface that shows one has to say the link is down, or
      // you are reading a transcript that stopped updating with nothing telling you (the user
      // 2026-07-29). `lastSeen` dates what is on screen.
      down: () => [...this.downHosts],
      lastSeen: (h: string) => this.lastSeen[h] || 0,
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
  }

  // kernel → browser: prefix this host's ids, merge tab orders, hand the rest to the panes.
  inbound(host: string, msg: any): void {
    const m = prefixInbound(host, msg);
    if (m && m.type === "session" && typeof m.id === "string") {
      (this.perHostSids[host] ||= new Set()).add(m.id);
    }
    if (m && m.type === "tabOrder") {
      const prevOrder = this.perHostOrder[host] || [];
      const prevTabs = this.perHostTabs[host] || [];
      this.perHostOrder[host] = Array.isArray(m.order) ? m.order.filter((x: any) => typeof x === "string") : [];
      this.perHostTabs[host] = Array.isArray(m.tabs) ? m.tabs : [];
      // session VIEWS (the user 2026-08-18): the blob is the LOCAL kernel's viewer pref (ids arrive
      // host-prefixed inside it already) — remote kernels' copies are their own dashboards' prefs.
      // Without this passthrough the merged re-emit silently dropped the field and the browser
      // dashboard's chat never learned the views at all.
      if (host === LOCAL && m.views && typeof m.views === "object") this.localViews = m.views;
      this.ensureHost(host);
      this.absorbHostReport(host, prevOrder, prevTabs);   // a host just reported its sessions → the one
      this.emitMergedOrder();                             //   moment the stored arrangement may be touched
      return;
    }
    if (m && m.type === "feed") {
      this.perHostFeed[host] = m;
      this.ensureHost(host);
      this.emitMergedFeed();
      return;
    }
    // timeline snapshots replace the panel's state wholesale (update/applyBars) — merge per host like the feed.
    if (m && m.type === "data" && m.data && typeof m.data === "object") {
      this.perHostTl[host] = m.data;
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
    const dead = this.hostSeq.filter((h) => {
      if (h === LOCAL) return false;
      const c = this.conns.get(h);
      return !c || !c.ws || c.ws.readyState === 3;   // no socket / closed = a dead link right now
    });
    window.dispatchEvent(new MessageEvent("message", { data: mergeHostFeeds(this.perHostFeed, this.hostSeq, this.view(), dead) }));
  }

  private emitMergedTimeline(bars: boolean): void {
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
      : { type: "data", data: mergeHostTimelines(this.perHostTl, this.hostSeq, this.view()) };
    window.dispatchEvent(new MessageEvent("message", { data }));
  }

  // Every caller re-emits WITHOUT touching the stored arrangement — a drag landing here through the
  // storage / CustomEvent path must never be answered by a rewrite (the 2026-08-02 revert bug: a pane
  // holding a stale session list pruned the very tab another pane had just moved). Only an inbound
  // tabOrder push mutates the store, in absorbHostReport below, because only a host's own report is
  // evidence about what exists.
  private emitMergedOrder(): void {
    const order = mergeHostOrder(this.perHostOrder, this.hostSeq, this.view());
    const tabs = this.hostSeq.flatMap((h) => this.perHostTabs[h] || []);
    window.dispatchEvent(new MessageEvent("message", { data: { type: "tabOrder", order, tabs, views: this.localViews ?? undefined } }));
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

  private lastClearHost = LOCAL; // where the most recent askClear routed — undoClear follows it

  // browser → kernel: route each message to the owning kernel, prefix stripped.
  outbound(m: any): void {
    // undoClear undoes the LAST clear, which may have gone to a remote kernel — follow it there.
    // (The kernel keeps its own cleared.jsonl; only the kernel that took the clear can undo it.)
    if (m && m.type === "undoClear" && this.lastClearHost !== LOCAL) {
      const c = this.conns.get(this.lastClearHost);
      if (c && c.ws && c.ws.readyState === 1) c.ws.send(JSON.stringify(m));
      else this.dropWarn(this.lastClearHost, m);
      this.lastClearHost = LOCAL;
      return;
    }
    const routes = routeOutbound(m, new Set(this.hostSeq.filter((h) => h !== LOCAL)));
    if (m && m.type === "askClear") this.lastClearHost = routes[0] ? routes[0].host : LOCAL;
    for (const r of routes) {
      if (r.host === LOCAL) {
        const s = (window as any).__rompLocalSend;
        if (typeof s === "function") s(r.msg);
      } else {
        const c = this.conns.get(r.host);
        if (c && c.ws && c.ws.readyState === 1) c.ws.send(JSON.stringify(r.msg));
        else this.dropWarn(r.host, r.msg);
      }
    }
  }

  // A route to a host whose socket isn't open would otherwise VANISH — creating a session on an
  // unreachable remote gave no feedback at all (the user 2026-07-10). Surface the drop as a local
  // `warn` (render.ts toasts it), naming the host and the action so the user knows what didn't land.
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
    const want = new Map<string, any>(tunnels.filter((t) => t.token && t.localPort).map((t) => [t.host, t]));
    for (const [host, t] of want) if (!this.conns.has(host)) this.openRemote(host, t.token, t.status === "up");
    for (const host of [...this.conns.keys()]) if (!want.has(host)) this.closeRemote(host);
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
    if (recovered.length) window.dispatchEvent(new MessageEvent("message", { data: { type: "hostUp", hosts: recovered } }));
    this.downHosts = down;
    if (changed) window.dispatchEvent(new Event("romp-hosts"));   // panes repaint their disconnected marks
  }

  private openRemote(host: string, token: string, live: boolean): void {
    // Dial the remote through THIS kernel's /remote/<host>/ws relay, on the same origin that served
    // the page — never at 127.0.0.1:<forwarded port>, which only exists on the kernel's machine:
    // from a phone reading the dashboard over `tailscale serve`, that address is the phone itself,
    // and every remote host silently vanished with no disconnected mark (the user 2026-07-30).
    // Same-origin also means the local auth cookie rides the upgrade; the ?token (the remote
    // kernel's credential, from /tunnels) is re-checked and rewritten by the relay either way.
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    // …carrying this dashboard's `wid`, exactly as the pane's own local socket does. Without it a remote
    // kernel sees every federated viewer as one anonymous client and BROADCASTS its per-viewer messages,
    // so one dashboard's jump to a remote session yanked every other open dashboard to that tab — the
    // very cross-window yank the local path fixed (the user 2026-07-29).
    const w = dashboardWid();
    const url = `${proto}${location.host}/remote/${encodeURIComponent(host)}/ws?app=${encodeURIComponent(this.app)}&token=${encodeURIComponent(token)}`
      + (w ? `&wid=${encodeURIComponent(w)}` : "");
    const conn: Conn = { host, ws: null, url, closed: false, live };
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
    try {
      ws = new WebSocket(conn.url);
    } catch (e) {
      setTimeout(() => this.connect(conn), 2000);
      return;
    }
    conn.ws = ws;
    ws.onopen = () => this.diag("hostconn", { host: conn.host, ev: "open" });
    ws.onmessage = (ev: MessageEvent) => {
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
      this.diag("hostconn", { host: conn.host, ev: "close", code: ev.code, clean: ev.wasClean, detached: conn.closed });
      if (!conn.closed) setTimeout(() => this.connect(conn), 2000); // reconnect a dropped remote
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (e) {}
    };
  }

  private closeRemote(host: string): void {
    const c = this.conns.get(host);
    if (!c) return;
    this.diag("hostconn", { host, ev: "detach" });   // /tunnels no longer lists it → its cards drop NOW
    c.closed = true;
    try {
      c.ws && c.ws.close();
    } catch (e) {}
    this.conns.delete(host);
    this.hostSeq = this.hostSeq.filter((h) => h !== host);
    // drop that host's tabs from the panes (else they linger stale), then re-emit the merged order.
    for (const sid of this.perHostSids[host] || []) {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "closed", id: sid } }));
    }
    delete this.perHostOrder[host];
    delete this.perHostTabs[host];
    delete this.perHostSids[host];
    delete this.perHostFeed[host];
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
