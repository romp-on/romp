// The chat's client-side PENDING SEND (render.ts registerOptimistic / reconcileOptimistic): a composer
import { markerLabel } from "./time-marker";
// send shows its bubble the instant Enter is pressed and re-asserts it on every push until the kernel's
// payload accounts for the message. The DECISIONS live here, pure, so send-pending.test.ts executes
// them (the repo's extract-for-execution idiom); render.ts owns the DOM and the per-session maps.
//
// An entry has NO LIFETIME. It used to expire after 20 s ("a real send always echoes within this"), and
// the 2026-09-06 audit watched the case that assumption misses: the kernel's own echo was pruned early,
// nothing showed a message the CLI still held for 30 s, and the client's bubble gave up at 20 s — the
// send looked lost, then reappeared higher up. An entry now ends on EVENTS only:
//   - a LANDED user atom carrying the text, placed AFTER the send's anchor (below), that no earlier
//     pending send with the same text has already claimed,
//   - the kernel's NEVER-DELIVERED verdict on it, placed after the same anchor (its dropped-echo bubble
//     takes over, with the copy-to-composer and dismiss actions),
//   - the user's ✕ (render.ts's qx delegate drops the entry).
// A kernel PROVISIONAL (its echo atom, its queued bubble) only COVERS one of ours for the push it is
// visible on — the durable record is the kernel's (a persisted echo, the dropped marking, the fed-text
// guard in prune_live), but if it blinks, ours steps straight back in; a copy seen after the press also
// proves the kernel RECEIVED that one send (`received`, attributed per send like a landing). Nothing here
// reads a clock for a press-time entry: the frame resident at the press is older than the send by
// construction, so its anchor is recorded by identity; only a LATE stamp (stampBase) compares stamps to
// order events, and there an event that wears the send's id outranks its stamp.
//
// WHERE THE BUBBLE IS DRAWN is not decided here (T252d, the user 2026-09-08): render.ts appends every
// pending send as one bare group at the TAIL, below every event the kernel has shown, in send order — and
// the kernel places the landed atom at its LANDING time, the moment the CLI took the message, which is that
// same tail position, so nothing moves on landing and the order on screen is the order the model read.
// (T252/T252b drew the bubble at its SEND slot, above the steps that ran while the CLI held it, with an
// anchor, a placement floor and a foreign-queue reading to hold it there; the user's call was that the
// message belongs where the model read it, so that machinery is gone.) The bubble's hover names the send
// time once the atom has landed more than a minute later (sentAtLabel).
//
// THE IDENTITY: every entry carries the copy's id from the PRESS. mintQid mints it, in the kernel's own echo
// form ("echo:" + 32 hex digits); render.ts posts it with the send and registers the entry under it (newPending
// carries it, or mints one for a caller that posts nothing); the kernel parks the copy under it,
// queues it under it, keys the echo with it and pairs the landed atom to it (kernel.py _send_or_park,
// sdk_backend.py send). So a queued copy, an echo or a landing that wears an entry's id is that entry's, from
// the first push that shows it, and the ✕ names the id on both sides. Before, the id was minted where the copy
// entered the backend's queue and the entry LATCHED it from the first copy attributed to it by text and
// position: a send parked in the kernel's own queue (compaction, a usage-limit hold) had no id at all until
// the drain, and text cannot tell a press-time copy from a same-text copy another client queued later. Where
// the frame shows the id NOWHERE (the kernel has not received the send yet; a kernel that mints its own; the
// whose copies carry none), text and order decide for that push, exactly as before ids existed.
//
// THE ANCHOR (2026-09-06 review): every decision is read from the events AFTER the send, never from a
// count of tail events. At the first reconcile after the press the entry records the uuid of the last
// stable kernel event (`at.after`) and the uuids of the user events that ALREADY carried the text
// (`at.seen`: an older identical message, an old echo, an undismissed never-delivered bubble). A
// landing, a verdict or a provisional counts for this send only when it sits after the anchor and is
// not in `seen`. ONE RECORD CAN CARRY SEVERAL SENDS (2026-09-07 review): the CLI takes back-to-back
// sends at one boundary as one user record with a text block each, and the kernel ships those blocks
// (`blocks`); such a record is as many copies as it has matching blocks, and `seen` lists its uuid once
// per copy already spoken for. The old form read the last 30 events and assumed the landing was near the
// tail — and with the lifetime gone, a landing further up (a gap in pushes: a sleep, a reconnect, a turn
// with many thinking and tool events) never ended the bubble. When the anchor itself has left the resident
// window (the transcript grew past the wire tail), everything resident is after it, so the scan starts at
// the head.

export type SendBase = {
  after: string | null;   // uuid of the last stable kernel event at the press; null → nothing to anchor on, scan from the head
  seen: string[];         // uuids of the user events carrying the text that are background for this send: what
                          //   the press found, and what an earlier same-text entry claimed since — ONE ENTRY PER
                          //   COPY (a record of several sends lists its uuid once per spoken-for block)
  queued: number;         // copies of the text the kernel's queued bubble(s) already listed at the press
};


