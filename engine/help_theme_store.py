"""
Help Theme Store - Manages help image background and blur settings.

Stores theme configuration in a JSON file within the plugin's data directory.
All file operations are async-safe via asyncio.to_thread().
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("astrbot")


DEFAULT_BLUR = 16
MIN_BLUR = 0
MAX_BLUR = 30
DEFAULT_BG_NAME = "default"


@dataclass
class HelpTheme:
    bg_name: str
    blur: int

    def is_valid(self) -> bool:
        return (
            isinstance(self.bg_name, str)
            and self.bg_name
            and isinstance(self.blur, int)
            and MIN_BLUR <= self.blur <= MAX_BLUR
        )


class HelpThemeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.themes_dir = self.data_dir / "help_backgrounds"
        self.cache_dir = self.data_dir / "help_cache"
        self.config_file = self.data_dir / "help_theme.json"

        self._theme: HelpTheme | None = None
        self._initialized = False

    async def init(self) -> None:
        """Async initialization. Must be called before use."""
        if self._initialized:
            return

        await asyncio.to_thread(self.themes_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.cache_dir.mkdir, parents=True, exist_ok=True)

        await self._load_config()
        await self._ensure_default_background()
        self._initialized = True

    async def _load_config(self) -> None:
        """Load theme configuration from JSON file."""

        def _load():
            if self.config_file.exists():
                try:
                    with open(self.config_file, encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"[HelpTheme] Failed to load config: {e}")
                    return None
            return None

        data = await asyncio.to_thread(_load)
        if data:
            self._theme = HelpTheme(
                bg_name=data.get("bg_name", DEFAULT_BG_NAME),
                blur=int(data.get("blur", DEFAULT_BLUR)),
            )
            if not self._theme.is_valid():
                logger.warning("[HelpTheme] Invalid theme config, using defaults")
                self._theme = HelpTheme(bg_name=DEFAULT_BG_NAME, blur=DEFAULT_BLUR)
        else:
            self._theme = HelpTheme(bg_name=DEFAULT_BG_NAME, blur=DEFAULT_BLUR)

    async def _save_config(self) -> None:
        """Save theme configuration to JSON file."""
        data = {
            "bg_name": self._theme.bg_name,
            "blur": self._theme.blur,
        }

        async def _save():
            try:
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[HelpTheme] Failed to save config: {e}")

        await asyncio.to_thread(_save)

    async def _ensure_default_background(self) -> None:
        """Ensure default background exists."""
        default_path = self.themes_dir / f"{DEFAULT_BG_NAME}.png"

        def _check_and_create():
            if not default_path.exists():
                try:
                    from PIL import Image, ImageDraw

                    img = Image.new("RGB", (1200, 1600), color=(30, 30, 40))
                    draw = ImageDraw.Draw(img)
                    draw.rectangle([(0, 0), (1200, 1600)], fill=(40, 40, 60))
                    for i in range(0, 1200, 100):
                        draw.line([(i, 0), (i, 1600)], fill=(50, 50, 80), width=1)
                    for i in range(0, 1600, 100):
                        draw.line([(0, i), (1200, i)], fill=(50, 50, 80), width=1)
                    img.save(default_path)
                    logger.info(f"[HelpTheme] Created default background: {default_path}")
                except Exception as e:
                    logger.error(f"[HelpTheme] Failed to create default background: {e}")

        await asyncio.to_thread(_check_and_create)

    @property
    def theme(self) -> HelpTheme:
        """Get current theme settings."""
        return self._theme

    async def get_bg_path(self, bg_name: str | None = None) -> Path | None:
        """Get path to a background image. Returns None if not found."""
        name = bg_name or self._theme.bg_name

        def _check():
            path = self.themes_dir / f"{name}.png"
            if path.exists():
                return path
            if bg_name is not None:
                return None
            default_path = self.themes_dir / f"{DEFAULT_BG_NAME}.png"
            if default_path.exists():
                return default_path
            return None

        return await asyncio.to_thread(_check)

    async def list_backgrounds(self) -> list[str]:
        """List all available background names."""

        def _list():
            backgrounds = []
            if self.themes_dir.exists():
                for f in self.themes_dir.iterdir():
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                        backgrounds.append(f.stem)
            if not backgrounds:
                backgrounds.append(DEFAULT_BG_NAME)
            return sorted(set(backgrounds))

        return await asyncio.to_thread(_list)

    async def set_background(self, bg_name: str) -> tuple[bool, str]:
        """Set the current background. Returns (success, message)."""
        if not bg_name or not bg_name.strip():
            return False, "背景名称不能为空"

        bg_name = bg_name.strip()

        def _check_exists():
            path = self.themes_dir / f"{bg_name}.png"
            if path.exists():
                return True
            path = self.themes_dir / f"{bg_name}.jpg"
            if path.exists():
                return True
            return False

        exists = await asyncio.to_thread(_check_exists)
        if not exists:
            available = await self.list_backgrounds()
            return False, f"背景 '{bg_name}' 不存在。可用背景: {', '.join(available)}"

        self._theme.bg_name = bg_name
        await self._save_config()
        await self._clear_cache()
        return True, f"背景已切换为: {bg_name}"

    async def set_blur(self, blur_value: int) -> tuple[bool, str]:
        """Set the blur radius. Returns (success, message)."""
        try:
            blur = int(blur_value)
        except (TypeError, ValueError):
            return False, f"无效的模糊值: {blur_value}，请输入 0-30 之间的整数"

        if blur < MIN_BLUR or blur > MAX_BLUR:
            return False, f"模糊值必须在 {MIN_BLUR} 到 {MAX_BLUR} 之间，当前值: {blur}"

        self._theme.blur = blur
        await self._save_config()
        await self._clear_cache()
        return True, f"模糊强度已设置为: {blur}"

    async def reset(self) -> tuple[bool, str]:
        """Reset to default theme settings."""
        self._theme = HelpTheme(bg_name=DEFAULT_BG_NAME, blur=DEFAULT_BLUR)
        await self._save_config()
        await self._clear_cache()
        return True, "已恢复默认主题（背景: default, 模糊: 16）"

    def get_cache_path(self, version: int, is_admin: bool, bg_name: str, blur: int) -> Path:
        """Get the cache file path for given parameters."""
        suffix = "admin" if is_admin else "user"
        filename = f"help_{suffix}_bg-{bg_name}_blur-{blur}_v{version}.png"
        return self.cache_dir / filename

    def get_cache_key(self, is_admin: bool, bg_name: str, blur: int, version: int) -> str:
        """Generate a cache key string."""
        suffix = "admin" if is_admin else "user"
        return f"help_{suffix}_bg-{bg_name}_blur-{blur}_v{version}"

    async def _clear_cache(self) -> None:
        """Clear all cached help images."""

        def _clear():
            if self.cache_dir.exists():
                for f in self.cache_dir.iterdir():
                    if f.suffix.lower() == ".png":
                        try:
                            f.unlink()
                        except Exception as e:
                            logger.warning(f"[HelpTheme] Failed to delete cache file {f}: {e}")

        await asyncio.to_thread(_clear)
