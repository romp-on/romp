'use strict';

// romp-timeline-view — the Timeline tab's panel: a reversed-log window slider +
// an SVG timeline (lanes per session, activity bars, prompt dots, message
// connectors, Obsidian-style status chips).
//
// TimelinePanel owns the DOM under a host element: build it once, then call
// update(data) each poll to redraw the SVG (the slider persists across redraws).

const SVGNS = 'http://www.w3.org/2000/svg';
const MIN_W = 60, MAX_W = 172800;                  // 1 min … 48 h window (NICE has 60 → 1-min ticks render)
const MAX_OFFSET = 72 * 3600;                      // pan slider: right edge from now (0) back to −72 h (linear)
// Compact metrics: rows collapse to the minimum height a bar+dots+label need.
const LANE_GAP = 26, BAR_H = 8, CORNER = 6, MSG_DROP = 10, DOT_R = 6, CLEAR = DOT_R + 4, COINCIDE = 45;
// Demo/recording VIEW filter (the user 2026-07-14): the dashboard loaded at `#only=<tag>` scopes every
// pane to sessions whose name starts with <tag>. The timeline reads the SHELL's URL (window.top) so one
// tag on the dashboard URL filters the lanes here too; a cross-origin top falls back to our own URL.
// Filtering data.sessions is enough — cross-session flows already skip when a lane is absent (vidx guard).
function _rompOnlyTag() {
  const read = (loc) => { try { const hay = (loc.hash || "") + " " + (loc.search || ""); const m = hay.match(/only=([^&\s]+)/i); return m ? (decodeURIComponent(m[1]).trim().toLowerCase() || null) : null; } catch (e) { return null; } };
  if (typeof window === "undefined") return null;   // no DOM (a test/headless context) → no filter
  try { return read((window.top || window).location); } catch (e) { /* cross-origin top */ }
  try { return read(window.location); } catch (e) { return null; }
}
// <tag> may be a comma-separated LIST (`#only=api,tests,web`) so demo sessions need no shared
// on-camera prefix (the user 2026-07-16). Mirrors ui/webview/only-filter.ts's matchesOnly.
function _rompMatchesOnly(name, tag) {
  if (!tag) return true;
  const n = (name || "").toLowerCase();
  return tag.split(",").map((t) => t.trim()).filter(Boolean).some((t) => n.indexOf(t) === 0);
}
// Is this lane's session on a remote host romp cannot currently reach? federation.js publishes the set
// (filled by the KERNEL's own ssh health checks) and fires 'romp-hosts' when it changes. No manager on
// the page — a single-kernel dashboard, the Obsidian panel — means no remote lanes, so: false.
function _rompHostDown(sid) {
  const i = typeof sid === 'string' ? sid.indexOf(':') : -1;
  if (i <= 0) return false;
  try {
    const fed = typeof window !== 'undefined' && window.__rompFed;
    return !!fed && typeof fed.down === 'function' && fed.down().indexOf(sid.slice(0, i)) >= 0;
  } catch (e) { return false; }
}
// Each directed flow (A→B) is ONE line; its thickness = MSG_W0 + (count-1)*MSG_GROW
// — linear in message count, no max cap (BAR_H=8 is the work-bar reference: a flow
// passes that around ~5-6 messages and keeps growing). Drawn at alpha .5 so
// overlapping flows stay legible.
const MSG_W0 = 2, MSG_GROW = 1.3;
// Invisible hit stroke for a message connector. Wide enough that the SHORT vertical runs (an
// immediately-delivered message is almost entirely vertical: lane → track → lane, ~26px total with
// the ends under the dots) are an easy target rather than a few pixels of exposed line.
const MSG_HIT_W = 18;
const GAP_MIN = 20 * 60;   // broken-axis: collapse idle gaps (no work on ANY lane) longer than this. Each
                           // collapses to GAP_FRAC of the window — a CONTINUOUS function of zoom (not the
                           // discrete niceStep), so the break width changes smoothly while zooming (no
                           // jumps), at a ~constant pixel width. (See _buildCompressMap.)
const GAP_FRAC = 0.11;     // collapsed-gap compressed width = GAP_FRAC * winSec ≈ GAP_FRAC * plotW pixels
// Cross-hover focus (feed-card hover, DAG journey, feed-modal line hover) draws EXACTLY like the
// native glyph hover: the element THICKENS in its own color (bar → grown + opaque, connector → its
// own-color highlight overlay, dot → grown radius). The old thick white border/outline language
// (DAG_HL/DAG_W) is gone — one hover language everywhere (the user 2026-07-17).
// idle >1h fade: blend the color toward the surface bg until its LUMINANCE hits a uniform low target,
// so every hue lands at the same perceived dimness (plain opacity leaves bright hues looking brighter).
// Shared algorithm with romp-chat-view so all surfaces match — keep FADE_TARGET in sync with it.
const FADE_TARGET = 38;   // faded luminance = bg luminance + this; lower → more fade
function fadeHex(hex, bg) {
  if (!hex || hex[0] !== '#' || hex.length < 7) return hex;
  const n = parseInt(hex.slice(1, 7), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const lum = (x, y, z) => 0.2126 * x + 0.7152 * y + 0.0722 * z;
  const Lc = lum(r, g, b), Lb = lum(bg[0], bg[1], bg[2]), Lt = Lb + FADE_TARGET;
  if (Lc <= Lt) return hex;                                   // already dim enough → leave it
  const t = Math.min(0.85, (Lc - Lt) / (Lc - Lb));
  const hx = (a, c) => Math.round(a * (1 - t) + c * t).toString(16).padStart(2, '0');
  return '#' + hx(r, bg[0]) + hx(g, bg[1]) + hx(b, bg[2]);
}
const PADL = 8, COLGAP = 10;                        // gutter: name col | chip col
const BADGE_FS = 9;
const NICE = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800];
const BADGE = { working: { bg: '#E0B020', fg: '#332600' }, ready: { bg: '#2B7FB8', fg: '#ffffff' },
                attention: { bg: '#C0392B', fg: '#ffffff' }, compacting: { bg: '#11808f', fg: '#ffffff' },
                retrying: { bg: '#e67e22', fg: '#2a1500' },   // amber: soft-blocked on an API rate-limit/overload auto-retry (api 2026-06-23)
                awaitbg: { bg: '#54B204', fg: '#0c1a00' } };  // romp brand green: idle, waiting on bg work — matches the chat chip (--st-awaitbg-bg; the user 2026-07-22)
const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
// Judging band: a compact second timeline UNDER the session lanes, on the SAME axis — one row per
// summarizer judge (docs/judges.md). Each mark is FILLED with the colour of the SESSION it acted on and
// OUTLINED in the judge's OWN colour (so a bar reads as "judge X on session Y"). Fed by
// data.judging = [{judge, sid, t, kind, text}]. Each judge's colour is a distinct hue from the romp palette.
// Each judge belongs to a SET (the user 2026-06-29): 'index' = the captioner + archiver (caption/archive
// bookkeeping); 'triage' = planner/grouper/closer/distiller/courier (goal triage). The two settings toggles
// (showIndexJudges / showTriageJudges) gate each set's rows on the band — see judgesShown().
const JUDGES = [
  { key: 'captioner', color: '#1EA1EB', group: 'index' }, { key: 'archiver', color: '#54B204', group: 'index' },
  { key: 'planner', color: '#E0B020', group: 'triage' }, { key: 'grouper', color: '#4EA8A9', group: 'triage' },
  { key: 'closer', color: '#C0392B', group: 'triage' }, { key: 'distiller', color: '#D26EA8', group: 'triage' },
  { key: 'courier', color: '#9088F0', group: 'triage' },
];
// The judges to show right now, from the two settings toggles. Legacy migration: when a toggle is unset, fall
// back to the old single `debug` flag, so an existing Debug-on user still sees every judge. Read fresh so a
// settings change repaints the band on the next draw().
function judgesShown() {
  let s = {}; try { s = JSON.parse(localStorage.getItem('romp:settings') || '{}') || {}; } catch (e) {}
  const idx = s.showIndexJudges !== undefined ? !!s.showIndexJudges : !!s.debug;
  const tri = s.showTriageJudges !== undefined ? !!s.showTriageJudges : !!s.debug;
  return JUDGES.filter((j) => (j.group === 'index' ? idx : tri));
}
const JROW = 14, JBAR_H = 9, JB_TOPGAP = 17, JB_BOTGAP = 5, JMARK_MINW = 6, JMERGE_GAP = 110;
const JUDGE_KIND = { segment: 'caption', turn: 'turn caption', index: 'archived', mint: 'new goal',
  sub: 'filed a step', done: 'completed', block: 'needs you', group: 'regrouped', plant: 'handoff in',
  distill: 'key takeaway', brief: 'decision brief' };