export type PendingSend = {
  text: string;        // the sent body, byte for byte — what the kernel echoes and the transcript lands
  body: string;        // `text` minus its image paths, whitespace-collapsed: an image send lands with the
                       //   paths rewritten to "[Image #N]" and stripped, so `text` itself can never match
  ts: number;          // press time (ms), never a lifetime: a ✕ that carries no id names its entry by it and the text
                       //   (dropPending); the identity is `qid`, and a ✕ that carries one is read by it first
  at?: SendBase;       // the send's place in the events (stamped by the first reconcile after the press)
  late?: boolean;      // the press found NO resident frame for the session (a placeholder tab, still
                       //   loading), so the stamp is taken at the first frame — which may already hold this
                       //   send's own echo or landing; stampBase then reads the events' own stamps
  imgPaths?: string[]; // dragged images → the bubble's thumbnails, and the image-aware landing match
  paths?: string[];    // EVERY attachment the send's trailing line carried (images and documents alike): the rescind's exact record (T373 fold)
  lost?: string;       // an event after the press that makes non-delivery LIKELY ("connection": the
                       //   socket dropped) — the bubble says "not confirmed" instead of "sending…"
  qid?: string;        // this send's IDENTITY, minted at the press (newPending) and posted with the send: the kernel's
                       //   queued copy, its echo atom (whose uuid IS the id) and the landed atom's qid/qids wear it, so
                       //   the landing, the cover and the hidden copy are decided by id wherever the frame shows it (an
                       //   event that carries an id is ours only if it carries THIS one); an event without an id (an
                       //   older kernel) and a push in which the frame carries this id nowhere decide by text
  cover?: string;      // the id of the kernel's queued copy that covered this send BY TEXT on the last push: the kernel
                       //   identifies that copy otherwise (an older kernel minted its own id for it), so a ✕ on that copy
                       //   is a ✕ on this send (dropPending). Re-read every push and cleared where the cover is by id
                       //   or absent; the send's own id stays the press's, nothing is latched
  received?: boolean;  // the kernel has shown a copy of the text attributed to THIS send (an echo atom or
                       //   a queued copy after the press that no earlier same-text send claimed): the send
                       //   reached it, so a connection drop before or after cannot have lost it — `lost` is
                       //   cleared and never set again
};

/** The slice of a chat event the decisions read (render.ts's ChatEvent is a superset). */
export type TailEvent = {
  kind: string;
  md?: string;
  uuid?: string;
  qid?: string;         // a landed user atom: the id of the queued copy it lands (T252c; the kernel pairs them)
  qids?: (string | null)[];   // a record of SEVERAL sends: one id (or null) per text block, in block order
  ts?: string;          // the kernel's stamp for the event, ISO-8601 UTC (kernel.py `iso(t)`, whole seconds)
  absorbed?: boolean;   // a mid-turn send the CLI spliced in: placed at its landing (T252d)
  sentAt?: number;      // …with the send time beside it (epoch s), for the bubble's hover
  undelivered?: boolean;
  images?: unknown[];
  texts?: { md?: string; hiddenByPending?: boolean; qid?: string; qts?: number; cancelable?: boolean; landing?: boolean }[];   // landing: a copy the caller holds after it left the kernel's queue (T262i)   // qid/qts: the copy's identity (T252c); hiddenByPending: render.ts hid this copy for a send drawn as our own bubble
  blocks?: string[];    // a user record the CLI wrote from SEVERAL sends taken at one boundary: one text per
                        //   block (kernel.py build_session ships them when there are two or more); `md` is
                        //   the blocks joined, so each block is a copy of its own send
};

export const OPT_PREFIX = "optimistic:";

/** The landed bubble's hover (T252d): "sent at HH:MM" when the atom's landing (`ts`, where it is placed) is more
 *  than a minute after the send (`sentAt`, both kernel stamps — never the client's clock); null otherwise. */
export function sentAtLabel(ts: string | undefined, sentAt: number | undefined): string | null {
  if (!ts || sentAt === undefined || sentAt === null) return null;
  const landed = Date.parse(ts);
  if (isNaN(landed) || Math.floor(landed / 1000) - sentAt <= 60) return null;
  return "sent at " + markerLabel(sentAt, null, sentAt * 1000).hm;   // `hm` is the bare HH:MM: no clock read (the module reads none)
}
export const isOptimisticUuid = (u?: string): boolean => !!u && u.startsWith(OPT_PREFIX);
export const isKernelEchoUuid = (u?: string): boolean => !!u && u.startsWith("echo:");
const collapse = (s: string): string => s.replace(/\s+/g, " ").trim();

/** `text` with its shipped image paths removed (quoted or bare, however the composer joined them). */
export function pendingBody(text: string, imgPaths?: string[]): string {
  let t = text;
  for (const p of imgPaths || []) t = t.split('"' + p + '"').join(" ").split(p).join(" ");
  return collapse(t);
}

/** The copy's id, minted at the press in the kernel's echo form ("echo:" + 32 hex digits, the shape of the key
 *  SdkBackend.send mints: isKernelEchoUuid, the kernel's landed-stamp echo skip and its re-queue prefix test all
 *  read it as the kernel's). Random, so two clients on one session never mint the same id; the kernel admits an
 *  id only in this form and only when the session does not already hold it (kernel.py _client_qid). */
