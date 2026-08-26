// What an API failure MEANS, in the few words that decide what the user does next.
//
// A status code alone doesn't say whose problem it is, and that is the only question worth answering on a
// red card: wait it out, fix your network, or stop and come back later. The trigger (the user 2026-07-29):
// one long-running thread kept dying while a fresh session connected fine, and every surface romp offered
// said only "API error" — which is true of all of those cases and useful in none of them.
//
// Shared by the chat's retry line and the bell's entry so the two can never drift into different words for
// the same failure. Pure and string-only: the callers own their own chrome.

export interface ApiErrorFacts {
  status?: number | string | null;
  networkDown?: boolean | null;
  rateLimitType?: string | null;
  spendLimit?: boolean | null;
  tooLong?: boolean | null;
  modelLimit?: boolean | null;
  refusal?: boolean | null;
}

// The plain-words reason, or "" when the facts don't identify one (callers then fall back to the bare
// status, which is still better than inventing a cause we can't support).
export function apiErrorReason(f: ApiErrorFacts): string {
  // The ON-YOU cases first: they are the only ones the user can actually act on, and each outranks a
  // status code (a spend cap also arrives as a 4xx, which would otherwise read as a plain rate limit).
  // Wording is the established one these badges already used — folding them in here is about having ONE
  // place that decides what a failure means, not about renaming failures that already read clearly.
  if (f.spendLimit) return "spend limit reached";
  if (f.tooLong) return "prompt too long (needs compaction)";
  // A MODEL's own allowance, not the account's: "rate limited" would send the user off to wait when the
  // fix is one model switch away (the user 2026-08-01).
  if (f.modelLimit) return "this model is out of allowance — switch model or add credits";
  // A safeguards refusal is deterministic on the same input — no retry or wait fixes it, only the prompt
  // does (the user 2026-08-15). Same words as the chat card's remedy line, so the surfaces can't diverge.
  // It outranks the status ladder: a refusal riding a 4xx would otherwise read as a plain rejection.
  if (f.refusal) return "the model's safeguards refused this prompt — rewrite it or drop this thread";
  if (f.networkDown) return "this machine is offline";
  if (f.rateLimitType) return `rate limited (${f.rateLimitType})`;
  const s = Number(f.status);
  if (!Number.isFinite(s)) return "";
  // 529 is the one worth spelling out: it is genuinely server-side and transient, it looks identical to a
  // session-specific fault from the outside, and it is far likelier to hit a LONG thread — a big request
  // needs more capacity at once, so a fresh session sails through the same minute a full one keeps bouncing.
  if (s === 529) return "the API was overloaded — server-side and temporary, not this session";
  // Account-WIDE, but not necessarily "quota": an API key hits per-minute org rate limits (keys are
  // never unlimited — RPM/TPM by usage tier), a subscription hits its usage window. Both clear on
  // their own; naming "quota" alone sent a key-billed user hunting for a cap that wasn't the cause
  // (the user 2026-08-19). Parallel sessions sharing one key make the per-minute case the common one.
  if (s === 429) return "rate limited account-wide (per-minute limits or the plan's usage window) — not this session, retries clear it";
  if (s === 401 || s === 403) return "the API rejected our credentials";
  if (s === 404) return "the API says that model does not exist";
  if (s >= 500) return "server-side and temporary";
  if (s >= 400) return "the request itself was rejected";
  return "";
}
