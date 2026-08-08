# iOS: the dashboard as a home-screen app

Status: ALL THREE proposals IMPLEMENTED, landing in the commit that adds this file. Proposals 1
and 2 came first (manifest + icons + Apple metas + iOS-standalone safe-area, and Web Push end to
end — `/sw.js`, `/push/*`, VAPID, the `_push_notify` sink beside `_system_notify`, the tab-bar
bell), live-verified on a real iPhone on 2026-08-07; then 3 plus tap-to-open on 2026-08-08, after
the first real push opened the app on a session OTHER than the one that fired it — the bug that
motivated the routing metadata below. A push now carries the firing card's sid and the
needs-you count as routing metadata: the notification tap lands on that session (live window →
SW postMessage → focus into the chat pane; cold start → `/?push-reveal=` → `POST /reveal` parks a
wid-aimed focus consumed on that window's chat `ready` — an exact event, no delay heuristics),
and the app icon wears the count (`navigator.setAppBadge` — the SW paints it while the app is
closed, the shell trues it up over its WS on connect and on every change).
Implementation notes that amend this sketch:
- The shell background is `#1e1e1e`, not the `#101418` guessed below (that is the login page);
  the manifest and theme-color use `#1e1e1e`.
- The manifest and the three icon PNGs are served auth-EXEMPT: browsers fetch a manifest (and
  its icon list) with credentials omitted, so behind the token gate the install sheet gets a 403.
  Fixed three-name allowlist; the rest of `/media/` stays gated.
- Safe-area insets are gated to iOS standalone only (`navigator.standalone` sets
  `html.ios-standalone` and flips `viewport-fit=cover` at runtime), because the browser-mode
  cover/env() combinations regressed Android Chrome and Firefox twice in 2026-06 — see the
  comments and tests around `#mtabs`.
- Web Push's crypto rides the python `cryptography` package as the kernel's one soft dependency:
  `/push/subscribe` answers a plain 500 naming it when missing (fail loudly), and CI installs it
  so the tests run rather than skip.

The user's ask (2026-08-07): they run the dashboard on an iPhone in a browser over their tailnet
and want it to feel like a "main app" — its own icon, launched full-screen, present in the app
switcher — rather than a tab. This doc records what that takes and stacks the related
iOS-specific enhancements so they get decided together.

## Verified findings (2026-08-07, read of the tree at 7cff0391)

1. **The pages already speak mobile, but not "installable".** Every kernel-served page carries a
   `viewport` meta and the shell has real touch work (single full-screen pane + bottom tab bar on
   narrow viewports, `100dvh` pinning, pinch handling, a mobile actions row). What is MISSING is
   everything Safari/Chrome consult for home-screen installs: there is no web app manifest, no
   `apple-mobile-web-app-capable` / status-bar meta, and no `apple-touch-icon` — the only icon is
   the SVG favicon (`/media/romp-swirl-glyph.svg`), which iOS ignores for touch icons (it wants
   PNG at fixed sizes). Net: "Add to Home Screen" works today but yields a bookmark that opens as
   a browser tab, chrome and all.
2. **Auth already fits the install flow, with one wrinkle.** The dashboard rides the serve token:
   first visit seeds a year-long `romp_token` cookie (`SameSite=Strict; HttpOnly`), and an
   unauthenticated GET of `/` renders a self-contained paste-the-token login page. A home-screen
   web app on iOS gets its OWN cookie container — the browser's cookie does not carry over — so
   the first launch from the icon lands on that login page once, then holds its own year cookie.
   That is the security model working as designed; the plan changes nothing about it, but the
   login page is the first thing the installed app shows, which is worth knowing before anyone
   calls it a bug.
3. **Notifications never reach the phone today.** The bell system (session `notify` flag, per-card
   bells in `notify-cards.json`) fires `_system_notify` — `osascript` on macOS, `notify-send`
   elsewhere — i.e. OS notifications on the KERNEL's machine. The detection is already exactly
   the right event (a fresh feed build diffed against the previous one, armed cards entering
   needs-you/completed); only the delivery is desk-bound.
4. **iOS gates Web Push on installation.** Since iOS 16.4, web apps can receive push
   notifications — but ONLY once added to the home screen (and served over HTTPS, which the
   tailnet proxy already provides). So proposal 1 is also the prerequisite for proposal 2.

## Proposal 1 — installable app: manifest + Apple meta + touch icons (small)

- A `/manifest.webmanifest` route on the kernel: name/short name, `start_url: /`,
  `display: standalone`, `background_color`/`theme_color` matching the shell (`#101418`), icons.
- PNG renders of the swirl glyph at 180 (apple-touch-icon), 192 and 512 px, served under
  `/media/`, checked in as assets (eyeball them before release like the other `docs/assets`
  media — they are images, outside the text-only privacy scans).
- `<link rel=manifest>`, `<link rel=apple-touch-icon>`, `apple-mobile-web-app-capable` and
  status-bar-style metas on the LANDING SHELL only — the iframes (feed/chat/timeline) are panes
  inside it, never install targets.
- Safe-area insets (`env(safe-area-inset-*)`) on the shell's bottom tab bar / rail, since
  standalone mode removes the browser chrome that used to keep it off the home indicator.
- No service worker in this step: romp is useless offline by nature (everything is a live kernel
  feed), and a cache-first worker would fight the existing stale-bundle detection. Installability
  on iOS does not require one.

Interaction with the pending clickjacking hardening (HANDOFF: add
`frame-ancestors 'self'` / `X-Frame-Options: SAMEORIGIN`): no conflict — a home-screen app is a
top-level load, not a frame, and the shell's own same-origin iframes stay allowed. Landing both
in the same season means one live-client verification pass covers the two.

## Proposal 2 — needs-you pushes to the phone (medium, after 1)

Deliver the bell events to installed home-screen apps via Web Push, so "blocked on you" reaches
the person wherever they are — the philosophy's "interrupt only when the human is the
bottleneck", extended to the pocket. Sketch:

- A minimal service worker whose ONLY job is `push`/`notificationclick` (no caching, sidestepping
  the stale-bundle concern above).
- A `/push/subscribe` route storing subscriptions under `$STATE`, keyed per device; kernel-side
  VAPID keys minted once at install.
- `_feed_notifications` gains a second sink next to `_system_notify`: same armed-card events,
  same silent first-build baseline, pushed to every stored subscription; dead subscriptions
  (`410 Gone`) pruned on send.
- Scope note: this is the first outbound network delivery the kernel makes (push endpoints are
  Apple/Google servers). Payloads should carry the card TITLE and nothing more, and the feature
  stays opt-in per device by construction (a push subscription only exists if that device
  subscribed).

## Proposal 3 — small standalone-mode polish (cheap, with 1)

- App-icon badge (`navigator.setAppBadge`) with the needs-you count — the glanceable "does
  anything need me" signal, no notification required.
- `display-mode: standalone` media query to hide the "open in app"-style hints that make no sense
  once installed, if any accrue.

## Open questions

- Icon: is a plain raster of the swirl glyph on the shell background good enough, or does the
  user want distinct artwork for the home screen?
- Should the login page detect standalone mode and say "paste the token once; this container
  keeps its own cookie" so the first launch reads as expected rather than as being signed out?
- Web Push through a tailnet-only kernel still works (the PHONE's subscription rides Apple's push
  service, not the tailnet), but the kernel then needs outbound HTTPS — fine on the current box;
  worth stating as a requirement.
