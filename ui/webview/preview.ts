// File-preview helpers shared by the chat (render.ts path thumbnails) and the feed (feed.ts artifact
// strips) — the user 2026-07-08: when an agent produces a plot/PDF/screenshot, show the thing, not just
// its path. The bytes come from the kernel's `/file?path=…&sid=…` endpoint (extension-allowlisted,
// existence-checked, behind the same auth as every route), so a preview is only ever what the kernel
// can actually read RIGHT NOW — a deleted/hallucinated path 404s and the <img> onerror hides the thumb
// (event-based; no stale placeholders). Web dashboard only: the VS Code webview sandbox can't reach the
// kernel origin from an <img>, so callers gate on canPreview() and keep the plain click-to-open link.

import { hostOf, bareId } from "./host-prefix";
import { mediaSrc } from "./media";

// Extensions the kernel's _PREVIEW_MIME serves — keep the two lists in step (tests pin both).
const IMG_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);

export type PreviewKind = "img" | "pdf";

export function previewKind(path: string): PreviewKind | null {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (IMG_EXT.has(ext)) return "img";
  if (ext === "pdf") return "pdf";
  return null;
}

// Previews load over the page's own origin, so they only work where the page IS the kernel
// (the web dashboard). In the VS Code webview (vscode-webview: origin) a relative /file URL
// resolves nowhere — callers keep the existing openFile behavior there.
export function canPreview(): boolean {
  return location.protocol === "http:" || location.protocol === "https:";
}

// LOADING CUE (the user 2026-07-31): a remote image's bytes arrive over the ssh tunnel, so for a
// beat the message showed only the path text and the picture "popped in" with nothing saying it was
// on the way. Per the loading-state rule the first thing up is the romp swirl: a mini spinning glyph
// holds the image's spot until its `load` event lands (event-based; an error still removes the whole
// box, spinner included — no backstop needed because the cue dies with its box either way). Memoized
// per URL for this page life: chat re-renders rebuild these elements constantly, and re-flashing a
// spinner over bytes the browser just painted would itself be flicker — only a URL's FIRST load spins.
const loadedOnce = new Set<string>();

// A manual retry's swirl stays up at least this long before a failure may swap the chip back in —
// an instant connection reset otherwise flashes it for one frame and the tap looks ignored (the
// user 2026-08-16). Presentation smoothing only: the failed state is already decided, this paces
// nothing but the paint.
const MIN_RETRY_SPIN_MS = 400;

// Blob types for the resumable retry's assembled bytes (an <img> renders a typed blob everywhere;
// untyped leans on sniffing). Keyed by extension, mirroring IMG_EXT.
const IMG_MIME: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
  webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
};

// Fully-fetched previews for this page life: original URL → object URL. The chat re-renders its
// messages constantly, and a resumable fetch bypasses the HTTP cache (no-store) — without this memo
// every re-render would re-pull the whole image over the very link that struggled to deliver it
// once. Bounded; the evicted entry's blob is released.
const resolvedUrls = new Map<string, string>();
function rememberResolved(url: string, objUrl: string): void {
  resolvedUrls.set(url, objUrl);
  if (resolvedUrls.size > 24) {
    const oldest = resolvedUrls.entries().next().value as [string, string];
    resolvedUrls.delete(oldest[0]);
    URL.revokeObjectURL(oldest[1]);
  }
}

function fmtBytes(got: number, total: number): string {
  const h = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  return total ? h(got) + " of " + h(total) : h(got);
}

// The fixed-footprint wait box shared by the retrying swirl AND the failure chip, so retry churn
// never shifts the layout under the reader (the user 2026-08-16: the chat scroll thrashed by about
// a line as the states swapped heights).
function mkWait(box: HTMLElement): HTMLElement {
  box.textContent = "";
  const wait = document.createElement("span");
  wait.className = "path-full-wait";
  box.appendChild(wait);
  return wait;
}

