// Which webview→kernel ops carry USER INTENT — a typed message or an explicit
// state-changing pick — versus view chatter (focus, hover, scroll, fold state).
// The KernelPipe holds intent ops while its socket is down and STILL DELIVERS
// them after a reconnect: the old reconnect path wiped the whole queue, which
// silently ate a card reply sent during a kernel-restart window (the user
// 2026-07-21, roof). View chatter stays droppable — the reconnect reloads the
// webview, and its fresh "ready" resyncs all view state from the kernel.
// The inbound half (HELD_ACROSS_RELOAD, below): which kernel→webview frames must
// outlive that same reload, because they answer a replayed op and come only once.
export const INTENT_OPS: ReadonlySet<string> = new Set([
  // typed text — losing these loses the user's words
  "sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend",
  // explicit clicks that mutate kernel/session state
  "interrupt", "apiRetry", "rewindDelete",
  "setModel", "setEffort", "setMode", "setFast", "setAuth",
  "renameSession", "moveSession", "endSession", "reviveSession",
  "nodeOverride", "askClear", "undoClear", "cardMove", "cardNotify", "redistill",
  "noticeAction",   // a notice card's button: a re-send of the user's own words, a click that mutates kernel state (T370)
  "answerAsk", "submitAsk", "toggleAsk", "cancelAsk",
  "setSessionFlag", "setSessionColor", "setGlobalRetryPaused", "setTimelineViews", "tagEdit", "openTagsDialog",
  // the REMOTE-tag edit (a tag homed on another kernel). Dropped with the view chatter it was simply
  // lost: the reconnect's reload wipes the pane's optimistic mirror too, and the resynced pane showed
  // the tag unchanged with nothing saying so (review find, 2026-09-08: the mirror never outlives the reload)
  "editTag",
  "reorderTabs", "closeTab",
]);

export function intentOp(type: unknown): boolean {
  return typeof type === "string" && INTENT_OPS.has(type);
}

// The INBOUND half: a kernel→webview frame that must survive the panel's own webview RELOAD. The
// reconnect path replays held intent and rebuilds the webview's html in the same tick, so the kernel's
// answer to a replayed op lands on a page that is gone: a frame posted between the reload and the
// fresh page's "ready" is dropped by the webview. State frames may go (the "ready" resyncs them), but a
// REFUSAL comes once, and one addressed by NAME (host + tag, not a per-page write id) is one the fresh
// page can still show. The case that found it (review find, 2026-09-08): a remote-tag ADD replayed
// right after a KERNEL restart is refused (the restarted kernel marks every attached host down until
// its tunnel supervisor dials it, and an add never queues), and the tagEditFailed frame hit the
// reloading timeline. Per-page write acks (tagEditAck, viewsAck) stay out on purpose: the fresh page
// tracks none of the old page's write ids and would drop them unread.
export const HELD_ACROSS_RELOAD: ReadonlySet<string> = new Set(["tagEditFailed"]);

export function heldAcrossReload(type: unknown): boolean {
  return typeof type === "string" && HELD_ACROSS_RELOAD.has(type);
}

// The hold itself, pure so the node runner can drive it. `offer` takes a frame the pipe received and
// says whether to deliver it now (false means it is parked); `release` hands back what was parked, in
// arrival order, for the webview's "ready" edge to deliver (extension.ts KernelPipe.webviewReady).
export class ReloadHold {
  private held: unknown[] = [];
  offer(m: any, webviewReady: boolean): boolean {
    if (webviewReady || !heldAcrossReload(m?.type)) return true;
    this.held.push(m);
    return false;
  }
  release(): unknown[] { return this.held.splice(0); }
}
