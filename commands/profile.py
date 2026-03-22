"""
Profile Commands - 用户画像相关命令实现
薄适配层：只负责解析用户输入、做权限和 scope 校验、调用 engine 入口。
"""

from .common import (
    CommandContext,
    ensure_admin,
    ensure_not_private_other,
    parse_target_user,
    RESP_MESSAGES,
)


async def handle_view(event, plugin):
    """查看用户画像"""
    target_user, raw_user_id = parse_target_user(event)
    ctx = CommandContext.from_event(event, plugin, target_user)

    if raw_user_id and not ctx.is_admin:
        return RESP_MESSAGES["permission_denied_profile"]

    deny = ensure_not_private_other(ctx, "查看")
    if deny:
        return deny

    if raw_user_id and ctx.is_admin and ctx.group_id:
        await plugin.profile.build_profile(target_user, ctx.scope_id, mode="update", umo=ctx.umo)
    return await plugin.profile.view_profile(ctx.scope_id, target_user)


async def handle_create(event, plugin):
    """创建用户画像"""
    target_user, raw_user_id = parse_target_user(event)
    ctx = CommandContext.from_event(event, plugin, target_user)

    if raw_user_id and not ctx.is_admin:
        return RESP_MESSAGES["permission_denied_profile"]

    deny = ensure_not_private_other(ctx, "创建")
    if deny:
        return deny

    return await plugin.profile.build_profile(target_user, ctx.scope_id, mode="create", umo=ctx.umo)


async def handle_update(event, plugin):
    """更新用户画像"""
    target_user, raw_user_id = parse_target_user(event)
    ctx = CommandContext.from_event(event, plugin, target_user)

    if raw_user_id and not ctx.is_admin:
        return RESP_MESSAGES["permission_denied_profile"]

    deny = ensure_not_private_other(ctx, "更新")
    if deny:
        return deny

    return await plugin.profile.build_profile(target_user, ctx.scope_id, mode="update", umo=ctx.umo)


async def handle_delete(event, plugin):
    """删除用户画像"""
    target_user, raw_user_id = parse_target_user(event)
    ctx = CommandContext.from_event(event, plugin, target_user)

    if raw_user_id and not ctx.is_admin:
        return RESP_MESSAGES["permission_denied_profile"]

    deny = ensure_not_private_other(ctx, "删除")
    if deny:
        return deny

    return await plugin.profile.delete_profile(ctx.scope_id, target_user)


async def handle_stats(event, plugin):
    """查看画像统计"""
    stats = await plugin.profile.list_profiles()
    return f"画像统计：\n- 用户数: {stats['total_users']}"


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    ctx = CommandContext.from_event(event, plugin)
    return ctx.is_admin
