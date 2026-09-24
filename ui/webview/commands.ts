// The command registry behind the palette (Cmd/Ctrl+P). Anything the dashboard can do gets
// registered here as a command; the palette is a fuzzy view over this list, so a new action
// becomes keyboard-reachable by registering it — never by minting another hotkey.

export type PaletteCommand = {
  id: string;       // stable, dot-namespaced ("session.open")
  title: string;    // what the palette shows and matches on — the user's words, verb first
  chord?: string;   // DEFAULT key binding, "Mod" form ("Mod+O" — Meta on a Mac, Ctrl elsewhere).
                    // The user's overrides live in the keybindings store (romp:keys); what a command
                    // actually answers to is effectiveChord(), and the palette's hotkey chip shows
                    // that, so a rebound command never advertises a stale default (the user 2026-08-09).
  hidden?: boolean; // bindable but not listed in the palette (palette.toggle: running "toggle the
  when?: () => boolean;   // listed only while true, re-read at every open (a pane whose control the gear hid; T317)
                    // palette" FROM the palette would just blink it)
  run: () => void;
};

// Every DEFAULT key binding, by command id — ONE table, so the palette registration below, the shell
// dispatcher, and the hover hints (keybindings' titleWithKey) can never disagree about what a command
// answers to out of the box. A command absent here ships unbound; the shortcuts dialog can still bind
// it, and every surface reads the result from the overrides store.
export const DEFAULT_CHORDS: Record<string, string> = {
  "session.jump": "Mod+O",
  "session.new": "Mod+Shift+O",
  "palette.toggle": "Mod+P",
  // LITERAL Ctrl, not Mod: these mirror the user's own Obsidian nav bindings (their vault's
  // hotkeys.json, verified 2026-08-14), where "Ctrl" is the Control key on every platform.
  "chat.navBack": "Ctrl+M",
  "chat.navForward": "Ctrl+,",
  // the editor convention for a split (VS Code's Cmd/Ctrl+\); closing a split stays unbound — Cmd+W is
  // the browser's tab close, and a mis-aimed close of a column costs a re-split, so the palette owns it
  "chat.split": "Mod+\\",
  "chat.splitDown": "Mod+Shift+\\",
  // LITERAL Ctrl once more (the user 2026-09-23): the pair the VS Code view already binds to Ctrl+Alt+arrows, so the
  // two surfaces answer to the same keys on Linux and Windows. VS Code's Mac form is Cmd+Alt+arrows, which the browser
  // itself owns for its own tabs, so a Mac gets Control+Option+arrows here. Where something binds Ctrl+Alt+arrows
  // itself — GNOME's workspace switch, VoiceOver's cursor on a Mac, a graphics driver's screen rotation — it takes
  // the key before the page; the shortcuts dialog rebinds. A chord the reader had already SAVED for another command
  // keeps it: keybindings' chordMap lets a saved binding outrank a default, and the dialog marks the default as
  // yielding. The strip's bare ←/→ stay: they switch only while nothing is being typed, this chord from the composer too.
  "chat.nextTab": "Ctrl+Alt+ArrowRight",
  "chat.prevTab": "Ctrl+Alt+ArrowLeft",
  // The jump cluster's moves (the user 2026-09-24): the same LITERAL Ctrl+Alt family, turned upright. Left and right step
  // between sessions, up and down step through the open one: your own messages; with Shift, the comment threads; Enter,
  // the next unread reply. Alt+Arrow alone is the shell's pane focus, and none of these edits text, so they work from the
  // composer too. The OS caveats of the session pair apply (GNOME's workspace keys, VoiceOver); the dialog rebinds.
  "chat.prevMine": "Ctrl+Alt+ArrowUp",
  "chat.nextMine": "Ctrl+Alt+ArrowDown",
  "chat.prevComment": "Ctrl+Alt+Shift+ArrowUp",
  "chat.nextComment": "Ctrl+Alt+Shift+ArrowDown",
  "chat.nextUnread": "Ctrl+Alt+Enter",
};

const commands = new Map<string, PaletteCommand>();

export function registerCommand(cmd: PaletteCommand): void {
  // re-registering an id replaces it, so a re-boot never duplicates; the default chord comes from the
  // one table above unless the caller carries its own
  commands.set(cmd.id, cmd.chord === undefined ? { ...cmd, chord: DEFAULT_CHORDS[cmd.id] } : cmd);
}

// A command leaves the registry (the per-tab hot keys, 2026-09-10: a session whose hot key was removed, or whose
// tab is gone, has no "Switch to" command any more — the dialog and the dispatcher forget it together).
export function unregisterCommand(id: string): boolean {
  return commands.delete(id);
}

export function commandList(): PaletteCommand[] {
  return Array.from(commands.values());   // registration order — the palette's empty-query order
}

export function runCommand(id: string): boolean {
  const c = commands.get(id);
  if (!c) return false;
  c.run();
  return true;
}
