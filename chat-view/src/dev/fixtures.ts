// Acceptance fixtures for the chat webview — one synthetic "scene" per content
// type, replayed into render.ts through the exact postMessage protocol the host
// uses (session / focus / askLive). Drives the dev gallery (bin/romp-gallery)
// and the coverage test (fixtures.test.ts).
//
// PRIVACY (repo rule): everything here is invented. Placeholder UUIDs, hostname
// TESTHOST, no real prompts/paths/identities. Never paste recorded data in.

// A postMessage payload the webview understands (loosely typed — the gallery just
// forwards these verbatim to window.postMessage).
export type Msg = Record<string, any>;

export interface Scene {
  id: string;          // stable slug (nav key)
  title: string;       // sidebar label
  group: string;       // sidebar section
  messages: Msg[];     // posted, in order, when the scene is selected
}

// ---- builders --------------------------------------------------------------

const ISO = "2026-06-15T17:00:00.000Z"; // fixed timestamp → deterministic gallery
let uid = 0;
const uuid = () => `11111111-2222-3333-4444-${String(++uid).padStart(12, "0")}`;

const READY = { state: "ready", sinceEpoch: null } as const;
const AWAITING = { state: "awaiting", sinceEpoch: null } as const;

function session(id: string, name: string, events: Msg[], status: Msg = READY): Msg {
  return { type: "session", id, name, color: null, events, status, firstSeen: 1781000000 };
}
const focus = (id: string): Msg => ({ type: "focus", id });

// A tool event with all the optional fields render.ts may read.
function tool(name: string, over: Partial<Msg> = {}): Msg {
  return { kind: "tool", name, desc: "", input: "", output: "", isError: false, uuid: uuid(), ts: ISO, ...over };
}

// The compact line diff editDiff() emits: trimmed head/tail context, +/- middle.
const SAMPLE_DIFF = [
  "   priority, due date, parent/epic, project key/name, issue type,",
  "-  description.",
  "+  description, and the issue's",
  "+  **comments** (author, timestamp, body).",
].join("\n");

// A single-select / permission ask, mirroring askparse.ParsedAsk.
function ask(kind: "single" | "multi" | "submit", question: string | undefined, labels: Array<string | [string, string]>, over: Partial<Msg> = {}): Msg {
  const options = labels.map((l, i) => {
    const [label, desc] = Array.isArray(l) ? l : [l, undefined];
    return { n: i + 1, label, desc, selected: i === 0, checked: kind === "multi" ? false : undefined };
  });
  return { kind, header: undefined, question, options, cursor: 1, multiSelect: kind === "multi", ...over };
}

// ---- content-type scenes (transcript) --------------------------------------

const transcript: Scene[] = [
  {
    id: "messages", title: "Messages & markdown", group: "Transcript",
    messages: (() => {
      const id = "sc-messages";
      return [session(id, "messages", [
        { kind: "user", md: "Can you summarize the **plan** and show a code sample?", uuid: uuid(), ts: ISO, human: true },
        { kind: "assistant", md: [
          "Here's the plan:",
          "",
          "## Steps",
          "1. Parse the pane",
          "2. Render the diff",
          "3. Ship it",
          "",
          "> A short blockquote for emphasis.",
          "",
          "```ts",
          "function add(a: number, b: number): number {",
          "  return a + b; // inline",
          "}",
          "```",
          "",
          "| col | meaning |",
          "| --- | ------- |",
          "| a   | first   |",
          "| b   | second  |",
          "",
          "See [the docs](https://example.com) for more.",
        ].join("\n"), uuid: uuid(), ts: ISO },
      ]), focus(id)];
    })(),
  },
  {
    id: "thinking", title: "Thinking (plain + encrypted)", group: "Transcript",
    messages: (() => {
      const id = "sc-thinking";
      return [session(id, "thinking", [
        { kind: "thinking", text: "Let me reason about the parser before editing.", encrypted: false, uuid: uuid(), ts: ISO },
        { kind: "thinking", text: "", encrypted: true, uuid: uuid(), ts: ISO },
        { kind: "assistant", md: "Done reasoning — here's the answer.", uuid: uuid(), ts: ISO },
      ]), focus(id)];
    })(),
  },
  {
    id: "tools", title: "Tool calls (Bash/Read/Grep)", group: "Transcript",
    messages: (() => {
      const id = "sc-tools";
      return [session(id, "tools", [
        tool("Bash", { desc: "Run the build", input: "npm run build && echo done", output: "dist/kernel.js  278.9kb\n⚡ Done in 7ms" }),
        tool("Read", { file: "/repo/src/app.ts", input: "/repo/src/app.ts", output: Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join("\n") }),
        tool("Grep", { input: "TODO   src/", output: "src/a.ts:12: // TODO\nsrc/b.ts:30: // TODO" }),
        tool("Bash", { desc: "Failing command", input: "exit 1", output: "boom: nonzero exit", isError: true }),
      ]), focus(id)];
    })(),
  },
  {
    id: "edits", title: "Edit / Write diffs", group: "Transcript",
    messages: (() => {
      const id = "sc-edits";
      return [session(id, "edits", [
        tool("Edit", { file: "/repo/agents/engram-jira.md", input: "/repo/agents/engram-jira.md", diff: SAMPLE_DIFF }),
        tool("Write", { file: "/repo/notes/new.md", input: "/repo/notes/new.md" }),
      ]), focus(id)];
    })(),
  },
  {
    id: "todo", title: "Todo checklist", group: "Transcript",
    messages: (() => {
      const id = "sc-todo";
      return [session(id, "todo", [
        { kind: "todo", uuid: uuid(), ts: ISO, tasks: [
          { id: "1", subject: "Parse the pane", status: "completed" },
          { id: "2", subject: "Render the diff", activeForm: "Rendering the diff", status: "in_progress" },
          { id: "3", subject: "Add a test", status: "pending" },
        ] },
      ]), focus(id)];
    })(),
  },
  {
    id: "postal", title: "Postal messages", group: "Transcript",
    messages: (() => {
      const id = "sc-postal";
      return [session(id, "postal", [
        { kind: "postal", direction: "in", peer: "peer-bravo", color: null, body: "FYI: build is green on TESTHOST.", mid: "m1", t: 1781000100, uuid: uuid(), ts: ISO },
        { kind: "postal", direction: "out", peer: "peer-charlie", color: null, body: "HANDOFF: I own src/dev/* — stay off them.", status: "delivered", uuid: uuid(), ts: ISO },
      ]), focus(id)];
    })(),
  },
  {
    id: "queued", title: "Queued messages", group: "Transcript",
    messages: (() => {
      const id = "sc-queued";
      return [session(id, "queued", [
        { kind: "user", md: "first ask", uuid: uuid(), ts: ISO, human: true },
        { kind: "queued", texts: ["second queued", "third queued"], uuid: uuid(), ts: ISO },
      ]), focus(id)];
    })(),
  },
];

