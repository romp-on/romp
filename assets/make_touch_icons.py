#!/usr/bin/env python3
"""Home-screen icons for the installable dashboard (plans/ios-app.md, proposal 1).

Downsamples the 1024px app tile (romp-icon-swirl-square-1024.png — the swirl on its
dark rounded-square background) to the three sizes the install surfaces consult, and
drops them straight into vscode-extension/media/ (the kernel's /media/ root, same
copy step as make_wordmark.py):

  romp-touch-180.png   apple-touch-icon (iOS home screen)
  romp-app-192.png     manifest icon (Android/Chrome install)
  romp-app-512.png     manifest icon (splash / high-dpi)

Flattened onto the tile's own background: apple-touch-icon must be opaque — iOS
composites transparency onto black, which would read as broken corners.

Run:  uv run --with pillow python make_touch_icons.py
"""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
MEDIA = HERE.parent / "vscode-extension" / "media"
SIZES = {"romp-touch-180.png": 180, "romp-app-192.png": 192, "romp-app-512.png": 512}


def main():
    src = Image.open(HERE / "romp-icon-swirl-square-1024.png").convert("RGBA")
    # corner pixel = the tile's own background; flatten onto it so alpha never ships
    bg = Image.new("RGBA", src.size, src.getpixel((0, 0)))
    flat = Image.alpha_composite(bg, src).convert("RGB")
    for name, px in SIZES.items():
        out = MEDIA / name
        flat.resize((px, px), Image.LANCZOS).save(out, optimize=True)
        print("wrote", out)


if __name__ == "__main__":
    main()
