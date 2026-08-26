// The romp strip — VS Code's stand-in for the web shell's bottom rail: the
// account usage windows (the rail's used-over-elapsed bar pairs) and the
// settings gear, docked below the chat composer / the feed's control bar
// (the user 2026-07-13). The web shell keeps its own rail, so the strip
// renders ONLY where the host opts in (window.__rompShowStrip, injected by
// the VS Code builders); when chat and feed are both visible the host hides
// the chat's copy (a {type:"stripShow"} message) — feed wins.
//
// Usage data: an initial GET /usage via the host-injected kernel base, then
// live {type:"usage"} pushes relayed by the host from the timeline view's
// forwards — the same event source the web rail rides.
//
// Every kernel fetch routes through media.ts kernelUrl(): it prepends the
// host-injected base AND appends ?token= when the host injected one — the
// kernel gates every request on the serve token (loopback included), and a
// webview's cross-origin fetch carries no cookie.
import { kernelUrl } from "./media";

export type UsageWindow = {
  key: string;
  label: string;        // the rail's expanded label
  short: string;        // the compressed tag a narrow strip swaps in ("5h" / "7d" / "F5")
  pct: number | null;   // used % of the limit (LAST-KNOWN when unknown — drawn faded)
  elapsedPct: number | null;  // % of the window elapsed (pace comparison)
  unknown: boolean;     // the window reset since the last report — the reading no longer describes the present
  title: string;        // hover detail
};

// The rail's window set: [key, span seconds, expanded label, compressed tag].
const WINS: Array<[string, number, string, string]> = [
  ["fiveHour", 5 * 3600, "5 hours", "5h"],
  ["sevenDay", 7 * 86400, "7 days", "7d"],
  ["fable", 7 * 86400, "Fable 5", "F5"],
];

// The rail's usage color ramp: green under 70%, amber under 90%, red at 90+.
export function usageColor(pct: number): string {
  return pct >= 90 ? "#c0392b" : pct >= 70 ? "#e0b020" : "#54B204";
}

export function fmtAgo(ep: number, nowS: number): string {
  const dt = Math.max(0, nowS - ep);
  const d = Math.floor(dt / 86400);
  const h = Math.floor((dt % 86400) / 3600);
  const m = Math.floor((dt % 3600) / 60);
  return ((d ? `${d}d ` : "") + (h || d ? `${h}h ` : "") + `${m}m`).trim() + " ago";
}

export function fmtReset(resetsAt: number, nowS: number): string {
  const dt = resetsAt - nowS;
  if (dt <= 0) return "soon";
  const d = Math.floor(dt / 86400);
  const h = Math.floor((dt % 86400) / 3600);
  const m = Math.floor((dt % 3600) / 60);
  return (d ? `${d}d ` : "") + (h || d ? `${h}h ` : "") + `${m}m`;
}

// /usage payload → the windows worth drawing (unreported windows drop out).
export function usageWindows(usage: any, nowS: number): UsageWindow[] {
  const out: UsageWindow[] = [];
  for (const [key, span, label, short] of WINS) {
    const seg = usage && usage[key];
    if (!seg || typeof seg.pct !== "number") continue;
    const rolled = !!(seg.resetsAt && nowS > seg.resetsAt);   // the window reset since the last report
    // A rolled window's reading no longer describes the PRESENT window — that is UNKNOWN, not 0
    // (the user 2026-07-31: a remote whose kernel had no live session to ask sat on a days-old
    // snapshot, and the rail drew a confident 0% beside a live account's real bars). The last-known
    // fill still draws — FADED, with a "?" readout — so unknown and genuinely-empty can never be
    // confused. Same fail-loudly rule as every other stale source.
    const pct = Math.max(0, Math.min(100, seg.pct));
    let elapsedPct: number | null = null;
    if (!rolled && seg.resetsAt && span) {
      elapsedPct = Math.max(0, Math.min(100, Math.round(((nowS - (seg.resetsAt - span)) / span) * 100)));
    }
    out.push({
      key, label, short, pct, elapsedPct, unknown: rolled,
      title: rolled
        ? `${label} — window reset ${fmtAgo(seg.resetsAt, nowS)} and no reading has arrived since — current usage unknown (last known ${pct}%)`
        : `${label} — used ${pct}%`
          + (elapsedPct != null ? ` · ${elapsedPct}% through the window` : "")
          + (seg.resetsAt ? ` · resets in ${fmtReset(seg.resetsAt, nowS)}` : ""),
    });
  }
  return out;
}

// 3 significant figures at every magnitude (the user 2026-08-13: a bare "1B tok" hides a third of a
// billion tokens) — 1.32B / 13.2B / 132B, trailing zeros kept so the precision reads as meant.
// Twin of the kernel rail's fmtTok; the two must stay in step (rail-spend pins).
function fmtSig3(v: number): string {
  return v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
}
export function fmtTok(n: number): string {
  if (n >= 1e9) return fmtSig3(n / 1e9) + "B";
  if (n >= 1e6) return fmtSig3(n / 1e6) + "M";
  if (n >= 1e3) return fmtSig3(n / 1e3) + "k";
  return String(n);
}

// API-key spend is ONE compact CELL, the strip twin of the web rail's apiCellHTML (the user
// 2026-08-11: the rail moved to this presentation and the strip must reflect it — spend rendered as
// three bar-less window rows was the OLD grammar, and on a key-billed machine it read as broken).
// Spend is NUMBERS, never bars (the user 2026-08-08: the spend bar graphs told you nothing): a
// constant "API" label — no fragment of any key, not even a last-4 tail, reaches a surface — then the
// 5-hour burn and the month-to-date, dollars AND tokens (the user 2026-08-09), each designator the
// window's ONE display name to the LEFT of its value. The full per-window breakdown (7 days and turn
// counts included) rides the cell's hover title. Keyed on the spend windows' PRESENCE, not the legacy
// apiKey flag — the same hasSpend branch the rail runs; rail-spend.test.ts holds the two in step.
export function fmtUsd(v: number): string { return "$" + String(Math.round(v)); }   // whole dollars everywhere — no cents (the user 2026-08-09)
export type ApiCell = {
  segs: Array<{ key: string; label: string; short: string; usd: number; tok: number }>;
  title: string;
};
export function apiCell(usage: any): ApiCell | null {
  const sp = usage && usage.spend;
  // pay-per-token has no reset windows (the user 2026-08-13): the key's story is 1 day / 1 week /
  // 1 month. day||fiveHour: an older kernel ships no 'day' yet (version skew) — its 5h burn is the
  // closest honest stand-in until it updates.
  if (!sp || !(sp.day || sp.fiveHour)) return null;
  const segs: ApiCell["segs"] = [];
  const daySeg = sp.day || sp.fiveHour;
  for (const [key, label, short, seg] of [["day", "1 day", "1d", daySeg],
                                          ["month", "1 month", "1mo", sp.month]] as const) {
    if (!seg || typeof seg.usd !== "number") continue;
    segs.push({ key, label, short, usd: seg.usd, tok: seg.tok || 0 });
  }
  if (!segs.length) return null;
  const lines = ["API-key spend"];
  for (const [key, label] of [["day", "1 day"], ["week", "1 week"], ["month", "1 month"]] as const) {
    const seg = sp[key];
    if (!seg || typeof seg.usd !== "number") continue;
    const turns = seg.turns || 0;
    lines.push(`${label} — ${fmtUsd(seg.usd)} · ${fmtTok(seg.tok || 0)} tok · ${turns} turn${turns === 1 ? "" : "s"}`);
  }
  return { segs, title: lines.join("\n") };
}

