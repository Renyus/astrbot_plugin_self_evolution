"""
Profile Commands - 用户画像相关命令实现
"""


async def handle_view(event, plugin):
    """查看用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法查看他人画像。"

    if group_id:
        profile_key = f"{group_id}_{target_user}"
    else:
        profile_key = target_user

    if user_id and is_admin and group_id:
        result = await plugin.profile.build_profile(user_id, group_id, mode="update")
        if "失败" in result or "无消息" in result:
            return await plugin.profile.view_profile(user_id)
        else:
            return await plugin.profile.view_profile(user_id)
    else:
        return await plugin.profile.view_profile(profile_key)


async def handle_create(event, plugin):
    """创建用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    if not group_id:
        return "此指令需要在群聊中使用。"

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法给他人创建画像。"

    return await plugin.profile.build_profile(target_user, group_id, mode="create")


async def handle_update(event, plugin):
    """更新用户画像实现"""
    sender_id = str(event.get_sender_id())
    group_id = event.get_group_id()
    is_admin = event.is_admin() or (plugin.admin_users and sender_id in plugin.admin_users)

    if not group_id:
        return "此指令需要在群聊中使用。"

    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    target_user = user_id if user_id else sender_id

    if user_id and not is_admin:
        return "权限拒绝：普通用户无法更新他人画像。"

    return await plugin.profile.build_profile(target_user, group_id, mode="update")


async def handle_delete(event, plugin):
    """删除用户画像实现"""
    user_id = ""
    if hasattr(event, "message_str"):
        parts = event.message_str.split()
        if len(parts) > 1:
            user_id = parts[1].strip()

    return await plugin.profile.delete_profile(user_id)


async def handle_stats(event, plugin):
    """查看画像统计实现"""
    stats = await plugin.profile.list_profiles()
    return f"画像统计：\n- 用户数: {stats['total_users']}\n- 兴趣标签: {stats['total_tags']}\n- 性格特征: {stats['total_traits']}"


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    return event.is_admin() or (plugin.admin_users and str(event.get_sender_id()) in plugin.admin_users)
