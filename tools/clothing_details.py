"""Build a wardrobe's isolated generation sheet from the original card pixels."""
from __future__ import annotations

import hashlib
import io
import json
import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


DETAILS_LAYOUT_ID = "CLOTHING_DETAILS_V1"
WARDROBE_STAGES = ("WARDROBE_CARD", "CLOTHING_DETAILS")
PROVENANCE_KEY = "StoryArt clothing details"


def is_wardrobe_plan(plan: dict) -> bool:
    startup = plan.get("startup_parameter_selection") or {}
    item = (startup.get("delegation") or {}).get("clothing_plan") or {}
    return (startup.get("menu_contract") == "CLOTHING_PLAN"
            and item.get("item_kind") == "CLOTHING"
            and item.get("request_id") == plan.get("request_id"))


def uses_clothing_details(plan: dict) -> bool:
    startup = plan.get("startup_parameter_selection") or {}
    item = (startup.get("delegation") or {}).get("clothing_plan") or {}
    layout = item.get("clothing_details_layout") or {}
    return is_wardrobe_plan(plan) and layout.get("id") == DETAILS_LAYOUT_ID


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font(size: int, bold: bool = False):
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    for name in names:
        try:
            folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            local = folder / name
            return ImageFont.truetype(str(local) if local.is_file() else name, size)
        except OSError:
            continue
    raise ValueError("Rendering clothing-details captions requires Arial or DejaVu Sans.")


def render(card: Path, regions: dict) -> bytes:
    """Render the wearer-free front, selected details and the original materials/palette."""
    card_hash = sha256(card)
    if regions.get("card_sha256") != card_hash:
        raise ValueError("Clothing detail regions must identify the exact source card hash.")
    with Image.open(card) as original:
        source = original.convert("RGB")

    def crop(box: list[int]) -> Image.Image:
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
            raise ValueError("Clothing detail boxes must be [left, top, right, bottom] pixel coordinates.")
        left, top, right, bottom = box
        if not (0 <= left < right <= source.width and 0 <= top < bottom <= source.height):
            raise ValueError("Clothing detail crop is outside the source card.")
        return source.crop(tuple(box))

    title = regions.get("title")
    heading = regions.get("details_heading")
    details = regions.get("details")
    if (not isinstance(title, str) or not title.strip() or "\n" in title
            or not isinstance(heading, str) or not heading.strip() or "\n" in heading
            or not isinstance(details, list)):
        raise ValueError("Supply a title, a detail heading and the list of generation-critical details.")
    front = crop(regions.get("front"))
    materials = crop(regions.get("materials_and_palette"))
    panels = []
    for detail in details:
        caption = detail.get("caption") if isinstance(detail, dict) else None
        if not isinstance(caption, str) or not caption.strip():
            raise ValueError("Each selected garment detail needs a caption stating what to preserve.")
        panels.append((crop(detail.get("box")), caption.strip()))

    ink, paper = "#32231f", "#f0e9e3"
    title_font, heading_font, label_font = _font(34, True), _font(21, True), _font(16, True)
    width, top, side, tile_width = 1024, 82, 426, 276
    rows = []
    for i in range(0, len(panels), 2):
        row = [(ImageOps.contain(image, (tile_width, 160), Image.Resampling.LANCZOS),
                textwrap.fill(label, width=27)) for image, label in panels[i:i + 2]]
        rows.append(row)
    side_height = 30 if rows else 0
    for row in rows:
        side_height += max(panel.height + 20 * (label.count("\n") + 1) for panel, label in row) + 16
    material_view = ImageOps.contain(materials, (width - side - 24, 430), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, top + max(795, side_height + material_view.height) + 24), paper)
    draw = ImageDraw.Draw(canvas)
    if draw.textlength(title, font=title_font) > width - 48:
        raise ValueError("Clothing sheet title does not fit its header.")
    draw.text((24, 16), title, fill=ink, font=title_font)
    draw.line((24, 67, width - 24, 67), fill="#8b3b2d", width=2)
    canvas.paste(ImageOps.contain(front, (378, 795), Image.Resampling.LANCZOS), (24, top))
    y = top
    if rows:
        draw.text((side, y), heading, fill=ink, font=heading_font)
        y += 30
    for row in rows:
        row_height = 0
        for column, (panel, caption) in enumerate(row):
            x = side + column * (tile_width + 16)
            if max(draw.textlength(line, font=label_font) for line in caption.splitlines()) > tile_width:
                raise ValueError("A garment detail caption is too wide; use a brief label.")
            canvas.paste(panel, (x, y))
            draw.multiline_text((x, y + panel.height + 3), caption, fill=ink, font=label_font, spacing=3)
            row_height = max(row_height, panel.height + 20 * (caption.count("\n") + 1))
        y += row_height + 16
    canvas.paste(material_view, (side, y))
    info = PngImagePlugin.PngInfo()
    info.add_itxt(PROVENANCE_KEY, json.dumps({"layout_id": DETAILS_LAYOUT_ID, **regions}, ensure_ascii=False))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def verify(card: Path, sheet: Path) -> None:
    with Image.open(sheet) as image:
        regions = json.loads(image.info.get(PROVENANCE_KEY, "{}"))
    if regions.get("layout_id") != DETAILS_LAYOUT_ID or render(card, regions) != sheet.read_bytes():
        raise ValueError("Clothing sheet differs from the recorded source regions or layout.")