function withLoadCue(box: HTMLElement, img: HTMLImageElement, url: string): void {
  if (loadedOnce.has(url)) return;
  const spin = document.createElement("img");
  spin.className = "path-load-spin";
  spin.src = mediaSrc("romp-swirl-glyph.svg");
  spin.alt = "loading preview…";
  spin.title = "loading preview…";
  box.appendChild(spin);
  img.classList.add("path-img-loading");
  img.addEventListener("load", () => {
    loadedOnce.add(url);
    spin.remove();
    img.classList.remove("path-img-loading");
  });
}

// The kernel serves the bytes; sid lets it resolve a relative path against THAT session's cwd
// (same resolution as click-to-open — kernel _resolve_open_path). A FEDERATED session's file lives
// on the REMOTE machine's disk, so a host-prefixed sid (`gpu1:‹uuid›` — see federation.ts) routes
// through this kernel's /remote/<host>/file relay, the HTTP twin of the /remote/<host>/ws splice,
// with the bare sid the remote kernel actually knows (the user 2026-07-31: mentioned plots on a
// remote session's chat never rendered — /file read the LOCAL disk and 404'd). Still a same-origin
// URL, so it works wherever the dashboard is viewed from (the phone over `tailscale serve` included).
export function fileUrl(path: string, sid?: string | null): string {
  const host = sid ? hostOf(sid) : "";
  const base = host ? "/remote/" + encodeURIComponent(host) + "/file" : "/file";
  const bare = sid ? bareId(sid) : "";
  return base + "?path=" + encodeURIComponent(path) + (bare ? "&sid=" + encodeURIComponent(bare) : "");
}