export function mintQid(): string {
  const bytes = new Uint8Array(16);
  const c = (globalThis as any).crypto;
  if (c && typeof c.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  let hex = "";
  for (const b of bytes) hex += (b < 16 ? "0" : "") + b.toString(16);
  return "echo:" + hex;
}

/** A fresh entry, wearing the id the press minted: the caller's (render.ts mints it first and posts it with the send,
 *  so the kernel's copy and this entry wear one id), or a new one. */
export function newPending(text: string, imgPaths?: string[], now: number = Date.now(), qid: string = mintQid(), paths?: string[]): PendingSend {
  return { text, body: pendingBody(text, imgPaths), ts: now, imgPaths, qid, ...(paths && paths.length ? { paths } : {}) };
}

/** EXACT text match, trimmed: the composer trims what it sends and the kernel strips what it lands
 *  (`" ".join(blocks).strip()`; a follow-up's body after `_split_followup`), so the two agree byte for
 *  byte apart from edge whitespace. Never a substring test — "test" is not "test the continue button",
 *  and a landing of the longer message must not retire the shorter send (2026-09-06 review). */
const sameText = (md: string, text: string): boolean => md.trim() === text.trim();

/** How many COPIES of the send's text a user event carries: one when its md IS the text; otherwise one
 *  per text block that is. The CLI takes back-to-back sends at one boundary as ONE user record with a
 *  block per send (the shape the kernel's own echo prune, `_atom_user_texts`, was written for), and the
 *  event's md is the blocks joined — so with an md-only test none of those sends' bubbles ever ended
 *  (2026-09-07 review). The kernel ships `blocks` only when there are two or more. */
function textCopies(e: TailEvent, p: PendingSend): number {
  if (typeof e.md === "string" && sameText(e.md, p.text)) return 1;
  let n = 0;
  if (Array.isArray(e.blocks)) for (const b of e.blocks) if (typeof b === "string" && sameText(b, p.text)) n++;
  return n;
}

/** How many copies of this send a LANDED user atom carries: a user event whose uuid is not the kernel's
 *  echo prefix and whose md (or one of whose blocks) IS the text — or, for an image send, whose md is
 *  the body and which carries images (the CLI rewrote the paths to "[Image #N]"; the kernel strips the
 *  placeholders and renders the pictures, so the paths are gone from the text; a quoted path may leave
 *  its quotes behind, so those are set aside on both sides). */
export function landedCopies(e: TailEvent, p: PendingSend): number {
  if (e.kind !== "user" || typeof e.md !== "string" || isKernelEchoUuid(e.uuid)) return 0;
  if (p.qid) {   // identity decides where the record carries it (T252c): ours if a block wears THIS send's id; not ours
    if (e.qid === p.qid || (Array.isArray(e.qids) && e.qids.includes(p.qid))) return 1;   // when every block wears another's
    if (e.qid || (Array.isArray(e.qids) && e.qids.length > 0 && e.qids.every((q) => !!q))) return 0;
  }   // a block the kernel could not pair (null): text decides for it, below
  const n = textCopies(e, p);
  if (n) return n;
  if (p.imgPaths && p.imgPaths.length && Array.isArray(e.images) && e.images.length > 0) {
    const noq = (s: string) => collapse(s.replace(/"/g, ""));
    return noq(e.md) === noq(p.body) ? 1 : 0;
  }
  return 0;
}
export const landedIn = (e: TailEvent, p: PendingSend): boolean => landedCopies(e, p) > 0;

/** How many copies of the send's text a kernel queued bubble lists. */
function queuedCopies(e: TailEvent, p: PendingSend): number {
  if (e.kind !== "queued" || !Array.isArray(e.texts)) return 0;
  let n = 0;
  for (const x of e.texts) if (typeof x.md === "string" && sameText(x.md, p.text)) n++;
  return n;
}

/** The kernel's own PROVISIONAL copies of the send: its queued bubble's, or its unlanded echo atom (one
 *  text, the send's own — the kernel builds every echo from a single block). A never-delivered echo is a
 *  VERDICT, not a provisional (lostIn), and never suppresses a bubble. */
function provisionalCopies(e: TailEvent, p: PendingSend): number {
  if (e.kind === "queued") return queuedCopies(e, p);
  if (!(e.kind === "user" && isKernelEchoUuid(e.uuid) && !e.undelivered)) return 0;
  if (p.qid) return e.uuid === p.qid ? 1 : 0;         // the echo's uuid IS the copy's id (T252c)
  return textCopies(e, p);
}
export const provisionalIn = (e: TailEvent, p: PendingSend): boolean => provisionalCopies(e, p) > 0;

/** The kernel's verdict that the send was LOST: its echo, flagged never-delivered (the CLI died holding
 *  it, or the session moved past it). That bubble carries the text and the resend/dismiss actions. */
function lostCopies(e: TailEvent, p: PendingSend): number {
  if (!(e.kind === "user" && !!e.undelivered)) return 0;
  if (p.qid && isKernelEchoUuid(e.uuid)) return e.uuid === p.qid ? 1 : 0;   // the verdict is the echo, by id (T252c)
  return textCopies(e, p);
}
export const lostIn = (e: TailEvent, p: PendingSend): boolean => lostCopies(e, p) > 0;

/** The copies of the send a USER event carries in whichever role it plays — the roles are disjoint (a
 *  landed atom is never the kernel's echo; the verdict is the flagged echo), so at most one is non-zero. */
const copiesIn = (e: TailEvent, p: PendingSend): number =>
  e.kind !== "user" ? 0 : landedCopies(e, p) || lostCopies(e, p) || provisionalCopies(e, p);

/** How many copies of the event `u` are already spoken for from this send's point of view. */
const spokenFor = (at: SendBase, u: string): number => { let n = 0; for (const s of at.seen) if (s === u) n++; return n; };

/** The kernel's LIVE OVERLAY cards (kernel.py `_OVERLAY_KINDS`, pinned equal by tests/test_send_pending_overlay_kinds.py): the
 *  to-do box, the compacting, clearing, reconnecting and retrying notes, the queued group and the API-error card. They sit
 *  after every transcript event, and after the queued group, and wear word uuids ("apiError"), not a record's. */
export const OVERLAY_KINDS: ReadonlySet<string> = new Set(["todo", "compacting", "clearing", "reconnecting", "retrying", "queued", "apiError"]);

/** A kernel event a pending send can be anchored to: a TRANSCRIPT event with a uuid the kernel will keep. The client's own
 *  injections and the kernel's echo atoms are excluded — an echo is replaced by the landed atom (a new uuid) the moment
 *  its text lands, so it is not a stable place — and so are the kernel's overlay cards (T389, the user 2026-09-12): a second
 *  send pressed while the first was still queued anchored on the kernel's QUEUED GROUP itself (uuid "queued", an overlay
 *  kind, the last event of the tail then), or on the API-error card when one stood after it; the group's copy of the new
 *  send sat at or before that anchor, so it was never read as the send's cover, and the message drew twice (the kernel's
 *  copy in its group, ours at the tail) until it landed. */
const stableUuid = (e: TailEvent): boolean => !!e.uuid && !isOptimisticUuid(e.uuid) && !isKernelEchoUuid(e.uuid) && !OVERLAY_KINDS.has(e.kind);

/** The kernel's second for an event, off its ISO stamp (kernel.py `iso(t)`); null when it carries none. */
const eventSecond = (e: TailEvent): number | null => {
  if (!e.ts) return null;
  const ms = Date.parse(e.ts);
  return isNaN(ms) ? null : Math.floor(ms / 1000);
};

/** Does a user event NAME the send: wear its id? The kernel's echo atom, delivered or flagged never-delivered
 *  (its uuid IS the id); the landed atom (`qid`); a record of several sends one of whose blocks is this one
 *  (`qids`). Never for an entry with no id. */
const namesSend = (e: TailEvent, qid: string | undefined): boolean =>
  !!qid && e.kind === "user" && (e.uuid === qid || e.qid === qid || (Array.isArray(e.qids) && e.qids.includes(qid)));

/** Where this send sits among the kernel's events, read once at the first reconcile after the press.
 *
 *  At a PRESS-TIME stamp (the normal path) everything resident predates the send by construction — the
 *  frame arrived before the keystroke — so the whole frame is background and no stamp is read. A LATE
 *  stamp (`p.late`: the press found no frame — a placeholder tab, still loading) reads a frame that may
 *  already hold this send's own echo or landing, and it used to read them as background: our dashed
 *  bubble sat beside the kernel's echo, or a landed send's bubble never ended (2026-09-06 review, round
 *  3). So a late stamp reads the events' own stamps: an event the kernel stamped at or after the press's
 *  second is not before the send — neither the anchor nor background. Events with no stamp keep the
 *  press-time reading, with ONE exception, the queued bubble (below).
 *
 *  THE QUEUED PRESUMPTION (round 4): the kernel's queued bubble carries no stamp at all — its texts are
 *  the queue's bodies and positions — so a late stamp cannot read which of its copies predate the press.
 *  A late entry therefore PRESUMES the newest `own` copies of its text in the frame (one per identical
 *  send pressed against the same placeholder frame; the caller counts them) are this press's own, not
 *  background: the press just happened, and a copy of the same text that appeared with the first frame
 *  is the one the kernel added at its receipt. Without this, a send into a busy or held session whose
 *  first frame already listed it recorded that copy as background, so the entry never got a position
 *  until the CLI took the text: our bubble sat beside the kernel's copy for the whole wait, and its ✕
 *  would have cancelled the real queued send by body. The presumption's cost, stated: when the kernel had
 *  NOT yet received the send as the frame was built and an older identical message sits in the queue (a
 *  held queue keeps one for hours), that older copy is read as this send's — receipt is presumed, and a
 *  send lost on its way to the kernel hides behind the older copy until the queue moves. That case needs
 *  the same text queued twice around a page load; the double bubble needed only a send into a queue. A
 *  stamp on each queued text would make the reading exact and remove the presumption.
 *
 *  THE CLOCK ASSUMPTION, stated once: the press is the client's clock (ms); the events wear the kernel
 *  host's clock in whole seconds — the echo atom is stamped `int(time.time())` at the kernel's receipt
 *  of the send (sdk_backend.py `send`, before the CLI can see the text), the landed atom by the CLI's
 *  transcript record, at or after that (T237b), and both reach the client as `ts = iso(t)`. The bound
 *  holds when the two clocks agree to the second: exactly for a client on the kernel's machine (the
 *  VS Code webview, the served page there), and for a phone as well as its clock is set. A client
 *  running BEHIND the kernel reads an older identical message stamped inside the skew as this send's.
 *  THE ID OUTRANKS THE CLOCK: the first user event that NAMES the send (namesSend: the echo, whose uuid
 *  is the id; the landed atom, whose qid or qids carry it) is this send's whatever its stamp, and the
 *  kernel showed the send there, so it and every event after it are after the send: the anchor is looked
 *  for below it only, and none of them is background. Without this, a client running AHEAD of the kernel
 *  read this send's own echo as background (our dashed bubble beside the kernel's echo until the landing;
 *  a never-delivered verdict that never ended the bubble) and its own landing as its anchor (a bubble
 *  that never ended). Below the first event that names the send, and throughout a frame that names it
 *  nowhere, the stamp bound decides as before for records that carry no id (an older kernel
 *  route) or another send's id. The bound is confined to the late stamp because that is the only stamp
 *  that can meet the send's own records, and because at a press-time stamp it could only misfire (an
 *  identical message that landed within the press's second would read as this send's). */
export function stampBase(events: TailEvent[], p: PendingSend, own: number = p.late ? 1 : 0): SendBase {
  const pressS = p.late ? Math.floor(p.ts / 1000) : Infinity;
  const beforeSend = (e: TailEvent): boolean => { const s = eventSecond(e); return s === null || s < pressS; };
  // where the kernel first shows the send: a user event wearing its id (namesSend). The send is at least that
  // old, so nothing from there on can be its anchor, whatever the stamps say (the id outranks the clock, above)
  let firstNamed = events.length;
  for (let i = 0; i < events.length; i++) if (namesSend(events[i], p.qid)) { firstNamed = i; break; }
  let after: string | null = null;
  for (let i = firstNamed - 1; i >= 0; i--) if (stableUuid(events[i]) && beforeSend(events[i])) { after = events[i].uuid!; break; }
  const seen: string[] = [];
  let queued = 0;
  // background is read by TEXT, below the first event that names the send: at a press-time stamp nothing the
  // frame holds can wear this send's id (minted at that press), and at a late stamp the events from the one that
  // names it on are the send's own or after it; the text path, which reads any push where the id shows nowhere,
  // must find these copies spoken for; read by id, an older echo or an older verdict after the anchor was
  // background for no one, and the first push covered or retired the new send with it
  const pv: PendingSend = { ...p, qid: undefined };
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.kind === "queued") { queued += queuedCopies(e, pv); continue; }
    if (e.kind !== "user" || !e.uuid || isOptimisticUuid(e.uuid) || !beforeSend(e) || i >= firstNamed) continue;
    const n = copiesIn(e, pv);
    for (let k = 0; k < n; k++) seen.push(e.uuid);   // once per COPY: a record of several sends is several
  }
  // the queued presumption (above): a late stamp's newest `own` copies are this press's, so the count of
  // background copies stops short of them — at zero when the frame lists fewer than presumed (the kernel
  // had not received every press yet; the copies still to come cover those entries in order)
  return { after, seen, queued: Math.max(0, queued - (p.late ? own : 0)) };
}

/** The first index AFTER the send's anchor — or 0 when there is no anchor, or when the anchor has left the
 *  resident window (then everything resident is later than the send). */
export function scanFrom(events: TailEvent[], at: SendBase): number {
  if (at.after === null) return 0;
  for (let i = events.length - 1; i >= 0; i--) if (events[i].uuid === at.after) return i + 1;
  return 0;
}

export type Reconciled = {
  keep: PendingSend[];                        // still pending after this push
  echoHide: number[];                         // the kernel's ECHO atoms covering a kept send (T262h): the caller hides them and
                                              //   draws ours — one bubble per message, OURS, at the tail, the same node until the
                                              //   landing; an echo sits at its send time, above the steps that ran since, so
                                              //   swapping ours for it shrank the tail under a bottom reader
  inject: PendingSend[];                      // …and drawn by us, at the tail: not covered by the kernel's echo atom (a
                                              //   queued copy does not cover — ours stays drawn and the copy is hidden, T252)
  unqueue: PendingSend[];                     // …whose kernel cover is a QUEUED copy: the caller hides that copy
  landed: { p: PendingSend; idx: number }[];  // retired by a landing; idx = the landed event's index
  lost: PendingSend[];                        // retired by the kernel's never-delivered verdict
};

/** One push's decision for a session's pending sends, read off the KERNEL's events (the caller has
 *  already stripped its own injections). Entries are read in registration order, and ONE kernel record
 *  accounts for ONE entry — the same rule for a landing and for the kernel's provisional copy:
 *   - the k-th landing after the anchor retires the k-th pending send with that text, and a landing an
 *     earlier entry took is background (`seen`) for every later entry with the same text — two
 *     identical sends in flight used to both retire on the first landing (2026-09-06 review). A record
 *     of SEVERAL sends (`blocks`) is as many landings as it has matching blocks, so the claims on one
 *     record are COUNTED (this push in `claimed`, for good in `seen`), never booleaned — two sends the
 *     CLI took as one record used to leave both bubbles pending for ever (2026-09-07 review);
 *   - the k-th kernel copy of the text after the anchor — an echo atom no earlier entry claimed, or a
 *     queued copy beyond the entry's press-time count — covers the k-th pending send with that text: it
 *     hides that send's bubble for this push and proves the kernel received THAT send (`received`). A
 *     claimed echo joins later same-text entries' `seen` for good; a queued copy without an id is handed
 *     out by position within a push (the copies an entry's press listed are its background). One echo
 *     used to mark every same-text entry received and clear every "not confirmed" (2026-09-06 review,
 *     round 3): with two identical sends in flight and one of them lost, the lost one read "sending…"
 *     for good, and nothing could ever mark it.
 *  Identity first (T252c): every send carries the id it was pressed with (newPending), and the kernel's copies
 *  of it wear that id (the queued copy's `qid`, the echo's uuid, the landed atom's `qid`/`qids`), so landing,
 *  cover and loss are decided by the id wherever the frame shows it. Text and order remain the reading for
 *  copies the kernel gave no id (an older kernel, a notice it queued itself) and for a push
 *  in which the frame carries the send's id nowhere (the kernel has not received it yet, or minted its own);
 *  the COUNT of confirmed sends is then what the kernel's records support. Nothing latches: an id a kernel
 *  copy wears that is not this send's is another send's, whatever its text says.
 *  When the kernel hides a fed send's echo behind a same-text queued copy (its chat dedups by text), a
 *  received send can read "not confirmed" after a drop until a copy of its own shows; that clears on the
 *  next kernel copy, and the error is toward "not confirmed", never toward a false "sending…". */
/** Where the frame SHOWS a send's identity, from its anchor on: the landed atom that carries it, a queued copy or an
 *  echo wearing it (the echo's uuid IS the id), or nowhere. Identity decides only where the frame shows it (second
 *  review): a send whose id is nowhere in the frame (the kernel has not received it yet, an older kernel minted
 *  its own, or a restart re-minted the mirrors an older kernel kept text-only) is read by TEXT for that push,
 *  exactly as before ids existed, rather than left with a bubble nothing can ever retire. */
function locateId(events: TailEvent[], from: number, qid: string): { where: "landed"; idx: number } | { where: "provisional" | "none" } {
  let provisional = false;
  for (let i = from; i < events.length; i++) {
    const e = events[i];
    if (e.kind === "queued") { if ((e.texts || []).some((t) => t.qid === qid)) provisional = true; continue; }
    if (e.kind !== "user") continue;
    if (e.uuid === qid) { provisional = true; continue; }   // the echo, delivered or flagged
    if (!isKernelEchoUuid(e.uuid) && (e.qid === qid || (Array.isArray(e.qids) && e.qids.includes(qid)))) return { where: "landed", idx: i };
  }
  return { where: provisional ? "provisional" : "none" };
}

export function reconcilePending(events: TailEvent[], list: PendingSend[]): Reconciled {
  // First reconcile after the send: whatever the events ALREADY hold for this text is background — an
  // older identical message, an old echo, an undismissed never-delivered bubble — not this send. Only
  // what appears after the anchor, beyond that set, is this send's (the user 2026-08-09, who watched
  // resends vanish in the call that created them; the 2026-09-06 review, which watched a resend of a
  // never-delivered message retired as lost by the old verdict). Late entries stamped against this frame
  // each presume ONE of its queued copies of their text is their own (stampBase's queued presumption), so
  // identical sends pressed against the same placeholder frame own as many copies as there were presses.
  const lateOwn = new Map<string, number>();
  for (const p of list) if (!p.at && p.late) lateOwn.set(p.text, (lateOwn.get(p.text) || 0) + 1);
  for (const p of list) if (!p.at) p.at = stampBase(events, p, p.late ? lateOwn.get(p.text) || 1 : 0);
  const r: Reconciled = { keep: [], inject: [], unqueue: [], landed: [], lost: [], echoHide: [] };
  const claimed = new Map<string, number>();           // "index\0text" → copies of that text in that landing taken by earlier entries THIS push
  const owned = new Set<string>();                     // the identities the pending sends were pressed with: an echo, a queued copy or a landing wearing one is that send's, never a text match
  for (const p of list) if (p.qid) owned.add(p.qid);
  // a landing read by text (this send's id is nowhere in the frame): a record wearing another pending send's id is that
  // send's, and in a record of several sends the same-text blocks wearing another send's id are not this one's (third review)
  const landedForText = (e: TailEvent, pv: PendingSend, self?: string): number => {
    if (e.qid && owned.has(e.qid) && e.qid !== self) return 0;
    let n = landedCopies(e, pv);
    if (n && Array.isArray(e.qids) && Array.isArray(e.blocks))
      for (let k = 0; k < e.qids.length; k++) {
        const q = e.qids[k], b = e.blocks[k];
        if (q && owned.has(q) && q !== self && typeof b === "string" && sameText(b, pv.text)) n--;
      }
    return Math.max(0, n);
  };
  const takenCopies = new Map<string, Set<number>>();  // text → queued-copy positions taken by an earlier entry THIS push
  for (const p of list) {
    const at = p.at!;
    const from = scanFrom(events, at);
    let landedIdx = -1, lostIdx = -1, echoIdx = -1, idCopy = -1;
    const copyIds: (string | undefined)[] = [];   // the group's same-text copies in order, each with its id when the kernel gave one
    // identity decides where the frame shows it (T252c): the atom carrying our id is our landing, whatever the
    // text arithmetic says (an identical send retired on the same record this push claimed the text once);
    // while a queued copy or an echo wears it, no landing is ours; and an id the frame carries NOWHERE leaves
    // this push to text, the reading every frame had before ids — `pv` is this send as text reads it
    const loc = p.qid ? locateId(events, from, p.qid) : { where: "none" as const };
    const pv: PendingSend = loc.where === "none" && p.qid ? { ...p, qid: undefined } : p;
    if (loc.where === "landed") landedIdx = loc.idx;
    else for (let i = from; i < events.length; i++) {
      const e = events[i];
      if (e.kind === "queued") {
        for (const t of e.texts || []) if (typeof t.md === "string" && sameText(t.md, p.text)) copyIds.push(t.qid);
        // the kernel's copy of THIS send, by id (T252c): the cover, wherever the copy sits in the group
        if (p.qid && (e.texts || []).some((t) => t.qid === p.qid)) idCopy = i;
        continue;
      }
      // an echo another pending send owns by id is not this one's, whatever its text says
      if (!pv.qid && e.kind === "user" && isKernelEchoUuid(e.uuid) && owned.has(e.uuid!)) continue;
      // the copies of this event that are NOT this send's: background at the stamp, or claimed by an
      // entry retired since (`seen`); a landing an earlier entry took this push is counted in `claimed`
      const spoken = e.uuid ? spokenFor(at, e.uuid) : 0;
      // a send that landed needs no cover and no verdict: scanning on would claim a later echo (another send's
      // cover) as its own, and mark it spoken for that send (second review)
      if (loc.where === "none" && landedIdx < 0 && landedForText(e, pv, p.qid) > spoken + (claimed.get(i + "\0" + p.text) || 0)) { landedIdx = i; break; }
      if (lostIdx < 0 && lostCopies(e, pv) > spoken) { lostIdx = i; continue; }
      if (echoIdx < 0 && provisionalCopies(e, pv) > spoken) echoIdx = i;   // the first echo no earlier entry claimed (`seen`)
    }
    // ONE kernel copy covers ONE send: an unclaimed echo atom first — its uuid is then background for
    // every later same-text entry, this push and every push after (the claim must outlive the claimant:
    // a ✕ on it must not hand its echo to the next entry; an echo is one text, so one entry in `seen` is
    // the whole of it) — else the first queued copy beyond this entry's press-time count that no
    // earlier entry took this push. A cover read by text is this push's only: the entry keeps the id it
    // was pressed with, and the copy wearing THAT id is the exact cover from the push that shows it.
    let covered = false, byQueued = false, byEcho = false;
    if (echoIdx >= 0) {
      covered = true; byEcho = true;
      const u = events[echoIdx].uuid;
      if (u) for (const q of list) if (q !== p && q.at && q.text === p.text && !q.at.seen.includes(u)) q.at.seen.push(u);
    }
    // The kernel's QUEUED copy of this send, beside the echo or alone. By id first: our copy wherever it sits (an
    // identified copy the caller HOLDS after it left the queue is ours by the same id — the fed gap, where the echo
    // shows too and the held copy must hide with it, or the message draws twice). Else by text, for a copy the kernel
    // gave no id: before any echo, the legacy reading (positions past the press-time count, untaken, not another
    // send's by id); after an echo cover, ONLY an id-less copy — a copy wearing ANOTHER id is another send's, and
    // taking it by text handed the second of two identical presses' copy to the first (the review of the fed-gap
    // fix: three bubbles for two messages, the wrong entry retired on the landing).
    const taken = takenCopies.get(p.text) || new Set<number>();
    const idHit = p.qid ? copyIds.indexOf(p.qid) : -1;
    p.cover = undefined;                                  // this push's cover only: the last frame's is stale
    if (idHit >= 0 && !taken.has(idHit)) {
      taken.add(idHit); covered = true; byQueued = true;   // exact, whatever its position; spoken for on the text path this push
    } else if (byEcho || (!pv.qid && copyIds.length > at.queued)) {
      let k = -1;
      for (let j = at.queued; j < copyIds.length; j++) {
        const id = copyIds[j];
        if (taken.has(j) || (id && owned.has(id) && id !== p.qid)) continue;   // taken this push, or another send's by id
        // after an echo: an id-less copy, or the copy the echo itself stands for (the same id: a fed copy the caller
        // holds after it left the queue, with the echo showing beside it); a copy wearing any OTHER id is another
        // send's, and this send's id is nowhere in the frame (a kernel that minted its own, or one that has not
        // received the send yet), so the echo alone covers it
        if (byEcho && id && id !== events[echoIdx].uuid) continue;
        k = j; break;
      }
      if (k >= 0) {
        taken.add(k); covered = true; byQueued = true;
        if (copyIds[k] && copyIds[k] !== p.qid) p.cover = copyIds[k];   // a copy the kernel identifies otherwise stood in for this send
      }
    }
    takenCopies.set(p.text, taken);
    if (covered) p.received = true;             // the kernel holds this send: proven once, latched
    if (p.received) p.lost = undefined;         // the drop is older news than the kernel's own copy
    if (landedIdx >= 0) {
      const ck = landedIdx + "\0" + p.text;             // per text: a record of two DIFFERENT sends is one landing for each
      claimed.set(ck, (claimed.get(ck) || 0) + 1);
      r.landed.push({ p, idx: landedIdx });
      continue;
    }
    if (lostIdx >= 0) { r.lost.push(p); continue; }
    r.keep.push(p);
    // The kernel's copy of a pending send never replaces OUR bubble (T252, T262h): a QUEUED copy sits in the
    // kernel's group at the tail and the caller hides it; an ECHO atom sits at its SEND time, above the steps
    // that ran since, and the caller hides it too — swapping ours (at the tail) for it shrank the tail under a
    // bottom reader by the bubble's height, the clamp the user watched. Ours stays drawn, one node, until the
    // landing takes its place; the kernel's copies only prove receipt (`received`).
    r.inject.push(p);
    if (covered && byQueued) r.unqueue.push(p);
    if (covered && byEcho && echoIdx >= 0 && !r.echoHide.includes(echoIdx)) r.echoHide.push(echoIdx);
  }
  // Every landing claimed this push is spoken for: one copy per claim becomes background for every
  // pending send with the same text that STAYS, on every push after (the retired entry's claim would
  // otherwise leave with it, and the next identical send would retire on a record that was never its
  // own). Once per claim — the claimant leaves the list with this push — so `seen` counts exactly.
  for (const { p, idx } of r.landed) {
    const u = events[idx].uuid;
    if (u) for (const q of r.keep) if (q.text === p.text) q.at!.seen.push(u);
  }
  return r;
}

/** The entry a ✕ on a pending bubble removes. The id decides first, whenever the ✕ carries one (`qid`, ridden
 *  as data-qid on OUR bubble and on the KERNEL's own queued/parked copy alike): the entry that owns the id
 *  (T252c), else the entry that copy covered by text on the last push (`cover`: a kernel that minted its own
 *  id for our send); an id that is neither names another client's send, or the kernel's own, and removes
 *  nothing of ours, even when a same-text entry shares the ✕'s `ts`. The press time is not the identity: two
 *  same-text sends registered in one synchronous loop share it when registerOptimistic's wall-clock reads, one
 *  per send, fall in one millisecond (flushStaged posts each staged slash command and goal-cited item as its
 *  own send; adoptProvisional posts each text a new session's tab held the same way), and reading `ts` and
 *  text first made the ✕ on the second drop the FIRST, while the cancel it posted named the second's id and
 *  the kernel removed exactly that copy: the client and the kernel then disagreed about which copy remained,
 *  the survivor's bubble hid the other copy, and the next ✕ was answered with the miss. Only a ✕ that names
 *  no id falls to the older readings: an id-less bubble (older data; newPending always mints one) names its
 *  entry by `ts` and text, and an id-less kernel copy drops the first pending send with that text, the one
 *  the kernel's first copy covers. Same-text entries carry different states (lost, received), and the
 *  first-with-the-text lookup the ✕ once used for every bubble dropped the wrong one from a "not confirmed ·
 *  sending…" pair: the next push brought the dismissed bubble back and the other was gone without a gesture
 *  (2026-09-06 review). Returns the removed entry; undefined when none matched: a bubble whose entry a push
 *  already retired removes nothing, never a neighbour with the same text. */
export function dropPending(list: PendingSend[], text: string, ts?: number, qid?: string): PendingSend | undefined {
  let i = -1;
  if (qid) {
    i = list.findIndex((p) => p.qid === qid);
    if (i < 0) i = list.findIndex((p) => p.cover === qid);
  } else if (ts !== undefined) i = list.findIndex((p) => p.ts === ts && p.text === text);
  else i = list.findIndex((p) => p.text === text);
  return i >= 0 ? list.splice(i, 1)[0] : undefined;
}

/** The words a refused send puts back in the composer (2026-09-19): the dropped entry's text when the box is empty (the
 *  press cleared it and nothing was typed since), else null: a draft in progress is never overwritten, and a refusal whose
 *  entry a push already retired restores nothing. */
export function refusedRestoreText(dropped: PendingSend | undefined, box: string): string | null {
  return dropped && box.trim() === "" ? dropped.text : null;
}


/** Which copy of `text` in a kernel queued group the caller hides for a send drawn at its own slot: the NEWEST
 *  copy not already hidden (the group lists the queue in order; ours is the latest press with that text), or -1
 *  when there is none — including when the only copies are ones the kernel marked cancelable:false (no recall
 *  exists there: the session's own queue). That copy stays the one bubble shown, with its honest tooltip, and ours is
 *  suppressed as before: hidden behind our bubble's ✕ it offered a cancel the kernel would refuse (review of the
 *  first cut). */
export function queuedCopyToHide(texts: { md?: string; cancelable?: boolean; hiddenByPending?: boolean; optimistic?: boolean; qid?: string }[], text: string, qid?: string): number {
  if (qid) {   // OUR copy by identity (T252c): never the newest same-text one
    const k = texts.findIndex((t) => t.qid === qid && !t.hiddenByPending && !t.optimistic);
    if (k >= 0) return texts[k].cancelable === false ? -1 : k;
  }
  for (let k = texts.length - 1; k >= 0; k--) {
    const t = texts[k];
    if (t.hiddenByPending || t.optimistic || typeof t.md !== "string" || !sameText(t.md, text)) continue;
    if (t.cancelable === false) return -1;
    return k;
  }
  return -1;
}

/** The bare group's one-line header, from its bubbles' OWN states: the lost ones (the connection dropped
 *  after the press and nothing has confirmed them) read "not confirmed", the rest "sending…". A group
 *  used to read "N not confirmed" when ANY bubble was lost, and kept that label for the survivors after
 *  a ✕ (2026-09-06 review). */
export type BareLabelPart = { text: string; lost: boolean };
export function bareGroupLabel(nLost: number, nSending: number): { parts: BareLabelPart[]; title: string } {
  const parts: BareLabelPart[] = [];
  if (nLost > 0) parts.push({ text: nLost === 1 ? "not confirmed" : `${nLost} not confirmed`, lost: true });
  if (nSending > 0) parts.push({ text: nSending === 1 ? "sending…" : `sending ${nSending}…`, lost: false });
  const lostTitle = "The connection dropped after " + (nLost === 1 ? "this was" : "these were")
    + " sent, and romp has not confirmed the session has " + (nLost === 1 ? "it" : "them") + ". ✕ moves "
    + (nLost === 1 ? "it" : "one") + " back to the composer to send again.";
  const sendingTitle = "on its way to the session — cancellable until the session takes it";
  const title = nLost > 0 && nSending > 0 ? lostTitle + " The rest: " + sendingTitle + "."
    : nLost > 0 ? lostTitle : sendingTitle;
  return { parts, title };
}
