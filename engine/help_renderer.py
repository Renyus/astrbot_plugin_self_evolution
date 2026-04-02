"""
Help Renderer - Generates help images using Pillow.

Creates a navigation poster style help image with:
- Blurred background
- Semi-transparent overlay
- Glass card layout for command groups
- Clean typography

All file I/O is async-safe via asyncio.to_thread().
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .help_catalog import (
    HELP_CATALOG_VERSION,
    HelpCommand,
    get_commands_by_group,
)
from .help_theme_store import HelpThemeStore

logger = logging.getLogger("astrbot")

TITLE_COLOR = (255, 255, 255)
TEXT_COLOR = (220, 220, 230)
CMD_COLOR = (255, 255, 255)
DESC_COLOR = (180, 180, 200)
OVERLAY_COLOR = (20, 20, 30, 180)

GROUP_NAMES = {
    "base": "基础",
    "user": "用户",
    "admin": "管理",
    "persona": "Persona",
}

_DEFAULT_FONT: Optional[ImageFont.FreeTypeFont] = None


def _init_fonts():
    """Initialize default fonts at module load time."""
    global _DEFAULT_FONT
    if _DEFAULT_FONT is not None:
        return
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:\\Windows\\Fonts\\consola.ttf",
        "C:\\Windows\\Fonts\\msgothic.ttc",
    ]
    for fp in font_paths:
        try:
            _DEFAULT_FONT = ImageFont.truetype(fp, 18)
            return
        except Exception:
            continue
    _DEFAULT_FONT = ImageFont.load_default()


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Try to get a font of the specified size."""
    _init_fonts()
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:\\Windows\\Fonts\\consola.ttf",
        "C:\\Windows\\Fonts\\msgothic.ttc",
    ]
    for fp in font_paths:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return _DEFAULT_FONT or ImageFont.load_default()