// Full-view lightbox: dark backdrop, the image at natural-but-capped size or the PDF in the browser's
// native viewer, filename caption. One singleton element; backdrop click / Esc / ✕ closes. Styles live
// in BOTH styles.css and feed.css (each page loads only its own sheet — the .romp-acted precedent).
export function openLightbox(path: string, sid?: string | null, pin?: string): void {
  document.getElementById("romp-lightbox")?.remove();
  const kind = previewKind(path);
  if (!kind) return;
  const wrap = document.createElement("div");
  wrap.id = "romp-lightbox";
  const inner = document.createElement("div");
  inner.className = "romp-lightbox-inner" + (kind === "pdf" ? " pdf" : "");
  if (kind === "pdf") {
    const frame = document.createElement("iframe");
    frame.className = "romp-lightbox-frame";
    frame.src = fileUrl(path, sid);
    frame.title = path;
    inner.appendChild(frame);
  } else {
    const img = document.createElement("img");
    img.className = "romp-lightbox-img";
    img.src = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
    img.alt = path;
    inner.appendChild(img);
  }
  const bar = document.createElement("div");
  bar.className = "romp-lightbox-bar";
  const name = document.createElement("span");
  name.className = "romp-lightbox-name";
  name.textContent = path;
  name.title = path;
  // download rides an ANCHOR with the download attribute (the user 2026-08-19): the browser saves
  // the same bytes the lightbox is showing — the pinned URL when a pin rode in, so a re-generated
  // file can't swap the image between viewing and saving. The filename is the path's basename.
  const dl = document.createElement("a");
  dl.className = "romp-lightbox-dl";
  dl.href = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
  dl.download = path.slice(path.lastIndexOf("/") + 1) || "image";
  // the tray icon every download control should wear (the composer buttons' stroke family) as an
  // inline SVG: the old text glyph (U+2B73, arrow-to-bar) has no coverage in the mac system fonts
  // and rendered as a tofu box instead of an icon (the user 2026-08-19). A literal — no sanitize.
  dl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
    + '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  dl.title = "download";
  dl.setAttribute("aria-label", "download");
  dl.onclick = (ev) => ev.stopPropagation();               // saving must not also dismiss
  const close = document.createElement("button");
  close.className = "romp-lightbox-close";
  close.textContent = "✕";
  close.title = "close (Esc)";
  bar.append(name, dl, close);
  inner.appendChild(bar);
  wrap.appendChild(inner);
  const dismiss = () => { wrap.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") { ev.stopPropagation(); dismiss(); } };
  close.onclick = (ev) => { ev.stopPropagation(); dismiss(); };
  wrap.onclick = (ev) => { if (ev.target === wrap) dismiss(); };   // backdrop closes; content clicks don't
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(wrap);
}

// A thumbnail element for `path`: an <img> that REMOVES ITSELF if the kernel can't serve the file
// (404/413 → onerror), so a mentioned-but-missing path costs nothing. Click opens the lightbox.
// PDFs get a labeled doc-chip instead of pixels (no server-side rendering); same click behavior.
export function previewThumb(path: string, sid?: string | null): HTMLElement | null {
  const kind = previewKind(path);
  if (!kind || !canPreview()) return null;
  const box = document.createElement("span");
  box.className = "path-thumb";
  box.title = "click to preview " + path;
  if (kind === "pdf") {
    box.classList.add("pdf");
    const tag = document.createElement("span");
    tag.className = "path-thumb-tag";
    tag.textContent = "PDF";
    const nm = document.createElement("span");
    nm.className = "path-thumb-name";
    nm.textContent = path.slice(path.lastIndexOf("/") + 1);
    box.append(tag, nm);
    // a chip can't self-verify like an <img> — probe so a missing PDF never shows a dead chip
    fetch(fileUrl(path, sid), { method: "HEAD" }).then((r) => { if (!r.ok) box.remove(); }).catch(() => box.remove());
  } else {
    const img = document.createElement("img");
    img.className = "path-thumb-img";
    const url = fileUrl(path, sid);
    img.src = url;
    img.alt = path;
    img.loading = "lazy";
    img.onerror = () => box.remove();
    withLoadCue(box, img, url);   // mini swirl holds the spot until the load event (first load only)
    box.appendChild(img);
  }
  box.onclick = (ev) => { ev.stopPropagation(); openLightbox(path, sid); };
  return box;
}

// FULL-SIZE inline render for a mentioned image in the CHAT (the user 2026-07-20, who wanted not even a
// thumbnail but a rendered image, like the user messages). Same self-verification as previewThumb —
// a path the kernel can't serve removes itself — and an image click still opens the lightbox. Images
// render at the user-image scale (.path-full-img mirrors .user-img's 320px cap, one size per
// information type). A PDF is a labeled CARD, not an auto-loading inline viewer (click → lightbox):
// the first cut embedded an <iframe> per mentioned PDF, and a browser set to "Download PDFs" (or one
// that declines to render inline) saved a FRESH COPY on every chat re-render — the user's Downloads
// folder silently filled with datasheet copies (2026-07-20). A fetch must be user-initiated, once.
// Web only — callers gate on canPreview and fall back per surface. The feed's artifact strips
// deliberately KEEP previewThumb: cards stay glanceable, the chat is where the full render lives.
// `verified`: the KERNEL already stat'd this path (spacePaths / a pathLinks verdict), so a load
// error is TRANSIENT — the kernel restarting mid-fetch, a tunnel blip — not a dead path. Removing
// the box then erases the preview silently until some later re-render (the user 2026-08-15, who sat
// through exactly that: verified path pills, no images, no cue). A verified path's failure therefore
// stays VISIBLE — a "preview unavailable — tap to retry" chip in the figure's spot — per the
// fail-loudly rule. Only an UNVERIFIED path (old kernel, no pathLinks key) keeps self-removal:
// there the error really does mean "no such file".
export function previewFull(path: string, sid?: string | null, verified = false, pin?: string): HTMLElement | null {
  const kind = previewKind(path);
  if (!kind || !canPreview()) return null;
  const box = document.createElement("span");
  box.className = "path-full" + (kind === "pdf" ? " pdf" : "");
  box.title = path;
  if (kind === "pdf") {
    box.classList.add("path-full-pdfcard");
    const tag = document.createElement("span");
    tag.className = "path-thumb-tag";
    tag.textContent = "PDF";
    const nm = document.createElement("span");
    nm.className = "path-thumb-name";
    nm.textContent = path.slice(path.lastIndexOf("/") + 1);
    box.append(tag, nm);
    box.style.cursor = "pointer";
    box.title = "click to view " + path;
    box.onclick = (ev) => { ev.stopPropagation(); openLightbox(path, sid); };
    // a chip can't self-verify like an <img> — HEAD-probe (headers only, no body — never a download)
    // so a missing PDF never shows a dead card. A kernel-VERIFIED card skips the probe: the kernel
    // said the file exists, and a transient probe failure must not erase the card.
    // an UNVERIFIED card's failed probe HIDES the card and keeps it registered for the heal
    // events — never removed from the DOM: one transient failure (a kernel-restart window, a tunnel blip)
    // used to erase the figure until a send's re-render minted a fresh box (the user 2026-08-24).
    // The hidden sentinel keeps the spot healable with zero visual noise when the mention really
    // is dead; a probe that later succeeds unhides the card in place.
    if (!verified) {
      const probe = () => fetch(fileUrl(path, sid), { method: "HEAD" })
        .then((r) => { if (r.ok) { box.style.display = ""; } else { box.style.display = "none"; failedPreviews.set(box, probe); } })
        .catch(() => { box.style.display = "none"; failedPreviews.set(box, probe); });
      probe();
    }
  } else {
    // `pin` freezes this MESSAGE's embed to its mention-time bytes (kernel _pin_mention): the sid
    // rides too (the pin store lives on the owning kernel; the relay forwards the query untouched),
    // and a pin whose blob was evicted falls back server-side to the live file.
    const url = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
    // A verified preview whose fetch died usually died because the KERNEL was away (a restart mid-
    // deploy — the 2026-08-15 report hit exactly the converge-restart window), and delta-send never
    // rebuilds an old turn's DOM, so the chip would otherwise sit until a human tapped it. Bounded so
    // a genuinely-dead file settles on the tap chip instead of re-fetching on every push forever —
    // but an attempt that MADE PROGRESS refills the budget (see the resumable retry below): forward
    // motion is the event proving the link works sometimes, and only truly dead attempts spend it.
    let autoRetries = 3;
    let chipHealedErr: string | null = null;    // the error a settled chip already spent its one heal on
    let fails = 0;                                   // total failed attempts — the chip's copy escalates
    // RESUMABLE RETRY STATE (the user 2026-08-16, on flaky wifi: every retry restarted the transfer
    // from byte 0, so a large figure never finished arriving — and the swirl gave no idea how far it
    // got). The happy path below stays a plain <img> (the browser cache makes the chat's constant
    // re-renders free); once a load has FAILED, retries switch to a managed fetch that keeps every
    // byte received so far and asks the kernel for the REST (Range: bytes=N-, honored by /file and
    // across the federation relay). A dropping link then finishes the picture ACROSS attempts, with
    // the swirl narrating real progress ("1.2 of 3.4 MB" — content-length makes it knowable). No
    // artificial deadline anywhere: only a real network error ends an attempt.
    let parts: Uint8Array[] = [];
    let got = 0;
    let total = 0;
    let fetching = false;                            // one managed attempt at a time (a tap mid-fetch no-ops)
    let lastErr = "";                                // the newest attempt's server-side reason, shown verbatim
    const showChip = () => {
      if (!box.isConnected) return;                  // the turn re-rendered; a fresh box owns this spot now
      // ONE continuous narrative while the machinery is still going (the user 2026-08-16, third
      // report: the box flipped between "trying" and "unavailable" on every auto-retry cycle even
      // though it eventually loaded — the state bounced, so the UI read as impatient). While bounded
      // auto-retries remain, the wait box KEEPS its loading persona — swirl + a note carrying the
      // failure and the plan ("dropped at 1.2 MB of 3.4 MB — retrying · tap to retry now"), the
      // whole box tappable — and the ⚠ chip appears only when the budget is genuinely spent. A
      // repeat failure must still READ as a response to a tap: the note re-pulses on swap-in.
      // INFRASTRUCTURE-DOWN failures are FREE (the user 2026-08-17: figures gave up seconds after a
      // kernel restart — the tunnel re-dial window produces instant "no attached host" 404s and
      // "tunnel not answering" 502s, and three of those spent the whole budget right before the link
      // came back). A failure that names the LINK, not the image, doesn't decrement: the preview
      // keeps retrying on every kernel push until the tunnel is up, and only real verdicts — a true
      // not-found from the owning kernel, a transfer that died with zero progress — spend attempts.
      const transient = /tunnel to .* is not answering|no attached host|re-dialing/i.test(lastErr);
      if (autoRetries > 0 || transient) {
        if (!transient) autoRetries--;
        failedPreviews.set(box, () => build(true));
        const wait = mkWait(box);
        wait.title = path + " — tap to retry now";
        wait.style.cursor = "pointer";
        wait.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
        const spin = document.createElement("img");
        spin.className = "path-load-spin";
        spin.src = "/media/romp-swirl-glyph.svg";
        spin.alt = "loading preview…";
        const note = document.createElement("span");
        note.className = "path-load-note";
        note.textContent = (got > 0 ? "connection dropped at " + fmtBytes(got, total)
                                    : lastErr || "connection dropped")
                           + " — retrying · tap to retry now";
        wait.append(spin, note);
        if (fails > 1) {
          note.classList.add("path-retry-flash");
          note.addEventListener("animationend", () => note.classList.remove("path-retry-flash"), { once: true });
        }
        return;
      }
      const wait = mkWait(box);
      const chip = document.createElement("span");
      chip.className = "path-full-retry";
      // the budget is spent: three attempts gained nothing (progress refills it), so say so plainly
      chip.textContent =
        (got > 0 ? "⚠ connection dropped at " + fmtBytes(got, total)
                 : lastErr ? "⚠ " + lastErr
                 : (fails > 1 ? "⚠ still unavailable" : "⚠ preview unavailable"))
        + " — tap to retry";
      chip.title = path;
      chip.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
      wait.appendChild(chip);
      // A settled chip still rides the push-heal (the user 2026-08-18: "they never render on their
      // own — only when I send a message"): only the retrying branch registered for the heal, so a
      // spent budget dropped the box from the map forever — pushes and tunnel recovery ignored it,
      // and a send only "worked" because the tail re-render minted a FRESH box. One heal attempt
      // per registration, and the box re-registers ONLY when the error CHANGED (new information —
      // the same verdict re-answered is no reason to fetch again): a truly-dead figure costs one
      // extra fetch per new-evidence transition, never one per push.
      if (lastErr !== chipHealedErr) {
        failedPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 1; build(true); });
      }
      // …and a RECONNECT-class event (romp:wsup / hostUp) heals a settled chip REGARDLESS of the
      // error text (the user 2026-08-24): a byte-identical 404 while the file was still being
      // written — or a constant connection-refused — parked the chip inert forever, though the
      // link coming back is new information even when the words didn't change. The budget refills
      // exactly like a send's fresh box; reconnects are rare, so this can't hammer.
      settledPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 3; build(true); });
      if (fails > 1) {
        chip.classList.add("path-retry-flash");
        chip.addEventListener("animationend", () => chip.classList.remove("path-retry-flash"), { once: true });
      }
    };
    const failAfterBeat = (started: number) => {
      fails++;
      // A retry that dies instantly (a dead tunnel resets the connection in milliseconds) would
      // flash the swirl for one frame and put back an identical chip — an ignored-looking tap.
      // Hold the swirl to a perceivable beat before swapping. Presentation smoothing only: the
      // attempt has already failed, and the auto-heal registration rides the same swap.
      const left = MIN_RETRY_SPIN_MS - (Date.now() - started);
      if (left > 0) setTimeout(showChip, left); else showChip();
    };
    const mkImg = (src: string) => {
      const img = document.createElement("img");
      img.className = "path-full-img";
      img.src = src;
      img.alt = path;
      img.loading = "lazy";
      img.onclick = (ev) => { ev.stopPropagation(); openLightbox(path, sid, pin); };
      return img;
    };
    // every tap READS as a tap even when the click lands mid-attempt and build() no-ops on its
    // `fetching` guard (the buttons-always-acknowledge rule: an unacknowledged tap gets re-tapped)
    const ackTap = (ev: Event) => {
      const t = ev.currentTarget as HTMLElement | null;
      if (!t) return;
      t.classList.add("path-retry-flash");
      t.addEventListener("animationend", () => t.classList.remove("path-retry-flash"), { once: true });
    };
    const resumeFetch = async (note: HTMLElement) => {
      const gotBefore = got;
      const r = await fetch(url, { cache: "no-store",
                                   headers: got > 0 ? { Range: "bytes=" + got + "-" } : {} });
      if (r.status === 206) {
        // the kernel continues our partial — the entity size rides Content-Range's "/<size>" tail
        total = parseInt((r.headers.get("Content-Range") || "").split("/")[1] || "0", 10) || total;
      } else if (r.ok) {
        parts = []; got = 0;                         // full body (no range asked, or the server restarted us)
        total = parseInt(r.headers.get("Content-Length") || "0", 10) || 0;
      } else {
        // the error BODY is the diagnostic (the kernel's 502 says "tunnel to <host> is not
        // answering") — a bare status code hid that the IMAGE was fine and the LINK was down
        let why = "";
        try { why = ((await r.text()) || "").split("\n")[0].slice(0, 120); } catch { /* body unavailable */ }
        // a refused status VOIDS the resume state (the user 2026-08-18, whose re-generated figures
        // never loaded): the file changed under our offset — an agent re-plotting the same name
        // shrinks it — and the kernel's 416 expects the client to RESTART cleanly. Keeping `got`
        // made every later attempt, tap and heal alike, replay the same stale Range and fail
        // deterministically fast, while a send's fresh box (got=0) rendered instantly.
        parts = []; got = 0;
        throw new Error(why || "http " + r.status);
      }
      const reader = r.body!.getReader();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parts.push(value);
          got += value.byteLength;
          note.textContent = "fetching… " + fmtBytes(got, total);
        }
      } finally {
        if (got > gotBefore) autoRetries = 3;        // progress refills the budget — the link works sometimes
      }
      if (total && got < total) throw new Error("cut at " + got);   // stream ended early → resume next attempt
      const blob = new Blob(parts as BlobPart[], { type: IMG_MIME[path.slice(path.lastIndexOf(".") + 1).toLowerCase()] || "" });
      return URL.createObjectURL(blob);
    };
    const build = (bust: boolean) => {
      const done = resolvedUrls.get(url);
      if (done) {                                    // already fully fetched this page-life → instant
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(done));
        return;
      }
      if (!bust) {                                   // first attempt: the plain <img> happy path
        box.textContent = "";
        const img = mkImg(url);
        img.onerror = () => {
          // unverified → the SAME retry machinery as verified, just invisible while failed (see
          // the probe note above): the box hides instead of wearing the chip, and a later success
          // unhides it — never self-removed, which erased the spot until a send re-rendered
          if (!verified) box.style.display = "none";
          failAfterBeat(0);                          // no beat on the first attempt — the cue was already up
        };
        withLoadCue(box, img, url);   // mini swirl holds the spot until the load event (memo on the un-busted url)
        box.appendChild(img);
        return;
      }
      if (fetching) return;
      fetching = true;
      const started = Date.now();
      const wait = mkWait(box);
      const spin = document.createElement("img");
      spin.className = "path-load-spin";
      spin.src = "/media/romp-swirl-glyph.svg";
      spin.alt = "loading preview…";
      const note = document.createElement("span");
      note.className = "path-load-note";
      note.textContent = got > 0 ? "resuming… " + fmtBytes(got, total) : "fetching…";
      wait.append(spin, note);
      resumeFetch(note).then((objUrl) => {
        fetching = false;
        lastErr = "";
        rememberResolved(url, objUrl);
        loadedOnce.add(url);                         // re-renders skip the cue — the bytes are in hand
        if (!box.isConnected) return;
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(objUrl));
      }).catch((e: unknown) => {
        fetching = false;
        lastErr = String((e as Error)?.message || "");
        if (lastErr.startsWith("cut at ")) lastErr = "";   // a mid-stream cut narrates via got/fmtBytes
        if (!verified) box.style.display = "none";         // hidden while failed, healable — never removed
        failAfterBeat(started);
      });
    };
    build(false);
  }
  return box;
}

