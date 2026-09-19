// The settings gear — the ⛭ modal (#rsettings) + token-usage analytics modal,
// SHARED by both hosts. This used to live as inline strings in the kernel's
// feed page (_gear_html/_GEAR_CSS/_GEAR_JS), which made it browser-only and a
// hand-ported drift surface; now the feed bundle renders it everywhere (the
// user 2026-07-13: the SAME romp-styled settings UI in VS Code, not a native
// picker). Styling lives in feed.css (the "settings gear" section).
//
// Host adaptations, all injected:
// - post(op): the feed bundle's one host channel (a real VS Code webview
//   throws if the API is acquired twice, so the old per-change re-acquire is
//   gone). Kernel ops (setAutoNudge, setJudgeModel, ...) ride it.
// - window.__rompKernelBase: fetch prefix — '' in the browser (same origin);
//   the VS Code host injects http://127.0.0.1:<port> and allows it in the
//   webview CSP (connect-src). window.__rompKernelToken rides along the same
//   way: the kernel gates every request on the serve token (loopback included);
//   the browser has its cookie, a webview's cross-origin fetch does not — so
//   ku() appends ?token= when the host injected one (mirrors media.ts kernelUrl).
// - Opening: a {romp:'openSettings'} window message (the web shell's rail gear
//   posts it into the settings iframe, the kernel's /settings page hosting this
//   module on its own since 2026-09-10; the VS Code host posts it into the feed webview).
// Model/effort <option>s come from GET /models at open (they were server-baked
// into the HTML before — /models was already the single source of truth).

var gclock = require('./gesture-clock.js');   // every `gt` below is minted here (see that file)
var BN = require('./backend-names.ts');   // the backends' user-facing names and the offer rule (T288)
var WP = require('./widget-prefs.ts');   // the order arithmetic both widget sections' drags share (moveId)
var SW = require('./status-widgets.ts');   // the status line's widgets (T409): the Status line section's rows render from its registry, as the line does
var TW = require('./tab-widgets.ts');   // the tab-title widgets (T379): the registry the Tab widgets section's rows render from, the strip's own module
var SC = require('./status-controls.ts');   // the status line's controls (T415 part two): the preview draws them through the line's own renderer, over a demo status
var LS = require('./landing-settle.ts');   // gestureEvidence: the chat's rule for telling the user's scroll from the browser's own (the section ask ends only on input, T379 follow-up)
function kb() { return (typeof window !== 'undefined' && window.__rompKernelBase) || ''; }
function ku(path) {
  var tok = (typeof window !== 'undefined' && window.__rompKernelToken) || '';
  if (!tok) return kb() + path;
  return kb() + path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(tok);
}

// The keyboard-shortcuts SECTION is one link now (the user 2026-08-09): the full list — bindable
// commands, recording, conflicts — lives in the shell's shortcuts dialog (shortcuts-modal.ts), and
// this row just opens it. Web shell only: in VS Code the same actions are contributed rompChat.*
// commands, rebindable in VS Code's own Keyboard Shortcuts editor, so the row says that instead
// (a second editor there would fight the native one). The old static list is gone with the section
// (it opened with "Enter — send message", a typing key nobody looks up, and went stale per surface).
var RS_TABS = [['general', 'General'], ['chat', 'Chat'], ['feed', 'Feed'], ['sessions', 'Sessions'], ['automation', 'Automation'], ['tasks', 'Task tracking'], ['debug', 'Debug']];
// older remembered tabs (romp:settingsTab) and older asks map to the tab that holds their rows now, never a blank card (T400):
// Automatic became Task tracking, System dissolved into Debug (its account rows into General), the short-lived Tabs tab is Chat's section,
// and Appearance is General's section since T404 (the user 2026-09-13): an ask for it lands on General scrolled to that section
var TAB_ALIASES = { automatic: 'tasks', system: 'debug', tabs: 'chat', appearance: 'general' };
var SHORTCUT_ROWS =
  '<div class=rs-key id=rs-keys-web hidden><button id=rs-keys-btn type=button>Customize shortcuts…</button>' +
  '<span class=rs-key-desc>view, record and rebind every dashboard shortcut</span></div>' +
  '<div class=rs-key id=rs-keys-vsc hidden><span class=rs-key-desc>Shortcuts are VS Code keybindings here — search "rompChat" in Keyboard Shortcuts.</span></div>';

// Auto Nudge's one-sentence line under its label (T408, the user 2026-09-13: the Automation rows' hover tooltips were hard to
// see, since a popup at the bottom of a two-row pane runs past the card and scrolls it; the rows say what they do in a line
// that is always there instead) lives in a var because fillAutoNudge() appends to it when the attached machines disagree:
// the row then has to say WHICH ones, and this is the one level down from the label.
var AUTONUDGE_SUB = "When a session goes idle with its work still in progress and nothing awaited, nudge it once for a status update, on every connected machine.";
// Fast mode's one-line hint, in a var because judgeFastGate() swaps it for the greyed-out reason when no
// judge tier is on Opus (the opt-in rides only a call whose model is Opus, so the box is inert then).
var JUDGEFAST_SUB = "This tier's judge calls run in Claude Code's fast mode (an Opus-only research preview, billed at a premium, "
  + "about twice the standard Opus rate). Off by default. Follows to every connected machine's kernel.";
var JUDGEFAST_SUB_OFF = "Fast mode is Opus-only, and this tier is not on Opus. Pick Opus for it to use fast mode; the setting is kept.";
function judgeFastSubRefused(word, r) {
  return "Fast mode was declined by Claude Code for the last " + word + " call (" + ((r && r.reason) || "no reason given")
    + "); the judges run at normal speed. The setting is kept.";
}

// The modal markup — ported verbatim from the kernel's _gear_html; the model/
// effort selects start empty and are filled from /models (see fill()).
var GEAR_HTML =
  '<button id=rgear hidden aria-hidden=true></button>' +
  '<div id=rsettings hidden><div class=rs-card>' +
  '<div class=rs-h>Settings</div>' +
  // THE TABS (T379, the user 2026-09-12; re-cut T400 into General, Chat, Feed, Sessions, Task tracking, Appearance, Debug): the
  // settings grouped by the surface they belong to, seven pills under the
  // title in the menu vocabulary; every row keeps its id and its key. The tab-widgets gear on the chat strip opens the
  // Chat tab scrolled to its Tab widgets section (openSettings(tab, section)); the last tab used is remembered per
  // browser (romp:settingsTab). RS_TABS is the one list the pills, the panes and selectTab read.
  '<div class=rs-tabs id=rs-tabs role=tablist>' + RS_TABS.map(function (t) { return '<button class=rs-tab type=button role=tab data-tab=' + t[0] + ' aria-selected=false>' + t[1] + '</button>'; }).join('') + '</div>' +
  '<div class=rs-pane data-pane=general hidden>' +
  // GENERAL (T400, the user 2026-09-12): the account this machine is logged in as, which panes this browser's dashboard shows at
  // all, and the keyboard shortcuts: settings about the dashboard as a whole, not one surface of it
  "<div class='rs-sec rs-sec-first'>Account</div>" +
  "<div class='rs-row' id=rs-billing style='cursor:default'>" +
  '<span style="flex:1 1 auto"><b>Claude login</b>' +
  '<span class=rs-sub id=rs-login-acct>…</span>' +
  "<div id=rs-login-flow style='margin-top:6px'>" +
  "<button id=rs-login-btn type=button style='cursor:pointer;background:var(--btn-bg, #2a2a2a);color:var(--fg, #ccc);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:3px 10px'>Log in to Claude Code</button>" +
  // rs-note, NOT rs-sub: this is a live inline status ("starting the login flow…"), not the row's
  // description — as an rs-sub it floated a SECOND hover popover under the Account row, stacked on
  // rs-login-acct's (the user 2026-09-02, who saw two tooltips stacked; even empty it painted a box)
  "<span id=rs-login-state class=rs-note style='margin-left:8px'></span>" +
  '</div>' +
  // the STORED logins (T346): the other Claude logins a session can be billed to, one row each with a
  // Remove; filled from the kernel's authed /logins (labels and dates, never a token)
  "<div id=rs-logins class=rs-logins style='margin-top:8px'></div>" +
  '</span></div>' +
  // Panes (the user 2026-09-10): which optional panes this browser's dashboard shows at all. The chat is
  // required and not listed; the rows are Sessions, Outline and Feed (the rail's own words for the panes
  // keys: timeline, 'fleet', feed), on by default. A pane off here is not in the dashboard: no rail button,
  // no phone tab, no palette command, its iframe never given a src (nothing loads, no socket). The kernel
  // keeps judging and tracking every session regardless; this is where THIS browser looks. The section is
  // for the dashboard's own gear (ownPage): the VS Code panels have no dashboard, so initGear hides it there.
  '<div class=rs-sec id=rs-panes-sec>Panes</div>' +
  '<label class="rs-row rs-panes-row"><input type=checkbox id=rs-pane-timeline checked>' +
  '<span><b>Sessions</b>' +
  '<span class=rs-sub>The lanes across the bottom: every session\'s turns, judging and messages on one time axis. Off, the band and its button are gone from this browser.</span>' +
  '</span></label>' +
  '<label class="rs-row rs-panes-row"><input type=checkbox id=rs-pane-fleet checked>' +
  '<span><b>Outline</b>' +
  '<span class=rs-sub>The by-session goal trees, with search across sessions. Off, the column and its button are gone from this browser.</span>' +
  '</span></label>' +
  '<label class="rs-row rs-panes-row"><input type=checkbox id=rs-pane-feed checked>' +
  '<span><b>Feed</b>' +
  '<span class=rs-sub>The cards: what needs you, what is in progress, what shipped. Off, the column and its button are gone from this browser; tracking carries on and the other browsers and devices are unaffected.</span>' +
  '</span></label>' +
  // the Files control (T317, the user 2026-09-10, who recalled a setting for it; a Panes row since T404): the toggle at the bottom of
  // the dashboard and the Files tab on a phone. OFF by default (T317b, the user the same day: the control is asked for,
  // not shipped): on shows both; off hides both, closes an open Files pane, and a file link opens over the pane you
  // clicked (the shell reads this store key and tells the panes: kernel.py
  // _LANDING_COLLAPSE_JS, render.ts panesAvail). Only the literal true shows it, so the box is unchecked until read.
  // The key is showFilesControl, a FRESH one (T317b review): the T317-era load() merged its default filesControl: true
  // into the object and save() wrote the whole object on ANY change, so a profile that touched any setting in that
  // window carries filesControl: true without ever touching this box; the old key is never read and load() drops it,
  // so the next save leaves it behind.
  '<label class="rs-row rs-panes-row"><input type=checkbox id=rs-filesctl>' +   // a Panes row like the three above it, so the off-dashboard hide takes it too (T404's tidy)
  '<span><b>Files</b>' +   // the row's name follows Sessions, Outline and Feed above it (T407, the user 2026-09-13); the id, the key and the default stand
  '<span class=rs-sub>Adds the Files toggle to the bottom of the dashboard, and the Files tab on a phone. Off (the default) hides them and closes the Files pane if it is open; file links then open over the pane you clicked.</span>' +
  '</span></label>' +
  // APPEARANCE, a section of General since T404 (the user 2026-09-13; a tab of its own before, renamed from Colors 2026-08-28): the
  // theme, the colormap and the session palette; an older ask or remembered tab named appearance lands here (TAB_ALIASES)
  "<div class='rs-sec' data-section=appearance>Appearance</div>" +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto;min-width:0'><b>Theme</b>" +
  "<span class=rs-sub>The dashboard's overall look, every pane. Classic and Yatharth are dark (they differ in the tab strip: original high-contrast vs the contributed flat-wash); Yatharth light is the warm light theme — inside VS Code it wins over the editor theme, deliberately.</span>" +
  "<div id=rs-theme style='position:relative;margin-top:5px'></div>" +
  '</span></div>' +
  "<div class='rs-row rs-sep' style='cursor:default'><span style='flex:1 1 auto'><b>Colormap</b>" +
  '<span class=rs-sub>One ramp for the whole dashboard — feed recency, usage, and context bars. Brightest = newest / highest.</span>' +
  "<div id=rs-cmap><button id=rs-cmap-btn type=button aria-label='Pick the recency colormap'></button>" +
  '<div id=rs-cmap-list hidden></div></div></span></div>' +
  "<div class='rs-row rs-sep' style='cursor:default'><span style='flex:1 1 auto'><b>Session colors</b>" +
  '<span class=rs-sub>The palette sessions draw their identity color from — tabs, cards, lanes. Switching recolors every session to the same slot in the new set.</span>' +
  "<div id=rs-pal><button id=rs-pal-btn type=button aria-label='Pick the session palette'></button>" +
  '<div id=rs-pal-list hidden></div></div></span></div>' +
  // PERMISSIONS (T404): what romp may do on this machine on your behalf
  '<div class=rs-sec>Permissions</div>' +
  "<label class='rs-row'><input type=checkbox id=rs-fileedit>" +
  '<span><b>Allow file editing</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>Let the file viewer’s Edit save straight to disk on the file’s machine. Off by default; the viewer asks the first time. A session working in the edited folder is told, and a save always refuses when the file changed underneath you. Applies on every connected machine’s kernel.</span>' +
  '</span></label>' +
  // THIS MACHINE (T404): the install's own housekeeping, memory and updates
  '<div class=rs-sec>This machine</div>' +
  "<label class='rs-row'><input type=checkbox id=rs-conserve>" +
  '<span><b>Conserve memory</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>Close the claude process of a session that has FADED (idle over an hour) and is on no open tab (each averages ~340MB). Everything persists — it revives on a tab click, a message, or a scheduled wake. An open tab always keeps its process; off = every session keeps its process for as long as it lives.</span>' +
  '</span></label>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto'><b>Updates install automatically <span class=rs-mixed hidden></span></b>" +
  '<span class=rs-sub>romp watches for new tagged releases (every 6 hours) AND new commits on main (origin polled every few minutes, plus a restart offer when updated code sits on disk unbooted) — one banner covers both, and acting on it converges every attached machine. Check and ask (the default) offers the banner with an Update button; Install automatically converges by itself: a change to kernel code restarts it at once (turns in flight are cut and resume with their history); anything else (the UI, the docs, the postal bus) converges in place with the kernel left up; Off never checks. Kernel-side setting.</span>' +
  "<select id=rs-updates style='display:none'>" +
  '<option value=ask>Check and ask</option><option value=auto>Install automatically</option><option value=off>Off</option>' +
  '</select></span></div>' +
  
  '<div class=rs-sec>Keyboard shortcuts</div>' + SHORTCUT_ROWS +
  '</div>' +
  '<div class=rs-pane data-pane=chat hidden>' +
  // DISPLAY (T404, the user 2026-09-13; Transcript before): how the chat shows things, nothing here changes what a session does
  "<div class='rs-sec rs-sec-first'>Display</div>" +
  '<label class=rs-row><input type=checkbox id=rs-compact>' +
  '<span><b>Compact transcript</b>' +
  '<span class=rs-sub>Collapse each run of tool uses into one line and hide thinking blocks in the chat.</span>' +
  '</span></label>' +
  // compact tabs and agents (the user 2026-09-08: on a phone, the strip and the background-work panel left about
  // three lines of transcript in view): density only, a body class render.ts applies (dense-chrome.ts)
  '<label class=rs-row><input type=checkbox id=rs-dense>' +
  '<span><b>Compact tabs and agents</b>' +
  '<span class=rs-sub>Keeps more of the transcript in view: tighter rows in the background-work panel under the transcript, which shows about four rows and scrolls for the rest, and smaller tabs and group headers in the tab strip. On a phone the session picker stands in for the strip, so there only the panel changes. Off by default.</span>' +
  '</span></label>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto;min-width:0'><b>Text scheme</b>" +
  "<span class=rs-sub>Chat text colors only. Each option previews its own tiers — prose, the dimmer tool text, code. (Solarized Light is omitted — its tiers are made for a light page and turn muddy here.)</span>" +
  "<div id=rs-chatscheme style='position:relative;margin-top:5px'></div>" +
  '</span></div>' +
  '<label class=rs-row><input type=checkbox id=rs-striprows checked>' +
  '<span><b>One tag group per row in the tab strip</b>' +
  '<span class=rs-sub>With the tabs grouped by tag, each group starts on its own row with its tag at the left edge. Off, the groups follow one another across the strip and wrap as they need, so many tags do not mean many rows.</span>' +
  '</span></label>' +
  "<div class='rs-sec'>Comments</div>" +
  "<div class='rs-row rs-jrow'><b>Comment model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model NEW comment threads start on. Same as the session (the default) keeps each thread on the model of the conversation it branches from; pinning one here starts every new thread on it. The comment dialog shows this default and its own pick still wins. Follows to every connected machine's kernel.</span><select id=rs-cmtmodel></select></div>" +
  "<div class='rs-row rs-jrow'><b>Comment effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for new comment threads. Same as the session (the default) inherits the effort of the conversation the thread branches from. Follows to every connected machine's kernel.</span><select id=rs-cmteffort></select></div>" +
  "<label class='rs-row'><input type=checkbox id=rs-cmtfast>" +
  '<span><b>Fast comment threads</b><span class=rs-mixed hidden></span>' +
  "<span class=rs-sub>Start new comment threads in fast mode (Opus-only research preview). If the thread's model can't run it, the thread still opens on that model at normal speed, with a notice. Off = same as the session. Follows to every connected machine's kernel.</span>" +
  '</span></label>' +
  // THINKING (T404): asks the API for reasoning summaries on every new session, so it CREATES (tokens the session pays for, and
  // adaptive thinking turned on where it was off), which is why it sits in Chat and not under Display
  "<div class='rs-sec'>Thinking</div>" +
  "<label class='rs-row'><input type=checkbox id=rs-thinksum>" +
  '<span><b>Thinking summaries</b>' +
  '<span class=rs-sub>For every new Claude Code session, ask the API for reasoning summaries and show them in the chat, folded to two lines (click to expand). The summaries are output tokens the session pays for, which is why this row sits under Chat and not Display. Compact transcript still hides them. If thinking was turned off for this install, this turns adaptive thinking on as well. A running session picks the change up at its next reconnect: an effort or billing switch, the first fast-mode opt-in, or a kernel restart. Switching the model applies live and does not reconnect. Off by default; this kernel keeps its own copy.</span>' +
  '</span></label>' +
  // CHAT HISTORY (2026-09-15): every chat loaded from its first message for every page, instead of from the saved document's
  // cut with the history above it loading as the user scrolls; the lever for a page that cannot fill the region above the cut.
  // Per-install, like Thinking summaries: the floor is this kernel's build decision; the kernel reads it live at every push.
  // Its own section (the 1704 read, low 4), in the user's words, not the wire's (low 5), the spans closed (low 3)
  "<div class='rs-sec'>Chat history</div>" +
  "<label class='rs-row'><input type=checkbox id=rs-wholechat>" +
  '<span><b>Always load whole chats</b>' +
  '<span class=rs-sub>Load every chat from its first message, instead of the most recent part with the rest loading as you scroll; slower on long chats.</span>' +
  '</span></label>' +
  // TAB STRIP (T415, the user 2026-09-14): the strip's gear jumps the panel here (data-section is the anchor showSection scrolls
  // the card to), a small section of the strip's own settings ABOVE Tab widgets; the tab lock (T395; a row in the gear's menu
  // since T405) is its checkbox row, the house grammar of every other row, saved through the one gear save the strip hears
  "<div class='rs-sec' data-section=tabstrip>Tab strip</div>" +
  "<label class='rs-row'><input type=checkbox id=rs-tablock>" +
  '<span><b>Lock the tabs in place</b>' +
  '<span class=rs-sub>No drag or move of the tabs, and no lane drag in the Sessions pane, until unlocked. The order stays as it is.</span>' +
  '</span></label>' +
  // TAB WIDGETS, a section of the Chat tab (the user's amendment 2026-09-12: not a tab of its own), following the strip's section
  "<div class='rs-sec' data-section=tabwidgets>Tab widgets</div>" +
  // the widget rows are built by initGear from the registry (tab-widgets.ts): a live demo, the name and what it does, the
  // sliding switch and the widget's own options; every control built once and re-filled in place (click-safe)
  '<div class=rs-hint>What a tab title carries, in this order. Each row shows the widget live.</div>' +
  '<div id=rs-widgets class=rs-widgets></div>' +
  // THE RINGS (the rings-as-widgets change, 2026-09-14): the three dashed rings a tab can wear are widgets too, each with
  // its own switch, listed as their own group under the title's rows and their preview (the preview box lands between
  // the two hosts at build). No grip: their order is the precedence, red over yellow over amber, and is the registry's
  '<div class=rs-hint>Rings around the tab. One at a time: the first that applies wins, in this order.</div>' +
  '<div id=rs-rings class=rs-widgets></div>' +
  // STATUS LINE (T409, the user 2026-09-13): the items the line above the composer carries besides its fixed parts, one
  // row per registered widget (status-widgets.ts); the rows are the whole entry point (the user: no gear on the line,
  // no new menu row). The same builder as the Tab widgets rows; the demo is the widget alone, as the line draws it.
  "<div class='rs-sec' data-section=statusline>Status line</div>" +
  '<div class=rs-hint>What the line above the composer carries, in this order. The state chip, the mode, model and effort controls and the context battery are always there.</div>' +
  '<div id=rs-swidgets class=rs-widgets></div>' +
  '</div>' +
  '<div class=rs-pane data-pane=feed hidden>' +
  "<div class='rs-sec rs-sec-first'>Cards</div>" +
  '<label class=rs-row><input type=checkbox id=rs-feedcollapsed>' +
  '<span><b>Collapse cards by default</b>' +
  '<span class=rs-sub>Every card arrives collapsed to its one-line gist; expanding one is a per-card override. Moved here from the feed footer — a set-and-forget default, not a per-glance action.</span>' +
  '</span></label>' +
  '</div>' +
  '<div class=rs-pane data-pane=sessions hidden>' +
  "<div class='rs-sec rs-sec-first'>New sessions</div>" +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto'><b>Default directory</b>" +
  '<span class=rs-sub>The default directory for NEW sessions (still editable per session). Persisted kernel-side — also settable with <code>romp default-dir</code>. Falls back to the romp install dir until you set one; blank reverts to it. ~ and $VARs expand.</span>' +
  "<div style='display:flex;gap:6px;margin-top:5px'>" +
  "<input id=rs-defaultdir type=text spellcheck=false placeholder='install/serve default' style='flex:1 1 auto;min-width:0;box-sizing:border-box;background:var(--input-bg, #1e1e1e);color:var(--fg, #ccc);" +
  "border:1px solid #3a3a3a;border-radius:5px;padding:3px 6px'>" +
  "<button id=rs-defaultdir-browse type=button style='flex:0 0 auto;cursor:pointer;background:var(--btn-bg, #2a2a2a);color:var(--fg, #ccc);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:3px 8px'>Browse…</button>" +
  '</div></span></div>' +
  "<div class='rs-row rs-sep' style='cursor:default'><span style='flex:1 1 auto'><b>Default backend</b>" +
  '<span class=rs-sub>What the + button uses for a NEW session. Claude Code runs the session through romp itself; Codex runs an OpenAI Codex agent (docs/codex.md).</span>' +
  "<select id=rs-backend style='display:none'>" +
  '<option value=sdk>Claude Code</option><option value=codex>Codex</option>' +
  '</select></span></div>' +
  '</div>' +
  '<div class=rs-pane data-pane=automation hidden>' +
  // AUTOMATION (T404, the user 2026-09-13): what romp sends to the sessions on its own
  "<div class='rs-sec rs-sec-first'>Nudges</div>" +
  // the two rows carry a permanent one-sentence line (rs-line) in place of a hover tooltip (T408): the pane has two rows, so a
  // popup under either ran past the card's bottom, and the card, the modal's one scroll box, grew a scrollbar for it and clipped
  // it. A note about what changes while task tracking is off may follow the line (the master switch's), in that order.
  "<label class='rs-row rs-sep'><input type=checkbox id=rs-autonudge>" +
  '<span><b>Auto Nudge</b><span class=rs-mixed id=rs-autonudge-split hidden></span>' +
  '<span class=rs-line id=rs-autonudge-sub>' + AUTONUDGE_SUB + '</span>' +
  '<span class=rs-note id=rs-autonudge-tt hidden>While task tracking is off, the goal nudges wait: the judges no longer update the goals they are about. Only the reminders about unanswered messages from other sessions still go out.</span>' +
  '</span></label>' +
  "<label class='rs-row'><input type=checkbox id=rs-suggestcompact>" +
  '<span><b>Suggest /compact</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-line>When a session has sat idle for an hour with a lot of context built up, suggest one /compact at a natural point, once per fill-up, on every connected machine.</span>' +
  '<span class=rs-note id=rs-suggestcompact-tt hidden>Task tracking off changes nothing here: the suggestion reads the context size, not the judges.</span>' +
  '</span></label>' +
  // MODEL (the user 2026-09-17; under Automation by the maintainer's decision, 2026-09-17): two switches about which
  // model a session runs on and how. Both are kernel policies applied to every session on the kernel's own initiative,
  // like the Nudges above, which is why they sit in Automation and not in Chat. Kernel-side, like the judges' Fast mode
  // boxes: stored on/off, stamped, propagated to every linked kernel; the SDK backend reads them at connect, on a model
  // change and from its retry tick. Off by default. The rows take the tab's own shape (T408): a permanent one-sentence
  // line (rs-line) under the label in place of a hover popover, since a popup under the pane's last rows runs past the
  // card and scrolls it; the fuller account of each switch is docs/reference.md's.
  "<div class='rs-sec'>Model</div>" +
  "<label class='rs-row'><input type=checkbox id=rs-alwaysfast>" +
  '<span><b>Always fast</b><span class=rs-mixed hidden></span>' +
  "<span class=rs-line>Every Opus session runs Claude Code's fast mode, billed at a premium; a session you set to Slow stays slow.</span>" +
  '</span></label>' +
  "<label class='rs-row'><input type=checkbox id=rs-retryupgrade>" +
  '<span><b>Retry upgrades after downgrades</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-line>A session whose model fell back without a pick asks for its picked model again every ten minutes, once quiet, until it is back.</span>' +
  '</span></label>' +
  '</div>' +
  '<div class=rs-pane data-pane=tasks hidden>' +
  // TASK TRACKING (T404, the user 2026-09-13): the master switch first, then the judges alone; the nudges went to Automation,
  // Conserve memory to General, Thinking summaries to Chat. Off, the switch stands the whole system down (kernel.py
  // _task_tracking_on: no judge tier, no feed or outline, the shell hides their buttons) and every control that depends on it
  // here, in General's Panes and in Debug's Judging bands wears rs-off with the one tooltip (dressTracking).
  "<div class='rs-sec rs-sec-first'>Task tracking</div>" +
  "<label class='rs-row'><input type=checkbox id=rs-tasktrack checked>" +
  '<span><b>Task tracking</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>romp reads every session and keeps the feed and the outline current with its judges. Off, the judges do not run and spend nothing, the feed and the outline are not shown, and the controls that depend on them wait; the chat and the Sessions pane carry on. Follows to every connected machine.</span>' +
  '</span></label>' +
  '<div class=rs-sec>Judges</div>' +
  "<div class='rs-row rs-jrow'><b>Triage model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model the triage judges use — planner, grouper, closer, courier (the judgment-heavy tier). Applies on the judges' next pass; no restart. A pick here follows to every connected machine's kernel.</span><select id=rs-judgemodel></select>" +
  // Fast mode sits with the model (the user 2026-09-10, who wanted the setting to speak the chat's own words
  // and sit with the model): the chat's statusline badge and docs/reference.md call it fast mode, so this
  // does too. The box keeps its id and message (setJudgeFast, judgeFast): the kernel side is untouched.
  "<label class=rs-fastin id=rs-judgefast-wrap><input type=checkbox id=rs-judgefast>Fast mode<span class=rs-mixed hidden></span>" +
  "<span class=rs-sub id=rs-judgefast-sub>" + JUDGEFAST_SUB + "</span></label></div>" +
  "<div class='rs-row rs-jrow'><b>Triage effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the triage judges. Default = no effort flag (the judges' standard behavior). Not every model accepts every level. Follows to every connected machine's kernel.</span><select id=rs-judgeeffort></select></div>" +
  "<div class='rs-row rs-jrow'><b>Distilling model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model for the judges that write the prose you read on cards — distiller, briefer, staller. Follow triage (the default) keeps them on the triage pick; pinning a model here lets the copy you read run richer than the placement judges. Follows to every connected machine's kernel.</span><select id=rs-distillmodel></select>" +
  "<label class=rs-fastin id=rs-distillfast-wrap><input type=checkbox id=rs-distillfast>Fast mode<span class=rs-mixed hidden></span>" +
  "<span class=rs-sub id=rs-distillfast-sub>" + JUDGEFAST_SUB + "</span></label></div>" +
  "<div class='rs-row rs-jrow'><b>Distilling effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the distilling judges. Follow triage (the default) rides the triage effort; Default pins no effort flag. Follows to every connected machine's kernel.</span><select id=rs-distilleffort></select></div>" +
  "<div class='rs-row rs-jrow'><b>Indexing model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model the indexing judges use — captioner + archiver (high-volume, low-stakes summarization). Haiku by default for cost. Follows to every connected machine's kernel.</span><select id=rs-indexmodel></select>" +
  "<label class=rs-fastin id=rs-indexfast-wrap><input type=checkbox id=rs-indexfast>Fast mode<span class=rs-mixed hidden></span>" +
  "<span class=rs-sub id=rs-indexfast-sub>" + JUDGEFAST_SUB + "</span></label></div>" +
  "<div class='rs-row rs-jrow'><b>Indexing effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the indexing judges. Default keeps this high-volume work cheap: effort low on models with adaptive thinking (Fable, Opus 4.6 and later, Sonnet 4.6 and later); Haiku, Sonnet 4.5 and Opus 4.5 have none, so they run with thinking off and no flag. Follows to every connected machine's kernel.</span><select id=rs-indexeffort></select></div>" +
  "<div class='rs-row rs-jrow'><b>Judge concurrency <span class=rs-mixed hidden></span></b><span class=rs-sub>How many judge calls run at once, across every tier. Default is 6, or the ROMP_JUDGE_CONCURRENCY the kernel's service environment sets. Applies on the judges' next pass; no restart. Follows to every connected machine's kernel.</span><select id=rs-judgeconc></select></div>" +
  '</div>' +
  '<div class=rs-pane data-pane=debug hidden>' +
  // DEBUG (T400, the user 2026-09-12): updates, the judges' debug views, the token usage analytics, the log and the version; the
  // former System tab dissolved here, its account rows to General; Updates went to General's This machine section (T404)
  "<div class='rs-sec rs-sec-first'>Judging bands</div>" +   // the two debug views of the judges' activity, from the Feed tab (T400)
  '<div class=rs-judges>' +
  '<label class=rs-row rs-half><input type=checkbox id=rs-judges-index>' +
  '<span><b>Show indexing judges</b>' +
  "<span class=rs-sub>Debug view: draws the captioner + archiver on the timeline's judging band. It does NOT turn the judges on or off — they always run; this only shows their activity.</span>" +
  '</span></label>' +
  '<label class=rs-row rs-half><input type=checkbox id=rs-judges-triage>' +
  '<span><b>Show triage judges</b>' +
  "<span class=rs-sub>Debug view: draws the planner, grouper, closer, distiller + courier on the timeline's judging band. It does NOT turn the judges on or off — they always run; this only shows their activity.</span>" +
  '</span></label>' +
  '</div>' +
  '<div class=rs-sec>Diagnostics</div>' +
  "<div class=rs-sep style='padding-top:8px'>" +
  '<button id=ra-open class=ra-openbtn>Token usage analytics</button>' +
  '<button id=rs-log-open class=ra-openbtn hidden>Open log<span class=rs-log-n hidden></span></button></div>' +   // T290: the Log moved here from the bottom bar (web shell only); the span is the unread count
  "<div class='rs-h rs-sep'>romp · version</div>" +
  '<div id=rsver>…</div></div></div></div>' +
  '<div id=rs-login-modal hidden>' +
  '<div class=rs-login-card>' +
  "<div class=rs-h style='margin-bottom:4px'>Log in to Claude Code</div>" +
  "<div id=rs-login-url hidden style='margin-top:6px'></div>" +
  "<div id=rs-login-code hidden style='margin-top:8px;display:flex;gap:6px;align-items:center'>" +
  "<input id=rs-login-input type=text autocomplete=off spellcheck=false placeholder='paste the code from the browser…' style='flex:1;background:var(--input-bg, #1e1e1e);color:var(--fg, #ccc);border:1px solid #3a3a3a;border-radius:5px;padding:4px 8px'>" +
  "<button id=rs-login-send type=button style='cursor:pointer;background:var(--btn-bg, #2a2a2a);color:var(--fg, #ccc);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:3px 10px'>Submit</button>" +
  '</div>' +
  "<div style='margin-top:10px;text-align:right'>" +
  "<button id=rs-login-cancel type=button style='cursor:pointer;background:transparent;color:#888;border:1px solid #3a3a3a;border-radius:5px;padding:3px 10px'>Cancel</button>" +
  '</div></div></div>' +
  '<div id=ranalytics-back hidden><div id=ranalytics>' +
  '<div class=ra-top><div class=ra-title>Token usage</div>' +
  '<button id=ra-close aria-label=Close>✕</button></div>' +
  '<div class=ra-periods>' +
  '<button data-w=3600>1h</button><button data-w=21600>6h</button>' +
  '<button data-w=86400 class=on>24h</button><button data-w=604800>7d</button>' +
  '<button data-w=2592000>30d</button></div>' +
  '<div class=ra-group>' +
  '<button data-g=judge class=on>By judge</button>' +
  '<button data-g=tier>Index vs triage</button></div>' +
  '<div class=ra-metric>' +
  '<button data-m=tokens class=on>Tokens</button>' +
  '<button data-m=cost>Cost ($)</button></div>' +
  '<div id=ra-chart class=ra-chart></div>' +
  '<div id=ra-legend class=ra-legend></div>' +
  '<div id=ra-note class=ra-note></div>' +
  '</div></div>';

