"""Render the brand PNGs for the Home Assistant brands repository.

Draws the same design as icon.svg with Pillow -- shutter slats behind a red IS3
wordmark with vlios.cz beneath -- and writes the four files brands expects:
a square icon and a wider logo, each at 1x and 2x.

Run from the repository root::

    python brands/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (226, 0, 26, 255)
GREY = (154, 154, 154, 255)
FONT_BOLD = r"C:\Windows\Fonts\ariblk.ttf"

OUT = Path(__file__).parent
# Draw large, then downscale, so the edges come out smooth.
SCALE = 4


def _font(size: int) -> ImageFont.FreeTypeFont:
    """The bold face at a given pixel size."""
    return ImageFont.truetype(FONT_BOLD, size * SCALE)


def _text(draw: ImageDraw.ImageDraw, xy, text, font, fill, spacing=0) -> None:
    """Draw centred text with optional letter spacing."""
    cx, cy = xy[0] * SCALE, xy[1] * SCALE
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * SCALE * (len(text) - 1)
    x = cx - total / 2
    ascent, descent = font.getmetrics()
    y = cy - (ascent + descent) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing * SCALE


def _slats(draw: ImageDraw.ImageDraw, x, width, ys, thickness) -> None:
    """Horizontal shutter slats, the motif from the vlios.cz logo."""
    for y in ys:
        draw.rounded_rectangle(
            [
                x * SCALE,
                y * SCALE,
                (x + width) * SCALE,
                (y + thickness) * SCALE,
            ],
            radius=thickness * SCALE / 2,
            fill=GREY,
        )


def _trim_square(img: Image.Image, margin: float) -> Image.Image:
    """Crop to the drawn content and centre it on a square canvas.

    ``margin`` is the fraction of the final side left empty on each edge, so
    the artwork fills the icon instead of floating in a wide transparent
    border and looking small in the Home Assistant card.
    """
    bbox = img.getbbox()
    if bbox is None:
        return img
    content = img.crop(bbox)
    side = round(max(content.size) / (1 - 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - content.width) // 2, (side - content.height) // 2)
    canvas.paste(content, offset)
    return canvas


def render_icon() -> Image.Image:
    """The square icon: slats, IS3, vlios.cz, trimmed to fill the square."""
    size = 512
    img = Image.new("RGBA", (size * SCALE, size * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _slats(draw, 96, 320, (132, 160, 188), 14)
    _slats(draw, 96, 320, (312, 340, 368), 14)
    _text(draw, (256, 250), "IS3", _font(190), RED, spacing=6)
    _text(draw, (256, 430), "vlios.cz", _font(52), RED, spacing=4)

    # Draw fills only the middle of the canvas; crop it back to the artwork so
    # the icon is bold rather than floating in empty space.
    return _trim_square(img, margin=0.06)


def render_logo() -> Image.Image:
    """The wide logo: IS3 with vlios.cz beneath, for the config dialog."""
    w, h = 720, 400
    img = Image.new("RGBA", (w * SCALE, h * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _slats(draw, 150, 420, (70, 100, 130), 16)
    _slats(draw, 150, 420, (270, 300, 330), 16)
    _text(draw, (360, 200), "IS3", _font(230), RED, spacing=8)
    _text(draw, (360, 360), "vlios.cz", _font(60), RED, spacing=5)

    return img.resize((w, h), Image.LANCZOS)


def _save(image: Image.Image, name: str, big: int) -> None:
    """Write name.png at `big` and name@2x.png at twice that, square-cropped."""
    ratio = image.width / image.height
    small = image.resize((round(big * ratio), big), Image.LANCZOS)
    small.save(OUT / f"{name}.png")
    twice = image.resize((round(big * 2 * ratio), big * 2), Image.LANCZOS)
    twice.save(OUT / f"{name}@2x.png")
    print(f"  {name}.png {small.size}   {name}@2x.png {twice.size}")


def main() -> None:
    """Render every brand file."""
    print("Rendering brand assets:")
    _save(render_icon(), "icon", 256)
    _save(render_logo(), "logo", 256)


if __name__ == "__main__":
    main()
