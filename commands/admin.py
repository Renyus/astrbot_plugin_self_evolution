"""
Admin Commands - 管理员命令实现
薄适配层：只负责参数解析、权限校验，调用 dao/底层接口。
"""

import time

from .common import CommandContext, RESP_MESSAGES, ensure_admin, ensure_group


async def handle_shut(event, plugin, minutes: str = ""):
    """闭嘴命令"""
    ctx = CommandContext.from_event(event, plugin)

    deny = ensure_admin(ctx)
    if deny:
        return deny

    deny = ensure_group(ctx)
    if deny:
        return deny

    current_group = ctx.group_id

    if current_group and current_group in plugin._shut_until_by_group:
        if time.time() < plugin._shut_until_by_group[current_group]:
            if not ctx.is_admin:
                return None

    if not minutes:
        if current_group in plugin._shut_until_by_group:
            if time.time() < plugin._shut_until_by_group[current_group]:
                remaining = int(plugin._shut_until_by_group[current_group] - time.time())
                return f"[!] 当前群闭嘴模式，剩余 {remaining} 秒"
        return "[OK] 当前群正常模式，未闭嘴"

    try:
        minutes_val = int(minutes)
    except ValueError:
        return RESP_MESSAGES["invalid_param"]

    if minutes_val < 0:
        return RESP_MESSAGES["negative_minutes"]

    if minutes_val == 0:
        if current_group in plugin._shut_until_by_group:
            del plugin._shut_until_by_group[current_group]
        return "[OK] 已取消当前群闭嘴模式"

    target_time = time.time() + minutes_val * 60
    plugin._shut_until_by_group[current_group] = target_time
    return f"[OK] 当前群已开启闭嘴模式，持续 {minutes_val} 分钟"


async def handle_db(event, plugin, action: str = "", param: str = ""):
    """数据库管理命令"""
    ctx = CommandContext.from_event(event, plugin)

    deny = ensure_admin(ctx)
    if deny:
        return deny

    action = action.lower()
    dao = plugin.dao

    if action == "show":
        stats = await dao.get_db_stats()
        table_cn = {
            "pending_evolutions": "待审核进化",
            "session_reflections": "会话反思",
            "group_daily_reports": "会话日报",
            "user_relationships": "用户关系",
            "user_interactions": "用户互动",
            "stickers": "表情包",
        }
        msg = ["【数据库统计】\n"]
        for table, count in stats.items():
            cn_name = table_cn.get(table, table)
            msg.append(f"- {cn_name}: {count}")
        return "\n".join(msg)

    elif action == "reset":
        plugin._pending_db_reset[ctx.sender_id] = time.time() + 30
        return "[!] 确认清空所有数据？\n此操作不可恢复！\n请在 30 秒内输入 /db confirm 确认执行。"

    elif action == "confirm":
        pending_time = plugin._pending_db_reset.get(ctx.sender_id, 0)

        if time.time() > pending_time:
            plugin._pending_db_reset.pop(ctx.sender_id, None)
            return "操作已超时，请重新输入 /db reset"

        results = await dao.reset_all_data()
        plugin._pending_db_reset.pop(ctx.sender_id, None)

        msg = ["[OK] 数据库已清空：\n"]
        for table, count in results.items():
            msg.append(f"- {table}: {count} 条")

        return "\n".join(msg)

    else:
        return (
            "【数据库管理】\n"
            "/db show      # 查看数据库统计\n"
            "/db reset     # 清空所有数据（需确认）\n"
            "/db confirm   # 确认执行清空"
        )


async def handle_set_san(event, plugin, value: str = ""):
    """查看或设置精力值"""
    ctx = CommandContext.from_event(event, plugin)
    deny = ensure_admin(ctx)
    if deny:
        return deny

    if not plugin.san_system.enabled:
        return "SAN 精力系统未启用"

    if not value:
        current = plugin.san_system.value
        status = plugin.san_system.get_status()
        return f"当前精力值：{current}/{plugin.san_system.max_value}（{status}）"

    try:
        new_val = int(value)
    except ValueError:
        return RESP_MESSAGES["invalid_param"]

    actual = plugin.san_system.set_value(new_val)
    status = plugin.san_system.get_status()
    return f"精力值已设置为：{actual}/{plugin.san_system.max_value}（{status}）"


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    ctx = CommandContext.from_event(event, plugin)
    return ctx.is_admin
