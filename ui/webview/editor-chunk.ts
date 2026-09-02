// The file viewer's EDITING SUBSTRATE (the user 2026-08-22): CodeMirror 6 replacing the raw-mode
// textarea. This module is its OWN esbuild entry (dist/editor-chunk.js), loaded on demand by
// file-view.ts the first time someone enters edit mode — people who never edit download nothing,
// and the main chat/feed bundles stay byte-stable (they import nothing from here; the contract is
// the window global below). CodeMirror's own ecosystem is the plugin system, curated HERE per the
// no-plugin-API doctrine (2026-08-20): syntax highlighting by file extension, local word
// autocomplete, bracket matching + auto-indent, in-buffer search, history/undo. EXPLICITLY OUT:
// language servers (an IDE's maintenance tail; agent-assisted edits through the message flow are
// the smart path), themes beyond the dashboard's own look, and any romp-level extension surface.
//
// The SAVE PATH IS NOT THIS MODULE'S: the consent gate, the nanosecond conflict floor, and the
// edit trace all live behind file-view's saveFile op — this is the text surface only, handing the
// same string to the same op. Byte fidelity is the mount contract: value() returns the buffer
// EXACTLY (CodeMirror joins lines with \n and never invents or strips a trailing newline; the
// CRLF restore stays file-view's, as it was for the textarea).
import { EditorState, Extension } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, indentOnInput, syntaxHighlighting, defaultHighlightStyle, StreamLanguage } from "@codemirror/language";
import { autocompletion, completeAnyWord, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { search, searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { yaml } from "@codemirror/legacy-modes/mode/yaml";
import { toml } from "@codemirror/legacy-modes/mode/toml";

/** The curated extension→language map, as a pure NAME so tests can pin the curation without a DOM.
 *  null = no highlighter (plain text) — everything else about the editor still applies. */
export function langNameFor(ext: string): string | null {
  const e = (ext || "").toLowerCase();
  if (["js", "jsx", "ts", "tsx", "mjs", "cjs"].includes(e)) return "javascript";
  if (["py", "pyi"].includes(e)) return "python";
  if (e === "css") return "css";
  if (["html", "htm", "svg", "vue"].includes(e)) return "html";
  if (e === "json") return "json";
  if (["md", "markdown"].includes(e)) return "markdown";
  if (["sh", "bash", "zsh", "bats"].includes(e)) return "shell";
  if (["yml", "yaml"].includes(e)) return "yaml";
  if (e === "toml") return "toml";
  return null;
}

function langExt(ext: string): Extension[] {
  switch (langNameFor(ext)) {
    case "javascript": return [javascript({ typescript: /^[cm]?tsx?$/.test(ext.toLowerCase()), jsx: /x$/.test(ext.toLowerCase()) })];
    case "python": return [python()];
    case "css": return [css()];
    case "html": return [html()];
    case "json": return [json()];
    case "markdown": return [markdown()];
    case "shell": return [StreamLanguage.define(shell)];
    case "yaml": return [StreamLanguage.define(yaml)];
    case "toml": return [StreamLanguage.define(toml)];
    default: return [];
  }
}

// The dashboard's own look and nothing more: the panel palette from styles.css, the accent only
// where the app already uses it (selection, matches, focus cues — via color-mix over var(--accent),
// which resolves to the dark literal rgba(156,210,255,…) washes exactly), the mono stack and 13px
// the viewer's read mode already renders — no new fonts, no new sizes (the font-size rule; and like
// the timeline, an adopted style must DECLARE font-family, never inherit a host's). Built per mount,
// not per module: `dark` is a CodeMirror-side branch (its base theme for panels/popups), so it must
// read the LIVE body class — a module-load constant froze the first theme forever (and was
// hardcoded { dark: true }, which kept the search panel near-black under body.theme-light — the
// user 2026-09-02, "the file editor looked black").
function rompTheme(): Extension {
  const light = typeof document !== "undefined" && document.body.classList.contains("theme-light");
  return EditorView.theme({
    "&": { height: "100%", fontSize: "13px", backgroundColor: "var(--bg, #1e1e1e)", color: "var(--fg, #d4d4d4)" },
    ".cm-content": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", caretColor: "var(--fg, #d4d4d4)" },
    ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", overflow: "auto" },
    "&.cm-focused": { outline: "none" },
    ".cm-gutters": { backgroundColor: "var(--bg, #1e1e1e)", color: "var(--dim, #8a8f98)", border: "none",
      borderRight: "1px solid var(--hairline, #3a3a3a)" },
    ".cm-activeLine": { backgroundColor: "var(--overlay-05, rgba(255, 255, 255, 0.06))" },
    ".cm-activeLineGutter": { backgroundColor: "var(--overlay-05, rgba(255, 255, 255, 0.06))" },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
      backgroundColor: "color-mix(in srgb, var(--accent, #9cd2ff) 22%, transparent)" },
    ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--accent, #9cd2ff) 14%, transparent)" },
    ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--accent, #9cd2ff) 18%, transparent)",
      outline: "1px solid color-mix(in srgb, var(--accent, #9cd2ff) 40%, transparent)" },
    ".cm-cursor": { borderLeftColor: "var(--fg, #d4d4d4)" },
    // the search panel wears the shared menu vocabulary (one dropdown skin, 2026-08-09)
    ".cm-panels": { backgroundColor: "var(--surface-raised, #252526)", color: "var(--fg, #d4d4d4)",
      borderTop: "1px solid var(--box-border, rgba(255, 255, 255, 0.12))" },
    ".cm-panel.cm-search input, .cm-panel.cm-search button": {
      fontFamily: "inherit", fontSize: "12px", background: "var(--bg, #1e1e1e)",
      color: "var(--fg, #d4d4d4)", border: "1px solid var(--box-border, rgba(255, 255, 255, 0.12))", borderRadius: "4px" },
  }, { dark: !light });
}

export interface EditorHandle {
  value(): string;
  focus(): void;
  destroy(): void;
}

export interface MountOpts {
  text: string;               // the buffer, LF-normalized by the caller (same as the textarea got)
  ext: string;                // file extension, picks the highlighter
  onChange: () => void;       // any doc change — the caller derives dirty from value()
  onSave: () => void;         // Mod-s inside the editor — same chord the textarea honored
}

export function mount(host: HTMLElement, opts: MountOpts): EditorHandle {
  const view = new EditorView({
    parent: host,
    state: EditorState.create({
      doc: opts.text,
      extensions: [
        lineNumbers(), highlightActiveLine(), drawSelection(),
        history(),
        indentOnInput(), bracketMatching(), closeBrackets(),
        autocompletion({ override: [completeAnyWord] }),   // LOCAL word completion — no servers, by design
        search({ top: true }),
        highlightSelectionMatches(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        ...langExt(opts.ext),
        rompTheme(),
        keymap.of([
          { key: "Mod-s", run: () => { opts.onSave(); return true; } },
          ...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap,
          ...historyKeymap, ...completionKeymap, indentWithTab,
        ]),
        EditorView.updateListener.of((u) => { if (u.docChanged) opts.onChange(); }),
      ],
    }),
  });
  return {
    value: () => view.state.doc.toString(),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}

// The mount contract with file-view.ts: a window global, NOT an import — an import would drag all
// of CodeMirror into the main render bundle and break the lazy discipline this chunk exists for.
// (guarded: the test bundle imports langNameFor under node, where there is no window)
if (typeof window !== "undefined") (window as any).__rompEditor = { mount, langNameFor };
