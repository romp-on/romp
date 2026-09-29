// The viewer's GitHub link (the user 2026-08-15): a lazy fileGitLink ask per open, answered by the
// file-OWNING kernel (git on ITS disk is the authority). While the ask is out the unit holds a
// placeholder — a disabled button and the loader's dots — so a slow check (the kernel's ls-remote can
// take 3 s) reads as a wait, not as the old no-button state (found in review). The action shows on
// EVERY answer (the user 2026-09-05): a real URL is an anchor; no URL is a real disabled button with
// the kernel's reason as a caption beside it and in its tooltip; a URL whose branch is not on origin
// is a dashed anchor with the note as its caption. The states run FOR REAL against a small DOM stand-in: the
// module mounts through document.createElement and hears the reply off a window message, so plain
// objects carrying the handful of DOM members it touches stand in for the page. What the DOM cannot
// show (the CSS, the kernel's side) stays pinned at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = web("file-view.ts");
const RENDER = web("render.ts");
const CHAT_CSS = web("styles.css");
const FEED_CSS = web("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const GITPR = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "gitpr.py"), "utf8");   // the one GitHub remote parser

// ── a DOM stand-in: the members the GitHub action touches at mount and on the reply ───────────────
class El {
  id = ""; title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1;
  href = ""; target = ""; rel = "";
  childNodes: Array<El | string> = [];
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
  classList = {
    add: (...c: string[]) => { for (const x of c) this.classes.add(x); },
    contains: (c: string) => this.classes.has(c),
  };
  constructor(public tagName: string) {}
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.childNodes = v === "" ? [] : [v]; }
  appendChild<T extends El>(c: T): T { this.childNodes.push(c); return c; }
  replaceChildren(...cs: El[]): void { this.childNodes = [...cs]; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(): void {}
  removeEventListener(): void {}
}
const win: any = new EventTarget();       // window: the reply arrives as a message event
win.parent = win;
(globalThis as any).window = win;
(globalThis as any).document = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: () => null,
  addEventListener: () => {},
  removeEventListener: () => {},
  body: new El("body"),
};
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};

const posted: any[] = [];
const parts = (unit: El) => ({
  cap: unit.childNodes.find((c) => c instanceof El && c.className === "fileview-gh-why") as El | undefined,
  dots: unit.childNodes.find((c) => c instanceof El && c.className === "fileview-gh-dots") as El | undefined,
  ctl: unit.childNodes.find((c) => c instanceof El && c.classList.contains("fileview-btn")) as El,
});
/** The unit as mounted, before any reply: hidden and empty while the kernel's check is out (T367, the user
 *  2026-09-12: nothing is rowed until a link resolves; the wait is named for assistive tech). */
function assertPending(unit: El): void {
  assert.equal(unit.className, "fileview-gh");
  assert.equal(unit.hidden, true, "hidden while the check is out: no greyed button, no dots (T367)");
  assert.equal(unit.getAttribute("aria-busy"), "true", "and named as a wait for assistive tech");
  assert.equal(unit.childNodes.length, 0, "no placeholder button, no dots, no caption");
}
// The module with its poster bound ONCE for this file: every initFileView call adds another romp:wsup
// (and message) listener on the shared window, so binding per test would make the re-ask count below
// equal the number of tests run so far — which is why it could only be asserted as "at least one".
let bound: Promise<typeof import("./file-view")> | null = null;
function view(): Promise<typeof import("./file-view")> {
  if (!bound) bound = import("./file-view").then((fv) => { fv.initFileView((m) => posted.push(m)); return fv; });
  return bound;
}
async function mountAndAnswer(url: string, reason: string): Promise<El> {
  const fv = await view();
  const unit = fv.githubLinkAction.mount({ path: "/tmp/notes-api/src/app.py", sid: null }) as unknown as El;
  assertPending(unit);
  const ask = posted[posted.length - 1];
  assert.deepEqual(ask, { type: "fileGitLink", path: "/tmp/notes-api/src/app.py", sid: undefined, reqId: ask.reqId });
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileGitLink", reqId: ask.reqId, url, reason } }));
  assert.equal(unit.hasAttribute("aria-busy"), false, "the answer ends the wait");
  assert.equal(parts(unit).dots, undefined); assert.equal(parts(unit).cap, undefined, "never a caption (T367)");
  assert.equal(unit.childNodes.filter((c) => c instanceof El && c.classList.contains("fileview-btn")).length, url ? 1 : 0,
    url ? "one control: the link" : "no control: nothing to link to, nothing rowed (T367)");
  assert.equal(unit.hidden, !url, url ? "shown with the link" : "stays hidden");
  return unit;
}

