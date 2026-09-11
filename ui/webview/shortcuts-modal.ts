// The keyboard-shortcuts dialog (the user 2026-08-09): every bindable command with its current
// chord, VS Code's grammar — click Change, press the keys, conflicts named and resolvable, Reset
// per row. Lives in the SHELL document like the palette (browser dashboard only — VS Code rebinds
// its contributed rompChat.* commands in its own Keyboard Shortcuts editor), wears the shared
// modal vocabulary (centered card, 0.55 dim, dashboard unchanged behind it), and ships in the
// palette-main dist bundle. The pure rules — chords, conflicts, the store — are keybindings.ts;
// this file is only the DOM over them.
import { commandList } from "./commands";
import { fuzzyMatch } from "./fuzzy";
import {
  bindable, builtInOwner, chordOf, conflictOf, displayChord, effectiveChord,
  loadOverrides, saveOverride, BUILT_IN, KEYS_EVENT,
} from "./keybindings";

const CSS =
  "#rkeys-back{position:fixed;inset:0;z-index:300;display:flex;align-items:flex-start;justify-content:center;" +
  "padding:10vh 16px 16px;background:rgba(0,0,0,0.55);box-sizing:border-box}" +
  "#rkeys-back[hidden]{display:none}" +
  "#rkeys{width:min(640px,94%);max-height:76vh;display:flex;flex-direction:column;background:#252526;" +
  "border:1px solid #3a3a3a;border-radius:10px;box-shadow:0 12px 36px #000000aa;padding:14px 16px;" +
  "color:#ccc;font:13px/1.6 'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;box-sizing:border-box}" +
  "#rkeys-h{flex:0 0 auto;font-size:14px;font-weight:600;color:#e8eaed;margin-bottom:8px}" +
  "#rkeys-in{flex:0 0 auto;background:#1b1b1c;border:1px solid #3a3a3a;border-radius:6px;color:#e8eaed;" +
  "font:inherit;padding:6px 10px;outline:none;box-sizing:border-box;width:100%}" +
  "#rkeys-in:focus{border-color:var(--accent,#9cd2ff)}" +
  "#rkeys-list{flex:1 1 auto;overflow-y:auto;margin-top:8px}" +
  ".rkeys-row{display:flex;align-items:center;gap:10px;padding:5px 8px;border-radius:6px}" +
  ".rkeys-row:hover{background:rgba(255,255,255,0.05)}" +
  ".rkeys-title{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
  ".rkeys-chip{flex:0 0 auto;color:#cfd6dd;font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;" +
  "border:1px solid #3d3d42;border-bottom-width:2px;border-radius:4px;padding:1px 7px;background:#1c1c1f}" +
  ".rkeys-none{flex:0 0 auto;color:#6e7681;font-size:11px}" +
  ".rkeys-act{flex:0 0 auto;visibility:hidden;cursor:pointer;background:#2a2a2a;color:#ccc;" +
  "border:1px solid #3a3a3a;border-radius:5px;padding:1px 8px;font-size:11px}" +
  ".rkeys-row:hover .rkeys-act{visibility:visible}" +
  ".rkeys-act:hover{background:#333;color:#e8eaed}" +
  // recording / conflict states: the accent marks "the dialog is listening", never a status color
  ".rkeys-row.recording{background:rgba(156,210,255,0.12);outline:1px solid var(--accent,#9cd2ff)}" +
  ".rkeys-hint{flex:0 0 auto;color:var(--accent,#9cd2ff);font-size:11px}" +
  ".rkeys-conflict{flex:0 0 auto;color:var(--warn,#d7a23a);font-size:11px}" +
  "#rkeys-fixed{flex:0 0 auto;margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08)}" +
  "#rkeys-fixed .rkeys-sec{color:#9aa0a6;font-size:11px;margin-bottom:2px}" +
  "#rkeys-fixed .rkeys-row{padding:2px 8px}" +
  "#rkeys-fixed .rkeys-chip{color:#9aa0a6;border-color:#33363b}" +
  "#rkeys-fixed .rkeys-title{color:#9aa0a6}";

