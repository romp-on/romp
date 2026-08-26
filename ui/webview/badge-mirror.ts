// Card trouble badges mirror into the shell's notification bell (the user 2026-07-27): anything that
// shows as a problem chip on a card — a judge warning, a failed follow-up, an API-error block, a
// retry storm — ALSO logs one entry in the bell, so problems are findable in one place after the
// fact. The chip on the card stays exactly as it was; the bell entry is the durable copy of the
// moment it appeared.
//
// DELIBERATELY NOT MIRRORED: the stalled hold. A stall is romp's nudge gate waiting out one of its
// own revivers (a judge call mid-flight, a reply still being judged), and almost every episode ends
// in seconds — the judge rules, or the auto-nudge fires and the session picks the work back up.
// That is the machinery working, not a problem, and mirroring it filled the log with "stalled" rows
// for holds nobody ever saw on a card (the user 2026-07-29). The chip on the card is the live
// surface; a stall that actually defeats the nudge escalates to nudgeFailed, which logs below.
//
// Pure: the caller passes the previously-notified signature set and gets back fresh notices + the
// now-active set. A signature keys the EPISODE (card + kind + the badge's own since/t), the same
// event-identity idea as the limit/judge signatures: per-push re-renders and page reloads don't
// re-log, a badge that clears leaves the active set (so a recurrence logs afresh), and a NEW episode
// of the same kind (different since/t) is a new entry.

import { apiErrorReason } from "./api-error-reason";

export interface BadgeItem {
  itemId: string; sid: string; name: string; text: string;
  nudgeFailed?: boolean;
  retrying?: { since?: number | null; count?: number; max?: number | null; status?: number | string | null; networkDown?: boolean | null; rateLimitType?: string | null } | null;
  warns?: { kind: string; t: number; msg: string }[] | null;
  blocked?: { state: string; status?: number; text?: string; tooLong?: boolean; spendLimit?: boolean; modelLimit?: boolean; refusal?: boolean } | null;
}
// sid + itemId ride along so a bell entry can JUMP back to the card it was minted from (the user
// 2026-07-28): the shell posts them back as {romp:'revealCard'} and the feed scrolls + pulses the card.
export interface BadgeNotice { kind: string; text: string; sig: string; sid: string; itemId: string; }

const cap = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export function badgeNotices(items: BadgeItem[], seen: Set<string>): { notices: BadgeNotice[]; active: Set<string> } {
  const notices: BadgeNotice[] = [];
  const active = new Set<string>();
  for (const it of items) {
    const add = (sig: string, kind: string, text: string) => {
      active.add(sig);
      if (!seen.has(sig)) notices.push({ kind, text, sig, sid: it.sid, itemId: it.itemId });
    };
    for (const w of it.warns ?? []) {
      add("w|" + it.itemId + "|" + w.t + "|" + w.kind, "warn", it.name + " — warning: " + cap(w.msg, 100));
    }
    if (it.nudgeFailed) {
      add("n|" + it.itemId, "nudge", it.name + " — follow-up failed on “" + cap(it.text, 50) + "”");
    }
    if (it.retrying) {
      // Name the failure behind the storm, not just that one exists — "API retry storm" was true of every
      // cause and actionable for none (the user 2026-07-29). The count says whether it is nearly out of road.
      const r = it.retrying;
      const why = apiErrorReason(r);
      const n = r.count ? ` (attempt ${r.count}${r.max ? " of " + r.max : ""})` : "";
      add("r|" + it.itemId + "|" + (r.since || 0), "retry",
        it.name + " — API retry storm" + n + (why ? ": " + why : ""));
    }
    // only the API-error block is an ERROR; a permission ask / picker is ordinary Needs-you traffic
    if (it.blocked && it.blocked.state === "apiError") {
      const b = it.blocked;
      // spendLimit / tooLong / modelLimit / refusal already read as plain words; apiErrorReason covers them too,
      // so the whole verdict comes from one place and the bell can't describe a failure differently than the chat
      // does. The signature's class slot keeps each on-you class a distinct EPISODE from a plain error on the
      // same card (a refusal often replaces a transient error mid-storm and must still mint its own entry).
      const onYou = b.spendLimit || b.tooLong || b.modelLimit || b.refusal;
      const what = apiErrorReason(b) || "API error" + (b.status ? " " + b.status : "");
      add("e|" + it.itemId + "|" + (b.status || "") + "|" + (b.spendLimit ? "sl" : b.tooLong ? "tl" : b.modelLimit ? "ml" : b.refusal ? "rf" : ""),
        "apierror", it.name + " — " + (b.status && !onYou ? "API error " + b.status + ": " : "") + what);
    }
  }
  return { notices, active };
}

