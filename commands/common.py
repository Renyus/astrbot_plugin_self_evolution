"""
Commands Common - 命令层公共基础设施
薄适配层：只负责解析用户输入、做权限和 scope 校验、统一响应格式。
"""

from dataclasses import dataclass
from typing import Any

PRIVATE_SCOPE_PREFIX = "private_"

RESP_MESSAGES = {
    "permission_denied": "权限拒绝：此操作仅限管理员执行。",
    "permission_denied_profile": "权限拒绝：普通用户无法操作他人画像。",
    "private_only_self": "私聊场景仅支持操作当前会话用户的画像。",
    "group_only": "此命令需要在群聊中使用",
    "invalid_param": "请输入有效的参数。",
    "negative_minutes": "分钟数不能为负数。",
}


@dataclass
class CommandContext:
    sender_id: str
    group_id: str | None
    scope_id: str
    is_private: bool
    is_admin: bool
    target_user_id: str | None
    umo: str | None
    _event: Any

    @classmethod
    def from_event(cls, event, plugin, target_user_id: str | None = None):
        sender_id = str(event.get_sender_id())
        group_id = event.get_group_id()
        scope_id = str(group_id) if group_id else f"{PRIVATE_SCOPE_PREFIX}{sender_id}"
        is_private = group_id is None
        is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)
        umo = getattr(event, "unified_msg_origin", None)
        return cls(
            sender_id=sender_id,
            group_id=group_id,
            scope_id=scope_id,
            is_private=is_private,
            is_admin=is_admin,
            target_user_id=target_user_id,
            umo=umo,
            _event=event,
        )


def ensure_admin(ctx: CommandContext) -> str | None:
    if ctx.is_admin:
        return None
    return RESP_MESSAGES["permission_denied"]


def ensure_not_private_other(ctx: CommandContext, action: str = "操作") -> str | None:
    if ctx.is_private and ctx.target_user_id and ctx.target_user_id != ctx.sender_id:
        return f"私聊场景仅支持{action}当前会话用户的画像。"
    return None


def ensure_group(ctx: CommandContext) -> str | None:
    if ctx.group_id:
        return None
    return RESP_MESSAGES["group_only"]


def parse_target_user(event, default_to_sender=True) -> tuple[str, str]:
    sender_id = str(event.get_sender_id())
    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()
    target = user_id if user_id else (sender_id if default_to_sender else "")
    return target, user_id