test("state 1 — a real URL: an anchor that opens a new tab, no caption, the URL as tooltip", async () => {
  const url = "https://github.com/TESTORG/notes-api/blob/main/src/app.py";
  const { cap, ctl } = parts(await mountAndAnswer(url, ""));
  assert.equal(ctl.tagName, "a");
  assert.equal(ctl.href, url); assert.equal(ctl.target, "_blank"); assert.equal(ctl.rel, "noopener");
  assert.equal(ctl.textContent, "GitHub ↗");
  assert.equal(ctl.title, url, "the full URL one hover away");
  assert.equal(ctl.classList.contains("fileview-gh-note"), false);
  assert.equal(cap, undefined, "nothing to say — no caption");
});

test("state 2 — no URL: nothing is rowed, neither a greyed button nor the reason (T367)", async () => {
  // the user 2026-09-12 wanted the greyed link and its explanation gone from a file outside a repository: the
  // 2026-09-05 always-fill-the-slot rule gave way; the kernel's verdict still rides the reply, it is just not rowed
  const unit = await mountAndAnswer("", "the file is not committed");
  assert.equal(unit.hidden, true);
  assert.equal(unit.childNodes.length, 0, "no button, no caption");
  assert.equal(unit.hasAttribute("aria-busy"), false, "the wait is over, and it stays hidden");
});

test("state 2b — a kernel that predates link reasons: no URL is no link, nothing rowed", async () => {
  const unit = await mountAndAnswer("", "");
  assert.equal(unit.hidden, true);
  assert.equal(unit.childNodes.length, 0);
});

test("state 3 — a URL whose branch is not on origin: a dashed anchor, the note in its tooltip and aria-label (no caption)", async () => {
  const url = "https://github.com/TESTORG/notes-api/blob/wip/src/app.py";
  const { cap, ctl } = parts(await mountAndAnswer(url, "the branch has not been pushed"));
  assert.equal(ctl.tagName, "a"); assert.equal(ctl.href, url);
  assert.equal(ctl.classList.contains("fileview-gh-note"), true);
  assert.equal(ctl.title, url + "\nthe branch has not been pushed");
  assert.equal(ctl.getAttribute("aria-label"), "GitHub: the branch has not been pushed");
  assert.equal(cap, undefined, "the note rides the tooltip and aria-label, not a caption (T367)");
});

test("a reply for an older open lands nowhere — the newer open keeps waiting for its own", async () => {
  const fv = await view();
  const ctx = { path: "/tmp/notes-api/src/app.py", sid: null };
  const first = fv.githubLinkAction.mount(ctx) as unknown as El;
  const firstReq = posted[posted.length - 1].reqId;
  const second = fv.githubLinkAction.mount(ctx) as unknown as El;   // a replace-open: its hooks supersede
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileGitLink", reqId: firstReq,
    url: "https://github.com/TESTORG/notes-api/blob/main/src/app.py", reason: "" } }));
  assertPending(first);    // the superseded open hears nothing: still the placeholder it mounted with
  assertPending(second);   // a stale reply is not the newer open's answer
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileGitLink", reqId: posted[posted.length - 1].reqId,
    url: "", reason: "the file is not committed" } }));
  assert.equal(second.hasAttribute("aria-busy"), false);
  assert.equal(second.hidden, true, "no URL: nothing rowed (T367)"); assert.equal(second.childNodes.length, 0);
});

