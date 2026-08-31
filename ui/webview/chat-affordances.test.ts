// Chat rail + dot color model (the user 2026-06-15):
//   - the session's identity color lives on the vertical RAIL (2px, 70%), NOT the window border.
//   - dot colors are DECOUPLED from the session (no per-session disc / no persistent ring, which would
//     clash with the hover-selection ring): white reply (= assistant text color), blue you (= your
//     message bubble #2b6cef), blue ✓ success disc (the cyan --check-bg, consistent with the feed), red ✗.
//   - clickable expanders ("12 lines" / "prompt") and file links carry a persistent dotted underline.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the fold toggle and file links have a persistent dotted-underline link affordance", () => {
  assert.match(CSS, /\.tool-fold-toggle \{[^}]*text-decoration: underline dotted/);
  assert.match(CSS, /\.tool-file \{[^}]*text-decoration: underline dotted/);
});

test("session identity: rail (2px, 70%) + the pane ring THEMED under Yatharth, none in Classic", () => {
  // 2026-06-23 removed the old 2px decorative frame; PR 730 re-introduced a 1px/30% linkage ring;
  // T122 (the user 2026-08-27: there never used to be an outline around the chat, and there
  // wasn't) scopes it under the Yatharth theme — Classic is frameless, byte-equal to pre-730.
  assert.match(CSS, /#winframe \{ display: none; \}/);
  assert.match(CSS, /body\.chat-theme-yatharth #winframe \{ display: block; position: fixed; inset: 0; z-index: 900; pointer-events: none;\n  border: 1px solid color-mix\(in srgb, var\(--active-accent, transparent\) 30%, transparent\); \}/);
  assert.doesNotMatch(CSS, /#winframe \{[^}]*border: 2px solid/);
  assert.match(CSS, /\.turn::before \{[^}]*width: 2px[^}]*background: var\(--active-accent[^}]*opacity: 0\.7/);
  // T113→T115 (the user 2026-08-27): CLASSIC is the default and is the PRE-720 strip verbatim,
  // typography excepted — the thick 1.5px selected ring, NO identity tint at any state, the
  // neutral line under the strip. The contributor's aesthetic lives on, exactly, as the opt-in
  // Yatharth theme scoped under body.chat-theme-yatharth (tab-theme.test.ts pins both in full).
  assert.match(CSS, /\.tab\.active\.colored \{ box-shadow: inset 0 0 0 1\.5px var\(--chip-bg\); \}/,
    "the classic selected ring — the user: the thicker outline makes the selected tab easy to tell");
  assert.match(CSS, /border-bottom: 1px solid var\(--box-border\);/, "the pre-720 neutral line under the strip");
  assert.doesNotMatch(CSS, /\.tab[^{]*\{[^}]*linear-gradient\(180deg/, "no glossy tab gradients");
  assert.match(CSS, /body\.chat-theme-yatharth \.tab\.active\.colored:not\(\.tab-blocked\) \{[^}]*border-color: color-mix\(in srgb, var\(--chip-bg\) 55%, transparent\);[^}]*box-shadow: none;/s);
});

test("dot colors are decoupled from the session (no ring, absolute hues)", () => {
  assert.doesNotMatch(CSS, /\n\.dot \{[^}]*box-shadow/, "the BASE dot has no persistent ring (it'd clash with the hover-selection ring)");
  assert.match(CSS, /\.dot\.ring \{[^}]*background: var\(--fg\)/, "assistant/thinking = the assistant text color (--fg)");
  assert.match(CSS, /\.dot\.green \{[^}]*background: var\(--check-bg\)/, "tool success = the blue ✓ disc (consistent with the feed)");
  assert.match(CSS, /\.dot\.err \{[^}]*background: var\(--err\)/, "tool error = red");
  assert.match(CSS, /\.dot\.user \{ background: var\(--you\)/, "your prompt = the bubble blue (#2b6cef)");
});

test("a hard-blocked (API-error) tab carries a translucent red fill atop its dashed ring (the user 2026-06-18)", () => {
  // the dashed outline alone read too faint; a translucent red fill makes a stopped session legible at a glance
  assert.match(CSS, /\.tab\.tab-blocked \{[^}]*background: rgba\(229, 72, 77, 0\.30\)/);
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{[^}]*outline: 2px dashed/);   // the dashed ring stays (now incl. amber retrying)
  // the red must beat .tab.active (white, equal specificity but later in source) + :hover, else a FOCUSED
  // blocked tab showed white instead of red (the user 2026-06-18)
  assert.match(CSS, /\.tab\.tab-blocked:hover \{[^}]*background: rgba\(229, 72, 77, 0\.38\)/);
});

test("a SELECTED blocked tab blends the selection white OVER the red, so it reads as both (the user 2026-07-24)", () => {
  // it can't fall back to the plain white selection background without losing the red, so the fill layers
  // them: selection white atop a stronger red → lighter/brighter than its unselected neighbours, still red
  const rule = (CSS.match(/\.tab\.tab-blocked\.active \{[^}]*\}/) || [""])[0];
  assert.match(rule, /linear-gradient\(rgba\(255, 255, 255, 0\.14\), rgba\(255, 255, 255, 0\.14\)\)/,
    "the normal selection white rides on top");
  assert.match(rule, /rgba\(229, 72, 77, 0\.42\)/, "over a stronger red than the unselected fill");
});

test("identity stands down on a blocked tab, so it can't mask the red (both mechanisms)", () => {
  // the tint family keeps the structural :not — AND the classic ring (restored by T113) carries the
  // 2026-07-24 stand-down override again, since a ring near red painted over the dashed state outline
  assert.match(CSS, /\.tab\.tab-blocked\.active\.colored \{ box-shadow: none; \}/);
  // matched on the DECLARATION as written (background: color-mix(... var(--chip-bg) ...)) and
  // COUNTED: the first form of this loop required var(--chip-bg) BEFORE the word background, an
  // order the sheet never uses, so it matched zero rules and asserted nothing (PR-730 review,
  // 2026-08-27). A guard that can match nothing must fail loudly instead.
  const tints = CSS.match(/\.tab[^,{]*\.colored[^,{]*\{[^}]*background: color-mix\(in srgb, var\(--chip-bg\)/gs) || [];
  assert.ok(tints.length >= 3, "the tint family (9/15/22%) is present — zero matches would make this guard vacuous");
  for (const m of tints) {
    assert.ok(m.includes(":not(.tab-blocked)"), "an identity TINT rule must except blocked: " + m.slice(0, 60));
  }
});
