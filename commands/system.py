"""System command display helpers."""

import asyncio
import sys
from pathlib import Path

try:
    from ..engine.help_catalog import format_text_help
except ImportError:
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from engine.help_catalog import format_text_help


async def _read_metadata_version() -> str:
    """Read version from metadata.yaml asynchronously."""

    def _read():
        metadata_path = Path(__file__).resolve().parents[1] / "metadata.yaml"
        if metadata_path.exists():
            try:
                with open(metadata_path, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("version:"):
                            return line.split(":", 1)[1].strip()
            except Exception:
                pass
        return None

    version = await asyncio.to_thread(_read)
    return version if version else "未知"


async def handle_version(event, plugin):
    """显示插件版本"""
    version = getattr(plugin, "_cached_version", None)
    if version is None:
        version = await _read_metadata_version()
        plugin._cached_version = version
    return f"【Self-Evolution】版本: {version}"


async def handle_help(event, plugin):
    """显示帮助信息"""
    user_id = event.get_sender_id()
    is_admin = event.is_admin() or (plugin.admin_users and str(user_id) in plugin.admin_users)
    return format_text_help(is_admin=is_admin)


async def handle_help_text(event, plugin) -> str:
    """显示纯文本帮助信息"""
    user_id = event.get_sender_id()
    is_admin = event.is_admin() or (plugin.admin_users and str(user_id) in plugin.admin_users)
    return format_text_help(is_admin=is_admin)
