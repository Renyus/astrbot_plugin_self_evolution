"""
Commands 模块 - 命令处理
"""

from .admin import check_admin as check_admin_admin
from .admin import handle_db, handle_shut
from .profile import (
    check_admin as check_profile_admin,
)
from .profile import (
    handle_create,
    handle_delete,
    handle_stats,
    handle_update,
    handle_view,
)
from .sticker import check_admin as check_sticker_admin
from .sticker import handle_sticker
from .system import handle_help, handle_version

__all__ = [
    "check_admin_admin",
    "check_profile_admin",
    "check_sticker_admin",
    "handle_create",
    "handle_db",
    "handle_delete",
    "handle_help",
    "handle_shut",
    "handle_stats",
    "handle_sticker",
    "handle_update",
    "handle_version",
    "handle_view",
]
