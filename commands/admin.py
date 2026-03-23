"""
Admin command handlers.

The command layer only parses input, performs permission checks, and
delegates to DAO / subsystem methods.
"""

from __future__ import annotations

import time

from .common import CommandContext, RESP_MESSAGES, ensure_admin, ensure_group


def _format_san_status(san_system) -> str:
    if not san_system.enabled:
        return "SAN 精力系统未启用"
    current = san_system.value
    status = san_system.get_status()
    return f"当前精力值：{current}/{san_system.max_value}（{status}）"


def _create_db_confirmation(plugin, user_id: str, action: str) -> None:
    plugin._pending_db_reset[user_id] = {
        "action": action,
        "expires_at": time.time() + 30,
    }


def _read_db_confirmation(plugin, user_id: str) -> tuple[str, float]:
    pending = plugin._pending_db_reset.get(user_id)
    if isinstance(pending, dict):
        return str(pending.get("action", "")), float(pending.get("expires_at", 0))
    if pending:
        return "reset", float(pending)
    return "", 0.0


async def handle_san_show(event, plugin):
    """查看当前 SAN 状态。"""
    return _format_san_status(plugin.san_system)


async def handle_shut(event, plugin, minutes: str = ""):
    """群级闭嘴命令。"""
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
    """数据库管理命令。"""
    del param

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

    if action == "reset":
        _create_db_confirmation(plugin, ctx.sender_id, "reset")
        return "[!] 确认清空所有数据？\n此操作不可恢复！\n请在 30 秒内输入 /db confirm 确认执行。"

    if action == "rebuild":
        _create_db_confirmation(plugin, ctx.sender_id, "rebuild")
        return "[!] 确认删除插件数据库文件并重建？\n这不是清空，而是会删除当前数据库文件后重新建库！\n请在 30 秒内输入 /db confirm 确认执行。"

    if action == "confirm":
        pending_action, pending_time = _read_db_confirmation(plugin, ctx.sender_id)

        if time.time() > pending_time:
            plugin._pending_db_reset.pop(ctx.sender_id, None)
            return "操作已超时，请重新输入 /db reset 或 /db rebuild"

        plugin._pending_db_reset.pop(ctx.sender_id, None)

        if pending_action == "rebuild":
            results = await dao.delete_and_rebuild()
            msg = ["[OK] 数据库文件已删除并重建：\n"]
            deleted_files = results.get("deleted_files", [])
            if deleted_files:
                msg.append(f"- 已删除文件: {', '.join(deleted_files)}")
            else:
                msg.append("- 已删除文件: 无（原文件不存在或已被清理）")
            msg.append(f"- 数据库路径: {results.get('db_path', getattr(dao, 'db_path', ''))}")
            return "\n".join(msg)

        results = await dao.reset_all_data()
        msg = ["[OK] 数据库已清空：\n"]
        for table, count in results.items():
            msg.append(f"- {table}: {count} 条")
        return "\n".join(msg)

    return (
        "【数据库管理】\n"
        "/db show      # 查看数据库统计\n"
        "/db reset     # 清空所有数据（需确认）\n"
        "/db rebuild   # 删除数据库文件并重建（需确认）\n"
        "/db confirm   # 确认执行 reset/rebuild"
    )


async def handle_set_san(event, plugin, value: str = ""):
    """查看或设置当前 SAN。"""
    ctx = CommandContext.from_event(event, plugin)
    deny = ensure_admin(ctx)
    if deny:
        return deny

    if not value:
        return _format_san_status(plugin.san_system)

    if not plugin.san_system.enabled:
        return "SAN 精力系统未启用"

    try:
        new_val = int(value)
    except ValueError:
        return RESP_MESSAGES["invalid_param"]

    actual = plugin.san_system.set_value(new_val)
    status = plugin.san_system.get_status()
    return f"精力值已设置为：{actual}/{plugin.san_system.max_value}（{status}）"


def check_admin(event, plugin):
    """检查是否有管理员权限。"""
    ctx = CommandContext.from_event(event, plugin)
    return ctx.is_admin
