"""
Profile Commands - 用户画像相关命令实现
"""


PRIVATE_SCOPE_PREFIX = "private_"


def _resolve_profile_scope_id(group_id, sender_id: str) -> str:
    return str(group_id) if group_id else f"{PRIVATE_SCOPE_PREFIX}{sender_id}"


async def handle_view(event, plugin):
    """查看用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    scope_id = _resolve_profile_scope_id(group_id, sender_id)
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法查看他人画像。"

    if not group_id and user_id and user_id != sender_id:
        return "私聊场景仅支持查看当前会话用户的画像。"

    if user_id and is_admin and group_id:
        result = await plugin.profile.build_profile(user_id, scope_id, mode="update", umo=event.unified_msg_origin)
        if "失败" in result or "无消息" in result:
            return await plugin.profile.view_profile(scope_id, user_id)
        else:
            return await plugin.profile.view_profile(scope_id, user_id)
    else:
        return await plugin.profile.view_profile(scope_id, target_user)


async def handle_create(event, plugin):
    """创建用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    scope_id = _resolve_profile_scope_id(group_id, sender_id)
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法给他人创建画像。"

    if not group_id and target_user != sender_id:
        return "私聊场景仅支持为当前会话用户创建画像。"

    return await plugin.profile.build_profile(target_user, scope_id, mode="create", umo=event.unified_msg_origin)


async def handle_update(event, plugin):
    """更新用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    scope_id = _resolve_profile_scope_id(group_id, sender_id)
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法更新他人画像。"

    if not group_id and target_user != sender_id:
        return "私聊场景仅支持更新当前会话用户的画像。"

    return await plugin.profile.build_profile(target_user, scope_id, mode="update", umo=event.unified_msg_origin)


async def handle_delete(event, plugin):
    """删除用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    scope_id = _resolve_profile_scope_id(group_id, sender_id)

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if not group_id and target_user != sender_id:
        return "私聊场景仅支持删除当前会话用户的画像。"

    return await plugin.profile.delete_profile(scope_id, target_user)


async def handle_stats(event, plugin):
    """查看画像统计实现"""
    stats = await plugin.profile.list_profiles()
    return f"画像统计：\n- 用户数: {stats['total_users']}"


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    return event.is_admin() or (plugin.admin_users and str(event.get_sender_id()) in plugin.admin_users)
