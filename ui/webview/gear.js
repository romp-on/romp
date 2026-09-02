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
//   posts it into the feed iframe; the VS Code host posts it into the webview).
// Model/effort <option>s come from GET /models at open (they were server-baked
// into the HTML before — /models was already the single source of truth).

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
var SHORTCUT_ROWS =
  '<div class=rs-key id=rs-keys-web hidden><button id=rs-keys-btn type=button>Customize shortcuts…</button>' +
  '<span class=rs-key-desc>view, record and rebind every dashboard shortcut</span></div>' +
  '<div class=rs-key id=rs-keys-vsc hidden><span class=rs-key-desc>Shortcuts are VS Code keybindings here — search "rompChat" in Keyboard Shortcuts.</span></div>';

// Auto Nudge's hover description lives in a var because fillAutoNudge() appends to it when the attached
// machines disagree — the row then has to say WHICH ones, and this is the one level down from the label.
var AUTONUDGE_SUB = "When a session goes idle but its goal still shows working (not blocked, not awaiting agents or a job "
  + "you), automatically nudge it once for a status update. Applies to every connected machine's kernel.";

// The modal markup — ported verbatim from the kernel's _gear_html; the model/
// effort selects start empty and are filled from /models (see fill()).
var GEAR_HTML =
  '<button id=rgear hidden aria-hidden=true></button>' +
  '<div id=rsettings hidden><div class=rs-card>' +
  '<div class=rs-h>Settings</div>' +
  "<div class='rs-sec rs-sec-first'>Account</div>" +
  "<div class='rs-row' id=rs-billing style='cursor:default'>" +
  '<span style="flex:1 1 auto"><b>Claude login</b>' +
  '<span class=rs-sub id=rs-login-acct>…</span>' +
  "<div id=rs-login-flow style='margin-top:6px'>" +
  "<button id=rs-login-btn type=button style='cursor:pointer;background:var(--btn-bg, #2a2a2a);color:var(--fg, #ccc);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:3px 10px'>Log in to Claude Code</button>" +
  // rs-note, NOT rs-sub: this is a live inline status ("starting the login flow…"), not the row's
  // description — as an rs-sub it floated a SECOND hover popover under the Account row, stacked on
  // rs-login-acct's (the user 2026-09-02, "weird double tooltip"; even empty it painted a box)
  "<span id=rs-login-state class=rs-note style='margin-left:8px'></span>" +
  '</div></span></div>' +
  '<div class=rs-sec>Sessions</div>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto'><b>Default directory</b>" +
  '<span class=rs-sub>The default directory for NEW sessions (still editable per session). Persisted kernel-side — also settable with <code>romp default-dir</code>. Falls back to the romp install dir until you set one; blank reverts to it. ~ and $VARs expand.</span>' +
  "<div style='display:flex;gap:6px;margin-top:5px'>" +
  "<input id=rs-defaultdir type=text spellcheck=false placeholder='install/serve default' style='flex:1 1 auto;min-width:0;box-sizing:border-box;background:var(--input-bg, #1e1e1e);color:var(--fg, #ccc);" +
  "border:1px solid #3a3a3a;border-radius:5px;padding:3px 6px'>" +
  "<button id=rs-defaultdir-browse type=button style='flex:0 0 auto;cursor:pointer;background:var(--btn-bg, #2a2a2a);color:var(--fg, #ccc);border:1px solid var(--hairline, #3a3a3a);border-radius:5px;padding:3px 8px'>Browse…</button>" +
  '</div></span></div>' +
  "<div class='rs-row rs-sep' style='cursor:default'><span style='flex:1 1 auto'><b>Default backend</b>" +
  '<span class=rs-sub>What the + button uses for a NEW session — tmux drives a terminal pane; SDK runs via the Agent SDK. Both kinds run side by side; this only sets the default.</span>' +
  "<select id=rs-backend style='display:none'>" +
  '<option value=sdk>SDK</option><option value=tmux>tmux (terminal)</option>' +
  '</select></span></div>' +
  "<label class='rs-row rs-sep'><input type=checkbox id=rs-autonudge>" +
  '<span><b>Auto Nudge</b><span class=rs-mixed id=rs-autonudge-split hidden></span>' +
  '<span class=rs-sub id=rs-autonudge-sub>' + AUTONUDGE_SUB + '</span>' +
  '</span></label>' +
  "<label class='rs-row'><input type=checkbox id=rs-suggestcompact>" +
  '<span><b>Suggest /compact</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>When a session has been idle over an hour with a lot of context built up (first past 400k tokens, again past 800k), send it ONE suggestion to /compact at a natural boundary — its call, once per fill-up. Never sent to workers, muted sessions, or anything mid-turn. Off by default for a fresh install; this kernel keeps its own copy.</span>' +
  '</span></label>' +
  "<label class='rs-row'><input type=checkbox id=rs-conserve>" +
  '<span><b>Conserve memory</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>Close the claude process of a session that has FADED (idle over an hour) and is on no open tab (each averages ~340MB). Everything persists — it revives on a tab click, a message, or a scheduled wake. An open tab always keeps its process; off = every session keeps its process for as long as it lives.</span>' +
  '</span></label>' +
  "<label class='rs-row'><input type=checkbox id=rs-fileedit>" +
  '<span><b>File editing</b><span class=rs-mixed hidden></span>' +
  '<span class=rs-sub>Let the file viewer’s Edit save straight to disk on the file’s machine. Off by default; the viewer asks the first time. A session working in the edited folder is told, and a save always refuses when the file changed underneath you. Applies on every connected machine’s kernel.</span>' +
  '</span></label>' +
  '<div class=rs-sec>Chat</div>' +
  '<label class=rs-row><input type=checkbox id=rs-compact>' +
  '<span><b>Compact transcript</b>' +
  '<span class=rs-sub>Collapse each run of tool uses into one line and hide thinking blocks in the chat.</span>' +
  '</span></label>' +
  '<label class=rs-row><input type=checkbox id=rs-branch>' +
  '<span><b>Show git branch</b>' +
  "<span class=rs-sub>Show the session's git branch (when it's in a repo) in the chat bottom bar, beside the directory.</span>" +
  '</span></label>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto;min-width:0'><b>Context gauge in tabs</b>" +
  "<span class=rs-sub>A slim vertical bar beside each session's name in the tab strip, filling as its context fills — the same colors as the context battery, no number. By default it appears only once a session is half full, so quiet tabs stay clean.</span>" +
  "<select id=rs-tabctx style='display:none'>" +
  '<option value=over50>When above 50%</option><option value=always>Always</option><option value=never>Never</option>' +
  '</select>' +
  "<div id=rs-tabctx-pick style='position:relative;margin-top:5px'></div>" +
  '</span></div>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto;min-width:0'><b>Text scheme</b>" +
  "<span class=rs-sub>Chat text colors only. Each option previews its own tiers — prose, the dimmer tool text, code. (Solarized Light is omitted — its tiers are made for a light page and turn muddy here.)</span>" +
  "<div id=rs-chatscheme style='position:relative;margin-top:5px'></div>" +
  '</span></div>' +
  
  "<div class='rs-row rs-jrow'><b>Comment model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model NEW comment threads start on. Same as the session (the default) keeps each thread on the model of the conversation it branches from; pinning one here starts every new thread on it. The comment dialog shows this default and its own pick still wins. Follows to every connected machine's kernel.</span><select id=rs-cmtmodel></select></div>" +
  "<div class='rs-row rs-jrow'><b>Comment effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for new comment threads. Same as the session (the default) inherits the effort of the conversation the thread branches from. Follows to every connected machine's kernel.</span><select id=rs-cmteffort></select></div>" +
  "<label class='rs-row'><input type=checkbox id=rs-cmtfast>" +
  '<span><b>Fast comment threads</b><span class=rs-mixed hidden></span>' +
  "<span class=rs-sub>Start new comment threads in fast mode (Opus-only research preview). If the thread's model can't run it, the thread still opens on that model at normal speed, with a notice. Off = same as the session. Follows to every connected machine's kernel.</span>" +
  '</span></label>' +
  '<div class=rs-sec>Sessions pane</div>' +  '<label class=rs-row><input type=checkbox id=rs-activeonly checked>' +
  '<span><b>Show active sessions only</b>' +
  '<span class=rs-sub>Only draw lanes for sessions with work in the visible time range, so idle sessions do not take up room. They stay in the chat, and a lane reappears the moment you zoom or pan to a stretch where it did something.</span>' +
  '</span></label>' +
  '<label class=rs-row><input type=checkbox id=rs-collapsegaps checked>' +
  '<span><b>Collapse idle gaps</b>' +
  '<span class=rs-sub>Squish long idle stretches (no work on any lane — e.g. overnight) into a thin break on the timeline, so the active periods get the width.</span>' +
  '</span></label>' +
  '<div class=rs-sec>Feed</div>' +
  '<label class=rs-row><input type=checkbox id=rs-feedcollapsed>' +
  '<span><b>Collapse cards by default</b>' +
  '<span class=rs-sub>Every card arrives collapsed to its one-line gist; expanding one is a per-card override. Moved here from the feed footer — a set-and-forget default, not a per-glance action.</span>' +
  '</span></label>' +
  '<div class=rs-sec>Appearance</div>' +   // renamed from Colors (2026-08-28): it owns the overall theme now, not just tints
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
  '<div class=rs-sec>Keyboard shortcuts</div>' + SHORTCUT_ROWS +
  '<div class=rs-sec>Judges</div>' +
  "<div class='rs-row rs-jrow'><b>Triage model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model the triage judges use — planner, grouper, closer, courier (the judgment-heavy tier). Applies on the judges' next pass; no restart. A pick here follows to every connected machine's kernel.</span><select id=rs-judgemodel></select></div>" +
  "<div class='rs-row rs-jrow'><b>Triage effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the triage judges. Default = no effort flag (the judges' standard behavior). Not every model accepts every level. Follows to every connected machine's kernel.</span><select id=rs-judgeeffort></select></div>" +
  "<div class='rs-row rs-jrow'><b>Distilling model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model for the judges that write the prose you read on cards — distiller, briefer, staller. Follow triage (the default) keeps them on the triage pick; pinning a model here lets the copy you read run richer than the placement judges. Follows to every connected machine's kernel.</span><select id=rs-distillmodel></select></div>" +
  "<div class='rs-row rs-jrow'><b>Distilling effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the distilling judges. Follow triage (the default) rides the triage effort; Default pins no effort flag. Follows to every connected machine's kernel.</span><select id=rs-distilleffort></select></div>" +
  "<div class='rs-row rs-jrow'><b>Indexing model <span class=rs-mixed hidden></span></b><span class=rs-sub>The model the indexing judges use — captioner + archiver (high-volume, low-stakes summarization). Haiku by default for cost. Follows to every connected machine's kernel.</span><select id=rs-indexmodel></select></div>" +
  "<div class='rs-row rs-jrow'><b>Indexing effort <span class=rs-mixed hidden></span></b><span class=rs-sub>Thinking effort for the indexing judges. Default = none (indexing runs with thinking disabled as a cost lever; leave Default unless you know you want it). Follows to every connected machine's kernel.</span><select id=rs-indexeffort></select></div>" +
  '<div class=rs-sec>Updates & debug</div>' +
  "<div class='rs-row' style='cursor:default'><span style='flex:1 1 auto'><b>Automatic updates <span class=rs-mixed hidden></span></b>" +
  '<span class=rs-sub>romp watches for new tagged releases (every 6 hours) AND new commits on main (origin polled every few minutes, plus a restart offer when updated code sits on disk unbooted) — one banner covers both, and acting on it converges every attached machine. Check and ask (the default) offers the banner with an Update button; Install automatically converges by itself, restarting at the next quiet moment; Off never checks. Kernel-side setting.</span>' +
  "<select id=rs-updates style='display:none'>" +
  '<option value=ask>Check and ask</option><option value=auto>Install automatically</option><option value=off>Off</option>' +
  '</select></span></div>' +
  
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
  "<div class=rs-sep style='padding-top:8px'>" +
  '<button id=ra-open class=ra-openbtn>Token usage analytics</button></div>' +
  "<div class='rs-h rs-sep'>romp · version</div>" +
  '<div id=rsver>…</div></div></div>' +
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
function initGear(post) {
  if (document.getElementById('rsettings')) return;
  document.body.insertAdjacentHTML('beforeend', GEAR_HTML);

  var g = document.getElementById('rgear'), p = document.getElementById('rsettings'),
    b = document.getElementById('rsver'), cc = document.getElementById('rs-compact'),
    jix = document.getElementById('rs-judges-index'), jtr = document.getElementById('rs-judges-triage'),
    an = document.getElementById('rs-autonudge'), bk = document.getElementById('rs-backend'),
    cvm = document.getElementById('rs-conserve'),
    csg = document.getElementById('rs-suggestcompact'),
    dd = document.getElementById('rs-defaultdir'), gb = document.getElementById('rs-branch'),
    tc = document.getElementById('rs-tabctx'),
    cs = document.getElementById('rs-chatscheme'),
    tt = document.getElementById('rs-theme'),
    cg = document.getElementById('rs-collapsegaps'), ao = document.getElementById('rs-activeonly'),
    fc = document.getElementById('rs-feedcollapsed'),
    jm = document.getElementById('rs-judgemodel'),
    im = document.getElementById('rs-indexmodel'), je = document.getElementById('rs-judgeeffort'),
    ie = document.getElementById('rs-indexeffort'), upm = document.getElementById('rs-updates'),
    dm = document.getElementById('rs-distillmodel'), de = document.getElementById('rs-distilleffort'),
    cmm = document.getElementById('rs-cmtmodel'), cme = document.getElementById('rs-cmteffort'),
    cmf = document.getElementById('rs-cmtfast'),
    fe = document.getElementById('rs-fileedit'),
    ans = document.getElementById('rs-autonudge-split'), asub = document.getElementById('rs-autonudge-sub');
  function load() { try { return Object.assign({ compact: true, colormap: 'aurora', subgoals: true, debug: false, backend: 'sdk', defaultDir: '', showBranch: false, tabCtx: 'over50', collapseGaps: true, activeOnly: true }, JSON.parse(localStorage.getItem('romp:settings') || 'null')); } catch (e) { return { compact: true, colormap: 'aurora', subgoals: true, debug: false, backend: 'sdk', defaultDir: '', showBranch: false, tabCtx: 'over50', collapseGaps: true, activeOnly: true }; } }
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
    try { localStorage.setItem('romp:settings', JSON.stringify(s)); } catch (e) {}
    try { window.dispatchEvent(new Event('romp:settings')); } catch (e) {}
    post({ type: 'settingsSync', settings: s });
  }
  cc.addEventListener('change', function () { var s = load(); s.compact = cc.checked; save(s); });
  if (gb) gb.addEventListener('change', function () { var s = load(); s.showBranch = gb.checked; save(s); });
  if (tc) tc.addEventListener('change', function () { var s = load(); s.tabCtx = tc.value; save(s); });
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
  // The Context-gauge picker joins the same builder (the user 2026-08-27, approving the flagged
  // migration: one menu vocabulary across the whole panel — the native select stuck out beside
  // the two house menus). Plain-text options, so the rows are just labels; the HIDDEN select
  // stays the VALUE holder (the versionMenu pattern): fill()/openSettings keep writing tc.value,
  // and the pick fires the select's own change event so persistence is the existing handler.
  var TABCTX = [
    { id: 'over50', name: 'When above 50%' },
    { id: 'always', name: 'Always' },
    { id: 'never', name: 'Never' }
  ];
  function tabCtxRowHTML(o) {
    return '<span style="flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--menu-fg, #ccc)">' + o.name + '</span>';
  }
  var tcDrop = housePick(document.getElementById('rs-tabctx-pick'), 'tabctx', tabCtxRowHTML, function (id) {
    if (tc) { tc.value = id; tc.dispatchEvent(new Event('change')); }
    tcPaint();
  });
  function tcPaint() {
    if (!tcDrop) return;
    tcDrop(TABCTX, tc ? tc.value : 'over50');
  }
  tcPaint();
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
  selectPick(de, 'flex:0 0 auto;width:45%');
  selectPick(cme, 'flex:0 0 auto;width:45%');
  jix.addEventListener('change', function () { var s = load(); s.showIndexJudges = jix.checked; save(s); });
  jtr.addEventListener('change', function () { var s = load(); s.showTriageJudges = jtr.checked; save(s); });
  if (cg) cg.addEventListener('change', function () { var s = load(); s.collapseGaps = cg.checked; save(s); });
  if (ao) ao.addEventListener('change', function () { var s = load(); s.activeOnly = ao.checked; save(s); });
  if (fc) fc.addEventListener('change', function () { var s = load(); s.collapsed = fc.checked; save(s); });
  // Auto Nudge / judge tiers are SERVER-SIDE (the kernel runs them): post the
  // change; the controls re-initialize from /version on every open (fill()).
  // Each attached kernel keeps its own copy, so the post goes to all of them
  // (federation.ts KERNEL_SETTING) — which is also what resolves a split box:
  // the click picks one answer and every machine takes it.
  if (an) an.addEventListener('change', function () {
    clearAutoNudgeSplit();
    post({ type: 'setAutoNudge', enabled: an.checked });
  });
  if (fe) fe.addEventListener('change', function () { post({ type: 'setFileEditing', enabled: fe.checked }); });
  if (cvm) cvm.addEventListener('change', function () { post({ type: 'setConserve', enabled: cvm.checked }); });
  if (csg) csg.addEventListener('change', function () { post({ type: 'setCompactSuggest', enabled: csg.checked }); });
  // ── the in-dashboard LOGIN flow (T157): the dashboard is already on the phone over Tailscale,
  // so streaming the CLI's paste-code OAuth URL here IS the phone login. The code input is a pure
  // pass-through to the kernel's PTY — nothing is stored or logged on any side.
  var lgB = document.getElementById('rs-login-btn'), lgS = document.getElementById('rs-login-state'),
      lgU = document.getElementById('rs-login-url'), lgC = document.getElementById('rs-login-code'),
      lgI = document.getElementById('rs-login-input'), lgSend = document.getElementById('rs-login-send'),
      lgX = document.getElementById('rs-login-cancel'), lgA = document.getElementById('rs-login-acct');
  var lgTimer = null, lgLive = '';   // lgLive = the flow state lgRender last saw (drives the button's two jobs)
  // The paste-code UI lives in a MODAL (the user 2026-08-30: "it would just be a login button…
  // then it would pop up another modal that says paste the code so it doesn't always sit there
  // taking up space") — centered card over a translucent backdrop, the panel rule's treatment.
  // Closing the modal never cancels the flow (Cancel does); the row's state span keeps narrating,
  // and the button reopens the modal mid-flow instead of restarting the login.
  var lgM = document.getElementById('rs-login-modal');
  function lgModal(on) { if (lgM) lgM.hidden = !on; }
  if (lgM) lgM.addEventListener('click', function (e) { if (e.target === lgM) lgModal(false); });
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
  if (upm) upm.addEventListener('change', function () { post({ type: 'setUpdateMode', mode: upm.value }); });
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
          versions.forEach(function (v) {
            var r2 = document.createElement('div');
            r2.setAttribute('style', rowStyle);
            r2.tabIndex = 0;
            r2.appendChild(document.createTextNode(v.label));
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
  if (jm) jm.addEventListener('change', function () { post({ type: 'setJudgeModel', model: jm.value }); });
  if (im) im.addEventListener('change', function () { post({ type: 'setIndexModel', model: im.value }); });
  if (je) je.addEventListener('change', function () { post({ type: 'setJudgeEffort', effort: je.value }); });
  if (ie) ie.addEventListener('change', function () { post({ type: 'setIndexEffort', effort: ie.value }); });
  if (dm) dm.addEventListener('change', function () { post({ type: 'setDistillModel', model: dm.value }); });
  if (de) de.addEventListener('change', function () { post({ type: 'setDistillEffort', effort: de.value }); });
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
      post({ type: 'setCommentFast', fast: 'session' });
    }
  }
  if (cmm) cmm.addEventListener('change', function () { post({ type: 'setCommentModel', model: cmm.value }); cmtFastGate(true); });
  if (cme) cme.addEventListener('change', function () { post({ type: 'setCommentEffort', effort: cme.value }); });
  if (cmf) cmf.addEventListener('change', function () { post({ type: 'setCommentFast', fast: cmf.checked ? 'on' : 'session' }); });
  // feed-colormap preview bar: a horizontal gradient of the SELECTED map's stops (mirrors render.ts COLORMAPS).
  var CMAPS = { aurora: [[84, 178, 4], [0, 180, 115], [35, 175, 156], [66, 169, 176], [25, 168, 201], [14, 164, 227], [74, 155, 241], [113, 145, 244], [144, 136, 240]],
    hawaii: [[140, 2, 115], [146, 46, 85], [151, 78, 62], [155, 111, 40], [156, 150, 28], [137, 189, 74], [107, 212, 142], [103, 233, 213], [179, 242, 253]],
    viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]],
    magma: [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]],
    inferno: [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]],
    plasma: [[13, 8, 135], [75, 3, 161], [125, 3, 168], [168, 34, 150], [203, 70, 121], [229, 107, 93], [248, 148, 65], [253, 195, 40], [240, 249, 33]],
    cividis: [[0, 34, 78], [33, 59, 110], [76, 85, 108], [108, 110, 114], [142, 137, 120], [177, 165, 112], [217, 197, 92], [254, 232, 56]] };
  var cmBtn = document.getElementById('rs-cmap-btn'), cmList = document.getElementById('rs-cmap-list');
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
  // The model/effort <option>s come from /models — the same single source the
  // chat + timeline pickers use. Cached after the first successful fetch.
  var choices = null;
  var choicesP = null;   // the single-flight promise — options are written exactly once per page
  function fillChoices() {
    if (choicesP) return choicesP;
    choicesP = fetch(ku('/models'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      choices = d || { models: [], efforts: [] };
      var mo = (choices.models || []).map(function (m) {
        var vs = (m.versions || []).map(function (v) { return '<option value="' + v.value + '">' + v.label + '</option>'; }).join('');
        return '<option value="' + m.value + '">' + m.label + '</option>' + vs;   // versions ride as options too — the hidden select stays the value holder for any pick
      }).join('');
      var eff = (choices.efforts || []).map(function (m) { return '<option value="' + m.value + '">' + m.label + '</option>'; }).join('');
      var eo = '<option value="">Default</option>' + eff;
      if (jm) jm.innerHTML = mo; if (im) im.innerHTML = mo;
      if (je) je.innerHTML = eo; if (ie) ie.innerHTML = eo;
      // the distilling pair leads with the follow-triage sentinel — its default, so a fresh kernel
      // shows "Follow triage" rather than a model nobody picked. Its Default (no effort flag) is the
      // stored sentinel "none", never "" — an empty state file reads back as the default ("follow").
      if (dm) dm.innerHTML = '<option value="triage">Follow triage</option>' + mo;
      if (de) de.innerHTML = '<option value="triage">Follow triage</option><option value="none">Default</option>' + eff;
      // the comment pair leads with the same-as-the-session sentinel — its default, so a fresh
      // kernel shows the inherit behavior, not a model nobody picked; "default" = the account default
      if (cmm) cmm.innerHTML = '<option value="session">Same as the session</option><option value="default">Default</option>' + mo;
      if (cme) cme.innerHTML = '<option value="session">Same as the session</option>' + eff;
      return choices;
    }).catch(function () { choicesP = null; return null; });   // retry on the next open, never a second live fetch
    return choicesP;
  }
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
  // the word beside the label — glanceable) and is NAMED in the hover line, one level down. Before this
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
     ['indexEffort', ie], ['distillModel', dm], ['distillEffort', de], ['fileEditing', fe],
     ['compactSuggest', csg],
     ['commentModel', cmm], ['commentEffort', cme], ['commentFast', cmf]].forEach(function (pair) {
      var key = pair[0], el = pair[1];
      if (!el) return;
      var row = el.closest ? el.closest('.rs-row') : null;
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
  // fill()'s ONE write path for kernel-backed selects (the user 2026-09-01, whose stored distill
  // pick displayed as the default): a stored value the option list doesn't carry is INJECTED as a
  // marked option and selected — the row shows the truth (an off-list value, named) rather than
  // silently falling to its first option. Values are validated at write time by the kernel, so an
  // injection here means THIS page's list is behind the store — worth seeing, never worth hiding.
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
  function fill() { fillChoices().then(function () { return fetch(ku('/version'), { cache: 'no-store' }); }).then(function (r) { return r.json(); }).then(function (v) {
    // ONE /tunnels fetch feeds every cross-machine comparison: the autoNudge box and the select marks.
    // A failed /tunnels leaves the local answers standing, unmarked — same fallback as before.
    fetch(ku('/tunnels'), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      var rows = (d && d.tunnels) || [];
      fillAutoNudge(v.autoNudge, rows);
      fillMixedMarks(v, rows);
    }).catch(function () { fillAutoNudge(v.autoNudge, []); fillMixedMarks(v, []); });
    if (fe) fe.checked = !!v.fileEditing;   // the kernel's persisted opt-in is authoritative (see the viewer's consent popup)
    if (cvm) cvm.checked = !!v.conserveMemory;   // T148: the kernel's persisted conserve flag is authoritative
    if (csg) csg.checked = !!v.compactSuggest;   // T208+: the kernel's persisted opt-in is authoritative
    lgRender(v);   // the Billing login block (T157) rides the same /version read
    if ((v.login || {}).state && !lgTimer) lgTimer = setTimeout(lgPoll, 1500);   // a flow mid-run resumes polling
    if (typeof v.updateMode === 'string') setShow(upm, v.updateMode);   // the kernel's persisted mode is authoritative
    if (typeof v.judgeModel === 'string') setShow(jm, v.judgeModel);   // the judge's ACTUAL current model/effort per tier is authoritative
    if (typeof v.indexModel === 'string') setShow(im, v.indexModel);
    if (typeof v.judgeEffort === 'string') setShow(je, v.judgeEffort);
    if (typeof v.indexEffort === 'string') setShow(ie, v.indexEffort);
    if (typeof v.distillModel === 'string') setShow(dm, v.distillModel);   // RAW: "triage" selects the Follow-triage option
    if (typeof v.distillEffort === 'string') setShow(de, v.distillEffort);
    if (typeof v.commentModel === 'string') setShow(cmm, v.commentModel);   // RAW: "session" selects Same as the session
    if (typeof v.commentEffort === 'string') setShow(cme, v.commentEffort);
    if (cmf && typeof v.commentFast === 'string') cmf.checked = v.commentFast === 'on';
    cmtFastGate(false);
    if (dd && typeof v.defaultDir === 'string') dd.value = v.defaultDir;   // the kernel's persisted default is authoritative
    // Browse… draws on the KERNEL's screen, and a kernel with no desktop has none — the click used to
    // vanish into a macOS-only dialog. Drop the button rather than offer one that cannot work; the
    // field takes a typed path, which is what that machine has. An older kernel sends no verdict and
    // keeps the button it always had.
    if (ddb && typeof v.nativeDialogs === 'boolean') ddb.style.display = v.nativeDialogs ? '' : 'none';
    repaintSelectPicks();   // fill() writes sel.value directly (no change event) — the closed rows follow
    var x = lv(); b.innerHTML = 'kernel ' + (v.kernel_sha || '?') + '\nserving v' + v.dist_ver + '\nthis tab v' + (x || '?');
  }).catch(function () { b.textContent = '(version unavailable)'; }); }
  // The settings modal is full-WINDOW in the web shell — ask it to expand the
  // feed iframe while open (no-op elsewhere: VS Code's feed panel IS the window).
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
      if (window.parent !== window) { document.body.classList.add('rs-lifted'); placeLifted(5); window.addEventListener('resize', onRsResize); } }
    else { de.classList.remove(m); document.body.classList.remove(m);
      document.body.classList.remove('rs-lifted'); document.body.classList.remove('rs-pane-gone');
      clearPaneVars();
      window.removeEventListener('resize', onRsResize); } }
  function closeSettings() { p.hidden = true; setModalCls(false); feedFull(false); }
  function openSettings() { if (!p.hidden) { closeSettings(); return; }   // the opener toggles the modal
    // Signal the SHELL first, then measure (the picker's order, adopted 2026-08-09): feedFull posts
    // settings-open, which is what un-hides #feed-pane when the feed is toggled off — measuring first
    // burned the whole 5-frame retry against a display:none pane, latched rs-pane-gone, and the
    // full-viewport fallback box blacked out every pane behind the modal.
    p.hidden = false; feedFull(true); setModalCls(true); var s = load(); cc.checked = !!s.compact; jix.checked = (s.showIndexJudges !== undefined ? !!s.showIndexJudges : !!s.debug); jtr.checked = (s.showTriageJudges !== undefined ? !!s.showTriageJudges : !!s.debug); if (gb) gb.checked = s.showBranch === true; if (tc) tc.value = tabCtxMode(s.tabCtx); tcPaint(); csPaint(); ttPaint(); if (cg) cg.checked = s.collapseGaps !== false; if (ao) ao.checked = s.activeOnly !== false; if (fc) fc.checked = s.collapsed === true; cmBuild(); cmPaint(s.colormap || 'aurora'); if (bk) bk.value = s.backend || 'sdk'; if (dd) dd.value = s.defaultDir || ''; plFill(); fill(); }
  if (g) g.onclick = function (e) { e.stopPropagation(); openSettings(); };   // hidden anchor; hosts open via the message below
  window.addEventListener('message', function (e) { if (e.data && e.data.romp === 'openSettings') openSettings(); });
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
  })();
  p.addEventListener('click', function (e) { if (e.target === p) closeSettings(); });   // click the dimmed backdrop (not the card) → close
  document.addEventListener('click', function (e) { if (!p.hidden && e.target !== g && !p.contains(e.target)) closeSettings(); });
  var rf = document.getElementById('rrefresh');   // ↻ (web shell rail only): POST /restart, poll /healthz, reload
  if (rf) rf.onclick = function () { rf.disabled = true; try { fetch(ku('/restart'), { method: 'POST' }).catch(function () {}); } catch (e) {}
    var n = 0; (function again() { setTimeout(function () { n++; fetch(ku('/healthz'), { cache: 'no-store' }).then(function (r) { if (r && r.ok) location.reload(); else if (n < 40) again(); }).catch(function () { if (n < 40) again(); }); }, 500); })(); };
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
    raLegend.innerHTML = '<span class=ra-li><span class="ra-sw" style="background:#7d8590"></span>sessions <b>' + raFmt(sessTot) + '</b></span>' + lg;
    var ratio = sessTot ? (judgeTot / sessTot * 100) : 0;
    raNote.textContent = 'last ' + raState.periodLabel + ' · judges = ' + (sessTot ? ratio.toFixed(1) : '0') + '% of session ' + (raCost() ? 'cost' : 'tokens') + ' · combined ' + raFmt(sessTot + judgeTot); }
  function raFetch() { raState.loading = true; raRender();
    fetch(ku('/analytics?window=' + raState.window), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { raState.loading = false; raState.data = d; raRender(); }).catch(function () { raState.loading = false; raChart.innerHTML = '<div class=ra-empty>analytics unavailable</div>'; raLegend.innerHTML = ''; raNote.textContent = ''; }); }
  if (raOpen) raOpen.onclick = function (e) { e.stopPropagation(); raBack.hidden = false; p.hidden = true; raFetch(); };
  if (raClose) raClose.onclick = function () { raBack.hidden = true; };
  if (raBack) raBack.addEventListener('click', function (e) { if (e.target === raBack) raBack.hidden = true; });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-periods button'), function (btn) { btn.onclick = function () { raState.window = +btn.getAttribute('data-w'); raState.periodLabel = btn.textContent;
    Array.prototype.forEach.call(document.querySelectorAll('.ra-periods button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raFetch(); }; });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-group button'), function (btn) { btn.onclick = function () { raState.group = btn.getAttribute('data-g');
    Array.prototype.forEach.call(document.querySelectorAll('.ra-group button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raRender(); }; });
  Array.prototype.forEach.call(document.querySelectorAll('.ra-metric button'), function (btn) { btn.onclick = function () { raState.metric = btn.getAttribute('data-m');
    Array.prototype.forEach.call(document.querySelectorAll('.ra-metric button'), function (b2) { b2.className = (b2 === btn) ? 'on' : ''; }); raRender(); }; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && raBack && !raBack.hidden) raBack.hidden = true; });
}

module.exports = { initGear };