// Which panes get a quick-open label when hidden (the user 2026-07-13, who wanted chat,
// outline, and feed — only the ones that aren't currently shown). Timeline lives
// in VS Code's own panel, so it isn't listed.
export const STRIP_PANES: Array<{ key: string; label: string }> = [
  { key: "chat", label: "Chat" },
  { key: "fleet", label: "Outline" },
  { key: "feed", label: "Feed" },
];

export function initStrip(openSettings: () => void, post?: (m: Record<string, unknown>) => void): void {
  if (!(window as any).__rompShowStrip) return;
  if (document.getElementById("romp-strip")) return;

  const strip = document.createElement("div");
  strip.id = "romp-strip";
  const usageWrap = document.createElement("div");
  usageWrap.id = "strip-usage";
  // Quick-opens for the panes NOT currently on screen — the host pushes the
  // hidden-set ({type:"stripPanes"}) on every panel create/dispose/view-state.
  const panesWrap = document.createElement("div");
  panesWrap.id = "strip-panes";
  // ↻ kernel restart — the rail's #rrefresh twin. The pipes reconnect and the
  // host reloads the webviews on their own once the kernel is back.
  const refresh = document.createElement("button");
  refresh.id = "strip-refresh";
  refresh.title = "Restart the romp kernel";
  refresh.textContent = "↻";
  refresh.addEventListener("click", (e) => {
    e.stopPropagation();
    refresh.disabled = true;
    fetch(kernelUrl("/restart"), { method: "POST" }).catch(() => { /* the reconnect machinery reports */ });
    setTimeout(() => { refresh.disabled = false; }, 8000);   // pure failsafe re-arm; the reload normally lands first
  });
  // Remote kernels — the rail's #rail-net twin (same endpoints; the shell keeps
  // its own copy until federation unifies them).
  const net = document.createElement("button");
  net.id = "strip-net";
  net.title = "Remote kernels";
  net.innerHTML = "<svg viewBox='0 0 16 16' width='15' height='15'>"
    + "<path d='M8 5 L8 8 M3 11 L3 8 L13 8 L13 11' fill='none' stroke='currentColor' stroke-width='1' stroke-linejoin='round'/>"
    + "<rect x='6' y='1' width='4' height='4' rx='0.6' fill='currentColor'/>"
    + "<rect x='1' y='11' width='4' height='4' rx='0.6' fill='currentColor'/>"
    + "<rect x='11' y='11' width='4' height='4' rx='0.6' fill='currentColor'/></svg>";
  const gear = document.createElement("button");
  gear.id = "strip-gear";
  gear.title = "romp settings";
  gear.textContent = "⛭";
  gear.addEventListener("click", (e) => { e.stopPropagation(); openSettings(); });
  // The actions travel as ONE cluster pushed to the right edge (margin-left:auto,
  // not a spacer item): the strip WRAPS rather than overflow into a horizontal
  // scrollbar (the user 2026-07-13), and a wrapped cluster keeps its right pin
  // on whatever row it lands on — a spacer only pushes within its own row.
  const acts = document.createElement("div");
  acts.className = "strip-acts";
  acts.append(refresh, net, gear);
  strip.append(usageWrap, panesWrap, acts);
  document.body.appendChild(strip);
  initNetPopover(net, post);

  // The compress ladder, MEASURED (the user 2026-07-14): fixed width thresholds
  // stepped the labels down while free space remained (with every pane open there
  // are no quick-open buttons, so the strip's real content is far narrower than
  // any hardcoded threshold could know). Instead the bars are fluid (strip.css:
  // .ru-bars flex-basis 54px, min-width 18px — they compress continuously as the
  // pane narrows) and a tier is stepped only when the bars are actually pinched
  // below comfort, or the strip has wrapped. Tiers on #romp-strip[data-tier]:
  // 0 full label · 1 short tag · 2 no % readout · 3 bars only. offsetWidth/Top
  // (layout px) keep the math zoom-independent under the host's uiZoom.
  const BAR_COMFORT = 34;
  function fit() {
    if (!usageWrap.childElementCount) { strip.removeAttribute("data-tier"); return; }
    for (let t = 0; ; t++) {
      strip.dataset.tier = String(t);
      if (t >= 3) return;   // narrowest tier — from here the fluid bars + row wrap absorb the rest
      const bars = usageWrap.querySelector(".ru-bars") as HTMLElement | null;
      const pinched = !!bars && bars.offsetWidth < BAR_COMFORT;
      const wrapped = acts.offsetTop >= usageWrap.offsetTop + usageWrap.offsetHeight - 1;
      if (!pinched && !wrapped) return;
    }
  }
  let fitW = 0;
  try {
    new ResizeObserver(() => {
      const w = strip.offsetWidth;
      if (Math.abs(w - fitW) < 1) return;   // our own tier flips / wraps only change height
      fitW = w;
      fit();
    }).observe(strip);
  } catch { /* no ResizeObserver → the fluid bars + wrap still prevent overflow */ }

  function renderPanes(hidden: Record<string, boolean>) {
    panesWrap.textContent = "";
    for (const p of STRIP_PANES) {
      if (!hidden[p.key]) continue;
      const b = document.createElement("button");
      b.className = "strip-pane";
      b.textContent = p.label;
      b.title = `Open the ${p.label} pane`;
      b.addEventListener("click", (e) => { e.stopPropagation(); post?.({ type: "openPane", pane: p.key }); });
      panesWrap.appendChild(b);
    }
    fit();
  }



  function render(usage: any) {
    const nowS = Math.floor(Date.now() / 1000);
    usageWrap.textContent = "";
    // the subscription windows render as bar rows; key spend follows as ONE API cell (the rail's order)
    // An UNKNOWN window is not drawn AT ALL (the user 2026-08-13; supersedes the 2026-07-31 '?' slot):
    // the bar shows only what we know. Its last-known reading survives on HOVER — the strip has no rich
    // panel, so the unknown rows' text rides the whole strip's title, labelled as such.
    const unknownLines: string[] = [];
    for (const w of usageWindows(usage, nowS)) {
      if (w.unknown) { unknownLines.push(w.title); continue; }
      const box = document.createElement("span");
      box.className = "ru-w";
      box.title = w.title;
      // Both the expanded label and the compressed tag render; the [data-tier]
      // ladder in strip.css shows exactly one (or neither at the narrowest tier),
      // so a tier flip never needs a JS re-render.
      const name = document.createElement("span");
      name.className = "ru-name";
      const nameFull = document.createElement("span");
      nameFull.className = "ru-name-full";
      nameFull.textContent = w.label;
      const nameShort = document.createElement("span");
      nameShort.className = "ru-name-short";
      nameShort.textContent = w.short;
      name.append(nameFull, nameShort);
      const bars = document.createElement("span");
      bars.className = "ru-bars";
      const mkTrack = (pct: number, color: string) => {
        const track = document.createElement("span");
        track.className = "ru-track";
        const fill = document.createElement("span");
        fill.className = "ru-fill";
        fill.style.width = `${pct}%`;
        fill.style.background = color;
        track.appendChild(fill);
        return track;
      };
      if (w.pct != null) bars.appendChild(mkTrack(w.pct, usageColor(w.pct)));
      if (w.elapsedPct != null) bars.appendChild(mkTrack(w.elapsedPct, "#6b7a8c"));
      const pct = document.createElement("span");
      pct.className = "ru-pct";
      pct.textContent = `${w.pct}%`;
      box.append(name, bars, pct);
      usageWrap.appendChild(box);
    }
    // The API cell — the rail's apiCellHTML twin (see apiCell above): "API", then designator → value
    // pairs. The dollars are the cell's own class (.ru-apiv), NOT .ru-pct: they are the information on
    // a key-billed machine, so the compress tiers fold the tokens (tier 2) and the labels (tier 3,
    // like every row) but never the dollars themselves.
    const cell = apiCell(usage);
    if (cell) {
      const box = document.createElement("span");
      box.className = "ru-w ru-api";
      box.title = cell.title;
      const lbl = document.createElement("span");
      lbl.className = "ru-name";
      lbl.textContent = "API";
      box.appendChild(lbl);
      for (const s of cell.segs) {
        const name = document.createElement("span");
        name.className = "ru-name";
        const nameFull = document.createElement("span");
        nameFull.className = "ru-name-full";
        nameFull.textContent = s.label;
        const nameShort = document.createElement("span");
        nameShort.className = "ru-name-short";
        nameShort.textContent = s.short;
        name.append(nameFull, nameShort);
        const val = document.createElement("span");
        val.className = "ru-apiv";
        val.textContent = fmtUsd(s.usd);
        const tok = document.createElement("span");
        tok.className = "ru-apitok";
        tok.textContent = " · " + fmtTok(s.tok) + " tok";
        val.appendChild(tok);
        box.append(name, val);
      }
      usageWrap.appendChild(box);
    }
    // the hidden-from-the-bar unknowns keep a hover home: the wrap's own title says what we last knew
    usageWrap.title = unknownLines.length
      ? "Not shown (no current reading):\n" + unknownLines.join("\n") : "";
    fit();
  }

  window.addEventListener("message", (ev: MessageEvent) => {
    const m = ev.data;
    if (!m) return;
    if (m.type === "usage") render(m.usage || null);                      // live: host-relayed timeline forwards
    else if (m.type === "stripShow") strip.style.display = m.show ? "" : "none";  // feed-over-chat rule
    else if (m.type === "stripPanes") renderPanes(m.hidden || {});        // which quick-opens to offer
  });

  fetch(kernelUrl("/usage"), { cache: "no-store" })
    .then((r) => r.json())
    .then((u) => render(u))
    .catch(() => { /* the live pushes fill it in */ });
}

