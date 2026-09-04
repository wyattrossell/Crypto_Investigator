"""
Generate the application icon: a Bitcoin coin under a magnifying glass.

Usage:  python tools/make_icon.py

Writes assets/crypto_investigator.ico (multi-resolution: 16-256 px) and a
256 px PNG preview alongside it. Drawn entirely with Pillow primitives -
no external artwork - so the icon is reproducible from source like every
other artefact in this project.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

CANVAS = 256

# Palette
COIN_FILL = (247, 147, 26, 255)      # bitcoin orange
COIN_RIM = (196, 106, 0, 255)        # darker rim
COIN_SHINE = (255, 190, 92, 255)     # inner highlight ring
SYMBOL = (255, 255, 255, 255)        # white B
GLASS_RING = (44, 62, 80, 255)       # dark slate
GLASS_FILL = (210, 230, 245, 70)     # faint blue glass
HANDLE = (44, 62, 80, 255)


def _bold_font(size: int):
    """A bold font for the B; falls back to PIL's default if unavailable."""
    for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_bitcoin_symbol(draw: ImageDraw.ImageDraw, cx: int, cy: int,
                         scale: float) -> None:
    """The bitcoin 'B': a bold B with the two vertical bars breaking its
    top and bottom edges (drawn manually so no font glyph is needed)."""
    font = _bold_font(int(150 * scale))
    bbox = draw.textbbox((0, 0), "B", font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text((cx - width / 2 - bbox[0], cy - height / 2 - bbox[1]), "B",
              font=font, fill=SYMBOL)
    bar_width = max(2, int(10 * scale))
    bar_height = int(18 * scale)
    for x_offset in (-int(18 * scale), int(6 * scale)):
        for y_top in (cy - height // 2 - bar_height + int(4 * scale),
                      cy + height // 2 - int(4 * scale)):
            draw.rounded_rectangle(
                [cx + x_offset, y_top,
                 cx + x_offset + bar_width, y_top + bar_height],
                radius=bar_width // 2, fill=SYMBOL)


def draw_icon(size: int = CANVAS) -> Image.Image:
    """Render the full icon at `size` x `size`."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size / CANVAS   # scale factor relative to design canvas

    # --- coin (upper-left of centre) ---
    coin_cx, coin_cy, coin_r = 106 * s, 106 * s, 92 * s
    draw.ellipse([coin_cx - coin_r, coin_cy - coin_r,
                  coin_cx + coin_r, coin_cy + coin_r], fill=COIN_RIM)
    inner = coin_r - max(2, 9 * s)
    draw.ellipse([coin_cx - inner, coin_cy - inner,
                  coin_cx + inner, coin_cy + inner], fill=COIN_FILL)
    shine = inner - max(1, 6 * s)
    draw.ellipse([coin_cx - shine, coin_cy - shine,
                  coin_cx + shine, coin_cy + shine],
                 outline=COIN_SHINE, width=max(1, int(4 * s)))
    _draw_bitcoin_symbol(draw, int(coin_cx), int(coin_cy), s)

    # --- magnifying glass (lens over the coin's lower-right) ---
    # Drawn on a separate layer and alpha-composited so the glass is truly
    # translucent: the coin stays visible THROUGH the lens.
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    lens_cx, lens_cy, lens_r = 164 * s, 164 * s, 64 * s
    ring_w = max(2, int(15 * s))
    odraw.ellipse([lens_cx - lens_r, lens_cy - lens_r,
                   lens_cx + lens_r, lens_cy + lens_r], fill=GLASS_FILL)
    odraw.ellipse([lens_cx - lens_r, lens_cy - lens_r,
                   lens_cx + lens_r, lens_cy + lens_r],
                  outline=GLASS_RING, width=ring_w)
    # small highlight arc inside the lens
    odraw.arc([lens_cx - lens_r + ring_w * 2, lens_cy - lens_r + ring_w * 2,
               lens_cx + lens_r - ring_w * 2, lens_cy + lens_r - ring_w * 2],
              start=200, end=260, fill=(255, 255, 255, 180),
              width=max(1, int(6 * s)))
    # handle at 45 degrees to the bottom-right corner
    handle_w = max(3, int(24 * s))
    start_d = lens_r + ring_w * 0.2
    hx0 = lens_cx + start_d * math.cos(math.radians(45))
    hy0 = lens_cy + start_d * math.sin(math.radians(45))
    hx1, hy1 = 242 * s, 242 * s
    odraw.line([hx0, hy0, hx1, hy1], fill=HANDLE, width=handle_w)
    for hx, hy in ((hx0, hy0), (hx1, hy1)):   # round the handle ends
        r = handle_w / 2
        odraw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=HANDLE)

    return Image.alpha_composite(image, overlay)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    master = draw_icon(CANVAS)

    preview_path = ASSETS_DIR / "crypto_investigator_icon.png"
    master.save(preview_path)

    ico_path = ASSETS_DIR / "crypto_investigator.ico"
    # Render each size independently so small sizes stay crisp.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_icon(sz) for sz in sizes]
    # bitmap_format="bmp": classic uncompressed entries - the most
    # compatible choice for Explorer shortcut icons.
    images[-1].save(ico_path, format="ICO",
                    sizes=[(sz, sz) for sz in sizes],
                    append_images=images[:-1],
                    bitmap_format="bmp")
    print(f"Wrote {ico_path} and {preview_path}")


if __name__ == "__main__":
    main()
