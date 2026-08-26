"""romp_palette — the selectable session-identity palettes (categorical).

The identity palette is the set session tabs, feed cards, timeline lanes, and
postal bubbles draw their per-session color from. It used to be ONE hardcoded
9-color list copied across three assigners (bin/romp, the kernel, the SDK
backend); the SET is selectable now (the user 2026-07-12) and this module is the
single source of truth:
 - the kernel and the SDK backend import this module directly;
 - bin/romp (shell — can't import Python) reads STATE/palette-colors, a
   bg<TAB>fg mirror the kernel rewrites at boot and on every switch, and falls
   back to the "romp" set when the mirror doesn't exist yet.
The chosen NAME persists in STATE/palette (gear -> Session colors); switching
remaps every stored session color to the SAME SLOT in the new set (kernel
_set_palette), so the fleet recolors consistently and slots survive round-trips.

Alongside romp's own set: the best categorical schemes from Fabio Crameri's
Scientific colour maps and from cmocean (the user 2026-07-12). Crameri's "S"
palettes order their samples for maximum adjacent distinction but include
near-black / near-white entries (built for white paper figures), so each set
here is CURATED to 9 mid-tone, mutually distinct colors that hold up as
identity chrome on the dark UI. batlowS — Crameri's flagship categorical — was
auditioned and dropped: a sequential map's gamut collapses into look-alike
olives and salmons at 9 swatches. cmocean has no categorical maps; "phase" is
its cyclic hue wheel at near-constant lightness, sampled evenly — which is
exactly what identity colors want. Each list is ordered so the FIRST few
assignments are maximally distinct (blue/green-family first, mirroring the romp
set's colorblind-tuned order).
"""
from pathlib import Path

DEFAULT = "romp"

# name -> {label (picker display), bg (9 hex), fg (black|white per bg — the text/status-bar color
# readable on that swatch, same words the names registry stores)}. "romp" leads the picker.
PALETTES = {
    "romp": {
        "label": "romp",
        "bg": ["#1EA1EB", "#54B204", "#4EA8A9", "#DD42FF", "#E87221",
               "#98998A", "#F85B5A", "#F9D849", "#9088F0"],
        "fg": ["white", "black", "white", "white", "black",
               "black", "white", "black", "black"],
    },
    "phase": {
        "label": "phase — cmocean",
        "bg": ["#5883DF", "#0F9A6B", "#DE357B", "#A8780D", "#A05DF4",
               "#1E93A8", "#CB5A3C", "#689119", "#D02FD0"],
        "fg": ["white", "white", "white", "white", "white",
               "white", "white", "white", "white"],
    },
    "hawaiiS": {
        "label": "hawaiiS — crameri",
        "bg": ["#8C0273", "#9C951C", "#6CD48C", "#974E3E", "#66E8D3",
               "#8ABC48", "#9B6F28", "#922E55", "#60DEB0"],
        "fg": ["white", "black", "black", "white", "black",
               "black", "white", "white", "black"],
    },
    "romaO": {
        "label": "romaO — crameri",
        "bg": ["#4F86B8", "#C3A34B", "#94D0CE", "#874037", "#5C538B",
               "#A2662C", "#74BBCD", "#733957", "#D1C26E"],
        "fg": ["white", "black", "black", "white", "white",
               "white", "black", "white", "black"],
    },
    # pastel — a soft high-lightness set (all-black text) for anyone who finds the saturated sets
    # loud across many tabs (2026-08-26). Hand-tuned like the romp set, same colorblind-tuned
    # order (blue, green, teal first); every swatch stays under the mid-tone luminance cap.
    "pastel": {
        "label": "pastel — romp soft",
        "bg": ["#8FC7F2", "#9AD48A", "#7ED4C8", "#D9A7EC", "#F2B27C",
               "#C9CBB0", "#F2A09E", "#EBD584", "#B4AFF2"],
        "fg": ["black", "black", "black", "black", "black",
               "black", "black", "black", "black"],
    },
}


def colors(name):
    """The bg list for palette `name`, falling back to the default set for an unknown name."""
    return PALETTES.get(name, PALETTES[DEFAULT])["bg"]


def fgs(name):
    return PALETTES.get(name, PALETTES[DEFAULT])["fg"]


def find(bg):
    """(palette_name, slot) of a stored bg hex, searching every palette — or None for a color no
    palette owns. This is how a switch remaps by SLOT, and why setSessionColor can accept a swatch
    click from a menu rendered before the switch."""
    for name, p in PALETTES.items():
        if bg in p["bg"]:
            return name, p["bg"].index(bg)
    return None


def fg_for(bg):
    """The readable text color word for a palette bg (owning palette's slot), or "white"."""
    loc = find(bg)
    return PALETTES[loc[0]]["fg"][loc[1]] if loc else "white"


def active_name(state_dir):
    """The chosen palette name from STATE/palette, validated; the default when unset/unknown."""
    try:
        n = (Path(state_dir) / "palette").read_text().strip()
    except OSError:
        return DEFAULT
    return n if n in PALETTES else DEFAULT