class HelpRenderer:
    def __init__(self, theme_store: HelpThemeStore):
        self.theme_store = theme_store

    async def render(self, is_admin: bool = False) -> tuple[Optional[Path], bool]:
        """
        Render the help image asynchronously.

        Returns (path, success). path is None if rendering failed.
        """
        theme = self.theme_store.theme
        cache_path = self.theme_store.get_cache_path(
            version=HELP_CATALOG_VERSION,
            is_admin=is_admin,
            bg_name=theme.bg_name,
            blur=theme.blur,
        )

        if cache_path and cache_path.exists():
            return cache_path, True

        bg_path = await self.theme_store.get_bg_path()
        if bg_path is None:
            logger.error("[HelpRenderer] No background image found")
            return None, False

        try:
            img = await self._create_image(bg_path, theme.blur, is_admin)
            if img is None:
                return None, False

            if cache_path:
                await asyncio.to_thread(self._save_image, img, cache_path)
                logger.info(f"[HelpRenderer] Saved help image to cache: {cache_path}")

            return cache_path, True
        except Exception as e:
            logger.error(f"[HelpRenderer] Failed to render help image: {e}")
            return None, False

    def _save_image(self, img: Image.Image, path: Path) -> None:
        """Save image to file (sync, runs in thread)."""
        img.save(path, "PNG")

    async def _create_image(self, bg_path: Path, blur_radius: int, is_admin: bool) -> Optional[Image.Image]:
        """Create the help image with glass card layout."""

        def _create():
            from PIL import ImageFilter

            bg = Image.open(bg_path).convert("RGBA")
            bg = bg.resize((1200, 1600), Image.Resampling.LANCZOS)

            if blur_radius > 0:
                bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))

            overlay = Image.new("RGBA", bg.size, color=OVERLAY_COLOR)
            bg = Image.alpha_composite(bg, overlay)

            canvas = Image.new("RGBA", (1200, 1600), (0, 0, 0, 0))
            canvas.paste(bg, (0, 0))

            draw = ImageDraw.Draw(canvas)

            self._draw_title(draw, is_admin)
            self._draw_commands(draw, is_admin)
            self._draw_footer(draw, is_admin)

            return canvas.convert("RGB")

        return await asyncio.to_thread(_create)

    def _draw_title(self, draw: ImageDraw.ImageDraw, is_admin: bool) -> None:
        """Draw the title section."""
        title_font = _get_font(36)
        subtitle_font = _get_font(20)

        title = "Self-Evolution 命令导航"
        subtitle = "管理员版" if is_admin else "用户版"

        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = bbox[2] - bbox[0]
        x = (1200 - title_width) // 2
        draw.text((x, 40), title, font=title_font, fill=TITLE_COLOR)

        bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        sub_width = bbox[2] - bbox[0]
        x = (1200 - sub_width) // 2
        draw.text((x, 90), subtitle, font=subtitle_font, fill=DESC_COLOR)

        draw.line([(100, 130), (1100, 130)], fill=(255, 255, 255, 80), width=1)

    def _draw_commands(self, draw: ImageDraw.ImageDraw, is_admin: bool) -> None:
        """Draw command groups in card layout."""
        groups = get_commands_by_group(include_admin=is_admin)

        card_x = 60
        card_y = 160
        card_width = 520
        card_height = 600
        card_spacing = 30

        col = 0
        row = 0

        for group_key in ["base", "user", "admin", "persona"]:
            if group_key == "admin" and not is_admin:
                continue

            cmds = groups.get(group_key, [])
            if not cmds:
                continue

            if col == 1:
                col = 0
                row += 1
                card_y = 160 + row * (card_height + card_spacing + 20)

            x = card_x + col * (card_width + card_spacing)
            self._draw_card(draw, x, card_y, card_width, card_height, group_key, cmds)

            col += 1

    def _draw_card(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        width: int,
        height: int,
        group_key: str,
        commands: list[HelpCommand],
    ) -> None:
        """Draw a single command group card with glass effect."""
        from PIL import ImageDraw

        card = Image.new("RGBA", (width, height), (255, 255, 255, 25))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle([(0, 0), (width - 1, height - 1)], radius=15, fill=(255, 255, 255, 20))
        card.paste((0, 0, 0, 0), (0, 0), mask=card)

        draw.rounded_rectangle([(x, y), (x + width - 1, y + height - 1)], radius=15, fill=(255, 255, 255, 20))

        group_name = GROUP_NAMES.get(group_key, group_key)
        title_font = _get_font(22)
        cmd_font = _get_font(16)
        desc_font = _get_font(14)

        draw.text((x + 20, y + 15), f"【{group_name}】", font=title_font, fill=TITLE_COLOR)

        y_offset = y + 55
        for cmd in commands:
            cmd_text = cmd.command
            desc_text = cmd.desc

            draw.text((x + 20, y_offset), cmd_text, font=cmd_font, fill=CMD_COLOR)
            y_offset += 26

            desc_bbox = draw.textbbox((x + 20, y_offset), desc_text, font=desc_font)
            desc_w = desc_bbox[2] - desc_bbox[0]
            if x + 20 + desc_w > x + width - 20:
                desc_text = desc_text[: (width - 60) // 6] + "..."
            draw.text((x + 25, y_offset), desc_text, font=desc_font, fill=DESC_COLOR)
            y_offset += 30

            if y_offset > y + height - 30:
                break

    def _draw_footer(self, draw: ImageDraw.ImageDraw, is_admin: bool) -> None:
        """Draw the footer hint."""
        footer_font = _get_font(14)

        hint1 = "发送 /system help text 查看文本版"
        hint2 = ""

        if is_admin:
            hint2 = "管理员可使用 /system help bg ... 自定义背景"

        draw.line([(100, 1510), (1100, 1510)], fill=(255, 255, 255, 60), width=1)

        bbox = draw.textbbox((0, 0), hint1, font=footer_font)
        w = bbox[2] - bbox[0]
        x = (1200 - w) // 2
        draw.text((x, 1525), hint1, font=footer_font, fill=DESC_COLOR)

        if hint2:
            bbox = draw.textbbox((0, 0), hint2, font=footer_font)
            w = bbox[2] - bbox[0]
            x = (1200 - w) // 2
            draw.text((x, 1550), hint2, font=footer_font, fill=DESC_COLOR)


async def render_help_image(theme_store: HelpThemeStore, is_admin: bool = False) -> tuple[Optional[Path], bool]:
    """Convenience function to render help image asynchronously."""
    renderer = HelpRenderer(theme_store)
    return await renderer.render(is_admin)
