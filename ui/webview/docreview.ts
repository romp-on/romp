// The file viewer's quote anchoring (born 2026-08-14 as the doc-review module; consolidated
// 2026-08-23): selecting a passage in the viewer seeds the chat's own labeled quote chip, and the
// label needs the passage's source LINE — anchorFor finds it by progressively looser matching, and
// quoteSrcLabel composes the path:line origin the chip carries. The old review-comment half
// (DocComment, buildReviewMessage, the per-file store key) is gone: batching notes for one hand-off
// is exactly what quote chips + ⌘⏎ staging already do. No DOM here so it is unit-testable.

// Collapse runs of whitespace (a rendered selection carries the layout's newlines and indentation, the
// source carries its own) so both sides compare on words alone.
function norm(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

// Strip the inline markdown the RENDERER consumed, so a span selected out of rendered text can still be
// found in the source. Deliberately does NOT touch `_`: snake_case identifiers are far commoner in these
// docs than underscore emphasis, and stripping them would break more matches than it fixes.
function stripInline(line: string): string {
  return line
    .replace(/^\s*(?:[#]{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/, "")   // block markers: heading, quote, list
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")                       // image → its alt text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")                        // link → its label
    .replace(/\*\*|~~|`|\*/g, "");                                  // bold / strike / code / emphasis
}

// Which source line does this selection come from? Tries progressively looser matches and returns null
// rather than a guess — a wrong line number sends the agent to the wrong place, which is worse than no
// line number at all (the message still carries the quoted text).
export function anchorFor(source: string, selected: string): { quote: string; line: number | null } {
  const quote = norm(selected);
  if (!quote) return { quote: "", line: null };
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  // A multi-line selection anchors on its FIRST non-empty line — that is where the agent should look.
  // Split the RAW selection: `quote` has already had its newlines collapsed away.
  const head = norm(selected.replace(/\r\n?/g, "\n").split("\n").find((l) => l.trim()) || quote);
  const needles = [head];
  const stripped = norm(stripInline(head));
  if (stripped && stripped !== head) needles.push(stripped);
  // Last resort: the first six words, so a selection whose tail crossed formatting we do not strip
  // (an underscore, a footnote marker) still lands on its line.
  const words = (stripped || head).split(" ").filter(Boolean);
  if (words.length > 6) needles.push(words.slice(0, 6).join(" "));

  for (const needle of needles) {
    if (!needle) continue;
    for (let i = 0; i < lines.length; i++) {
      const raw = norm(lines[i]);
      if (raw.includes(needle) || norm(stripInline(lines[i])).includes(needle)) {
        return { quote, line: i + 1 };
      }
    }
  }
  return { quote, line: null };
}

// The chip's origin label: "path:line" when the selection's source line can honestly be found,
// the bare path otherwise (a wrong line sends the agent to the wrong place — worse than none).
export function quoteSrcLabel(path: string, source: string | null, selected: string): string {
  const line = source ? anchorFor(source, selected).line : null;
  return line ? path + ":" + line : path;
}
