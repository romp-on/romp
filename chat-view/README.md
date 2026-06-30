# romp Chat View

A read-only, nicely-rendered, **live** view of Claude Code (romp) sessions inside
VS Code / Cursor — styled to look like the official Claude Code panel, but for
*any* session (including ones running in a terminal / romp), which the official
panel can't drive.

It is a thin client of the romp **kernel** (`bin/romp-kernel`): it connects over
WebSocket and renders the chat / feed / timeline the kernel pushes, so the panel
updates live as the session advances. A browser tab and this extension share one
kernel and render the same UI.

## How it works

- The **kernel** parses each session's transcript into an event tree
  (`bin/romp-event-model`) and pushes pane payloads over WebSocket.
- `src/extension.ts` (extension host) spawn-or-attaches a kernel and hosts the
  webviews, piping `postMessage` both ways — it does not parse transcripts.
- `../ui/webview/render.ts` + `feed.ts` + `styles.css` (webview) render the
  pushed events. Base colors/fonts come from VS Code theme variables; the
  accents (green rail dot, warm code tones) match the shipped Claude Code CSS.

Thinking text is only stored in plaintext for small models; the big reasoning
models store a signature-only block, so those render as a `Thinking…` placeholder.

## Develop

```sh
npm install
npm run build      # or: npm run watch
```

Then open this folder in VS Code/Cursor and press **F5** (Run romp Chat View).
In the dev host, run the command **”romp Chat: Open Session Viewer”** and pick a
session.

### Acceptance gallery

```sh
npm run gallery    # → http://127.0.0.1:5599/gallery.html  (--open to launch a browser)
```

A standalone page that replays one synthetic **scene per content type** —
messages/markdown, thinking, tool calls, Edit/Write diffs, todo, postal, queued,
and every permission popup (edit red/green diff, Bash command, single/multi/submit
asks) — through the exact `postMessage` protocol the host uses. Editing
`src/webview/*.ts`, `styles.css`, or the fixtures live-reloads the page (esbuild
watch + SSE).

- Fixtures: `src/dev/fixtures.ts` (synthetic only — see the repo privacy rule).
- Coverage is pinned by `src/dev/fixtures.test.ts`: every content kind is present,
  and the permission scenes trip the real `pendingEditDiff` / `pendingCommand`
  matchers, so a popup regression fails `npm test`, not just the eye.
