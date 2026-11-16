"""Generate piglet's icon files from the solid snout mark.

Usage: ``python scripts/gen_icons.py`` (run after changing the mark; the outputs are committed).

Writes the browser favicons and the iPhone Home Screen icon to ``web/public`` and the desktop
app icon to ``desktop/resources``. The outlined snout used inside the UI is drawn by
``web/src/components/Logo.tsx`` on the same 64-unit grid.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
WEB_PUBLIC = ROOT / "web" / "public"
DESKTOP_RESOURCES = ROOT / "desktop" / "resources"

GRID = 64
STADIUM = (6, 15, 58, 49)  # left, top, right, bottom; the ends are semicircles
NOSTRILS = ((21, 26, 29, 38), (35, 26, 43, 38))  # ellipse bounding boxes, cut out of the stadium

INK_LIGHT = "#b4467a"  # --accent (light theme)
INK = "#e08ab3"  # --accent (dark theme)
TILE = "#161616"  # --bg (dark theme)
RIM = "#2f2f2e"  # --border (dark theme), keeps the desktop tile visible on dark docks
SUPERSAMPLE = 8


def _fmt(value: float) -> str:
    return f"{value:g}"


def favicon_svg() -> str:
    """Return the scalable favicon: the mark alone, in the accent color of the browser's theme."""
    left, top, right, bottom = STADIUM
    r = (bottom - top) / 2
    path = (
        f"M{_fmt(left + r)} {_fmt(top)}H{_fmt(right - r)}"
        f"A{_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(right - r)} {_fmt(bottom)}"
        f"H{_fmt(left + r)}A{_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(left + r)} {_fmt(top)}Z"
    )
    for x0, y0, x1, y1 in NOSTRILS:
        rx, ry, cy = (x1 - x0) / 2, (y1 - y0) / 2, (y0 + y1) / 2
        arc = f"A{_fmt(rx)} {_fmt(ry)} 0 1 0"
        path += f"M{_fmt(x0)} {_fmt(cy)}{arc} {_fmt(x1)} {_fmt(cy)}{arc} {_fmt(x0)} {_fmt(cy)}Z"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="3 3 58 58">'
        f"<style>path{{fill:{INK_LIGHT}}}"
        f"@media (prefers-color-scheme:dark){{path{{fill:{INK}}}}}</style>"
        f'<path fill-rule="evenodd" d="{path}"/></svg>\n'
    )


def render_icon(size: int, *, inset: float, radius: float, mark: float, rim: bool) -> Image.Image:
    """Render the mark in the accent color on a dark rounded tile.

    Args:
        size: Width and height of the image in pixels.
        inset: Transparent margin around the tile, as a fraction of ``size``.
        radius: Corner radius of the tile, as a fraction of ``size``.
        mark: Width of the mark's 64-unit grid, as a fraction of ``size``; the grid is centered.
        rim: Whether to outline the tile.

    Returns:
        An RGBA image, drawn at a higher resolution and downsampled for smooth edges.
    """
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    edge = round(inset * big)
    draw.rounded_rectangle(
        (edge, edge, big - edge - 1, big - edge - 1),
        radius=round(radius * big),
        fill=TILE,
        outline=RIM if rim else None,
        width=max(1, big // 160),
    )
    scale = mark * big / GRID
    offset = (big - GRID * scale) / 2

    def place(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = (round(offset + value * scale) for value in box)
        return x0, y0, x1 - 1, y1 - 1

    left, top, right, bottom = place(STADIUM)
    draw.rounded_rectangle((left, top, right, bottom), radius=(bottom - top + 1) // 2, fill=INK)
    for nostril in NOSTRILS:
        draw.ellipse(place(nostril), fill=TILE)
    return image.resize((size, size), Image.Resampling.BOX)


def main() -> None:
    """Write every icon file."""
    WEB_PUBLIC.mkdir(parents=True, exist_ok=True)
    DESKTOP_RESOURCES.mkdir(parents=True, exist_ok=True)

    (WEB_PUBLIC / "favicon.svg").write_text(favicon_svg(), encoding="utf-8")
    # Safari and older browsers ignore SVG favicons; iOS uses the Home Screen icon instead.
    tab = {px: render_icon(px, inset=0, radius=0.22, mark=0.85, rim=False) for px in (16, 32)}
    tab[32].save(WEB_PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32)], append_images=[tab[16]])
    # iOS rounds the corners itself and fills transparency with black, so this one is full-bleed.
    home = render_icon(180, inset=0, radius=0, mark=2 / 3, rim=False)
    home.convert("RGB").save(WEB_PUBLIC / "apple-touch-icon.png", optimize=True)
    # macOS icon grid: an 824px tile centered in 1024px; electron-builder converts it per platform.
    tile = 824 / 1024
    app = render_icon(1024, inset=(1 - tile) / 2, radius=185 / 1024, mark=tile * 2 / 3, rim=True)
    app.save(DESKTOP_RESOURCES / "icon.png", optimize=True)

    for path in sorted([*WEB_PUBLIC.iterdir(), *DESKTOP_RESOURCES.iterdir()]):
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