// A /clear boundary that settled open cards (the kernel's clearNotices payload, read from the
// episodes log's own settle record). Same episode-identity contract as the badges above: one bell
// entry per boundary (sid + its t), so a clear that silently dropped cards is always findable in
// the bell after the fact (the user 2026-07-27). The entry names the dropped cards and the way back
// (Undo restores the batch).
export interface ClearNoticeRow { sid: string; name: string; t: number; titles: string[]; ended?: boolean; }   // ended: a session death finalized these cards (2026-08-13), not a /clear

export function clearBoundaryNotices(rows: ClearNoticeRow[], seen: Set<string>): { notices: BadgeNotice[]; active: Set<string> } {
  const notices: BadgeNotice[] = [];
  const active = new Set<string>();
  for (const r of rows) {
    const sig = "c|" + r.sid + "|" + r.t;
    active.add(sig);
    if (seen.has(sig)) continue;
    const n = r.titles.length;
    // an ENDED row is a session death that finalized open cards (2026-08-13) — same channel as the
    // /clear drop, its own phrasing: nothing here is restorable by Undo, the session is gone
    notices.push(r.ended
      ? { kind: "ended", sig, sid: r.sid, itemId: "",
          text: r.name + " ended with " + n + " open card" + (n === 1 ? "" : "s") + ": "
            + cap(r.titles.join(", "), 120) }
      : { kind: "cleared", sig, sid: r.sid, itemId: "",   // no single card — the jump opens the session
          text: r.name + " — /clear dropped " + n + " open card" + (n === 1 ? "" : "s") + ": "
            + cap(r.titles.join(", "), 120) + " (Undo on the feed restores them)" });
  }
  return { notices, active };
}

// SDK-backend problems (the kernel's sdkNotices payload — SdkBackend._log's problem ring). Same
// episode-identity contract: the kernel signs each OCCURRENCE (its start + the ring's sequence), so
// re-renders and reloads never re-log, while a repeat of the same failure is a NEW occurrence and logs
// again (the bell's own coalescing turns a flood into one counted row). Until 2026-07-28 these went to
// the kernel log alone, so a session whose thread died just looked odd, with nothing to look at.
export interface SdkNoticeRow { sig: string; t: number; text: string; }

export function sdkProblemNotices(rows: SdkNoticeRow[], seen: Set<string>): { notices: BadgeNotice[]; active: Set<string> } {
  const notices: BadgeNotice[] = [];
  const active = new Set<string>();
  for (const r of rows ?? []) {
    if (!r || !r.sig || !r.text) continue;   // a blank line is not an entry
    active.add(r.sig);
    if (seen.has(r.sig)) continue;
    notices.push({ kind: "sdk", text: cap(r.text, 240), sig: r.sig, sid: "", itemId: "" });
  }
  return { notices, active };
}

// Automatic fleet syncs (the kernel's syncNotices payload — the ring _auto_push_remote / _auto_pull_remote
// / _auto_ask_peer write their outcome to). The user asked for these on 2026-07-30: romp moves commits
// between machines on its own, and until now the only trace was a phase line in the network panel that
// disappeared the moment the sync finished — so a push that landed, and a push that failed while you were
// looking elsewhere, both ended up equally invisible. SUCCESSES log too, deliberately: the point is a
// record of what romp did to your machines, not just an alarm.
//
// The Log is a browser-side store the kernel cannot write to, which is why this rides the payload and
// mirrors here rather than being appended server-side. Same episode-identity contract as the SDK ring:
// the kernel signs each occurrence (its start + the ring's sequence).
export interface SyncNoticeRow { sig: string; t: number; text: string; ok?: boolean }

export function syncNotices(rows: SyncNoticeRow[], seen: Set<string>): { notices: BadgeNotice[]; active: Set<string> } {
  const notices: BadgeNotice[] = [];
  const active = new Set<string>();
  for (const r of rows ?? []) {
    if (!r || !r.sig || !r.text) continue;
    active.add(r.sig);
    if (seen.has(r.sig)) continue;
    notices.push({ kind: "sync", text: cap(r.text, 240), sig: r.sig, sid: "", itemId: "" });
  }
  return { notices, active };
}
