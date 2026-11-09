"""Regenerate packaging/asm.ico and asm-*.png from the logo geometry (same shape as Logo in Login.tsx).

Each size is drawn at 16x and downsampled with LANCZOS, so edges stay crisp at 16-256 px.
Run: python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "packaging"
ORANGE, WHITE = (255, 82, 0, 255), (255, 255, 255, 255)
# logo path on a 32x32 grid
OUTER = [(9, 24), (16, 7), (23, 24), (19, 24), (17.6, 20.4), (14.3, 20.4), (13, 24)]
HOLE = [(14.4, 17.5), (16.7, 17.5), (15.6, 14)]


def render(px: int) -> Image.Image:
    ss = 16
    big = px * ss
    k = big / 32
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(6 * k), fill=ORANGE)
    # small sizes: thicken the letter a little so it survives downsampling
    grow = 0.6 if px <= 24 else 0.3 if px <= 48 else 0
    cx = 16
    outer = [(cx + (x - cx) * (1 + grow / 8), y + (grow if y > 16 else -grow)) for x, y in OUTER]
    d.polygon([(x * k, y * k) for x, y in outer], fill=WHITE)
    d.polygon([(x * k, y * k) for x, y in HOLE], fill=ORANGE)
    return im.resize((px, px), Image.LANCZOS)


def main() -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = {s: render(s) for s in sizes}
    imgs[256].save(OUT / "asm.ico", sizes=[(s, s) for s in sizes], append_images=[imgs[s] for s in sizes[:-1]])
    for s in (16, 32, 48, 256):
        imgs[s].save(OUT / f"asm-{s}.png")
    print("wrote", OUT / "asm.ico")


if __name__ == "__main__":
    main()
