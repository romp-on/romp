#!/usr/bin/env bash
# Headless UI verification: assemble a page from a stylesheet + a body fixture, screenshot it in the
# playwright-cached Chromium. VERIFICATION TOOLING ONLY — nothing here ships to a production surface.
# Grown from the harness pattern the feed-footer / hover-freeze / column-layout work rebuilt per task
# under /tmp (2026-08-24); checked in so the next task starts from here instead of from scratch.
#
# Usage:
#   tools/ui-verify/shot.sh --css ui/webview/feed.css --body tools/ui-verify/fixtures/feed-footer.html \
#       --out /tmp/shot.png [--size 760x420] [--extra-css '#feed-list { min-height: 300px; }']
#
# The output lands OUTSIDE the repo by convention (/tmp): screenshots of real boards are never
# committed (CLAUDE.md privacy), and fixture screenshots don't need to be either — render, look, delete.
set -euo pipefail

CSS="" BODY="" OUT="" SIZE="760x420" EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --css)   CSS="$2"; shift 2 ;;
    --body)  BODY="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    --size)  SIZE="$2"; shift 2 ;;
    --extra-css) EXTRA="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$CSS" ] && [ -n "$BODY" ] && [ -n "$OUT" ] || { echo "need --css, --body, --out" >&2; exit 2; }
[ -f "$CSS" ] || { echo "no such stylesheet: $CSS" >&2; exit 2; }
[ -f "$BODY" ] || { echo "no such body fixture: $BODY" >&2; exit 2; }

# Chromium resolution, most-deliberate first: an explicit env override, then the playwright browser
# cache (PLAYWRIGHT_BROWSERS_PATH or the default ~/.cache/ms-playwright — present on any machine that
# ever ran `npx playwright install chromium`; the devDependency in vscode-extension pins the CLI so
# the browser stops being cached by luck), then a system chrome.
find_chrome() {
  if [ -n "${UI_VERIFY_CHROME:-}" ] && [ -x "$UI_VERIFY_CHROME" ]; then echo "$UI_VERIFY_CHROME"; return; fi
  local cache="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
  local hit
  hit=$(ls -d "$cache"/chromium-*/chrome-linux*/chrome 2>/dev/null | sort -V | tail -1 || true)
  if [ -n "$hit" ] && [ -x "$hit" ]; then echo "$hit"; return; fi
  for c in chromium chromium-browser google-chrome; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return; fi
  done
  echo ""
}
CHROME=$(find_chrome)
[ -n "$CHROME" ] || { echo "no Chromium found — run: (cd vscode-extension && npx playwright install chromium)" >&2; exit 3; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp "$CSS" "$TMP/page.css"
{
  printf '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">\n'
  printf '<link href="page.css" rel="stylesheet">\n'
  printf '<style>body { margin: 0; } %s</style></head><body>\n' "$EXTRA"
  cat "$BODY"
  printf '\n</body></html>\n'
} > "$TMP/page.html"

"$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size="${SIZE/x/,}" --screenshot="$OUT" "file://$TMP/page.html" 2>/dev/null
[ -s "$OUT" ] || { echo "screenshot did not render" >&2; exit 4; }
echo "wrote $OUT (${SIZE})"