// Failed VERIFIED previews awaiting recovery. A kernel push arriving IS the kernel-is-back event —
// no pushes arrive while it's down, so retrying on push can't spam — and render.ts calls this on
// every incoming kernel message, healing the chips without a tap (event-based; the tap chip stays
// as the manual path and the backstop once a box's bounded auto-retries are spent).
const failedPreviews = new Map<HTMLElement, () => void>();
export function retryFailedPreviews(): void {
  if (!failedPreviews.size) return;
  for (const [box, rebuild] of Array.from(failedPreviews.entries())) {
    failedPreviews.delete(box);                      // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();                  // a re-rendered turn made a fresh box — let the old go
  }
}

// Settled chips (auto-retry budget spent) awaiting a RECONNECT-class heal — romp:wsup (this page's
// kernel socket came back) or hostUp (a federated tunnel recovered). Drained only by these events,
// never by the per-message heal above, so a dead figure costs one fetch per reconnect, not per push.
const settledPreviews = new Map<HTMLElement, () => void>();
export function refreshSettledPreviews(): void {
  if (!settledPreviews.size) return;
  for (const [box, rebuild] of Array.from(settledPreviews.entries())) {
    settledPreviews.delete(box);                     // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();
  }
}

// Markdown-inline <img> (a figure pasted as markdown in a message body) had NO failure handling at
// all: DOMPurify strips inline handlers (correctly — untrusted transcript HTML) and nothing
// re-attached one, so a load that failed once sat as a dead element in the cached DOM until a send
// re-rendered the turn (the user 2026-08-24). Error events don't bubble but DO capture: one
// document-level capture listener covers every md() img on the page — no per-render wiring — and
// registers the element in the same failedPreviews machinery, so every kernel message re-attempts
// it. Previews' own <img>s are skipped: their machinery (budgets, resume, chips) owns those.
let mdImgHealOn = false;
export function installMdImgHeal(): void {
  if (mdImgHealOn) return;                           // ensure-once (the click-safety installation rule)
  mdImgHealOn = true;
  document.addEventListener("error", (e) => {
    const img = e.target as HTMLImageElement | null;
    if (!img || img.tagName !== "IMG") return;
    const src = img.src || "";
    if (!src || src.startsWith("data:")) return;     // a broken data: URI has no server to heal
    if (img.onerror || img.closest(".path-full")) return;   // the preview machinery retries its own
    failedPreviews.set(img, () => {
      const u = img.src;
      img.removeAttribute("src");
      img.src = u;                                   // a fresh attempt; a repeat error re-registers here
    });
  }, true);
}