test("a socket drop while the ask is out: the reconnect re-asks with the same reqId, so the wait ends", async () => {
  // the frame went and nothing re-sends it, so the reply is lost and the placeholder would pulse for
  // the rest of the open. The shim's romp:wsup (its RECONNECT event; the first connect fires none) is
  // the re-ask's trigger — an event, not a timer, as the browse overlay does.
  const fv = await view();
  const unit = fv.githubLinkAction.mount({ path: "/tmp/notes-api/src/app.py", sid: null }) as unknown as El;
  const reqId = posted[posted.length - 1].reqId;
  const before = posted.length;
  win.dispatchEvent(new Event("romp:wsdown"));
  assertPending(unit);     // the drop alone changes nothing: the answer may still come
  win.dispatchEvent(new Event("romp:wsup"));
  const again = posted.slice(before);
  assert.equal(again.length, 1, "exactly one re-ask per reconnect: one listener, one pending ask");
  assert.ok(again.every((m) => m.type === "fileGitLink" && m.reqId === reqId && m.path === "/tmp/notes-api/src/app.py"),
    "the same question, same reqId: a late first reply and the second are one answer");
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileGitLink", reqId, url: "", reason: "not committed (staged only)" } }));
  assert.equal(unit.hasAttribute("aria-busy"), false);
  assert.equal(unit.hidden, true, "no URL: nothing rowed (T367)");
  const settled = posted.length;
  win.dispatchEvent(new Event("romp:wsup"));
  assert.equal(posted.length, settled, "an answered open asks nothing more");
});

// ── what the DOM cannot show, pinned at source ─────────────────────────────────────────────────────

