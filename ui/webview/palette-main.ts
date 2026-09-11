// Shell-page boot for the quick-pick hotkeys (browser dashboard only — the VS Code surface
// gets real contributed keybindings instead, rebindable in its own Keyboard Shortcuts
// editor). Obsidian's split, per the user (2026-08-08): Cmd/Ctrl+O is the SESSION JUMP
// switcher — sessions most-recently-used first, fuzzy by name, Enter switches; it never
// creates. Cmd/Ctrl+Shift+O opens the full new-session picker (create, directory, backend,
// host). Cmd/Ctrl+P toggles the command palette. All three combos are claimable by a page
// (Google Docs and Figma take Cmd+O/Cmd+P), the override lasts only while this tab has
// focus, and the window/tab-management set (Cmd+W/T/N/L/Q) stays untouched.
import { registerCommand, unregisterCommand, runCommand, commandList } from "./commands";
import { initPalette, PickItem } from "./palette";
import { initShortcutsModal } from "./shortcuts-modal";
import { chordMap, chordOf, dispatchable, displayChord, effectiveChord, keyHint, loadOverrides, saveOverride, titleWithKey, KEYS_EVENT } from "./keybindings";
import { hostPrefix } from "./host-prefix";   // pure display helper — safe here (never federation.ts, which boots a manager on import)
import { installMenuEcho } from "./tag-menu";   // model deps only (tag-lens/session-views) — no manager, no DOM cost
import { loadSettings, OPTIONAL_PANES, type PaneSet } from "./settings";   // the gear's store, read at every palette open (no side effects at import)
import { hotkeyCommandId, loadTabKeys, rememberTabKey, forgetTabKey, tabChord, unboundTabKeys, TABKEYS_KEY } from "./tab-keys";   // per-tab hot keys (2026-09-10)

type SessionRow = { id: string; name: string; dir: string; bg: string };

// The SHELL document must broadcast the menu echo too: it mounts no tag menu, but a click on its
// chrome (statusline, pane rail, the palette/log/remote backdrops) has to dismiss a menu open
// inside any pane. Module-level and before boot()'s in-iframe early return — the writer is
// per-document plumbing, not palette behavior. Idempotent (the module guard already ran it).
installMenuEcho();