// ---- permission / ask popups (the live #live-ask region) -------------------

const popups: Scene[] = [
  {
    id: "perm-edit", title: "Permission: edit (red/green diff)", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-edit";
      return [
        session(id, "perm-edit", [{ kind: "assistant", md: "Editing engram-jira.md…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        // The diff rides on the ask itself (askparse extracts it from the pane's
        // ╌-fenced block into ParsedAsk.diff) — that's what the popup renders.
        { type: "askLive", id, ask: ask("single", "Do you want to make this edit to engram-jira.md?",
          ["Yes", ["Yes, allow all edits during this session", "shift+tab"], "No, and tell Claude what to do differently (esc)"],
          { diff: SAMPLE_DIFF, fileHead: "Edit file\n../../tmp/demo/engram-jira.md\nThis will modify /tmp/demo/engram-jira.md (outside working directory) via a symlink" }) },
      ];
    })(),
  },
  {
    id: "perm-write", title: "Permission: write (new file)", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-write";
      // A new file is all additions — leading-dash lines (markdown bullets, the
      // --- frontmatter rule) must read green, never as red deletions.
      const NEW_FILE = [
        "+---", "+title: Notes", "+---", "+# Heading",
        "+- first bullet", "+- second bullet", "+Some closing prose.",
      ].join("\n");
      return [
        session(id, "perm-write", [{ kind: "assistant", md: "Creating notes.md…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("single", "Do you want to create notes.md?",
          ["Yes", "Yes, allow all edits during this session", "No"],
          { diff: NEW_FILE, fileHead: "Create file\nnotes.md" }) },
      ];
    })(),
  },
  {
    id: "perm-notebook", title: "Permission: edit notebook", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-notebook";
      const NB_DIFF = [
        "-print(\"hello\")",
        "   No newline at end of file",
        "+import math",
        "+",
        "+# Edited via NotebookEdit to test the notebook diff UI",
        "+for n in range(5):",
        "+    print(n, math.sqrt(n))",
        "   No newline at end of file",
      ].join("\n");
      return [
        session(id, "perm-notebook", [{ kind: "assistant", md: "Editing demo.ipynb…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("single", "Do you want to make this edit to demo.ipynb?",
          ["Yes", ["Yes, allow all edits during this session", "shift+tab"], "No"],
          { diff: NB_DIFF, fileHead: "Edit notebook\nThis will modify /tmp/demo/demo.ipynb (outside working directory) via a symlink\n/tmp/demo/demo.ipynb\nReplace cell contents for cell cell-0" }) },
      ];
    })(),
  },
  {
    id: "perm-bash", title: "Permission: Bash command", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-bash";
      return [
        session(id, "perm-bash", [{ kind: "assistant", md: "Running the build…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        // body = the pane's detail block (the command + description), extracted by askparse.
        { type: "askLive", id, ask: ask("single", "Do you want to proceed?",
          ["Yes", "Yes, and don't ask again for npm commands in /repo", "No"],
          { body: "npm run build && npm test -- --reporter=dot && echo 'all green'\nBuild and test the project" }) },
      ];
    })(),
  },
  {
    id: "perm-bash-multi", title: "Permission: Bash (multi-line)", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-bash-multi";
      return [
        session(id, "perm-bash-multi", [{ kind: "assistant", md: "Tagging the release…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        // A heredoc-style multi-line command: every command line must stay in the
        // code box — only the trailing prose line is peeled as the description.
        { type: "askLive", id, ask: ask("single", "Do you want to proceed?",
          ["Yes", "Yes, and don't ask again for git commands in /repo", "No"],
          { body: [
            "for f in src/*.ts; do",
            "  echo \"checking $f\"",
            "  grep -n TODO \"$f\" || true",
            "done",
            "Scan every source file for TODO markers",
          ].join("\n") }) },
      ];
    })(),
  },
  {
    id: "perm-fetch", title: "Permission: WebFetch (url)", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-fetch";
      return [
        session(id, "perm-fetch", [{ kind: "assistant", md: "Fetching example.com…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("single", "Do you want to allow Claude to fetch this content?",
          ["Yes", "Yes, and don't ask again for example.com", ["No", "tell Claude what to do differently (esc)"]],
          { body: 'url: "https://example.com", prompt: "What is the main heading text on this page?"\nClaude wants to fetch content from example.com' }) },
      ];
    })(),
  },
  {
    id: "perm-mcp", title: "Permission: MCP tool", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-mcp";
      return [
        session(id, "perm-mcp", [{ kind: "assistant", md: "Checking the other live sessions…", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        // body = tool line + a long description (with parentheses) that must peel
        // off as dimmed italic context, not sit in the code box with the tool.
        { type: "askLive", id, ask: ask("single", "Do you want to proceed?",
          ["Yes", "Yes, and don't ask again for romp-postal commands in /repo", "No"],
          { body: "romp-postal - list_agents (MCP)\nList the live romp sessions you can message (yours is marked), with each one's git branch and what it's working on — check this before editing shared files to avoid collisions." }) },
      ];
    })(),
  },
  {
    id: "perm-plan", title: "Permission: plan mode", group: "Permission popups",
    messages: (() => {
      const id = "sc-perm-plan";
      const PLAN = [
        "## Plan",
        "",
        "1. Parse the pane into structured asks",
        "   - handle the diff frame",
        "   - handle the plan box",
        "2. Render each kind cleanly",
        "",
        "```ts",
        "function render(ask: ParsedAsk) { /* … */ }",
        "```",
      ].join("\n");
      return [
        session(id, "perm-plan", [{ kind: "assistant", md: "Here's the plan.", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("single", "Would you like to proceed?",
          ["Yes, and auto-accept edits", "Yes, and manually approve edits", "No, keep planning"],
          { planBody: PLAN, fileHead: "Ready to code?\nHere is Claude's plan:" }) },
      ];
    })(),
  },
  {
    id: "ask-single", title: "AskUserQuestion: single", group: "Permission popups",
    messages: (() => {
      const id = "sc-ask-single";
      return [
        session(id, "ask-single", [{ kind: "assistant", md: "Which approach should I take?", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("single", "Which library should we use?",
          [["highlight.js", "what we use today"], ["shiki", "heavier, more accurate"], "Type something"]) },
      ];
    })(),
  },
  {
    id: "ask-multi", title: "AskUserQuestion: multi", group: "Permission popups",
    messages: (() => {
      const id = "sc-ask-multi";
      return [
        session(id, "ask-multi", [{ kind: "assistant", md: "Pick the features to enable.", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("multi", "Which features do you want?",
          ["Line numbers", "Word-level highlight", "Auto-reload"]) },
      ];
    })(),
  },
  {
    id: "ask-submit", title: "AskUserQuestion: review/submit", group: "Permission popups",
    messages: (() => {
      const id = "sc-ask-submit";
      return [
        session(id, "ask-submit", [{ kind: "assistant", md: "Review your answers.", uuid: uuid(), ts: ISO }], AWAITING),
        focus(id),
        { type: "askLive", id, ask: ask("submit", undefined, ["Submit answers", "Cancel"], {
          chosen: ["Red", "Extra medium"],
          pairs: [{ q: "Favorite color?", a: "Red" }, { q: "Pick a size?", a: "Extra medium" }],
        }) },
      ];
    })(),
  },
];

export const SCENES: Scene[] = [...transcript, ...popups];

// The content-event kinds the transcript scenes are expected to cover (the
// coverage test pins this so a new ChatEvent kind can't quietly go undemoed).
export const EXPECTED_EVENT_KINDS = ["user", "assistant", "thinking", "tool", "todo", "postal", "queued"] as const;