// The remote-kernels popover — the strip twin of the web shell's rail-net
// popover (_LANDING_REMOTES_JS in bin/romp-kernel): same kernel endpoints
// (/ssh-hosts, /tunnels, /tunnels/detach|update|start), leaner chrome. The two
// copies unify when client federation reaches VS Code; until then remote
// SESSIONS render only in the browser — this manages the kernel's tunnels.
//
// The button acknowledges every toggle (.open accent chrome) and each toggle
// posts a clientDiag breadcrumb through the host to the kernel's
// client-diag.jsonl — the user reported the button doing nothing in VS Code
// (2026-07-14) while every repro outside VS Code works, so the next report
// comes with recorded evidence instead of guesses.
function initNetPopover(button: HTMLButtonElement, post?: (m: Record<string, unknown>) => void) {
  const pop = document.createElement("div");
  pop.id = "strip-net-pop";
  pop.hidden = true;
  const row = document.createElement("div");
  row.className = "sn-attach";
  const sel = document.createElement("select");
  const attach = document.createElement("button");
  attach.textContent = "Attach";
  row.append(sel, attach);
  const list = document.createElement("div");
  list.id = "sn-list";
  // "Automatically update" (the user 2026-07-24) — the fleet-wide alternative to a modal landing mid-screen
  // on every advance. Panel-wide, under the list, since it applies to all hosts rather than one row. Mirrors
  // the web popover's copy: the two must say the same thing.
  const autoL = document.createElement("label");
  autoL.className = "sn-auto";
  const autoCb = document.createElement("input");
  autoCb.type = "checkbox";
  const autoT = document.createElement("span");
  autoT.textContent = "Automatically update";
  autoL.append(autoCb, autoT);
  autoL.title = "Keep attached machines on this machine\n\n"
    + "When a machine is connected and its romp is simply BEHIND this one — your commits only add to what it "
    + "already has — romp pushes your build to it and restarts its kernel, in the background, without asking. "
    + "The network icon animates while that runs; hover it for the live phase.\n\n"
    + "It never fires when a push could destroy anything: a machine holding its own commits, or one whose "
    + "build this repo doesn't recognise, is left alone and keeps its manual Push button. Uncommitted local "
    + "edits are never sent — only what you have committed.";
  autoCb.addEventListener("change", () => {
    const on = autoCb.checked;
    autoCb.disabled = true;
    fetch(kernelUrl("/tunnels/autoupdate"), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on }) })
      .then((rp) => rp.json())
      .then((d) => {
        autoCb.disabled = false;
        if (d && d.ok) { autoCb.checked = !!d.on; schedule(600); }
        else { autoCb.checked = !on; }   // the kernel refused — show what is actually in force
      })
      .catch(() => { autoCb.disabled = false; autoCb.checked = !on; });
  });
  pop.append(row, list, autoL);
  document.body.appendChild(pop);

  const LBL: Record<string, string> = {
    up: "connected", authorizing: "authorizing…", connecting: "connecting…", starting: "connecting…",
    "no-kernel": "kernel not answering", down: "reconnecting…", error: "error",   // a row exists = intent stands; romp never stops dialing (the user 2026-08-24)
  };
  // Every status explains itself on hover (the user 2026-07-22: learn it from tooltips, not the CLI).
  // Mirrors the web popover's TIP map — the two copies must say the same thing.
  const TIP: Record<string, string> = {
    up: "Connected: the ssh tunnel is open and that machine's romp kernel is answering through it. Its sessions appear in your tabs and timeline.",
    authorizing: "Opening an ssh connection and reading that machine's access token. Needs `ssh <host>` to work without a prompt.",
    connecting: "The ssh tunnel is up; waiting for the remote kernel to answer on its port.",
    starting: "The ssh tunnel is up; waiting for the remote kernel to answer on its port.",
    "no-kernel": "The tunnel is open but no romp kernel is answering on that machine. Start pushes this machine's romp there and boots it.",
    down: "The ssh tunnel is not up. romp keeps retrying on its own, waiting longer between tries the longer it stays down, so a machine that comes back is picked up without you doing anything. Try now dials immediately.",
    error: "The connection failed. Hover the status text for the reason romp got back. romp keeps retrying in the background.",
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = (ms: number) => { clearTimeout(timer); if (!pop.hidden) timer = setTimeout(refresh, ms); };
  const busy = (s: string) => s !== "up" && s !== "down" && s !== "error" && s !== "no-kernel";

  function loadHosts() {
    fetch(kernelUrl("/ssh-hosts"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
      const hs: string[] = (d && d.hosts) || [];
      sel.innerHTML = hs.length
        ? hs.map((h) => `<option value="${h}">${h}</option>`).join("")
        : `<option value="">(no ~/.ssh/config hosts)</option>`;
    }).catch(() => { sel.innerHTML = `<option value="">(kernel unreachable)</option>`; });   // loud, never silently empty
  }

  function act(path: string, host: string, b: HTMLButtonElement, busyText: string, via?: string) {
    b.disabled = true;
    const prev = b.textContent;
    b.textContent = busyText;
    fetch(kernelUrl(path), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(via ? { host, via } : { host }) })
      .then((rp) => rp.json().catch(() => null))
      .then((d) => {
        if (via && d && d.ok === false) {
          // a forwarded refusal has no status row of its own to land on — name it HERE (fail loudly)
          b.disabled = false;
          b.textContent = prev;
          b.classList.add("sn-actfail");
          b.title = String(d.error || d.detail || "refused");
          return;
        }
        schedule(600);
        if (via) fetchSub(via);   // the sub-list's state lives on the via machine — re-read it
      })
      .catch(() => { b.disabled = false; b.textContent = prev; });
  }

  // A trust change confirms on a LATER poll, and renderList rebuilds every poll — so a snapshot
  // fetched before the change repainted the OLD level after it, which read as "didn't hold" and
  // invited a second click (the user 2026-07-27). Pending survives re-renders: the select shows the
  // CHOSEN level, disabled with the accent applying cue, until a snapshot agrees (the confirming
  // event — no timer). A refused write deletes the entry, so the next render honestly reverts.
  const pendingTrust = new Map<string, string>();

  // BETWEEN YOUR MACHINES (the user 2026-08-11): how attached machines hold EACH OTHER's mail — a
  // pair link appears nowhere else in this popover; every other row manages only this machine's own
  // gate. Read live from each machine's kernel via /tunnels/pairs (kicked once per refresh, one dial
  // in flight — it fans out over the tunnels and must not ride the 3s poll itself), written back
  // through the kernel's /tunnels/trust-remote proxy: your tunnel + that machine's own serve token,
  // the same you-with-both-tokens boundary as the web popover's Match. Pending per direction (keyed
  // holder|sender), confirmed only when a later pairs read shows the level on the holder's own
  // table — never a timer. A pairs answer repaints from the poll's cached args (lastList), so it
  // costs no extra /tunnels round trip and cannot loop.
  const pendingPair = new Map<string, string>();
  let pairs: any = null;          // last /tunnels/pairs answer; null = not read yet this opening
  let pairsBusy = false;
  let lastUp = 0;
  let lastList: [any[], any[]] | null = null;

  // ITS CONNECTIONS (the user 2026-08-11): every up host's row expands into THAT machine's own
  // attached-host list — same row treatment, working controls — so what's connected to what is
  // managed from one dashboard. Rows come from /tunnels/of (the machine's own /tunnels, read over
  // your tunnel + its serve token), fetched on EXPAND and after a forwarded action, never in the
  // 3s poll — the /tunnels/pairs rule. Actions post the normal routes with {via}: the kernel
  // relays them to the machine that owns the tunnel (_via_forward), which judges the action
  // itself; a refusal is named on the button. Keyed expand state and a via|host pending-trust
  // latch survive re-renders (the progressive-disclosure and pending-confirm rules).
  const openSub = new Set<string>();
  const subInfo = new Map<string, any>();       // via-host -> last /tunnels/of answer
  const subBusy = new Set<string>();
  const pendingSub = new Map<string, string>(); // `${via}|${host}` -> chosen trust, confirmed by a later read

  function fetchSub(host: string) {
    if (subBusy.has(host)) return;
    subBusy.add(host);
    fetch(kernelUrl("/tunnels/of?host=" + encodeURIComponent(host)), { cache: "no-store" })
      .then((r) => r.json())
      .catch(() => ({ ok: false, error: "kernel unreachable" }))
      .then((d) => {
        subBusy.delete(host);
        subInfo.set(host, d || { ok: false, error: "empty answer" });
        if (openSub.has(host) && lastList) renderList(...lastList);
      });
  }

  // A native <select>'s open dropdown dies with its DOM node, and renderList rebuilds every row each
  // poll — at the connecting-phase 600ms cadence (schedule below) the trust picker's options dismissed
  // the instant they opened (the user 2026-08-04: click it and "it just immediately unclicks"; fine
  // once the host is up, whose 3s cadence usually leaves room). So the popover DEFERS the rebuild while
  // a trust select is engaged — focus/mousedown arms it, blur or a made choice releases — and flushes
  // the newest deferred snapshot on release: the timeline's defer-don't-rebuild idiom (_pointerHeld),
  // select-flavored. Event-based, no timers; reopening the popover resets the latch (a hidden popover
  // can never blur its way free, and its select is gone anyway).
  let trustEngaged = false;
  let deferredRender: (() => void) | null = null;
  const releaseTrust = () => {
    trustEngaged = false;
    const flush = deferredRender;
    deferredRender = null;
    if (flush) flush();
  };

  function renderList(ts: any[], known: any[] = []) {
    if (trustEngaged) { deferredRender = () => renderList(ts, known); return; }   // mid-pick — land it after
    list.textContent = "";
    button.classList.toggle("on", ts.some((t) => t.status === "up"));
    // Each option carries its own plain gloss: the bare words are romp's vocabulary, not English, and a
    // dropdown whose meaning only appears on hover makes you uncover every option before you can choose.
    // (Shared by the per-host selects and the pair rows below.)
    const TRUSTW: Record<string, string> = {
      trusted: "trusted (auto-accept)", directed: "directed (held for you)", isolated: "isolated (no mail)",
    };
    if (!ts.length && !known.length) {
      const e = document.createElement("div");
      e.className = "sn-empty";
      e.textContent = "No remotes attached.";
      list.appendChild(e);
      return;
    }
    for (const t of ts) {
      const r = document.createElement("div");
      r.className = "sn-row";
      const dot = document.createElement("span");
      dot.className = "sn-dot";
      dot.style.background = t.status === "up" ? "var(--accent, #9cd2ff)"
        : (t.status === "error" || t.status === "no-kernel") ? "#E5534B"
        : (t.status === "down") ? "#8a8a8a" : "transparent";
      if (dot.style.background === "transparent") dot.style.boxShadow = "inset 0 0 0 1.5px var(--accent, #9cd2ff)";
      dot.title = TIP[t.status] || "";
      const nm = document.createElement("span");
      nm.className = "sn-name";
      // Version drift names HOW it differs, matching the web popover: behind N (a push delivers
      // exactly those), ahead N (a pull collects them), diverged, or different build (sha unknown
      // here). Shas + the remote commit's date ride the tooltip (progressive disclosure).
      // STALE (the user 2026-07-28): drift comes from the sha of the LAST SUCCESSFUL poll, and only an `up`
      // row polled this pass. Drawn as fact, a host unreachable for hours still announced "behind 2 commits"
      // right beside the word "disconnected" — two claims that cannot both be current. Keep the number (a
      // blank is less useful) but name it as remembered, and date it in the tooltip.
      const stale = !!t.stale;
      const seen = t.lastOk ? new Date(t.lastOk * 1000).toLocaleTimeString() : "";
      let ver = "";
      if (t.outOfDate) {
        const bb = t.behindBy, ab = t.aheadBy;
        ver = " · different build";
        if (typeof bb === "number" && typeof ab === "number") {
          ver = bb > 0 && ab > 0 ? " · diverged"
            : ab > 0 ? ` · ahead ${ab} commit${ab === 1 ? "" : "s"}`
            : bb > 0 ? ` · behind ${bb} commit${bb === 1 ? "" : "s"}` : ver;
        }
        if (stale) ver = ver.replace(" · ", " · last known: ");
      }
      // A connected host reporting NO build at all runs a plain file copy (no git checkout): it cannot
      // name a release or commit, and drift can't be measured — it may be months behind and never say
      // so (the user 2026-08-11, devbox). Say it where the build word sits, matching the web popover.
      const unversioned = t.status === "up" && !t.outOfDate && !t.kernelSha && !t.kernelVer;
      if (unversioned) ver = " · unversioned copy";
      nm.textContent = `${t.host} — ${LBL[t.status] || t.status}` + ver;
      nm.title = (TIP[t.status] || "")
        + (t.outOfDate ? `\n\nRunning ${t.kernelSha || "?"}${t.kernelDate ? " from " + t.kernelDate : ""}; this machine is at ${t.localSha || "?"}.` : "")
        + (unversioned ? `\n\n${t.host} is running romp from a plain file copy — not a git checkout — so it cannot name its release or commit, and how far it is from this machine cannot be measured: it may be far behind and never say so. Reinstall it as a git clone to restore the build name and updates.` : "")
        + (stale && t.outOfDate ? `\nLast confirmed ${seen || "not since this kernel started"}; not re-checked while ${LBL[t.status] || t.status}.` : "")
        + (t.outOfDate && t.checkinPeer
          ? (t.askPull
            ? " No ssh path from this machine (it checked in over its own tunnel), so Update asks it to fast-forward itself over the link it holds."
            : " No ssh path from this machine (it checked in over its own tunnel) — sync from its own dashboard.")
          : "");
      r.append(dot, nm);
      // Federation trust (per-host): trusted = full two-way postal; directed (default) = its mail is
      // HELD for your approval; isolated = dashboard only, no postal. The gate lives in the bus.
      const trust = document.createElement("select");
      trust.className = "sn-trust";
      trust.title = `What happens to postal mail from ${t.host}. trusted: delivered straight to `
        + "your sessions. directed: held for your approval. isolated: none, dashboard only.";
      let pend = pendingTrust.get(t.host);
      if (pend && (t.trust || "directed") === pend) { pendingTrust.delete(t.host); pend = undefined; }
      for (const lvl of ["trusted", "directed", "isolated"]) {
        const o = document.createElement("option");
        o.value = lvl; o.textContent = TRUSTW[lvl];
        if ((pend || t.trust || "directed") === lvl) o.selected = true;
        trust.appendChild(o);
      }
      if (pend) { trust.disabled = true; trust.classList.add("sn-applying"); }
      trust.addEventListener("focus", () => { trustEngaged = true; });      // keyboard path
      trust.addEventListener("mousedown", () => { trustEngaged = true; });  // pointer path, before the popup opens
      trust.addEventListener("blur", releaseTrust);
      trust.addEventListener("change", () => {
        pendingTrust.set(t.host, trust.value);   // ack on the click; re-renders show the chosen level
        trust.disabled = true;
        trust.classList.add("sn-applying");
        fetch(kernelUrl("/tunnels/trust"), { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: t.host, trust: trust.value }) })
          .then((rp) => rp.json())
          .then((d) => { if (!(d && d.ok)) pendingTrust.delete(t.host); schedule(600); })
          .catch(() => { pendingTrust.delete(t.host); schedule(600); });
        releaseTrust();   // the choice is made — land any deferred snapshot now (pendingTrust keeps it painted)
      });
      r.appendChild(trust);
      if (pend) {
        const pn = document.createElement("span");
        pn.className = "sn-pend";
        pn.textContent = "applying…";
        r.appendChild(pn);
      }
      // A push romp is ALREADY doing needs no button — it would only invite a duplicate of the work in
      // flight. The row shows the live phase instead (below); the manual Push returns if it fails.
      const apx = !!(t.autoPush && (t.autoPush.phase === "pushing" || t.autoPush.phase === "waiting"
        || t.autoPush.phase === "pulling" || t.autoPush.phase === "asking"));
      // Every button is gated on the action being PROVABLY possible (the user 2026-07-28, whose laptop was
      // offered a push that could never run). Push needs an ssh route from here AND a straight fast-forward:
      // a checked-in host has no route, and a diverged — or unknown — remote build is refused by the
      // remote's own ancestor check every time (a commit this repo has never seen cannot be an ancestor of
      // its HEAD). Those states get the action that CAN work instead: Pull when the remote is strictly
      // ahead, Update when a checked-in peer is behind, else the drift word and its tooltip.
      if (t.status === "up" && t.fastForward && !apx && !t.checkinPeer) {
        const u = document.createElement("button");
        u.textContent = "Push";
        u.title = `Push this machine's committed romp to ${t.host} and restart its kernel, so it runs exactly this code. `
          + `Uncommitted local edits are not sent, so commit first.`;
        u.addEventListener("click", () => act("/tunnels/update", t.host, u, "Pushing…"));
        r.appendChild(u);
      }
      if (t.status === "up" && t.askPull && !apx) {
        const a = document.createElement("button");
        a.textContent = "Update";
        a.title = `${t.host} checked in over its own tunnel, so this machine cannot push to it. This asks its romp `
          + `to pull these commits from here and restart, over the link it already holds.`;
        a.addEventListener("click", () => act("/tunnels/askpull", t.host, a, "Asking…"));
        r.appendChild(a);
      }
      if (t.status === "up" && t.fastPull && !apx && !t.checkinPeer) {
        const pl = document.createElement("button");
        pl.textContent = "Pull";
        pl.title = `Pull ${t.host}'s newer commits into this machine's romp (fast-forward only; refuses if this tree `
          + `has uncommitted changes). This kernel keeps running the old build until you restart romp.`;
        pl.addEventListener("click", () => act("/tunnels/pull", t.host, pl, "Pulling…"));
        r.appendChild(pl);
      }
      if (t.status === "no-kernel") {
        const s = document.createElement("button");
        s.textContent = "Start";
        s.title = `No kernel is answering on ${t.host}. This pushes this machine's romp there and boots its kernel.`;
        s.addEventListener("click", () => act("/tunnels/start", t.host, s, "Starting…"));
        r.appendChild(s);
      }
      const d = document.createElement("button");
      d.textContent = "Detach";
      d.title = `Close the ssh tunnel to ${t.host}. It stays in this list as a previously-attached host, `
        + `keeping its trust level, so you can re-attach in one click.`;
      d.addEventListener("click", () => act("/tunnels/detach", t.host, d, "…"));
      r.appendChild(d);
      if (t.status === "up") {
        // ITS CONNECTIONS — the keyed expand (progressive disclosure): compact row by default,
        // that machine's own attached list one click deeper, fetched on the click, never the poll.
        const xp = document.createElement("button");
        xp.className = "sn-subtoggle";
        xp.textContent = (openSub.has(t.host) ? "▾" : "▸") + " connections";
        xp.title = `${t.host}'s own attached hosts — see and manage what IT is connected to, from here. `
          + `Rows read live from its kernel over your tunnel + its own token; actions run there.`;
        xp.addEventListener("click", () => {
          if (openSub.has(t.host)) openSub.delete(t.host);
          else { openSub.add(t.host); if (!subInfo.has(t.host)) fetchSub(t.host); }
          if (lastList) renderList(...lastList);
        });
        r.appendChild(xp);
      }
      list.appendChild(r);
      // Live automatic-update phase under the row — the work still announces itself, it just does it here
      // instead of over your screen. A FAILURE stays put and red (fail loudly) rather than vanishing into a
      // silently-stale remote.
      if (t.autoPush) {
        const ap = document.createElement("div");
        ap.className = "sn-ap" + (t.autoPush.phase === "failed" ? " bad" : "");
        ap.textContent = (t.autoPush.phase === "failed" ? "auto-update failed — " : "auto-update: ")
          + (t.autoPush.detail || t.autoPush.phase);
        ap.title = t.autoPush.phase === "failed"
          ? "romp tried to update this host automatically and could not. The manual Push button is back; it will not retry by itself until either machine's commit moves."
          : "romp is updating this host in the background.";
        list.appendChild(ap);
      }
      if (t.status === "up" && openSub.has(t.host)) renderSub(t.host);
    }
    // PREVIOUSLY ATTACHED (the user 2026-07-22): hosts attached before, kept after detach so they are one
    // click away instead of buried in the ssh-config dropdown. Dimmed, each remembering the trust level
    // last set for it — re-attaching a box marked `trusted` will not silently drop back to directed.
    if (known.length) {
      const hd = document.createElement("div");
      hd.className = "sn-khead";
      hd.textContent = "Previously attached";
      hd.title = "Hosts romp remembers. Most were attached before and keep the trust level you last chose, "
        + "so re-attaching restores it. A row marked “trust remembered” was never attached from this "
        + "machine — it only records how to hold that host's mail. Forget removes a host from this list.";
      list.appendChild(hd);
      for (const k of known) {
        const r = document.createElement("div");
        r.className = "sn-row sn-known";
        const dot = document.createElement("span");
        dot.className = "sn-dot";
        dot.style.background = "transparent";
        dot.style.boxShadow = "inset 0 0 0 1.5px #5a5a5a";
        dot.title = "Not attached right now.";
        const nm = document.createElement("span");
        nm.className = "sn-name";
        // A row that only remembers a mail-trust tier says so (the user 2026-08-12, who read
        // "Previously attached" on a machine that never held that tunnel). k.attached is stamped by
        // the attach/detach/check-in writers; a trust-only row never gets it.
        const kwas = !!k.attached;
        nm.textContent = kwas
          ? `${k.host} — not attached · ${k.trust || "directed"}`
          : `${k.host} — trust remembered · never attached here · ${k.trust || "directed"}`;
        nm.title = kwas
          ? "Trust level remembered from the last time this host was attached; re-attaching restores it."
          : `No tunnel to ${k.host} has ever been attached from this machine — this row only records how `
            + "its mail is held (trust is judged by origin, e.g. for a relayed peer). Attaching is still one click.";
        r.append(dot, nm);
        const ra = document.createElement("button");
        ra.textContent = kwas ? "Re-attach" : "Attach";
        ra.title = kwas
          ? `Open the ssh tunnel to ${k.host} again, restoring its remembered trust level.`
          : `Open an ssh tunnel to ${k.host} (first attach from this machine); its remembered trust level rides along.`;
        ra.addEventListener("click", () => act("/tunnels", k.host, ra, "Attaching…"));
        const fg = document.createElement("button");
        fg.textContent = "Forget";
        fg.title = `Remove ${k.host} from this list. It does not touch the host itself; attaching again will re-add it.`;
        fg.addEventListener("click", () => act("/tunnels/forget", k.host, fg, "…"));
        r.append(ra, fg);
        list.appendChild(r);
      }
    }
    // BETWEEN YOUR MACHINES — one row per direction; mechanics on the state block above. Only offered
    // when two machines are live: with fewer there is no pair to speak of.
    if (ts.filter((t) => t.status === "up").length >= 2) {
      const hd = document.createElement("div");
      hd.className = "sn-khead";
      hd.textContent = "Between your machines";
      hd.title = "How your attached machines hold each other's postal mail, one line per direction, read live "
        + "from each machine's own kernel. Changing a line writes to the holding machine through your tunnel "
        + "and its own access token: you are acting on both ends; the machines never set each other's trust.";
      list.appendChild(hd);
      if (pairs && pairs.pairs) {
        for (const pr of pairs.pairs) {
          const dirs: [string, string, string | null][] = [[pr.a, pr.b, pr.ab], [pr.b, pr.a, pr.ba]];
          for (const [hold, frm, tier] of dirs) {
            const r = document.createElement("div");
            r.className = "sn-row sn-known";
            const nm = document.createElement("span");
            nm.className = "sn-name";
            // null = that machine's table was unreadable this pass (named error, retried on the next
            // kick); "" = no explicit row there yet, which its bus treats as directed for a relayed origin.
            if (tier === null) {
              const he = (pairs.hosts && pairs.hosts[hold] && pairs.hosts[hold].error) || "unreadable";
              nm.textContent = `${hold} holds ${frm}'s mail: unreadable — ${he}`;
              nm.title = `Could not read ${hold}'s trust table over the tunnel: ${he}. It keeps gating mail `
                + `by its own last-set levels; retried on the next refresh.`;
              r.appendChild(nm);
              list.appendChild(r);
              continue;
            }
            nm.textContent = `${hold} holds ${frm}'s mail`;
            r.appendChild(nm);
            const pk = `${hold}|${frm}`;
            let pend = pendingPair.get(pk);
            if (pend && (tier || "") === pend) { pendingPair.delete(pk); pend = undefined; }
            const sel = document.createElement("select");
            sel.className = "sn-trust";
            const imp = !tier && !pend ? " Never set explicitly — directed is its default." : "";
            sel.title = `What ${hold} does with postal mail from ${frm}.${imp} trusted: delivered straight `
              + `to its sessions. directed: held on ${hold} for your approval. isolated: none.`;
            for (const lvl of ["trusted", "directed", "isolated"]) {
              const o = document.createElement("option");
              o.value = lvl; o.textContent = TRUSTW[lvl];
              if ((pend || tier || "directed") === lvl) o.selected = true;
              sel.appendChild(o);
            }
            if (pend) { sel.disabled = true; sel.classList.add("sn-applying"); }
            sel.addEventListener("focus", () => { trustEngaged = true; });      // keyboard path
            sel.addEventListener("mousedown", () => { trustEngaged = true; });  // pointer path
            sel.addEventListener("blur", releaseTrust);
            sel.addEventListener("change", () => {
              pendingPair.set(pk, sel.value);   // ack on the click; re-renders show the chosen level
              sel.disabled = true;
              sel.classList.add("sn-applying");
              releaseTrust();   // the choice is made — land any deferred snapshot (pendingPair keeps it painted)
              fetch(kernelUrl("/tunnels/trust-remote"), { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ onHost: hold, host: frm, trust: sel.value }) })
                .then((rp) => rp.json())
                .then((d) => { if (!(d && d.ok)) pendingPair.delete(pk); refreshPairs(); })
                .catch(() => { pendingPair.delete(pk); refreshPairs(); });
            });
            r.appendChild(sel);
            if (pend) {
              const pn = document.createElement("span");
              pn.className = "sn-pend";
              pn.textContent = "applying…";
              r.appendChild(pn);
            }
            list.appendChild(r);
          }
        }
      } else if (pairs && pairs.error) {
        const e = document.createElement("div");
        e.className = "sn-empty";
        e.textContent = `Couldn't read how your machines hold each other: ${pairs.error} — retrying.`;
        list.appendChild(e);
      } else {
        const e = document.createElement("div");
        e.className = "sn-empty";
        e.textContent = "Reading how your machines hold each other…";
        list.appendChild(e);
      }
    }
  }

  // The expanded "connections" block under an up host: THAT machine's attached rows, indented,
  // with the controls its own popover would offer — status/version drift from the fields IT
  // computed (drift there is measured between the via machine and its remote, so the numbers are
  // its own), and every action riding the normal route + {via}. Loading and errors say so; a
  // failed read gets a Retry, never a silent blank.
  function renderSub(via: string) {
    const d = subInfo.get(via);
    if (!d) {
      const e = document.createElement("div");
      e.className = "sn-sub sn-empty";
      e.textContent = `Reading ${via}'s connections…`;
      list.appendChild(e);
      return;
    }
    if (!d.ok) {
      const e = document.createElement("div");
      e.className = "sn-sub sn-empty";
      e.textContent = `Couldn't read ${via}'s connections: ${d.error || "unknown error"} `;
      const rt = document.createElement("button");
      rt.textContent = "Retry";
      rt.addEventListener("click", () => {
        subInfo.delete(via);
        fetchSub(via);
        if (lastList) renderList(...lastList);
      });
      e.appendChild(rt);
      list.appendChild(e);
      return;
    }
    const rows = d.tunnels || [];
    if (!rows.length) {
      const e = document.createElement("div");
      e.className = "sn-sub sn-empty";
      e.textContent = `${via} has no hosts attached.`;
      list.appendChild(e);
      return;
    }
    for (const s of rows) subRow(via, s);
  }

  function subRow(via: string, s: any) {
    const TRUSTW: Record<string, string> = {
      trusted: "trusted (auto-accept)", directed: "directed (held for you)", isolated: "isolated (no mail)",
    };
    const r = document.createElement("div");
    r.className = "sn-row sn-sub";
    const dot = document.createElement("span");
    dot.className = "sn-dot";
    dot.style.background = s.status === "up" ? "var(--accent, #9cd2ff)"
      : (s.status === "error" || s.status === "no-kernel") ? "#E5534B"
      : (s.status === "down") ? "#8a8a8a" : "transparent";
    if (dot.style.background === "transparent") dot.style.boxShadow = "inset 0 0 0 1.5px var(--accent, #9cd2ff)";
    dot.title = TIP[s.status] || "";
    const nm = document.createElement("span");
    nm.className = "sn-name";
    let ver = "";
    if (s.outOfDate) {
      const bb = s.behindBy, ab = s.aheadBy;
      ver = " · different build";
      if (typeof bb === "number" && typeof ab === "number") {
        ver = bb > 0 && ab > 0 ? " · diverged"
          : ab > 0 ? ` · ahead ${ab} commit${ab === 1 ? "" : "s"}`
          : bb > 0 ? ` · behind ${bb} commit${bb === 1 ? "" : "s"}` : ver;
      }
    }
    nm.textContent = `${s.host} — ${LBL[s.status] || s.status}${ver}`;
    nm.title = `${via}'s tunnel to ${s.host}. ` + (TIP[s.status] || "")
      + (s.outOfDate ? `\n\n${s.host} runs ${s.kernelSha || "?"}; ${via} is at ${s.localSha || "?"} — `
        + `drift here is between THOSE two machines, not this one.` : "");
    r.append(dot, nm);
    // trust: what VIA does with this host's mail — written on via over your tunnel + its token,
    // the same you-with-both-tokens boundary as the pair rows. Pending latches per via|host until
    // a later /tunnels/of read agrees (the confirming event, never a timer).
    const pk = `${via}|${s.host}`;
    let pend = pendingSub.get(pk);
    if (pend && (s.trust || "directed") === pend) { pendingSub.delete(pk); pend = undefined; }
    const sel = document.createElement("select");
    sel.className = "sn-trust";
    sel.title = `What ${via} does with postal mail from ${s.host}. trusted: delivered straight to its `
      + `sessions. directed: held on ${via} for approval. isolated: none.`;
    for (const lvl of ["trusted", "directed", "isolated"]) {
      const o = document.createElement("option");
      o.value = lvl; o.textContent = TRUSTW[lvl];
      if ((pend || s.trust || "directed") === lvl) o.selected = true;
      sel.appendChild(o);
    }
    if (pend) { sel.disabled = true; sel.classList.add("sn-applying"); }
    sel.addEventListener("focus", () => { trustEngaged = true; });
    sel.addEventListener("mousedown", () => { trustEngaged = true; });
    sel.addEventListener("blur", releaseTrust);
    sel.addEventListener("change", () => {
      pendingSub.set(pk, sel.value);
      sel.disabled = true;
      sel.classList.add("sn-applying");
      releaseTrust();
      fetch(kernelUrl("/tunnels/trust"), { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ via, host: s.host, trust: sel.value }) })
        .then((rp) => rp.json())
        .then((dd) => { if (!(dd && dd.ok)) pendingSub.delete(pk); fetchSub(via); })
        .catch(() => { pendingSub.delete(pk); fetchSub(via); });
    });
    r.appendChild(sel);
    if (pend) {
      const pn = document.createElement("span");
      pn.className = "sn-pend";
      pn.textContent = "applying…";
      r.appendChild(pn);
    }
    // The same provably-possible gating as the top rows, judged with the fields VIA computed
    // about ITS remote (fastForward/fastPull/askPull are relative to via's own build).
    const apx = !!(s.autoPush && (s.autoPush.phase === "pushing" || s.autoPush.phase === "waiting"
      || s.autoPush.phase === "pulling" || s.autoPush.phase === "asking"));
    if (s.status === "up" && s.fastForward && !apx && !s.checkinPeer) {
      const u = document.createElement("button");
      u.textContent = "Push";
      u.title = `Push ${via}'s committed romp to ${s.host} and restart its kernel — the work runs on ${via}.`;
      u.addEventListener("click", () => act("/tunnels/update", s.host, u, "Pushing…", via));
      r.appendChild(u);
    }
    if (s.status === "up" && s.askPull && !apx) {
      const a = document.createElement("button");
      a.textContent = "Update";
      a.title = `${s.host} checked in to ${via} over its own tunnel, so ${via} cannot push to it. This asks `
        + `it to pull ${via}'s commits over the link it already holds.`;
      a.addEventListener("click", () => act("/tunnels/askpull", s.host, a, "Asking…", via));
      r.appendChild(a);
    }
    if (s.status === "up" && s.fastPull && !apx && !s.checkinPeer) {
      const pl = document.createElement("button");
      pl.textContent = "Pull";
      pl.title = `Pull ${s.host}'s newer commits into ${via}'s romp (fast-forward only) — the work runs on ${via}.`;
      pl.addEventListener("click", () => act("/tunnels/pull", s.host, pl, "Pulling…", via));
      r.appendChild(pl);
    }
    if (s.status === "no-kernel") {
      const st = document.createElement("button");
      st.textContent = "Start";
      st.title = `No kernel answers ${via}'s tunnel to ${s.host}. This has ${via} push its romp there and boot it.`;
      st.addEventListener("click", () => act("/tunnels/start", s.host, st, "Starting…", via));
      r.appendChild(st);
    }
    const dt = document.createElement("button");
    dt.textContent = "Detach";
    dt.title = `Close ${via}'s ssh tunnel to ${s.host}. It stays in ${via}'s previously-attached list.`;
    dt.addEventListener("click", () => act("/tunnels/detach", s.host, dt, "…", via));
    r.appendChild(dt);
    list.appendChild(r);
  }

  // The pair table is read OUTSIDE the poll (each read dials every live machine's kernel over its
  // tunnel): refresh() kicks it, at most one in flight, and the answer repaints from the cached poll
  // args. Fewer than two live hosts means no pairs — clear and skip the dial.
  function refreshPairs(ts?: any[]) {
    if (ts) lastUp = ts.filter((t) => t.status === "up").length;
    if (lastUp < 2) { pairs = null; return; }
    if (pairsBusy) return;
    pairsBusy = true;
    fetch(kernelUrl("/tunnels/pairs"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
      pairsBusy = false;
      pairs = d && d.ok ? d : { error: (d && d.error) || "unreadable" };
      if (!pop.hidden && lastList) renderList(lastList[0], lastList[1]);
    }).catch((err) => {
      pairsBusy = false;
      pairs = { error: String(err) };
      if (!pop.hidden && lastList) renderList(lastList[0], lastList[1]);
    });
  }

  let diagPending = false;   // report the first /tunnels outcome of each open, not every 3s poll
  function refresh() {
    fetch(kernelUrl("/tunnels"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
      const ts = (d && d.tunnels) || [];
      if (diagPending) { diagPending = false; post?.({ type: "clientDiag", surface: "strip", what: "netFetch", data: { ok: true, tunnels: ts.length } }); }
      if (!autoCb.disabled) autoCb.checked = !!(d && d.autoUpdate);   // mirror the kernel; never clobber a write in flight
      lastList = [ts, (d && d.known) || []];
      renderList(lastList[0], lastList[1]);
      refreshPairs(ts);
      // An automatic push in flight counts as busy: the button marches while romp works in the background,
      // and the poll runs fast so the phase reads live.
      const pushing = ts.some((t: any) => t.autoPush && (t.autoPush.phase === "pushing" || t.autoPush.phase === "waiting" || t.autoPush.phase === "pulling"));
      button.classList.toggle("busy", ts.some((t: any) => busy(t.status)) || pushing);
      schedule(ts.some((t: any) => busy(t.status)) || pushing ? 600 : 3000);   // fast while mid-attach/pushing, slow keep-alive after
    }).catch((err) => {
      // Fail loudly: an unreachable kernel renders as an error line, never a
      // silently empty box that reads as a dead button.
      if (diagPending) { diagPending = false; post?.({ type: "clientDiag", surface: "strip", what: "netFetch", data: { ok: false, err: String(err) } }); }
      list.textContent = "";
      const e = document.createElement("div");
      e.className = "sn-empty";
      e.textContent = `Couldn't reach the kernel (${(window as any).__rompKernelBase || "same origin"}) — retrying…`;
      list.appendChild(e);
      schedule(3000);
    });
  }

  attach.addEventListener("click", () => {
    if (!sel.value) return;
    act("/tunnels", sel.value, attach, "Attaching…");
    setTimeout(() => { attach.disabled = false; attach.textContent = "Attach"; }, 2000);
  });
  const setOpen = (open: boolean) => {
    pop.hidden = !open;
    button.classList.toggle("open", open);   // instant acknowledgment on the button itself
    if (!open) clearTimeout(timer);
    trustEngaged = false; deferredRender = null;   // a toggled popover starts (or leaves) unengaged — no stale latch
  };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(pop.hidden);
    if (!pop.hidden) {
      // Instant content before any round-trip: the box never opens blank.
      if (!list.childElementCount) {
        const e2 = document.createElement("div");
        e2.className = "sn-empty";
        e2.textContent = "Checking remotes…";
        list.appendChild(e2);
      }
      // Anchor just above the strip however many rows it wrapped to. offsetWidth
      // math is layout-px; style px are layout px too, so this stays zoom-safe.
      const strip = document.getElementById("romp-strip");
      if (strip) pop.style.bottom = `${strip.offsetHeight + 6}px`;
      diagPending = true;
      pairs = null;   // fresh read per opening — the loader line, then live data (never a stale table)
      loadHosts();
      refresh();
    }
    post?.({ type: "clientDiag", surface: "strip", what: "netToggle",
             data: { open: !pop.hidden, base: (window as any).__rompKernelBase || "" } });
  });
  document.addEventListener("click", (e) => {
    if (!pop.hidden && !pop.contains(e.target as Node) && e.target !== button) setOpen(false);
  });
}