(function boot() {
  // The shell page only, never inside a pane: the pane documents get the KEY wiring below
  // (a keydown fires in whichever document holds focus and never crosses the iframe boundary),
  // but the palette itself must sit in the top document to composite over all the panes.
  if (window.parent && window.parent !== window) return;
  const w = window as any;
  const mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  // the dispatcher's chord → command map, rebuilt lazily; declared up here because the per-tab hot-key registrations
  // below invalidate it at boot, before the dispatcher's own block runs (a `let` further down was a TDZ error that
  // aborted this whole module — the served split test caught it, 2026-09-10)
  let byChord: Map<string, string> | null = null;

  function pane(id: string): HTMLIFrameElement | null {
    return document.getElementById(id) as HTMLIFrameElement | null;
  }
  // The chat column the user last worked in (split screen, the user 2026-09-08): every chat-directed
  // command lands there — the shell's focus script tracks it (__rompFocusedChatId); the first column
  // when there is no split, exactly as before.
  function chatPane(): HTMLIFrameElement | null {
    try { const id = w.__rompFocusedChatId && w.__rompFocusedChatId(); if (id) { const f = pane(id); if (f) return f; } } catch (e) { /* no split script */ }
    return pane("f-chat");
  }
  function chatPost(msg: object): void {
    // Reveal the chat pane if it's toggled off, focus it, and hand it the message. The
    // __romp* globals and the pane's message handlers are read lazily at RUN time, so boot
    // order across the shell's script tags doesn't matter.
    try { if (w.__rompPaneToggle) w.__rompPaneToggle("chat", true); } catch (e) { /* rail not booted yet */ }
    const f = chatPane();
    try { f!.contentWindow!.focus(); f!.contentWindow!.postMessage(msg, "*"); }
    catch (e) { /* chat pane not loaded yet — nothing to talk to */ }
  }
  function openNewSessionPicker(): void {
    chatPost({ type: "openPicker", toggle: true });
  }

  // ── the session jump switcher (Cmd/Ctrl+O) ────────────────────────────────────────────────
  // Sessions come from the CHAT page's registry first (__rompSessionList: tab order, identity
  // colors — and the only place this page can see the federation-merged REMOTE sessions),
  // unioned with the kernel's /sessions (locals whose tab was closed). Recency comes from the
  // chat pane's __rompMru (most-recently-ACTIVATED tab ids, current session first). Obsidian's
  // trick, kept: the current session is excluded and the previous one sorts first, so Cmd+O
  // Enter toggles between your two most recent sessions.
  type SwitchRow = { id: string; name: string; bg: string; dir: string };
  function chatSessions(): SwitchRow[] {
    try {
      const ls = (chatPane()?.contentWindow as any)?.__rompSessionList;
      return ls ? ls().map((r: any) => ({ id: String(r.id), name: String(r.name), bg: String(r.bg || ""), dir: "" })) : [];
    } catch (e) { return []; }
  }
  function mruIds(): string[] {
    try { return (chatPane()?.contentWindow as any)?.__rompMru?.slice() || []; }
    catch (e) { return []; }
  }
  function sessionItems(locals: SessionRow[] | null): PickItem[] {
    const rows = chatSessions();
    const seen = new Set(rows.map((r) => r.id));
    for (const l of locals || []) {
      if (!seen.has(l.id)) rows.push({ id: l.id, name: l.name, bg: l.bg || "", dir: l.dir || "" });
      else { const r = rows.find((x) => x.id === l.id); if (r) r.dir = l.dir || ""; }   // dir tails for tab rows too
    }
    const mru = mruIds();
    const byId = new Map(rows.map((r) => [r.id, r]));
    const ordered: SwitchRow[] = [];
    for (const id of mru.slice(1)) { const r = byId.get(id); if (r) { ordered.push(r); byId.delete(id); } }
    for (const r of rows) if (byId.has(r.id) && r.id !== mru[0]) ordered.push(r);
    const base = (d: string) => (d || "").replace(/\/+$/, "").split("/").pop() || "";
    return ordered.map((r) => {
      const p = hostPrefix(r.name, r.id);   // remote → {host:"host:", rest}; local (bare uuid) → null
      return {
        title: p ? p.host + p.rest : r.name,
        hostLen: p ? p.host.length : 0,
        color: r.bg || undefined,
        dim: base(r.dir),
        run: () => chatPost({ type: "jumpSession", id: r.id }),
      };
    });
  }
  function openSessionSwitcher(): void {
    fetch("/sessions").then((r) => r.json()).catch(() => null).then((locals) => {
      const items = sessionItems(Array.isArray(locals) ? (locals as SessionRow[]) : null);
      // fail loudly, not with a silently empty list: nothing from the chat pane AND no answer
      // from the kernel means the kernel is the story, not "you have no sessions"
      palette.openPick({
        placeholder: "Jump to a session…",
        items: items.length || locals !== null ? items
          : [{ title: "Couldn't load sessions — the kernel didn't answer", run: () => {} }],
        altEnter: { label: "new session…", run: openNewSessionPicker },
      });
    });
  }

  // ── the dashboard's actions, registered as commands ──────────────────────────────────────
  // Each calls the SAME code path its rail button uses; the palette adds reachability, not
  // behavior.
  // Default chords come from commands.ts's DEFAULT_CHORDS — one table for the palette, the
  // dispatcher, and the hover hints — so none is declared at these call sites.
  registerCommand({ id: "session.jump", title: "Jump to a session", run: openSessionSwitcher });
  registerCommand({ id: "session.new", title: "New session", run: openNewSessionPicker });
  registerCommand({
    id: "session.fork", title: "Fork this session…",
    // the chat pane owns the modal (it knows the active session); from the palette the fork is
    // from-the-tip — the whole conversation (per-message forks live on the message's own hover row)
    run: () => { try { chatPane()!.contentWindow!.postMessage({ romp: "forkSession" }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({
    id: "session.notify", title: "Toggle notifications for this session",
    // the per-session bell the tab menu's row flips, from the keyboard (the user 2026-09-11): the chat pane owns the
    // flag (it knows the active session and the bell's state) and answers with a toast, since the flip itself is
    // otherwise visible only in that menu. Lands in the column last worked in, like every chat-directed command.
    run: () => { try { chatPane()!.contentWindow!.postMessage({ romp: "notifyToggle" }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({
    id: "settings.open", title: "Open settings",
    // the gear lives on its own served page, the shell's hidden #f-settings iframe (the user 2026-09-10;
    // it rode the feed pane before, which made that pane required), loaded on the first open: the shell's one
    // opener (kernel _LANDING_SETTINGS_JS __rompOpenSettings) gives it its src and holds the ask for its load
    run: () => { if (w.__rompOpenSettings) w.__rompOpenSettings(); },
  });
  // Chat history back/forward (the user 2026-08-14; their own Obsidian nav keys — Ctrl+M / Ctrl+,
  // per their vault's hotkeys.json). The chat pane owns the trail (it knows the tabs + scroll spots);
  // these run when focus is in the SHELL — with focus inside the chat, its own capture handler
  // (render.ts) reads the same bindings store, so a rebind moves both at once.
  registerCommand({
    id: "chat.navBack", title: "Navigate back in the chat",
    run: () => { try { chatPane()!.contentWindow!.postMessage({ romp: "chatNav", dir: -1 }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({
    id: "chat.navForward", title: "Navigate forward in the chat",
    run: () => { try { chatPane()!.contentWindow!.postMessage({ romp: "chatNav", dir: 1 }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({ id: "log.open", title: "Open the log", run: () => { if (w.__rompOpenErrs) w.__rompOpenErrs(); } });
  registerCommand({ id: "net.open", title: "Remote kernels", run: () => { if (w.__rompOpenNet) w.__rompOpenNet(); } });
  registerCommand({ id: "usage.open", title: "Token usage", run: () => { if (w.__rompUsagePanel) w.__rompUsagePanel(); } });
  registerCommand({ id: "kernel.restart", title: "Restart the romp kernel", run: () => { if (w.__rompRestart) w.__rompRestart(); } });
  // Pane toggles. The Outline pane's INTERNAL key stays 'fleet' (the pane controller's API);
  // the command speaks the user-facing name.
  // A pane hidden from this browser's dashboard in the gear (romp:settings.panes, the user 2026-09-10) is not
  // LISTED: the shell never loads its iframe and the toggle refuses its key (out of the controller's set), so a
  // dead entry would be noise. The check is a `when` predicate over the live setting (settings.ts's reader, the
  // same store the shell's reconcile reads), re-read at every open like the Files predicate below, never a
  // boot-time look at the iframe's src: a pane enabled later gains its command with the gear save, and a pane
  // hidden later loses it, no reload either way (review, 2026-09-10).
  const panes: Array<[string, string]> = [["chat", "chat"], ["timeline", "timeline"], ["fleet", "outline"], ["feed", "feed"], ["files", "files"]];
  const optional = new Set<string>(OPTIONAL_PANES);
  for (const [key, label] of panes) {
    registerCommand({
      id: "pane." + label, title: "Show or hide the " + label + " pane",
      run: () => { if (w.__rompPaneToggle) w.__rompPaneToggle(key); },
      // the Files control hidden by its gear setting (T317): the shell's body wears no-files-control, the toggle refuses,
      // so the entry is not listed either (re-read at every open: the gear's change reaches the body class live)
      when: key === "files" ? () => !document.body.classList.contains("no-files-control")
        : optional.has(key) ? () => loadSettings().panes[key as keyof PaneSet]
        : undefined,
    });
  }
  // Split screen (the user 2026-09-08): another chat column beside the last one, and closing the one the
  // user is in (the last one when the first column has the focus). The shell owns the columns
  // (_LANDING_SPLIT_JS); the rail's split action runs the same code.
  registerCommand({ id: "chat.split", title: "Split the chat", run: () => { if (w.__rompSplitChat) w.__rompSplitChat(); } });
  registerCommand({ id: "chat.closeSplit", title: "Close this chat split", run: () => { if (w.__rompCloseSplit) w.__rompCloseSplit(); } });
  // Cycle the focus between chat columns (the user 2026-09-10): unbound by default — the browser owns most
  // tab-cycling chords — and set in Keyboard shortcuts; with one column there is nothing to cycle.
  function cycleSplit(dir: 1 | -1): void {
    const ids: string[] = w.__rompChatFrameIds ? w.__rompChatFrameIds() : ["f-chat"];
    if (ids.length < 2) return;
    try { if (w.__rompPaneToggle) w.__rompPaneToggle("chat", true); } catch (e) { /* rail not booted yet */ }   // a hidden chat pane shows itself: the focus has to land somewhere visible
    const cur = (w.__rompFocusedChatId && w.__rompFocusedChatId()) || "f-chat";
    const i = Math.max(0, ids.indexOf(cur));
    const f = pane(ids[(i + dir + ids.length) % ids.length]);
    try { f!.contentWindow!.focus(); f!.contentWindow!.postMessage({ type: "focusComposer" }, "*"); } catch (e) { /* column not loaded */ }
  }
  registerCommand({ id: "chat.nextSplit", title: "Focus the next chat column", run: () => cycleSplit(1) });
  registerCommand({ id: "chat.prevSplit", title: "Focus the previous chat column", run: () => cycleSplit(-1) });
  // Per-tab hot keys (the user 2026-09-10): a session with a hot key is a command "Switch to <name>" whose chord
  // lives in the bindings store like any other, so the dispatcher below, the conflict check and the shortcuts
  // dialog cover it. The set of such sessions (romp:tabkeys) is read at boot, before any pane has loaded, and
  // grows when a pane's tab menu asks (hotkeyConfigure), which also opens the dialog on that one row, recording.
  function jumpToSession(sid: string): void {
    try { if (w.__rompPaneToggle) w.__rompPaneToggle("chat", true); } catch (e) { /* rail not booted yet */ }
    const f = (w.__rompChatTarget && w.__rompChatTarget(sid)) || pane("f-chat");   // the column showing it, else the one last worked in
    // the composer takes the focus too: the switch is for typing there next, and the focus moving INTO that column's
    // document is what carries the shell's focus ring across (a window focus() alone need not fire it)
    try { f!.contentWindow!.focus(); f!.contentWindow!.postMessage({ type: "jumpSession", id: sid }, "*"); f!.contentWindow!.postMessage({ type: "focusComposer" }, "*"); } catch (e) { /* chat not loaded */ }
  }
  const hotkeySids = new Set<string>();   // the sessions whose command this window has registered
  function registerTabHotkey(sid: string, name: string): void {
    registerCommand({ id: hotkeyCommandId(sid), title: "Switch to " + (name || sid.slice(0, 8)), run: () => jumpToSession(sid) });
    hotkeySids.add(sid);
    invalidate();
  }
  // The set is kept honest (review, 2026-09-10 — it only ever grew, so a cancelled first recording or a menu's
  // Remove left a dead "Switch to <name>" row in the dialog for good): a session stays in it exactly while it has
  // a chord bound and its tab exists. THIS window drops the unbound ones — it hears every bindings write, its
  // own (KEYS_EVENT) and a pane's Remove (a storage event); the chat pane drops the ones whose tab is gone and
  // re-titles renamed ones (render.ts, it knows the strip) — and every window's registry follows the set.
  function unregisterTabHotkey(sid: string): void {
    forgetTabKey(localStorage, sid);
    unregisterCommand(hotkeyCommandId(sid));
    hotkeySids.delete(sid);
    saveOverride(hotkeyCommandId(sid), null);   // the "" unbind (or any chord) leaves the store with the command
    invalidate();
  }
  function syncTabHotkeys(): void {   // the registry = the set: titles refreshed, departed sessions unregistered
    const set = loadTabKeys(localStorage);
    for (const [sid, name] of Object.entries(set)) registerTabHotkey(sid, name);
    for (const sid of Array.from(hotkeySids)) if (!(sid in set)) { unregisterCommand(hotkeyCommandId(sid)); hotkeySids.delete(sid); invalidate(); }
  }
  syncTabHotkeys();

  // Esc (or running an item) hands focus back to the chat pane, so "palette, Esc, type"
  // never strands the keyboard on the shell document. The palette's hotkey chips show each
  // command's EFFECTIVE binding (kbdFor), so a rebound command never advertises a stale default.
  const palette = initPalette({
    onClose: () => { try { chatPane()!.contentWindow!.focus(); } catch (e) { /* no chat pane */ } },
    kbdFor: (c) => { const ch = effectiveChord(c.id, c.chord, loadOverrides(), mac); return ch ? displayChord(ch, mac) : undefined; },
  });
  w.__rompPalette = palette;   // reachable by other shell scripts (e.g. a future mobile-bar button)

  // The palette toggle is itself a bindable command — hidden from the palette's own list (running
  // "toggle the palette" from the palette would just blink it). Cmd+Shift+P deliberately stays
  // unbound: it is the browser's / VS Code's own palette.
  registerCommand({ id: "palette.toggle", title: "Command palette", hidden: true, run: () => palette.toggle() });

  // The shortcuts dialog: every command above, rebindable — VS Code's grammar, the browser's home
  // (the user 2026-08-09). Reachable from the palette, the gear's customize link ({romp:'openKeys'}
  // from the feed iframe), and the shell Escape chain closes it first (topmost, z300).
  const keys = initShortcutsModal(mac);
  registerCommand({ id: "keys.open", title: "Keyboard shortcuts", run: () => keys.open() });
  w.__rompKeysOpen = () => keys.open();
  w.__rompKeysClose = () => keys.close();   // false when not open — the Escape chain moves on
  window.addEventListener("message", (e) => { if (e.data && e.data.romp === "openKeys") keys.open(); });
  // Sessions in the set with no chord bound leave it — never while the dialog is up (the one being recorded
  // has none yet), so the solo dialog's own close runs it too.
  function pruneUnboundHotkeys(): void {
    if (keys.isOpen()) return;
    for (const sid of unboundTabKeys(loadTabKeys(localStorage), loadOverrides(), mac)) unregisterTabHotkey(sid);
  }
  // A tab's menu asks for a hot key: the session joins the set, its command exists, the dialog opens on it
  // recording. When the dialog goes — a chord set, or Esc — the focus returns to the pane that asked (its
  // composer: the shell document is nowhere to type), and a session left with no chord leaves the set.
  function configureHotkey(sid: string, name: string, from: Window | null): void {
    rememberTabKey(localStorage, sid, name);
    registerTabHotkey(sid, name);
    keys.openFor(hotkeyCommandId(sid), () => {
      if (tabChord(sid, loadOverrides(), mac)) rememberTabKey(localStorage, sid, name); else unregisterTabHotkey(sid);
      pruneUnboundHotkeys();
      const back = from || chatPane()?.contentWindow || null;
      try { back!.focus(); back!.postMessage({ type: "focusComposer" }, "*"); } catch (e) { /* the asking pane is gone */ }
    });
  }
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || m.romp !== "hotkeyConfigure" || typeof m.sid !== "string" || !m.sid) return;
    configureHotkey(m.sid, typeof m.name === "string" ? m.name : "", (e.source as Window | null) || null);
  });
  w.__rompHotkeyConfigure = (sid: string, name: string) => configureHotkey(sid, name, null);   // the pane's menu gates on this being here
  // another window changed the set (added a session's hot key, or a pane dropped a departed one): follow it here
  // too, so the chord fires — or stops firing — in this window as well
  window.addEventListener("storage", (e) => { if (e.key === TABKEYS_KEY) syncTabHotkeys(); });
  window.addEventListener("storage", (e) => { if (e.key === KEYS_EVENT) pruneUnboundHotkeys(); });   // a pane's Remove is a "" write
  window.addEventListener(KEYS_EVENT, pruneUnboundHotkeys);
  pruneUnboundHotkeys();   // a set an earlier build let grow is trimmed at boot

  // ── hover discoverability (the user 2026-08-10) ───────────────────────────────────────────────
  // Every shell control that runs a command carries data-keycmd=<command id> (the rail buttons and
  // the mobile bar's, in the landing HTML); this sweep appends the command's CURRENT binding to its
  // tooltip and re-runs on every rebind, so shortcuts are discoverable by hovering the button that
  // does the same thing — and a rebound command never advertises a stale chord. The original title
  // is kept in data-kt0 so the sweep is idempotent. __rompKeyHint serves the landing page's inline
  // scripts (the pane toggles, whose titles are rewritten per toggle), and the KEYS_EVENT nudge
  // below hands them their first hints once this module has booted.
  function syncKeyTitles(): void {
    for (const el of Array.from(document.querySelectorAll("[data-keycmd]")) as HTMLElement[]) {
      const base = el.dataset.kt0 !== undefined ? el.dataset.kt0 : (el.dataset.kt0 = el.title || "");
      el.title = titleWithKey(base, el.dataset.keycmd || "");
    }
  }
  w.__rompKeyHint = keyHint;
  syncKeyTitles();
  window.addEventListener(KEYS_EVENT, syncKeyTitles);
  window.addEventListener("storage", syncKeyTitles);
  try { window.dispatchEvent(new Event(KEYS_EVENT)); } catch (e) { /* nudge inline listeners once __rompKeyHint exists */ }

  // ONE dispatcher for every bound chord, rebuilt lazily when the store changes (KEYS_EVENT from
  // this document's saves, `storage` from another tab's).
  function invalidate(): void { byChord = null; }   // a function declaration: the per-tab registrations above call it before this line runs
  window.addEventListener(KEYS_EVENT, invalidate);
  window.addEventListener("storage", invalidate);
  function isTyping(t: EventTarget | null): boolean {
    const el = t as HTMLElement | null;
    if (!el || !el.closest) return false;
    return !!el.closest("input, textarea, select, [contenteditable=true]");
  }
  function onKey(e: KeyboardEvent): void {
    if (keys.isOpen()) { keys.feed(e); return; }  // the dialog is recording/browsing — never dispatch under it; a recording takes the key from any pane
    if (!dispatchable(e, isTyping(e.target))) return;
    const ch = chordOf(e);
    if (!ch) return;
    if (!byChord) byChord = chordMap(commandList(), loadOverrides(), mac);
    const id = byChord.get(ch);
    if (!id) return;
    e.preventDefault(); e.stopPropagation();
    palette.close();                              // a command fired by key must not land under an open palette
    runCommand(id);
  }
  // The same dual wiring as the Alt+Arrow pane nav (_LANDING_FOCUS_JS): capture on the shell
  // document AND on every same-origin pane document, re-attached on every iframe (re)load.
  // render.ts's own window-capture Cmd+O handler stands down inside the shell (inRompShell),
  // so a keystroke in the chat document lands here exactly once. The hidden #f-settings iframe
  // (the /settings page, the gear's document since 2026-09-10) is wired with the panes: the gear
  // holds the keyboard while it is open, and the hotkey worked from inside it when it rode the feed.
  document.addEventListener("keydown", onKey, true);
  function wireKeys(f: HTMLIFrameElement | null): void {
    if (!f) return;
    const wire = () => {
      try { if (f.contentDocument) f.contentDocument.addEventListener("keydown", onKey, true); }
      catch (e) { /* cross-origin frame: not one of ours */ }
    };
    f.addEventListener("load", wire);
    wire();
  }
  ["f-chat", "f-fleet", "f-feed", "f-files", "f-timeline", "f-settings"].forEach((id) => wireKeys(pane(id)));
  // the split's chat columns too (the user 2026-09-08): the ones restored before this module booted (the
  // shell's split script runs ahead of it, so their romp-chat-cols events fired into no listener), and any
  // made later (the shell dispatches romp-chat-cols with the new frame) — so the chords work from every column
  ((w.__rompChatFrameIds ? w.__rompChatFrameIds() : []) as string[]).forEach((id) => { if (id !== "f-chat") wireKeys(pane(id)); });
  window.addEventListener("romp-chat-cols", (e) => wireKeys((((e as CustomEvent).detail || {}) as { frame?: HTMLIFrameElement }).frame || null));
})();
