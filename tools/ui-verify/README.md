# ui-verify — headless screenshot checks for the webview surfaces

Verification tooling only: nothing here ships to a production surface. This is the
checked-in form of the `/tmp` harness the feed-footer, hover-freeze, and
column-layout work each rebuilt from scratch (2026-08-24) — assemble a page from a
real stylesheet plus a hand-written body fixture, screenshot it in headless
Chromium, and eyeball (or pixel-diff) the result before publishing a UI change.

## Run

```sh
tools/ui-verify/shot.sh \
  --css ui/webview/feed.css \
  --body tools/ui-verify/fixtures/feed-footer.html \
  --out /tmp/feed-footer.png \
  --size 760x420 \
  --extra-css '#feed-list { min-height: 260px; }'
```

Render the same fixture at two widths (or with a state class flipped) to prove a
before/after; the column-width fix shipped on exactly that evidence.

- `--body` is an HTML fragment inserted into a minimal skeleton, so a fixture
  mirrors what the TS builders emit — class for class. Copy the example fixture
  next to your change and edit it; the check is only as good as that mirroring.
- Fixtures are SYNTHETIC by the repo's privacy rules: the notes-api demo domain
  (`web`/`api`/`tests` sessions), invented titles, placeholder UUIDs. Never paste
  a real board's content. Outputs go to `/tmp` and are never committed.
- This exercises the CSS + a static DOM, not the bundle's runtime. For behavior,
  the source-pin tests are the harness; this catches what pins can't — cascade
  outcomes, layout, the actual pixels.

## Where Chromium comes from

`shot.sh` resolves, in order: `$UI_VERIFY_CHROME` (explicit override), the
playwright browser cache (`$PLAYWRIGHT_BROWSERS_PATH`, default
`~/.cache/ms-playwright` — newest `chromium-*` wins), then a system
`chromium`/`google-chrome`. Historically the cache was populated only as a side
effect of some other project having run playwright — the browser was there by
luck. `playwright` is now a devDependency of `vscode-extension/`, so the pinned
CLI is always at hand; on a machine without the cache, populate it once:

```sh
cd vscode-extension && npx playwright install chromium
```

## Pointing it at a BUILT bundle

The static-fixture path above needs no build. To check a page whose look depends
on runtime-computed styles, build first (`cd vscode-extension && npm run build`),
serve or open the real page (the kernel's own `/feed`, `/fleet` pages, or a file
URL at `vscode-extension/dist/`), and screenshot that URL with the same Chromium
flags `shot.sh` uses (`--headless=new --screenshot=... --window-size=...`).
Screenshots of a LIVE board show real session data — keep them in `/tmp`, never
in the repo (CLAUDE.md privacy rules).