test("the ask is lazy, per open, and sid-routed — never on the /file byte path", () => {
  assert.match(VIEW, /const ask = \(\) => post\(\{ type: "fileGitLink", path, sid: sid \|\| undefined, reqId \}\);/);
  // thumbnails must not pay three git subprocesses each: /file itself is untouched
  assert.doesNotMatch(KERNEL, /_file_github_url\(fp/);
});

test("the poster is bound at the boot of the document that hosts the viewer", () => {
  assert.match(RENDER, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/);
});

test("the sheets dress the unit: the link at button size, hidden and out of the row's flow when there is none, the note dashed (T367)", () => {
  // both sheets: the FEED hosts the same viewer (the file browser), so its unit dresses the same
  for (const css of [CHAT_CSS, FEED_CSS]) {
    assert.match(css, /a\.fileview-btn \{ text-decoration: none;/);
    assert.match(css, /\.fileview-gh \{ display: inline-flex; align-items: center; gap: 5px; min-width: 0; \}/);
    // the unit's inline-flex would beat the browser's [hidden] rule and leave a live flex item eating a gap (review)
    assert.match(css, /\.fileview-btn\[hidden\], \.fileview-group\[hidden\], \.fileview-zoom\[hidden\], \.fileview-gh\[hidden\] \{ display: none; \}/);
    // the caption and the placeholder dots are gone with the always-fill-the-slot rule: no dead rules for them
    assert.doesNotMatch(css, /\.fileview-gh-(why|dots)/, "the caption and the placeholder dots left (T367)");
    assert.match(css, /\.fileview-dot \{ width: 4px; height: 4px; border-radius: 50%; background: var\(--accent\);\n  animation: fileview-pulse/, "the pane's loader dots stay");
    assert.match(css, /\.fileview-btn \{ font: inherit; font-size: 0\.82em;/);
    // the disabled dress is every bar button's since the text-size control's ends took it too; the unit's no-link
    // button is a real disabled button under it
    assert.match(css, /\.fileview-btn:disabled, \.fileview-btn\[aria-disabled="true"\] \{ opacity: 0\.55; cursor: default; \}/);
    assert.match(css, /\.fileview-btn:disabled:hover, \.fileview-btn\[aria-disabled="true"\]:hover \{ border-color: var\(--card-border\); color: var\(--fg\); background: transparent; \}/);
    assert.match(css, /\.fileview-btn:disabled:active, \.fileview-btn\[aria-disabled="true"\]:active \{ transform: none; \}/);
    assert.match(css, /a\.fileview-gh-note \{ border-style: dashed; \}/);
    assert.doesNotMatch(css, /\.fileview-gh[^{\n]*aria-disabled|a\.fileview-btn[^{\n]*aria-disabled/,
      "the unit's disabled state is the button's own, not an aria bit on an anchor (the text-size control's ends carry aria-disabled to keep the keyboard focus, and the shared rule dresses both)");
  }
});

test("replies are reqId-guarded and cannot touch a later open", () => {
  assert.match(VIEW, /m\.type === "fileGitLink" && gitHooks && m\.reqId === gitHooks\.reqId/);
  assert.match(VIEW, /h\.apply\(String\(m\.url \|\| ""\), String\(m\.reason \|\| ""\)\);/, "the reply's reason travels with its url");
  // both the close and the replace path drop the hooks, so a late reply lands nowhere
  const closes = VIEW.match(/gitHooks = null;/g) || [];
  assert.ok(closes.length >= 2, "cleared on close AND on replace-open");
});

test("the kernel's answer is a verdict from git itself, threaded off the recv loop", () => {
  // the phrases themselves are the kernel's (tests/test_file_github.py pins them); the viewer shows
  // whatever string arrives
  assert.match(KERNEL, /def _file_github_url\(raw, sid\):/);
  assert.match(KERNEL, /def _file_github_link\(raw, sid, check_origin=True\):/, "the (url, reason) sibling the op uses");
  // ls-files reads the index; a staged-only file is checked against HEAD's tree before a URL is built
  assert.match(KERNEL, /"cat-file", "-e", "HEAD:" \+ rel\.replace\(os\.sep, "\/"\)/, "no live button at a path GitHub has on no ref");
  assert.match(KERNEL, /"url": url, "reason": reason\}\)/, "the reason rides the reply");
  // the local tracking ref answers first; ls-remote is the fallback, short-timed, never a prompt
  assert.match(KERNEL, /"refs\/remotes\/origin\/" \+ ref/);
  assert.match(KERNEL, /"ls-remote", "--heads", "origin", full\], top, timeout=GH_LS_REMOTE_S/);
  assert.match(KERNEL, /GIT_TERMINAL_PROMPT="0"/);
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "fileGitLink":/);
  assert.match(KERNEL, /threading\.Thread\(target=_gl, daemon=True\)\.start\(\)/);
  // the spellings git actually writes for a GitHub origin — incl. ports, ssh.github.com and an ssh host alias;
  // the kernel shares gitpr's parser rather than keeping its own
  assert.match(KERNEL, /^_GITHUB_REMOTE = gp\.GITHUB_REMOTE_RE\b/m);
  assert.match(GITPR, /ssh:\/\/git@\(\?:ssh\\\.\)\?github\\\.com\(\?:-\[\\w\.-\]\+\)\?\(\?::\\d\+\)\?/);
  assert.match(KERNEL, /ls-files", "--error-unmatch"/, "tracked files only — no link to a thing not there");
  // realpath, not normpath: a lexical '..' collapse linked a DIFFERENT file than the viewer shows
  assert.match(KERNEL, /p = os\.path\.realpath\(p\)\n    d = os\.path\.dirname\(p\)/);
});

test("the GitHub link is the action REGISTRY's first entry, not another hand-wired button", () => {
  // the registry (the user 2026-08-22): internal seam, no compatibility promise — actions on the
  // open file declare a mount() instead of editing openFileView, so viewer PRs stop colliding there
  assert.match(VIEW, /export function registerFileViewAction\(a: FileViewAction\): void \{/);
  assert.match(VIEW, /if \(!fileViewActions\.some\(\(x\) => x\.id === a\.id\)\) fileViewActions\.push\(a\);/, "same id registered twice mounts once");
  assert.match(VIEW, /export const githubLinkAction: FileViewAction = \{\n  id: "github-link",/);
  assert.match(VIEW, /registerFileViewAction\(githubLinkAction\);/);
  // openFileView renders registered actions by WALKING THE TABLE, after the built-ins
  assert.match(VIEW, /for \(const a of fileViewActions\) \{\n    const n = a\.mount\(\{ path, sid: sid \|\| null \}\);\n    if \(n\) fileGroup\.appendChild\(n\);\n  \}/, "into the file group (T367)");
});