export type ShortcutsModal = {
  open(): void;
  // The tab-menu flow (the user 2026-09-10): open on ONE command, recording at once — the heading names it,
  // the list shows only its row (the built-in section too is out of the way), and a recorded chord (or
  // Backspace) closes the dialog; Esc cancels and closes. `onClose` runs after either exit, so the caller can
  // hand the focus back where the ask came from and drop the command when nothing got bound.
  openFor(id: string, onClose?: () => void): void;
  // A keydown from ANY document while recording: the shell's dispatcher hears every pane's keys and stands down
  // while the dialog is open — it hands them here, so the chord lands wherever the user's focus was when the
  // dialog opened (a tab menu's ask leaves it inside the pane). Returns whether the recorder took it.
  feed(e: KeyboardEvent): boolean;
  // One Escape level at a time: recording → cancel it (stay open); open → close. Returns whether
  // it consumed the press — the shell's Escape chain (_LANDING_ESC_JS) calls this FIRST.
  close(): boolean;
  isOpen(): boolean;
};

export function initShortcutsModal(mac: boolean, doc: Document = document): ShortcutsModal {
  let back: HTMLElement | null = null;
  let input: HTMLInputElement;
  let list: HTMLElement;
  // recording state: which command is listening, and a held candidate when it conflicted
  let recId: string | null = null;
  let pendChord: string | null = null;
  let pendOther: string | null = null;
  let soloId: string | null = null;   // openFor's command: the dialog is that one row, and a commit or a cancel closes it
  let soloDone: (() => void) | null = null;   // …and what runs once it has closed
  let refused: string | null = null;  // the last key the recorder would not take, and why — said in the row, never a silent wait
  let heading: HTMLElement;
  let fixed: HTMLElement;

  function ensure(): void {
    if (back) return;
    const style = doc.createElement("style");
    style.textContent = CSS;
    doc.head.appendChild(style);
    back = doc.createElement("div");
    back.id = "rkeys-back";
    back.hidden = true;
    const panel = doc.createElement("div");
    panel.id = "rkeys";
    panel.tabIndex = -1;   // focusable: openFor hides the filter box, and the recorder's capture listener sits on the card
    const h = doc.createElement("div");
    h.id = "rkeys-h";
    h.textContent = "Keyboard shortcuts";
    heading = h;
    input = doc.createElement("input");
    input.id = "rkeys-in";
    input.placeholder = "Filter commands…";
    input.spellcheck = false;
    list = doc.createElement("div");
    list.id = "rkeys-list";
    fixed = doc.createElement("div");
    fixed.id = "rkeys-fixed";
    const sec = doc.createElement("div");
    sec.className = "rkeys-sec";
    sec.textContent = "Built in";
    fixed.appendChild(sec);
    for (const [chord, what] of BUILT_IN) {
      const row = doc.createElement("div");
      row.className = "rkeys-row";
      const t = doc.createElement("span");
      t.className = "rkeys-title";
      t.textContent = what;
      const c = doc.createElement("span");
      c.className = "rkeys-chip";
      c.textContent = chord.split(" / ").map((x) => displayChord(x, mac)).join(" / ");
      row.appendChild(t);
      row.appendChild(c);
      fixed.appendChild(row);
    }
    panel.appendChild(h);
    panel.appendChild(input);
    panel.appendChild(list);
    panel.appendChild(fixed);
    back.appendChild(panel);
    doc.body.appendChild(back);
    input.addEventListener("input", () => render());
    back.addEventListener("click", (e) => {
      if (e.target === back) { if (recId) cancelRecord(); else close(); }   // the dim, not the card
    });
    // The recorder: CAPTURE on the card so a pressed chord never types into the filter box or
    // leaks to the page. Escape is NOT handled here — the shell's Escape chain owns it and calls
    // close(), whose first level cancels the recording.
    panel.addEventListener("keydown", onRecordKey, true);
    // another tab (or the dispatcher's own save) changed the store → repaint the chips
    try { (doc.defaultView || window).addEventListener(KEYS_EVENT, () => { if (isOpen()) render(); }); } catch (e) { /* tests */ }
  }

  function bindableCommands() {
    return commandList();   // every command is bindable — `hidden` only hides from the PALETTE list
  }

  function startRecord(id: string): void {
    recId = id;
    pendChord = pendOther = null;
    refused = null;
    render();
  }
  // the tab flow's exit: the dialog is done (a chord set, or the recording cancelled — either is leaving), and
  // the caller's onClose runs AFTER it has gone, so the focus it hands back is not stolen by a card still shown
  function leaveSolo(): void {
    const done = soloDone;
    soloId = null; soloDone = null;
    if (back) back.hidden = true;
    if (done) done();
  }
  function cancelRecord(): void {
    recId = null;
    pendChord = pendOther = null;
    refused = null;
    if (soloId) { leaveSolo(); return; }   // the tab flow: cancelling is leaving
    render();
    input.focus();
  }
  function commit(id: string, chord: string): void {
    saveOverride(id, chord);
    recId = null;
    pendChord = pendOther = null;
    refused = null;
    if (soloId) { leaveSolo(); return; }   // the tab flow: the chord is set, the dialog is done
    render();
    input.focus();
  }

  function onRecordKey(e: KeyboardEvent): void {
    if (!recId) return;
    if (e.key === "Escape") return;   // the shell Escape chain routes it into close() → cancelRecord
    e.preventDefault();
    e.stopPropagation();
    // a held conflict resolves on Enter (reassign: the other command loses the chord) — anything
    // else keeps recording
    if (pendChord && e.key === "Enter") {
      saveOverride(pendOther!, "");   // the loser is visibly unbound, never silently dead
      commit(recId, pendChord);
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") { commit(recId, ""); return; }   // unbind
    const ch = chordOf(e);
    if (!ch) return;                  // a bare modifier — still building the chord
    // Typing/close keys the panes own are refused — and SAID (the user 2026-09-10, who pressed ` then 1 and saw
    // nothing happen): a bare character would fire on every keystroke in the composer, so it needs a modifier;
    // the dialog keeps listening with the reason on the row instead of looking dead.
    if (!bindable(ch)) {
      refused = ch.includes("+")
        ? displayChord(ch, mac) + " is a key the panes own — pick another"
        : "“" + displayChord(ch, mac) + "” alone would fire while you type — hold " + (mac ? "⌘, ⌃ or ⌥" : "Ctrl or Alt") + " with it (F-keys work alone)";
      render();
      return;
    }
    // …and so is a chord a built-in behaviour already owns (Alt+Arrow moves the focus between panes before any
    // dispatcher sees it): binding it would make a dead hot key, so the row names the owner instead
    const own = builtInOwner(ch, mac);
    if (own) {
      refused = displayChord(ch, mac) + " is built in (" + own.charAt(0).toLowerCase() + own.slice(1) + ") — pick another";
      render();
      return;
    }
    refused = null;
    const other = conflictOf(ch, recId, bindableCommands(), loadOverrides(), mac);
    if (other) { pendChord = ch; pendOther = other; render(); return; }
    commit(recId, ch);
  }

  function render(): void {
    const q = input.value.trim();
    const overrides = loadOverrides();
    list.textContent = "";
    const solo = soloId ? bindableCommands().find((x) => x.id === soloId) : undefined;
    heading.textContent = solo ? "Hot key for \u201c" + solo.title.replace(/^Switch to /, "") + "\u201d" : "Keyboard shortcuts";
    input.hidden = !!solo;   // one row: nothing to filter
    fixed.hidden = !!solo;   // …and no built-in section under it: the dialog is that row alone
    for (const c of bindableCommands()) {
      if (solo && c.id !== solo.id) continue;
      if (q && !fuzzyMatch(q, c.title)) continue;
      const row = doc.createElement("div");
      row.className = "rkeys-row" + (c.id === recId ? " recording" : "");
      const t = doc.createElement("span");
      t.className = "rkeys-title";
      t.textContent = c.title;
      row.appendChild(t);
      if (c.id === recId) {
        const hint = doc.createElement("span");
        if (pendChord) {
          hint.className = "rkeys-conflict";
          const other = bindableCommands().find((x) => x.id === pendOther);
          hint.textContent = displayChord(pendChord, mac) + " is used by “" + (other ? other.title : pendOther) +
            "” — Enter reassigns it here, Esc keeps it there";
        } else if (refused) {
          hint.className = "rkeys-conflict";
          hint.textContent = refused;
        } else {
          hint.className = "rkeys-hint";
          hint.textContent = "press a key combination… (Backspace removes, Esc cancels)";
        }
        row.appendChild(hint);
        // a BOUND command's listening row offers Remove beside the hint (the user 2026-09-11: the tab menu's one
        // "Update hot key…" row covers changing and removing) — the same unbind Backspace makes, as a button
        if (effectiveChord(c.id, c.chord, overrides, mac)) {
          const rm = doc.createElement("button");
          rm.type = "button";
          rm.className = "rkeys-act";
          rm.textContent = "Remove";
          rm.addEventListener("click", (e) => { e.stopPropagation(); commit(c.id, ""); });
          row.appendChild(rm);
        }
      } else {
        const eff = effectiveChord(c.id, c.chord, overrides, mac);
        if (eff) {
          const chip = doc.createElement("span");
          chip.className = "rkeys-chip";
          chip.textContent = displayChord(eff, mac);
          row.appendChild(chip);
        } else {
          const none = doc.createElement("span");
          none.className = "rkeys-none";
          none.textContent = "not bound";
          row.appendChild(none);
        }
        const change = doc.createElement("button");
        change.type = "button";
        change.className = "rkeys-act";
        change.textContent = "Change";
        change.addEventListener("click", (e) => { e.stopPropagation(); startRecord(c.id); });
        row.appendChild(change);
        if (overrides[c.id] !== undefined) {
          const reset = doc.createElement("button");
          reset.type = "button";
          reset.className = "rkeys-act";
          reset.textContent = "Reset";
          reset.title = "back to the default" + (c.chord ? " (" + displayChord(c.chord, mac) + ")" : " (none)");
          reset.addEventListener("click", (e) => { e.stopPropagation(); saveOverride(c.id, null); render(); });
          row.appendChild(reset);
        }
      }
      list.appendChild(row);
    }
    if (!list.childElementCount) {
      const empty = doc.createElement("div");
      empty.className = "rkeys-none";
      empty.textContent = "No matches";
      list.appendChild(empty);
    }
  }

  function open(): void {
    ensure();
    back!.hidden = false;
    input.value = "";
    recId = null;
    soloId = null;
    pendChord = pendOther = null;
    render();
    input.focus();
  }
  function openFor(id: string, onClose?: () => void): void {
    ensure();
    back!.hidden = false;
    input.value = "";
    soloId = id;
    soloDone = onClose || null;
    pendChord = pendOther = null;
    refused = null;
    recId = id;                                   // recording from the first keystroke
    render();
    (back!.querySelector("#rkeys") as HTMLElement | null)?.focus?.();
  }
  function close(): boolean {
    if (!back || back.hidden) return false;
    if (recId) { cancelRecord(); return true; }   // one Escape level: leave recording, stay open
    back.hidden = true;
    return true;
  }
  function isOpen(): boolean { return !!back && !back.hidden; }
  function feed(e: KeyboardEvent): boolean {
    if (!isOpen() || !recId || e.key === "Escape") return false;   // Escape stays the shell chain's (close → cancel)
    onRecordKey(e);
    return true;
  }

  return { open, openFor, close, isOpen, feed };
}
