// Shell-page boot for the quick-pick hotkeys (browser dashboard only — the VS Code surface
// gets real contributed keybindings instead, rebindable in its own Keyboard Shortcuts
// editor). Obsidian's split, per the user (2026-08-08): Cmd/Ctrl+O is the SESSION JUMP
// switcher — sessions most-recently-used first, fuzzy by name, Enter switches; it never
// creates. Cmd/Ctrl+Shift+O opens the full new-session picker (create, directory, backend,
// host). Cmd/Ctrl+P toggles the command palette. All three combos are claimable by a page
// (Google Docs and Figma take Cmd+O/Cmd+P), the override lasts only while this tab has
// focus, and the window/tab-management set (Cmd+W/T/N/L/Q) stays untouched.
import { registerCommand, runCommand, commandList } from "./commands";
import { initPalette, PickItem } from "./palette";
import { initShortcutsModal } from "./shortcuts-modal";
import { chordMap, chordOf, dispatchable, displayChord, effectiveChord, keyHint, loadOverrides, titleWithKey, KEYS_EVENT } from "./keybindings";
import { hostPrefix } from "./host-prefix";   // pure display helper — safe here (never federation.ts, which boots a manager on import)
import { installMenuEcho } from "./tag-menu";   // model deps only (tag-lens/session-views) — no manager, no DOM cost

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

  function pane(id: string): HTMLIFrameElement | null {
    return document.getElementById(id) as HTMLIFrameElement | null;
  }
  function chatPost(msg: object): void {
    // Reveal the chat pane if it's toggled off, focus it, and hand it the message. The
    // __romp* globals and the pane's message handlers are read lazily at RUN time, so boot
    // order across the shell's script tags doesn't matter.
    try { if (w.__rompPaneToggle) w.__rompPaneToggle("chat", true); } catch (e) { /* rail not booted yet */ }
    const f = pane("f-chat");
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
      const ls = (pane("f-chat")?.contentWindow as any)?.__rompSessionList;
      return ls ? ls().map((r: any) => ({ id: String(r.id), name: String(r.name), bg: String(r.bg || ""), dir: "" })) : [];
    } catch (e) { return []; }
  }
  function mruIds(): string[] {
    try { return (pane("f-chat")?.contentWindow as any)?.__rompMru?.slice() || []; }
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
    run: () => { try { pane("f-chat")!.contentWindow!.postMessage({ romp: "forkSession" }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({
    id: "settings.open", title: "Open settings",
    run: () => { try { pane("f-feed")!.contentWindow!.postMessage({ romp: "openSettings" }, "*"); } catch (e) { /* feed not loaded */ } },
  });
  // Chat history back/forward (the user 2026-08-14; their own Obsidian nav keys — Ctrl+M / Ctrl+,
  // per their vault's hotkeys.json). The chat pane owns the trail (it knows the tabs + scroll spots);
  // these run when focus is in the SHELL — with focus inside the chat, its own capture handler
  // (render.ts) reads the same bindings store, so a rebind moves both at once.
  registerCommand({
    id: "chat.navBack", title: "Navigate back in the chat",
    run: () => { try { pane("f-chat")!.contentWindow!.postMessage({ romp: "chatNav", dir: -1 }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({
    id: "chat.navForward", title: "Navigate forward in the chat",
    run: () => { try { pane("f-chat")!.contentWindow!.postMessage({ romp: "chatNav", dir: 1 }, "*"); } catch (e) { /* chat not loaded */ } },
  });
  registerCommand({ id: "log.open", title: "Open the log", run: () => { if (w.__rompOpenErrs) w.__rompOpenErrs(); } });
  registerCommand({ id: "net.open", title: "Remote kernels", run: () => { if (w.__rompOpenNet) w.__rompOpenNet(); } });
  registerCommand({ id: "usage.open", title: "Token usage", run: () => { if (w.__rompUsagePanel) w.__rompUsagePanel(); } });
  registerCommand({ id: "kernel.restart", title: "Restart the romp kernel", run: () => { if (w.__rompRestart) w.__rompRestart(); } });
  // Pane toggles. The Outline pane's INTERNAL key stays 'fleet' (the pane controller's API);
  // the command speaks the user-facing name.
  const panes: Array<[string, string]> = [["chat", "chat"], ["timeline", "timeline"], ["fleet", "outline"], ["feed", "feed"]];
  for (const [key, label] of panes) {
    registerCommand({
      id: "pane." + label, title: "Show or hide the " + label + " pane",
      run: () => { if (w.__rompPaneToggle) w.__rompPaneToggle(key); },
    });
  }

  // Esc (or running an item) hands focus back to the chat pane, so "palette, Esc, type"
  // never strands the keyboard on the shell document. The palette's hotkey chips show each
  // command's EFFECTIVE binding (kbdFor), so a rebound command never advertises a stale default.
  const palette = initPalette({
    onClose: () => { try { pane("f-chat")!.contentWindow!.focus(); } catch (e) { /* no chat pane */ } },
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
  let byChord: Map<string, string> | null = null;
  const invalidate = () => { byChord = null; };
  window.addEventListener(KEYS_EVENT, invalidate);
  window.addEventListener("storage", invalidate);
  function isTyping(t: EventTarget | null): boolean {
    const el = t as HTMLElement | null;
    if (!el || !el.closest) return false;
    return !!el.closest("input, textarea, select, [contenteditable=true]");
  }
  function onKey(e: KeyboardEvent): void {
    if (keys.isOpen()) return;                    // the dialog is recording/browsing — never dispatch under it
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
  // so a keystroke in the chat document lands here exactly once.
  document.addEventListener("keydown", onKey, true);
  ["f-chat", "f-fleet", "f-feed", "f-timeline"].forEach((id) => {
    const f = pane(id);
    if (!f) return;
    const wire = () => {
      try { if (f.contentDocument) f.contentDocument.addEventListener("keydown", onKey, true); }
      catch (e) { /* cross-origin frame: not one of ours */ }
    };
    f.addEventListener("load", wire);
    wire();
  });
})();