// Wire the whole gear into the current document. `post` is the feed bundle's
// kernel channel (webview postMessage → host pipe → kernel WS, or the browser
// shim's WS directly). Idempotent: a second init is a no-op.
// opts.ownPage: this document IS the gear (the kernel's /settings page, settings-page.ts) — nothing sits
// under the modal, so the lift never pins the body to a pane rect (setModalCls below) and the page stays
// transparent under the dim. Absent for the feed hosts (VS Code's feed panel), whose content keeps painting.
function initGear(post, opts) {
  if (document.getElementById('rsettings')) return;
  var ownPage = !!(opts && opts.ownPage);
  document.body.insertAdjacentHTML('beforeend', GEAR_HTML);

  var g = document.getElementById('rgear'), p = document.getElementById('rsettings'),
    b = document.getElementById('rsver'), cc = document.getElementById('rs-compact'), tl = document.getElementById('rs-tablock'),
    jix = document.getElementById('rs-judges-index'), jtr = document.getElementById('rs-judges-triage'),
    an = document.getElementById('rs-autonudge'), bk = document.getElementById('rs-backend'),
    cvm = document.getElementById('rs-conserve'),
    csg = document.getElementById('rs-suggestcompact'),
    dd = document.getElementById('rs-defaultdir'),
    fsc = document.getElementById('rs-filesctl'),
    sr = document.getElementById('rs-striprows'),
    dn = document.getElementById('rs-dense'),
    cs = document.getElementById('rs-chatscheme'),
    tt = document.getElementById('rs-theme'),
    fc = document.getElementById('rs-feedcollapsed'),
    jm = document.getElementById('rs-judgemodel'),
    im = document.getElementById('rs-indexmodel'), je = document.getElementById('rs-judgeeffort'),
    ie = document.getElementById('rs-indexeffort'), upm = document.getElementById('rs-updates'),
    jc = document.getElementById('rs-judgeconc'),
    dm = document.getElementById('rs-distillmodel'), de = document.getElementById('rs-distilleffort'),
    cmm = document.getElementById('rs-cmtmodel'), cme = document.getElementById('rs-cmteffort'),
    cmf = document.getElementById('rs-cmtfast'),
    jf = document.getElementById('rs-judgefast'), df = document.getElementById('rs-distillfast'), xf = document.getElementById('rs-indexfast'),   // T300: one per tier
    fe = document.getElementById('rs-fileedit'),
    pn = { timeline: document.getElementById('rs-pane-timeline'), fleet: document.getElementById('rs-pane-fleet'), feed: document.getElementById('rs-pane-feed') },
    ths = document.getElementById('rs-thinksum'),
    wcf = document.getElementById('rs-wholechat'),
    afb = document.getElementById('rs-alwaysfast'), rub = document.getElementById('rs-retryupgrade'),   // the Automation pane's model switches (2026-09-17)
    tk = document.getElementById('rs-tasktrack'),
    ans = document.getElementById('rs-autonudge-split'), asub = document.getElementById('rs-autonudge-sub');
  // No default for tabWidgets (round one, HIGH): an injected empty object won over a pre-widgets store's tabCtx, so the
  // Context bar read as on at 50 percent whatever the user had chosen, and a save of ANY setting wrote the empty prefs and
  // rewrote the mirror. A store with no tabWidgets derives the prefs from tabCtx at read time (widgetPrefs, the same
  // derivation settings.ts makes), and only a widget change writes the key (saveWidgets).
  function load() { try { var o = Object.assign({ compact: true, colormap: 'aurora', subgoals: true, debug: false, backend: 'sdk', defaultDir: '', tabCtx: 'over50', showFilesControl: false, stripGroupRows: true, denseChrome: false, collapseGaps: true, activeOnly: true }, JSON.parse(localStorage.getItem('romp:settings') || 'null')); delete o.filesControl; delete o.fileLinkPane; return o; } catch (e) { return { compact: true, colormap: 'aurora', subgoals: true, debug: false, backend: 'sdk', defaultDir: '', tabCtx: 'over50', showFilesControl: false, stripGroupRows: true, denseChrome: false, collapseGaps: true, activeOnly: true }; } }
  // mirrors settings.ts tabCtxMode (this file can't import the TS module): the gauge shipped for a
  // few hours as a boolean toggle — false was an explicit hide, true the default nobody chose.
  function tabCtxMode(v) { return (v === 'always' || v === 'never') ? v : (v === false ? 'never' : 'over50'); }
  // save() ALWAYS dispatches the same-doc 'romp:settings' signal: consumers in
  // THIS document (the feed's card gates, and the chat transcript now that it
  // hosts its own gear) never get a 'storage' event for a same-document write —
  // the compact toggle sat dead in the VS Code chat because its handler was the
  // one save that forgot to emit (the user 2026-07-14). It also posts the save
  // to the host: VS Code webviews each own a SEPARATE localStorage, so the host
  // fans {settingsSync} out to the other panes (the browser ignores it — its
  // same-origin tabs already sync via the storage event).
  function save(s) {
    // both widget keys normalized on EVERY gear save, when the store carries them (never injected: the fresh-key rule), as
    // settings.ts's saveSettings does, so a malformed order in either section is rewritten clean by any save (review round
    // two, low 7); the mirrors follow the normalized prefs
    if ('tabWidgets' in s) { s.tabWidgets = TW.tabWidgetPrefs(s.tabWidgets, s.tabCtx); s.tabCtx = TW.tabCtxOfPrefs(s.tabWidgets); }
    if ('statusWidgets' in s) { s.statusWidgets = SW.statusWidgetPrefs(s.statusWidgets); var m2 = SW.legacyOfStatusPrefs(s.statusWidgets); s.showBranch = m2.showBranch; s.showSessionBadge = m2.showSessionBadge; }
    try { localStorage.setItem('romp:settings', JSON.stringify(s)); } catch (e) {}
    try { window.dispatchEvent(new Event('romp:settings')); } catch (e) {}
    post({ type: 'settingsSync', settings: s });
  }
  cc.addEventListener('change', function () { var s = load(); s.compact = cc.checked; save(s); });
  tl.addEventListener('change', function () { var s = load(); s.tabsLocked = tl.checked; save(s); });   // the tab lock (T415): the strip hears the save (tabsLocked is in its signature)
  // one tag group per row in the tab strip (on by default); render.ts repaints the strip on the save
  if (sr) sr.addEventListener('change', function () { var s = load(); s.stripGroupRows = sr.checked; save(s); });
  // compact tabs and agents (off by default): render.ts applies a body class on the save, and the strip and the panel repaint through the cascade
  if (dn) dn.addEventListener('change', function () { var s = load(); s.denseChrome = dn.checked; save(s); });
  if (fsc) fsc.addEventListener('change', function () { var s = load(); s.showFilesControl = fsc.checked; save(s); });   // the shell hears the store change (its storage listener) and hides or shows the control (T317)
  // the optional panes: the whole set is rewritten from the three boxes on every change (a missing key reads
  // as shown everywhere, settings.ts paneSet), and the shell hears the save as a storage event
  function panesOf(s) { var p = (s && s.panes && typeof s.panes === 'object') ? s.panes : {}; return { timeline: p.timeline !== false, fleet: p.fleet !== false, feed: p.feed !== false }; }
  Object.keys(pn).forEach(function (k) { if (pn[k]) pn[k].addEventListener('change', function () { var s = load(); var p = panesOf(s); p[k] = pn[k].checked; s.panes = p; save(s); }); });
  // the section is the dashboard's: VS Code's panels have no dashboard shell to hide a pane from
  if (!ownPage) Array.prototype.forEach.call(document.querySelectorAll('#rs-panes-sec,.rs-panes-row'), function (el) { el.hidden = true; });
  // ── the settings' value-picker DROPDOWNS (T117, the user 2026-08-27, screenshot: the Chat
  // tabs and Text scheme pickers rendered every option always-expanded, and the description spans
  // ran off the card's right edge). Progressive disclosure: the CLOSED state is ONE row — the
  // current option's name + its description/preview, ellipsized so it can never overrun — and the
  // options are one click away in the house menu vocabulary (the chat .ctx-menu spec; versionMenu
  // below inlines the same values through the menu TOKENS — var(--menu-bg/--menu-fg/--menu-border/
  // --menu-hover, --radius-menu, --shadow-menu, --check-bg) with the dark literals as fallbacks
  // (T226: the literals alone left every picker a dark card in the light theme). NOT a native
  // <select> like the Context-gauge row above: these options carry rich row content — the scheme
  // rows preview their own colored tiers (the user 2026-08-24: "I need to see a preview") — which
  // <option> cannot render. The open menu is position:absolute inside the row's wrapper (the
  // #rs-cmap/#rs-pal mechanic, proven inside this scrolling card): left:0;right:0 pins its width
  // to the card's content width, so it can never overflow the panel sideways, and rows clamp
  // their text with the same ellipsis as the closed state; opening scrolls it into view so the
  // card's bottom edge never clips it either. Dismissal is event-based: any outside click in this
  // document, Escape, a pick, opening the sibling dropdown — plus the cross-pane menu echo
  // (romp:menu-echo): sibling panes' clicks never reach this document, so tag-menu.ts writes a
  // pointerdown echo in every webview bundle (feed.ts and render.ts both load it) and this menu,
  // like versionMenu, wires the LISTENER half via the storage event. Persistence and live-apply
  // are the caller's pick() verbatim — the dropdown changes when the options are visible, never
  // what a pick does.
  var openHousePick = null;   // at most one of the card's dropdowns is open (a click that opens one closes the other)
  var widgetDrag = false;   // a widget row in flight (the reorder, T409): the drag takes the Escape itself, so the shell's Escape-to-close stands down while it is on
  var dragAborts = [];      // the teardown of EVERY drag in flight, one per pointer (a second finger on a second grip is its own drag), added at the press and removed at the end:
                            // whatever hides the card (closeSettings, the Token usage opener) ends them all first (rows restored, listeners gone, nothing armed), because a release
                            // under a hidden card never reaches this document and the listeners would survive the reopen (part two's third read; the migration read widened the
                            // single slot, which tore down only the last drag pressed)
  function endDrags() { dragAborts.slice().forEach(function (f) { f(); }); }
  function housePick(wrap, attr, rowHTML, pick) {
    if (!wrap) return null;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.style.cssText = 'display:flex;align-items:center;gap:8px;width:100%;min-width:0;text-align:left;' +
      'background:var(--input-bg, #1e1e1e);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:5px 8px;cursor:pointer;font:inherit;color:var(--fg, #ccc)';
    var menu = document.createElement('div');
    menu.hidden = true;
    menu.style.cssText = 'position:absolute;left:0;right:0;top:100%;margin-top:4px;z-index:30;padding:4px;' +
      'background:var(--menu-bg, #252526);border:1px solid var(--menu-border, rgba(255,255,255,0.12));border-radius:var(--radius-menu, 6px);box-shadow:var(--shadow-menu, 0 4px 12px rgba(0,0,0,0.35));' +
      'font-size:12px;line-height:1.4;color:var(--menu-fg, #cccccc);user-select:none';
    wrap.appendChild(btn); wrap.appendChild(menu);
    var close = function () { menu.hidden = true; if (openHousePick === menu) openHousePick = null; };
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (!menu.hidden) { close(); return; }
      if (openHousePick && openHousePick !== menu) openHousePick.hidden = true;
      menu.hidden = false; openHousePick = menu;
      if (menu.scrollIntoView) menu.scrollIntoView({ block: 'nearest' });   // the card scrolls to reveal it — never clipped at the bottom
    });
    document.addEventListener('click', close);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    try { window.addEventListener('storage', function (e) { if (e.key === 'romp:menu-echo' && e.newValue) close(); }); } catch (e) {}
    // paint(options, curId): the closed row shows the CURRENT option, the menu one row per option
    return function (options, curId) {
      // options can be EMPTY: the swept effort selects start blank until /models resolves
      // (fillChoices) — paint a quiet placeholder row instead of dying on options[0].name,
      // which would abort the whole initGear before anything else wired (caught in the
      // Playwright harness pre-merge; kernel tests also pin this file clear of retired option
      // words, so keep comments plain here).
      var cur = null;
      options.forEach(function (o) { if (o.id === curId) cur = o; });
      if (!cur) cur = options.length ? options[0] : null;
      btn.innerHTML = (cur ? rowHTML(cur) : '<span style="flex:1 1 auto;min-width:0;color:#8a8a8a">\u2026</span>') +
        '<span style="flex:0 0 auto;margin-left:auto;opacity:0.55">\u25BE</span>';
      menu.innerHTML = '';
      options.forEach(function (o) {
        var row = document.createElement('div');
        row.setAttribute('data-' + attr, o.id);
        row.style.cssText = 'display:flex;align-items:center;gap:8px;min-width:0;position:relative;' +
          'padding:4px 22px 4px 8px;border-radius:4px;cursor:pointer';
        row.innerHTML = rowHTML(o);
        if (cur && o.id === cur.id) {
          var ck = document.createElement('span'); ck.textContent = '\u2713';
          ck.setAttribute('style', 'position:absolute;right:6px;top:50%;transform:translateY(-50%);background:var(--check-bg, #1EA1EB);color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;');
          row.appendChild(ck);
        }
        row.addEventListener('mouseenter', function () { row.style.background = 'var(--menu-hover, rgba(255,255,255,0.09))'; });
        row.addEventListener('mouseleave', function () { row.style.background = 'transparent'; });
        row.addEventListener('click', function (e) { e.stopPropagation(); close(); pick(o.id); });
        menu.appendChild(row);
      });
    };
  }
  // The scheme options preview their own tiers (the user 2026-08-24, on the live check: "I need
  // to see a preview"). Tier hexes MIRROR styles.css body.scheme-* (this file can't read the
  // sheet across webviews); chat-scheme.test.ts pins the two byte-equal so they cannot drift.
  // Default previews the stock tiers. Each row paints ITS OWN tiers on the chat's dark ground.
  var SCHEMES = [
    { id: 'default', name: 'Default', fg: '#cccccc', dim: '#9a9a9a', code: '#e1c08d' },
    { id: 'high-contrast', name: 'High contrast', fg: '#e8e8e8', dim: '#b8b8b8', code: '#ecd9ae' },
    { id: 'solarized-dark', name: 'Solarized Dark', fg: '#eee8d5', dim: '#93a1a1', code: '#d5b02d' }
  ];
  function schemeRowHTML(sc) {
    return '<span style="flex:0 0 auto;min-width:96px;color:var(--fg, #ccc)">' + sc.name + '</span>' +
      '<span style="flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
      '<span style="color:' + sc.fg + '">Prose text</span> \u00b7 ' +
      '<span style="color:' + sc.dim + '">tool / meta</span> \u00b7 ' +
      '<span style="color:' + sc.code + ';font-family:monospace">code()</span></span>';
  }
  var csDrop = housePick(cs, 'scheme', schemeRowHTML, function (id) { var s = load(); s.chatScheme = id; save(s); csPaint(); });
  function csPaint() {
    if (!csDrop) return;
    var cur = load().chatScheme; cur = (cur === 'high-contrast' || cur === 'solarized-dark') ? cur : 'default';
    csDrop(SCHEMES, cur);
  }
  var THEMES = [
    { id: 'classic', name: 'Classic', sub: 'dark \u00b7 the original high-contrast strip' },
    { id: 'yatharth', name: 'Yatharth', sub: 'dark \u00b7 flat session wash \u00b7 tinted line under the strip' },
    { id: 'yatharth-light', name: 'Yatharth light', sub: 'warm light \u00b7 cream page, clay accents \u00b7 the yatharth strip' }
  ];
  function themeRowHTML(th) {
    return '<span style="flex:0 0 auto;min-width:96px;color:var(--fg, #ccc)">' + th.name + '</span>' +
      '<span style="flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.82em;color:var(--fg, #cccccc);opacity:0.6">' + th.sub + '</span>';
  }
  function themeOf(s) {   // migration mirror of settings.ts loadSettings: pre-`theme` stores read their strip pick
    if (s.theme === 'yatharth' || s.theme === 'yatharth-light' || s.theme === 'classic') return s.theme;
    return s.chatTabTheme === 'yatharth' ? 'yatharth' : 'classic';
  }
  var ttDrop = housePick(tt, 'theme', themeRowHTML, function (id) {
    var s = load(); s.theme = id;
    s.chatTabTheme = (id === 'classic' ? 'classic' : 'yatharth');   // the derived LEGACY alias, kept in step for older readers
    save(s); ttPaint();
  });
  function ttPaint() {
    if (!ttDrop) return;
    ttDrop(THEMES, themeOf(load()));
  }
  ttPaint();
  // (the Context gauge picker moved onto the Context bar widget's row in the Chat tab's Tab widgets section, T379: tcPaint below is its stand-in)
  function tcPaint() {}
  // ── THE TABS (T379, re-cut T400) ── seven pills, one pane each; selectTab shows one pane and remembers it per browser (an older
  // remembered name maps through TAB_ALIASES)
  var TAB_KEY = 'romp:settingsTab';
  function knownTab(t) { t = TAB_ALIASES[t] || t; return RS_TABS.some(function (x) { return x[0] === t; }) ? t : null; }
  function selectTab(t) {
    t = knownTab(t) || knownTab((function () { try { return localStorage.getItem(TAB_KEY); } catch (e) { return null; } })()) || 'chat';
    Array.prototype.forEach.call(document.querySelectorAll('#rsettings .rs-tab'), function (b) { var on = b.getAttribute('data-tab') === t; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
    Array.prototype.forEach.call(document.querySelectorAll('#rsettings .rs-pane'), function (pn) { pn.hidden = pn.getAttribute('data-pane') !== t; });
    try { localStorage.setItem(TAB_KEY, t); } catch (e) {}
    return t;
  }
  Array.prototype.forEach.call(document.querySelectorAll('#rsettings .rs-tab'), function (b) { b.addEventListener('click', function (e) { e.stopPropagation(); selectTab(b.getAttribute('data-tab')); clearSectionScroll(); }); });   // a pill change starts its pane at the top with no section room left behind (the follow-up's round one, LOW 1)
  // a SECTION of the tab (the user 2026-09-12): an ask may name a section of the tab it opens (data-section on the section's
  // head; the strip's tab-widgets gear asks for chat / tabwidgets), and the card scrolls so that head sits at its top, under
  // the padding. Looked up in the SHOWN pane only, after the panel is displayed (rects exist only then). Set on the card,
  // the modal's one scroll box, never scrollIntoView, which would scroll the host document too.
  // the one pending section ask (round two, LOW 2): an observer registered for an unlaid-out ask is disconnected on close and
  // before a new ask, so a later open never fires a stale scroll; a plain open (no section) resets the card (LOW 7)
  var sectionRO = null, sectionAsk = null, sectionWrote = false;
  // THE ASK'S OWN WRITES, marked (round three, LOW 1): a scrollTop write that moves the card owes exactly one scroll event, its
  // echo, which the scroll handler below consumes and never reads as the user's, even when the user's press on a row fell within
  // the input window before it (the ask died 0 and 60 ms after a press, measured by the review). A write that did not move owes
  // none, and must not eat a later gesture. The chat's writeScroll pairs a write with its echo the same way (lastScrollWriteAfter).
  // A move is what the FRAME renders (round four, MEDIUM 1): the value the frame started from (from, read by the caller before any
  // layout it changed) against the value after the write. go() drops the room to measure, which clamps the card from 594 to 0, and
  // lands it back at 594: Chromium renders no net move and fires no scroll event, so a mark read off the clamped intermediate was a
  // debt the user's first real scroll paid (a wheel tick moved the card and the ask stood, measured by the review). And a debt no
  // event pays (two writes in one frame netting to its start) is forgiven two frames on, before it could eat the user's own scroll:
  // the echo comes with the next frame's scroll events or never.
  function writeCard(card, top, from) {
    var before = from === undefined ? card.scrollTop : from;
    card.scrollTop = top;
    if (card.scrollTop === before) return;
    sectionWrote = true;
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(function () { requestAnimationFrame(function () { sectionWrote = false; }); });
  }
  function clearSectionScroll() {
    if (sectionRO) { sectionRO.disconnect(); sectionRO = null; }
    sectionAsk = null;
    var card = document.querySelector('#rsettings .rs-card');
    if (card) { writeCard(card, 0); card.removeAttribute('data-section-landed'); }
    Array.prototype.forEach.call(document.querySelectorAll('#rsettings .rs-pane'), function (pn) { pn.style.paddingBottom = ''; });
  }
  function showSection(section) {
    if (sectionRO) { sectionRO.disconnect(); sectionRO = null; }
    sectionAsk = null;
    var card0 = document.querySelector('#rsettings .rs-card');
    if (card0) card0.removeAttribute('data-section-landed');   // a new ask's landing is its own: the earlier mark goes with the earlier ask
    if (typeof section !== 'string' || !section) return;
    var sec = document.querySelector('#rsettings .rs-pane:not([hidden]) .rs-sec[data-section="' + section + '"]');
    var card = document.querySelector('#rsettings .rs-card');
    var pane = sec && sec.closest('.rs-pane');
    if (!sec || !card || !pane) return;
    var go = function () {
      var top0 = card.scrollTop;   // where the frame started: the room drop below clamps the card, and the landing is measured against this, not the clamp
      pane.style.paddingBottom = '';   // measure the pane's own end: a re-ask on an already roomed pane must not read its earlier room
      var cs = getComputedStyle(card), padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
      // room at the pane's END so the head can reach the top even when the section is the last thing in the pane (round two,
      // LOW 1: the scroll used to stop at the card's end with the head far below the padding): the pane grows by what is
      // missing below the section, cleared on close, a plain open or a pill change. The room is sized to the card's CAP
      // (max-height, 88vh, a content box), not its current height: below the cap the card is content-driven and grows
      // under any room added, so the head stopped short on tall windows (152px off at 1200px, measured by the review);
      // sized to the cap, the card lands exactly there in one pass, whatever the window. A second measurement after the
      // write takes up rounding, or a cap the computed style did not resolve to pixels.
      var below = function () { return pane.getBoundingClientRect().bottom - sec.getBoundingClientRect().top; };
      var land = function () {
        writeCard(card, card.scrollTop + sec.getBoundingClientRect().top - card.getBoundingClientRect().top - padT, top0);
        card.setAttribute('data-section-landed', section);   // the landing's mark: what a lab waits for before it measures (never a delay)
      };
      var capH = parseFloat(cs.maxHeight);
      if (!isFinite(capH) || capH <= 0) { pane.style.paddingBottom = ''; land(); return; }   // no cap (not this sheet's case: the card is capped at 88vh): the card fits its content, nothing to room for
      // the cap is a BORDER box on the served page (feed.css: every element is border-box), so the content the room fills is the
      // cap less the paddings and borders; under a content box (no such rule) the cap is the content itself
      var edges = cs.boxSizing === 'border-box' ? padT + padB + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0) : 0;
      var content = capH - edges;
      var missing = content - below();
      pane.style.paddingBottom = missing > 0 ? Math.ceil(missing) + 'px' : '';
      var short = (card.clientHeight - padT - padB) - below();   // measured ONCE after the write: rounding
      if (short > 0) pane.style.paddingBottom = Math.ceil(Math.max(missing, 0) + short) + 'px';
      land();
    };
    var ask = {};
    sectionAsk = ask;
    if (card.clientHeight > 0) go();   // laid out already: an open panel, or a host that never hides this document
    // The ask STANDS until the user's own input scrolls the card, a pill changes the pane, or the panel closes: the scroll
    // re-lands the head whenever the pane's or the card's size changes, whatever scroll events came before (a browser's scroll
    // anchoring nudge, the clamp a taller window applies: neither is the user's). Two reasons, both events, never timers: in the shell this document
    // sits in an iframe that is display:none until the shell hears the settings-open message feedFull just posted, and
    // a scroll set on a box with no size clamps to zero (the served lab measured 0 on the first open), so the card
    // gaining a size is the first landing; and a layout that settles AFTER that first size (a web font arriving, a list
    // filling) moves everything above the section, so the head slid off the top on a slow runner (CI, 2026-09-13: neither
    // at the top nor at the end). Only a size change re-lands it, so a picker opening over the pane moves nothing.
    if (typeof ResizeObserver !== 'function') return;
    sectionRO = new ResizeObserver(function () { if (sectionAsk === ask && card.clientHeight > 0) go(); });
    sectionRO.observe(card);
    sectionRO.observe(pane);
  }
  // The USER's scroll ends a standing section ask; the browser's never does. A scroll event is the user's only with INPUT
  // evidence: a wheel, a key or a touch within the settle rule's window before it, or a pointer holding the scroller's gutter
  // (a thumb drag), the chat's own rule (landing-settle.ts gestureEvidence). A scroll delta is no evidence: Chrome's scroll
  // anchoring moved the card 2px after a late layout settle, and a taller window's clamp moved it 143px, and both were read as
  // the user's, killing the ask (the follow-up's review, 2026-09-13).
  // The inputs are read on the WINDOW with capture, the way the chat reads them (render.ts settleInput), never on the card
  // (round three, MEDIUM): the card has no tabindex, so a key scroll after a click in it targets BODY and never reached a
  // listener on the card; the ask stood and the next size change threw the user's scroll away. Scoped to the card: a wheel, a
  // touch or a press by its target's containment; a key by the card being the scroll focus (the user's last press fell in it,
  // or the focus sits in it), and never while a field has the focus (typing scrolls the field, not the card).
  (function () {
    var card = document.querySelector('#rsettings .rs-card');
    if (!card) return;
    var inputAt = 0, held = false, pressedIn = false;
    var inCard = function (e) { return e.target instanceof Node && card.contains(e.target); };
    // a FIELD is an element that consumes the scroll keys (Page, Home, End, the arrows): a text-like input, a textarea, a select, a
    // contenteditable. A checkbox, radio, button or range input is none (round four, MEDIUM 2: PageUp after a click on a checkbox row
    // scrolled the card with no evidence counted, and the next size change re-landed over the user's scroll)
    var NOT_FIELDS = { checkbox: 1, radio: 1, button: 1, submit: 1, reset: 1, range: 1, color: 1, file: 1, image: 1 };
    var inField = function () { var a = document.activeElement; if (!a) return false;
      if (a.tagName === 'INPUT') return !NOT_FIELDS[String(a.getAttribute('type') || 'text').toLowerCase()];
      return a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || !!a.isContentEditable; };
    var keyFocus = function () { var a = document.activeElement; return pressedIn || (!!a && a !== document.body && card.contains(a)); };
    var onInput = function (e) {
      if (e.type === 'keydown') { if (inField() || !keyFocus()) return; }
      else if (e.type === 'pointerdown') {
        pressedIn = inCard(e);
        if (!pressedIn) return;
        // the hold latches on the scroller's GUTTER only (round three, LOW 2): a press whose target is the card's own padding is a
        // press like any other, with the timed window, not a grab that outlives it
        var r = card.getBoundingClientRect();
        // offsets from the PADDING box (the border box less clientLeft and clientTop), the box clientWidth and clientHeight measure
        // (round four, LOW 2: measured from the border box, the innermost content pixel column read as the gutter)
        if (LS.scrollerGrab(false, e.clientX - r.left - card.clientLeft, e.clientY - r.top - card.clientTop, card.clientWidth, card.clientHeight)) held = true;
      }
      else if (!inCard(e)) return;
      inputAt = performance.now();
    };
    ['wheel', 'keydown', 'touchstart', 'pointerdown'].forEach(function (k) { window.addEventListener(k, onInput, { capture: true, passive: true }); });
    window.addEventListener('pointerup', function () { held = false; });
    window.addEventListener('pointercancel', function () { held = false; });
    // a page put behind another mid-press gets neither pointerup nor pointercancel from Chromium (round three, LOW 2; the chat's
    // round four): the hold ends with the page's focus or visibility as well, or a lost release would make the next scroll of
    // any origin end the ask
    window.addEventListener('blur', function () { held = false; });
    document.addEventListener('visibilitychange', function () { held = false; });
    card.addEventListener('scroll', function () {
      if (sectionWrote) { sectionWrote = false; return; }   // the ask's own write echoing (one event per moving write): never the user's
      if (!sectionAsk || !LS.gestureEvidence(inputAt, performance.now(), held)) return;
      if (sectionRO) { sectionRO.disconnect(); sectionRO = null; }
      sectionAsk = null;
      card.removeAttribute('data-section-landed');
    });
  })();

  // ── THE WIDGET ROWS (T379; a second section for the status line since T409) ── one per registered widget: the live
  // demo (the tab widgets: a miniature tab with the widget's node where the strip would put it; the status line's: the
  // widget alone, as the line draws it), the name and what it does, the sliding switch, and the widget's own options as
  // house pickers. Built once; every paint re-fills in place (click-safe). A change writes the registry's prefs and its
  // mirror(s) through save(), and the chat repaints on the romp:settings signal. ONE builder serves both sections.
  function widgetOptRowHTML(o) { return '<span style="flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--menu-fg, #ccc)">' + o.name + '</span>'; }
  function widgetSection(cfg) {   // cfg: host, list(), order(prefs) -> ids in visual order (a divider's id among them), divider {id, label} or null,
                                  //      group(id) -> a key rows may not leave (null: none), prefs(store), save(prefs), on(prefs, w), opts(prefs, w),
                                  //      demo(w, prefs) -> node or null, preview(prefs) -> node, pickPrefix,
                                  //      reorder: false -> no grip and no drag (the rings, 2026-09-14: the order is the precedence and the registry's)
    var rows = {}, dividerRow = null, previewBody = null;
    // REORDER (the user's addition to T409): the rows drag by their grip (pointer events on the document for the drag's life,
    // one drag per pointer, each ended when the card hides; Escape cancels) and move by the arrow keys on the focused grip; the order is the render order on
    // the surface and is stored WHOLE (the divider's id included, so every tab widget's side of the name is explicit from
    // the first drag on). The same moveId rule serves the drag's drop and the key. A section whose rows have GROUPS (the
    // status line: its two slots) holds a row inside its group, with a nudge for a move that would leave it.
    function rowOf(id) { return id === (cfg.divider && cfg.divider.id) ? dividerRow : (rows[id] && rows[id].row); }
    function idOf(r) { return r.getAttribute('data-widget') || r.getAttribute('data-divider'); }
    function currentList() { return Array.from(cfg.host.children).map(idOf).filter(Boolean); }
    function labelOf(id) { var w = cfg.list().filter(function (x) { return x.id === id; })[0]; return w ? w.label : id; }
    function commit(list, movedId) { var prefs = cfg.prefs(load()); prefs.order = list; cfg.save(prefs); if (movedId) announce(list, movedId); }
    // only a row OUT OF PLACE moves (review round two, the regression): appendChild re-inserts a node that sits where it belongs, and
    // re-inserting the ancestor of document.activeElement blurs it, so a switch toggled by Space lost its focus at the paint
    function placeRows(list) {
      var prev = null;
      list.forEach(function (id) {
        var r = rowOf(id); if (!r) return;
        var want = prev ? prev.nextSibling : cfg.host.firstChild;
        if (want !== r) cfg.host.insertBefore(r, want);
        prev = r;
      });
    }
    // a move that would leave the row's group is refused with a brief cue (the status line's two slots stay the registry's)
    function nudge(row) { row.classList.remove('rs-nudge'); void row.offsetWidth; row.classList.add('rs-nudge'); setTimeout(function () { row.classList.remove('rs-nudge'); }, 350); }
    // the groups' runs, consecutive repeats collapsed: [left, right, right] reads "left,right"; a row carried across a
    // boundary opens a second run of its group, or swaps the runs, and either differs from the list it started from
    // (a group of one row is always contiguous, so the moved row's own group alone proved nothing: the lab walked the
    // session name into the right slot)
    function runs(list) { var out = []; list.forEach(function (x) { var g = cfg.group(x); if (!out.length || out[out.length - 1] !== g) out.push(g); }); return out.join(); }
    function keepsGroup(list, base) { return !cfg.group || runs(list) === runs(base); }
    function liveRegion() {
      var live = document.getElementById('rs-widget-live');
      if (!live) { live = document.createElement('div'); live.id = 'rs-widget-live'; live.className = 'rs-live'; live.setAttribute('aria-live', 'polite'); document.getElementById('rsettings').appendChild(live); }
      return live;
    }
    // the polite LIVE REGION every move speaks through (review round two): one element for the card, the position and, for
    // the tab widgets, the side of the session name. For the status line the position is counted WITHIN the row's slot and
    // the slot is named (round three, low 2), and a refused move says so (low 1): a screen-reader user tells a key that did
    // nothing from a key that went unheard
    function announce(list, id) {
      var g = cfg.group ? cfg.group(id) : null;
      var ids = list.filter(function (x) { return !(cfg.divider && x === cfg.divider.id) && (!cfg.group || cfg.group(x) === g); }), n = ids.indexOf(id) + 1;
      var side = cfg.divider ? (list.indexOf(id) < list.indexOf(cfg.divider.id) ? ', before the ' + cfg.divider.label.toLowerCase() : ', after the ' + cfg.divider.label.toLowerCase()) : '';   // mid-sentence, the label lowercased
      var where = cfg.group ? ' in the ' + cfg.groupLabel(g) : '';
      liveRegion().textContent = labelOf(id) + ' moved to position ' + n + ' of ' + ids.length + side + where;
    }
    function refused(id) { liveRegion().textContent = labelOf(id) + ' stays in the ' + cfg.groupLabel(cfg.group(id)); }
    function wireGrip(grip, row, id) {
      grip.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        e.preventDefault();
        var list = currentList(), i = list.indexOf(id), to = e.key === 'ArrowUp' ? i - 1 : i + 1;
        if (i < 0 || to < 0 || to >= list.length) return;
        var next = WP.moveId(list, id, to);
        if (!keepsGroup(next, list)) { nudge(row); refused(id); return; }
        commit(next, id);
        grip.focus();
      });
      grip.addEventListener('pointerdown', function (e) {
        if (e.button !== 0) return;   // no preventDefault: the grip TAKES the focus, so the frame hears Escape and the keys
        var pid = e.pointerId, before = currentList(), blocked = false;
        var others = function () { return Array.from(cfg.host.children).filter(function (r) { return r !== row; }); };
        // the moves and the release are heard on the DOCUMENT, never through a pointer capture on the grip: moving the row
        // re-inserts it, which releases a capture on its grip, and the drag would end after its first step. Only the drag's
        // own pointer counts (review round two, low 8): a second finger's release must not commit the drag.
        row.classList.add('rs-dragging'); widgetDrag = true;
        var move = function (ev) {
          if (ev.pointerId !== pid) return;
          var y = ev.clientY, after = null;
          others().forEach(function (r) { var b = r.getBoundingClientRect(); if (y > b.top + b.height / 2) after = r; });
          var wantBefore = after ? after.nextSibling : cfg.host.firstChild;
          if (wantBefore === row || (after && after.nextSibling === row)) return;
          var tentative = others().map(idOf); tentative.splice(after ? tentative.indexOf(idOf(after)) + 1 : 0, 0, id);
          if (!keepsGroup(tentative, before)) { blocked = true; return; }   // held inside its group; the cue comes at the release
          cfg.host.insertBefore(row, wantBefore);
        };
        // the CLICK the release synthesizes belongs to the drag, not to the panel: with the grip moved since the press, the
        // browser targets that click at the two positions' common ancestor, the body, and the panel's click-outside would
        // close the settings under the user's hand. One swallow, for the release's own POINTER click alone (a keyboard's
        // click has detail 0 and no pointer type: the next Space on a switch must land), disarmed by that click, by the next
        // press or key, or one frame on (review round two, medium 2)
        var armSwallow = function () {
          var swallow = function (ce) { if (ce.detail === 0 && !ce.pointerType) return; ce.stopImmediatePropagation(); ce.preventDefault(); disarm(); };   // immediate: the click's target is the document itself, where the panel's own click-outside listener sits beside this one
          var disarm = function () { document.removeEventListener('click', swallow, true); document.removeEventListener('pointerdown', disarm, true); document.removeEventListener('keydown', disarm, true); };
          document.addEventListener('click', swallow, true); document.addEventListener('pointerdown', disarm, true); document.addEventListener('keydown', disarm, true);
          requestAnimationFrame(disarm);
        };
        var end = function (ev) {
          if (ev && ev.pointerId !== undefined && ev.pointerId !== pid) return;
          document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', end); document.removeEventListener('pointercancel', cancel); document.removeEventListener('keydown', esc, true);
          row.classList.remove('rs-dragging'); dragAborts = dragAborts.filter(function (f) { return f !== abort; }); widgetDrag = dragAborts.length > 0;
          if (ev && ev.type === 'pointerup') armSwallow();
          else if (!ev) {
            // Escape ended the drag under a held pointer: its release is still to come, and the click that release synthesizes
            // is the drag's. A CANCEL arms nothing (round three, the medium): a cancelled pointer fires no click and delivers no
            // pointerup, so a listener armed there would wait for the mouse's NEXT release, whose id in Chromium is always 1,
            // and eat that release's click. The late listener leaves on its pointer's release or cancel, never outliving it.
            var lateUp = function (up) { if (up.pointerId !== pid) return; document.removeEventListener('pointerup', lateUp, true); document.removeEventListener('pointercancel', lateUp, true); if (up.type === 'pointerup') armSwallow(); };
            document.addEventListener('pointerup', lateUp, true); document.addEventListener('pointercancel', lateUp, true);
          }
          var now = currentList();
          if (blocked && now.join() === before.join()) { nudge(row); refused(id); }
          if (now.join() !== before.join()) commit(now, id); else paint();
        };
        var cancel = function (ev) { if (ev.pointerId !== pid) return; placeRows(before); end(ev); };
        // the card hidden under the held pointer: the same teardown as a cancel (rows back, listeners off, nothing armed)
        var abort = function () { placeRows(before); end({ type: 'abort', pointerId: pid }); };
        dragAborts.push(abort);
        // Escape is the drag's own while a drag is on (heard first, in the capture phase, and stopped there: the panel's
        // Escape-to-close must not fire under a cancelled drag)
        var esc = function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); placeRows(before); end(); } };
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', end); document.addEventListener('pointercancel', cancel); document.addEventListener('keydown', esc, true);
      });
    }
    function build() {
      if (!cfg.host || cfg.host.children.length) return;
      cfg.list().forEach(function (w) {
        var row = document.createElement('div'); row.className = 'rs-widget'; row.setAttribute('data-widget', w.id);
        var grip = document.createElement('button'); grip.type = 'button'; grip.className = 'rs-grip'; grip.textContent = '⠿';   // the six-dot grip glyph
        grip.setAttribute('aria-label', 'Drag to reorder: ' + w.label); grip.title = 'Drag to reorder, or press the arrow keys';
        if (cfg.reorder === false) { grip = document.createElement('span'); grip.className = 'rs-grip-none'; }   // a section that does not reorder: an empty cell in the grip's column keeps the grid's columns aligned
        var demo = document.createElement('span'); demo.className = 'rs-widget-demo';
        var name = document.createElement('span'); name.className = 'rs-widget-name';
        var b = document.createElement('b'); b.textContent = w.label; name.appendChild(b);
        var d = document.createElement('span'); d.className = 'rs-sub'; d.textContent = w.description; name.appendChild(d);   // the panel's idiom: the description is the row's hover popover
        var sw = document.createElement('button'); sw.type = 'button'; sw.className = 'rs-switch'; sw.setAttribute('role', 'switch'); sw.setAttribute('aria-label', w.label);
        sw.addEventListener('click', function (e) { e.stopPropagation(); var prefs = cfg.prefs(load()); prefs.on[w.id] = !cfg.on(prefs, w); cfg.save(prefs); });
        var opts = document.createElement('span'); opts.className = 'rs-widget-opts';
        var paints = [];
        (w.options || []).forEach(function (o) {
          var wrap = document.createElement('span'); wrap.className = 'rs-widget-opt'; wrap.style.position = 'relative'; wrap.setAttribute('data-opt', o.key); wrap.title = o.label;
          opts.appendChild(wrap);
          var drop = housePick(wrap, cfg.pickPrefix + w.id + '-' + o.key, widgetOptRowHTML, function (id) { var prefs = cfg.prefs(load()); prefs.opts[w.id] = prefs.opts[w.id] || {}; prefs.opts[w.id][o.key] = id; cfg.save(prefs); });
          paints.push(function (prefs) { if (drop) drop(o.choices.map(function (c) { return { id: c.value, name: c.label }; }), cfg.opts(prefs, w)[o.key]); });
        });
        row.appendChild(grip); row.appendChild(demo); row.appendChild(name); row.appendChild(sw); row.appendChild(opts);
        if (cfg.reorder !== false) wireGrip(grip, row, w.id);
        cfg.host.appendChild(row);
        rows[w.id] = { row: row, demo: demo, sw: sw, paints: paints };
      });
      if (cfg.divider) {   // the fixed row the widgets are dragged above or below: a line with the name's place in subtle text, never a fake name (the user's word)
        dividerRow = document.createElement('div'); dividerRow.className = 'rs-widget rs-divider'; dividerRow.setAttribute('data-divider', cfg.divider.id);
        dividerRow.setAttribute('role', 'separator'); dividerRow.setAttribute('aria-label', cfg.divider.label);
        var lbl = document.createElement('span'); lbl.className = 'rs-divider-label'; lbl.textContent = cfg.divider.label; dividerRow.appendChild(lbl);
        cfg.host.appendChild(dividerRow);
      }
      if (cfg.preview) {   // the LIVE PREVIEW (the user's addition): the surface as the current choices and order draw it, under the rows
        // the caption is a TITLE above the box (T415 part two, the user 2026-09-14): the word Preview never sits inside the previewed thing
        var title = document.createElement('div'); title.className = 'rs-preview-title'; title.textContent = 'Preview';
        var box = document.createElement('div'); box.className = 'rs-preview';
        previewBody = document.createElement('div'); previewBody.className = 'rs-preview-body'; box.appendChild(previewBody);
        cfg.host.parentNode.insertBefore(title, cfg.host.nextSibling);
        cfg.host.parentNode.insertBefore(box, title.nextSibling);
      }
    }
    function paint() {
      build();
      var prefs = cfg.prefs(load());
      var focused = document.activeElement && cfg.host.contains(document.activeElement) ? document.activeElement : null;
      placeRows(cfg.order(prefs));   // the rows in the stored order: only a row out of place moves (nodes never rebuild: click-safe, focus kept)
      if (focused && document.activeElement !== focused && focused.isConnected) focused.focus();   // the belt under placeRows: a focus a move did take comes back
      cfg.list().forEach(function (w) {
        var r = rows[w.id]; if (!r) return;
        var on = cfg.on(prefs, w);
        r.sw.classList.toggle('on', on); r.sw.setAttribute('aria-checked', on ? 'true' : 'false');
        r.row.classList.toggle('rs-widget-off', !on);
        var node = cfg.demo(w, prefs);
        if (node) r.demo.replaceChildren(node); else r.demo.replaceChildren();
        r.paints.forEach(function (fn) { fn(prefs); });
      });
      if (previewBody) { var pv = cfg.preview(prefs); if (pv) previewBody.replaceChildren(pv); else previewBody.replaceChildren(); }
    }
    return { paint: paint, save: cfg.save };   // save exposed: a second section over the same store (the rings) writes through the first's
  }
  // the tab widgets: settings.tabWidgets with tabCtx as the mirror; the demo is a miniature tab with the widget's node
  // where the strip would put it (before the name or after it)
  function widgetPrefs(s) { return TW.tabWidgetPrefs(s.tabWidgets, s.tabCtx); }
  // the demo tab (T415 part two, the user 2026-09-14): the demo record's NAME in its identity colour, the way a real tab wears it
  // (styles.css .tab.colored .tab-label reads --chip-bg, the strip's own variable; gear.css mirrors the rule for this page), from
  // one source, the status line's demo record (status-widgets.ts DEMO_RECORD: web, in the accent)
  function demoTab() { var tab = document.createElement('span'); tab.className = 'tab colored'; tab.style.setProperty('--chip-bg', SW.DEMO_RECORD.color.bg); return tab; }
  function demoLabel() { var label = document.createElement('span'); label.className = 'tab-label'; label.textContent = SW.DEMO_RECORD.name; return label; }
  var tabSection = widgetSection({
    host: document.getElementById('rs-widgets'), list: TW.titleWidgets, prefs: widgetPrefs, pickPrefix: 'wopt-',   // the widgets that render INTO the title; the rings have their own rows below
    order: TW.tabListOrder, divider: { id: TW.NAME_DIVIDER, label: 'Session name' }, group: null, groupLabel: null,   // sentence case, as every section label (the user 2026-09-18)
    save: function (prefs) { var s = load(); s.tabWidgets = prefs; s.tabCtx = TW.tabCtxOfPrefs(prefs); save(s); paintWidgets(); },
    on: TW.widgetOn, opts: TW.widgetOpts,
    preview: function (prefs) {   // a tab as the strip would draw it: the enabled widgets on each side of the name, in order
      var tab = demoTab();
      TW.composeTabWidgets(tab, 'before', TW.DEMO_SID, TW.DEMO_STATUS, prefs);
      tab.appendChild(demoLabel());
      TW.composeTabWidgets(tab, 'after', TW.DEMO_SID, TW.DEMO_STATUS, prefs);
      return tab;
    },
    demo: function (w, prefs) {
      var tab = demoTab(), label = demoLabel();
      var node = TW.renderWidgetDemo(w, prefs);
      if (w.slot === 'before') { if (node) tab.appendChild(node); tab.appendChild(label); }
      else { tab.appendChild(label); if (node) tab.appendChild(node); }
      return tab;
    },
  });
  // THE RINGS (the rings-as-widgets change, 2026-09-14): the same builder over the registry's rings, the same store
  // (settings.tabWidgets, through the tab section's own save, so the tabCtx mirror and the repaint come with it) and the
  // same switch; no divider, no grip, no drag (the order is the precedence: red over yellow over amber, the registry's).
  // The demo is a miniature tab wearing the ring its predicate lights on its demo status, a plain tab once switched off.
  var ringSection = widgetSection({
    host: document.getElementById('rs-rings'), list: TW.ringWidgets, prefs: widgetPrefs, pickPrefix: 'wopt-',
    order: function () { return TW.ringWidgets().map(function (w) { return w.id; }); }, divider: null, group: null, groupLabel: null, reorder: false,
    save: tabSection.save,
    on: TW.widgetOn, opts: TW.widgetOpts,
    demo: function (w, prefs) {
      var tab = demoTab(); tab.appendChild(demoLabel());   // the demo record's name in its identity colour (T415 part two), the ring on top
      var cls = TW.ringDemoClass(w, prefs); if (cls) tab.classList.add(cls);
      return tab;
    },
  });
  // the status line's widgets (T409): settings.statusWidgets with showBranch and showSessionBadge as the mirrors (no
  // default injected by load() for any of the three: a store from before the widgets reads the widget defaults, its two
  // old keys being the gear's own injected default and no choice (the one-shot migration), and only a change here writes
  // the key); the demo is the widget alone, as the line draws it
  function statusPrefs(s) { return SW.statusWidgetPrefs(s.statusWidgets); }
  var statusSection = widgetSection({
    host: document.getElementById('rs-swidgets'), list: SW.statusWidgets, prefs: statusPrefs, pickPrefix: 'swopt-',
    order: SW.statusListOrder, divider: null,
    group: function (id) { var w = SW.statusWidget(id); return w ? w.slot : null; },
    groupLabel: function (g) { return g + ' slot'; },   // "left slot" / "right slot", the words the live region uses   // a row stays in its slot's group: the line's slots are the registry's (the user's word), a drag across is refused with a cue
    preview: function (prefs) {   // the line as the chat would draw it: the left slot, the state chip, then the right slot ahead of the controls
      var line = document.createElement('div'); line.className = 'rs-sl';
      SW.composeStatusWidgets(line, 'left', SW.DEMO_RECORD, prefs);
      var chip = document.createElement('span'); chip.className = 'chip rs-sl-chip'; chip.textContent = 'Ready'; line.appendChild(chip);
      var right = document.createElement('span'); right.className = 'rs-sl-right';
      SW.composeStatusWidgets(right, 'right', SW.DEMO_RECORD, prefs);
      // the controls and the battery through the line's own renderer over the demo status (T415 part two, the user 2026-09-14: the
      // words and the boxed number previewed nothing): the mode, model and effort badges tinted by the kernel's rank rule on the
      // selected colormap, the battery filled and coloured by its percentage; no hooks, so nothing opens and nothing compacts
      var st = SC.demoStatus(cmStops(load().colormap));
      var meta = document.createElement('span'); meta.className = 'spinner-meta'; SC.syncMetaControls(meta, st, null, {}); right.appendChild(meta);
      var bar = SC.ctxBar(); SC.setCtxBar(bar, st.ctx, false, SC.pickTone(st.ctxColor, st.ctxTone), false); right.appendChild(bar);   // the tone on the yatharth themes, as the line picks it
      line.appendChild(right);
      return SW.makeInert(line);   // a preview never carries the folder's click act (review round two, low 4)
    },
    save: function (prefs) { var s = load(); s.statusWidgets = prefs; var m = SW.legacyOfStatusPrefs(prefs); s.showBranch = m.showBranch; s.showSessionBadge = m.showSessionBadge; save(s); paintWidgets(); },
    on: SW.statusWidgetOn, opts: SW.statusWidgetOpts,
    demo: function (w, prefs) { return SW.renderStatusWidgetDemo(w, prefs); },
  });
  function paintWidgets() { tabSection.paint(); ringSection.paint(); statusSection.paint(); }
  // the previews' tints follow the theme and the colormap (T415 part two): the light theme re-encodes the badges' colours at paint
  // (ctx-color's readableRgb reads the body's class) and the demo's tints sample the selected map, so a theme flip (the body's class,
  // applyTheme) and every settings write (the same-document signal save() raises, the storage event from another frame) repaint the
  // previews, as the chat's line repaints on its own tick
  if (typeof MutationObserver === 'function') new MutationObserver(function () { paintWidgets(); }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  window.addEventListener('romp:settings', function () { paintWidgets(); });
  window.addEventListener('storage', function (e) { if (e.key === 'romp:settings') paintWidgets(); });
  // The remaining native selects sweep onto the same builder (the user 2026-08-27, closing the
  // 3-house/3-native split the gauge migration left): a generic adapter over ANY hidden select —
  // options snapshot from sel.options (so the effort selects, whose options arrive from /models
  // in fillChoices, stay correct), the pick writes sel.value and fires the select's own change
  // event, so every existing persistence path (setUpdateMode / s.backend / setJudgeEffort / ...)
  // and the fillMixedMarks row lookup keep working untouched. Repaints ride the same events the
  // value rides: the change event, a childList mutation (fillChoices rewriting options), and the
  // end of fill() (which sets values silently — repaintSelectPicks below). The model pickers keep
  // versionMenu (they need family→version submenus); tabctx/scheme/theme have their own rows.
  var selectPickPaints = [];
  function repaintSelectPicks() { selectPickPaints.forEach(function (fn) { fn(); }); }
  function selectPick(sel, wrapStyle) {
    if (!sel) return;
    sel.style.display = 'none';
    var wrap = document.createElement('div');
    wrap.setAttribute('style', 'position:relative;' + wrapStyle);
    sel.parentNode.insertBefore(wrap, sel.nextSibling);
    var rowHTML = function (o) {
      return '<span style="flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--menu-fg, #ccc)">' + o.name + '</span>';
    };
    var drop = housePick(wrap, 'val', rowHTML, function (id) {
      sel.value = id; sel.dispatchEvent(new Event('change')); paint();
    });
    function paint() {
      drop(Array.prototype.map.call(sel.options, function (o) { return { id: o.value, name: o.textContent }; }), sel.value);
    }
    sel.addEventListener('change', paint);
    new MutationObserver(paint).observe(sel, { childList: true });
    selectPickPaints.push(paint);
    paint();
  }
  selectPick(upm, 'margin-top:5px');
  selectPick(bk, 'margin-top:5px');
  selectPick(je, 'flex:0 0 auto;width:45%');
  selectPick(ie, 'flex:0 0 auto;width:45%');
  selectPick(jc, 'flex:0 0 auto;width:45%');   // T277: the concurrency select wears the same facade as the effort picks
  selectPick(de, 'flex:0 0 auto;width:45%');
  selectPick(cme, 'flex:0 0 auto;width:45%');
  jix.addEventListener('change', function () { var s = load(); s.showIndexJudges = jix.checked; save(s); });
  jtr.addEventListener('change', function () { var s = load(); s.showTriageJudges = jtr.checked; save(s); });
  if (fc) fc.addEventListener('change', function () { var s = load(); s.collapsed = fc.checked; save(s); });
  // Every gt-stamped post below mints its stamp through the gesture clock (gesture-clock.js): epoch
  // ms at the click, lifted above the highest stamp this page has seen for that store — fill()
  // learns each store's current stamp from /version, the settingStale listener from the frame that
  // refused a gesture — so a device whose clock runs behind another's still outranks every stamp it
  // knows of. Federation queues KERNEL_SETTING posts per host while a socket is down and flushes
  // them on reconnect, so a delayed copy must carry the ORIGINAL gesture's stamp — the kernel orders
  // applies by it and stands a stale flush down instead of walking the mesh back to an hours-old
  // pick (a frozen tab's flush did exactly that). Stamp in the message literal, never at send/flush
  // time.
  // Thinking summaries is kernel-side but PER-INSTALL: the post goes to the LOCAL kernel only —
  // deliberately not in federation's KERNEL_SETTING set (thinking-summaries.test.ts pins the
  // membership), so it neither queues for nor reaches another machine. Stamped all the same: two
  // dashboards on one kernel still race, and the kernel orders every setting by `gt`.
  if (ths) ths.addEventListener('change', function () { post({ type: 'setThinkingSummaries', enabled: ths.checked, gt: gclock.stamp('thinking-summaries') }); });
  // Whole chat frames (2026-09-15): per-install like Thinking summaries (the LOCAL kernel's floor), not a KERNEL_SETTING; stamped
  if (wcf) wcf.addEventListener('change', function () { post({ type: 'setWholeChatFrames', enabled: wcf.checked, gt: gclock.stamp('whole-chat-frames') }); });
  // THE TASK TRACKING SWITCH (T404): the kernel's setting (gt-gated, a KERNEL_SETTING across machines). The click POSTS and
  // nothing more: the dependent controls dress and the shell hears the flip (a taskTracking message: the rail's Outline and
  // Feed buttons and any open pane of theirs, ahead of its next /version read) on the KERNEL'S ECHO, the taskTracking frame
  // the applying kernel answers on this socket, so a refused write (a settingStale frame with why) leaves the shell and the
  // panes as they were and fill() snaps the box back (T404 round two, medium 4: the gear and the shell went off 6 ms after
  // a click the kernel had refused, and the judges kept spending behind a switch that read off)
  var TT_OFF_TIP = 'Enable task tracking to use this (Settings, Task tracking).';
  function dressTracking(on) {
    var rows = Array.prototype.slice.call(document.querySelectorAll('#rsettings .rs-pane[data-pane=tasks] .rs-row')).filter(function (r) { return !r.querySelector('#rs-tasktrack'); });   // the judge rows; never the switch's own row
    [pn.fleet, pn.feed, jix, jtr].forEach(function (el) { var row = el && el.closest ? el.closest('label') : null; if (row) rows.push(row); });
    rows.forEach(function (row) {
      row.classList.toggle('rs-off', !on);
      if (on) row.removeAttribute('title'); else row.title = TT_OFF_TIP;
      Array.prototype.forEach.call(row.querySelectorAll('input, select, button'), function (c) { c.disabled = !on; });
    });
    var an1 = document.getElementById('rs-autonudge-tt'), sc1 = document.getElementById('rs-suggestcompact-tt');
    if (an1) an1.hidden = !!on;
    if (sc1) sc1.hidden = !!on;
  }
  function tellShellTracking(on) { try { (window.parent !== window ? window.parent : window).postMessage({ romp: 'taskTracking', on: !!on }, '*'); } catch (e) {} }
  if (tk) tk.addEventListener('change', function () { post({ type: 'setTaskTracking', enabled: tk.checked, gt: gclock.stamp('task-tracking') }); });
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (!m || m.type !== 'taskTracking' || typeof m.on !== 'boolean') return;   // the kernel's echo of an applied flip
    if (tk) tk.checked = m.on;
    dressTracking(m.on);
    tellShellTracking(m.on);
  });
  // Auto Nudge / judge tiers are SERVER-SIDE (the kernel runs them): post the
  // change; the controls re-initialize from /version on every open (fill()).
  // Each attached kernel keeps its own copy, so the post goes to all of them
  // (federation.ts KERNEL_SETTING) — which is also what resolves a split box:
  // the click picks one answer and every machine takes it.
  if (an) an.addEventListener('change', function () {
    clearAutoNudgeSplit();
    post({ type: 'setAutoNudge', enabled: an.checked, gt: gclock.stamp('auto-nudge') });
  });
  if (fe) fe.addEventListener('change', function () { post({ type: 'setFileEditing', enabled: fe.checked, gt: gclock.stamp('file-editing') }); });
  if (cvm) cvm.addEventListener('change', function () { post({ type: 'setConserve', enabled: cvm.checked }); });
  if (csg) csg.addEventListener('change', function () { post({ type: 'setCompactSuggest', enabled: csg.checked, gt: gclock.stamp('compact-suggest') }); });
  // ── the in-dashboard LOGIN flow (T157): the dashboard is already on the phone over Tailscale,
  // so streaming the CLI's paste-code OAuth URL here IS the phone login. The code input is a pure
  // pass-through to the kernel's PTY — nothing is stored or logged on any side.
  var lgB = document.getElementById('rs-login-btn'), lgS = document.getElementById('rs-login-state'),
      lgU = document.getElementById('rs-login-url'), lgC = document.getElementById('rs-login-code'),
      lgI = document.getElementById('rs-login-input'), lgSend = document.getElementById('rs-login-send'),
      lgX = document.getElementById('rs-login-cancel'), lgA = document.getElementById('rs-login-acct');
  var lgTimer = null, lgLive = '';   // lgLive = the flow state lgRender last saw (drives the button's two jobs)
  // ── the stored logins (T346): every other Claude login a session on this machine can be billed to, listed
  // under the machine's own with a Remove each. Read from the kernel's authed /logins on every settings fill
  // and after a Remove: labels, organisations, dates and states, never a token. A Remove acknowledges at once
  // (the row goes, the button disables) and the re-read confirms.
  var lgL = document.getElementById('rs-logins');
  function lgDate(t) { try { return new Date(t * 1000).toLocaleDateString(); } catch (e) { return ''; } }
  function lgWhen(t) {
    if (typeof t !== 'number') return 'soon';
    var d = Math.round((t * 1000 - Date.now()) / 86400000);
    return d <= 0 ? 'now (the token is a year old)' : 'in ' + d + ' day' + (d === 1 ? '' : 's');
  }
  function lgLogins(d) {
    if (!lgL) return;
    var rows = (d && d.logins) || [];
    lgL.textContent = '';
    var head = document.createElement('div');
    head.className = 'rs-sub';
    head.textContent = rows.length ? 'Other logins a session can bill (the token stays where you keep it; romp keeps the label and the command that reads it):'
                                   : 'No other Claude logins stored on this machine. Add one with romp login add <label>.';
    lgL.appendChild(head);
    rows.forEach(function (r) {
      var row = document.createElement('div');
      row.className = 'rs-login-row';
      var name = document.createElement('span');
      name.className = 'rs-login-name';
      name.textContent = r.display || r.label || r.id;
      name.title = name.textContent;
      row.appendChild(name);
      var note = r.why ? r.why
        : r.expiresSoon ? 'token expires ' + lgWhen(r.expiresAt)
        : (typeof r.addedAt === 'number' ? 'added ' + lgDate(r.addedAt) : '');
      var st = document.createElement('span');
      st.className = 'rs-note' + (r.why ? ' rs-login-bad' : (r.expiresSoon ? ' rs-login-warn' : ''));
      st.textContent = note;
      row.appendChild(st);
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'rs-login-rm';
      rm.textContent = 'Remove';
      rm.title = 'Forget this login here: its record leaves this machine (the token stays where you keep it); a session billed to it falls back at its next launch.';
      rm.addEventListener('click', function () {
        rm.disabled = true; rm.textContent = 'Removing…';
        post({ type: 'loginRemove', id: r.id });
        row.remove();
        lgFetchLogins();
      });
      row.appendChild(rm);
      lgL.appendChild(row);
    });
  }
  function lgFetchLogins() {
    if (!lgL) return;
    fetch(ku('/logins'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(lgLogins).catch(function () {});
  }
  // The paste-code UI lives in a MODAL (the user 2026-08-30: "it would just be a login button…
  // then it would pop up another modal that says paste the code so it doesn't always sit there
  // taking up space") — centered card over a translucent backdrop, the panel rule's treatment.
  // Closing the modal never cancels the flow (Cancel does); the row's state span keeps narrating,
  // and the button reopens the modal mid-flow instead of restarting the login.
  var lgM = document.getElementById('rs-login-modal');
  function lgModal(on) { if (lgM) lgM.hidden = !on; }
  if (lgM) lgM.addEventListener('click', function (e) {
    // This dialog is a sibling of settings. Keep its clicks inside the dialog so the
    // settings outside-click handler cannot close the panel and shrink its host iframe.
    e.stopPropagation();
    if (e.target === lgM) lgModal(false);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && lgM && !lgM.hidden) lgModal(false); });
  function lgRender(v) {
    if (!lgB) return;
    var f = (v && v.login) || { state: '' };
    if (lgA) lgA.textContent = v && v.acctLabel ? 'Logged in as ' + v.acctLabel + '. Each machine logs in its own credential store.'
                                                : 'No Claude login on this machine — sessions can only bill an API key. Log in from any browser (your phone works: the code flow needs no localhost).';
    lgLive = f.state || '';
    var mid = f.state === 'url' || f.state === 'starting' || f.state === 'verifying';
    lgB.hidden = false;
    lgB.textContent = mid ? 'Show login code…' : 'Log in to Claude Code';
    if (!f.state) lgModal(false);   // the flow ended (logged in / cancelled) — the modal's job is done
    lgS.textContent = f.state === 'starting' ? 'starting the login flow…'
      : f.state === 'verifying' ? 'checking the code…'
      : f.state === 'error' ? (f.err || 'the login flow failed — try again') : '';
    lgS.style.color = f.state === 'error' ? '#F85B5A' : '';
    lgU.hidden = f.state !== 'url';
    lgC.hidden = f.state !== 'url';
    if (f.state === 'url' && f.url && lgU.getAttribute('data-url') !== f.url) {
      lgU.setAttribute('data-url', f.url);
      lgU.textContent = '';
      var a = document.createElement('a');
      a.href = f.url; a.target = '_blank'; a.rel = 'noreferrer';
      a.textContent = '1. Open the sign-in page (any device) →';
      a.style.color = '#9cd2ff';
      lgU.appendChild(a);
      var hint = document.createElement('div');
      hint.className = 'rs-sub';
      hint.textContent = '2. Finish signing in there; it shows a code. 3. Paste the code below.';
      lgU.appendChild(hint);
    }
  }
  function lgPoll() {
    fetch(ku('/version'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (v) {
      lgRender(v);
      var st = (v.login || {}).state;
      if (st === 'starting' || st === 'url' || st === 'verifying') lgTimer = setTimeout(lgPoll, 1500);
      else lgTimer = null;
    }).catch(function () { lgTimer = setTimeout(lgPoll, 3000); });
  }
  if (lgB) lgB.addEventListener('click', function () {
    lgModal(true);
    if (lgLive === 'url' || lgLive === 'starting' || lgLive === 'verifying') return;   // reopen only — never restart a live flow
    post({ type: 'loginStart' });
    lgS.textContent = 'starting the login flow…';
    if (!lgTimer) lgTimer = setTimeout(lgPoll, 800);
  });
  if (lgSend) lgSend.addEventListener('click', function () {
    var code = lgI && lgI.value ? lgI.value.trim() : '';
    if (!code) return;
    post({ type: 'loginCode', code: code });   // pass-through; the kernel writes it to the PTY and nothing else
    if (lgI) lgI.value = '';
    lgS.textContent = 'checking the code…';
    if (!lgTimer) lgTimer = setTimeout(lgPoll, 800);
  });
  if (lgI) lgI.addEventListener('keydown', function (e) { if (e.key === 'Enter' && lgSend) lgSend.click(); });
  if (lgX) lgX.addEventListener('click', function () { lgModal(false); post({ type: 'loginCancel' }); if (lgTimer) { clearTimeout(lgTimer); lgTimer = null; } lgRender({ login: { state: '' } }); });
  if (upm) upm.addEventListener('change', function () { post({ type: 'setUpdateMode', mode: upm.value, gt: gclock.stamp('update-mode') }); });
  // The judge MODEL pickers mirror the session pickers (the user 2026-08-25): families top-level,
  // clicking a family sends its /models `default` (the user's remembered version), hover or
  // ArrowRight reveals a side submenu of versions. The native select stays (hidden) as the VALUE
  // holder — fill()/mixed marks keep working — and the button+menu is the visible control. The
  // caret ALWAYS faces right (▸); the submenu PREFERS the right side, falling left only when the
  // right edge would clip (measured, never assumed).
  function versionMenu(sel, extraFirst) {
    if (!sel) return;
    sel.style.display = 'none';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'rs-vermenu-btn';
    btn.setAttribute('style', 'background:var(--input-bg, #1e1e1e);color:var(--fg, #ccc);border:1px solid rgba(255,255,255,0.25);'
      + 'border-radius:5px;padding:2px 8px;cursor:pointer;font:inherit;');
    sel.parentNode.insertBefore(btn, sel.nextSibling);
    var labelOf = function (val) {
      var o = sel.querySelector('option[value="' + val + '"]');
      return o ? o.textContent : val;
    };
    var syncBtn = function () { btn.textContent = labelOf(sel.value) + ' \u25BE'; };
    var mo = new MutationObserver(syncBtn);
    mo.observe(sel, { childList: true });
    sel.addEventListener('change', syncBtn);
    // fill() writes sel.value SILENTLY on every open (a change event here would POST the setting
    // back), so the button must join the ONE repaint registry fill() flushes — without this it
    // showed its install-tick label (the first option) while the select and the STATE file held
    // the user's pick (the user 2026-09-01, whose Opus distill pick displayed as Follow triage).
    selectPickPaints.push(syncBtn);
    setTimeout(syncBtn, 0);
    var menu = null, sub = null;
    var closeAll = function () { if (sub) { sub.remove(); sub = null; } if (menu) { menu.remove(); menu = null; } };
    document.addEventListener('click', closeAll);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAll(); });
    try { window.addEventListener('storage', function (e) { if (e.key === 'romp:menu-echo' && e.newValue) closeAll(); }); } catch (e) {}
    var pick = function (val) { sel.value = val; sel.dispatchEvent(new Event('change')); syncBtn(); closeAll(); };
    var MSTYLE = 'position:fixed;z-index:1001;min-width:130px;padding:4px;background:var(--menu-bg, #252526);'
      + 'border:1px solid var(--menu-border, rgba(255,255,255,0.12));border-radius:var(--radius-menu, 6px);box-shadow:var(--shadow-menu, 0 4px 12px rgba(0,0,0,0.35));'
      + 'font-size:12px;line-height:1.4;color:var(--menu-fg, #cccccc);user-select:none;';
    var rowStyle = 'padding:4px 22px 4px 8px;border-radius:4px;cursor:pointer;position:relative;white-space:nowrap;display:flex;align-items:center;';
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (menu) { closeAll(); return; }
      menu = document.createElement('div');
      menu.setAttribute('style', MSTYLE);
      menu.addEventListener('click', function (e2) { e2.stopPropagation(); });
      (extraFirst || []).concat(choices && choices.models || []).forEach(function (fam) {
        var row = document.createElement('div');
        row.setAttribute('style', rowStyle);
        row.tabIndex = 0;
        row.appendChild(document.createTextNode(fam.label));
        var versions = fam.versions || [];
        var famCur = sel.value === fam.value || versions.some(function (v) { return v.value === sel.value; });
        if (famCur) {
          var ck = document.createElement('span'); ck.textContent = '\u2713';
          ck.setAttribute('style', 'position:absolute;right:6px;top:50%;transform:translateY(-50%);background:var(--check-bg, #1EA1EB);color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;');
          row.appendChild(ck);
        }
        var openSub = versions.length > 1 ? function () {
          if (sub) { sub.remove(); sub = null; }
          sub = document.createElement('div');
          sub.setAttribute('style', MSTYLE + 'z-index:1002;');
          // "Latest" heads the submenu — the session pickers' floating gesture, on the judge tiers
          // too: the family row sends the remembered pin and the rows below pin, so this is the row
          // that sends the bare alias, and the tier follows the CLI's newest again
          var latest = document.createElement('div');
          latest.setAttribute('style', rowStyle);
          latest.tabIndex = 0;
          latest.appendChild(document.createTextNode('Latest'));
          if (sel.value === fam.value) {
            var c0 = document.createElement('span'); c0.textContent = '\u2713';
            c0.setAttribute('style', 'position:absolute;right:6px;top:50%;transform:translateY(-50%);background:var(--check-bg, #1EA1EB);color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;');
            latest.appendChild(c0);
          }
          latest.addEventListener('mouseenter', function () { latest.style.background = 'var(--menu-hover, rgba(255,255,255,0.09))'; });
          latest.addEventListener('mouseleave', function () { latest.style.background = 'transparent'; });
          latest.addEventListener('click', function (e2) { e2.stopPropagation(); pick(fam.value); });
          latest.addEventListener('keydown', function (e2) {
            if (e2.key === 'Enter' || e2.key === ' ') { e2.preventDefault(); e2.stopPropagation(); pick(fam.value); }
            else if (e2.key === 'ArrowLeft') { e2.preventDefault(); sub.remove(); sub = null; row.focus(); }
          });
          sub.appendChild(latest);
          versions.forEach(function (v) {
            var r2 = document.createElement('div');
            r2.setAttribute('style', rowStyle);
            r2.tabIndex = 0;
            r2.appendChild(document.createTextNode(v.label));
            if (v.learned) {
              // LOUD, per the fail-loudly rule: a version no catalog list carries — a running session's
              // CLI reported it (kernel /models `learned`) — says so, as the chat and timeline pickers do
              var tag = document.createElement('span'); tag.textContent = ' new';
              tag.setAttribute('style', 'font-size:0.82em;opacity:0.6;margin-left:4px;');
              r2.appendChild(tag);
              r2.title = "Reported by a running session's Claude Code; not yet in romp's version list";
            }
            if (sel.value === v.value) {
              var c2 = document.createElement('span'); c2.textContent = '\u2713';
              c2.setAttribute('style', 'position:absolute;right:6px;top:50%;transform:translateY(-50%);background:var(--check-bg, #1EA1EB);color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;');
              r2.appendChild(c2);
            }
            r2.addEventListener('mouseenter', function () { r2.style.background = 'var(--menu-hover, rgba(255,255,255,0.09))'; });
            r2.addEventListener('mouseleave', function () { r2.style.background = 'transparent'; });
            r2.addEventListener('click', function (e2) { e2.stopPropagation(); pick(v.value); });
            r2.addEventListener('keydown', function (e2) {
              if (e2.key === 'Enter' || e2.key === ' ') { e2.preventDefault(); e2.stopPropagation(); pick(v.value); }
              else if (e2.key === 'ArrowLeft') { e2.preventDefault(); sub.remove(); sub = null; row.focus(); }
            });
            sub.appendChild(r2);
          });
          document.body.appendChild(sub);
          var rr = row.getBoundingClientRect();
          // the side rule: PREFER right; fall left only when the right edge would clip (measured)
          var sw = sub.offsetWidth || 130;
          if (rr.right + 4 + sw <= window.innerWidth - 8) sub.style.left = Math.round(rr.right + 4) + 'px';
          else sub.style.left = Math.max(8, Math.round(rr.left) - sw - 4) + 'px';
          var sh = sub.offsetHeight || 0;
          sub.style.top = Math.min(Math.round(rr.top), Math.max(8, window.innerHeight - sh - 8)) + 'px';
          return sub;
        } : null;
        if (openSub) {
          var caret = document.createElement('span');
          caret.textContent = '\u25B8';   // ALWAYS right-facing — it marks "expandable", not the side
          caret.setAttribute('style', 'margin-left:auto;padding-left:10px;opacity:0.55;');
          row.appendChild(caret);
          row.addEventListener('mouseenter', function () { row.style.background = 'var(--menu-hover, rgba(255,255,255,0.09))'; openSub(); });
        } else {
          row.addEventListener('mouseenter', function () { row.style.background = 'var(--menu-hover, rgba(255,255,255,0.09))'; if (sub) { sub.remove(); sub = null; } });
        }
        row.addEventListener('mouseleave', function () { row.style.background = 'transparent'; });
        row.addEventListener('click', function (e2) { e2.stopPropagation(); pick(fam.default || fam.value); });
        row.addEventListener('keydown', function (e2) {
          if (e2.key === 'Enter' || e2.key === ' ') { e2.preventDefault(); e2.stopPropagation(); pick(fam.default || fam.value); }
          else if ((e2.key === 'ArrowRight' || e2.key === 'ArrowLeft') && openSub) {
            e2.preventDefault();
            var s = openSub();
            var first = s && s.querySelector('[tabindex]');
            if (first) first.focus();
          }
        });
        menu.appendChild(row);
      });
      document.body.appendChild(menu);
      var br = btn.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(Math.round(br.left), window.innerWidth - (menu.offsetWidth || 140) - 8)) + 'px';
      var mh = menu.offsetHeight || 0;
      menu.style.top = (br.bottom + 4 + mh > window.innerHeight - 8 ? Math.max(8, Math.round(br.top) - mh - 4) : Math.round(br.bottom + 4)) + 'px';
    });
  }
  fillChoices().then(function () {
    versionMenu(jm);
    versionMenu(im);
    versionMenu(dm, [{ value: 'triage', label: 'Follow triage', versions: [] }]);
    versionMenu(cmm, [{ value: 'session', label: 'Same as the session', versions: [] },
                      { value: 'default', label: 'Default', versions: [] }]);
  });
  if (jm) jm.addEventListener('change', function () { post({ type: 'setJudgeModel', model: jm.value, gt: gclock.stamp('judge-model') }); judgeFastGate(); });
  if (im) im.addEventListener('change', function () { post({ type: 'setIndexModel', model: im.value, gt: gclock.stamp('index-model') }); judgeFastGate(); });
  if (je) je.addEventListener('change', function () { post({ type: 'setJudgeEffort', effort: je.value, gt: gclock.stamp('judge-effort') }); });
  if (ie) ie.addEventListener('change', function () { post({ type: 'setIndexEffort', effort: ie.value, gt: gclock.stamp('index-effort') }); });
  if (jc) jc.addEventListener('change', function () { post({ type: 'setJudgeConcurrency', value: jc.value, gt: gclock.stamp('judge-concurrency') }); });
  if (dm) dm.addEventListener('change', function () { post({ type: 'setDistillModel', model: dm.value, gt: gclock.stamp('distill-model') }); judgeFastGate(); });
  if (de) de.addEventListener('change', function () { post({ type: 'setDistillEffort', effort: de.value, gt: gclock.stamp('distill-effort') }); });
  // Fast is an Opus-only research preview (render.ts fastAvailable, the same rule): a pinned
  // non-Opus comment model makes the box a dead control, so it disables — and a model pick that
  // strands a checked box also unchecks it, visibly, as part of the user's own gesture (never a
  // silent per-fill flap; the kernel would otherwise ask fast on every create and toast every
  // refusal). 'session'/'default' stay enabled: the session/account default may be Opus.
  function cmtFastGate(fromModelPick) {
    if (!cmf || !cmm) return;
    var val = (cmm.value || '').toLowerCase();
    var can = val === 'session' || val === 'default' || val.indexOf('opus') !== -1;
    cmf.disabled = !can;
    if (!can && cmf.checked && fromModelPick) {
      cmf.checked = false;
      post({ type: 'setCommentFast', fast: 'session', gt: gclock.stamp('comment-fast') });
    }
  }
  if (cmm) cmm.addEventListener('change', function () { post({ type: 'setCommentModel', model: cmm.value, gt: gclock.stamp('comment-model') }); cmtFastGate(true); });
  if (cme) cme.addEventListener('change', function () { post({ type: 'setCommentEffort', effort: cme.value, gt: gclock.stamp('comment-effort') }); });
  if (cmf) cmf.addEventListener('change', function () { post({ type: 'setCommentFast', fast: cmf.checked ? 'on' : 'session', gt: gclock.stamp('comment-fast') }); });
  // Fast mode for the judges, one box per tier (T300, the user 2026-09-10): a kernel setting per tier like the
  // judge knobs (stamped, propagated); the judges read each tier's flag per call
  if (jf) jf.addEventListener('change', function () { post({ type: 'setJudgeFast', enabled: jf.checked, gt: gclock.stamp('judge-fast') }); judgeFastGate(); });   // the hint follows the box (a refusal reads only on a checked box)
  if (df) df.addEventListener('change', function () { post({ type: 'setDistillFast', enabled: df.checked, gt: gclock.stamp('distill-fast') }); judgeFastGate(); });   // the hint follows the box (a refusal reads only on a checked box)
  if (xf) xf.addEventListener('change', function () { post({ type: 'setIndexFast', enabled: xf.checked, gt: gclock.stamp('index-fast') }); judgeFastGate(); });   // the hint follows the box (a refusal reads only on a checked box)
  // the Automation pane's model switches (the user 2026-09-17): kernel settings like the judge boxes (stamped, propagated); the
  // SDK backend reads Always fast at connect and on a model change, Retry upgrades from its tick — no gate: the kernel
  // decides per session whether the model can run fast, and the switch is a standing wish, not a per-model verdict
  if (afb) afb.addEventListener('change', function () { post({ type: 'setAlwaysFast', enabled: afb.checked, gt: gclock.stamp('always-fast') }); });
  if (rub) rub.addEventListener('change', function () { post({ type: 'setRetryUpgrade', enabled: rub.checked, gt: gclock.stamp('retry-upgrade') }); });
  // Fast mode is an Opus-only research preview (render.ts fastAvailable and cmtFastGate above, the same rule),
  // and the judges' opt-in rides only a call whose model is Opus: with no tier on Opus the box is inert, so it
  // greys and its hint says why (a review finding on the setting's first cut: with the default tiers the box
  // did nothing and the gear did not say so). The stored value is left alone, unlike cmtFastGate's uncheck:
  // nothing fires or toasts while it is inert, and a tier pinned to Opus later brings it back without a
  // second click. Distilling on Follow triage resolves to the triage pick; a tier whose value has not
  // loaded is unknown, never a refusal (the benefit of the doubt fastAvailable gives an unknown model).
  var fastRefused = {};   // /version's record: judge tier ("triage" | "distill" | "index") -> { reason, model, t }
  function judgeFastTiers() {
    return [
      { box: jf, wrap: 'rs-judgefast-wrap', sub: 'rs-judgefast-sub', tier: 'triage', word: 'triage',
        model: function () { return jm ? (jm.value || '') : ''; } },
      { box: df, wrap: 'rs-distillfast-wrap', sub: 'rs-distillfast-sub', tier: 'distill', word: 'distilling',
        model: function () { var v = dm ? (dm.value || '') : ''; return v === 'triage' ? (jm ? (jm.value || '') : '') : v; } },   // Follow triage resolves
      { box: xf, wrap: 'rs-indexfast-wrap', sub: 'rs-indexfast-sub', tier: 'index', word: 'indexing',
        model: function () { return im ? (im.value || '') : ''; } }];
  }
  function judgeFastGate() {
    judgeFastTiers().forEach(function (t) {
      if (!t.box) return;
      var wrap = document.getElementById(t.wrap), sub = document.getElementById(t.sub);
      var v = t.model(), can = !v || v.toLowerCase().indexOf('opus') !== -1;   // unknown (not loaded) = the benefit of the doubt
      t.box.disabled = !can;
      if (wrap) wrap.classList.toggle('rs-off', !can);
      var r = can && t.box.checked ? fastRefused[t.tier] : null;   // the CLI declined this tier's last fast ask: say why
      if (sub) sub.textContent = !can ? JUDGEFAST_SUB_OFF : (r ? judgeFastSubRefused(t.word, r) : JUDGEFAST_SUB);
    });
  }
  // the Default backend list is static (Claude Code, Codex); a saved default no longer offered (the retired terminal
  // backend) reads as Claude Code (effectiveDefaultBackend), and the facade paints from the select once at init
  if (bk) { bk.value = BN.effectiveDefaultBackend(load().backend); }
  repaintSelectPicks();
  // feed-colormap preview bar: a horizontal gradient of the SELECTED map's stops (mirrors render.ts COLORMAPS).
  var CMAPS = { aurora: [[84, 178, 4], [0, 180, 115], [35, 175, 156], [66, 169, 176], [25, 168, 201], [14, 164, 227], [74, 155, 241], [113, 145, 244], [144, 136, 240]],
    hawaii: [[140, 2, 115], [146, 46, 85], [151, 78, 62], [155, 111, 40], [156, 150, 28], [137, 189, 74], [107, 212, 142], [103, 233, 213], [179, 242, 253]],
    viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]],
    magma: [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]],
    inferno: [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]],
    plasma: [[13, 8, 135], [75, 3, 161], [125, 3, 168], [168, 34, 150], [203, 70, 121], [229, 107, 93], [248, 148, 65], [253, 195, 40], [240, 249, 33]],
    cividis: [[0, 34, 78], [33, 59, 110], [76, 85, 108], [108, 110, 114], [142, 137, 120], [177, 165, 112], [217, 197, 92], [254, 232, 56]] };
  var cmBtn = document.getElementById('rs-cmap-btn'), cmList = document.getElementById('rs-cmap-list');
  function cmStops(name) { return CMAPS[(name || '').toLowerCase()] || CMAPS.aurora; }   // the map by name, aurora the default as the chat's selectedStops has it (the settings default)
  function cmGrad(name) { var st = CMAPS[(name || '').toLowerCase()] || CMAPS.hawaii;
    return 'linear-gradient(to right,' + st.map(function (c) { return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')'; }).join(',') + ')'; }
  function cmPaint(name) { if (cmBtn) cmBtn.style.background = cmGrad(name);
    if (cmList) Array.prototype.forEach.call(cmList.children, function (o) { o.classList.toggle('sel', o.getAttribute('data-cmap') === name); }); }
  function cmBuild() { if (!cmList || cmList.children.length) return; Object.keys(CMAPS).forEach(function (name) {
    var o = document.createElement('div'); o.className = 'rs-cmap-opt'; o.setAttribute('data-cmap', name); o.title = name;
    o.style.background = cmGrad(name); o.addEventListener('click', function (e) { e.stopPropagation(); cmPick(name); }); cmList.appendChild(o); }); }
  function cmPick(name) { var s = load(); s.colormap = name; save(s); cmPaint(name); if (cmList) cmList.hidden = true;
    post({ type: 'setColormap', name: name }); }
  if (cmBtn) cmBtn.addEventListener('click', function (e) { e.stopPropagation(); cmBuild(); if (cmList) cmList.hidden = !cmList.hidden; });
  document.addEventListener('click', function (e) { var w = document.getElementById('rs-cmap');
    if (cmList && !cmList.hidden && w && !w.contains(e.target)) cmList.hidden = true; });
  // Session-colors palette picker: options + the active name come from /palette (the kernel is authoritative).
  var plBtn = document.getElementById('rs-pal-btn'), plList = document.getElementById('rs-pal-list'), plData = null, plActive = '';
  function plDots(cols) { return cols.map(function (c) { return '<span class=rs-pal-dot style="background:' + c + '"></span>'; }).join(''); }
  function plRow(pd) { return plDots(pd.colors) + '<span class=rs-pal-name>' + pd.label + '</span>'; }
  function plPaint() { if (!plData) return; plData.forEach(function (pd) { if (pd.name === plActive && plBtn) plBtn.innerHTML = plRow(pd); });
    if (plList) Array.prototype.forEach.call(plList.children, function (o) { o.classList.toggle('sel', o.getAttribute('data-pal') === plActive); }); }
  function plBuild() { if (!plList || !plData || plList.children.length) return; plData.forEach(function (pd) {
    var o = document.createElement('div'); o.className = 'rs-pal-opt'; o.setAttribute('data-pal', pd.name); o.title = pd.label;
    o.innerHTML = plRow(pd); o.addEventListener('click', function (e) { e.stopPropagation(); plPick(pd.name); }); plList.appendChild(o); }); }
  function plPick(name) { plActive = name; plPaint(); if (plList) plList.hidden = true;
    post({ type: 'setPalette', name: name }); }
  function plFill() { fetch(ku('/palette'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
    if (d && d.palettes) { plData = d.palettes; plActive = d.active || ''; plBuild(); plPaint(); } }).catch(function () {}); }
  if (plBtn) plBtn.addEventListener('click', function (e) { e.stopPropagation(); plBuild(); if (plList) plList.hidden = !plList.hidden; });
  document.addEventListener('click', function (e) { var w = document.getElementById('rs-pal');
    if (plList && !plList.hidden && w && !w.contains(e.target)) plList.hidden = true; });
  if (bk) bk.addEventListener('change', function () { var s = load(); s.backend = bk.value; save(s); });   // webview-local pref read at createSession time
  if (dd) dd.addEventListener('change', function () { var v = dd.value.trim(); var s = load(); s.defaultDir = v; save(s);
    post({ type: 'setDefaultDir', value: v }); });   // persist kernel-side: _default_create_dir reads this file FIRST
  var ddb = document.getElementById('rs-defaultdir-browse');
  if (ddb) ddb.addEventListener('click', function () { post({ type: 'browseDir', target: 'gear' }); });   // kernel-side native folder dialog
  // The kernel's browseResult (target 'gear') fills the field + persists via the change handler.
  // (This listener lives HERE, with the field — it used to sit in render.ts, a different document.)
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (m && m.type === 'browseResult' && m.target === 'gear' && typeof m.path === 'string' && dd) {
      dd.value = m.path; dd.dispatchEvent(new Event('change'));
    }
  });
  // A stood-down gesture must be visible to the dashboard that made it (2026-08-29). When a
  // settings gesture loses the ordering race (a frozen tab's queued flush, or a click here that a
  // newer pick elsewhere outranks), the kernel answers the DELIVERING socket with a settingStale
  // frame — without it the refusal was one kernel stderr line: this modal kept displaying the
  // refused pick as applied (fill() runs only on open), and with every kernel AGREEING on the
  // kept value the mixed marks show nothing. Event-keyed: the frame IS the deciding event — toast
  // it in plain words and re-read the kernel's actual values if the modal is up. No polling.
  var STALE_LABELS = { 'auto-nudge': 'Auto Nudge', 'compact-suggest': 'Suggest /compact', 'task-tracking': 'Task tracking',
    'file-editing': 'File editing',
    'update-mode': 'Automatic updates', 'judge-model': 'Triage model', 'judge-effort': 'Triage effort',
    'index-model': 'Indexing model', 'index-effort': 'Indexing effort', 'judge-concurrency': 'Judge concurrency',
    'distill-model': 'Distilling model', 'distill-effort': 'Distilling effort',
    'comment-model': 'Comment model', 'comment-effort': 'Comment effort',
    'comment-fast': 'Fast comment threads',
    'judge-fast': 'Fast mode (triage judges)', 'distill-fast': 'Fast mode (distilling judges)', 'index-fast': 'Fast mode (indexing judges)',
    'always-fast': 'Always fast', 'retry-upgrade': 'Retry upgrades after downgrades',
    'thinking-summaries': 'Thinking summaries', 'whole-chat-frames': 'Always load whole chats' };
  // store name → the message type that sets it: the whitelist for the toast's Apply anyway (a frame
  // may re-issue the one setting it names, nothing else) and the completeness pin's map
  // (gear.test.ts checks every emitter stamps through the clock under its own store name)
  var STALE_TYPE = { 'auto-nudge': 'setAutoNudge', 'compact-suggest': 'setCompactSuggest', 'task-tracking': 'setTaskTracking',
    'file-editing': 'setFileEditing', 'update-mode': 'setUpdateMode', 'thinking-summaries': 'setThinkingSummaries',
    'whole-chat-frames': 'setWholeChatFrames',
    'judge-model': 'setJudgeModel', 'judge-effort': 'setJudgeEffort',
    'index-model': 'setIndexModel', 'index-effort': 'setIndexEffort', 'judge-concurrency': 'setJudgeConcurrency',
    'distill-model': 'setDistillModel', 'distill-effort': 'setDistillEffort',
    'comment-model': 'setCommentModel', 'comment-effort': 'setCommentEffort', 'comment-fast': 'setCommentFast',
    'judge-fast': 'setJudgeFast', 'distill-fast': 'setDistillFast', 'index-fast': 'setIndexFast',
    'always-fast': 'setAlwaysFast', 'retry-upgrade': 'setRetryUpgrade' };
  // store name → the words its select shows for the sentinel options whose value is not the word. The
  // effort selects' Default is the EMPTY value (no effort flag), which read as no value at all, so a
  // refused Default pick drew the value-less copy and a plain Apply anyway — in the frozen-tab case, the
  // one pick the user most needs to see named (#967 review). Kept in step with paintChoices' option
  // lists: setting-stale.test.ts pins every literal sentinel there against this map.
  var STALE_WORDS = { 'judge-effort': { '': 'Default' }, 'index-effort': { '': 'Default' }, 'judge-concurrency': { '': 'Default' },
    'distill-model': { 'triage': 'Follow triage' }, 'distill-effort': { 'triage': 'Follow triage', 'none': 'Default' },
    'comment-model': { 'session': 'Same as the session', 'default': 'Default' }, 'comment-effort': { 'session': 'Same as the session' } };
  // Dismissal is the warn-toast FAMILY treatment (the user 2026-08-25: a notice with no visible
  // way out gets in the way — worst on touch, and this toast's mint site is a frozen phone tab
  // flushing on recovery): a visible ✕ in the chip-✕ dress, the whole toast still click-dismisses,
  // Escape clears the stack, and the fade precedes the auto-remove. COPIED from the family home
  // (render.ts warnToast + styles.css .warn-toast-x) because the gear is its own document, loaded
  // by panes that ship only this sheet — keep the two in step (setting-stale.test.ts pins this copy).
  function staleToast(text, act) {
    var box = document.getElementById('rs-stale-toasts');
    if (!box) {   // created once; dismissal delegated to the STABLE container (click-safe rule)
      box = document.createElement('div'); box.id = 'rs-stale-toasts';
      box.addEventListener('click', function (e2) {
        var t = e2.target;
        while (t && t !== box && !(t.classList && t.classList.contains('rs-stale-toast'))) t = t.parentNode;
        if (t && t !== box) t.remove();
      });
      // Escape clears the stack, additively — never stopPropagation: clearing a toast is
      // noise-removal, not a key any other surface loses (family rule, render.ts warnToast)
      document.addEventListener('keydown', function (e2) {
        if (e2.key === 'Escape') Array.prototype.slice.call(box.children).forEach(function (w) { w.remove(); });
      });
      document.body.appendChild(box);
    }
    var t = document.createElement('div');
    t.className = 'rs-stale-toast'; t.setAttribute('role', 'status'); t.title = 'click to dismiss';
    var txt = document.createElement('span');
    txt.className = 'rs-stale-toast-msg'; txt.textContent = text;
    t.appendChild(txt);
    if (act) {   // the toast's one action: a real button whose click also bubbles to the container's
      var b = document.createElement('button');   // delegated dismiss, so acting clears the toast too
      b.type = 'button'; b.className = 'rs-stale-toast-act'; b.textContent = act.label;
      b.title = act.title;   // its own tooltip: the toast's reads "click to dismiss", which the button is not
      b.addEventListener('click', act.run);
      t.appendChild(b);
    }
    var x = document.createElement('button');
    x.className = 'rs-stale-toast-x'; x.setAttribute('aria-label', 'Dismiss'); x.title = 'dismiss (Esc)';
    x.textContent = '✕';   // clicks bubble to the container's delegated dismiss, like the family's
    t.appendChild(x);
    box.appendChild(t);
    setTimeout(function () { t.classList.add('fade'); }, 11000);   // the family fade first…
    setTimeout(function () { t.remove(); }, 12000);   // …then the self-clearing backstop; a click/Esc dismisses sooner
    return t;
  }
  // The copy names the setting, the value that was refused, the kernels that refused it and the value
  // they kept, and says the pick was not applied. The refused value matters in the frozen-tab case: the
  // pick is hours old and one click on Apply anyway sends it to every linked kernel, so the user must
  // see what they would be applying (#945 review). It does NOT say who changed the setting, nor that
  // the kept pick came after this one: the kernel knows only that it holds a larger stamp, and with
  // device clocks in the mix that is not the same as "someone changed it after you" (#879 review; the
  // #945 review retired the copy's recency claim on the same ground). The hosts ARE known: each
  // refusal came from one of them. Either value may be absent (an older kernel echoes no gesture and
  // sends no kept value); the copy drops that clause.
  function staleText(label, refused, kept, hosts) {
    return label + ': ' + (refused ? refused + ' was not applied on ' : 'not applied on ') + hosts.join(', ') + '.'
      + (kept ? ' Keeping ' + kept + '.' : '');
  }
  // Values read as words: a boolean toggle's on/off, a select's sentinel by the name the select shows for
  // it (STALE_WORDS, per setting), any other string as itself, anything else as absent.
  function staleWord(v, setting) {
    var words = STALE_WORDS[setting];
    if (words && typeof v === 'string' && Object.prototype.hasOwnProperty.call(words, v)) return words[v];
    return v === true ? 'on' : v === false ? 'off' : (typeof v === 'string' && v ? v : '');
  }
  // The value the refused gesture carried. Every emitter posts {type, <one value field>, gt} and the
  // kernel echoes it without gt, so the value is the echo's one key beside type — read generically
  // rather than through a third type→field map kept in step with STALE_TYPE. Trusted only for the
  // setting the frame names (the whitelist Apply anyway uses: an echo for another setting is not this
  // setting's value); an echo of any other shape (two value fields, none, or no echo from an older
  // kernel) reads as no value, and the copy and the label take their value-less form.
  function staleRefused(m) {
    if (!m.gesture || typeof m.gesture !== 'object' || STALE_TYPE[m.setting] !== m.gesture.type) return '';
    var keys = Object.keys(m.gesture).filter(function (k) { return k !== 'type'; });
    return keys.length === 1 ? staleWord(m.gesture[keys[0]], m.setting) : '';
  }
  // One toast per refused GESTURE, not per refusing kernel: a dashboard's broadcast reaches every
  // linked kernel, so one stale flush used to draw N identical toasts naming no host. The fold key
  // is the gesture's identity — setting + its own gt, which the frame carries — an event key, never
  // a time window: N kernels refusing one flush share it, two different gestures never do. A frame
  // without gt (an older kernel) keeps one toast per frame. Liveness is read at lookup — the node's
  // parentNode, and not yet fading: a toast in its fade→remove second sits at opacity 0, so a frame
  // written into it would never be seen (#945 review) — so a dismissed, expired or fading toast never
  // absorbs a later frame, and still no cleanup rides the timers. A remote kernel's frame arrives
  // host-stamped (federation.ts prefixInbound); the local kernel's has no host and reads as this
  // machine, the name the gear already uses for it.
  var staleOpen = {};   // 'setting:gt' → { t: the toast node, hosts: [...], refused: the value in words } while the toast is up
  function staleHost(m) { return (typeof m.host === 'string' && m.host) ? m.host : 'this machine'; }
  // The kernel's reason a write was refused OUTRIGHT as a clause for the copy: its file could not be READ, or
  // (a `why` starting "write failed:", the fold on PR #1019) WRITTEN — one clause per cause; absent on a stand-down
  function staleWhy(m) { return (typeof m.why === 'string' && m.why) ? ' Its settings file could not be ' + (m.why.indexOf('write failed:') === 0 ? 'written' : 'read') + ' (' + m.why + ').' : ''; }
  function staleLive(t) { return !!t.parentNode && !(t.classList && t.classList.contains('fade')); }
  // Apply anyway re-issues the frame's echoed gesture as a NEW one, stamped above everything this
  // page has seen (the frame's storedGt included, learned just before): a fresh click is legitimate
  // new information, the event the ordering rule wants — never a clock heuristic. Offered only when
  // the echo's type is the setting the frame names, so a frame from any linked kernel may re-issue
  // that one setting and nothing else; an older kernel sends no echo and the toast shows without it.
  // The label names the value one click would apply everywhere (the frozen-tab case again), and the
  // button carries its own title: without one it inherited the toast's click-to-dismiss tooltip.
  function staleAction(m, refused) {
    if (!m.gesture || typeof m.gesture !== 'object' || STALE_TYPE[m.setting] !== m.gesture.type) return null;
    return { label: refused ? 'Apply ' + refused + ' anyway' : 'Apply anyway',
             title: 'apply ' + (refused || 'this pick') + ' on every linked kernel, replacing the value they kept',
             run: function () { post(Object.assign({}, m.gesture, { gt: gclock.stamp(m.setting) })); } };
  }
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (!m || m.type !== 'settingStale') return;
    gclock.learn(m.setting, m.storedGt);   // the frame IS new information about that store's clock
    var label = STALE_LABELS[m.setting] || String(m.setting || 'A setting');
    var kept = staleWord(m.kept, m.setting);
    var refused = staleRefused(m);
    var key = typeof m.gt === 'number' ? m.setting + ':' + m.gt : '';
    var live = key && staleOpen[key] && staleLive(staleOpen[key].t) ? staleOpen[key] : null;
    var where = staleHost(m);
    // A write the kernel refused because it could not READ the setting's file (the frame carries `why`) is
    // not an ordering race: re-issuing the gesture cannot succeed while the file is unreadable, so that
    // toast offers no Apply anyway and says why instead (review find on #1018, 2026-09-08)
    var why = staleWhy(m);
    if (live) {   // the same gesture, refused by one more kernel: add the host to the toast on screen
      if (live.hosts.indexOf(where) < 0) live.hosts.push(where);
      live.t.querySelector('.rs-stale-toast-msg').textContent = staleText(label, live.refused || refused, kept, live.hosts) + why;
    } else {
      var t = staleToast(staleText(label, refused, kept, [where]) + why, why ? null : staleAction(m, refused));
      if (key) staleOpen[key] = { t: t, hosts: [where], refused: refused };
    }
    if (!p.hidden) fill();   // the open modal re-reads the kernel's values so it stops showing the refused pick
  });
  // The model/effort <option>s come from /models — the same single source the
  // chat + timeline pickers use. Cached after the first successful fetch.
  var choices = null, choicesRev = -1;   // the list, and the highest /models `rev` applied to it
  // A /models response is applied only if it is not OLDER than one already applied: its `rev` is the
  // pick memory's revision — the models frame's counter — and the frame's re-read can overlap the first
  // fill, or two quick frames each other, with the responses landing out of order; without the check the
  // STALE list won until the next change. A payload without a rev (an older kernel) always applies.
  // Returns whether `choices` moved.
  function adoptChoices(d) {
    if (d && typeof d.rev === 'number') { if (d.rev < choicesRev) return false; choicesRev = d.rev; }
    choices = d || { models: [], efforts: [] };
    return true;
  }
  // The ONE write path for kernel-backed selects — fill()'s (the user 2026-09-01, whose stored distill
  // pick displayed as the default) and paintChoices': a value the option list doesn't carry is INJECTED
  // as a marked option and selected — the row shows the truth (an off-list value, named) rather than
  // silently falling to its first option or, on a repaint, to NOTHING: per the spec a select assigned a
  // value none of its options carries deselects every option (value ''), so a repaint that handed the
  // held value straight back would leave the select blank, and the version menu's label with it,
  // whenever the value was one fill() had injected or a learned version that has since left the list.
  // Values are validated at write time by the kernel, so an injection here means THIS page's list is
  // behind the store — worth seeing, never worth hiding.
  function setShow(sel, val) {
    if (!sel) return;
    if (val && !Array.prototype.some.call(sel.options, function (o) { return o.value === val; })) {
      var o = document.createElement('option');
      o.value = val;
      o.textContent = val + " — not in this kernel's list";
      sel.appendChild(o);
    }
    sel.value = val;
  }
  // Write every select's <option>s from the CURRENT list — whichever response won — and give each select
  // back the value it held, through setShow: plainly when the list still offers it, as a marked off-list
  // option when it no longer does. Rewriting a select's options resets it to its first option, which is
  // why a re-read alone could never repaint; and a page-load fill that skipped its paint because the
  // frame's re-read had applied a newer list first left every later fill() short-circuiting on the cache
  // — eight empty pickers for the life of the page. The paint keys on the list, not on which fetch
  // carried it.
  function paintChoices() {
    var mo = (choices.models || []).map(function (m) {
      var vs = (m.versions || []).map(function (v) { return '<option value="' + v.value + '">' + v.label + '</option>'; }).join('');
      return '<option value="' + m.value + '">' + m.label + '</option>' + vs;   // versions ride as options too — the hidden select stays the value holder for any pick
    }).join('');
    var eff = (choices.efforts || []).map(function (m) { return '<option value="' + m.value + '">' + m.label + '</option>'; }).join('');
    var eo = '<option value="">Default</option>' + eff;
    // judge concurrency: the kernel's exact range (1..16, its _CONCURRENCY_VALUES) behind the same Default
    // sentinel — the empty value clears the setting back to the variable, else 6 (T277)
    var co = '<option value="">Default</option>';
    for (var ci = 1; ci <= 16; ci++) co += '<option value="' + ci + '">' + ci + '</option>';
    function put(sel, html) {
      if (!sel) return;
      var held = sel.value;
      sel.innerHTML = html;
      if (held) setShow(sel, held);   // a held value the new list lacks stays, marked — never a blank select
    }
    put(jm, mo); put(im, mo);
    put(je, eo); put(ie, eo);
    put(jc, co);
    // the distilling pair leads with the follow-triage sentinel — its default, so a fresh kernel
    // shows "Follow triage" rather than a model nobody picked. Its Default (no effort flag) is the
    // stored sentinel "none", never "" — an empty state file reads back as the default ("follow").
    put(dm, '<option value="triage">Follow triage</option>' + mo);
    put(de, '<option value="triage">Follow triage</option><option value="none">Default</option>' + eff);
    // the comment pair leads with the same-as-the-session sentinel — its default, so a fresh
    // kernel shows the inherit behavior, not a model nobody picked; "default" = the account default
    put(cmm, '<option value="session">Same as the session</option><option value="default">Default</option>' + mo);
    put(cme, '<option value="session">Same as the session</option>' + eff);
  }
  // The promise is the memo, not the list: a settings open racing the page-load fetch used to fire a
  // SECOND /models fetch, and whichever resolved last rewrote every select's options after fill() had
  // set their values. One live fetch per page; a failed one clears the memo for retry.
  var choicesP = null;
  function fillChoices() {
    if (choicesP) return choicesP;
    choicesP = fetch(ku('/models'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      adoptChoices(d);   // a frame's re-read may have applied a NEWER list while this one was in flight: paint from whichever won
      paintChoices();
      return choices;
    }).catch(function () { choicesP = null; return null; });   // retry on the next open, never a second live fetch
    return choicesP;
  }
  // The kernel's models frame: the pick memory moved — a version pinned, a family un-pinned by Latest, a
  // refused pin dropped, from any surface or dashboard — or the catalog grew, so the cached list's
  // `default` (what a family row SENDS, read from `choices` at click time) is stale. Re-read on the
  // event, like the browseResult listener above; never a poll. The list moving repaints the selects too
  // (paintChoices keeps their values), so a frame that lands before the page-load fill has painted still
  // leaves populated pickers. The frame reaches this document because the kernel sends it to the FEED
  // app too (the gear lives in the feed bundle).
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (!m || m.type !== 'models') return;
    fetch(ku('/models'), { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) { if (d && Array.isArray(d.models) && adoptChoices(d)) paintChoices(); }).catch(function () {});
  });
  function lv() { var t = document.querySelector('script[src*="feed.js"]');
    var m = t && t.getAttribute('src').match(/[?&]v=(\d+)/); return m ? +m[1] : 0; }
  function clearAutoNudgeSplit() {
    if (an) an.indeterminate = false;
    if (ans) { ans.hidden = true; ans.textContent = ''; }
    if (asub) asub.textContent = AUTONUDGE_SUB;
  }
  // Auto Nudge is one switch for every connected machine, but each kernel keeps its own copy — and
  // /version answers for THIS one alone. So the box takes the local kernel's setting, then checks the
  // others: a connected host that disagrees puts the box in the mixed state (a tri-state checkbox plus
  // the word beside the label — glanceable) and is NAMED in the row's permanent line under the label (T408), one level down. Before this
  // the box quietly spoke for machines it could not see, and the other kernel went on nudging for days
  // behind an unchecked box (the user 2026-08-14). Clicking a mixed box picks one answer for everyone,
  // since the post goes to every kernel.
  //
  // A host that never reported a setting — an older kernel, a row that has not polled — is left OUT
  // rather than read as off: guessing would invent a disagreement and invite a click that changes a
  // machine nobody asked about. Same for a host that is not `up`, whose row is a memory (see
  // _remote_public's stale note). A /tunnels that fails leaves the local answer standing.
  function fillAutoNudge(mine, rows) {
    if (!an) return;
    an.checked = !!mine;
    clearAutoNudgeSplit();
    var split = (rows || []).filter(function (t) {
      return t && t.status === 'up' && typeof t.autoNudge === 'boolean' && t.autoNudge !== !!mine;
    }).map(function (t) { return t.host; });
    if (!split.length) return;
    an.indeterminate = true;
    if (ans) { ans.textContent = 'mixed'; ans.hidden = false; }
    if (asub) asub.textContent = AUTONUDGE_SUB
      + (mine ? ' Right now these have it off: ' : ' Right now these still have it on: ')
      + split.join(', ') + '. Clicking sets them all the same way.';
  }
  // The autoNudge rule, generalized to EVERY kernel-side select (the user 2026-08-14): the control keeps
  // showing the LOCAL kernel's value, and a small "mixed" mark appears when a connected, up, REPORTING
  // machine disagrees — hover names the hosts. A machine that never reported (older kernel, unpolled row)
  // is unknown, never a disagreement: guessing would invite a click that changes a machine nobody asked
  // about. One pick posts to every kernel (KERNEL_SETTING) and the next fill clears the mark — the local
  // value is the default ANSWER to confirm, never a silent overwrite of a remote's deliberate setting.
  function fillMixedMarks(v, rows) {
    var mine = (v && v.settings) || null;
    [['updateMode', upm], ['judgeModel', jm], ['judgeEffort', je], ['indexModel', im],
     ['indexEffort', ie], ['judgeConcurrency', jc], ['distillModel', dm], ['distillEffort', de], ['fileEditing', fe],
     ['compactSuggest', csg], ['taskTracking', tk],
     ['commentModel', cmm], ['commentEffort', cme], ['commentFast', cmf],
     ['judgeFast', jf], ['distillFast', df], ['indexFast', xf],
     ['alwaysFast', afb], ['retryUpgrade', rub]].forEach(function (pair) {
      var key = pair[0], el = pair[1];
      if (!el) return;
      // the mark nearest the control: a checkbox's own <label> (the fast-mode box shares the Triage model
      // row, whose first mark belongs to the picker), else the row
      var row = el.closest ? (el.closest('label') || el.closest('.rs-row')) : null;
      var mark = row ? row.querySelector('.rs-mixed') : null;
      if (mark) { mark.hidden = true; mark.textContent = ''; mark.removeAttribute('title'); }
      if (!mine || typeof mine[key] === 'undefined' || !mark) return;
      var split = (rows || []).filter(function (t) {
        return t && t.status === 'up' && t.settings && typeof t.settings[key] !== 'undefined'
          && String(t.settings[key]) !== String(mine[key]);
      }).map(function (t) { return t.host; });
      if (!split.length) return;
      mark.textContent = 'mixed';
      mark.title = 'differs on: ' + split.join(', ') + ' — picking here sets every machine the same way';
      mark.hidden = false;
    });
  }
  // setShow — fill()'s write path for every kernel-backed select below — sits beside paintChoices, which
  // shares it.
  function fill() { fillChoices().then(function () { return fetch(ku('/version'), { cache: 'no-store' }); }).then(function (r) { return r.json(); }).then(function (v) {
    gclock.learnAll(v.settingsGt);   // each store's last-applied stamp: the clock climbs above them (an older kernel sends none)
    // ONE /tunnels fetch feeds every cross-machine comparison: the autoNudge box and the select marks.
    // A failed /tunnels leaves the local answers standing, unmarked — same fallback as before.
    fetch(ku('/tunnels'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      var rows = (d && d.tunnels) || [];
      fillAutoNudge(v.autoNudge, rows);
      fillMixedMarks(v, rows);
    }).catch(function () { fillAutoNudge(v.autoNudge, []); fillMixedMarks(v, []); });
    if (ths) ths.checked = !!v.thinkingSummaries;   // per-install opt-in: this kernel's persisted answer is authoritative
    if (wcf) wcf.checked = !!v.wholeChatFrames;     // the Whole chat frames switch: the same per-install rule
    if (tk) { tk.checked = v.taskTracking !== false; dressTracking(tk.checked); }   // the master switch (T404): absent reads on
    if (fe) fe.checked = !!v.fileEditing;   // the kernel's persisted opt-in is authoritative (see the viewer's consent popup)
    if (cvm) cvm.checked = !!v.conserveMemory;   // T148: the kernel's persisted conserve flag is authoritative
    if (csg) csg.checked = !!v.compactSuggest;   // T208+: the kernel's persisted opt-in is authoritative
    lgRender(v);   // the Billing login block (T157) rides the same /version read
    lgFetchLogins();   // …and the stored logins beside it (T346), from the authed /logins
    if ((v.login || {}).state && !lgTimer) lgTimer = setTimeout(lgPoll, 1500);   // a flow mid-run resumes polling
    if (typeof v.updateMode === 'string') setShow(upm, v.updateMode);   // the kernel's persisted mode is authoritative
    if (typeof v.judgeModel === 'string') setShow(jm, v.judgeModel);   // the judge's ACTUAL current model/effort per tier is authoritative
    if (typeof v.indexModel === 'string') setShow(im, v.indexModel);
    if (typeof v.judgeEffort === 'string') setShow(je, v.judgeEffort);
    if (typeof v.indexEffort === 'string') setShow(ie, v.indexEffort);
    if (typeof v.judgeConcurrency === 'string') setShow(jc, v.judgeConcurrency);   // RAW: "" selects Default (the variable, else 6)
    if (typeof v.distillModel === 'string') setShow(dm, v.distillModel);   // RAW: "triage" selects the Follow-triage option
    if (typeof v.distillEffort === 'string') setShow(de, v.distillEffort);
    if (typeof v.commentModel === 'string') setShow(cmm, v.commentModel);   // RAW: "session" selects Same as the session
    if (typeof v.commentEffort === 'string') setShow(cme, v.commentEffort);
    if (cmf && typeof v.commentFast === 'string') cmf.checked = v.commentFast === 'on';
    if (jf && typeof v.judgeFast === 'string') jf.checked = v.judgeFast === 'on';   // RAW on/off: the kernel's persisted answer
    if (df && typeof v.distillFast === 'string') df.checked = v.distillFast === 'on';
    if (xf && typeof v.indexFast === 'string') xf.checked = v.indexFast === 'on';
    if (afb && typeof v.alwaysFast === 'string') afb.checked = v.alwaysFast === 'on';   // RAW on/off: the kernel's persisted answer (2026-09-17)
    if (rub && typeof v.retryUpgrade === 'string') rub.checked = v.retryUpgrade === 'on';
    fastRefused = (v.fastRefused && typeof v.fastRefused === 'object') ? v.fastRefused : {};
    cmtFastGate(false);
    judgeFastGate();   // the tiers are set above; the boxes follow them
    if (dd && typeof v.defaultDir === 'string') dd.value = v.defaultDir;   // the kernel's persisted default is authoritative
    // Browse… draws on the KERNEL's screen, and a kernel with no desktop has none — the click used to
    // vanish into a macOS-only dialog. Drop the button rather than offer one that cannot work; the
    // field takes a typed path, which is what that machine has. An older kernel sends no verdict and
    // keeps the button it always had.
    if (ddb && typeof v.nativeDialogs === 'boolean') ddb.style.display = v.nativeDialogs ? '' : 'none';
    repaintSelectPicks();   // fill() writes sel.value directly (no change event) — the closed rows follow
    var x = lv(); b.innerHTML = 'kernel ' + (v.kernel_sha || '?') + '\nserving v' + v.dist_ver + '\nthis tab v' + (x || '?');
  }).catch(function () { b.textContent = '(version unavailable)'; }); }
  // The settings modal is full-WINDOW in the web shell — ask it to lift the iframe hosting this modal
  // (the shell's #f-settings since 2026-09-10; the feed iframe before) while open (no-op elsewhere:
  // VS Code's feed panel IS the window).
  function feedFull(on) { try { if (window.parent !== window) window.parent.postMessage({ romp: 'settings', on: !!on }, '*'); } catch (e) {} }
  // While lifted, pin the BODY to the feed pane's old screen rect and keep painting (rs-lifted +
  // --pane-* vars), so the feed stays exactly where it was — live and visible under the dim like every
  // other pane — instead of leaving a black hole where its pane had been (the user 2026-08-08; same
  // technique as the chat picker's placeLifted). A pane we can't measure (hidden pane, or a
  // cross-origin parent like VS Code) falls back to hiding the feed's content (rs-pane-gone). The
  // measurement retries a few frames: opening from a hidden feed pane, the shell's settings-open class
  // (which forces the pane visible) lands only after the postMessage round-trip.
  function paneRect() { try { var el = window.parent !== window ? window.parent.document.getElementById('feed-pane') : null;
    return el ? el.getBoundingClientRect() : null; } catch (e) { return null; } }
  function placeLifted(tries) {
    if (p.hidden || !document.body.classList.contains('rs-lifted')) return;   // closed while retrying
    var r = paneRect(), gone = !r || r.width < 40 || r.height < 40;
    document.body.classList.toggle('rs-pane-gone', gone);
    if (!gone) { var st = document.documentElement.style;
      st.setProperty('--pane-x', r.left + 'px'); st.setProperty('--pane-y', r.top + 'px');
      st.setProperty('--pane-w', r.width + 'px'); st.setProperty('--pane-h', r.height + 'px'); }
    else if (tries > 0) requestAnimationFrame(function () { placeLifted(tries - 1); });
  }
  function onRsResize() { placeLifted(0); }   // panes track the window; follow them while open
  function clearPaneVars() { var st = document.documentElement.style;   // a stale rect from THIS open must not
    ['--pane-x', '--pane-y', '--pane-w', '--pane-h'].forEach(function (k) { st.removeProperty(k); }); }   // place the NEXT one (the user 2026-08-09)
  function setModalCls(on) { var de = document.documentElement, m = 'rs-modal-open';
    if (on) { de.classList.add(m); document.body.classList.add(m);
      if (window.parent !== window && !ownPage) { document.body.classList.add('rs-lifted'); placeLifted(5); window.addEventListener('resize', onRsResize); } }
    else { de.classList.remove(m); document.body.classList.remove(m);
      document.body.classList.remove('rs-lifted'); document.body.classList.remove('rs-pane-gone');
      clearPaneVars();
      window.removeEventListener('resize', onRsResize); } }
  // THE POPOVER STAYS INSIDE THE CARD (the T408 read, 2026-09-13): a row's hover description is absolutely positioned under the
  // row inside the card, the modal's one scroll box, so a row near the card's bottom sent it past the edge, the card grew a
  // scrollbar for it and clipped it (four rows measured 23 to 47 px past). On hover the row measures where its popover ends;
  // past the card's visible bottom, with room above the row, the row wears rs-up and the popover opens above it (gear.css).
  // Measured per hover, since the card scrolls and the rows move; dropped when the pointer leaves, so the next hover measures afresh.
  var pcard = document.querySelector('#rsettings .rs-card');
  // the HOST a hover belongs to: the closest of a Fast mode box, a row or a widget row, so a hover on the box is the box's, not
  // its judge row's (round two, the medium: the rule climbed to the row, read the row's popover, which the sheet hides while the
  // box is hovered, and returned on its zero height; no Fast mode popover ever went up)
  var HOSTS = '#rsettings .rs-fastin, #rsettings .rs-row, #rsettings .rs-widget';
  function hostOf(t) { return t && t.closest ? t.closest(HOSTS) : null; }
  function ownSub(host) {   // the popover the host OWNS: a row's own, never the Fast mode box's nested inside it; the box's own for the box
    var subs = host.querySelectorAll('.rs-sub');
    for (var i = 0; i < subs.length; i++) { if (subs[i].closest(HOSTS) === host) return subs[i]; }
    return null;
  }
  function placeSub(host) {
    var sub = ownSub(host);
    if (!sub || !pcard) return;
    host.classList.remove('rs-up');
    var sr = sub.getBoundingClientRect(), cr = pcard.getBoundingClientRect();
    if (!sr.height) return;   // no popover shown (a picker open, the mixed mark hovered): nothing to place
    if (sr.bottom <= cr.bottom) return;   // it fits below: the default stands
    // the room above is measured from what bottom:100% resolves against: the ROW, for a Fast mode box too, since the box is
    // static and its popover's containing block is the row (round three, low 3: measuring the box left 5 px of slack)
    var anchor = host.classList.contains('rs-fastin') ? (host.closest('#rsettings .rs-row') || host) : host;
    var ar = anchor.getBoundingClientRect(), above = ar.top - cr.top;
    // above only when it fits there. When it fits on neither side (a very short window) the popover stays BELOW, as main had
    // it (round three, the manager's ruling): a bottom clip is reachable by the card's scroll, a top clip is not
    if (above >= sr.height + 2) host.classList.add('rs-up');
  }
  if (pcard) {
    pcard.addEventListener('mouseover', function (e) { var host = hostOf(e.target); if (host) placeSub(host); });
    pcard.addEventListener('mouseout', function (e) { var host = hostOf(e.target); if (host && !(e.relatedTarget && host.contains(e.relatedTarget))) host.classList.remove('rs-up'); });
  }
  function closeSettings() { endDrags(); if (raBack && !raBack.hidden) raHide(); clearSectionScroll(); p.hidden = true; setModalCls(false); feedFull(false); }   // the reset FIRST, while the card still has a layout: a hidden card ignores a scroll write and keeps its old offset for the next open (measured); a pending section ask dies with the panel (round two, LOW 2 and 7)
  function openSettings(tab, section) {
    if (raBack && !raBack.hidden) raHide();   // the Token usage panel up: down first, so the card is what this open shows, never the card under the layer (the read of the panel's close fix)
    if (tab === 'appearance' && !section) section = 'appearance';   // the former Appearance tab is General's section (T404)
    if (!p.hidden) { if (knownTab(tab)) { selectTab(tab); if (section) showSection(section); else clearSectionScroll(); return; } closeSettings(); return; }   // the opener toggles the modal; a named tab on an open panel switches to it, and to its section (T379)
    selectTab(tab);
    // Signal the SHELL first, then measure (the picker's order, adopted 2026-08-09): feedFull posts
    // settings-open, which is what un-hides #feed-pane when the feed is toggled off — measuring first
    // burned the whole 5-frame retry against a display:none pane, latched rs-pane-gone, and the
    // full-viewport fallback box blacked out every pane behind the modal.
    try { if (window.parent !== window) window.parent.postMessage({ romp: 'logUnseenQuery' }, '*'); } catch (e) { /* no shell to ask */ }   // T290: the Open log count
    p.hidden = false; feedFull(true); setModalCls(true); var s = load(); cc.checked = !!s.compact; tl.checked = !!s.tabsLocked; jix.checked = (s.showIndexJudges !== undefined ? !!s.showIndexJudges : !!s.debug); jtr.checked = (s.showTriageJudges !== undefined ? !!s.showTriageJudges : !!s.debug); if (sr) sr.checked = s.stripGroupRows !== false; if (dn) dn.checked = s.denseChrome === true; if (fsc) fsc.checked = (s.showFilesControl === true); (function (p) { Object.keys(pn).forEach(function (k) { if (pn[k]) pn[k].checked = p[k]; }); })(panesOf(s)); tcPaint(); paintWidgets(); csPaint(); ttPaint(); if (fc) fc.checked = s.collapsed === true; cmBuild(); cmPaint(s.colormap || 'aurora'); if (bk) { bk.value = BN.effectiveDefaultBackend(s.backend); repaintSelectPicks(); } if (dd) dd.value = s.defaultDir || ''; plFill(); fill(); if (section) showSection(section); else clearSectionScroll(); }
  if (g) g.onclick = function (e) { e.stopPropagation(); openSettings(); };   // hidden anchor; hosts open via the message below
  window.addEventListener('message', function (e) { if (e.data && e.data.romp === 'openSettings') openSettings(typeof e.data.tab === 'string' ? e.data.tab : undefined, typeof e.data.section === 'string' ? e.data.section : undefined); });   // the tab and its section ride the ask (T379: the strip's gear opens Chat at Tab widgets)
  // Escape, relayed by the web shell's Escape chain (_LANDING_ESC_JS captures keydown in this same-origin
  // document and calls this synchronously): close the modal and say so, unless one of its own dialogs is up
  // (the login card, an open house dropdown), which the document's own Escape handlers close one level at a
  // time; the shell then leaves the press alone. A cross-origin host (VS Code) cannot reach this and has no chain.
  window.__rompSettingsClose = function () { if (raBack && !raBack.hidden) { raHide(); return true; } if (p.hidden || (lgM && !lgM.hidden) || openHousePick || widgetDrag) return false; closeSettings(); return true; };   // the Token usage panel is one level: the shell's chain asks this whenever settings-open stands, so a press with the keyboard in the shell document takes the layer down and returns to the card (the panel's own document-local Escape below serves the standalone page, where no shell asks)   // one Escape level at a time: a drag in flight takes it (the shell's handler runs before the drag's own, so this is where it yields)
  // The shortcuts row: the web shell (same-origin parent) gets the customize link — it opens the
  // shell's shortcuts dialog and closes this modal so the two never stack; VS Code (cross-origin
  // parent) gets the pointer at its own Keyboard Shortcuts editor instead (the user 2026-08-09).
  (function () {
    var web = false;
    try { web = window.parent !== window && !!window.parent.document; } catch (e) { web = false; }
    var wrow = document.getElementById('rs-keys-web'), vrow = document.getElementById('rs-keys-vsc');
    if (wrow) wrow.hidden = !web;
    if (vrow) vrow.hidden = web;
    var kb2 = document.getElementById('rs-keys-btn');
    if (kb2) kb2.onclick = function () { closeSettings(); try { window.parent.postMessage({ romp: 'openKeys' }, '*'); } catch (e) { /* no shell to ask */ } };
    // "Open log" (T290, the user 2026-09-09): the Log left the bottom bar; this button, the last row of Updates &
    // debug, opens the shell's Log panel (a centered modal over the dimmed dashboard, the panels rule). The modal
    // closes first so the two never stack. Web shell only: VS Code's cross-origin parent has no Log panel.
    var lg = document.getElementById('rs-log-open');
    if (lg) { lg.hidden = !web; lg.onclick = function () { closeSettings(); try { window.parent.postMessage({ romp: 'openLog' }, '*'); } catch (e) { /* no shell to ask */ } }; }
    // the unread count the bar's opener used to draw (T290): the shell posts {romp:'logUnseen', n} on every
    // repaint of its Log and answers {romp:'logUnseenQuery'}; the label reads "Open log · N" (9+ past nine)
    var lgn = lg ? lg.querySelector('.rs-log-n') : null;
    window.__rompSetLogCount = function (n) {
      if (!lgn) return;
      n = Number(n) || 0;
      lgn.hidden = n <= 0;
      lgn.textContent = n <= 0 ? '' : ' \u00b7 ' + (n > 9 ? '9+' : String(n));
    };
    window.addEventListener('message', function (e) { var m = e.data; if (m && m.romp === 'logUnseen') window.__rompSetLogCount(m.n); });
  })();
  p.addEventListener('click', function (e) { if (e.target === p) closeSettings(); });   // click the dimmed backdrop (not the card) → close
  document.addEventListener('click', function (e) { if (!p.hidden && e.target !== g && !p.contains(e.target)) closeSettings(); });
  // ── token-usage analytics modal: a sessions-vs-judges bar chart over a selectable window ──
  var raBack = document.getElementById('ranalytics-back'), raOpen = document.getElementById('ra-open'),
    raClose = document.getElementById('ra-close'), raChart = document.getElementById('ra-chart'),
    raLegend = document.getElementById('ra-legend'), raNote = document.getElementById('ra-note');
  var JCOL = { captioner: '#1EA1EB', archiver: '#54B204', planner: '#E0B020', grouper: '#4EA8A9', closer: '#C0392B', distiller: '#D26EA8', courier: '#9088F0' };
  var JORDER = ['captioner', 'archiver', 'planner', 'grouper', 'closer', 'distiller', 'courier'];
  var TIERCOL = { index: '#3FA7C4', triage: '#E0973A' };
  var raState = { window: 86400, periodLabel: '24h', group: 'judge', metric: 'tokens', data: null, loading: false };
  function fmtTok(n) { n = n || 0; if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M'; if (n >= 1e3) return Math.round(n / 1e3) + 'k'; return '' + n; }
  function fmtUsd(v) { v = v || 0; return v >= 100 ? '$' + v.toFixed(0) : (v >= 1 ? '$' + v.toFixed(2) : '$' + v.toFixed(3)); }
  function raCost() { return raState.metric === 'cost'; }
  function raVal(o) { return raCost() ? (o.cost || 0) : ((o.in || 0) + (o.out || 0)); }
  function raFmt(v) { return raCost() ? fmtUsd(v) : fmtTok(v); }
  function raEsc(s) { return (s == null ? '' : '' + s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function raSegments() { var j = (raState.data && raState.data.judges) || { byJudge: {}, byTier: {} }; var segs = [];
    if (raState.group === 'tier') { ['index', 'triage'].forEach(function (k) { var bt = (j.byTier || {})[k] || {};
      segs.push({ label: k === 'index' ? 'index (captioner+archiver)' : 'triage (planner/grouper/closer/distiller/courier)', color: TIERCOL[k], in: bt.in || 0, out: bt.out || 0, calls: bt.calls || 0, cost: bt.cost || 0 }); }); }
    else { JORDER.forEach(function (k) { var bj = (j.byJudge || {})[k]; if (bj) segs.push({ label: k, color: JCOL[k] || '#888', in: bj.in || 0, out: bj.out || 0, calls: bj.calls || 0, cost: bj.cost || 0 }); });
      Object.keys(j.byJudge || {}).forEach(function (k) { if (JORDER.indexOf(k) < 0 && k !== '?') { var bj = j.byJudge[k]; segs.push({ label: k, color: '#888', in: bj.in || 0, out: bj.out || 0, calls: bj.calls || 0, cost: bj.cost || 0 }); } }); }
    return segs.filter(function (s) { return (s.in + s.out) > 0; }); }
  function raRender() {
    if (raState.loading) { raChart.innerHTML = '<div class=ra-empty>loading…</div>'; raLegend.innerHTML = ''; raNote.textContent = ''; return; }
    var d = raState.data; if (!d) { raChart.innerHTML = '<div class=ra-empty>no data</div>'; return; }
    var sess = d.sessions || { in: 0, out: 0, cost: 0 }, sessTot = raVal(sess);
    var segs = raSegments(), judgeTot = segs.reduce(function (a, s) { return a + raVal(s); }, 0);
    var maxV = Math.max(sessTot, judgeTot, 1);
    var W = 480, H = 250, top = 24, bot = 30, chartH = H - top - bot, baseY = top + chartH, barW = 92, cx1 = W * 0.30, cx2 = W * 0.70;
    function rect(x, y, w, h, fill, title) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + Math.max(h, 0) + '" style="fill:' + fill + '" rx="2"><title>' + raEsc(title) + '</title></rect>'; }
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" preserveAspectRatio="xMidYMid meet">';
    svg += '<line x1="6" y1="' + baseY + '" x2="' + (W - 6) + '" y2="' + baseY + '" style="stroke:var(--hairline, #3a3a3a)"/>';
    var sh = sessTot / maxV * chartH;
    svg += rect(cx1 - barW / 2, baseY - sh, barW, sh, 'var(--text-faint, #7d8590)', 'sessions · ' + fmtTok(sess.in) + ' in / ' + fmtTok(sess.out || 0) + ' out · ' + fmtUsd(sess.cost || 0));
    svg += '<text x="' + cx1 + '" y="' + (baseY - sh - 6) + '" text-anchor="middle" style="fill:var(--text-bright, #ddd)" font-size="12">' + raFmt(sessTot) + '</text>';
    svg += '<text x="' + cx1 + '" y="' + (baseY + 18) + '" text-anchor="middle" style="fill:var(--text-muted, #9aa0a6)" font-size="12">Sessions</text>';
    var cum = 0; segs.forEach(function (s) { var st = raVal(s), h = st / maxV * chartH, y = baseY - cum - h; cum += h;
      svg += rect(cx2 - barW / 2, y, barW, h, s.color, s.label + ' · ' + fmtTok(s.in) + ' in / ' + fmtTok(s.out) + ' out · ' + s.calls + ' calls · ' + fmtUsd(s.cost || 0)); });
    svg += '<text x="' + cx2 + '" y="' + (baseY - cum - 6) + '" text-anchor="middle" style="fill:var(--text-bright, #ddd)" font-size="12">' + raFmt(judgeTot) + '</text>';
    svg += '<text x="' + cx2 + '" y="' + (baseY + 18) + '" text-anchor="middle" style="fill:var(--text-muted, #9aa0a6)" font-size="12">Judges</text>';
    svg += '</svg>'; raChart.innerHTML = svg;
    var lg = segs.map(function (s) { return '<span class=ra-li><span class=ra-sw style="background:' + s.color + '"></span>' + raEsc(s.label) + ' <b>' + raFmt(raVal(s)) + '</b></span>'; }).join('');
    raLegend.innerHTML = '<span class=ra-li><span class="ra-sw" style="background:var(--text-faint, #7d8590)"></span>sessions <b>' + raFmt(sessTot) + '</b></span>' + lg;   // the swatch matches its bar (PR #886 review: the bar moved to --text-faint and they split in classic)
    var ratio = sessTot ? (judgeTot / sessTot * 100) : 0;
    raNote.textContent = 'last ' + raState.periodLabel + ' · judges = ' + (sessTot ? ratio.toFixed(1) : '0') + '% of session ' + (raCost() ? 'cost' : 'tokens') + ' · combined ' + raFmt(sessTot + judgeTot); }
  function raFetch() { raState.loading = true; raRender();
    fetch(ku('/analytics?window=' + raState.window), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { raState.loading = false; raState.data = d; raRender(); }).catch(function () { raState.loading = false; raChart.innerHTML = '<div class=ra-empty>analytics unavailable</div>'; raLegend.innerHTML = ''; raNote.textContent = ''; }); }
  if (raOpen) raOpen.onclick = function (e) { e.stopPropagation(); endDrags(); raBack.hidden = false; p.hidden = true; raFetch(); };   // the card hides here too: a drag in flight ends first (the migration read's low 2)
  // the panel's every close returns to the CARD it was opened from (the T409 tidy's read found the gap): a bare hide of the layer
  // left the card hidden too, and with nothing posting settings off the shell kept its transparent full-window frame over the
  // page (Escape dead, since the shell asks this page and a hidden card answers no; every click on the invisible frame; only the
  // palette or a reload recovered). From the card, Escape and the backdrop close the settings by the normal road. The close's
  // own click stops here: bubbling on to the document, it would meet the card's click-outside listener with the card just
  // shown and close the settings outright (the lab saw the card hidden again a moment after the close). The settings' own
  // close, open and Escape answer route through this too, so the layer never survives a close or sits over a fresh open.
  function raHide(e) { if (e && e.stopPropagation) e.stopPropagation(); raBack.hidden = true; p.hidden = false; }
  if (raClose) raClose.onclick = raHide;
  if (raBack) raBack.addEventListener('click', function (e) { if (e.target === raBack) raHide(e); });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-periods button'), function (btn) { btn.onclick = function () { raState.window = +btn.getAttribute('data-w'); raState.periodLabel = btn.textContent;
    Array.prototype.forEach.call(document.querySelectorAll('.ra-periods button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raFetch(); }; });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-group button'), function (btn) { btn.onclick = function () { raState.group = btn.getAttribute('data-g');
    Array.prototype.forEach.call(document.querySelectorAll('.ra-group button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raRender(); }; });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-metric button'), function (btn) { btn.onclick = function () { raState.metric = btn.getAttribute('data-m');
    Array.prototype.forEach.call(document.querySelectorAll('.ra-metric button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raRender(); }; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && raBack && !raBack.hidden) raHide(); });
}

module.exports = { initGear };