function el(t, a) { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; }
function esc(s) { return (s || '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
// Strip romp's own HTML-comment markers (<!-- romp-injected/-system/-auto/-goal-id … -->) from a prompt before
// it's shown. They're classification metadata — invisible in the chat's MARKDOWN render, but the timeline
// tooltips ESCAPE their text, so an injected notice (a romp-system bg-task-death message) leaked the literal
// "<!-- romp-injected --><!-- romp-system -->" into the tip (the user 2026-07-15). Leading whitespace left by a
// removed marker is trimmed so the visible text starts at the real content.
function stripRompMarks(s) { return (s || '').replace(/<!--\s*romp-[\s\S]*?-->/g, '').replace(/^\s+/, ''); }
// romp's auto-retry ("retry", romp-injected) can COALESCE into one delivered user message during an API-error
// storm, so a retry segment's request read "retry retry retry …" — many repeats of the same bookkeeping token
// (the user 2026-07-16). When a request is nothing but the SAME token repeated, collapse it to that token +
// the repeat count ("retry ×14"): the storm's SIZE is the useful signal, not fourteen copies of the word. A
// genuine request (any variety in its words) is left untouched.
function collapseRepeat(s) {
  const toks = (s || '').split(/\s+/).filter(Boolean);
  if (toks.length > 1 && toks.every((t) => t.toLowerCase() === toks[0].toLowerCase())) return toks[0] + ' ×' + toks.length;
  return s;
}
// romp labels its own injected notices "[romp] …" for the chat's benefit. On the timeline the dot already
// wears the romp logo + a 'romp' caption, so the prefix is pure redundancy in the tip (the user 2026-07-16) —
// strip it, exactly as the chat's romp-system card does ("the chip already says who it's from"). Only romp's
// own notices carry the label, so this never eats a human's words.
function stripRompLabel(s) { return (s || '').replace(/^\s*\[romp\]\s*/i, ''); }
// A tooltip request line is a GIST, not a transcript dump (the user 2026-07-17: a kernel-restart notice
// filled the tip and hard-cut mid-word — "…history intact. R"). Same gist idiom as the chat's nudge and
// romp-system cards: FIRST non-empty line only, and over 90 chars it truncates at a word boundary with
// an ellipsis. The full message is a click away (the dot opens the chat) — progressive disclosure.
function reqText(prompt) {
  const s = collapseRepeat(stripRompLabel(stripRompMarks(prompt)));
  const first = (s.split('\n').find((l) => l.trim()) || s).trim();
  return esc(first.length > 90 ? first.slice(0, 88).replace(/\s+\S*$/, '') + '…' : first);
}
function clock(t) { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
function clockS(t) { const d = new Date(t * 1000); return clock(t) + ':' + String(d.getSeconds()).padStart(2, '0'); }   // seconds precision for API call times
function fmtWin(s) { return s < 3600 ? Math.round(s / 60) + 'm' : (s / 3600 < 10 ? (s / 3600).toFixed(1) : Math.round(s / 3600)) + 'h'; }
function fmtTokens(n) { n = Math.round(n || 0); return n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'k' : String(n); }
function fmtDur(ms) { ms = Math.round(ms || 0); return ms < 1000 ? ms + 'ms' : ms < 60000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms / 60000) + 'm'; }
// Pure (exported for tests): a long idle gap's SPAN as a concise day/week/month label ("2 days", "1 week",
// "3 weeks", "2 months") so a multi-day broken-axis break isn't ambiguous between its two HH:MM boundary
// clocks ("23:40 → 08:50" could be 9h or 2 days). Day-scale only — callers gate on span ≥ 1 day (the user
// 2026-06-17). Each unit's count is clamped below the next unit's threshold so it never reads "7 days".
function fmtSpan(s) {
  const DAY = 86400, WEEK = 7 * DAY, MONTH = 30 * DAY;
  const u = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  if (s < WEEK)  return u(Math.min(6, Math.max(1, Math.round(s / DAY))),  'day');
  if (s < MONTH) return u(Math.min(4, Math.max(1, Math.round(s / WEEK))), 'week');
  return u(Math.max(1, Math.round(s / MONTH)), 'month');
}
function niceStep(W) { for (const s of NICE) if (W / s <= 8) return s; return 172800; }
// Smooth live-edge advance (the user 2026-06-13): between data polls, advance the effective `now` by the
// wall-clock elapsed since the current data.now was observed, so the live edge GLIDES instead of jumping
// each poll (most visible zoomed in). Pure + exported so the clamp is unit-tested.
//   baseSec — the data's `now` (epoch sec)      live  — are we live-following right now?
//   baseMs  — monotonic ms when baseSec observed  nowMs — monotonic ms now
// Clamp the advance to [0, maxAheadSec]: never run backward (a clock hiccup), and never fling the edge
// far ahead if the tab was backgrounded (rAF paused → huge elapsed) or the kernel went quiet.
const MAX_INTERP_AHEAD = 30;   // seconds the edge may glide past the last data.now before it just waits
const LIVE_MIN_PX = 0.15;      // live-tick repaints once the edge would move ≥ this many px — small so the
                               // glide stays smooth at high zoom (effectively native rAF), but >0 so a
                               // near-static (zoomed-out) edge idles instead of repainting for nothing
function perfNow() { return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now(); }
function interpNow(baseSec, baseMs, nowMs, live, maxAheadSec) {
  if (!live || baseMs == null) return baseSec;
  const cap = maxAheadSec == null ? MAX_INTERP_AHEAD : maxAheadSec;
  return baseSec + Math.max(0, Math.min((nowMs - baseMs) / 1000, cap));
}

// Re-anchor decision for the live edge's time-baseline (pure + exported for unit tests). The edge
// FREE-RUNS on the local clock between re-anchors (interpNow off a FIXED baseSec/baseMs); we re-snap the
// baseline onto a fresh data.now only on a genuine STEP — never for the few-ms poll-ARRIVAL jitter which,
// snapped every poll, made the live edge hiccup ~1-2px while otherwise gliding smoothly (the user
// 2026-06-15; worse zoomed in, where a px is fewer ms). Re-anchor when:
//   • no anchor yet (first poll), or we're not live-following (held/frozen: nowS doesn't drive the window
//     position — `off` cancels it — so keeping data.now fresh is jump-free AND keeps off-screen pending
//     items advancing), or we just (re)entered live-following (adopt the current now), or
//   • the free-running edge has drifted from data.now past REANCHOR_SEC — a backgrounded-tab resume
//     (interpNow's clamp left us behind), a seek, or real client↔kernel clock skew → one clean catch-up.
// Same physical machine → data.now and the local clock share a rate, so a live edge re-anchored once then
// free-run stays locked to data.now (drift ~0); the constant transport-latency offset is invisible.
const REANCHOR_SEC = 0.5;   // s the edge may fall BEHIND a sample before it catches up forward
function shouldReanchorEdge(baseSec, baseMs, nowMs, dataNow, live, wasLive) {
  if (baseSec == null || baseMs == null || !live || !wasLive) return true;
  const displayed = interpNow(baseSec, baseMs, nowMs, true, MAX_INTERP_AHEAD);
  return Math.abs(dataNow - displayed) > REANCHOR_SEC;
}

// Re-anchor the live edge's time-baseline from a fresh sample, keeping the displayed edge MONOTONIC — it
// never moves backward. The edge free-runs on the local monotonic clock between samples (interpNow off a
// fixed base); each live poll rebases it:
//   • first anchor / (re)entering live-follow (wasLive false) → adopt the sample directly.
//   • the edge fell BEHIND reality by > REANCHOR_SEC (a backgrounded tab whose interp clamped, or real
//     lag) → catch up FORWARD to the sample.
//   • otherwise HOLD the current displayed value and continue from it at real rate. This is the
//     "jumps forward and then jumps back" fix (the user 2026-07-03): the OLD rule rebased baseSec to the
//     sample on any > 0.5s ABSOLUTE drift, so bursty/jittery delivery (a sample landing BEHIND the
//     free-run edge, e.g. a backlogged push, a federated re-emit, transport latency variance) snapped the
//     axis BACKWARD, and a sample landing ahead hopped it FORWARD. Holding at max(sample, displayed) makes
//     the edge non-decreasing: the only motion is a forward catch-up when genuinely behind; the constant
//     sub-second lead a burst leaves is invisible and never grows (both advance at 1× real rate).
// Returns the new {baseSec, baseMs}. Pure + exported for tests.
function reanchorEdge(baseSec, baseMs, nowMs, dataNow, wasLive) {
  if (baseSec == null || baseMs == null || !wasLive) return { baseSec: dataNow, baseMs: nowMs };
  const displayed = interpNow(baseSec, baseMs, nowMs, true, MAX_INTERP_AHEAD);
  if (dataNow > displayed + REANCHOR_SEC) return { baseSec: dataNow, baseMs: nowMs };   // behind → catch up forward
  return { baseSec: displayed, baseMs: nowMs };                                          // ahead/steady → hold, never backward
}

// Is `incoming` a genuinely NEW clock sample — newer than the newest data.now seen this page-lifetime?
// A single kernel's clock never runs backward within a page lifetime, so a non-increasing data.now is
// definitionally a RE-EMISSION of an older payload, not time: federation re-emits the STORED local
// payload whenever a REMOTE host pushes (its `now` is up to a push interval old), and _cached_timeline
// re-serves the `now` baked at build time. Anchoring the live edge on those made the axis snap backward
// on every remote push and forward on every local one — the "jumps forward and then keeps going
// backwards" oscillation (the user 2026-07-03, first remote host attached). Pure + exported for tests.
function isFreshNowSample(newestSeen, incoming) {
  // Number.isFinite (not typeof): a NaN sample adopted as newestSeen would reject every later real
  // sample (NaN compares false), freezing data.now at NaN for the page's lifetime.
  return Number.isFinite(incoming) && (newestSeen == null || incoming > newestSeen);
}

// Right edge a work bar is DRAWN to. An OPEN ("still working") bar has its `end` baked to data.now at
// emit, so between polls it would sit at the stale now while the axis glides past — then jump forward on
// the next re-emit (the user saw this 2026-06-13). So draw an open bar to the interpolated live edge
// (nowS) instead, so it advances WITH the axis; a closed bar keeps its real end. "open" = the data's
// flag if present, else (robust) its end reached the emit-time now (open intervals ride `now`).
function barEndT(t, nowS, dataNow) {
  const liveBar = t.open === true || (typeof t.end === 'number' && t.end >= dataNow);
  return liveBar ? Math.max(nowS, t.start) : t.end;
}

// Which axis a click-drag commits to once it passes the threshold (the user 2026-06-13's mouse model):
// horizontal-dominant → PAN the plot, vertical-dominant → REORDER the lane (mirrors onWheel's horiz/vert
// split). null until it moves enough to decide, so a plain click still selects/opens the lane.
function dragAxis(dx, dy, threshold) {
  const t = threshold == null ? 4 : threshold;
  if (Math.abs(dx) < t && Math.abs(dy) < t) return null;
  return Math.abs(dx) > Math.abs(dy) ? 'pan' : 'row';
}
// The uuid anchor a WORK-intent click opens in the chat: the period's READABLE
// reply line (replyUuid = last assistant line with text, NOT the first which is
// usually a thinking block), then workUuid (first reply line), then the boundary
// uuid (an interrupted period has no reply at all). Shared by the focus handler
// and the work-bar click so the two landings can never drift apart again.
function workAnchorOf(t) { return (t && (t.replyUuid || t.workUuid || t.uuid)) || null; }

// Which ATOM of a turn a highlight set covers — `hit(id)` = membership in the active DAG-journey
// ∪ hover set. A turn renders two glyphs: the prompt DOT and the work-period BAR. The DOT lights
// for the whole-turn id (a DAG journey / a coarse card hover wants the whole turn) OR the PROMPT
// atom id; the BAR for the whole-turn id OR the WORK atom id. Because the two atom ids are minted
// distinctly by romp-events (t.promptId / t.workId), a chat 'message' hover (emitting promptId)
// rings ONLY the dot and an 'action' hover (emitting workId) ONLY the bar — the split is read from
// the data model, never guessed at render time. Exported for tests.
function dotLit(t, hit) { return hit(t.id) || hit(t.promptId); }
function barLit(t, hit) { return hit(t.id) || hit(t.workId); }

// Pure (exported for tests): from MERGED activity intervals [[a,b],…] (sorted, non-overlapping)
// the list of idle stretches worth collapsing on the broken axis — each ≥GAP_MIN AND wider than the
// collapsed width gapCT. Two sources: gaps BETWEEN activity, plus — when `now` is given — the TRAILING
// gap from the last activity's end up to now (the user 2026-06-12), so a quiet period before the
// present collapses too. Returns [{ ra, rb, trailing }] in ascending order; the trailing gap (if any)
// is last and flagged so its right boundary (now) draws no "resumed" clock.
function idleGaps(merged, gapCT, now) {
  const gaps = [];
  for (let i = 1; i < merged.length; i++) {
    const ga = merged[i - 1][1], gb = merged[i][0], D = gb - ga;
    if (D >= GAP_MIN && D > gapCT) gaps.push({ ra: ga, rb: gb, trailing: false });
  }
  if (now != null && merged.length) {
    const ga = merged[merged.length - 1][1], D = now - ga;
    if (D >= GAP_MIN && D > gapCT) gaps.push({ ra: ga, rb: now, trailing: true });
  }
  return gaps;
}

function badgeFor(s) {
  if (!s || !s.live) return null;
  let m = null;
  if (s.state === 'working') {
    // Live Task-subagent count (SDK only) rides the WORKING badge —
    // so "what's actually running" is glanceable, the transparency the tmux backend never had. Blank when none.
    const n = (s.subagents && s.subagents.length) || 0;
    m = { label: n ? 'Working · ' + n + (n === 1 ? ' subagent' : ' subagents') : 'Working', kind: 'working' };
  }
  else if (s.state === 'retrying') m = { label: 'Retrying', kind: 'retrying' };   // amber, distinct from the red BLOCKED — a soft API-retry stall (api 2026-06-23)
  // the lane state IS the chat chip now (the kernel's shared _session_chip, the user 2026-07-03) — the
  // chip vocabulary below; the legacy raw names stay accepted for the cold-skeleton fallback.
  else if (s.state === 'blocked') m = { label: 'API error', kind: 'attention' };  // same red the chat chip shows
  else if (s.state === 'interrupting') m = { label: 'Interrupting', kind: 'working' };  // stop in flight
  else if (s.state === 'permission' || s.state === 'awaiting') m = { label: 'Blocked', kind: 'attention' };
  // AWAITING dispatched/background work: its OWN chip state now ('awaitingBg', the kernel's shared
  // _session_chip split, the user 2026-07-13 — no longer folded into working) in STRAW, the working gold's
  // paler sibling: same family, visibly held rather than producing. The s.awaitingBg why-field key stays as
  // the fallback (a remote host on an older kernel still reports state 'working' + the field).
  // (The LEGACY lane state 'awaiting' above means blocked-on-you — this name dodges that.)
  else if (s.state === 'awaitingBg' || s.awaitingBg) m = { label: 'Awaiting', kind: 'awaitbg' };
  else if (s.state === 'ready' || s.state === 'waiting' || s.state === 'idle') m = { label: 'Ready', kind: 'ready' };
  if (!m) return null;
  return { label: m.label, bg: BADGE[m.kind].bg, fg: BADGE[m.kind].fg };
}

// context-window fill % → a battery bar (matches romp-chat-view's context indicator, v0.4.115): the
// fill WIDTH = pct, recolored by level (green <60, amber 60–84, red ≥85). null for historical sessions
// / before first report. BAT_W×BAT_H is the bar box.
const BAT_W = 48, BAT_H = 14;
function ctxInfo(s) {
  if (!s || s.context == null) return null;
  const p = s.context;
  // The GLOBAL colormap (the user 2026-06-26): the kernel computes the fill color server-side (s.ctxColor =
  // ramp(context%) on the selected map, bright = full) so the client just applies it — same pattern as the
  // usage bar. Fall back to the old traffic-light only if an older kernel didn't ship a color.
  const color = (s.ctxColor && s.ctxColor.length === 3) ? 'rgb(' + s.ctxColor.join(',') + ')'
    : (p >= 85 ? '#c0392b' : (p >= 60 ? '#e0b020' : '#54B204'));
  return { label: p + '%', pct: p, color: color };
}

// Model + effort, e.g. "Opus 4.8 xhigh" — the SAME string the Claude status bar shows.
// statusline.sh publishes @claude-model/@claude-effort to tmux; the data layer reads them onto the
// session. Rendered as muted secondary text between the name and the state chip. '' when unknown
// (historical/dead lanes never reported it, and some models carry no effort level).
const MODEL_FG = '#9aa0a6';
// The romp accent blue (same as the Fleet pill / focus accent). The lane toggles (feed checkbox, mailbox)
// draw in this when ON, and fall back to the muted gray MODEL_FG + strike-through when OFF (the user
// 2026-06-24): an ENABLED toggle reads as romp-blue, a disabled one as a faded, struck-out gray.
const ROMP_BLUE = '#9cd2ff';
function modelLabel(s) {
  if (!s) return '';
  if (!s.model) return s.effort || '';   // effort is always known (registry) — show it even before the model connects
  return s.effort ? s.model + ' ' + s.effort : s.model;
}

// The model + effort labels are little drop-down pickers (mirror of the chat statusline's): on a
// LIVE lane, clicking the model or effort word opens a menu whose pick injects the matching /model or
// /effort slash command into that session's pane (see _sendCommand → tmux, like _compactSession). The
// label refreshes on the next poll when the TUI republishes @claude-model/@claude-effort; _metaPending
// dims the word in the gap. Values mirror the extension's allowlist (extension.ts META_VALUES) verbatim.
const META_HOVER_FG = '#e6edf3';   // brighten the word + reveal its caret on hover
const META_CARET = ' ▾';           // appended (hair-spaced) after each clickable word
// Per-lane feed show/hide TOGGLE (the user 2026-06-22; a circular CHECKBOX since 2026-06-23, was an eye):
// sits between the name and the model. ON the feed (default) = a gray ring with a check inside, its prompts
// mint feed cards; OFF the feed = the SAME checkbox struck through + MORE faded (de-emphasised — NOT a
// highlight colour; we don't spotlight the disabled state), its prompts won't make cards though the lane
// stays on the timeline (re-opening only affects NEW prompts; it doesn't resurface past ones to clear). One
// click toggles it directly — no menu.
// Drawn (not an emoji) so it stays crisp + monochrome everywhere: a thin circular box + a checkmark, same
// gray line weight as the old eye; `off`=true adds the same strike-through slash so the muted state is
// obvious. One gray throughout — the caller fades the off state (now a touch more than the on state).
function feedCheckIcon(off, cx, cy, color) {
  const g = el('g', { 'pointer-events': 'none' });
  g.appendChild(el('circle', { cx: cx, cy: cy, r: 6, fill: 'none', stroke: color, 'stroke-width': 1.2 }));
  g.appendChild(el('path', { d: 'M' + (cx - 3) + ' ' + (cy + 0.3) + ' L' + (cx - 0.8) + ' ' + (cy + 2.7) + ' L' + (cx + 3.4) + ' ' + (cy - 2.7), fill: 'none', stroke: color, 'stroke-width': 1.3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  if (off) g.appendChild(el('line', { x1: cx - 6.5, y1: cy + 4.5, x2: cx + 6.5, y2: cy - 4.5, stroke: color, 'stroke-width': 1.4, 'stroke-linecap': 'round' }));
  return g;
}
// Per-lane POSTAL ISOLATION toggle (the user 2026-06-23): a monochrome ROADSIDE MAILBOX — a side-view tube
// body on a post, with a raised flag — sitting just right of the feed checkbox. Drawn (not an emoji) so it
// stays crisp + monochrome everywhere, same gray line weight as the checkbox; `off`=true (isolated) adds the
// SAME strike-through slash so the disabled state reads identically. Enforced in bin/romp-postal-service (hidden from
// peers, no messages in or out).
function mailboxIcon(off, cx, cy, color) {
  const g = el('g', { 'pointer-events': 'none' });
  const st = { fill: 'none', stroke: color, 'stroke-width': 1.2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' };
  // tube body (rounded top, flat bottom) — a roadside mailbox seen from the side
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx - 5) + ' ' + (cy + 1) + 'L' + (cx - 5) + ' ' + (cy - 1) + 'Q' + (cx - 5) + ' ' + (cy - 3) + ' ' + (cx - 2.8) + ' ' + (cy - 3) + 'L' + (cx + 1) + ' ' + (cy - 3) + 'Q' + (cx + 3) + ' ' + (cy - 3) + ' ' + (cx + 3) + ' ' + (cy - 1) + 'L' + (cx + 3) + ' ' + (cy + 1) + 'Z' }, st)));
  // the door (front face) + a small knob
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx + 1.3) + ' ' + (cy + 1) + 'L' + (cx + 1.3) + ' ' + (cy - 2.3) }, st)));
  g.appendChild(el('circle', { cx: cx + 2.15, cy: cy - 0.5, r: 0.55, fill: color, stroke: 'none' }));
  // the raised flag on the right
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx + 3) + ' ' + (cy - 1) + 'L' + (cx + 3) + ' ' + (cy - 5) + 'L' + (cx + 5.2) + ' ' + (cy - 5) + 'L' + (cx + 5.2) + ' ' + (cy - 3.3) + 'L' + (cx + 3) + ' ' + (cy - 3.3) }, st)));
  // the post + base (a mailbox on a stand)
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx - 1) + ' ' + (cy + 1) + 'L' + (cx - 1) + ' ' + (cy + 3.8) }, st)));
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx - 2.6) + ' ' + (cy + 3.8) + 'L' + (cx + 0.6) + ' ' + (cy + 3.8) }, st)));
  if (off) g.appendChild(el('line', { x1: cx - 6.5, y1: cy + 4.5, x2: cx + 6.5, y2: cy - 4.5, stroke: color, 'stroke-width': 1.4, 'stroke-linecap': 'round' }));
  return g;
}
// Per-lane NOTIFICATION toggle (the user 2026-07-28): a monochrome BELL just right of the mailbox. ON =
// the kernel fires an OS notification when this session's work blocks on you or completes (session-flags
// "notify", read by the kernel's feed-diff notifier); OFF (default) = the same bell struck through + more
// faded. Same drawn-not-emoji idiom + slash convention as the checkbox/mailbox. (The error center's rail
// icon stopped being a bell the same day, so the bell shape now means exactly this.)
function bellIcon(off, cx, cy, color) {
  const g = el('g', { 'pointer-events': 'none' });
  const st = { fill: 'none', stroke: color, 'stroke-width': 1.2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' };
  // the dome + skirt, one closed path (side profile, flared mouth)
  g.appendChild(el('path', Object.assign({ d: 'M' + cx + ' ' + (cy - 4.6) + 'C' + (cx - 2.4) + ' ' + (cy - 4.4) + ' ' + (cx - 3.2) + ' ' + (cy - 2.6) + ' ' + (cx - 3.2) + ' ' + (cy - 0.8) + 'L' + (cx - 3.2) + ' ' + (cy + 1) + 'L' + (cx - 4.4) + ' ' + (cy + 2.8) + 'L' + (cx + 4.4) + ' ' + (cy + 2.8) + 'L' + (cx + 3.2) + ' ' + (cy + 1) + 'L' + (cx + 3.2) + ' ' + (cy - 0.8) + 'C' + (cx + 3.2) + ' ' + (cy - 2.6) + ' ' + (cx + 2.4) + ' ' + (cy - 4.4) + ' ' + cx + ' ' + (cy - 4.6) + 'Z' }, st)));
  // the clapper: a small arc under the mouth
  g.appendChild(el('path', Object.assign({ d: 'M' + (cx - 1.3) + ' ' + (cy + 4.2) + 'A 1.3 1.3 0 0 0 ' + (cx + 1.3) + ' ' + (cy + 4.2) }, st)));
  if (off) g.appendChild(el('line', { x1: cx - 6.5, y1: cy + 4.5, x2: cx + 6.5, y2: cy - 4.5, stroke: color, 'stroke-width': 1.4, 'stroke-linecap': 'round' }));
  return g;
}
// The per-lane SETTINGS GEAR (the user 2026-07-28, round 3): the three toggle icons above no longer
// draw on the lane — three always-on icons crowded the row — so ONE gear opens them as a drop-down
// (_openLaneMenu), each row wearing its icon, its state, and a plain-language line. Drawn like its
// neighbors: a toothed ring, monochrome and HOLLOW — no hub dot (the user 2026-07-28), which also
// matches the ⛭ the rail's settings button wears at the bottom right.
function gearIcon(cx, cy, color) {
  const g = el('g', { 'pointer-events': 'none' });
  g.appendChild(el('circle', { cx: cx, cy: cy, r: 3.9, fill: 'none', stroke: color, 'stroke-width': 1.2 }));
  for (let i = 0; i < 8; i++) {
    const a = (Math.PI / 4) * i;
    g.appendChild(el('line', { x1: cx + 4.4 * Math.cos(a), y1: cy + 4.4 * Math.sin(a),
      x2: cx + 6.1 * Math.cos(a), y2: cy + 6.1 * Math.sin(a),
      stroke: color, 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
  }
  return g;
}
// Vertical placement for the fixed-position drop-downs (_openLaneMenu / _openMetaMenu). The timeline
// often renders as a SHORT bottom band — the web shell's f-timeline iframe — and position:fixed pins
// to THAT band's viewport, so a menu hung unconditionally at anchor.bottom+4 from a gear near the
// band's lower edge fell straight past the iframe's bottom and read as invisible / hidden behind the
// shell (the user 2026-08-07). Prefer below the anchor; flip above when below can't hold the menu;
// clamp to the viewport as the backstop when NEITHER side can (a band shorter than the menu keeps the
// menu's top edge on-screen — a cropped last row beats no menu at all).
function menuTop(anchor, menuH, viewH) {
  const below = anchor.bottom + 4;
  if (below + menuH <= viewH - 6) return below;
  const above = anchor.top - 4 - menuH;
  if (above >= 6) return above;
  return Math.max(6, viewH - 6 - menuH);
}
// Translate a pane-local anchor rect into a host document's coordinates by summing the intervening
// iframes' offsets (the moveTip pattern, extracted pure so the arithmetic is testable). `frames` is
// the list of frameElement rects between the pane and the host, innermost first.
function offsetRect(rect, frames) {
  let { left, top, bottom } = rect;
  for (const fr of frames || []) { left += fr.left; top += fr.top; bottom += fr.top; }
  return { left, top, bottom };
}
// The gear menu's rows, one per per-session flag. `enabled` reads the toggle's MEANING off the session
// (hideFromFeed/postalServiceOff are off-flags; notify is an on-flag), `value` maps a desired
// enabled-state back to the flag value _setSessionFlag persists.
const LANE_TOGGLES = [
  { flag: 'hideFromFeed', label: 'Feed cards', icon: feedCheckIcon,
    enabled: (s) => !s.hideFromFeed, value: (enable) => !enable,
    desc: 'its prompts make cards on the feed; off, the lane stays here but new prompts mint none' },
  { flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon,
    enabled: (s) => !s.postalServiceOff, value: (enable) => !enable,
    desc: 'visible to peer sessions, can send and receive their messages; off = fully isolated' },
  { flag: 'notify', label: 'Notifications', icon: bellIcon,
    enabled: (s) => !!s.notify, value: (enable) => enable,
    desc: 'system notification when its work blocks on you or completes' },
];
// Model + effort choices come from the kernel's /models — the ONE list shared with the chat statusline picker
// and the judge-tier settings (the user 2026-07-02: no hardcoded model list per surface). Populated in place
// on load so _openMetaMenu keeps its reference; the lane picker appends its own 'Default' sentinel (not a model).
const MODEL_CHOICES = [];
const EFFORT_CHOICES = [];
try {
  if (typeof fetch !== 'undefined') fetch('/models', { cache: 'no-store' }).then((r) => r.json()).then((d) => {
    if (Array.isArray(d.models)) { MODEL_CHOICES.length = 0; for (const m of d.models) MODEL_CHOICES.push(m); MODEL_CHOICES.push({ label: 'Default', value: 'default' }); }
    if (Array.isArray(d.efforts)) { EFFORT_CHOICES.length = 0; for (const e of d.efforts) EFFORT_CHOICES.push(e); }
  }).catch(() => {});
} catch (e) {}
// Is this menu entry the lane's CURRENT value? Effort matches exactly; the model var holds a display
// name ("Opus 4.8"), so match on the leading word — same rule as the chat view's isCurrentMeta.
function isCurrentMeta(kind, s, value) {
  const cur = ((kind === 'model' ? s.model : s.effort) || '').toLowerCase();
  return kind === 'effort' ? cur === value : cur.startsWith(value);
}

// rounded orthogonal path through waypoints (message connectors)
function roundedPath(pts, r) {
  const p = pts.filter((q, i) => i === 0 || q.x !== pts[i - 1].x || q.y !== pts[i - 1].y);
  if (p.length < 2) return '';
  let d = 'M ' + p[0].x + ' ' + p[0].y;
  for (let i = 1; i < p.length - 1; i++) {
    const a = p[i - 1], c = p[i], b = p[i + 1];
    const inL = Math.hypot(c.x - a.x, c.y - a.y), outL = Math.hypot(b.x - c.x, b.y - c.y);
    const rr = Math.max(0, Math.min(r, inL / 2, outL / 2));
    const i1 = { x: c.x - Math.sign(c.x - a.x) * rr, y: c.y - Math.sign(c.y - a.y) * rr };
    const o1 = { x: c.x + Math.sign(b.x - c.x) * rr, y: c.y + Math.sign(b.y - c.y) * rr };
    d += ' L ' + i1.x + ' ' + i1.y + ' Q ' + c.x + ' ' + c.y + ' ' + o1.x + ' ' + o1.y;
  }
  const L = p[p.length - 1]; d += ' L ' + L.x + ' ' + L.y; return d;
}
function crossX(lo0, hi0, xs, xe, obstacles) {
  const lo = Math.min(lo0, hi0), hi = Math.max(lo0, hi0);
  const between = obstacles.filter((o) => o.lane > lo && o.lane < hi && o.x >= xs - CLEAR && o.x <= xe + CLEAR);
  let xc = xs, changed = true, g = 0;
  while (changed && g++ < 60) { changed = false; for (const o of between) if (Math.abs(o.x - xc) < CLEAR) { xc = o.x + CLEAR; changed = true; } }
  return xc > xe ? xs : xc;
}

// Media assets: the kernel serves /media on the page origin (web/Obsidian), but a VS Code
// webview has a synthetic origin with no /media route — an absolute src there 404s and the
// loader's broken-image icon SPINS on the rl-o animation (the user 2026-07-13). The VS Code
// host injects window.__rompMediaBase = <asWebviewUri of media/>; every asset URL routes
// through here so both hosts resolve.
function mediaUrl(name) {
  return ((typeof window !== 'undefined' && window.__rompMediaBase) || '/media') + '/' + name;
}

class TimelinePanel {
  constructor(host) {
    this.host = host;
    this.data = null;
    this.fitted = false;
    // The kernel ships the timeline as a LANES skeleton ({type:"data"}, no turns) then the heavy BARS
    // ({type:"bars"} → applyBars). Until the bars land, the plot area (right of the lane labels) is empty,
    // so draw() paints the romp swirl loader there. Set true the instant applyBars runs (or a full one-shot
    // data object arrives through update()), and the loader is gone on the next draw. CLAUDE.md loader rule.
    this._barsLoaded = false;
    this._loaderBackstop = null;   // timer id: force the loader done if a warming build never brings content
    this.M = { left: 130, right: 16, top: 8, bottom: 22 };   // axis labels live in the bottom margin
    this._mc = document.createElement('canvas').getContext('2d');

    // No on-screen controls: window width + offset are driven entirely by trackpad gestures
    // (horizontal scroll = pan, pinch = zoom). They live as continuous SECONDS in _winSec/_offSec,
    // persisted directly to localStorage. winSec()/offSec() read them; fitWindow seeds _winSec.
    this.WSTORE = 'romp-tl-winsec';
    this.OSTORE = 'romp-tl-offsec';
    this.CSTORE = 'romp-tl-collapse';
    this.LSTORE = 'romp-tl-locknow';
    this._winSec = null; this._offSec = 0; this._drawRAF = null;
    // broken-axis: collapse long idle gaps (no work on any lane — e.g. overnight) into a thin squiggle
    // break, so the active periods get the width. ON by default; the checkbox below the axis toggles it.
    this._collapseGaps = true;
    // 🔒 lock-to-now (the user 2026-06-11): the live edge is pinned PERMANENTLY — pan gestures can't
    // leave it, and a focus that's off-screen ZOOMS OUT (window widens leftward, right edge stays at
    // now, target lands ~mid-window) instead of panning away. OFF by default; checkbox far right.
    this._lockNow = false;
    this._compactClicked = {};   // sid → click ts: show the compacting cue OPTIMISTICALLY until the real state catches up
    this._pendingFlags = {};     // sid → {flag: value}: an optimistic eye-toggle held STICKY across pushes until the kernel's data confirms it (no flicker-back)
    this._dismissed = new Set(); // sids cleared via the dead-lane Clear pill, held STICKY the same way (see _reconcileDismissed)
    // "Collapse idle gaps" now lives in the SETTINGS dialog (romp:settings.collapseGaps), moved out of the
    // timeline toolbar (the user 2026-06-25). Read it fresh here + re-read on the 'storage' event so a toggle
    // in the gear iframe live-syncs. (CSTORE is the legacy per-view key — honoured as a one-time fallback so
    // an existing OFF preference isn't lost on upgrade.)
    try {
      const raw = localStorage.getItem('romp:settings');
      if (raw) this._collapseGaps = JSON.parse(raw).collapseGaps !== false;
      else if (localStorage.getItem(this.CSTORE) === '0') this._collapseGaps = false;
    } catch (e) {}
    try { if (localStorage.getItem(this.LSTORE) === '1') this._lockNow = true; } catch (e) {}
    try { const v = localStorage.getItem(this.WSTORE); if (v != null && /^\d+(\.\d+)?$/.test(v)) { this._winSec = +v; this.fitted = true; } } catch (e) {}
    try { const v = localStorage.getItem(this.OSTORE); if (v != null && /^\d+(\.\d+)?$/.test(v)) this._offSec = +v; } catch (e) {}
    if (this._lockNow) this._offSec = 0;   // a restored mid-pan offset never overrides the lock
    // Live-follow vs hold-position. PINNED (default) = the window's right edge tracks `now`, so it
    // auto-scrolls. The instant the user pans/zooms off the now-edge it UNPINS and HOLDS its absolute
    // real-time position (no creep as `now` advances); a far-right ⟩⟩ button re-pins + resumes follow.
    // _holdReal = the absolute right-edge time held while unpinned; _offDirty = a gesture just wrote
    // _offSec, so draw() honors it verbatim this frame before resuming the hold. Restored mid-pan stays
    // unpinned (loaded off>0). [[contract with vs_chat]] is unaffected — this is pan state only.
    this._offDirty = true; this._holdReal = null; this._pinned = !(this._offSec > 0);
    // Smooth live-edge advance (see interpNow): while live-following, a rAF loop (_liveRAF) advances the
    // effective `now` by wall-clock between polls so the window glides. _nowBaseSec/_nowBaseMs = the edge's
    // time-baseline (epoch sec + the monotonic ms when it was observed); the edge FREE-RUNS off this fixed
    // pair and shouldReanchorEdge re-snaps it only on a genuine step, so per-poll arrival jitter no longer
    // hiccups the edge. _wasLive = were we live-following at the last poll (→ re-anchor on re-entry).
    // _lastLiveNow = effective-now of the last live repaint (sub-pixel guard so we only repaint when the
    // edge would actually move). Re-armed each poll; self-stops when not live.
    this._nowBaseSec = null; this._nowBaseMs = null; this._wasLive = false;
    this._liveRAF = null; this._lastLiveNow = null;
    // Newest data.now sample ever seen this page-lifetime (see isFreshNowSample): a push carrying an
    // OLDER now is a RE-EMISSION — federation re-emits the STORED local payload whenever a remote host
    // pushes, and _cached_timeline re-serves its build-time now — never a fresh clock sample, so it must
    // not move the live edge (the user 2026-07-03, who saw the timeline jump forward and then keep going
    // backwards the moment a remote host is attached).
    this._newestNow = null;

    this.wrap = host.createDiv({ cls: 'romp-tl-wrap' });
    this.svg = document.createElementNS(SVGNS, 'svg');
    this.svg.setAttribute('xmlns', SVGNS); this.wrap.appendChild(this.svg);
    // PERSISTENT compacting scan-bar overlay (the user 2026-06-29): the compacting battery's leftward
    // "compression" sweep is POSITIONAL motion, and as a SMIL <rect> it lived inside the SVG which draw()
    // WIPES + recreates every poll AND every live-edge rAF frame — so the bar could only ever animate as
    // smoothly as that irregular, heavy redraw cadence (visibly jumpy), unlike the chat tab's bar which is a
    // CSS animation on a PERSISTENT DOM node the compositor drives independently of JS. A SMIL phase-resync only
    // fixed the PHASE on rebuild, not the cadence. So the sweep now rides HTML divs in this overlay layer that draw()
    // REPOSITIONS (cheap, never restarts a CSS animation) but never destroys → it glides on the compositor like
    // the chat. The SVG keeps only the static battery box + the transparent /compact click target. The wrap is
    // 1:1 with the SVG viewBox (viewBox '0 0 W H', width=W=wrap.clientWidth), so overlay px == SVG user coords.
    this.wrap.style.position = this.wrap.style.position || 'relative';
    this._compactLayer = document.createElement('div');
    this._compactLayer.className = 'romp-tl-compact-layer';
    this._compactLayer.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden';
    this.wrap.appendChild(this._compactLayer);
    this._compactBars = new Map();   // sid -> persistent scan-bar div (CSS-animated; repositioned per draw)
    this._workLabels = new Map();    // sid -> persistent WORKING-badge label div (CSS color-pulse; repositioned per draw)
    this._metaDots = new Map();      // sid -> persistent 3-dot "model switching…" pulse div (mirrors the chat badge's .meta-dots; repositioned per draw)
    try {
      if (typeof document !== 'undefined' && document.head && !document.getElementById('tl-compact-css')) {
        const cst = document.createElement('style'); cst.id = 'tl-compact-css';
        // scaleX (transform-origin:left) from full → ~1px, compositor-accelerated; opacity fades the loop ends
        // so it never snaps. DUR 3.2s matches the old SMIL sweep. minW/innerW ≈ 1/46 ≈ 0.022.
        // background steps through the colormap gradient (--cmp4 widest = the map's high colour … --cmp0
        // narrowest = its 0% colour), set per-draw in _positionCompactBar from the kernel's cmapGrad, so the
        // scan-bar mirrors the context battery fill as it compresses. Falls back to the flat teal if unset.
        // (Changing the vars does NOT restart the animation — the persistent div keeps its compositor clock —
        // so this stays smooth, unlike the tab bar which is rebuilt each render.)
        cst.textContent = '.romp-tl-compact-bar{position:absolute;border-radius:1px;background:var(--cmp4,#14b8a6);'
          + 'pointer-events:none;transform-origin:left center;will-change:transform,opacity;'
          + 'animation:romp-tl-compact 3.2s linear infinite}'
          + '@keyframes romp-tl-compact{0%{transform:scaleX(1);opacity:0;background:var(--cmp4,#14b8a6)}'
          + '10%{transform:scaleX(1);opacity:1;background:var(--cmp4,#14b8a6)}'
          + '30%{background:var(--cmp3,#14b8a6)}50%{background:var(--cmp2,#14b8a6)}70%{background:var(--cmp1,#14b8a6)}'
          + '90%{transform:scaleX(0.022);opacity:1;background:var(--cmp0,#14b8a6)}'
          + '100%{transform:scaleX(0.022);opacity:0;background:var(--cmp0,#14b8a6)}}';
        document.head.appendChild(cst);
      }
      // WORKING-badge label pulse: black↔teal on the SAME 1.5s ease-in-out-sine clock as the chat chip's
      // .chip-pulse (the user 2026-07-01). A PERSISTENT overlay div the compositor breathes independently of
      // draw() — replaces the per-<text> SMIL <animate>, which stuttered/truncated because the SVG wipe
      // recreated it at the (irregular) redraw cadence. draw() only repositions it (see _positionWorkLabel).
      if (typeof document !== 'undefined' && document.head && !document.getElementById('tl-work-css')) {
        const wst = document.createElement('style'); wst.id = 'tl-work-css';
        wst.textContent = '.romp-tl-work-label{position:absolute;pointer-events:none;white-space:nowrap;'
          + 'font-weight:700;letter-spacing:0.03em;transform:translate(-50%,-50%);will-change:color;'
          + 'animation:romp-tl-workpulse 1.5s cubic-bezier(0.37,0,0.63,1) infinite}'
          + '@keyframes romp-tl-workpulse{0%,100%{color:#1a1a1a}50%{color:#0d9488}}';
        document.head.appendChild(wst);
      }
      // "Model switching…" dots (the user 2026-07-03): while a /model pick resolves, the lane shows three
      // pulsing accent-blue dots where the model name is — the SAME romp loader dot motif the chat badge
      // uses (.meta-dots), instead of just dimming the stale name. A PERSISTENT overlay div (like the work
      // label / compacting bar) so the pulse rides the compositor and never restarts on the SVG wipe.
      if (typeof document !== 'undefined' && document.head && !document.getElementById('tl-metadots-css')) {
        const dst = document.createElement('style'); dst.id = 'tl-metadots-css';
        dst.textContent = '.romp-tl-meta-dots{position:absolute;pointer-events:none;display:inline-flex;align-items:center;gap:3px;transform:translateY(-50%)}'
          + '.romp-tl-meta-dots i{width:4px;height:4px;border-radius:50%;background:' + ROMP_BLUE + ';display:inline-block;animation:romp-tl-metadots 1s ease-in-out infinite}'
          + '.romp-tl-meta-dots i:nth-child(2){animation-delay:0.16s}.romp-tl-meta-dots i:nth-child(3){animation-delay:0.32s}'
          + '@keyframes romp-tl-metadots{0%,70%,100%{opacity:0.3}35%{opacity:1}}';
        document.head.appendChild(dst);
      }
    } catch (e) {}

    // Click-safe redraws (the user 2026-06-24): the EXTERNAL redraw paths — the poll update() and the live-edge
    // _tickLive() (which rebuilds the SVG every animation frame while following now) — wipe and recreate every
    // SVG child (lane rows, bars, dots, hit-targets). A native `click` needs mousedown AND mouseup on the same
    // node; an external redraw between them destroys the pressed element and the click is dropped — the "had to
    // click it several times" bug. So while a pointer is pressed on the SVG we HOLD those external redraws
    // (exactly like the existing freeze-on-hover: buffer via _dirtyWhileTip, repaint the catch-up on release) —
    // and repaint AFTER the click has fired (setTimeout 0; click dispatches after pointerup). USER gestures
    // (pan / zoom / lane-reorder / touch) drive draw()/_scheduleDraw directly, NOT through update()/_tickLive,
    // so they are never held — panning stays live. Event-based (pointerdown/up), no time heuristic. CLAUDE.md.
    this._pointerHeld = false;
    this.svg.addEventListener('pointerdown', () => { this._pointerHeld = true; });
    // Click-drag to pan / reorder starts ONLY on EMPTY row space (the per-lane rowHit, below). Bars, dots and
    // the postal connectors keep their own click → jump-to-chat; a press on them does NOT start a drag.
    // While dragging, force the CLOSED-FIST (grabbing) cursor over the WHOLE plot: setting it on the svg
    // alone doesn't show, because a child under the pointer (rowHit's 'grab') wins — so override every
    // descendant with !important via a .tl-grabbing class on the wrap (the user 2026-06-26).
    try {
      if (typeof document !== 'undefined' && document.head && !document.getElementById('tl-grab-css')) {
        const gst = document.createElement('style'); gst.id = 'tl-grab-css';
        gst.textContent = '.tl-grabbing,.tl-grabbing *{cursor:grabbing!important}';
        document.head.appendChild(gst);
      }
    } catch (e) {}
    const _release = () => {
      if (!this._pointerHeld) return;
      this._pointerHeld = false;
      const tipUp = this.tip && this.tip.classList && this.tip.classList.contains('show');
      if (this._dirtyWhileTip && !tipUp) { this._dirtyWhileTip = false; setTimeout(() => { if (this.data) this.draw(); }, 0); }
    };
    window.addEventListener('pointerup', _release);
    window.addEventListener('pointercancel', _release);
    // The timeline is a THIN band: a press (or hover) begun inside it commonly ENDS OUTSIDE it — over the
    // chat/feed pane — so the window 'pointerup' / the tip's mouseleave never fire here and _pointerHeld (or a
    // shown tooltip) sticks true. update() then buffers EVERY push (the freeze path) and the lanes freeze on a
    // STALE frame — e.g. a since-revived session still reading 'not running' while this.data already says it's
    // live — because this.data updates silently but draw() never runs (the user 2026-06-25). Focus leaving the
    // iframe means no press/hover can still be live here, so blur is a reliable release proxy for the lost
    // pointerup AND a stuck tip: clear both and flush the buffered redraw. Event-based (blur genuinely fires),
    // not a timeout — there's no pointerup to wait for when it was released in another frame.
    window.addEventListener('blur', () => {
      if (this.tip && this.tip.classList && this.tip.classList.contains('show')) this.hideTip();
      _release();
    });

    // controls row BELOW the time axis. Layout (the user 2026-06-11): usage bars LEFT-justified,
    // then a flexible spacer, then RIGHT-justified "collapse idle gaps" with the 🔒 lock-to-now
    // toggle at the far right, under the lanes.
    this.controls = this.wrap.createDiv({ cls: 'romp-tl-controls' });
    this.controls.setAttribute('style', 'display:flex;align-items:center;gap:16px;padding:4px 8px;font-size:11px;color:#9aa0a6;user-select:none;');

    // (The restart-kernel ↻ button moved UP to the feed's top-right, next to the ⛭ settings gear (the
    // user 2026-06-17) — off the timeline's bottom-left, which now carries only the usage bars + the
    // right-justified gap/lock toggles. The gear was already there; the ↻ now sits beside it. The
    // 'romp:settings' contract still lives in ui/webview/settings.ts.)

    // Claude usage bars (the /usage rate-limit %: 5-hour + weekly), LEFT-justified. Hidden until
    // statusline.sh reports usage (Pro/Max only); _updateUsage() fills them each
    // poll from data.usage. The pct bar is color-coded green/amber/red; the reset countdown is in the
    // hover title.
    this._usageWrap = this.controls.createDiv();
    this._usageWrap.setAttribute('style', 'display:none;align-items:center;gap:14px;');
    this._usageBars = {};
    // Each window = a label + a column of TWO stacked mini-bars: the USAGE % (colored) over a
    // TIME-THROUGH-WINDOW bar (neutral slate — how far between the window's start = resets_at−winSec and
    // its reset). Comparing the two fill widths is the BURN-RATE cue: usage ahead of time = spending
    // faster than the window refills. The account-wide windows: 5h session, weekly, and the included
    // Fable 5 weekly allowance (the user 2026-07-02; CLI window type seven_day_overage_included).
    const mkUsageBar = (key, label, winSec) => {
      const g = this._usageWrap.createDiv();
      g.setAttribute('style', 'display:inline-flex;align-items:center;gap:6px;');
      const lab = g.createSpan({ text: label }); lab.setAttribute('style', 'opacity:0.85;');
      const col = g.createDiv(); col.setAttribute('style', 'display:flex;flex-direction:column;gap:3px;');
      const mkRow = (kindLabel, fillColor, txtOpacity) => {
        const row = col.createDiv(); row.setAttribute('style', 'display:inline-flex;align-items:center;gap:4px;');
        const kl = row.createSpan({ text: kindLabel }); kl.setAttribute('style', 'opacity:0.55;min-width:42px;');
        const track = row.createDiv();
        track.setAttribute('style', 'width:64px;height:6px;border-radius:3px;background:rgba(255,255,255,0.10);overflow:hidden;');
        const fill = track.createDiv();
        fill.setAttribute('style', 'height:100%;width:0%;border-radius:3px;background:' + fillColor + ';transition:width .3s ease;');
        const txt = row.createSpan({ text: '–' }); txt.setAttribute('style', 'min-width:30px;font-variant-numeric:tabular-nums;opacity:' + txtOpacity + ';');
        return { row, fill, txt };
      };
      const usage = mkRow('used', '#54B204', '0.9');       // % of the limit consumed — color set per-poll
      const time = mkRow('elapsed', '#6b7a8c', '0.55');    // % of the window elapsed — neutral slate (pace)
      this._usageBars[key] = { group: g, winSec, usage, time };
    };
    mkUsageBar('fiveHour', 'session', 5 * 3600);
    mkUsageBar('sevenDay', 'week', 7 * 86400);
    mkUsageBar('fable', 'Fable 5', 7 * 86400);

    // (The per-window token grid that used to sit here was removed at the user's request 2026-06-18 — only
    // the /usage rate-limit bars above remain. The kernel still ships data.tokens; nothing reads it now.)

    // spacer: everything after it sits flush right ("collapse idle gaps" moved to the Settings dialog's
    // Timeline section, the user 2026-06-25 — so the toolbar no longer hosts that checkbox).
    const ctlSpacer = this.controls.createDiv();
    ctlSpacer.setAttribute('style', 'flex:1 1 auto;');

    // Lock-to-now is NO LONGER a toolbar checkbox (the user 2026-06-26): it's a padlock ICON drawn at the
    // NOW-EDGE (bottom of the rightmost tick) in draw()/_drawLockToggle — accent-blue when locked, gray when
    // unlocked, click toggles. _setLock keeps _lockNow + the persisted state in sync; the icon is redrawn
    // each frame so there's no separate DOM element to keep current. (_lockBox/_lockIcon are gone; _setLock's
    // guards skip them.)

    // The tooltip ESCAPES the pane when it can (the user 2026-07-17): in the multi-pane web dashboard
    // the timeline iframe can be short (few lanes), and a pane-local tip clips at the iframe edge even
    // when the surrounding page has room. Adopt the topmost SAME-ORIGIN document as the tip's host, so
    // the tip overlays the sibling panes (chat, feed). Cross-origin parents (the VS Code webview host)
    // throw on access — the try/catch keeps the local document, and moveTip's viewport clamp still
    // prevents cut-off there. Obsidian has no frame parent, so it stays local too.
    this._tipWin = window;
    try { while (this._tipWin.parent && this._tipWin.parent !== this._tipWin && this._tipWin.parent.document) this._tipWin = this._tipWin.parent; } catch (e) { /* cross-origin boundary — host at the last same-origin window */ }
    const tipDoc = this._tipWin.document;
    if (tipDoc !== document && !tipDoc.getElementById('romp-tl-tip-css')) {
      // carry the tip's own styles over — the adopted host page doesn't load timeline-pane.css. Copied
      // from OUR stylesheets at adopt time (selector prefix match), so timeline-pane.css stays the one
      // source of truth and this can't drift.
      let css = '';
      try {
        for (const sh of document.styleSheets) {
          try { for (const r of sh.cssRules) if (r.selectorText && r.selectorText.indexOf('.romp-tl-tip') === 0) css += r.cssText + '\n'; } catch (e2) { /* foreign sheet */ }
        }
      } catch (e2) {}
      const st = tipDoc.createElement('style'); st.id = 'romp-tl-tip-css'; st.textContent = css;
      tipDoc.head.appendChild(st);
    }
    this.tip = tipDoc.createElement('div'); this.tip.className = 'romp-tl-tip'; tipDoc.body.appendChild(this.tip);
    // an iframe teardown (pane close/reload) must not orphan the tip in the adopted host document
    if (tipDoc !== document) window.addEventListener('pagehide', () => { try { this.tip.remove(); } catch (e) {} });
    this._tipOwner = null;   // the hit element that opened the current tip (see _onTipSweep)
    // The judging band (Debug) AND "collapse idle gaps" are both global romp:settings, toggled by the gear ⛭
    // in another same-origin iframe. React to that via the storage event so they apply live (no reload):
    // re-read collapseGaps, then repaint (draw() reads debug fresh).
    try {
      window.addEventListener('storage', (e) => {
        if (!e || e.key !== 'romp:settings') return;
        try { this._collapseGaps = JSON.parse(e.newValue || localStorage.getItem('romp:settings') || '{}').collapseGaps !== false; } catch (e2) {}
        this.draw();
      });
    } catch (e) {}

    // model/effort drop-down pickers: the open menu element + per-lane optimistic "pending" cues
    // ('sid:kind' → {was, until}) that dim a word until the tmux var actually flips (or 20s elapses).
    // _laneMenu = the per-lane GEAR drop-down (feed/postal/notify toggles — the user 2026-07-28).
    this._metaMenu = null; this._metaPending = {}; this._laneMenu = null;
    this._onDocClick = () => { this._closeMetaMenu(); this._closeLaneMenu(); };
    this._onDocKey = (e) => { if (e.key === 'Escape') { this._closeMetaMenu(); this._closeLaneMenu(); } };
    document.addEventListener('click', this._onDocClick);
    document.addEventListener('keydown', this._onDocKey);
    // The menus render in the tip's host document (see _menuHost), so a click or Escape landing on the
    // HOST page — which now shows the menu — must close them too. Same teardown discipline as the tip:
    // pagehide unhooks the host listeners and drops any open menu, so an iframe reload can't orphan either.
    if (tipDoc !== document) {
      tipDoc.addEventListener('click', this._onDocClick);
      tipDoc.addEventListener('keydown', this._onDocKey);
      window.addEventListener('pagehide', () => { try {
        tipDoc.removeEventListener('click', this._onDocClick);
        tipDoc.removeEventListener('keydown', this._onDocKey);
        this._closeMetaMenu(); this._closeLaneMenu();
      } catch (e) {} });
    }

    this._onResize = () => this.draw();
    this._onWheel = (e) => this.onWheel(e);
    window.addEventListener('resize', this._onResize);
    // A remote host dropping (or coming back) changes what the lanes MEAN, so redraw the disconnected
    // marks on that event — fired by federation.js only when the reachable set actually changes.
    this._onHosts = () => this.draw();
    window.addEventListener('romp-hosts', this._onHosts);
    // Trackpad gestures over the plot: two-finger horizontal scroll pans the offset, pinch/expand
    // zooms the window width (anchored at the cursor). Pinch reaches us as a ctrlKey wheel event in
    // Chromium/Electron. Non-passive so we can preventDefault.
    this.wrap.addEventListener('wheel', this._onWheel, { passive: false });
    // Touchscreen equivalents (phones/tablets, where there are no wheel events): one finger PANS — or,
    // when 🔒locked, ZOOMS with the right edge pinned at now; two fingers PINCH-zoom anchored at the
    // midpoint. Mirrors onWheel's math. touch-action:pan-y keeps vertical lane-scroll native while we own
    // horizontal + pinch and stop the browser from page-zooming the whole view. Touch never breaks 🔒.
    this._touch = null;
    this.wrap.style.touchAction = 'pan-y';
    this._onTouchStart = (e) => this.onTouchStart(e);
    this._onTouchMove = (e) => this.onTouchMove(e);
    this._onTouchEnd = (e) => this.onTouchEnd(e);
    this.wrap.addEventListener('touchstart', this._onTouchStart, { passive: false });
    this.wrap.addEventListener('touchmove', this._onTouchMove, { passive: false });
    this.wrap.addEventListener('touchend', this._onTouchEnd, { passive: false });
    this.wrap.addEventListener('touchcancel', this._onTouchEnd, { passive: false });
    // keyboard: ↑/↓ move a SELECTED lane cursor, Enter opens it in the chat. Scoped to the timeline
    // (wrap is focusable + focused on click) so arrows act only when the timeline has focus, not globally.
    this.selectedSid = null; this._vis = [];
    // vertical drag-to-reorder lanes: _drag holds the in-flight gesture, _dragOrder is the live
    // (transient) sid order draw() honors while dragging; on drop we write the full SID order to the
    // shared session-order.json (the chat tabs read+write the same file). _suppressClick stops the
    // mouseup-click from also firing a select after a real drag.
    this._drag = null; this._dragOrder = null; this._suppressClick = false;
    this._dag = null;   // request-DAG journey overlay (set by focusEvent when the feed supplies a dag)
    this._hover = null; // feed→timeline hover highlight {ids,...} (set by update from data.hover OR setHover; null = none)
    this._hoverNonce = null;  // highest hover nonce applied — gates the direct push vs the file poll so neither clobbers the other (the same monotonic nonce rides both; see setHover)
    this._frozeFromPin = false;  // freeze-on-hover: true while a tooltip has paused live-follow that WAS pinned (so hideTip knows to resume)
    this._dirtyWhileTip = false; // a data poll arrived while a tooltip was up (draw was skipped) → hideTip repaints the catch-up
    this._unfreezeTimer = null;  // deferred hideTip resume — cancelled by a quick glyph→glyph hover handoff
    this.wrap.tabIndex = 0; this.wrap.style.outline = 'none';
    this._onKey = (e) => this.onKey(e);
    // preventScroll: focusing on mousedown must NOT scroll the wrap into view — that scroll shifts the layout
    // BETWEEN mousedown and mouseup, so the click lands on a different element (or nothing) and is dropped. It
    // was the "first click only focuses, second click acts" bug on the % compact battery (the user 2026-06-29):
    // the first press focused (+ scrolled), eating the click; the second, already focused, landed.
    this._focusWrap = () => { try { this.wrap.focus({ preventScroll: true }); } catch (e) { this.wrap.focus(); } };
    this.wrap.addEventListener('keydown', this._onKey);
    this.wrap.addEventListener('mousedown', this._focusWrap);
    // SAFETY NET for a stuck tooltip: a hit's own mouseleave never fires if a redraw
    // (expand/collapse, a live update) pulls the element out from under a stationary
    // cursor — so the tip stays shown over empty timeline. On any move over the plot,
    // drop the tip once the cursor is no longer over the element that opened it (or
    // that element is gone). hideTip() on the owner's mouseleave still handles the
    // normal case; this only catches the orphaned ones.
    // The INVERSE net (the user 2026-07-21, message connectors): the same redraw that orphans a SHOWN tip
    // also swallows one that never got to open. draw() rebuilds the WHOLE svg, so the glyph under the cursor
    // is replaced by a fresh element — and a pointer that hasn't moved crosses no boundary, so the new
    // element's mouseenter never fires and the tooltip simply doesn't come up. On a busy fleet (a redraw per
    // push, plus the live tick sliding content) that reads as "connector tips don't appear immediately":
    // they only land if you happen to jiggle the mouse between rebuilds. Remember where the pointer is so
    // draw() can re-run the hover-in of whatever it just rebuilt underneath it (see _rehover).
    this._ptr = null;
    this._onTipSweep = (e) => {
      this._ptr = { x: e.clientX, y: e.clientY };
      if (!this.tip || !this.tip.classList.contains('show')) return;
      const o = this._tipOwner;
      // Owner removed by a redraw. Hiding alone left the tip DOWN until the next draw() re-armed it
      // (_rehover), which on an idle fleet is up to seconds away — the tip appeared, vanished, and only
      // came back if you held the cursor perfectly still (the user 2026-07-21). Rebind to whatever the
      // rebuild put under the cursor right now instead of waiting for that redraw.
      if (!o || !o.isConnected) { this.hideTip(); this._rehover(); return; }
      // Leave-test against the owner's HIT extent, not its bare geometry box. getBoundingClientRect on
      // an SVG path measures the path outline, and Chrome does not include the stroke — so a connector
      // for an immediately-delivered message, which is a straight vertical line, reports a box 0px WIDE.
      // Every sub-pixel horizontal twitch of a real hand then read as "pointer left the glyph" and hid
      // the tip, which only came back on the next redraw: appears, vanishes, and returns only if you
      // hold perfectly still (the user 2026-07-21). Firefox measures these boxes differently, which is
      // why it did not show the same flicker. Pad by half the stroke so the test matches the invisible
      // target the user is actually aiming at.
      const r = o.getBoundingClientRect();
      const pad = (parseFloat(o.getAttribute && o.getAttribute('stroke-width')) || 0) / 2;
      if (e.clientX < r.left - pad || e.clientX > r.right + pad || e.clientY < r.top - pad || e.clientY > r.bottom + pad) this.hideTip();
    };
    this.wrap.addEventListener('mousemove', this._onTipSweep);
    this._onPtrOut = () => { this._ptr = null; };   // cursor left the plot → nothing left to re-hover
    this.wrap.addEventListener('mouseleave', this._onPtrOut);

    // Show the romp loader FROM CONSTRUCTION (the user 2026-07-03, who reported nothing appearing in the timeline as it's
    // starting up, without the romp logo and the spinning things). draw() otherwise bails at its top on
    // null data (`if (!data...) return`) — so between the iframe (re)loading on a kernel restart and the first
    // {type:"data"} skeleton arriving, the pane was BLANK, no loader at all. The loader now goes up at once and
    // draw() clears it once the bars land; _armLoaderBackstop guards a never-arriving payload (CLAUDE.md).
    this._showLoader(true);
    this._armLoaderBackstop();
  }

  destroy() {
    window.removeEventListener('resize', this._onResize);
    if (this._onHosts) window.removeEventListener('romp-hosts', this._onHosts);
    if (this.wrap) { this.wrap.removeEventListener('wheel', this._onWheel); this.wrap.removeEventListener('keydown', this._onKey); this.wrap.removeEventListener('mousedown', this._focusWrap); this.wrap.removeEventListener('mousemove', this._onTipSweep); this.wrap.removeEventListener('mouseleave', this._onPtrOut);
      this.wrap.removeEventListener('touchstart', this._onTouchStart); this.wrap.removeEventListener('touchmove', this._onTouchMove); this.wrap.removeEventListener('touchend', this._onTouchEnd); this.wrap.removeEventListener('touchcancel', this._onTouchEnd); }
    if (this._drawRAF) cancelAnimationFrame(this._drawRAF);
    this._stopLiveTick();
    if (this._autoOpenT) clearTimeout(this._autoOpenT);
    if (this._unfreezeTimer) clearTimeout(this._unfreezeTimer);
    if (this._onDragMove) window.removeEventListener('mousemove', this._onDragMove, true);
    if (this._onDragUp) window.removeEventListener('mouseup', this._onDragUp, true);
    document.removeEventListener('click', this._onDocClick);
    document.removeEventListener('keydown', this._onDocKey);
    try {   // the host-document twins of the two closers above (menus render in the tip's host doc)
      if (this._tipWin && this._tipWin !== window) {
        this._tipWin.document.removeEventListener('click', this._onDocClick);
        this._tipWin.document.removeEventListener('keydown', this._onDocKey);
      }
    } catch (e) { /* host gone — its listeners went with it */ }
    this._closeMetaMenu();
    this._closeLaneMenu();
    if (this.tip) this.tip.remove();
  }

  // [r,g,b] of the surface the timeline sits on (Obsidian theme / VS Code panel bg), so the perceptual
  // fade blends toward the real background. Walks up for the first non-transparent bg; dark fallback.
  _surfaceBg() {
    try {
      let node = this.wrap;
      while (node) {
        const c = getComputedStyle(node).backgroundColor || '';
        const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
        if (m && (m[4] === undefined || parseFloat(m[4]) > 0.05)) return [+m[1], +m[2], +m[3]];
        node = node.parentElement;
      }
    } catch (e) {}
    return [24, 24, 24];
  }

  _font(b) { this._mc.font = (b ? '700 ' + BADGE_FS + 'px ' : '650 12px ') + FONT; }
  labelWidth(s) { this._font(false); return this._mc.measureText(s || '').width; }
  badgeWidth(s) { this._font(true); return this._mc.measureText(s || '').width; }
  ctxWidth(s) { this._mc.font = '600 11px ' + FONT; return this._mc.measureText(s || '').width; }

  // Number.isFinite (not != null): the min/max clamp passes NaN straight through, and a NaN window
  // makes every x() NaN — the whole plot vanishes. Non-finite → the same default as unset.
  winSec() { const w = Number.isFinite(this._winSec) ? this._winSec : Math.sqrt(MIN_W * MAX_W); return Math.round(Math.max(MIN_W, Math.min(MAX_W, w))); }
  offSec() { return Math.round(Math.max(0, Math.min(MAX_OFFSET, this._offSec || 0))); }   // 0 at right (now) … 72h at left

  // keyboard lane selection: ↑/↓ move the cursor AND auto-open that session in the chat WITHOUT
  // stealing focus (so you can keep arrowing through and previewing). Enter commits + focuses the chat.
  onKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); this.moveSelection(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.moveSelection(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); this.composeSelected(); }    // Enter → cursor into the prompt box
  }
  moveSelection(dir) {
    const vis = this._vis || [];
    if (!vis.length) return;
    let idx = vis.findIndex((s) => s.id === this.selectedSid);
    if (idx < 0) idx = dir > 0 ? -1 : vis.length;            // first press lands on the first/last lane
    idx = Math.max(0, Math.min(vis.length - 1, idx + dir));
    this.selectedSid = vis[idx].id;
    this.draw();
    // debounce the auto-open so holding/rapid arrows settle on the lane you land on (not every one
    // in between) — preview-only, focus stays on the timeline.
    if (this._autoOpenT) clearTimeout(this._autoOpenT);
    this._autoOpenT = setTimeout(() => { this._autoOpenT = null; this.openSelected(true); }, 120);
  }
  // a lane's LIVE transcript id = its most recent turn's tid (the current fork), falling back to the
  // lane sid. Opening with no anchor → the chat scrolls to the bottom (latest) of that transcript.
  _laneTid(s) {
    const turns = (this.data && this.data.turns && this.data.turns[s.id]) || [];
    const t = turns.length ? turns[turns.length - 1] : null;
    return (t && t.tid) || s.id;
  }
  openSelected(preserveFocus) {
    const s = (this._vis || []).find((x) => x.id === this.selectedSid);
    if (!s) return;
    this.openChat(this._laneTid(s), null, preserveFocus);   // switch → bottom (latest), no specific anchor
  }
  // Enter on the selected lane → open its tab (at bottom) and drop the cursor into the chat's message
  // box so you can type a message to that session. (Needs the chat composer enabled — vs_chat.)
  composeSelected() {
    const s = (this._vis || []).find((x) => x.id === this.selectedSid);
    if (!s) return;
    this.openChat(this._laneTid(s), null, false, true);
  }

  _scheduleDraw() {
    if (this._drawRAF) return;
    this._drawRAF = requestAnimationFrame(() => { this._drawRAF = null; this.draw(); });
  }

  // Trackpad gestures: horizontal two-finger scroll → pan (offset); pinch/expand → zoom the window width,
  // anchored on the REAL time under the cursor (_anchorOff), so hovering a thing and pinching expands INTO
  // it. Both write the continuous window/offset state and re-seat the slider thumbs, so the result persists
  // + redraws like a drag.
  onWheel(e) {
    const g = this._geom; if (!g || !this.data || !this.data.sessions) return;
    const pinch = e.ctrlKey;                                   // Chromium maps trackpad pinch → ctrl+wheel
    const horiz = Math.abs(e.deltaX) > Math.abs(e.deltaY);
    // wheel model (the user 2026-06-22): a plain VERTICAL wheel SCROLLS the panel up/down NATIVELY — we no
    // longer hijack it to zoom (that "expanded" the timeline, which the user didn't want). So zoom is now
    // PINCH (ctrl+wheel — trackpad pinch, or ctrl+wheel on a mouse), and a HORIZONTAL wheel (two-finger /
    // shift-wheel) PANS the time axis — EXCEPT when 🔒locked to now, where there's nowhere to pan, so the
    // horizontal wheel ZOOMS with the right edge pinned at now instead (the user 2026-06-22; mirrors the
    // locked touch-drag). Click-drag also pans — and BREAKS the lock; the wheel keeps it.
    if (!pinch && !horiz) return;                             // plain vertical → don't preventDefault, let it scroll
    const rect = this.svg.getBoundingClientRect();
    const scaleX = rect.width ? g.W / rect.width : 1;          // svg user-units per client px
    // Only pan/zoom when the cursor is over the PLOT, not the gutter that holds the lane name + feed/mail
    // toggles + model/effort pickers + status chip + context battery (the user 2026-06-27): a horizontal
    // scroll or pinch over those controls must not move the timeline. g.ml is the plot's left edge. Bail
    // WITHOUT preventDefault so the gesture falls through to the control / native, never to the timeline.
    if ((e.clientX - rect.left) * scaleX < g.ml) return;
    e.preventDefault();
    const curWin = this.winSec(), curOff = this.offSec();
    // Work in COMPRESSED time (the geom's mapping is LINEAR there). cT0 = window's left edge in
    // compressed seconds. Pan = translate at a CONSTANT compressed-sec-per-px scale → smooth, no rescale.
    const compress = g.compress || ((t) => t);
    const cNow = compress(this.data.now), cT1 = cNow - curOff, cT0 = cT1 - curWin;
    if (pinch) {
      const factor = Math.exp(e.deltaY * 0.01);                // deltaY>0 → wider window (zoom out); pinch is smooth
      const newWin = Math.max(MIN_W, Math.min(MAX_W, curWin * factor));
      const svgX = (e.clientX - rect.left) * scaleX;
      const frac = Math.max(0, Math.min(1, (svgX - g.ml) / g.plotW));   // cursor position across the plot
      // Pin the REAL time under the cursor, not its compressed coordinate: gap widths ride the window
      // (gapCT = winSec * GAP_FRAC), so the compressed axis RESCALES on every zoom step — see _anchorOff.
      const rc = (g.decompress || ((c) => c))(cT0 + frac * curWin);
      this._winSec = newWin;
      this._offSec = this._anchorOff(rc, frac, newWin);
    } else if (this._lockNow) {
      // 🔒 horizontal wheel → ZOOM (no pan possible — the right edge is pinned at now). Rightward (toward
      // now) zooms IN, leftward (toward the past) zooms OUT — same direction as the locked touch-drag.
      const factor = Math.exp(-e.deltaX * 0.01);               // deltaX<0 (toward past) → wider window (zoom out)
      this._winSec = Math.max(MIN_W, Math.min(MAX_W, curWin * factor));
    } else {
      const dt = e.deltaX * scaleX * (curWin / g.plotW);       // compressed-sec per px (CONSTANT → smooth pan)
      this._offSec = Math.max(0, Math.min(MAX_OFFSET, curOff - dt));
    }
    if (this._lockNow) this._offSec = 0;   // 🔒 the wheel HONORS the lock: zoom keeps the right edge at now (a DRAG breaks it)
    this._markOffsetGesture();   // honor this _offSec verbatim next frame; re-pin if it lands at the now-edge
    try { localStorage.setItem(this.WSTORE, String(this.winSec())); } catch (e2) {}
    try { localStorage.setItem(this.OSTORE, String(Math.round(this._offSec))); } catch (e2) {}
    this._scheduleDraw();
    this._startLiveTick();   // a pan back to the now-edge re-pins → resume smooth advance (no-op otherwise)
  }

  // The offset that puts REAL time `rc` back at fraction `frac` across the plot, for a window `newWin`
  // wide. Every zoom anchor goes through here (wheel pinch + touch pinch), because the compressed axis is
  // NOT a fixed coordinate system: a collapsed idle gap is `gapCT = winSec * GAP_FRAC` wide, so changing
  // the window rescales every gap, and _buildCompressMap anchors its origin at the FIRST collapsed gap —
  // near the start of history. Pinning the anchor in compressed seconds (what this did before) therefore
  // pinned it against that far-left origin: the zoom's real fixed point sat near the beginning of the
  // timeline and the hovered instant slid away, worse with every step (the user 2026-07-21, who observed it looked like
  // it was fixing the leftmost coordinate). So we convert to real time, then re-derive the offset under the
  // map the NEXT draw will build (same inputs as draw(): the live now + the new window's gap width).
  _anchorOff(rc, frac, newWin) {
    const nowS = this._liveNow();
    const cmap = this._collapseGaps && this.data
      ? this._buildCompressMap(this.data.turns || {}, newWin * GAP_FRAC, nowS) : null;
    const compress = cmap ? cmap.compress : (t) => t;
    return Math.max(0, Math.min(MAX_OFFSET, compress(nowS) - (compress(rc) + (1 - frac) * newWin)));
  }

  _touchDist(a, b) { return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }

  // Touchscreen pan/zoom (phones — no wheel events). ONE finger, horizontal: PAN the window (free, breaks
  // 🔒 like a mouse drag) — content tracks the finger (drag right → earlier time slides in). When 🔒locked
  // a horizontal drag ZOOMS instead (the right edge stays pinned at now, so there's nowhere to pan) — this
  // is the user's rule that a locked-to-now drag does a zoom. ONE finger, vertical: falls through to native lane
  // scroll (touch-action:pan-y), and a tap with no movement falls through to the lane's click/select. TWO
  // fingers: PINCH-zoom the window width, anchored at the REAL time under the midpoint (_anchorOff, shared
  // with the wheel). Panning stays in COMPRESSED time, where the mapping is linear, so a pan is a pure
  // translate. _winSec/_offSec are the same continuous state the wheel + sliders write.
  onTouchStart(e) {
    const g = this._geom; if (!g || !this.data || !this.data.sessions) return;
    if (e.touches.length >= 2) {
      const a = e.touches[0], b = e.touches[1];
      const rect = this.svg.getBoundingClientRect();
      const scaleX = rect.width ? g.W / rect.width : 1;            // svg user-units per client px
      const curWin = this.winSec(), curOff = this.offSec();
      const compress = g.compress || ((t) => t);
      const cNow = compress(this.data.now), cT1 = cNow - curOff, cT0 = cT1 - curWin;
      const svgX = ((a.clientX + b.clientX) / 2 - rect.left) * scaleX;   // midpoint across the plot
      const frac = Math.max(0, Math.min(1, (svgX - g.ml) / g.plotW));
      // pin the REAL time under the midpoint (see _anchorOff): the anchor is captured ONCE here and reused
      // for every move, so a compressed-coordinate anchor drifts harder here than on the trackpad — the
      // window changes by a large factor across one gesture, rescaling the axis under a fixed number.
      this._touch = { mode: 'pinch', startDist: Math.max(1, this._touchDist(a, b)), frac,
        rc: (g.decompress || ((c) => c))(cT0 + frac * curWin), startWin: curWin };
      e.preventDefault();
    } else if (e.touches.length === 1) {
      const t = e.touches[0];
      this._touch = { mode: 'drag', axis: null, startX: t.clientX, startY: t.clientY,
        startOff: this.offSec(), startWin: this.winSec(), locked: this._lockNow };
      // no preventDefault yet — the first real move decides ours (horizontal) vs. native (vertical scroll)
    }
  }
  onTouchMove(e) {
    const d = this._touch, g = this._geom; if (!d || !g || !g.plotW || !this.data) return;
    const rect = this.svg.getBoundingClientRect();
    const scaleX = rect.width ? g.W / rect.width : 1;
    if (d.mode === 'pinch') {
      if (e.touches.length < 2) return;
      const dist = Math.max(1, this._touchDist(e.touches[0], e.touches[1]));
      const newWin = Math.max(MIN_W, Math.min(MAX_W, d.startWin * (d.startDist / dist)));   // spread → narrower window (zoom in)
      this._winSec = newWin;
      this._offSec = this._anchorOff(d.rc, d.frac, newWin);
      // …and CLAIM the offset (as the wheel does): without this the next draw either forces off=0 (still
      // pinned to now) or re-derives it from _holdReal to hold the right edge, discarding the anchor we
      // just computed — the mid-gesture frames ignored the midpoint entirely.
      this._markOffsetGesture();
      this._scheduleDraw();
      e.preventDefault();
    } else if (d.mode === 'drag') {
      const dx = e.touches[0].clientX - d.startX, dy = e.touches[0].clientY - d.startY;
      if (d.axis == null) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 6) return;        // below threshold → still undecided (could be a tap)
        d.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      }
      if (d.axis === 'y') return;                                     // vertical → leave it to native lane scroll
      const dxc = dx * scaleX;                                        // client px → svg user-units
      if (d.locked) {                                                 // 🔒 → a horizontal drag ZOOMS, right edge stays at now
        this._winSec = Math.max(MIN_W, Math.min(MAX_W, d.startWin * Math.exp(-dxc / g.plotW)));   // drag right → zoom in
        this._offSec = 0;
      } else {                                                        // free PAN — content tracks the finger; breaks 🔒
        const dt = dxc * (d.startWin / g.plotW);
        this._offSec = Math.max(0, Math.min(MAX_OFFSET, d.startOff + dt));   // drag right → window slides to earlier time
        this._setLock(false);
        this._pinned = false;
      }
      this._offDirty = true;
      this._scheduleDraw();
      e.preventDefault();
    }
  }
  onTouchEnd(e) {
    const d = this._touch; if (!d) return;
    if (e.touches && e.touches.length >= 1) {                         // a finger lifted but one remains (pinch→drag): rebaseline
      const t = e.touches[0];
      this._touch = { mode: 'drag', axis: null, startX: t.clientX, startY: t.clientY,
        startOff: this.offSec(), startWin: this.winSec(), locked: this._lockNow };
      return;
    }
    this._touch = null;
    if (d.mode === 'drag' && d.axis !== 'x') return;                  // a native scroll or a tap — nothing of ours to persist
    if (this._lockNow) this._offSec = 0;                              // a 🔒locked zoom keeps the right edge at now
    this._markOffsetGesture();                                        // honor _offSec next frame; re-pin if it landed at the now-edge
    try { localStorage.setItem(this.WSTORE, String(this.winSec())); } catch (e2) {}
    try { localStorage.setItem(this.OSTORE, String(Math.round(this._offSec))); } catch (e2) {}
    this._scheduleDraw();
    this._startLiveTick();
  }

  // A user gesture/nav just wrote _offSec → draw() honors it verbatim this frame (_offDirty), then
  // resumes holding the absolute position. RE-PIN (resume live-follow) when the right edge lands within
  // ~6px of the now-edge, so panning the whole way back to the right turns auto-scroll back on.
  _markOffsetGesture() {
    const g = this._geom;
    const pinEps = (g && g.plotW) ? Math.max(2, 6 * this.winSec() / g.plotW) : 2;   // ~6px of the right edge, in compressed sec
    this._pinned = this.offSec() <= pinEps;
    this._offDirty = true;
  }

  // Programmatically toggle 🔒 lock-to-now, keeping the checkbox + icon + persisted state in sync. A
  // click-drag uses this to BREAK the lock (the user 2026-06-13: drag away from now = free pan).
  _setLock(on) {
    on = !!on;
    if (this._lockNow === on) return;
    this._lockNow = on;
    // The padlock is drawn in the SVG each frame (_drawLockToggle) — no DOM element to sync; just persist.
    try { localStorage.setItem(this.LSTORE, on ? '1' : '0'); } catch (e) {}
  }

  // Far-right ⟩⟩ button (see _drawNowButton): snap the window all the way to the live edge and resume
  // auto-scroll. Only reachable while unpinned (the button hides at the edge).
  _jumpToNow() {
    this._pinned = true; this._offSec = 0; this._offDirty = true;
    this._holdReal = this.data ? this.data.now : null;
    try { localStorage.setItem(this.OSTORE, '0'); } catch (e) {}
    this.draw();
    this._startLiveTick();   // resume the smooth-advance loop now that we're following the edge again
  }

  // --- smooth live-edge advance (see interpNow + the constructor field comment) ---
  // Are we auto-scrolling the live edge right now? (lock forces follow; a hover-freeze or a pan stops it.)
  _liveFollowing() { return (this._pinned || this._lockNow) && !this._frozeFromPin; }
  // On-screen? rAF already pauses for a hidden TAB; this also catches a hidden Obsidian leaf / detached
  // node (no offsetParent) so the loop doesn't spin for an invisible pane. False-negative just degrades
  // to per-poll redraw (no interpolation), never breaks.
  _isVisible() {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return false;
    const w = this.wrap;
    return !!(w && w.offsetParent !== null);
  }
  // The effective `now` draw() renders the right edge at: data.now plus wall-clock since that poll while
  // live-following, else the raw data.now (a held/frozen view must NOT creep as time passes).
  _liveNow() {
    const base = (this._nowBaseSec != null) ? this._nowBaseSec : (this.data ? this.data.now : 0);
    // Frozen on hover → hold EVERYTHING at the hover instant (_holdReal): the axis edge AND, via barEndT /
    // execAt / startAt which read this, the open work bars + pending items. Otherwise they keep advancing
    // per poll while the edge sits still — the "doesn't stop on hover" bug. Not frozen → glide, or data.now.
    if (this._frozeFromPin && this._holdReal != null) return this._holdReal;
    return interpNow(base, this._nowBaseMs, perfNow(), this._liveFollowing(), MAX_INTERP_AHEAD);
  }
  // Arm the rAF loop (no-op if already running or not currently live+visible). Re-armed each poll by
  // update(), so even after the loop self-stops it returns within one poll once we're live again. NOT
  // called from draw() — draw() runs inside the tick, and re-arming there would double the loop.
  _startLiveTick() {
    if (this._liveRAF != null || !this._liveFollowing() || !this._isVisible()) return;
    this._liveRAF = requestAnimationFrame(() => this._tickLive());
  }
  _tickLive() {
    this._liveRAF = null;
    if (!this._liveFollowing() || !this._isVisible() || !this.data) return;   // gate closed → stop the loop
    // Click-safe: don't rebuild the SVG under a pressed pointer (a click in progress) — skip this frame's draw
    // but keep the loop alive so the edge resumes gliding the moment the pointer releases. See the constructor.
    if (this._pointerHeld) { this._liveRAF = requestAnimationFrame(() => this._tickLive()); return; }
    const g = this._geom;
    // Only repaint when the edge would actually move ≥ LIVE_MIN_PX since the last live draw — a wide
    // (zoomed-out) window where the edge barely creeps costs ~nothing, a zoomed-in one repaints every
    // native frame. Keep looping either way so we catch the moment it does move.
    if (!g || this._lastLiveNow == null || ((this._liveNow() - this._lastLiveNow) / g.winSec * g.plotW) >= LIVE_MIN_PX) {
      this.draw();
    }
    this._liveRAF = requestAnimationFrame(() => this._tickLive());
  }
  _stopLiveTick() { if (this._liveRAF != null) { cancelAnimationFrame(this._liveRAF); this._liveRAF = null; } }

  // (The restart ↻ handler moved to the feed's top-right gear (the kernel's _GEAR_JS) along with the
  // button — the user 2026-06-17. It POSTs the same /restart, polls /healthz, and reloads, as before.)

  // (The settings gear + its modal moved to the feed's top-right ⛭ — the user 2026-06-16. The timeline
  // no longer hosts settings; ui/webview/settings.ts still owns the 'romp:settings' contract
  // and the chat applies it live via the storage event the feed gear fires.)

  // A small vertical ⟩⟩ button hugging the far-right edge, shown ONLY when the view is held back off the
  // live edge (unpinned). Click → _jumpToNow (snap to `now` + resume live-follow). Drawn last so it sits
  // on top; pointer-events on the group only (the rest of the row stays clickable underneath elsewhere).
  _drawNowButton(svg) {
    const g = this._geom; if (!g) return;
    const bw = 16, bh = 46;
    const bx = g.W - this.M.right - bw + 5;            // hug the right edge, slight overhang into the margin
    const axisY = g.H - this.M.bottom;
    const by = g.top + ((axisY - g.top) - bh) / 2;     // vertically centered in the plot band
    const grp = el('g', {}); grp.style.cursor = 'pointer';
    grp.appendChild(el('rect', { x: bx, y: by, width: bw, height: bh, rx: 5, fill: '#1b1d22', 'fill-opacity': 0.82, stroke: '#ffffff', 'stroke-opacity': 0.45, 'stroke-width': 1 }));
    const cx = bx + bw / 2, cy = by + bh / 2;
    const chev = (ox) => 'M ' + (cx - 3 + ox) + ' ' + (cy - 5) + ' L ' + (cx + 3 + ox) + ' ' + cy + ' L ' + (cx - 3 + ox) + ' ' + (cy + 5);
    grp.appendChild(el('path', { d: chev(-2.5) + ' ' + chev(2.5), fill: 'none', stroke: '#ffffff', 'stroke-opacity': 0.9, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    const ttl = el('title', {}); ttl.textContent = 'Jump to now · resume live'; grp.appendChild(ttl);
    grp.addEventListener('click', (ev) => { ev.stopPropagation(); this._jumpToNow(); });
    svg.appendChild(grp);
  }

  // Fill the usage bars from data.usage (Claude /usage rate-limit %). Hidden when absent (not Pro/Max,
  // or nothing reported yet). A window past its resets_at has rolled over → show 0 until the next write.
  _updateUsage(usage) {
    // The usage bars belong to the HOST'S chrome, not this pane: the web shell renders them in the left
    // rail, the VS Code extension in the status-bar item's menu/tooltip. Prefer the explicit host hook
    // (__rompForwardUsage, installed by the VS Code boot — a webview's window.parent is VS Code's opaque
    // wrapper, so the parent postMessage below would vanish); else forward to the shell iframe parent.
    // Standalone (Obsidian / no parent frame) keeps rendering them in the toolbar as before.
    if (typeof window !== 'undefined' && typeof window.__rompForwardUsage === 'function') {
      try { window.__rompForwardUsage(usage || null); } catch (e) {}
      if (this._usageWrap) this._usageWrap.style.display = 'none';
      return;
    }
    if (typeof window !== 'undefined' && window.parent && window.parent !== window) {
      try { window.parent.postMessage({ romp: 'usage', usage: usage || null }, '*'); } catch (e) {}
      if (this._usageWrap) this._usageWrap.style.display = 'none';
      return;
    }
    if (!this._usageWrap) return;
    if (!usage || (!usage.fiveHour && !usage.sevenDay && !usage.fable)) { this._usageWrap.style.display = 'none'; return; }
    this._usageWrap.style.display = 'flex';
    const nowS = (typeof Date !== 'undefined' && Date.now) ? Math.floor(Date.now() / 1000) : 0;
    const apply = (key, seg, name) => {
      const b = this._usageBars[key]; if (!b) return;
      if (!seg) { b.group.style.display = 'none'; return; }
      b.group.style.display = 'inline-flex';
      // A ROLLED window (its reset passed since this reading was taken) is UNKNOWN, not 0 — the reading
      // describes a window that has already ended (the user 2026-07-31: a machine with no live session to
      // ask sits on a stale snapshot, and a confident 0% is indistinguishable from a genuinely idle
      // account). Last-known fill stays, FADED, and the readout is '?'; the title dates the gap.
      const rolled = !!(seg.resetsAt && nowS > seg.resetsAt);
      const pct = Math.max(0, Math.min(100, seg.pct || 0));
      // No BAR at all when the window is unknown (the user 2026-07-31, round 2): a fill of any length —
      // even faded — asserts a value we do not have. The track is hidden and the readout is '?'; the
      // last-known number stays in the hover title, said in words.
      const track = b.usage.fill.parentElement;
      if (track) track.style.display = rolled ? 'none' : '';
      b.usage.fill.style.width = pct + '%';
      b.usage.fill.style.background = pct >= 90 ? '#c0392b' : (pct >= 70 ? '#e0b020' : '#54B204');
      b.usage.txt.textContent = rolled ? '?' : pct + '%';
      // TIME through the window: 0% at the window start (resets_at − winSec), 100% at the reset. Meaningless
      // once the window has rolled — that pace would describe a window nobody is in any more.
      let timePct = null;
      if (!rolled && seg.resetsAt && b.winSec) timePct = Math.max(0, Math.min(100, Math.round((nowS - (seg.resetsAt - b.winSec)) / b.winSec * 100)));
      b.time.row.style.display = (timePct == null) ? 'none' : 'inline-flex';
      if (timePct != null) { b.time.fill.style.width = timePct + '%'; b.time.txt.textContent = timePct + '%'; }
      b.group.setAttribute('title', rolled
        ? name + ' — window reset ' + this._fmtAgo(seg.resetsAt) + ' and no reading has arrived since; current usage unknown (last known ' + pct + '%)'
        : name + ' — usage ' + pct + '%' + (timePct != null ? ' · ' + timePct + '% through the window' : '') + (seg.resetsAt ? ' · resets in ' + this._fmtReset(seg.resetsAt) : ''));
    };
    apply('fiveHour', usage.fiveHour, 'Session (5h)');
    apply('sevenDay', usage.sevenDay, 'Weekly');
    apply('fable', usage.fable, 'Fable 5 (7d)');
  }
  // Compact "2d 3h 14m ago" for a reset that has already passed — how stale an UNKNOWN window's
  // last reading is (the '?' bars above).
  _fmtAgo(epoch) {
    const nowS = (typeof Date !== 'undefined' && Date.now) ? Math.floor(Date.now() / 1000) : 0;
    let dt = Math.max(0, nowS - epoch);
    const d = Math.floor(dt / 86400); dt -= d * 86400;
    const h = Math.floor(dt / 3600); dt -= h * 3600;
    const m = Math.floor(dt / 60);
    return ((d ? d + 'd ' : '') + ((h || d) ? h + 'h ' : '') + m + 'm').trim() + ' ago';
  }
  // Compact "2d 3h 14m" countdown to a reset epoch, for the usage-bar hover title.
  _fmtReset(epoch) {
    const nowS = (typeof Date !== 'undefined' && Date.now) ? Math.floor(Date.now() / 1000) : 0;
    let dt = epoch - nowS; if (dt <= 0) return 'soon';
    const d = Math.floor(dt / 86400); dt -= d * 86400;
    const h = Math.floor(dt / 3600); dt -= h * 3600;
    const m = Math.floor(dt / 60);
    return (d ? d + 'd ' : '') + (h || d ? h + 'h ' : '') + m + 'm';
  }


  // Returns whether it actually fitted — callers latch `fitted` ONLY on true. Without a clock sample
  // (data.now missing: a remote-first federation merge before the local snapshot) the math is NaN, and
  // a latched NaN window blanked the plot for the page's lifetime (the Chrome stub-lines bug, 2026-07-15).
  fitWindow() {
    if (!Number.isFinite(this.data && this.data.now)) return false;
    let e = this.data.now;
    this.data.messages.forEach((m) => { if (m.sent) e = Math.min(e, m.sent); });
    Object.values(this.data.turns).forEach((ts) => ts.forEach((t) => { if (t.start) e = Math.min(e, t.start); }));
    this._winSec = Math.min(12 * 3600, Math.max(3600, Math.round((this.data.now - e) * 1.15)));
    return true;
  }

  // Tell the shell the dashboard has first content so it can drop the boot splash (the user 2026-06-26).
  // The timeline lanes render first (no parse), so this is the earliest "something's on screen" signal.
  _signalReady() {
    if (this._readySent) return;
    this._readySent = true;
    try { if (window.parent && window.parent !== window) window.parent.postMessage({ romp: 'ready' }, '*'); } catch (e) {}
  }

  update(data) {
    if (!data || data.unavailable || !data.sessions) { this.data = data; this.drawMessage(data && data.unavailable ? 'Timeline needs a desktop Obsidian with tmux.' : 'No romp activity.'); this._signalReady(); return; }
    const _only = _rompOnlyTag();   // demo/recording view filter: keep only matching-name lanes (the user 2026-07-14)
    if (_only) data = Object.assign({}, data, { sessions: data.sessions.filter((s) => _rompMatchesOnly(s.name, _only)) });
    // The kernel ships the timeline as TWO messages (the user 2026-06-25): {type:"data"} carries the LANES
    // SKELETON (sessions/status/tokens, no turns/judging/messages/nudges) and a following {type:"bars"} carries
    // the heavy detail (applyBars). Carry the last-known detail across a skeleton-only update so the bars don't
    // blink out every push. A full data object (the test harness, or an older one-shot) keeps its own bars.
    // A FULL data object (the test harness / an older one-shot) carries its own turns, so the bars are
    // already present → no loader. The two-message path leaves turns empty here; the loader shows until
    // applyBars lands. Read the RAW turns BEFORE the prev-carry below back-fills them.
    if (data.turns && Object.keys(data.turns).length) this._barsLoaded = true;
    const prev = this.data;
    if (prev && (!data.turns || !Object.keys(data.turns).length)) {
      data.turns = prev.turns || {}; data.judging = prev.judging || [];
      data.messages = prev.messages || [];
    }
    this.data = data;
    if (data.cmapGrad) this._cmapGrad = data.cmapGrad;   // compaction-sweep colormap gradient (persists across the lighter {type:bars} pushes)
    this._reconcilePendingFlags();   // hold an optimistic eye-toggle sticky until THIS push (or a later one) confirms it
    this._reconcileDismissed();      // ...and a Cleared dead lane, so a stale/merged push can't pop it back
    this._signalReady();             // first lanes are about to paint → let the shell drop the boot splash
    // Live-edge baseline: the edge free-runs off a FIXED anchor and each poll rebases it MONOTONICALLY
    // (reanchorEdge) — it catches up forward when behind but NEVER moves backward, so bursty/jittery/
    // re-emitted pushes can't snap the axis around (the user 2026-07-03, who saw it jump forward then back). A
    // RE-EMITTED payload (older data.now — federation/cache) is also clamped to the newest sample seen so
    // the bars/positions it drives don't regress (isFreshNowSample). When held/frozen (not live-following),
    // `off` cancels the edge's position, so we just keep data.now fresh for off-screen pending items.
    if (isFreshNowSample(this._newestNow, data.now)) this._newestNow = data.now;
    else if (this._newestNow != null) data.now = this._newestNow;
    const _live = this._liveFollowing(), _tMs = perfNow();
    if (_live) {
      const _a = reanchorEdge(this._nowBaseSec, this._nowBaseMs, _tMs, data.now, this._wasLive);
      this._nowBaseSec = _a.baseSec; this._nowBaseMs = _a.baseMs;
    } else {
      this._nowBaseSec = data.now; this._nowBaseMs = _tMs;
    }
    this._wasLive = _live;
    if (!this.fitted && Object.keys(this.data.turns || {}).length && this.fitWindow()) this.fitted = true;   // fit once bars exist (a skeleton-only first paint waits for applyBars); no latch without a clock sample
    // first paint with a chat already open → seed the highlight from it (don't override a later local pick)
    if (this.selectedSid == null) { const sid = this._sidForActiveChat(data.activeChat); if (sid) this.selectedSid = sid; }
    // Feed→timeline HOVER from the FILE (timeline-hover.json — the cross-front-end broadcast channel,
    // also read by VS Code trackchanges + the Obsidian vault). data.hover.ids = the subtree's events +
    // delegation messages ([] = cleared). The SAME monotonic nonce rides both this file and the direct
    // setHover push (server.ts pushHover), so gate on it. Apply ONLY when data.hover actually carries a
    // hover with a nonce — a periodic push with hover:null/absent must NOT clear a highlight set by the
    // direct pushHover, or a feed-card hover blinks out ~0.5s later when the next poll lands (the user
    // 2026-06-27). A newer (or equal) file nonce updates/clears; an older one is ignored. The highlight
    // persists until an explicit clear (mouseleave → showAskPath off → direct push [] ). [[contract with vs_chat]]
    if (data.hover && typeof data.hover.nonce === 'number') {
      const _hvN = data.hover.nonce;
      if (this._hoverNonce == null || _hvN >= this._hoverNonce) {
        this._hover = (data.hover.ids && data.hover.ids.length) ? data.hover : null;
        this._hoverNonce = _hvN;
      }
    }
    // DAG overlay is DERIVED state: re-synced from the current focus file on EVERY poll, so it clears
    // the instant the feed clears/replaces the focus's dag — even when no fresh focusEvent fires (e.g.
    // the double-clicked card is un-highlighted). The nonce-gated focusEvent below only does the JUMP.
    this._dag = this._dagFromFocus(data.focus);
    this._updateUsage(data.usage);   // Claude /usage rate-limit bars in the controls row (HTML, outside the SVG)
    // STILL SNAPSHOT while a tooltip is up (the user 2026-06-13): the data + derived state above are
    // buffered, but DON'T re-lay-out the SVG — a fresh layout (new events, recompressed idle gaps) shifts
    // every x-position = the jump the user saw under the held edge. Keep the last frame; hideTip repaints
    // the buffered data as ONE catch-up. (Also skips the focus-jump + live-tick below — both move the view.)
    // Hold the SVG layout while it's deliberately frozen: a tooltip is up (freeze-on-hover) OR a pointer is
    // pressed (a click in progress — click-safe, see the constructor). Buffer the data; repaint the catch-up
    // when the hold ends (hideTip / pointer release). Skips the focus-jump + live-tick below — both move the view.
    if ((this.tip && this.tip.classList && this.tip.classList.contains('show')) || this._pointerHeld) { this._dirtyWhileTip = true; return; }
    this.draw();
    // feed→timeline locate: a NEW focus nonce (update_feed wrote timeline-focus.json on a card click)
    // → pan/scroll/pulse to that event. Adopt the nonce silently on first load (don't jump to a stale
    // file); only a CHANGE fires. Guarded so the 1s/3s poll re-reading the same file never re-fires.
    if (data.focus && data.focus.nonce != null) {
      if (this._focusNonce === undefined) this._focusNonce = data.focus.nonce;
      else if (data.focus.nonce !== this._focusNonce) { this._focusNonce = data.focus.nonce; this.focusEvent(data.focus); }
    }
    if (this._barsLoaded) this._startLiveTick();   // re-arm the smooth-advance loop each poll (skip while the loader is up — draw() no-ops then)
  }

  // The heavy second half of a timeline push (the user 2026-06-25): the per-segment work BARS + the judging
  // band + message connectors + nudge marks — ~95% of the payload, deferred so the lanes (update()) paint
  // first. The skeleton always lands first (TCP-ordered on the one socket), so this.data.sessions is set.
  applyBars(m) {
    if (!m || !this.data || !this.data.sessions) return;
    this.data.turns = m.turns || {};
    this.data.judging = m.judging || [];
    this.data.messages = m.messages || [];
    // (nudges array retired 2026-07-07 payload audit: auto-nudges render from the bar's nudgeAuto)
    // Keep the romp loader up through the COLD warm-up rather than flashing "no romp activity" (the user
    // 2026-07-03: on restart the timeline went straight to the empty message instead of the spinning
    // logo). The kernel's live-first build is PARTIAL (m.warming) — on a cold connect the SDK backend and
    // any attached remote may not be merged yet, so it can land with zero lanes/turns. Only a payload with
    // real content — a live lane or any turn — or a SETTLED (non-warming) build finalizes the load; a
    // warming-and-empty one leaves _barsLoaded false so draw() keeps showing the loader. A backstop timer
    // drops it regardless, so a genuinely-empty fleet can never trap the loader (CLAUDE.md loader rule).
    const hasContent = Object.keys(this.data.turns).some((k) => (this.data.turns[k] || []).length)
      || (this.data.sessions || []).some((s) => s.live);
    if (!(m && m.warming) || hasContent) {
      this._barsLoaded = true;
      if (this._loaderBackstop != null) { clearTimeout(this._loaderBackstop); this._loaderBackstop = null; }
    } else {
      this._armLoaderBackstop();
    }
    // Adopt m.now only when it is a fresh clock sample — a cached/merged bars payload re-serves an OLDER
    // now, and regressing data.now here walked the axis backward between polls (see isFreshNowSample).
    if (typeof m.now === 'number') {
      if (isFreshNowSample(this._newestNow, m.now)) this._newestNow = m.now;
      this.data.now = (this._newestNow != null) ? this._newestNow : m.now;
    }
    if (!this.fitted && Object.keys(this.data.turns).length && this.fitWindow()) this.fitted = true;   // no latch without a clock sample (see fitWindow)
    // honor the same freeze-on-hover / click-hold guard update() uses (don't relayout under a held pointer/tip)
    if ((this.tip && this.tip.classList && this.tip.classList.contains('show')) || this._pointerHeld) { this._dirtyWhileTip = true; return; }
    this.draw();
    this._startLiveTick();   // bars are up now → resume the smooth-advance loop (gated off while the loader showed)
  }

  // Direct hover push from the kernel (server.ts pushHover) — the FAST path that skips the
  // timeline-hover.json write → fs.watch → full rebuild that otherwise made modal/chat hover lag
  // behind the (instant, ws-pushed) chat glow. m = {ids: string[]|null, nonce}. The SAME hover also
  // lands in the file (the cross-front-end broadcast), carrying the SAME monotonic nonce — so honor
  // max-nonce: a following data-poll reading the file ties (same nonce, no-op) and any stale/out-of-
  // order push (lower nonce) is ignored. Redraws ONLY (no update()/rebuild). nonce absent → always apply.
  setHover(m) {
    if (!m) return;
    const nonce = (typeof m.nonce === 'number') ? m.nonce : null;
    if (nonce != null && this._hoverNonce != null && nonce < this._hoverNonce) return;   // stale → ignore
    if (nonce != null) this._hoverNonce = nonce;
    this._hover = (m.ids && m.ids.length) ? { ids: m.ids } : null;
    this._scheduleDraw();
  }

  // resolve the chat's active tab {tid,name} to a lane sid: precise by transcript id (a lane's turn
  // carries that tid), else by name. null if no lane matches.
  _sidForActiveChat(ac) {
    if (!ac || !this.data || !this.data.sessions) return null;
    const turns = this.data.turns || {};
    if (ac.tid) {
      for (const s of this.data.sessions) { if ((turns[s.id] || []).some((t) => t.tid === ac.tid)) return s.id; }
    }
    if (ac.name) { const s = this.data.sessions.find((x) => x.name === ac.name); if (s) return s.id; }
    return null;
  }

  // set the single selection highlight + redraw only on a real change.
  _select(sid) { if (sid && this.selectedSid !== sid) { this.selectedSid = sid; this.draw(); } }

  // Reverse hover: a glyph hover tells the host to light the matching feed card + glow the chat turns
  // in [t0,t1] (the host has the receivers; web kernel only — no-op in Obsidian). sid null → clear.
  _emitHover(sid, segIds, t0, t1) {
    try { if (typeof window !== 'undefined' && typeof window.__rompTimelineHover === 'function') window.__rompTimelineHover(sid, segIds, t0, t1); } catch (e) {}
  }

  // The chat published a new active tab (host fs-watches chat-active and pushes this instantly on tab
  // switch). Move the highlight to follow it — but DON'T fire openChat back (that would loop).
  setActiveChat(ac) {
    if (!this.data || !this.data.sessions) return;
    this.data.activeChat = ac || null;
    const sid = this._sidForActiveChat(ac);
    if (sid) this.selectedSid = sid;
    this.draw();
  }

  // ── vertical drag-to-reorder ─────────────────────────────────────────────
  // A row drag reorders the lanes AND writes the new full SID order to the shared session-order.json,
  // which the chat tabs read+write too — so dragging a row reorders the tabs and vice-versa.
  // Distinguished from a plain click by a small movement threshold; the lanes shuffle live under the
  // cursor, and the order is persisted on drop (optimistically applied so there's no snap-back).
  _svgY(e) {
    const g = this._geom; if (!g) return 0;
    const rect = this.svg.getBoundingClientRect();
    const scaleY = rect.height ? g.H / rect.height : 1;   // svg user-units per client px
    return (e.clientY - rect.top) * scaleY;
  }
  // One mousedown on a lane; the first real movement decides via dragAxis: horizontal → PAN the plot,
  // vertical → REORDER the lane. A plain click (no movement) falls through to the row's select handler.
  _beginDrag(sid, e) {
    if (e.button !== 0 || !this._geom) return;                 // left button, need geometry
    const order = (this._vis || []).map((s) => s.id);
    const fromIdx = order.indexOf(sid);
    if (fromIdx < 0) return;
    this._suppressClick = false;
    this._drag = {
      sid, fromIdx, order, toIdx: fromIdx, moved: false, mode: null,
      startX: e.clientX, startY: e.clientY,                    // client coords → axis decision
      panOff: this.offSec(), panWin: this.winSec(),           // pan baseline (constant scale from gesture start)
    };
    this._onDragMove = (ev) => this._dragMove(ev);
    this._onDragUp = (ev) => this._dragUp(ev);
    window.addEventListener('mousemove', this._onDragMove, true);
    window.addEventListener('mouseup', this._onDragUp, true);
    e.preventDefault();
  }
  _dragMove(ev) {
    const d = this._drag; if (!d) return;
    if (d.mode == null) {
      d.mode = dragAxis(ev.clientX - d.startX, ev.clientY - d.startY);
      if (d.mode == null) return;                              // below threshold → still a potential click
      d.moved = true; this.wrap.classList.add('tl-grabbing');   // closed-fist cursor over the whole plot (pan OR reorder)
      if (d.mode === 'row') this.selectedSid = d.sid;
    }
    if (d.mode === 'pan') this._panDragMove(ev); else this._rowDragMove(ev);
  }
  // Horizontal click-drag → pan at a CONSTANT compressed-sec-per-px scale (same as onWheel's pan branch),
  // measured from the gesture start. BREAKS pin + 🔒lock so you can drag away from now freely.
  _panDragMove(ev) {
    const d = this._drag, g = this._geom; if (!d || !g || !g.plotW) return;
    const rect = this.svg.getBoundingClientRect();
    const scaleX = rect.width ? g.W / rect.width : 1;
    const dt = (ev.clientX - d.startX) * scaleX * (d.panWin / g.plotW);
    // GRAB-THE-CONTENT direction (the user 2026-06-26): drag RIGHT → the content follows your hand right,
    // revealing earlier time on the left (offset grows into the past); drag LEFT → toward now. (This is the
    // opposite sign from the trackpad/scrollbar pan in onWheel, which the user is happy with.)
    this._offSec = Math.max(0, Math.min(MAX_OFFSET, d.panOff + dt));
    this._setLock(false);                                      // a drag turns OFF 🔒 — no snap-back to now
    this._pinned = false;
    this._offDirty = true;
    this._suppressClick = true;
    this._scheduleDraw();
    ev.preventDefault();
  }
  _rowDragMove(ev) {
    const d = this._drag; if (!d) return;
    const n = d.order.length, y = this._svgY(ev);
    // invert laneY with the host-group offsets: pick the lane whose center is nearest the pointer
    // (offsets shift live as the drag crosses a group boundary — nearest-center keeps it stable).
    const offs = (this._geom && this._geom.laneOffs) || [];
    let toIdx = 0, bestDy = Infinity;
    for (let i = 0; i < n; i++) {
      const ly = this._geom.top + i * LANE_GAP + (offs[i] || 0) + LANE_GAP / 2;
      const dy = Math.abs(y - ly);
      if (dy < bestDy) { bestDy = dy; toIdx = i; }
    }
    toIdx = Math.max(0, Math.min(n - 1, toIdx));
    d.toIdx = toIdx;
    // rebuild from the ORIGINAL order each move (no drift): pull the dragged sid, splice at the target band.
    const base = d.order.filter((id) => id !== d.sid);
    base.splice(toIdx, 0, d.sid);
    this._dragOrder = base;
    this._scheduleDraw();
    ev.preventDefault();
  }
  _dragUp(ev) {
    const d = this._drag;
    window.removeEventListener('mousemove', this._onDragMove, true);
    window.removeEventListener('mouseup', this._onDragUp, true);
    this._onDragMove = this._onDragUp = null;
    this._drag = null; this.svg.style.cursor = ''; this.wrap.classList.remove('tl-grabbing');
    if (!d || !d.moved) { this._dragOrder = null; return; }    // no movement → a click; let select fire
    if (d.mode === 'pan') {
      this._markOffsetGesture();                               // re-pin if dragged back to the now-edge
      try { localStorage.setItem(this.OSTORE, String(Math.round(this._offSec))); } catch (e) {}
      this.draw();
      this._startLiveTick();                                   // re-pinned at the edge → resume smooth advance
      ev.preventDefault();
      return;
    }
    this._suppressClick = true;
    const visOrder = this._dragOrder || d.order;
    this._dragOrder = null;
    const full = this._mergeVisibleOrder(visOrder);            // full SID list with only the visible lanes permuted
    this._applyOrderToData(full);                              // optimistic in-place reorder → no snap-back pre-poll
    this._persistOrder(full);                                  // write the shared file (tabs watch it)
    this.draw();
    ev.preventDefault();
  }
  // Map the new VISIBLE order back onto the full session list, keeping non-visible (out-of-window/idle)
  // sessions in their existing absolute slots — only the visible lanes get permuted into their new order.
  _mergeVisibleOrder(visOrder) {
    const oldIds = (this.data && this.data.sessions || []).map((s) => s.id);
    const visSet = new Set(visOrder);
    let vi = 0;
    return oldIds.map((id) => visSet.has(id) ? visOrder[vi++] : id);
  }
  _applyOrderToData(full) {
    if (!this.data || !this.data.sessions) return;
    const oidx = new Map(full.map((id, i) => [id, i]));
    this.data.sessions.sort((a, b) => ((oidx.has(a.id) ? oidx.get(a.id) : Infinity) - (oidx.has(b.id) ? oidx.get(b.id) : Infinity)));
  }
  // Persist the full SID order to ~/.local/state/romp/session-order.json. VS Code webview has no Node →
  // hand it to the extension host (which writes atomically); Obsidian desktop writes directly (tmp+rename).
  _persistOrder(order) {
    try {
      if (typeof window !== 'undefined' && typeof window.__rompTimelineWriteOrder === 'function') {
        window.__rompTimelineWriteOrder(order); return;
      }
      // The direct write is the OBSIDIAN DESKTOP path — an Electron app. Under PLAIN node with a window
      // shim (the `node --test` runner) there is no romp UI at all, so never touch the real state file:
      // the lane-drag test used to land here and WIPE ~/.local/state/romp/session-order.json to its
      // two fixture sids on every `npm test`, which is exactly the "tabs keep reordering themselves"
      // bug the order-audit log finally pinned (the user 2026-07-02). Electron-or-nothing, no heuristic.
      if (typeof process === 'undefined' || !process.versions || !process.versions.electron) return;
      const fs = require('fs'), path = require('path'), os = require('os');
      const base = process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local', 'state');
      const root = process.env.ROMP_STATE_DIR || path.join(base, 'romp');   // per-kernel state root (plans/multi-kernel.md)
      const f = path.join(root, 'session-order.json'), tmp = f + '.tmp';
      fs.writeFileSync(tmp, JSON.stringify(order));
      fs.renameSync(tmp, f);
    } catch (e) { /* no host hook + no Node → can't persist; the drag still reordered visually until next poll */ }
  }

  // ── feed→timeline locate ────────────────────────────────────────────────
  // Driven by timeline-focus.json (update_feed writes {id,sid,t,nonce} on a feed-card click; the data
  // builder surfaces it as data.focus). Pan the time window to the event (if off-screen), highlight +
  // scroll its lane into view, and pulse a ring at (time, lane).
  // Resolve the focus `sid` (update_feed writes the event's transcript fsid) to the lane that actually
  // draws it. Usually fsid === the lane's romp SID; but a FORKED session is merged into ONE lane keyed
  // by the root SID, with the fork's fsid surfacing as a turn's `tid` — so fall back to a tid match.
  _laneForFocusSid(sid) {
    if (!sid || !this.data || !this.data.sessions) return sid;
    if (this.data.sessions.some((s) => s.id === sid)) return sid;            // direct lane id
    const turns = this.data.turns || {};
    for (const s of this.data.sessions) { if ((turns[s.id] || []).some((t) => t.tid === sid)) return s.id; }  // fork fsid → merged lane
    return sid;
  }
  // Exact event id-join (the canonical key — romp-events `e.id` == the feed itemId, now on each turn):
  // find the turn whose id matches and return its lane + exact start. Beats sid+t (no time drift, and
  // it lands on whatever lane actually draws it, so the fork-merge case is handled for free).
  _focusTargetById(id) {
    if (!id || !this.data || !this.data.sessions) return null;
    const turns = this.data.turns || {};
    for (const s of this.data.sessions) {
      for (const t of (turns[s.id] || [])) { if (t.id === id) return { sid: s.id, t: t.start, end: t.end, tid: t.tid, uuid: t.uuid, workUuid: t.workUuid, replyUuid: t.replyUuid, src: t.src }; }
    }
    return null;
  }
  // Broken-axis: a GLOBAL real→compressed time map. Long idle gaps (≥GAP_MIN, no work on any lane)
  // each collapse to `gapCT` compressed-seconds (one tick interval); active time maps 1:1. Because the
  // gap set is GLOBAL (not window-clipped) and gapCT is fixed for a given zoom, the map is STABLE while
  // panning — so x() only rescales on zoom, never on pan (no edge-snap jank). Returns {compress(t),
  // decompress(c), gaps:[{ra,rb}]} or null (no qualifying gaps → caller uses identity).
  _buildCompressMap(turns, gapCT, now) {
    const iv = [];
    for (const sid in turns) for (const t of (turns[sid] || [])) {
      const s0 = t.start, a = s0, b = Math.max(t.end || s0, s0);
      if (b > a) iv.push([a, b]);
    }
    if (!iv.length) return null;
    iv.sort((p, q) => p[0] - q[0]);
    const merged = [];
    for (const [a, b] of iv) { const last = merged[merged.length - 1]; if (last && a <= last[1] + 1) last[1] = Math.max(last[1], b); else merged.push([a, b]); }
    const gaps = idleGaps(merged, gapCT, now);                // [{ra, rb, trailing}], collapse-worthy idle stretches
    if (!gaps.length) return null;
    const segs = [];                                          // real [ra,rb] → compressed [ca,cb]
    let curC = gaps[0].ra;
    for (let i = 0; i < gaps.length; i++) {
      const ga = gaps[i].ra, gb = gaps[i].rb, D = gb - ga, ct = Math.min(D, gapCT);
      segs.push({ ra: ga, rb: gb, ca: curC, cb: curC + ct }); curC += ct;
      if (i + 1 < gaps.length) { const nga = gaps[i + 1].ra, len = nga - gb; segs.push({ ra: gb, rb: nga, ca: curC, cb: curC + len }); curC += len; }
    }
    const first = segs[0], last = segs[segs.length - 1];
    const compress = (t) => {                                 // identity outside the gap range, slope 1
      if (t <= first.ra) return first.ca + (t - first.ra);
      if (t >= last.rb) return last.cb + (t - last.rb);
      for (const s of segs) if (t <= s.rb) { const f = s.rb > s.ra ? (t - s.ra) / (s.rb - s.ra) : 0; return s.ca + f * (s.cb - s.ca); }
      return last.cb + (t - last.rb);
    };
    const decompress = (c) => {
      if (c <= first.ca) return first.ra + (c - first.ca);
      if (c >= last.cb) return last.rb + (c - last.cb);
      for (const s of segs) if (c <= s.cb) { const f = s.cb > s.ca ? (c - s.ca) / (s.cb - s.ca) : 0; return s.ra + f * (s.rb - s.ra); }
      return last.rb + (c - last.cb);
    };
    return { compress, decompress, gaps };
  }
  // broken-axis marker for one collapsed gap: just a vertical zigzag squiggle (no band), framed by two
  // boundary gridlines labelled with the time work STOPPED (left) and RESUMED (right).
  // showLeft/showRight (default true) gate each boundary gridline+clock: a gap that STRADDLES a window
  // edge (its start before t0, or end past t1) has that boundary clamped to the plot edge, so we suppress
  // its label/gridline rather than draw it into the gutter (over the battery column) or off the plot.
  // placeLabel (the caller's axis-row occupancy fn) gates each boundary CLOCK: when it would overlap a
  // label already on the row (a regular tick, or another gap's clock) the text is dropped — the
  // gridline and squiggle still draw, so the break stays visible without doubled-up labels.
  _drawGapBreak(svg, x0, x1, ra, rb, top, axisY, showLeft, showRight, placeLabel) {
    const ends = [];
    if (showLeft !== false) ends.push([x0, ra, 'end', -2]);
    if (showRight !== false) ends.push([x1, rb, 'start', 2]);
    for (const e of ends) {
      svg.appendChild(el('line', { x1: e[0], y1: top, x2: e[0], y2: axisY, stroke: '#ffffff20', 'stroke-width': 1, 'pointer-events': 'none' }));
      const s = clock(e[1]), lx = e[0] + e[3];
      this._mc.font = '9px ' + FONT;
      const w = this._mc.measureText(s).width;
      if (placeLabel && !placeLabel(e[2] === 'end' ? lx - w : lx, e[2] === 'end' ? lx : lx + w)) continue;
      const tx = el('text', { x: lx, y: axisY + 14, 'text-anchor': e[2], fill: 'var(--text-muted)', 'font-size': 9, 'pointer-events': 'none' }); tx.textContent = s; svg.appendChild(tx);
    }
    const cx = (x0 + x1) / 2, amp = 3, seg = 7;
    // SPAN label (the user 2026-06-17): a multi-day collapsed gap shows its concise duration ("2 days",
    // "1 week") centered at the TOP of the break — a different row from the boundary clocks below the axis,
    // so it never collides — making clear how long the gap is. Sub-day gaps draw the squiggle full-height.
    const span = rb - ra, hasSpan = span >= 86400, sqTop = top + (hasSpan ? 14 : 0);
    if (hasSpan) {
      const tx = el('text', { x: cx, y: top + 10, 'text-anchor': 'middle', fill: 'var(--text-muted)', 'font-size': 9, 'font-weight': 600, 'pointer-events': 'none' });
      tx.textContent = fmtSpan(span); svg.appendChild(tx);
    }
    let d = 'M ' + cx + ' ' + sqTop, yy = sqTop, k = 0;
    while (yy < axisY) { const ny = Math.min(yy + seg, axisY); d += ' L ' + (cx + (k % 2 ? amp : -amp)) + ' ' + ny; yy = ny; k++; }
    svg.appendChild(el('path', { d, fill: 'none', stroke: '#ffffff', 'stroke-width': 1.4, opacity: 0.5, 'pointer-events': 'none' }));
  }
  // Build the DAG overlay sets from a focus payload's dag (or null when absent/empty). The overlay is
  // synced from this in update() every poll, so it tracks the focus file's CURRENT dag (clears on
  // clear/replace) without needing a fresh focusEvent.
  _dagFromFocus(f) {
    if (!f || !f.dag) return null;
    const ev = f.dag.events || [], ms = f.dag.msgs || [];
    if (!ev.length && !ms.length) return null;
    return { events: new Set(ev), msgs: new Set(ms) };
  }
  // Pan (ONLY) so the focus time `t` sits ~mid-window when it's currently off-screen (compressed-time
  // check, gap-aware). No pulse/open — shared by the full focusEvent jump and the paint-only dag focus.
  // Returns true if it actually panned (caller redraws).
  _panToTime(t) {
    if (t == null) return false;
    const g = this._geom, compress = (g && g.compress) ? g.compress : ((x) => x);
    const win = this.winSec(), cNow = compress(this.data.now), ct = compress(t);
    const cT1 = cNow - this.offSec(), cT0 = cT1 - win;
    // 🔒 locked to now: never pan off the live edge — ZOOM OUT instead. Widen the window (right
    // edge stays at now) until the target is on-screen, sitting ~mid-window, so the right half
    // spans target → now. Already-visible targets change nothing.
    if (this._lockNow) {
      if (ct >= cT0 && ct <= cT1 && this.offSec() === 0) return false;
      this._winSec = Math.max(MIN_W, Math.min(MAX_W, 2 * Math.max(1, cNow - ct)));
      this._offSec = 0; this._offDirty = true; this._pinned = true;
      try { localStorage.setItem(this.WSTORE, String(this.winSec())); } catch (e) {}
      try { localStorage.setItem(this.OSTORE, '0'); } catch (e) {}
      return true;
    }
    if (ct < cT0 || ct > cT1) {                        // off-screen in time → pan so t sits ~mid-window
      this._offSec = Math.max(0, Math.min(MAX_OFFSET, cNow - ct - win * 0.5));
      this._markOffsetGesture();                       // hold at the navigated target (don't creep)
      try { localStorage.setItem(this.OSTORE, String(Math.round(this._offSec))); } catch (e) {}
      return true;
    }
    return false;
  }
  focusEvent(f) {
    if (!f || !this.data || !this.data.sessions) return;
    // The DAG overlay (this._dag) is synced every poll in update() from the focus file — so a clear or
    // replace takes effect even without a fresh focusEvent. focusEvent only does the one-shot JUMP.
    // locate:false is a PAINT-only signal. the user's ruling (2026-06-10): a feed-card HOVER (or single click)
    // must only HIGHLIGHT the journey on the timeline (paint, already done by update()) and NEVER jump/pan
    // — only a DOUBLE-CLICK jumps. The double-click carries jump:true, so even on a paint focus we PAN to
    // bring the DAG on-screen (pan only — no chat-open, that's first-party in romp-chat-view ≥v0.4.171; no
    // pulse). Plain hover/single-click omit jump → just paint, no pan.
    if (f.locate === false) {
      if (f.jump) {
        const tb = f.id ? this._focusTargetById(f.id) : null;
        if (this._panToTime(tb ? tb.t : f.t)) this.draw();
      }
      return;
    }
    const byId = f.id ? this._focusTargetById(f.id) : null;     // prefer the exact id-join
    // Each work period has TWO anchors: the prompt START DOT (uuid, the boundary line) and the WORK BAR
    // (workUuid, the period's reply/response). The feed now sends an explicit CLICK-INTENT hint, because
    // kind-inference can't see intent: a reply filed under a direct ask lands ON the typed turn, so a
    // work-row click can carry a typed-turn id. anchor='work' → flash the BAR + open workUuid (even on a
    // typed turn); anchor='prompt' → the start dot. Absent (old payloads) → fall back to kind-inference:
    // typed/queued/enqueue = the user's prompt (dot); drain/absorbed/decision = peer/queue work (bar). This
    // is the fix for the user landing on an edit or their own message (the start glyph of a drain turn is a
    // tool-use boundary or the coincident message-arrival dot), never the work.
    const kindWork = !!(byId && byId.src && byId.src !== 'typed' && byId.src !== 'queued');
    const onWork = !!byId && (f.anchor === 'work' ? true : (f.anchor === 'prompt' ? false : kindWork));
    const sid = byId ? byId.sid : this._laneForFocusSid(f.sid);  // else fall back to sid (fork-aware)
    const t = byId ? byId.t : f.t;                               // else the written time (turn START)
    this._panToTime(t);                                          // pan so the target sits ~mid-window if off-screen
    if (sid) this.selectedSid = sid;
    this.draw();                                     // redraw with the new pan + selection (refreshes _geom/_vis)
    this._pulseFocus(sid, t, onWork ? byId : null);  // reply event → flash the BAR; prompt → ring on the dot
    // Land the chat half too. A reply event opens its READABLE reply line (replyUuid = last assistant
    // line with text, NOT the first which is usually a thinking block → workUuid/uuid fallbacks); a typed
    // prompt opens its prompt line (uuid). anchorT (the turn/event time) rides along belt-and-braces so
    // the chat scrolls by time if the uuid anchor misses. On a _focusTargetById MISS (event outside the
    // loaded window) we STILL open the lane by time (anchorT=f.t) rather than silently doing nothing.
    if (byId && byId.tid) {
      const a = onWork ? workAnchorOf(byId) : byId.uuid;
      // !onWork = we resolved to the boundary uuid = PROMPT-intent → anchorKind=user (kind-safe fallback)
      this.openChat(byId.tid, a, false, false, byId.t, onWork ? undefined : 'user');
    } else if (sid && f.t != null) {
      const lane = this.data.sessions.find((x) => x.id === sid);
      // byId missed → pure time fallback; a 'prompt'-anchored focus is still prompt-intent
      this.openChat((lane && this._laneTid(lane)) || sid, undefined, false, false, f.t, f.anchor === 'prompt' ? 'user' : undefined);
    }
  }
  // Pan to an event and pulse it, and do NOTHING else — the chat-rail click's landing (the user
  // 2026-07-23). focusEvent is the wrong tool for that click: it also calls openChat, and the click came
  // FROM the chat, so it would scroll the pane the user is already reading back to where they clicked.
  // Resolution mirrors focusEvent's: prefer the exact id-join, fall back to (sid, t); a non-typed target
  // pulses its work BAR, a typed/queued one its start DOT.
  revealEvent(sid, t, id) {
    if (!this.data || !this.data.sessions) return;
    const tb = id ? this._focusTargetById(id) : null;
    const lane = tb ? tb.sid : this._laneForFocusSid(sid);
    const tt = tb ? tb.t : t;
    if (tt == null) return;
    this._panToTime(tt);
    if (lane) this.selectedSid = lane;
    this.draw();                                    // refreshes _geom/_vis, which _pulseFocus reads
    const onWork = !!(tb && tb.src && tb.src !== 'typed' && tb.src !== 'queued');
    this._pulseFocus(lane, tt, onWork ? tb : null);
  }
  // Flash the focused event + scroll it into view. Called AFTER draw() so _geom (time→x) and _vis
  // (lane index) are current. Two shapes: a prompt focus pulses a RING on its start dot; a reply/work
  // focus (workTurn given) pulses an OUTLINE over the whole work BAR (start→end) — so the confirm lands
  // on the work, not on a prompt/message glyph. (A poll redraw may clear it early — that's fine.)
  _pulseFocus(sid, t, workTurn) {
    const g = this._geom; if (!g) return;
    const i = (this._vis || []).findIndex((s) => s.id === sid);
    if (i < 0) return;
    const y = g.top + i * LANE_GAP + ((g.laneOffs && g.laneOffs[i]) || 0) + LANE_GAP * 0.5;
    const X = (tt) => g.ml + ((g.compress ? g.compress(tt) : tt) - g.cT0) / g.winSec * g.plotW;   // compressed-time x
    const startMs = (typeof performance !== 'undefined' && performance.now) ? performance.now() : null;
    const DUR = 1400;
    if (workTurn) {
      // bar outline: span the work period, pulse stroke-width + fade
      const xs = X(workTurn.t), xe = X(workTurn.end != null && workTurn.end > workTurn.t ? workTurn.end : workTurn.t);
      const bw = Math.max(6, xe - xs), h = BAR_H + 6;
      const box = el('rect', { x: xs - 3, y: y - h / 2, width: bw + 6, height: h, rx: h / 2, fill: 'none', stroke: '#ffd166', 'stroke-width': 2.5, opacity: 0.95 });
      this.svg.appendChild(box);
      try { box.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
      const step = (nowMs) => {
        if (!box.parentNode) return;
        const p = startMs != null ? Math.min(1, (nowMs - startMs) / DUR) : 1;
        const ph = (p * 2) % 1;                        // two pulses
        box.setAttribute('stroke-width', String(2.5 + ph * 2.5));
        box.setAttribute('opacity', String(0.95 * (1 - ph)));
        if (p >= 1) { box.remove(); return; }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
      return;
    }
    const cx = X(t);
    const ring = el('circle', { cx, cy: y, r: 5, fill: 'none', stroke: '#ffd166', 'stroke-width': 2.5, opacity: 0.95 });
    this.svg.appendChild(ring);
    try { ring.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
    const step = (nowMs) => {
      if (!ring.parentNode) return;                  // a poll redraw cleared it → stop
      const p = startMs != null ? Math.min(1, (nowMs - startMs) / DUR) : 1;
      const ph = (p * 2) % 1;                         // two expanding pulses
      ring.setAttribute('r', String(5 + ph * 16));
      ring.setAttribute('opacity', String(0.95 * (1 - ph)));
      if (p >= 1) { ring.remove(); return; }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  drawMessage(msg) {
    while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);
    this.svg.setAttribute('height', '60');
    const t = el('text', { x: 14, y: 34, fill: 'var(--text-muted)', 'font-size': 13 }); t.textContent = msg; this.svg.appendChild(t);
  }

  // FREEZE-ON-HOVER (the user 2026-06-12): while a glyph tooltip is shown, pause live-follow so the
  // content stops sliding out from under the cursor. Only when we were PINNED (following now) and not
  // 🔒-locked — a user who has panned into history is already frozen, and the lock means "always now".
  // We hold at the current now (unpin to a fixed _holdReal); hideTip resumes. The poll redraws against
  // the hold, so the now-edge stops advancing — no continuous slide to fight.
  showTip(html, ev) {
    // bridge a glyph→glyph hover handoff (e.g. work-bar → its prompt dot): cancel any pending unfreeze
    // so the now-edge doesn't resume in the gap and slip the next glyph out from under the cursor (the
    // user 2026-06-15). An already-frozen state carries straight into the new tooltip.
    if (this._unfreezeTimer) { clearTimeout(this._unfreezeTimer); this._unfreezeTimer = null; }
    this.tip.innerHTML = html; this.tip.classList.add('show'); this._tipOwner = (ev && ev.currentTarget) || null; this.moveTip(ev);
    // Freeze the live edge while hovering, so the user can actually read a bar (the user 2026-06-13).
    // Freeze whenever we're following the live edge — pinned OR 🔒locked (lock no longer blocks it).
    // Do NOT mark _offDirty: that makes the next poll take the offset verbatim (off=0 → edge jumps to the
    // new now); leaving it false lets draw()'s hold branch pin the edge at _holdReal (the hover instant).
    if ((this._pinned || this._lockNow) && this.data) {
      this._frozeFromPin = true; this._pinned = false; this._holdReal = this.data.now; this._offDirty = false;
    }
  }
  // Position the tip beside the pointer IN ITS HOST document's viewport (the topmost same-origin one —
  // see the adopt block in the constructor): the pointer's pane-local coords are translated by each intervening
  // iframe's offset, the flip happens against the HOST viewport (so a tip near the pane's bottom edge
  // overlays the pane below instead of flipping), and a final clamp pins it on-screen — a tip hugging
  // an edge beats one cut off by it (the user 2026-07-17: tips popped too high/low and clipped).
  moveTip(ev) {
    const pad = 14;
    let px = ev.clientX, py = ev.clientY, hw = innerWidth, hh = innerHeight;
    if (this._tipWin && this._tipWin !== window) {
      try {
        for (let w = window; w !== this._tipWin && w.frameElement; w = w.parent) {
          const fr = w.frameElement.getBoundingClientRect(); px += fr.left; py += fr.top;
        }
        hw = this._tipWin.innerWidth; hh = this._tipWin.innerHeight;
      } catch (e) { /* host went cross-origin/away — position pane-locally */ }
    }
    const r = this.tip.getBoundingClientRect();
    let lx = px + pad, ly = py + pad;
    if (lx + r.width > hw) lx = px - r.width - pad;
    if (ly + r.height > hh) ly = py - r.height - pad;
    lx = Math.max(0, Math.min(lx, hw - r.width)); ly = Math.max(0, Math.min(ly, hh - r.height));
    this.tip.style.left = lx + 'px'; this.tip.style.top = ly + 'px';
  }
  hideTip() {
    this.tip.classList.remove('show'); this._tipOwner = null;   // tooltip hides at once…
    // …but DEFER the live-follow resume a beat: a quick move onto another glyph (bar→dot) fires its
    // showTip, which cancels this timer, so the now-edge never resumes/jumps mid-handoff. If nothing
    // grabs it within the grace window, the timer resumes live-follow + snaps to now (the catch-up).
    if (this._unfreezeTimer) return;
    this._unfreezeTimer = setTimeout(() => {
      this._unfreezeTimer = null;
      const dirty = this._dirtyWhileTip; this._dirtyWhileTip = false;
      if (this._frozeFromPin) { this._frozeFromPin = false; this._jumpToNow(); }
      else if (dirty && this.data) this.draw();
    }, 40);
  }

  // Re-arm hover after a rebuild (the user 2026-07-21, message-connector tips). draw() wipes and rebuilds
  // the whole SVG, so a glyph the cursor is resting on comes back as a BRAND NEW element — and the browser
  // fires mouseenter only when a pointer MOVES across a boundary, never for one that has sat still. Without
  // this the tip waits for the user to jiggle the mouse. So: after the rebuild, hit-test the pointer we
  // already track and re-run whatever is under it. Event-based (keyed on the rebuild finishing + a real
  // pointer position), never a timer or a hover-intent delay. Cheap: one elementFromPoint per redraw, and
  // only while the cursor is actually over the plot. Skipped when a tip is already up — an open tip holds
  // the redraw off entirely (draw()'s freeze), so there is nothing to restore.
  _rehover() {
    const p = this._ptr;
    if (!p || !this.tip || !this.tip.classList) return;
    // A SHOWN tip is only worth leaving alone while its owner SURVIVED the rebuild. draw() wipes the
    // svg, so the usual case is a tip still up but pointing at a DETACHED owner — stale, not live.
    // Skipping on `.show` alone left that stale tip in place, so the re-arm below never ran; the next
    // mouse movement then hit _onTipSweep, which hides a tip whose owner is no longer isConnected,
    // and nothing restored it until the NEXT redraw — up to seconds away on an idle fleet. That is
    // the "it appears, disappears, and only comes back if I hold still" report (the user 2026-07-21).
    // Re-arming here rebinds the tip to the freshly rebuilt element within the same draw.
    if (this.tip.classList.contains('show') && this._tipOwner && this._tipOwner.isConnected) return;
    let node = null;
    try { node = this.svg.ownerDocument.elementFromPoint(p.x, p.y); } catch (e) { return; }
    // walk up to the hit target: elementFromPoint can land on a child of the element carrying the handler
    for (let n = node; n && n !== this.svg; n = n.parentNode) {
      if (n.__tlHoverIn) { n.__tlHoverIn({ clientX: p.x, clientY: p.y, currentTarget: n }); return; }
    }
  }

  // Deep-link a click on a timeline item into the romp Chat View VS Code extension,
  // focusing that session's tab and scrolling to the EXACT transcript line. Contract
  // (agreed with vs_chat): open vscode://romp.romp-chat-view/open?session=<TRANSCRIPT_ID>&anchor=<LINE_UUID>.
  //   session = the PER-EVENT source transcript basename (a lane can span multiple
  //             transcripts over resume/fork, so this is the clicked item's tid, NOT the lane id).
  //   anchor  = the uuid of the conversational JSONL line to scroll to; OMITTED → the chat opens the
  //             tab and scrolls to the BOTTOM (latest). Lane-level selects (row/bar/awaiting/↑↓) omit it.
  //   preserveFocus = true → append &focus=0 so the chat reveals WITHOUT stealing focus (used by lane
  //                   selects/↑↓ preview so you can keep arrowing); a dot/line click omits it → focus the chat.
  openChat(session, anchor, preserveFocus, compose, anchorT, anchorKind) {
    if (!session) return;                       // need a transcript/session to open
    let url = 'vscode://romp.romp-chat-view/open?session=' + encodeURIComponent(session);
    if (anchor) url += '&anchor=' + encodeURIComponent(anchor);   // uuid anchor (wins); omit → use anchorT / bottom
    // anchorT (epoch seconds): the chat view scrolls to the nearest turn by time (skipping thinking
    // blocks) when the uuid anchor misses — so a click NEVER silently no-ops. Sent belt-and-braces.
    if (anchorT != null && isFinite(anchorT)) url += '&anchorT=' + Math.round(anchorT);
    // anchorKind=user (PROMPT-intent opens only): when the uuid anchor misses and the open falls back
    // to time, the chat view (≥v0.4.157) restricts the nearest-readable-turn search to the USER's own
    // turns — so a prompt-intent click can degrade in PRECISION but never land on an assistant answer
    // (the user's rule: a fallback may degrade landing precision, never landing KIND). Omitted = any turn.
    if (anchorKind) url += '&anchorKind=' + encodeURIComponent(anchorKind);
    if (preserveFocus) url += '&focus=0';
    if (compose) url += '&compose=1';           // Enter → put the cursor in the chat's message box for this session
    try {
      // VS Code webview surface (vscode-trackchanges): no Node here, so hand the uri to the
      // extension host, which opens it via vscode.env.openExternal. Host injects this hook.
      if (typeof window !== 'undefined' && typeof window.__rompTimelineOpenExternal === 'function') {
        window.__rompTimelineOpenExternal(url); return;
      }
      // Obsidian desktop surface: shell out via Node.
      require('child_process').execFile('open', [url]);
    } catch (e) { /* no host hook + no shell → silently ignore */ }
  }

  // Click the context battery → send `/compact` to that session's terminal. VS Code: hand the session
  // name to the extension host (no Node in the webview); Obsidian: shell tmux directly. Types the slash
  // command literally then submits it. (Targets the tmux session by name, like romp-postal-service's inject.)
  // (Removed _smilBegin: the working-badge breathe no longer uses an in-SVG SMIL <animate> — a phase resync
  // couldn't fix the CADENCE, so even phase-correct it stuttered/truncated at the irregular redraw rate. It's
  // now a persistent CSS-animated overlay div (see _positionWorkLabel), like the compacting sweep — the user
  // 2026-07-01.)
  // SVG-user-coords → overlay (wrap) px. The svg renders at width/height = its viewBox on DESKTOP (1:1), but on
  // TOUCH the CSS scales it (svg{width:100%;height:auto}) and overflow-x:auto can scroll it — so map through the
  // svg's ACTUAL rendered rect vs its viewBox, and offset by the svg's position within the wrap (handles scroll).
  // Memoized per draw (keyed by _drawSeq) so the getBoundingClientRect reflow happens at most once per paint, and
  // only when a lane is actually compacting. try/catch → identity in a DOM-less test/headless context.
  _ovScaleNow() {
    if (this._ovSeq === this._drawSeq && this._ovScale) return this._ovScale;
    let m = { sx: 1, sy: 1, ox: 0, oy: 0 };
    try {
      const sr = this.svg.getBoundingClientRect(), wr = this.wrap.getBoundingClientRect();
      const vbW = +this.svg.getAttribute('width') || sr.width || 1;
      const vbH = +this.svg.getAttribute('height') || sr.height || 1;
      m = { sx: sr.width / vbW, sy: sr.height / vbH, ox: sr.left - wr.left, oy: sr.top - wr.top };
    } catch (e) {}
    this._ovSeq = this._drawSeq; this._ovScale = m;
    return m;
  }
  // Create-or-reposition this sid's PERSISTENT compacting scan-bar div over its battery cell. The div's CSS
  // `animation` is set ONCE on creation and never touched again, so the compositor runs the sweep continuously;
  // draw() only nudges left/top/width here, which never restarts a CSS animation → smooth regardless of how
  // often (or unevenly) draw() fires. (Mirrors the chat tab's CSS-animated compaction bar. The user 2026-06-29.)
  _positionCompactBar(sid, x, y, w, h) {
    if (!this._compactLayer) return;
    let bar = this._compactBars.get(sid);
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'romp-tl-compact-bar';
      this._compactLayer.appendChild(bar);
      this._compactBars.set(sid, bar);
    }
    // colormap gradient for the compression sweep (kernel cmapGrad = 5 rgb stops, narrowest→widest: --cmp0 is
    // the map's 0% colour, --cmp4 its full/100% colour). Re-applied each draw so a live colormap switch recolours
    // an in-progress bar; changing a custom prop referenced by the keyframes recolours WITHOUT restarting the
    // compositor animation, so the sweep stays smooth.
    const g = this._cmapGrad;
    if (g && g.length === 5) for (let k = 0; k < 5; k++) bar.style.setProperty('--cmp' + k, 'rgb(' + g[k][0] + ',' + g[k][1] + ',' + g[k][2] + ')');
    const m = this._ovScaleNow();
    bar.style.left = (m.ox + x * m.sx) + 'px'; bar.style.top = (m.oy + y * m.sy) + 'px';
    bar.style.width = (w * m.sx) + 'px'; bar.style.height = (h * m.sy) + 'px';
  }
  // Remove scan-bar divs for sids that aren't compacting (or scrolled off) this draw — so a finished compaction,
  // a closed lane, or the loader screen leaves no orphan bar. `keep` is the set of sids drawn a bar this pass.
  _reapCompactBars(keep) {
    if (!this._compactBars) return;
    for (const [sid, bar] of this._compactBars) {
      if (!keep || !keep.has(sid)) { try { bar.remove(); } catch (e) {} this._compactBars.delete(sid); }
    }
  }
  // Create-or-reposition this sid's PERSISTENT working-badge label div (CSS color-pulse set once on creation,
  // never touched again → the compositor breathes it continuously, smooth regardless of the redraw cadence).
  // draw() only nudges its position/text/size here. Centered on (cx, cy) in SVG user coords via translate(-50%).
  // (Mirrors _positionCompactBar; the chat chip's .chip-pulse gets the same 1.5s clock. The user 2026-07-01.)
  _positionWorkLabel(sid, cx, cy, text) {
    if (!this._compactLayer) return;
    let lab = this._workLabels.get(sid);
    if (!lab) {
      lab = document.createElement('div');
      lab.className = 'romp-tl-work-label';
      this._compactLayer.appendChild(lab);
      this._workLabels.set(sid, lab);
    }
    if (lab.textContent !== text) lab.textContent = text;
    const m = this._ovScaleNow();
    lab.style.left = (m.ox + cx * m.sx) + 'px';
    lab.style.top = (m.oy + cy * m.sy) + 'px';
    lab.style.fontSize = (BADGE_FS * m.sy) + 'px';
  }
  // Remove working-label divs for sids no longer working (or scrolled off / loader up) this draw.
  _reapWorkLabels(keep) {
    if (!this._workLabels) return;
    for (const [sid, lab] of this._workLabels) {
      if (!keep || !keep.has(sid)) { try { lab.remove(); } catch (e) {} this._workLabels.delete(sid); }
    }
  }
  // Create-or-reposition this sid's PERSISTENT "model switching…" dots div, left-aligned at (x, cy) in SVG
  // user coords (x = the model name's start, cy = the lane center). Set once, only repositioned after → the
  // pulse rides the compositor unbroken by the SVG wipe. Mirrors _positionWorkLabel.
  _positionMetaDots(sid, x, cy) {
    if (!this._compactLayer) return;
    let dots = this._metaDots.get(sid);
    if (!dots) {
      dots = document.createElement('div');
      dots.className = 'romp-tl-meta-dots';
      dots.appendChild(document.createElement('i'));
      dots.appendChild(document.createElement('i'));
      dots.appendChild(document.createElement('i'));
      this._compactLayer.appendChild(dots);
      this._metaDots.set(sid, dots);
    }
    const m = this._ovScaleNow();
    dots.style.left = (m.ox + x * m.sx) + 'px';
    dots.style.top = (m.oy + cy * m.sy) + 'px';
  }
  // Remove the switching-dots div for sids no longer resolving a /model pick (or scrolled off / loader up).
  _reapMetaDots(keep) {
    if (!this._metaDots) return;
    for (const [sid, dots] of this._metaDots) {
      if (!keep || !keep.has(sid)) { try { dots.remove(); } catch (e) {} this._metaDots.delete(sid); }
    }
  }
  _compactSession(name) {
    if (!name) return;
    try {
      if (typeof window !== 'undefined' && typeof window.__rompTimelineCompact === 'function') {
        window.__rompTimelineCompact(name); return;
      }
      const cp = require('child_process'), tmux = this._tmuxPath();
      cp.execFile(tmux, ['send-keys', '-t', name, '-l', '/compact'], (err) => {
        if (!err) cp.execFile(tmux, ['send-keys', '-t', name, 'Enter']);
      });
    } catch (e) { /* no host hook + no Node → can't send */ }
  }
  // Inject a slash command into a session's pane (the model/effort pickers). VS Code surface: hand it
  // to the host hook if present; Obsidian: shell tmux. We BRACKETED-PASTE the command (set-buffer +
  // paste-buffer -p) rather than send-keys -l, then submit with a delayed Enter — mirroring the
  // the extension's sendToSession. A literal type would feed "/model …" to Claude Code's slash-command
  // AUTOCOMPLETE char-by-char and an immediate Enter would race the TUI; a bracketed paste lands the
  // whole string atomically (no autocomplete), and the 250ms gap lets the paste arrive before Enter.
  //
  // confirm=true → send a SECOND Enter after the submit. /model doesn't switch on submit: it opens a
  // "Switch model?" picker (cursor pre-seated on "Yes, switch …") that fires no hook and waits — so the
  // one Enter only OPENS the dialog and the model never changes. The extra Enter accepts the default
  // "Yes". /effort and /compact apply directly (no cache-invalidation confirmation), so they don't pass
  // it. The extra Enter is harmless even if a build skips the dialog (an empty composer submit is a no-op).
  _sendCommand(name, cmd, confirm) {
    if (!name || !cmd) return;
    try {
      if (typeof window !== 'undefined' && typeof window.__rompTimelineSendCommand === 'function') {
        window.__rompTimelineSendCommand(name, cmd); return;
      }
      const cp = require('child_process'), tmux = this._tmuxPath();
      const env = Object.assign({}, process.env, { LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8', LC_CTYPE: 'en_US.UTF-8' });
      const run = (args, cb) => cp.execFile(tmux, args, { timeout: 4000, encoding: 'utf8', env }, (err, out) => { if (cb) cb(err, out); });
      const enter = () => run(['send-keys', '-t', name, 'Enter']);
      const BUF = 'romp-timeline';
      const submit = () => { enter(); if (confirm) setTimeout(enter, 600); };   // 2nd Enter → accept "Switch model? Yes"
      const paste = () => run(['set-buffer', '-b', BUF, cmd], () =>
        run(['paste-buffer', '-b', BUF, '-d', '-p', '-t', name], () => setTimeout(submit, 250)));
      // exit copy-mode first if the pane is scrolled, so the paste + Enter actually land
      run(['display-message', '-p', '-t', name, '#{pane_in_mode}'], (err, out) => {
        if (!err && String(out || '').trim() === '1') run(['send-keys', '-t', name, '-X', 'cancel'], paste);
        else paste();
      });
    } catch (e) { /* no host hook + no Node → can't send */ }
  }

  _closeMetaMenu() { if (this._metaMenu) { this._metaMenu.remove(); this._metaMenu = null; } }

  // Where a drop-down should live and be measured: the tip's host document (the topmost same-origin
  // window — in the web shell that's the whole page, so a menu taller than the timeline band gets the
  // full window's height instead of the band's few rows; the user 2026-08-07, still cropped after the
  // flip fix). The anchor rect is translated by the intervening iframes' offsets, moveTip-style. A
  // cross-origin parent (the VS Code webview) throws on frameElement access → fall back to our own
  // pane, which is exactly the pre-host behavior.
  _menuHost(anchorRect) {
    if (this._tipWin && this._tipWin !== window) {
      try {
        const frames = [];
        for (let w = window; w !== this._tipWin && w.frameElement; w = w.parent) frames.push(w.frameElement.getBoundingClientRect());
        return { win: this._tipWin, doc: this._tipWin.document, rect: offsetRect(anchorRect, frames) };
      } catch (e) { /* host went cross-origin/away — position pane-locally */ }
    }
    return { win: window, doc: document, rect: anchorRect };
  }

  // Open the model/effort drop-down anchored under the clicked label. Re-clicking the same word's
  // caret toggles it shut. Refused while the lane is AWAITING a prompt — the pane's keyboard belongs to
  // the picker, so a pasted "/model …" + Enter would answer it instead (the chat view guards the same way).
  _openMetaMenu(kind, s, anchorEl) {
    const reopen = this._metaMenu && this._metaMenu._kind === kind && this._metaMenu._sid === s.id;
    this._closeMetaMenu();
    if (reopen) return;
    if (s.state === 'awaiting' || s.state === 'permission') return;
    // Styled inline (NOT via a CSS class): injectStyles() guards on an existing <style> id, so a CSS
    // rule added later never lands after a plugin reload — only a full restart. Inline always applies.
    const menu = document.body.createDiv();
    menu.setAttribute('style', 'position:fixed;z-index:1001;min-width:96px;padding:4px;background:#1c2430;border:1px solid #ffffff1f;border-radius:8px;box-shadow:0 8px 24px #00000066;font-size:12px;color:#e6edf3;user-select:none;');
    menu._kind = kind; menu._sid = s.id;
    for (const c of (kind === 'model' ? MODEL_CHOICES : EFFORT_CHOICES)) {
      const cur = isCurrentMeta(kind, s, c.value);
      const item = menu.createDiv({ text: c.label });
      item.setAttribute('style', 'padding:4px 22px 4px 9px;border-radius:5px;cursor:pointer;position:relative;white-space:nowrap;' + (cur ? 'color:#54B204;' : ''));
      if (cur) { const ck = item.createSpan({ text: '✓' }); ck.setAttribute('style', 'position:absolute;right:8px;'); }
      item.addEventListener('mouseenter', () => { item.style.background = '#ffffff14'; });
      item.addEventListener('mouseleave', () => { item.style.background = 'transparent'; });
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        this._sendCommand(s.name, '/' + kind + ' ' + c.value, kind === 'model');
        const now = (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
        this._metaPending[s.id + ':' + kind] = { was: (kind === 'model' ? s.model : s.effort) || '', until: now + 20000 };
        this._closeMetaMenu();
        this.draw();
      });
    }
    const h = this._menuHost(anchorEl.getBoundingClientRect());
    h.doc.body.appendChild(menu);   // a cross-document append ADOPTS the node; its listeners are kept
    // clamp to the host viewport so a right-edge lane's menu stays on-screen
    const left = Math.min(Math.round(h.rect.left), (h.win.innerWidth || 9999) - 140);
    menu.style.left = Math.max(6, left) + 'px';
    menu.style.top = Math.round(menuTop(h.rect, menu.offsetHeight || 0, h.win.innerHeight || 9999)) + 'px';
    this._metaMenu = menu;
  }

  _closeLaneMenu() { if (this._laneMenu) { this._laneMenu.remove(); this._laneMenu = null; } }

  // The per-lane settings drop-down is BACK (the user 2026-07-28, superseding their 2026-06-22 removal:
  // that rule held for ONE flag, where a direct icon beat a menu — at THREE flags the icons crowded the
  // lane). The gear opens one row per LANE_TOGGLES entry: the toggle's own icon (blue = on, slashed
  // gray = off), its label + state word, and a plain-language line on what it does. Clicking a row
  // toggles that flag with the SAME optimistic + sticky treatment as the old direct icons, and the menu
  // stays open, repainting in place — it's a settings panel, not a command.
  _openLaneMenu(s, anchorEl) {
    const reopen = this._laneMenu && this._laneMenu._sid === s.id;
    this._closeLaneMenu(); this._closeMetaMenu();
    if (reopen) return;
    // Styled inline like the meta menu (injectStyles can't add rules after a plugin reload).
    const menu = document.body.createDiv();
    menu.setAttribute('style', 'position:fixed;z-index:1001;width:280px;padding:4px;background:#1c2430;'
      + 'border:1px solid #ffffff1f;border-radius:8px;box-shadow:0 8px 24px #00000066;font-size:12px;'
      + 'color:#e6edf3;user-select:none;');
    menu._sid = s.id;
    menu.addEventListener('click', (e) => e.stopPropagation());   // inside clicks must not reach the doc closer
    const build = () => {
      menu.textContent = '';
      for (const t of LANE_TOGGLES) {
        const on = t.enabled(s);
        const row = menu.createDiv();
        row.setAttribute('style', 'display:flex;gap:9px;align-items:flex-start;padding:6px 9px;border-radius:5px;cursor:pointer;');
        const ic = el('svg', { viewBox: '0 0 17 17', width: 15, height: 15 });
        ic.setAttribute('style', 'flex:0 0 auto;margin-top:1px;' + (on ? '' : 'opacity:0.55;'));
        ic.appendChild(t.icon(!on, 8.5, 8.5, on ? ROMP_BLUE : MODEL_FG));
        row.appendChild(ic);
        const body = row.createDiv();
        body.setAttribute('style', 'display:flex;flex-direction:column;line-height:1.35;min-width:0;');
        const lab = body.createDiv({ text: t.label + ' — ' + (on ? 'on' : 'off') });
        lab.setAttribute('style', on ? '' : 'opacity:0.75;');
        const sub = body.createDiv({ text: t.desc });
        sub.setAttribute('style', 'opacity:0.55;font-size:11px;');
        row.addEventListener('mouseenter', () => { row.style.background = '#ffffff14'; });
        row.addEventListener('mouseleave', () => { row.style.background = 'transparent'; });
        row.addEventListener('click', (e) => {
          e.stopPropagation();
          const next = t.value(!on);                 // the flag value that flips this toggle
          s[t.flag] = next;                          // optimistic …
          (this._pendingFlags[s.id] = this._pendingFlags[s.id] || {})[t.flag] = next;   // … sticky until the kernel confirms
          this._setSessionFlag(s, t.flag, next);
          this._reconcilePendingFlags();
          this.draw();
          build();                                   // repaint states in place; the panel stays open
        });
      }
    };
    build();
    const h = this._menuHost(anchorEl.getBoundingClientRect());
    h.doc.body.appendChild(menu);   // a cross-document append ADOPTS the node; its listeners are kept
    const left = Math.min(Math.round(h.rect.left), (h.win.innerWidth || 9999) - 300);   // clamp on-screen
    menu.style.left = Math.max(6, left) + 'px';
    menu.style.top = Math.round(menuTop(h.rect, menu.offsetHeight || 0, h.win.innerHeight || 9999)) + 'px';
    this._laneMenu = menu;
  }

  // Optimistic per-session view flags (the feed checkbox → hideFromFeed). A click flips the flag locally AND
  // fires _setSessionFlag, but the kernel's confirming rebuild takes ~1s and ANY routine push that lands in
  // that window carries the OLD value — wholesale-replacing this.data would REVERT the checkbox (a flicker, the
  // session "un-hiding" for a beat before settling — the user 2026-06-22). So hold each clicked value in
  // _pendingFlags and re-apply it onto every incoming push until the kernel's value MATCHES (confirmed → drop
  // it). Net: click → it changes → it stays, never bounces. Called from update() right after this.data = data.
  _reconcilePendingFlags() {
    const pend = this._pendingFlags; if (!pend) return;
    for (const s of (this.data && this.data.sessions) || []) {
      const p = pend[s.id]; if (!p) continue;
      for (const flag of Object.keys(p)) {
        if (s[flag] === p[flag]) delete p[flag];   // the kernel now agrees → stop overriding this flag
        else s[flag] = p[flag];                    // not yet confirmed → keep the optimistic value sticky
      }
      if (!Object.keys(p).length) delete pend[s.id];
    }
  }

  // Clear (dead-lane dismiss) needs the SAME stickiness as the eye toggle, and for the same reason: the
  // optimistic removal at the click site only edits the CURRENT frame, and update()'s wholesale
  // `this.data = data` puts the lane straight back. Any payload still carrying it wins — the kernel's own
  // in-flight push, or (much more often, with a flapping remote) the federation manager re-emitting a
  // MERGED timeline built from its cached per-host snapshots, which still hold the pre-dismissal local
  // one. That is the "click Clear → it pops back → a second later it goes away on its own" bug (the user
  // 2026-07-22). So hold each cleared sid and re-apply the removal onto every push until either the
  // kernel drops the lane (confirmed) or it comes back LIVE (a revive, which by the kernel's own rule
  // un-dismisses it). Called from update() right after this.data = data.
  _reconcileDismissed() {
    if (!this._dismissed.size || !this.data || !Array.isArray(this.data.sessions)) return;
    const byId = new Map(this.data.sessions.map((s) => [s.id, s]));
    for (const id of Array.from(this._dismissed)) {
      const s = byId.get(id);
      if (!s || s.live) this._dismissed.delete(id);   // kernel caught up, or the sid revived → stop holding it
    }
    if (this._dismissed.size) this.data.sessions = this.data.sessions.filter((s) => !this._dismissed.has(s.id));
  }

  // Persist a per-session flag. Web dashboard: the host WS hook (→ kernel setSessionFlag → rebuild feed).
  // Obsidian/headless fallback: write the same session-flags.json the kernel's build_feed reads.
  _setSessionFlag(s, flag, value) {
    try {
      if (typeof window !== 'undefined' && typeof window.__rompTimelineSetFlag === 'function') {
        window.__rompTimelineSetFlag(s.id, flag, value); return;
      }
      const fs = require('fs'), os = require('os'), path = require('path');
      const dir = path.join(os.homedir(), '.local', 'state', 'romp');
      const fp = path.join(dir, 'session-flags.json');
      let cur = {};
      try { cur = JSON.parse(fs.readFileSync(fp, 'utf8')) || {}; } catch (e) {}
      const f = (cur[s.id] && typeof cur[s.id] === 'object') ? cur[s.id] : {};
      if (value) f[flag] = true; else delete f[flag];
      if (Object.keys(f).length) cur[s.id] = f; else delete cur[s.id];
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(fp, JSON.stringify(cur));
    } catch (e) { /* no host hook + no Node fs → can't persist */ }
  }

  // Clear a DEAD lane's leftover row from the timeline (the Clear pill on a struck-through lane). The kernel
  // holds the dismissal IN MEMORY only, so it does NOT survive a `romp refresh` (the user 2026-07-02) —
  // a mistakenly-cleared lane comes back on restart. Web-shell only (no Obsidian/Node path: nothing to persist).
  _dismissLane(id) {
    try {
      if (typeof window !== 'undefined' && typeof window.__rompTimelineDismiss === 'function') {
        window.__rompTimelineDismiss(id);
      }
    } catch (e) {}
  }

  _tmuxPath() {
    if (this._tmux) return this._tmux;
    this._tmux = 'tmux';
    try { const fs = require('fs'); for (const p of ['/opt/homebrew/bin/tmux', '/usr/local/bin/tmux', '/usr/bin/tmux', '/bin/tmux']) if (fs.existsSync(p)) { this._tmux = p; break; } } catch (e) {}
    return this._tmux;
  }

  // Items that aren't themselves a conversational line (awaiting/compaction spans, message
  // connectors) borrow the deep-link anchor of the session's nearest work period to `t`.
  nearestTurnAnchor(sid, t) {
    const ts = (this.data && this.data.turns && this.data.turns[sid]) || [];
    let best = null, bestd = Infinity;
    for (const x of ts) {
      const d = (t >= x.start && t <= x.end) ? 0 : Math.min(Math.abs(t - x.start), Math.abs(t - x.end));
      if (d < bestd) { bestd = d; best = x; }
    }
    return best;   // {tid,uuid,...} or null → openChat no-ops on a null anchor
  }

  draw() {
    const data = this.data; if (!data || !data.sessions) return;
    this._drawSeq = (this._drawSeq || 0) + 1;   // per-paint nonce: memoizes the overlay scale reflow (_ovScaleNow)
    // LOADING (the user 2026-06-26): until the heavy bars arrive, show ONLY the romp wordmark loader (R +
    // spinning swirl-o + m + p + dots) — NO lanes, NO gridlines. Partial data + empty gridlines read as
    // "broken", so suppress the SVG entirely and show the loader until applyBars sets _barsLoaded. (Data that
    // already carries turns — a full one-shot, or a direct draw() — counts as loaded even without the flag.)
    const barsReady = this._barsLoaded || !!(data.turns && Object.keys(data.turns).length);
    if (!barsReady) { this._showLoader(true); this._reapCompactBars(null); this._reapWorkLabels(null); this._reapMetaDots(null); return; }   // loader up → no lanes, drop any overlays
    this._showLoader(false);
    const svg = this.svg, M = this.M;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    // candy-cane hatch for AWAITING spans: diagonal white stripes overlaid on the bar
    const defs = el('defs', {});
    const pat = el('pattern', { id: 'vault-await-hatch', patternUnits: 'userSpaceOnUse', width: 7, height: 7, patternTransform: 'rotate(45)' });
    pat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: '#ffffff', 'stroke-width': 4, opacity: 0.78 }));
    defs.appendChild(pat);
    // CROSS-hatch (X-weave) for CONTEXT COMPACTION spans — distinct from the single-diagonal candy-cane;
    // a cool cyan reads as "compressed". Two perpendicular line sets.
    const cpat = el('pattern', { id: 'vault-compact-hatch', patternUnits: 'userSpaceOnUse', width: 6, height: 6, patternTransform: 'rotate(45)' });
    cpat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: '#86e1ff', 'stroke-width': 2, opacity: 0.9 }));
    cpat.appendChild(el('line', { x1: 0, y1: 0, x2: 6, y2: 0, stroke: '#86e1ff', 'stroke-width': 2, opacity: 0.9 }));
    defs.appendChild(cpat);
    // WORKING-chip color-pulse mirrors romp-chat-view's `.chip-pulse`: the letters are ONE solid color
    // that breathes between two tones on a sine ease. That's a per-<text> SMIL `<animate fill>` (added
    // where the chip is drawn below), so no gradient def is needed here.
    svg.appendChild(defs);
    // Pan: the window's RIGHT edge is `now` minus the offset slider; the actual live `now` (nowS)
    // is separate, so pending events still ride the true now (off-screen to the right when panned back).
    const nowS = this._liveNow(), winSec = this.winSec();   // effective now: glides between polls while live-following
    this._lastLiveNow = nowS;                               // baseline for the live-tick's sub-pixel guard
    // Broken-axis (see _buildCompressMap): the window (winSec/off) is in COMPRESSED seconds; each long
    // idle gap collapses to one tick interval (step). The map is GLOBAL + stable, so panning is a pure
    // translate (x only rescales on zoom). When collapse is off, compress is identity = plain linear.
    const step = niceStep(winSec);                            // axis tick interval (discrete nice value)
    const gapCT = winSec * GAP_FRAC;                          // gap compressed width — CONTINUOUS → smooth zoom
    const cmap = this._collapseGaps ? this._buildCompressMap(data.turns, gapCT, nowS) : null;
    const compress = cmap ? cmap.compress : (t) => t;
    const decompress = cmap ? cmap.decompress : (c) => c;
    const cNow = compress(nowS);
    // FOLLOW-NOW vs HOLD-POSITION (see constructor). Pinned → right edge = now (live auto-scroll).
    // Unpinned → re-derive `off` each POLL so the right edge stays at `_holdReal` (absolute → no creep);
    // a fresh gesture/nav (_offDirty) is taken verbatim this frame, then we resume holding.
    let off;
    if (this._lockNow && !this._frozeFromPin) this._pinned = true;   // 🔒 lock pins to now — but a hover-freeze still wins (hold at the hovered instant)
    if (this._pinned) off = 0;
    else if (!this._offDirty && this._holdReal != null) off = Math.max(0, Math.min(MAX_OFFSET, cNow - compress(this._holdReal)));
    else off = this.offSec();
    this._offSec = off; this._offDirty = false;
    const cT1 = cNow - off, cT0 = cT1 - winSec;
    const t1 = decompress(cT1), t0 = decompress(cT0);         // real-time window edges (for clip filters)
    this._holdReal = t1;                                      // remember the absolute right edge for the next poll's hold

    const inWin = (t) => t >= t0 && t <= t1;
    const overlaps = (a, b) => b >= t0 && a <= t1;
    // An event is positioned at its PROCESS-START (when it began affecting the workflow). While still
    // pending (queued / in-flight, not yet worked) it rides the live `now` edge (nowS); once processed
    // the data carries a FIXED past time so it can never equal now again (anti-"perpetual-just-landing").
    const execAt = (mm) => mm.pending ? nowS : mm.exec;
    // RELAYED mail's exec refinement (the user 2026-08-06): a cross-host message's true process-start
    // lives in the RECIPIENT's turns, which only meet the sender's connector HERE, in the merged view —
    // the kernel's own binder (_bind_message_execs) never sees a remote lane's turns. The read receipt
    // now carries the remote's delivery mid (dmid) — exactly what the recipient's transcript markers
    // record — so the join is exact: the earliest bar on the recipient's lane whose mids carry the
    // message's id or dmid, its start is the landing. Idempotent for local mail (the kernel already
    // bound those to the same bar start), and a no-match leaves the receipt time as before.
    if (data.messages && data.messages.length) {
      const midStart = {};
      Object.keys(data.turns || {}).forEach((sid) => (data.turns[sid] || []).forEach((b) => {
        (b.mids || []).forEach((mid) => { const k = sid + '|' + mid; if (!(k in midStart) || b.start < midStart[k]) midStart[k] = b.start; });
      }));
      data.messages.forEach((mm) => {
        const s1 = midStart[mm.toId + '|' + (mm.id || '')], s2 = midStart[mm.toId + '|' + (mm.dmid || '')];
        const st = (s1 != null && s2 != null) ? Math.min(s1, s2) : (s1 != null ? s1 : s2);
        if (st != null) { mm.exec = st; mm.pending = false; }
      });
    }
    const startAt = (t) => t.pending ? nowS : t.start;
    // LANE IDENTITY IS THE SID (data.turns + vidx + connectors all key by session.id, since two
    // live sessions can share a name and a rename keeps the id). `name` is display-only.
    const turnsOf = (sid) => data.turns[sid] || [];
    const colorOf = (sid) => { const s = data.sessions.find((x) => x.id === sid); return s ? s.color : '#888'; };

    // live sessions ALWAYS get a lane (even with no activity in this window);
    // closed sessions appear only when the window covers their past activity
    const active = (s) => s.live ||
      turnsOf(s.id).some((t) => overlaps(t.start, t.end)) ||
      data.messages.some((m) => (m.fromId === s.id || m.toId === s.id) && overlaps(Math.min(m.sent, m.exec), Math.max(m.sent, m.exec)));
    let vis = data.sessions.filter(active);
    // while a row is being dragged, honor the transient drag order so the lanes shuffle live under
    // the cursor (data.sessions still holds the persisted order; _dragOrder overrides until drop).
    if (this._dragOrder) {
      const oidx = new Map(this._dragOrder.map((id, i) => [id, i]));
      vis = vis.slice().sort((a, b) => ((oidx.has(a.id) ? oidx.get(a.id) : Infinity) - (oidx.has(b.id) ? oidx.get(b.id) : Infinity)));
    }
    this._vis = vis;   // visible lanes in order → keyboard ↑/↓ selection
    const vidx = {}; vis.forEach((s, i) => { vidx[s.id] = i; });
    // Host grouping (federation): the merged payload stamps each session's owning kernel on s.host
    // ('' = local; the merge concatenates local-first). Remote hosts' lanes sit BELOW the local group
    // with a HALF-ROW gap at each host boundary (the user 2026-07-02) so "a different machine" reads at
    // a glance. laneOffs[i] = the cumulative extra y before lane i; single-kernel data has no host
    // field → all offsets 0 → layout identical to before.
    const laneOffs = []; let laneOffAcc = 0;
    for (let i = 0; i < vis.length; i++) {
      if (i > 0 && (vis[i].host || '') !== (vis[i - 1].host || '')) laneOffAcc += LANE_GAP * 0.5;
      laneOffs.push(laneOffAcc);
    }
    const laneOffTotal = laneOffAcc;


    // "compacting" = the real @claude-state OR an OPTIMISTIC click not yet confirmed (≤6s) — so the cue
    // appears the instant the user clicks the battery, not after the next state poll.
    const nowMs = (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
    const compactingNow = (s) => s.state === 'compacting' || (this._compactClicked[s.id] != null && (nowMs - this._compactClicked[s.id]) < 6000);
    // gutter = name column (left-aligned) + chip column (every chip shares an x,
    // like the dashboard's badge column). Names left-aligned, chips follow.
    const visB = vis.map((s) => s.state === 'clearing'
      ? { label: 'Clearing', bg: BADGE.compacting.bg, fg: BADGE.compacting.fg }     // a /clear in flight — same context-op teal as compacting
      : compactingNow(s)
      ? { label: 'Compacting', bg: BADGE.compacting.bg, fg: BADGE.compacting.fg }   // NO %: the scraped pct was laggy/inaccurate, and the SDK offers none (compact_progress events are lifecycle-only — investigated 2026-07-02); the scan-bar is the live cue
      : badgeFor(s));
    const visC = vis.map((s) => ctxInfo(s));
    const maxName = Math.max(40, ...(vis.length ? vis : data.sessions).map((s) => this.labelWidth(s.name)));
    // model+effort column: each word is a clickable picker drawn as [model ▾] [effort ▾], so reserve the
    // word + caret widths (+ a gap between the two pickers). Same 11px font as ctx (ctxWidth).
    const META_GAP = 6, caretW = this.ctxWidth(META_CARET);
    // model + effort share the meta column, but the EFFORT is LEFT-JUSTIFIED to a FIXED sub-column x (the
    // user 2026-07-03): every effort word starts at the SAME offset regardless of its lane's model-name length,
    // so "high"/"xhigh"/… line up as a column instead of dangling right after each model. Reserve the widest
    // model PIECE (name + caret) and the widest effort PIECE (word + caret) independently.
    const modelPieceW = (s) => (s.model ? this.ctxWidth(s.model) + caretW : 0);
    const effortPieceW = (s) => (s.effort ? this.ctxWidth(s.effort) + caretW : 0);
    const maxModelPiece = Math.max(0, ...vis.map(modelPieceW));
    const maxEffortPiece = Math.max(0, ...vis.map(effortPieceW));
    const effortGap = maxEffortPiece > 0 ? META_GAP : 0;
    const maxModel = Math.ceil(maxModelPiece) + effortGap + Math.ceil(maxEffortPiece);   // whole meta column width
    const maxChip = Math.max(0, ...visB.map((b) => (b ? this.badgeWidth(b.label) + 12 : 0)));
    const maxCtx = (visC.some((c) => c) || vis.some((s) => compactingNow(s))) ? BAT_W : 0;   // ctx column = battery bar
    // gear column: a per-session settings gear between the name and the model, on LIVE lanes (the user
    // 2026-06-19). Reserve its width only when there IS a live lane, so an all-historical view keeps the
    // tight [name][model] layout.
    const EYE_W = 13, EYE_GAP = 6, anyLive = vis.some((s) => s.live);
    const eyeColX = PADL + Math.ceil(maxName) + COLGAP;                              // [name] [gear] [model+effort] [chip] [ctx]
    // ONE settings-gear column again (the user 2026-07-28, round 3): the feed checkbox, postal mailbox
    // and notification bell folded into the gear's drop-down (LANE_TOGGLES), so the lane is back to a
    // single icon column between the name and the model.
    const modelColX = eyeColX + (anyLive ? EYE_W + EYE_GAP : 0);
    const effortColX = modelColX + Math.ceil(maxModelPiece) + effortGap;   // fixed left edge for EVERY lane's effort word
    const chipColX = modelColX + (maxModel > 0 ? Math.ceil(maxModel) + COLGAP : 0);
    const ctxColX = chipColX + (maxChip > 0 ? Math.ceil(maxChip) + COLGAP : 0);
    M.left = ctxColX + (maxCtx > 0 ? Math.ceil(maxCtx) + COLGAP : 4);
    // (the compacting cue is now a solid teal "compression" rect drawn per-lane below — no shared gradient)

    // judging band height: a compact judge row per JUDGES entry, shown only when there's judging
    // activity inside the current window. Folded into H so the shared axis (axisY = H - M.bottom)
    // and its gridlines span BOTH bands, with the time labels at the very bottom.
    // the two judge-set toggles (romp:settings, set in the gear) gate WHICH judges show; read fresh each draw
    const shownJudges = judgesShown();
    // the band shows when an ENABLED judge has run-spans in window (auto-nudge ⚡ marks were removed from the
    // band entirely — the user 2026-06-23; an auto-nudge still shows as a romp-logo dot on its lane)
    const shownKeys = new Set(shownJudges.map((j) => j.key));
    const jShow = !!(shownJudges.length && data.judging && data.judging.some((e) => shownKeys.has(e.judge) && inWin(e.t)));
    const bandH = jShow ? (JB_TOPGAP + shownJudges.length * JROW + JB_BOTGAP) : 0;
    const W = Math.max(640, this.wrap.clientWidth || 900);
    const plotW = W - M.left - M.right, H = M.top + Math.max(1, vis.length) * LANE_GAP + laneOffTotal + bandH + M.bottom;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('height', H); svg.setAttribute('width', W);
    // x is LINEAR in compressed time → smooth pan (only zoom rescales). Identity compress = plain linear.
    const x = (t) => M.left + (compress(t) - cT0) / winSec * plotW;
    const laneY = (i) => M.top + i * LANE_GAP + (laneOffs[i] || 0) + LANE_GAP * 0.5;
    this._geom = { ml: M.left, plotW, W, H, top: M.top, t0, t1, cT0, winSec, compress, decompress, laneOffs };

    // axis — gridlines + time labels. Ticks at real nice intervals, drawn at their compressed x (evenly
    // spaced in active regions, squished across gaps). A tick inside a collapsed gap is skipped — the
    // squiggle break + its two boundary-time labels stand in for that span.
    const inGap = (t) => cmap && cmap.gaps.some((g) => t > g.ra && t < g.rb);
    const axisY = H - M.bottom;
    // axis-label collision guard: every clock on the label row claims its x-extent; a label that
    // would overlap an already-placed one is dropped (its gridline still draws). Regular interval
    // ticks draw first so they always win — the gap-break boundary clocks (drawn after) yield.
    const placedLabels = [];
    const placeLabel = (a, b) => { for (const p of placedLabels) if (a < p[1] + 6 && b > p[0] - 6) return false; placedLabels.push([a, b]); return true; };
    // Reserve a slot at the NOW-EDGE for the lock padlock (_drawLockToggle, below) so the nearest time label
    // never renders into its area (the user 2026-06-26): seed the collision list with its x-extent up front.
    const lockCx = x(t1), lockHalf = 9;
    placedLabels.push([lockCx - lockHalf, lockCx + lockHalf]);
    for (let tk = Math.ceil(t0 / step) * step; tk <= t1; tk += step) {
      if (inGap(tk)) continue;
      svg.appendChild(el('line', { x1: x(tk), y1: M.top, x2: x(tk), y2: axisY, stroke: '#ffffff10', 'stroke-width': 1 }));
      this._mc.font = '10px ' + FONT;
      const hw = this._mc.measureText(clock(tk)).width / 2;
      if (!placeLabel(x(tk) - hw, x(tk) + hw)) continue;
      const tx = el('text', { x: x(tk), y: axisY + 14, 'text-anchor': 'middle', fill: 'var(--text-muted)', 'font-size': 10 }); tx.textContent = clock(tk); svg.appendChild(tx);
    }
    svg.appendChild(el('line', { x1: x(t1), y1: M.top, x2: x(t1), y2: axisY, stroke: '#ffffff22', 'stroke-width': 1 }));
    // broken-axis squiggle(s): one per collapsed gap visible in the window (real edges → compressed x).
    // CLAMP to the plot [M.left, plotRight]: a gap straddling a window edge would otherwise map x(g.ra)
    // LEFT of M.left → the squiggle + its label render in the gutter, over the battery column (the bug).
    if (cmap) {
      const plotR = W - M.right;
      for (const g of cmap.gaps) if (g.rb > t0 && g.ra < t1) {
        const rx0 = x(g.ra), rx1 = x(g.rb);
        const gx0 = Math.max(M.left, rx0), gx1 = Math.min(plotR, rx1);
        if (gx1 > gx0 + 0.5) this._drawGapBreak(svg, gx0, gx1, g.ra, g.rb, M.top, axisY, rx0 >= M.left - 0.5, !g.trailing && rx1 <= plotR + 0.5, placeLabel);
      }
    }
    if (!vis.length) { const tx = el('text', { x: M.left, y: M.top + 16, fill: 'var(--text-muted)', 'font-size': 12 }); tx.textContent = 'no romp activity in this window'; svg.appendChild(tx); }

    // lanes + activity bars + status chip + label
    const bgRGB = this._surfaceBg();   // surface color for the perceptual idle-fade blend
    const dag = this._dag || null;     // request-DAG journey overlay: {events:Set, msgs:Set} or null
    // feed-modal hover: a SET of ids (a parent line covers its whole subtree — union of reply events +
    // delegation messages). Each id matches either an event (turn → bar/dot) or a postal message
    // (connector/arrival dot), so the hover set feeds BOTH match helpers below.
    const hoverSet = (this._hover && this._hover.ids && this._hover.ids.length) ? new Set(this._hover.ids) : null;
    // An event glyph (bar + start dot) / a message glyph (connector + arrival dot) gets the SAME
    // native-hover treatment — thickened/grown in its OWN color — whether it's part of the DAG journey
    // (card hover) OR is in the hovered set; the user wants line-hover and card-hover and cross-hover
    // all identical (2026-07-17, replacing the white focus border). `dagOrHover(id)` = membership
    // in either; dotLit/barLit then split the event glyph by ATOM id, so a hover that carries the PROMPT
    // atom (promptId) lights only the start dot and one that carries the WORK atom (workId) lights only
    // the bar — the whole-turn id (DAG journey, coarse card hover) still lights both halves. (NB: name it
    // dagOrHover, NOT `hit` — the bar/connector loops use a LOCAL `const hit` rect; a `const hit` here
    // would put those blocks in a TDZ and crash draw() on the first in-window bar.)
    const dagOrHover = (id) => !!id && ((dag && dag.events.has(id)) || (hoverSet && hoverSet.has(id)));
    const dagOrHoverMsg = (id) => !!id && ((dag && dag.msgs.has(id)) || (hoverSet && hoverSet.has(id)));
    const compactSeen = new Set();   // sids whose compacting scan-bar is live this draw → reconcile the overlay after
    const workSeen = new Set();      // sids whose WORKING label overlay is live this draw → reconcile after (persistent pulse)
    const metaSeen = new Set();      // sids showing the "model switching…" dots this draw → reconcile the overlay after
    vis.forEach((s, i) => {
      const y = laneY(i);
      // perceptual idle fade: faded lanes blend their colors toward bgRGB to a uniform low luminance.
      const F = (hex) => s.faded ? fadeHex(hex, bgRGB) : hex;
      const fadedEls = [];   // {el, full, faded} for a faded lane → un-faded to full color while hovered
      // ONE highlight: a soft filled block (light gray, NO border) on the SELECTED lane. Selection is
      // set by clicking a lane/item, by ↑/↓, and by the chat's active tab — all the same highlight.
      // Drawn first → bars/dots sit on top.
      if (this.selectedSid === s.id) {
        svg.appendChild(el('rect', { x: 2, y: y - LANE_GAP / 2 + 1, width: W - 4, height: LANE_GAP - 2, rx: 4,
          fill: '#d6dbe2', 'fill-opacity': 0.1 }));
      }
      // full-row click target (low z): clicking ANY empty part of the row selects the lane + previews
      // it at the bottom (latest) — same as a bar, just no anchor. Non-interactive lane elements below
      // are pointer-events:none so their area falls through here; bars/dots keep their handlers on top.
      const rowHit = el('rect', { x: 0, y: y - LANE_GAP / 2, width: W, height: LANE_GAP, fill: 'transparent' });
      rowHit.style.cursor = 'grab';   // grab = drag to PAN (horizontal) or REORDER (vertical); a plain click still selects/opens
      rowHit.addEventListener('mousedown', (e) => this._beginDrag(s.id, e));   // drag starts on EMPTY row space only (bars/dots keep their click → jump-to-chat)
      rowHit.addEventListener('click', () => {
        if (this._suppressClick) { this._suppressClick = false; return; }   // just finished a drag → not a select
        this._select(s.id); this.openChat(this._laneTid(s), null, true);
      });
      // ever-so-slight hover tint on the row (much fainter than the selected block) + un-fade a faded
      // lane's colors to full while hovered, so an idle row is readable when you point at it.
      rowHit.addEventListener('mouseenter', () => { rowHit.setAttribute('fill', '#ffffff'); rowHit.setAttribute('fill-opacity', '0.035'); for (const f of fadedEls) f.el.setAttribute('fill', f.full); });
      rowHit.addEventListener('mouseleave', () => { rowHit.setAttribute('fill', 'transparent'); rowHit.removeAttribute('fill-opacity'); for (const f of fadedEls) f.el.setAttribute('fill', f.faded); });
      svg.appendChild(rowHit);
      svg.appendChild(el('line', { x1: M.left, y1: y, x2: x(t1), y2: y, stroke: '#ffffff14', 'stroke-width': 2, 'stroke-linecap': 'round', 'pointer-events': 'none' }));
      turnsOf(s.id).forEach((t) => {
        const a = Math.max(t.start, t0), b = Math.min(barEndT(t, nowS, data.now), t1); if (b <= a) return;
        // ONE bar per work period = ONE hover/summary. A permission pause is a gate WITHIN one task
        // (same ask before & after), so it does NOT split the work — it's an overlay (candy-stripe
        // below). Only a new ASK (typed/queued/absorbed/drain) starts a new period. The bar's color
        // also backs the candy-stripe.
        const bx = x(a), bw = Math.max(2, x(b) - x(a)), eh = BAR_H + 5;
        // Cross-hover focus on this work period — a DAG journey event (card hover) or the single event
        // hovered in the feed modal — draws EXACTLY like the native bar hover below: the bar itself
        // grown to eh and fully opaque, in its own color. No white outline (the user 2026-07-17).
        const lit = barLit(t, dagOrHover);
        const bh = lit ? eh : BAR_H;
        const bar = el('rect', { x: bx, y: y - bh / 2, width: bw, height: bh, rx: bh / 2, fill: s.color, opacity: lit ? 1 : 0.9 });
        svg.appendChild(bar);
        const act = s.state === 'working' || s.state === 'permission' || s.state === 'awaiting' || s.state === 'awaitingBg' || s.state === 'compacting' || s.state === 'clearing';
        const ongoing = s.live && act && t.end > t.start && (data.now - t.end) <= 5;
        const hit = el('rect', { x: bx, y: y - 7, width: bw, height: 14, fill: 'transparent' }); hit.style.cursor = 'pointer';
        const html = () => '<div class="r"><span class="chip" style="background:' + s.color + '"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="t">' + clock(t.start) + '–' + clock(t.end) + '</span></div>' + this.barBody(t, ongoing);
        const grow = (h) => { bar.setAttribute('y', y - h / 2); bar.setAttribute('height', h); bar.setAttribute('rx', h / 2); };
        hit.addEventListener('mouseenter', (e) => { grow(eh); bar.setAttribute('opacity', '1'); this.showTip(html(), e); this._emitHover(s.id, [t.id], t.start, t.end); });
        hit.addEventListener('mousemove', (e) => this.moveTip(e));
        hit.addEventListener('mouseleave', () => { grow(bh); bar.setAttribute('opacity', lit ? '1' : '0.9'); this.hideTip(); this._emitHover(null); });   // restore to the DRAWN state — a cross-lit bar stays grown
        // the BAR = the work/response: open the period's readable reply (workAnchorOf), with the
        // period's start as the by-time fallback. This was a bare lane-open with NO anchor, so every
        // work-bar click visibly did nothing while prompt-dot clicks worked (the user, 2026-06-12).
        // The prompt dot keeps the prompt-line uuid.
        hit.addEventListener('click', () => { this._select(s.id); this.openChat(t.tid || this._laneTid(s), workAnchorOf(t), false, false, t.start); });
        svg.appendChild(hit);
      });
      // AWAITING a background task while the main thread is idle (the user 2026-07-13): a full-thickness
      // segment (BAR_H, the work-bar reference) in the lane color from the last work period's end to the
      // live edge, but FADED to 0.4 alpha — "something is pending here", a faded continuation of the work
      // bar rather than active work (which stays the solid ~0.9 bar). Its own hover lists the task(s),
      // consistent with the feed's "Waiting on task" pill. Event-gated on the SAME live signal the lane
      // badge reads (s.awaitingBg — kernel _session_awaiting, non-null only while the turn is CLOSED), so
      // it appears with the wait and vanishes the moment the tasks settle or a new turn opens.
      if (s.live && s.awaitingBg) {
        let anchor = t0;                       // wait began before the window → the stretch enters from the left edge
        turnsOf(s.id).forEach((t) => { if (t.end > anchor) anchor = Math.min(t.end, t1); });
        const lx1 = x(anchor), lx2 = x(Math.max(anchor, Math.min(nowS, t1)));
        if (lx2 - lx1 > 3) {
          const ln = el('line', { x1: lx1, y1: y, x2: lx2, y2: y, stroke: s.color, 'stroke-width': BAR_H,
            'stroke-linecap': 'round', opacity: 0.4, 'pointer-events': 'none' });
          svg.appendChild(ln);
          const rows = ((s.awaitingTasks && s.awaitingTasks.length) ? s.awaitingTasks : [s.awaitingBg])
            .map((d) => '<div class="b" style="opacity:.85">' + esc(d) + '</div>').join('');
          const tip = '<div class="r"><span class="chip" style="background:' + s.color + '"></span><span class="who" style="color:' + s.color + '">' + esc(s.name)
            + '</span><span class="t">' + clock(anchor) + '– waiting…</span></div>' + rows;
          const wh = el('rect', { x: lx1, y: y - 7, width: lx2 - lx1, height: 14, fill: 'transparent' }); wh.style.cursor = 'grab';
          wh.addEventListener('mouseenter', (e) => { ln.setAttribute('stroke-width', String(BAR_H + 2)); ln.setAttribute('opacity', '0.6'); this.showTip(tip, e); });
          wh.addEventListener('mousemove', (e) => this.moveTip(e));
          wh.addEventListener('mouseleave', () => { ln.setAttribute('stroke-width', String(BAR_H)); ln.setAttribute('opacity', '0.4'); this.hideTip(); });
          // the stretch keeps the empty-row behaviors it covers: drag to pan/reorder, click to select/open
          wh.addEventListener('mousedown', (e) => this._beginDrag(s.id, e));
          wh.addEventListener('click', () => {
            if (this._suppressClick) { this._suppressClick = false; return; }
            this._select(s.id); this.openChat(this._laneTid(s), null, true);
          });
          svg.appendChild(wh);
        }
      }
      // AWAITING (permission) → candy-stripe every span the session sat blocked on your
      // input (historical, from the state-transition log), plus the current open one. The
      // dashed white overlay reads as a distinct texture vs a solid "still working" bar.
      const aw = (s.awaiting && s.awaiting.length) ? s.awaiting
                 : ((s.live && (s.state === 'permission' || s.state === 'awaiting') && s.since != null) ? [[s.since, t1]] : []);
      for (const span of aw) {
        const a0 = span[0], b0 = span[1];
        const sa = Math.max(a0, t0), sb = Math.min(b0, t1); if (sb <= sa) continue;
        // The awaiting interval (state log) and the work bars (transcript) come from different
        // sources, so a bar can end a few seconds BEFORE the permission prompt → a gap (made worse by
        // rounded caps). Bridge the colored backing to the adjacent work bars (within BRIDGE) and overlap
        // their rounded caps so the candy-cane reads as one continuous lane, not a floating segment.
        const BRIDGE = 180;
        let pe = null, ns = null;
        for (const t of turnsOf(s.id)) {
          if (t.end <= t.start) continue;
          if (t.end <= a0 + 1 && (pe == null || t.end > pe)) pe = t.end;
          if (t.start >= b0 - 1 && (ns == null || t.start < ns)) ns = t.start;
        }
        let bx0 = x(sa), bx1 = x(sb);
        if (pe != null && a0 - pe <= BRIDGE) bx0 = Math.min(bx0, x(Math.max(pe, t0)) - BAR_H / 2);
        if (ns != null && ns - b0 <= BRIDGE) bx1 = Math.max(bx1, x(Math.min(ns, t1)) + BAR_H / 2);
        const eh = BAR_H + 5;
        // colored backing bridges the gap (square caps so it merges with the rounded bars on either side)
        const back = el('rect', { x: bx0, y: y - BAR_H / 2, width: Math.max(2, bx1 - bx0), height: BAR_H, fill: s.color, opacity: 0.9 });
        svg.appendChild(back);
        // candy-cane stripes over the ACTUAL awaiting span (shows the session color THROUGH the stripes)
        const stripe = el('rect', { x: x(sa), y: y - BAR_H / 2, width: Math.max(2, x(sb) - x(sa)), height: BAR_H, fill: 'url(#vault-await-hatch)' });
        svg.appendChild(stripe);
        const sh = el('rect', { x: bx0, y: y - 7, width: Math.max(2, bx1 - bx0), height: 14, fill: 'transparent' }); sh.style.cursor = 'pointer';
        const end = b0 >= data.now - 2 ? 'now' : clock(b0);
        const shtml = () => '<div class="r"><span class="chip" style="background:' + BADGE.attention.bg + '"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="k">blocked</span></div><div class="b">blocked on your input · ' + clock(a0) + '–' + end + '</div>';
        const grow = (h) => { for (const r of [back, stripe]) { r.setAttribute('y', y - h / 2); r.setAttribute('height', h); } };
        sh.addEventListener('mouseenter', (e) => { grow(eh); this.showTip(shtml(), e); });
        sh.addEventListener('mousemove', (e) => this.moveTip(e));
        sh.addEventListener('mouseleave', () => { grow(BAR_H); this.hideTip(); });
        sh.addEventListener('click', () => { this._select(s.id); this.openChat(this._laneTid(s), null, true); });
        svg.appendChild(sh);
      }
      // CONTEXT COMPACTING (LIVE) → cyan cross-hatch over the session color for every span the session
      // sat compacting (PreCompact→PostCompact from the state log), plus the current open one if it's
      // compacting RIGHT NOW. This is the in-progress indicator; the isCompactSummary marker below is the
      // after-the-fact one. Same figure-ground as the awaiting candy-cane.
      const comp = (s.compacting && s.compacting.length) ? s.compacting
                   : ((s.live && s.state === 'compacting' && s.since != null) ? [[s.since, t1]] : []);
      for (const span of comp) {
        const a0 = span[0], b0 = span[1];
        const sa = Math.max(a0, t0), sb = Math.min(b0, t1); if (sb <= sa) continue;
        const eh = BAR_H + 5, cx = x(sa), cw = Math.max(2, x(sb) - x(sa));
        const cback = el('rect', { x: cx, y: y - BAR_H / 2, width: cw, height: BAR_H, rx: 2, fill: s.color, opacity: 0.9 });
        svg.appendChild(cback);
        const chx = el('rect', { x: cx, y: y - BAR_H / 2, width: cw, height: BAR_H, rx: 2, fill: 'url(#vault-compact-hatch)' });
        svg.appendChild(chx);
        const ch = el('rect', { x: cx, y: y - 7, width: cw, height: 14, fill: 'transparent' }); ch.style.cursor = 'pointer';
        const live = b0 >= data.now - 2;
        const cw2 = live ? 'compacting' : 'compacted';
        const chtml = () => '<div class="r"><span class="chip" style="background:#86e1ff"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="k">' + cw2 + '</span></div><div class="b">context ' + cw2 + ' · ' + clock(a0) + '–' + (live ? 'now' : clock(b0)) + '</div>';
        const cgrow = (h) => { for (const r of [cback, chx]) { r.setAttribute('y', y - h / 2); r.setAttribute('height', h); } };
        ch.addEventListener('mouseenter', (e) => { cgrow(eh); this.showTip(chtml(), e); });
        ch.addEventListener('mousemove', (e) => this.moveTip(e));
        ch.addEventListener('mouseleave', () => { cgrow(BAR_H); this.hideTip(); });
        ch.addEventListener('click', () => { this._select(s.id); this.openChat(this._laneTid(s), null, true); });
        svg.appendChild(ch);
      }
      // CONTEXT COMPACTION → a cyan cross-hatch SPAN over the session color (same figure-ground as the
      // awaiting candy-cane: identity color behind, texture in front). The span runs from compaction
      // START (prev real event) to completion (cp.t), CLAMPED to the last CCAP seconds so a long
      // idle-then-/compact gap doesn't stretch the bar — the compaction itself is at most a minute or two.
      const CCAP = 300;
      for (const cp of (s.compactions || [])) {
        if (cp.t < t0 || cp.t > t1) continue;
        const cs = Math.max(cp.t - CCAP, t0);
        const ce = Math.min(cp.t, t1);
        const cx = x(cs), cw = Math.max(6, x(ce) - cx), eh = BAR_H + 5;
        const cback = el('rect', { x: cx, y: y - BAR_H / 2, width: cw, height: BAR_H, rx: 2, fill: s.color, opacity: 0.9 });
        svg.appendChild(cback);
        const chx = el('rect', { x: cx, y: y - BAR_H / 2, width: cw, height: BAR_H, rx: 2, fill: 'url(#vault-compact-hatch)' });
        svg.appendChild(chx);
        const ch = el('rect', { x: cx, y: y - 7, width: cw, height: 14, fill: 'transparent' }); ch.style.cursor = 'pointer';
        const chtml = () => '<div class="r"><span class="chip" style="background:#86e1ff"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="k">compacted</span></div><div class="b">context compacted · ' + clock(cp.t) + '</div>';
        const cgrow = (h) => { for (const r of [cback, chx]) { r.setAttribute('y', y - h / 2); r.setAttribute('height', h); } };
        ch.addEventListener('mouseenter', (e) => { cgrow(eh); this.showTip(chtml(), e); });
        ch.addEventListener('mousemove', (e) => this.moveTip(e));
        ch.addEventListener('mouseleave', () => { cgrow(BAR_H); this.hideTip(); });
        ch.addEventListener('click', () => { this._select(s.id); this.openChat(this._laneTid(s), null, true); });
        svg.appendChild(ch);
      }
      // A /CLEAR SEAM — an episode boundary: the conversation ended here and a blank one began. Drawn
      // as a film-splice cut through the lane (two short slanted strokes), quiet by default with the
      // mechanics on hover — the lane stays CONTINUOUS across it (same session, same slot); only the
      // conversation restarted. Fed by the kernel's episodes log (row 0 = first observation, not a clear).
      for (const cl of (s.clears || [])) {
        if (cl.t < t0 || cl.t > t1) continue;
        const sx = x(cl.t), sh = BAR_H + 6;
        for (const dx of [-1.6, 1.6]) {
          svg.appendChild(el('line', { x1: sx + dx - 2, y1: y + sh / 2, x2: sx + dx + 2, y2: y - sh / 2,
                                       stroke: '#ffffff', 'stroke-width': 1.2, opacity: 0.55, 'pointer-events': 'none' }));
        }
        const hh = el('rect', { x: sx - 6, y: y - 10, width: 12, height: 20, fill: 'transparent' });
        const html = () => '<div class="r"><span class="chip" style="background:#9cd2ff"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="k">cleared</span></div><div class="b">conversation cleared · a fresh one starts here · ' + clock(cl.t) + '</div>';
        hh.addEventListener('mouseenter', (e) => this.showTip(html(), e));
        hh.addEventListener('mousemove', (e) => this.moveTip(e));
        hh.addEventListener('mouseleave', () => this.hideTip());
        svg.appendChild(hh);
      }
      // name left-aligned; status chip in the shared chip column to its right. ENDED or idle >1h
      // (s.faded) → name/chip/ctx blended toward the surface bg to a uniform low luminance (perceptual
      // fade via F(), consistent across hues + with the chat tabs), instead of a flat opacity.
      const lblA = { x: PADL, y: y + 3.5, 'text-anchor': 'start', 'font-weight': 650, 'font-size': 12, fill: F(s.color), 'pointer-events': 'none' };
      if (!s.live) lblA['text-decoration'] = 'line-through';   // dead lane → strike the name (mirrors the feed)
      const lbl = el('text', lblA);
      // A FEDERATED session's "host:" prefix is metadata, not part of the name (the user 2026-07-11):
      // render it quiet — gray, not bold, italic, a step smaller — like every other surface's
      // .host-prefix. The marker is the sid's own prefix (federation prefixes id AND name; a local sid
      // never contains a colon), the same rule as ui/webview/host-prefix.ts.
      const hci = String(s.id || '').indexOf(':');
      const hpre = hci > 0 ? String(s.id).slice(0, hci + 1) : null;
      let hostTsp = null;
      if (hpre && s.name && s.name.startsWith(hpre) && s.name.length > hpre.length) {
        // its own fill, so the parent <text>'s faded color can NOT reach it — fade it explicitly (and
        // un-fade it with the name on hover, below) or "host:" outshines the dimmed name (the user 2026-07-22)
        hostTsp = el('tspan', { fill: F(MODEL_FG), 'font-weight': 400, 'font-style': 'italic', 'font-size': 10.5 });
        // A lane whose HOST is unreachable strikes the "host:" token (the user 2026-07-29): the lane
        // keeps its bars — they happened — but nothing on it is current, and a frozen lane is otherwise
        // indistinguishable from an idle one. Striking the host, not the name, keeps it distinct from
        // the dead-session strike above (that one crosses the whole label).
        if (_rompHostDown(s.id)) hostTsp.setAttribute('text-decoration', 'line-through');
        hostTsp.textContent = hpre;
        const tn = el('tspan', {}); tn.textContent = s.name.slice(hpre.length);
        lbl.appendChild(hostTsp); lbl.appendChild(tn);
      } else { lbl.textContent = s.name; }
      svg.appendChild(lbl);
      if (s.faded) {
        fadedEls.push({ el: lbl, full: s.color, faded: F(s.color) });
        if (hostTsp) fadedEls.push({ el: hostTsp, full: MODEL_FG, faded: F(MODEL_FG) });
      }
      // Clicking the NAME JUMPS you into that session in the chat — focus and all (the user 2026-07-03).
      // The empty row (rowHit) only PREVIEWS it (preserveFocus=true, no focus steal); the name is the
      // "take me there" affordance, so it opens with focus=true. It's still a drag handle: mousedown starts
      // the same reorder/pan gesture as the rest of the row (a plain click, no movement, falls through to the
      // click → jump; a real drag sets _suppressClick so it doesn't also jump). The drawn name is
      // pointer-events:none, so this transparent rect over its box is the real hit target.
      const nhw = Math.min(Math.ceil(this.labelWidth(s.name)) + 6, (eyeColX - COLGAP) - (PADL - 3));
      const nhit = el('rect', { x: PADL - 3, y: y - 9, width: Math.max(0, nhw), height: 18, fill: 'transparent', 'pointer-events': 'all' });
      nhit.style.cursor = 'pointer';
      nhit.addEventListener('mousedown', (e) => this._beginDrag(s.id, e));   // still drag-to-reorder / pan
      nhit.addEventListener('click', () => {
        if (this._suppressClick) { this._suppressClick = false; return; }   // just finished a drag → not a jump
        this._select(s.id); this.openChat(this._laneTid(s), null, false);   // focus=true → jump into the chat
      });
      // keep the row's hover treatment (faint tint + un-fade) while pointing at the name
      nhit.addEventListener('mouseenter', () => { rowHit.setAttribute('fill', '#ffffff'); rowHit.setAttribute('fill-opacity', '0.035'); for (const f of fadedEls) f.el.setAttribute('fill', f.full); });
      nhit.addEventListener('mouseleave', () => { rowHit.setAttribute('fill', 'transparent'); rowHit.removeAttribute('fill-opacity'); for (const f of fadedEls) f.el.setAttribute('fill', f.faded); });
      svg.appendChild(nhit);
      // per-session settings GEAR (live lanes only — the user 2026-07-28, round 3): the feed checkbox,
      // postal mailbox and notification bell no longer draw on the lane (three always-on icons crowded
      // it) — ONE gear opens them as a drop-down with a labelled, explained row per toggle
      // (_openLaneMenu / LANE_TOGGLES). Same hit-rect + showTip treatment as the old icons; opens on
      // POINTERDOWN (a redraw between mousedown and mouseup would eat a plain click).
      if (s.live) {
        const gcx = eyeColX + 5, gcy = y + 0.5;
        const gbox = gearIcon(gcx, gcy, MODEL_FG);
        gbox.setAttribute('opacity', '0.75');
        svg.appendChild(gbox);
        const ghit = el('rect', { x: eyeColX - 4, y: y - 9, width: EYE_W + 8, height: 18, fill: 'transparent', 'pointer-events': 'all' });
        ghit.style.cursor = 'pointer';
        ghit.setAttribute('aria-label', 'session settings'); svg.appendChild(ghit);
        const gtip = "Session settings<div style='opacity:.65;margin-top:2px'>feed cards, postal service, notifications</div>";
        ghit.addEventListener('mouseenter', (e) => { gbox.setAttribute('opacity', '1'); this.showTip(gtip, e); });
        ghit.addEventListener('mousemove', (e) => this.moveTip(e));
        ghit.addEventListener('mouseleave', () => { gbox.setAttribute('opacity', '0.75'); this.hideTip(); });
        ghit.addEventListener('pointerdown', (e) => {
          e.stopPropagation();
          this.hideTip();
          this._openLaneMenu(s, ghit);
        });
      }
      // DEAD lane → a "Clear" pill just right of the struck name (the user 2026-07-02). A dead session lingers
      // as a faded/struck lane while it's still in the activity window, with NONE of the live controls (no
      // feed/postal toggle, no model picker, no chip, no ctx battery — all empty), so the row right of the name
      // is free. The pill mirrors the feed cards' Clear button chrome (outlined, dim → red fill on hover) and
      // dismisses the leftover lane. The kernel forgets the dismissal on restart, so `romp refresh` brings a
      // mistakenly-cleared lane back. pointerdown (not click): a poll redraw between mousedown/up would eat a
      // 'click', same as the feed/postal toggles above.
      if (!s.live) {
        const CL_H = 15, CL_PAD = 7, CL_RED = '#c74e39';
        const cw = Math.ceil(this.ctxWidth('Clear')), bw = cw + CL_PAD * 2, bx = eyeColX, by = y - CL_H / 2;
        const box = el('rect', { x: bx, y: by, width: bw, height: CL_H, rx: 6, fill: 'transparent', stroke: MODEL_FG, 'stroke-width': 1, 'stroke-opacity': 0.5, 'pointer-events': 'none' });
        svg.appendChild(box);
        const ctx = el('text', { x: bx + bw / 2, y: y + 3.5, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 600, fill: MODEL_FG, 'pointer-events': 'none' });
        ctx.textContent = 'Clear'; svg.appendChild(ctx);
        const chit = el('rect', { x: bx, y: by, width: bw, height: CL_H, rx: 6, fill: 'transparent' });
        chit.style.cursor = 'pointer'; chit.setAttribute('aria-label', 'clear this ended session from the timeline');
        chit.addEventListener('mouseenter', (e) => {
          box.setAttribute('fill', CL_RED); box.setAttribute('stroke', CL_RED); box.setAttribute('stroke-opacity', '1'); ctx.setAttribute('fill', '#ffffff');
          this.showTip("Clear this ended session from the timeline<div style='opacity:.65;margin-top:2px'>a kernel restart (romp refresh) brings it back</div>", e);
        });
        chit.addEventListener('mousemove', (e) => this.moveTip(e));
        chit.addEventListener('mouseleave', () => {
          box.setAttribute('fill', 'transparent'); box.setAttribute('stroke', MODEL_FG); box.setAttribute('stroke-opacity', '0.5'); ctx.setAttribute('fill', MODEL_FG); this.hideTip();
        });
        chit.addEventListener('pointerdown', (e) => {
          e.stopPropagation(); this.hideTip();
          // optimistic: drop it from the current frame so it vanishes at once, and hold it in _dismissed so
          // a stale or federation-merged push can't put it back before the kernel confirms (_reconcileDismissed).
          // A restart — forgetting it kernel-side — still brings it back, as designed.
          this._dismissed.add(s.id);
          if (this.data && this.data.sessions) this.data.sessions = this.data.sessions.filter((x) => x.id !== s.id);
          this._dismissLane(s.id); this.draw();
        });
        svg.appendChild(chit);
      }
      // model + effort, muted, between the name and the state chip (left-aligned in its column). On a
      // LIVE lane each word is a drop-down picker — hover reveals a ▾ caret, click opens a menu whose
      // pick injects /model or /effort into that pane. Dead/historical lanes render it as static text.
      if (s.model || s.effort) {   // a freshly-launched SDK lane has no model for a few seconds, but effort is always known
        if (!s.live) {
          // static text (no picker), but STILL split model @ modelColX / effort @ effortColX so a dead lane's
          // effort lines up in the same column as the live lanes' (the user 2026-07-03).
          const staticPiece = (word, sx) => {
            const mt = el('text', { x: sx, y: y + 3.5, 'text-anchor': 'start', 'font-size': 11, 'font-weight': 600, fill: F(MODEL_FG), 'pointer-events': 'none' });
            mt.textContent = word; svg.appendChild(mt);
            if (s.faded) fadedEls.push({ el: mt, full: MODEL_FG, faded: F(MODEL_FG) });
          };
          if (s.model) staticPiece(s.model, modelColX);
          if (s.effort) staticPiece(s.effort, effortColX);
        } else {
          const pendingOf = (kind) => {
            // A /model switch is server-driven now (kernel modelPending): show the pending cue until the NEW
            // name actually lands — event-based, no timeout (the user 2026-07-03). The local _metaPending click
            // heuristic still covers effort (no server flag) + the sub-second before the first server push.
            if (kind === 'model' && s.modelPending) return true;
            const p = this._metaPending[s.id + ':' + kind]; if (!p) return false;
            const cur = (kind === 'model' ? s.model : s.effort) || '';
            if (cur !== p.was || nowMs > p.until) { delete this._metaPending[s.id + ':' + kind]; return false; }
            return true;
          };
          const drawPiece = (kind, word, sx) => {
            const pend = pendingOf(kind), ww = this.ctxWidth(word);
            // A switching MODEL shows the pulsing accent-blue dots (the chat badge's .meta-dots motif) in place
            // of the stale name, rather than just dimming it (the user 2026-07-03). The name <text> stays (fully
            // transparent) so it keeps its click target + reserves the column width; the dots overlay stands in.
            const dots = kind === 'model' && pend;
            // Tint the model name / effort on the GLOBAL colormap by capability/effort rank (kernel modelColor/
            // effortColor, the user 2026-07-02); unknown → the default gray. The caret stays neutral gray, and
            // hover still brightens to META_HOVER_FG — mouseleave restores the TINT, not the gray.
            const tint = kind === 'model' ? s.modelColor : s.effortColor;
            const base = (tint && tint.length === 3) ? ('rgb(' + tint[0] + ',' + tint[1] + ',' + tint[2] + ')') : MODEL_FG;
            const wt = el('text', { x: sx, y: y + 3.5, 'text-anchor': 'start', 'font-size': 11, 'font-weight': 600, fill: base, 'pointer-events': 'auto' });
            wt.textContent = word; wt.style.cursor = 'pointer';
            if (dots) wt.setAttribute('opacity', '0'); else if (pend) wt.setAttribute('opacity', '0.45');
            svg.appendChild(wt);
            const ct = el('text', { x: sx + ww, y: y + 3.5, 'text-anchor': 'start', 'font-size': 11, 'font-weight': 600, fill: MODEL_FG, opacity: (pend && !dots) ? '0.45' : '0', 'pointer-events': 'none' });
            ct.textContent = META_CARET; svg.appendChild(ct);
            wt.addEventListener('mouseenter', () => { if (dots) return; wt.setAttribute('fill', META_HOVER_FG); ct.setAttribute('fill', META_HOVER_FG); ct.setAttribute('opacity', '1'); });
            wt.addEventListener('mouseleave', () => { if (dots) return; wt.setAttribute('fill', base); ct.setAttribute('fill', MODEL_FG); ct.setAttribute('opacity', pendingOf(kind) ? '0.45' : '0'); });
            wt.addEventListener('click', (e) => { e.stopPropagation(); this._openMetaMenu(kind, s, wt); });
            if (dots) { this._positionMetaDots(s.id, sx, y); metaSeen.add(s.id); }
          };
          // model @ its column, effort @ the FIXED effort column — so efforts line up across lanes
          if (s.model) drawPiece('model', s.model, modelColX);
          if (s.effort) drawPiece('effort', s.effort, effortColX);
        }
      }
      const bdg = visB[i];
      if (bdg) {
        const h = 14, padX = 6, w = Math.ceil(this.badgeWidth(bdg.label)) + padX * 2, by = y - h / 2;
        const chipBg = el('rect', { x: chipColX, y: by, width: w, height: h, rx: h / 2, fill: F(bdg.bg), 'pointer-events': 'none' }); svg.appendChild(chipBg);
        if (s.state === 'working') {
          // WORKING label breathes black↔teal on the 1.5s ease-in-out-sine clock, exactly like the chat chip's
          // .chip-pulse. It rides a PERSISTENT CSS-animated overlay div (compositor-driven) that draw() only
          // repositions — NOT a per-<text> SMIL <animate>, which stuttered/truncated because the SVG wipe
          // recreated it at the (irregular) redraw cadence (the user 2026-07-01). Working sessions are never
          // faded, so no fade handling needed here.
          this._positionWorkLabel(s.id, chipColX + w / 2, y, bdg.label);
          workSeen.add(s.id);
        } else {
          const bt = el('text', { x: chipColX + w / 2, y: y + 3, 'text-anchor': 'middle', fill: F(bdg.fg), 'font-size': BADGE_FS, 'font-weight': 700, 'pointer-events': 'none' });
          bt.setAttribute('letter-spacing', '0.03em'); bt.textContent = bdg.label;
          svg.appendChild(bt);
          if (s.faded) { fadedEls.push({ el: chipBg, full: bdg.bg, faded: F(bdg.bg) }); fadedEls.push({ el: bt, full: bdg.fg, faded: F(bdg.fg) }); }
        }
      }
      // context-window battery bar (matches the chat view): faint box + level-colored fill (width ∝ pct)
      // + "N%" inside. While COMPACTING it instead shows a rainbow scan-bar (no %), the live cue.
      const cinfo = visC[i], isComp = compactingNow(s);
      if (cinfo || isComp) {
        const byTop = y - BAT_H / 2;
        svg.appendChild(el('rect', { x: ctxColX, y: byTop, width: BAT_W, height: BAT_H, rx: 3, fill: 'rgba(255,255,255,0.07)', stroke: 'rgba(255,255,255,0.35)', 'stroke-width': 1, 'pointer-events': 'none' }));
        if (isComp) {
          // a solid TEAL rectangle fills the battery, then its RIGHT edge slides left (width shrinks via a
          // scaleX from a fixed left edge) — a "compression" cue, fading in full + out when compressed so the
          // loop never snaps. No % while compacting. This rides a PERSISTENT overlay div (CSS-animated on the
          // compositor) that draw() only REPOSITIONS, not a per-frame-recreated SVG <rect> — so it glides
          // smoothly regardless of the redraw cadence (the user 2026-06-29: the SVG sweep read as jumpy because
          // draw() destroyed+rebuilt it every rAF/poll). _positionCompactBar reconciles _compactBars by sid.
          this._positionCompactBar(s.id, ctxColX + 1, byTop + 1, BAT_W - 2, BAT_H - 2);
          compactSeen.add(s.id);
        } else {
          const innerW = BAT_W - 2, fillW = Math.max(0, Math.min(1, cinfo.pct / 100)) * innerW, fillCol = F(cinfo.color);
          if (fillW > 0.5) {
            const fr = el('rect', { x: ctxColX + 1, y: byTop + 1, width: fillW, height: BAT_H - 2, rx: 2, fill: fillCol, 'pointer-events': 'none' });
            svg.appendChild(fr);
            if (s.faded) fadedEls.push({ el: fr, full: cinfo.color, faded: fillCol });
          }
          const ct = el('text', { x: ctxColX + BAT_W / 2, y: y + 3, 'text-anchor': 'middle', fill: '#ffffff', 'font-size': 9, 'font-weight': 700, 'font-family': 'ui-monospace, SFMono-Regular, Menlo, monospace', 'pointer-events': 'none' });
          ct.setAttribute('style', 'text-shadow: 0 0 2px rgba(0,0,0,.75)');
          ct.textContent = cinfo.label; svg.appendChild(ct);
        }
        // CLICK the battery → send /compact to that live session; optimistically show the cue at once.
        if (s.live) {
          const hit = el('rect', { x: ctxColX, y: byTop, width: BAT_W, height: BAT_H, rx: 3, fill: 'transparent' });
          hit.style.cursor = 'pointer';
          const cmt = () => '<div class="r"><span class="chip" style="background:' + s.color + '"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="k">' + (isComp ? 'compacting' : ((cinfo ? cinfo.label : '') + ' context')) + '</span></div><div class="b">' + (isComp ? 'compaction in progress' : 'click to /compact this session') + '</div>';
          hit.addEventListener('mouseenter', (e) => this.showTip(cmt(), e));
          hit.addEventListener('mousemove', (e) => this.moveTip(e));
          hit.addEventListener('mouseleave', () => this.hideTip());
          // Act on POINTERDOWN, not a click or a down→up pair: when the timeline pane isn't focused, the first
          // press focuses it AND a poll redraw lands mid-press, which REPLACES this hit-rect — so the matching
          // pointerup (like a synthesized click) fires on a different node and is dropped, and you have to click
          // twice ("first click only focuses, second acts", the user 2026-07-02). Firing on pointerdown lands
          // the action on the very first press, before any redraw can interrupt — the SAME fix the feed/postal
          // lane toggles already use for this exact reason. A press on the battery is always intent to compact
          // (it starts no drag: only the empty rowHit does), so there's nothing to wait for a release to confirm.
          hit.addEventListener('pointerdown', (e) => {
            if (e.button !== 0) return;
            e.stopPropagation(); this.hideTip();
            this._compactClicked[s.id] = (Date.now ? Date.now() : 0);
            this._compactSession(s.name); this.draw();
          });
          svg.appendChild(hit);
        }
      }
      // (The 📬 unread/parked-mail glyph was removed 2026-06-24: the DOTTED message-flow connector already
      // signals an undelivered/waiting message between sessions, so the emoji was redundant.)
    });
    this._reapCompactBars(compactSeen);   // drop overlay scan-bars for lanes no longer compacting / off-screen
    this._reapWorkLabels(workSeen);        // drop overlay WORKING labels for lanes no longer working / off-screen
    this._reapMetaDots(metaSeen);          // drop switching-dots overlays for lanes whose /model pick has landed / off-screen

    // obstacles for routing — at each event's process-start (a pending event rides `now` via execAt/startAt)
    const obstacles = [];
    data.messages.forEach((mm) => { if (inWin(execAt(mm)) && vidx[mm.toId] != null) obstacles.push({ x: x(execAt(mm)), lane: vidx[mm.toId] }); });
    vis.forEach((s, i) => turnsOf(s.id).forEach((t) => { if (inWin(startAt(t))) obstacles.push({ x: x(startAt(t)), lane: i }); }));

    // one connector per directed FLOW (A→B): a single line spanning the flow's first
    // send → last delivery, whose THICKNESS grows linearly with the message count
    // (no cap). Drawn at alpha .5 and colored by sender, so the two directions (which
    // sit at slightly different tracks) read even where they overlap. The per-message
    // arrival dots below still mark each individual message.
    const flows = {};
    data.messages.forEach((mm) => {
      if (vidx[mm.fromId] == null || vidx[mm.toId] == null) return;
      if (execAt(mm) < t0 || mm.sent > t1) return;
      const k = mm.fromId + '|' + mm.toId;
      const f = flows[k] || (flows[k] = { from: mm.fromId, to: mm.toId, n: 0, sent: Infinity, exec: -Infinity, last: null });
      f.n++; if (mm.sent < f.sent) f.sent = mm.sent; if (mm.exec > f.exec) f.exec = mm.exec;
      if (!f.last || mm.exec > f.last.exec) f.last = mm;
    });
    // base flow lines (thickness ∝ message count) — the visual band per directed flow
    const flowW = {};
    Object.keys(flows).forEach((k) => {
      const f = flows[k];
      const sLane = vidx[f.from], rLane = vidx[f.to];
      const xs = x(Math.max(f.sent, t0)), ys = laneY(sLane), xe = x(f.exec), ye = laneY(rLane), col = colorOf(f.from);
      const dir = (ys < ye) ? 1 : -1;
      const track = ye - dir * MSG_DROP;
      const xc = crossX(sLane, rLane, xs, xe, obstacles);
      const pts = (xc > xs + 0.5) ? [{ x: xs, y: ys }, { x: xc, y: ys }, { x: xc, y: track }, { x: xe, y: track }, { x: xe, y: ye }]
                                  : [{ x: xs, y: ys }, { x: xs, y: track }, { x: xe, y: track }, { x: xe, y: ye }];
      const d = roundedPath(pts, CORNER);
      const w = MSG_W0 + (f.n - 1) * MSG_GROW;   // thickness ∝ message count, no max cap
      flowW[k] = w;   // band no longer drawn — per-message connectors below draw each message's own line
    });
    // Per-message connector + arrival dot are ONE interactive unit: hovering the line OR the dot
    // co-highlights both and shows the tooltip; clicking either jumps to where the message LANDED
    // (the recipient's transcript at exec). No longer separate hover/click targets.
    const msgUI = {};   // message index → { hl, dot }, shared across the line + dot passes
    const msgHtml = (mm) => () => { const col = colorOf(mm.fromId); return '<div class="r"><span class="chip" style="background:' + col + '"></span><span class="who" style="color:' + col + '">' + esc(mm.from) + '</span><span class="ar">→</span><span class="who" style="color:' + colorOf(mm.toId) + '">' + esc(mm.to) + '</span>' + (mm.pending ? ' <span class="k">pending</span>' : '') + '<span class="t">' + clock(mm.sent) + (mm.pending ? ' → …' : ' → ' + clock(mm.exec)) + '</span></div>' + this.body(esc(mm.summary || mm.text || '')); };
    const msgNav = (mm) => () => { const an = this.nearestTurnAnchor(mm.toId, execAt(mm)); this._select(mm.toId); this.openChat((an && an.tid) || mm.toId, mm.id || (an && (an.uuid || an.replyUuid)), false, false, execAt(mm)); };   // land on the message's OWN postal card BY ID — the chat matches mm.id to the card's data-mid (the user 2026-06-20); nearest-turn uuid / time only as fallback
    // PASS 1: connector line + highlight (drawn first so the dots sit on top).
    data.messages.forEach((mm, i) => {
      if (vidx[mm.fromId] == null || vidx[mm.toId] == null) return;
      if (execAt(mm) < t0 || mm.sent > t1) return;
      const sLane = vidx[mm.fromId], rLane = vidx[mm.toId];
      const offL = mm.sent < t0;   // sent BEFORE the visible window — only the delivery is in view
      const xs = x(offL ? t0 : mm.sent), ys = laneY(sLane), xe = x(execAt(mm)), ye = laneY(rLane), col = colorOf(mm.fromId);
      const dir = (ys < ye) ? 1 : -1, track = ye - dir * MSG_DROP;
      const xc = crossX(sLane, rLane, xs, xe, obstacles);
      // An off-window send used to CLAMP to the left edge and hug the sender's lane all the way to the
      // crossing — which read as "sent at the window's start", a send time that never existed (the user
      // 2026-08-06: "a timing issue maybe?"). Enter from the edge at the crossing track height instead,
      // so an off-screen send reads as exactly that; the tooltip carries the true send time.
      const pts = offL ? [{ x: xs, y: track }, { x: xe, y: track }, { x: xe, y: ye }]
                : (xc > xs + 0.5) ? [{ x: xs, y: ys }, { x: xc, y: ys }, { x: xc, y: track }, { x: xe, y: track }, { x: xe, y: ye }]
                                  : [{ x: xs, y: ys }, { x: xs, y: track }, { x: xe, y: track }, { x: xe, y: ye }];
      const d = roundedPath(pts, CORNER);
      const lineAttr = { d, fill: 'none', stroke: col, 'stroke-width': MSG_W0, opacity: mm.pending ? 0.4 : 0.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
      if (mm.pending) lineAttr['stroke-dasharray'] = '1 4';
      svg.appendChild(el('path', lineAttr));
      // A connector in the focused journey (DAG card hover) or the hovered subtree's delegation messages
      // lights EXACTLY like its native hover: the own-color highlight overlay at full strength — no white
      // casing (the user 2026-07-17). msgLit remembers the drawn state so a local mouseleave restores it.
      const msgLit = dagOrHoverMsg(mm.id);
      const hl = el('path', { d, fill: 'none', stroke: col, 'stroke-width': MSG_W0 + 3, opacity: msgLit ? 0.95 : 0, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
      svg.appendChild(hl);
      const u = (msgUI[i] = { hl, dot: null, lit: msgLit });
      // The hit target is BUILT here but APPENDED in a final pass below, after the arrival dots
      // (the user 2026-07-21, who found that hovering the vertical part didn't pop up the tooltip and they had to hit
      // the horizontal part or the dot). Appended here it sat UNDER every dot drawn afterwards, so on
      // a connector's vertical runs — which start and end AT the lanes, exactly where the dots are —
      // most of the line was covered and the hover landed on whatever dot was on top. Round caps/joins
      // so the hit follows the rounded corners to the very ends instead of stopping short (butt caps),
      // and a wider stroke so a short vertical (an immediately-delivered message is almost ALL vertical)
      // is still an easy target.
      const hit = el('path', { d, fill: 'none', stroke: 'transparent', 'stroke-width': MSG_HIT_W, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }); hit.style.cursor = 'pointer';
      const mEnter = (e) => { hl.setAttribute('opacity', '0.95'); if (u.dot) u.dot.setAttribute('r', DOT_R + 2); this.showTip(msgHtml(mm)(), e); };
      hit.__tlHoverIn = mEnter;                    // re-armable after a redraw rebuilds this path (_rehover)
      hit.addEventListener('mouseenter', mEnter);
      hit.addEventListener('mousemove', (e) => this.moveTip(e));
      hit.addEventListener('mouseleave', () => { hl.setAttribute('opacity', msgLit ? '0.95' : '0'); if (u.dot) u.dot.setAttribute('r', msgLit ? DOT_R + 2 : DOT_R); this.hideTip(); });
      hit.addEventListener('click', msgNav(mm));
      u.hit = hit;
    });

    // dot helper: optional onClick (deep-link) + optional linkedHl (co-light a connector on hover).
    // lit = cross-hover focus (feed-card hover / DAG journey): drawn GROWN in its own color — the same
    // growth the native hover applies, no white ring (the user 2026-07-17) — and mouseleave restores it.
    const dot = (cx, cy, color, html, onClick, linkedHl, lit) => {
      const c = el('circle', { cx, cy, r: lit ? DOT_R + 2 : DOT_R, fill: color, stroke: '#e8eef5', 'stroke-width': 0.75 }); c.style.cursor = onClick ? 'pointer' : 'default';   // thinner white border on EVERY dot — romp + user (the user 2026-06-23)
      const dEnter = (e) => { c.setAttribute('r', DOT_R + 2); if (linkedHl) linkedHl.setAttribute('opacity', '0.95'); this.showTip(html(), e); };
      c.__tlHoverIn = dEnter;                      // re-armable after a redraw rebuilds this dot (_rehover)
      c.addEventListener('mouseenter', dEnter);
      c.addEventListener('mousemove', (e) => this.moveTip(e));
      c.addEventListener('mouseleave', () => { c.setAttribute('r', lit ? DOT_R + 2 : DOT_R); if (linkedHl) linkedHl.setAttribute('opacity', lit ? '0.95' : '0'); this.hideTip(); });
      if (onClick) c.addEventListener('click', onClick);
      svg.appendChild(c);
      return c;
    };

    // PASS 2: message arrival dots (on top of the lines), linked to their connector so the two
    // co-highlight and share the click. A dot whose sender lane is off-screen has no connector but
    // is still its own hoverable/clickable target.
    data.messages.forEach((mm, i) => {
      if (vidx[mm.toId] == null || !inWin(execAt(mm))) return;
      const col = colorOf(mm.fromId), cy = laneY(vidx[mm.toId]);
      const u = msgUI[i];
      const c = dot(x(execAt(mm)), cy, col, msgHtml(mm), msgNav(mm), u && u.hl, dagOrHoverMsg(mm.id));
      if (u) u.dot = c;
    });

    // PASS 3: the connector hit targets, appended LAST so a message's own line wins the hover along
    // its WHOLE path — including the vertical runs, which the arrival dots above would otherwise cover.
    // Nothing is lost by sitting over a message dot: the dot's tooltip, growth and click are the same
    // message's, and mEnter grows the linked dot too. Prompt dots are drawn after this pass, so they
    // keep their own hover.
    Object.keys(msgUI).forEach((k) => { const u = msgUI[k]; if (u && u.hit) svg.appendChild(u.hit); });

    // turn process-start (prompt) dots — at startAt; CLICKABLE → jump to the prompt that started
    // the period. Skipped where a PROCESSED message dot coincides (the message dot stands in).
    vis.forEach((s, i) => {
      const y = laneY(i);
      turnsOf(s.id).forEach((t) => {
        if (t.cont) return;                  // a post-sleep continuation piece of one segment: its prompt dot belongs to the FIRST piece, not here
        if (!inWin(startAt(t))) return;
        if (data.messages.some((mm) => mm.toId === s.id && !mm.pending && Math.abs(execAt(mm) - startAt(t)) <= 1)) return;
        const dx = x(startAt(t));
        // cross-hover focus (dot GROWN in place, via dot()'s lit param): DAG journey node, a coarse card
        // hover (whole-turn id), OR a prompt-atom hover (promptId) — never a work-only (workId) hover
        // A romp-AUTHORED prompt (t.romp — an auto-nudge, the Nudge button, or an auto-retry: anything romp
        // injected rather than the human typing) is marked as a ROMP MESSAGE: a BLACK-filled dot with the romp
        // favicon swirl inside (the user 2026-06-23, replacing the old white ⚡ bolt). Originally auto-nudges
        // ONLY; widened to every romp message (the user 2026-07-16, who reported an auto-retry rendering as a user prompt
        // instead of a romp logo thing), mirroring the chat's 2026-07-05 supersession of the same rule — at
        // the data level a retry and a nudge are both just romp-injected. The AUTO-nudge keeps its own caption
        // ('romp · nudge' + "romp nudged <name>"); any other romp message says 'romp' and shows its request,
        // so an auto-retry reads "romp / retry ×14" instead of masquerading as something the user asked for.
        const isRomp = t.romp || t.nudgeAuto;
        const tip = t.nudgeAuto
          ? () => '<div class="r"><img src="' + mediaUrl('romp-swirl-glyph.svg') + '" width="13" height="13" style="vertical-align:-2px;margin-right:5px;border-radius:2px"><span class="who" style="color:#fff">romp · nudge</span><span class="t">' + clock(startAt(t)) + '</span></div>' + this.body('romp nudged ' + esc(s.name))
          : isRomp
          ? () => '<div class="r"><img src="' + mediaUrl('romp-swirl-glyph.svg') + '" width="13" height="13" style="vertical-align:-2px;margin-right:5px;border-radius:2px"><span class="who" style="color:#fff">romp</span><span class="t">' + clock(startAt(t)) + '</span></div>' + this.body(this.req(t))
          : () => '<div class="r"><span class="chip" style="background:' + s.color + '"></span><span class="who" style="color:' + s.color + '">' + esc(s.name) + '</span><span class="t">' + clock(startAt(t)) + '</span></div>' + this.body(this.req(t));
        dot(dx, y, isRomp ? '#000' : s.color, tip, () => { this._select(s.id); this.openChat(t.tid || s.id, t.uuid, false, false, startAt(t), 'user'); }, null, dotLit(t, dagOrHover));   // romp message → a black dot (the swirl reads on it); prompt-intent → time fallback restricted to user turns
        if (isRomp) {                                    // the romp favicon swirl INSIDE the black dot; pointer-events:none → the dot keeps its hover/click
          const sz = DOT_R * 1.9;
          svg.appendChild(el('image', { x: dx - sz / 2, y: y - sz / 2, width: sz, height: sz, href: mediaUrl('romp-swirl-glyph.svg'), 'pointer-events': 'none' }));
        }
      });
    });

    // ── judging band: the summarizer judges on the same axis, under the lanes. Each mark is coloured
    // by the SESSION it acted on; adjacent same-session marks merge into a stretch of attention. A mark
    // within ~8s of the live edge is "running now" (white-outlined). (docs/judges.md; data.judging.)
    if (jShow) {
      const jb0 = M.top + vis.length * LANE_GAP + laneOffTotal + JB_TOPGAP;     // top of the first judge row (below the host-group gaps)
      const jY = (i) => jb0 + i * JROW + JROW * 0.5;
      const nameOf = (sid) => { const s = data.sessions.find((z) => z.id === sid); return s ? s.name : sid; };
      const sepY = jb0 - JB_TOPGAP * 0.5;
      svg.appendChild(el('line', { x1: M.left, y1: sepY, x2: x(t1), y2: sepY, stroke: '#ffffff14', 'stroke-width': 1, 'pointer-events': 'none' }));
      // vertical "judges" section label in the freed gutter space, just left of the right-justified judge names
      const jcx = Math.max(12, M.left - 72), jcy = (jY(0) + jY(shownJudges.length - 1)) / 2;
      const hd = el('text', { x: jcx, y: jcy, fill: 'var(--text-faint)', 'font-size': 9, 'font-weight': 700, 'letter-spacing': '.08em', 'text-anchor': 'middle', transform: 'rotate(-90 ' + jcx + ' ' + jcy + ')' }); hd.textContent = 'judges'; svg.appendChild(hd);
      shownJudges.forEach((J, ji) => {
        const y = jY(ji);
        // baseline rail through the row, faintly tinted in the judge's colour so each row is identifiable
        svg.appendChild(el('line', { x1: M.left, y1: y, x2: x(t1), y2: y, stroke: J.color, 'stroke-opacity': 0.28, 'stroke-width': 2, 'stroke-linecap': 'round', 'pointer-events': 'none' }));
        // judge name right-justified so it sits right beside the start of its rail
        const lbl = el('text', { x: M.left - 6, y: y + 3, 'text-anchor': 'end', fill: J.color, 'font-size': 10, 'font-weight': 600 }); lbl.textContent = J.key; svg.appendChild(lbl);
        // merge this judge's in-window marks into same-session blocks (a stretch of attention)
        // each mark is a RUN SPAN [t, t1] = [sent, recv] (g70): the real wall-clock the judge call ran, not
        // a point back-placed onto the work. Merge adjacent same-session spans into a stretch of attention.
        const evs = data.judging.filter((e) => e.judge === J.key && inWin(e.t)).sort((a, b) => a.t - b.t);
        const blocks = [];
        for (const e of evs) { const es = e.t, ee = (e.t1 != null ? e.t1 : e.t); const last = blocks[blocks.length - 1];
          if (last && last.sid === e.sid && es - last.end <= JMERGE_GAP) { last.end = Math.max(last.end, ee); last.open = last.open || !!e.open; last.members.push(e); }
          else blocks.push({ sid: e.sid, start: es, end: ee, open: !!e.open, members: [e] }); }
        // Stack time-OVERLAPPING blocks into sub-lanes within this judge's row, so concurrent judging of
        // DIFFERENT sessions on the same judge each stays visible and independently hoverable instead of
        // drawing on top of each other (the user 2026-06-23). Same-session concurrent calls already merged
        // above, so any overlap remaining here is cross-session. Greedy interval-partition over pixel extents
        // (blocks are start-sorted): each block takes the first sub-lane whose previous bar already ended.
        const laneEnds = [];                                  // right-edge px of the last bar placed in each sub-lane
        for (const b of blocks) {
          // an OPEN run (still in flight) has no recv yet — grow its bar to the live edge so it appears WHEN
          // it starts and advances with the axis, instead of popping in (back-dated) only once it ends.
          let bx1 = x(b.start), bx2 = x(b.open ? Math.max(b.end, nowS) : b.end);
          if (bx2 - bx1 < JMARK_MINW) { const c = (bx1 + bx2) / 2; bx1 = c - JMARK_MINW / 2; bx2 = c + JMARK_MINW / 2; }
          b._x1 = bx1; b._x2 = bx2;
          let lane = laneEnds.findIndex((endX) => bx1 >= endX);
          if (lane === -1) { lane = laneEnds.length; laneEnds.push(bx2); } else laneEnds[lane] = bx2;
          b._lane = lane;
        }
        const depth = Math.max(1, laneEnds.length);           // how many bars stack at the busiest instant
        const slotTop = y - JROW / 2, laneH = JROW / depth;   // split the fixed row into `depth` sub-lanes
        const barH = Math.max(2, Math.min(JBAR_H, laneH - 1));// shrink bars to fit; full 9px when depth === 1
        for (const b of blocks) {
          const x1 = b._x1, x2 = b._x2;
          const by = slotTop + laneH * (b._lane + 0.5);       // vertical centre of this block's sub-lane
          const col = colorOf(b.sid), active = b.open || ((nowS - b.end) >= 0 && (nowS - b.end) < 8);
          // fill = the SESSION being judged; outline = THIS judge's own colour
          // SOLID session colour, NO border (the user 2026-06-18): the judge's own colour already lives on
          // the row's horizontal rail, so a per-bar outline just repeated it. "Running now" reads as a fully
          // opaque bar; a settled one is slightly dimmed — that's the only cue, no stroke.
          const r = el('rect', { x: x1, y: by - barH / 2, width: x2 - x1, height: barH, rx: Math.min(2.5, barH / 2),
            fill: col, 'fill-opacity': active ? 1 : 0.82, 'data-judge': J.key });
          svg.appendChild(r);
          const html = () => {
            const span = b.open ? clock(b.start) + '– running…' : (b.start === b.end ? clock(b.start) : clock(b.start) + '–' + clock(b.end));
            // elapsed (total judge compute) + tokens for this stretch, summed from each mark's matched run
            const ms = b.members.reduce((a, m) => a + (m.ms || 0), 0);
            const tin = b.members.reduce((a, m) => a + (m['in'] || 0), 0), tout = b.members.reduce((a, m) => a + (m['out'] || 0), 0);
            const usage = (ms || tin || tout) ? '<div style="opacity:.7;margin-top:3px">⏱ ' + fmtDur(ms) + ' · ' + fmtTokens(tin + tout) + ' tok</div>' : '';
            // the LITERAL API call window: when the prompt went out → when the response came back (seconds
            // precision; judge calls are seconds-scale), the earliest send and latest recv across this block.
            const sents = b.members.map((m) => m.sent).filter((x) => x != null), recvs = b.members.map((m) => m.recv).filter((x) => x != null);
            const api = (sents.length && recvs.length) ? '<div style="opacity:.7;margin-top:2px">API ' + clockS(Math.min.apply(null, sents)) + ' → ' + clockS(Math.max.apply(null, recvs)) + '</div>' : '';
            const rows = b.members.slice(-5).map((m) => '<div class="b" style="opacity:.85"><span class="k">' + esc(JUDGE_KIND[m.kind] || m.kind) + '</span> ' + esc((m.text || '').slice(0, 90)) + '</div>').join('');
            return '<div class="r"><span class="who" style="color:' + J.color + '">' + esc(J.key) + '</span><span class="ar">▸</span><span style="color:' + col + '">' + esc(nameOf(b.sid)) + '</span><span class="t">' + span + (b.members.length > 1 ? ' · ' + b.members.length : '') + '</span></div>' + usage + api + rows;
          };
          const hit = el('rect', { x: x1 - 2, y: by - laneH / 2, width: (x2 - x1) + 4, height: laneH, fill: 'transparent' }); hit.style.cursor = 'default';
          hit.addEventListener('mouseenter', (e) => this.showTip(html(), e));
          hit.addEventListener('mousemove', (e) => this.moveTip(e));
          hit.addEventListener('mouseleave', () => this.hideTip());
          svg.appendChild(hit);
        }
      });
      // (auto-nudge ⚡ marks were removed from the judge band entirely — the user 2026-06-23. An auto-nudge
      // still surfaces as a romp-logo dot on its own lane; the band is now judge run-spans only.)
    }

    // far-right ⟩⟩ jump-to-now button — only when held back off the live edge (unpinned)
    if (!this._pinned) this._drawNowButton(svg);

    // 🔒 lock-to-now padlock at the now-edge (replaces the old toolbar checkbox, the user 2026-06-26)
    this._drawLockToggle(svg, lockCx, axisY);

    // The svg is fully rebuilt above — restore the hover the rebuild just swallowed, so a tip under a
    // stationary cursor comes up at once instead of waiting for the next mouse move (see _rehover).
    this._rehover();
  }

  // The lock-to-now padlock, drawn at the NOW-EDGE (bottom of the rightmost tick) — accent-blue when LOCKED,
  // gray (the feed mailbox gray) when unlocked; click toggles (the user 2026-06-26, replacing the toolbar
  // checkbox). Reuses the toolbar lock geometry (a 0..15/0..14 glyph) via a translate, so locked = shackle
  // seated, unlocked = shackle swung out. Click-safe: draw()'s _pointerHeld guard defers external redraws
  // while a pointer is pressed, so the click survives a rebuild (CLAUDE.md; same model as _drawNowButton).
  _drawLockToggle(svg, cx, axisY) {
    const on = this._lockNow;
    const color = on ? ROMP_BLUE : '#6e7681';          // accent when locked, gray when unlocked
    const x0 = cx - 7.5, y0 = axisY + 6;               // center the 15-wide glyph under the now-edge, in the bottom margin
    const g = el('g', { transform: 'translate(' + x0 + ' ' + y0 + ')' }); g.style.cursor = 'pointer';
    const hit = el('rect', { x: -2, y: -1, width: 19, height: 15, fill: 'transparent' });   // hit pad (whole glyph clickable)
    g.appendChild(hit);
    const st = { fill: 'none', stroke: color, 'stroke-width': 1.4, 'stroke-linecap': 'round', 'pointer-events': 'none' };
    g.appendChild(el('rect', Object.assign({ x: 3, y: 6.2, width: 8, height: 5.6, rx: 1.2 }, st)));
    g.appendChild(el('path', Object.assign({ d: on ? 'M4.8 6.2 V4.4 a2.2 2.2 0 0 1 4.4 0 V6.2'           // seated shackle (locked)
                                                  : 'M9.4 6.2 V5.3 A2.4 2.4 0 0 1 13.6 3.7' }, st)));   // swung-out shackle (unlocked)
    // Tooltip via the romp tip, NOT a native <title> (the user 2026-06-26: the native one never appeared —
    // the live edge rebuilds the SVG many times a second, resetting the browser's hover-delay timer). showTip
    // shows the styled tip INSTANTLY on mouseenter AND freezes redraws while hovering, which also keeps the
    // lock element stable so a press can't be dropped by a mid-gesture rebuild.
    const tipHtml = '<div class="b">' + (on
      ? 'Locked to the present — click to unlock (allow panning).'
      : 'Lock the timeline to the present — keeps the now-edge in view; a focus jump zooms out instead of panning away.') + '</div>';
    hit.addEventListener('mouseenter', (e) => this.showTip(tipHtml, e));
    hit.addEventListener('mousemove', (e) => this.moveTip(e));
    hit.addEventListener('mouseleave', () => this.hideTip());
    // Toggle on POINTERDOWN, not click: when the timeline pane isn't focused, the browser spends the first
    // CLICK focusing the iframe (so the lock used to need two clicks) — but the pointerdown still fires, so
    // acting on it makes lock/unlock a single, seamless press (the user 2026-06-26).
    g.addEventListener('pointerdown', (ev) => {
      if (ev.button != null && ev.button !== 0) return;   // primary button only
      ev.stopPropagation();
      const next = !this._lockNow;
      this._setLock(next);
      if (next) this._jumpToNow();   // snap to the live edge + resume live (also redraws)
      else this.draw();
    });
    svg.appendChild(g);
  }

  // The full romp WORDMARK loader (the user 2026-06-26): "R" + the reverse-spinning swirl-as-"o" + "m" + "p"
  // (the swirl's three arm colours) over three pulsing accent-blue dots — the SAME look as the boot splash +
  // every pane loader (CLAUDE.md "Loading/waiting states"). Shown in place of the SVG while the heavy bars
  // load, so the timeline never flashes partial data / empty gridlines. Built once, then just toggled.
  _showLoader(show) {
    if (show && !this._loaderEl) this._buildLoader();
    if (this._loaderEl) this._loaderEl.style.display = show ? 'flex' : 'none';
    if (this.svg) this.svg.style.display = show ? 'none' : '';
  }
  // Backstop so a warming-but-empty timeline can NEVER trap the loader (CLAUDE.md loader rule): if no
  // real content has landed within the window, force the load done so a genuinely-empty fleet shows its
  // "no romp activity" message. Armed once per cold warm-up; a real content payload clears _barsLoaded's
  // gate first and this becomes a no-op.
  _armLoaderBackstop() {
    if (this._loaderBackstop != null || this._barsLoaded) return;
    this._loaderBackstop = setTimeout(() => {
      this._loaderBackstop = null;
      if (!this._barsLoaded) { this._barsLoaded = true; this.draw(); }
    }, 12000);
  }
  _buildLoader() {
    if (!document.getElementById('tl-loader-css')) {
      const st = document.createElement('style'); st.id = 'tl-loader-css';
      st.textContent =
        "@font-face{font-family:'RompAnta';src:url(" + mediaUrl('Anta-Regular.ttf') + ") format('truetype');font-display:swap}"
        + ".tl-loader{min-height:240px;display:flex;align-items:center;justify-content:center}"
        + ".tl-loader .rl-in{display:flex;flex-direction:column;align-items:center;gap:18px}"
        + ".tl-loader .rl-word{font-family:'RompAnta',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        + "font-size:38px;line-height:1;white-space:nowrap}"
        + ".tl-loader .rl-o{width:.65em;height:.65em;vertical-align:middle;margin:0 -.0335em;animation:tl-rl-spin 7s linear infinite}"
        + ".tl-loader .rl-dots{display:flex;gap:7px}"
        + ".tl-loader .rl-dots i{width:7px;height:7px;border-radius:50%;background:#9cd2ff;animation:tl-rl-bnc 1.1s ease-in-out infinite}"
        + ".tl-loader .rl-dots i:nth-child(2){animation-delay:.16s}.tl-loader .rl-dots i:nth-child(3){animation-delay:.32s}"
        + "@keyframes tl-rl-bnc{0%,75%,100%{opacity:.25;transform:translateY(0)}38%{opacity:1;transform:translateY(-5px)}}"
        + "@keyframes tl-rl-spin{to{transform:rotate(-360deg)}}";
      document.head.appendChild(st);
    }
    const wrap = document.createElement('div'); wrap.className = 'tl-loader';
    const inner = document.createElement('div'); inner.className = 'rl-in';
    const word = document.createElement('div'); word.className = 'rl-word';
    const mk = (t, c) => { const s = document.createElement('span'); s.style.color = c; s.textContent = t; return s; };
    const o = document.createElement('img'); o.className = 'rl-o'; o.src = mediaUrl('romp-swirl-o.svg'); o.alt = 'o';
    o.onerror = () => o.remove();   // a missing asset must never leave a spinning broken-image icon
    word.appendChild(mk('R', '#1EA1EB')); word.appendChild(o);
    word.appendChild(mk('m', '#54B204')); word.appendChild(mk('p', '#4EA8A9'));
    const dots = document.createElement('div'); dots.className = 'rl-dots';
    for (let i = 0; i < 3; i++) dots.appendChild(document.createElement('i'));
    inner.appendChild(word); inner.appendChild(dots); wrap.appendChild(inner);
    this._loaderEl = wrap;
    this.wrap.insertBefore(wrap, this.svg);   // sits in the pane flow, above the (hidden) svg
  }

  // hover bodies: the prompt DOT shows the MESSAGE caption — a gist of what the user ASKED — once the
  // captioner produces it (ready early, the moment the message lands), and falls back to the raw prompt
  // only in the intermediate before that caption exists (the user 2026-06-19). The activity BAR is the
  // WORK (t.summary — what the agent DID); the two are now separate captions, dot vs line.
  req(t) { return t.msgCaption ? esc(t.msgCaption) : (t.prompt ? reqText(t.prompt) : ''); }
  // activity-bar hover = what the agent DID: the work period's own caption (t.summary), or a readable
  // Only when there's NO work caption yet do we fall
  // back to the request (the prompt) — "working on… <prompt>" in progress, else "request: <prompt>"
  // muted — so we never invent a result the summarizer hasn't produced.
  barBody(t, ongoing) {
    const work = t.summary ? esc(t.summary) : '';
    if (work) return '<div class="b">' + work + '</div>';
    const reqp = t.prompt ? reqText(t.prompt) : '';
    if (ongoing) return '<div class="b"><span style="opacity:.55;font-style:italic">working on: </span>' + (reqp || 'awaiting summary') + '</div>';
    return '<div class="b"><span style="opacity:.55;font-style:italic">request: </span>' + (reqp || '(no summary)') + '</div>';
  }
  body(s) { return s ? '<div class="b">' + s + '</div>' : ''; }
}

module.exports = { TimelinePanel, badgeFor, roundedPath, crossX, workAnchorOf, idleGaps, fmtSpan, dotLit, barLit, interpNow, shouldReanchorEdge, reanchorEdge, isFreshNowSample, barEndT, dragAxis, stripRompMarks, collapseRepeat, reqText, menuTop, offsetRect };
