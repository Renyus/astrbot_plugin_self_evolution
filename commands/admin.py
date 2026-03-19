"""
Admin Commands - 管理员命令实现
"""

import time


async def handle_shut(event, plugin, minutes: str = ""):
    """闭嘴命令实现"""
    user_id = str(event.get_sender_id())
    current_group = event.get_group_id()
    is_admin = event.is_admin() or (plugin.admin_users and user_id in plugin.admin_users)

    if current_group and current_group in plugin._shut_until_by_group:
        if time.time() < plugin._shut_until_by_group[current_group]:
            if not is_admin:
                return None

    if not is_admin:
        return "权限拒绝：此操作仅限管理员执行。"

    if not current_group:
        return "此命令需要在群聊中使用"

    if not minutes:
        if current_group in plugin._shut_until_by_group:
            if time.time() < plugin._shut_until_by_group[current_group]:
                remaining = int(plugin._shut_until_by_group[current_group] - time.time())
                return f"[!] 当前群闭嘴模式，剩余 {remaining} 秒"
        return "[OK] 当前群正常模式，未闭嘴"

    try:
        minutes_val = int(minutes)
    except ValueError:
        return "请输入有效的分钟数"

    if minutes_val < 0:
        return "分钟数不能为负数"

    if minutes_val == 0:
        if current_group in plugin._shut_until_by_group:
            del plugin._shut_until_by_group[current_group]
        return "[OK] 已取消当前群闭嘴模式"

    target_time = time.time() + minutes_val * 60
    plugin._shut_until_by_group[current_group] = target_time
    return f"[OK] 当前群已开启闭嘴模式，持续 {minutes_val} 分钟"


async def handle_db(event, plugin, action: str = "", param: str = ""):
    """数据库管理命令实现"""
    user_id = str(event.get_sender_id())
    is_admin = event.is_admin() or (plugin.admin_users and user_id in plugin.admin_users)

    if not is_admin:
        return "权限拒绝：此操作仅限管理员执行。"

    action = action.lower()
    dao = plugin.dao

    if action == "show":
        stats = await dao.get_db_stats()
        table_cn = {
            "pending_evolutions": "待审核进化",
            "session_reflections": "会话反思",
            "group_daily_reports": "群日报",
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
        plugin._pending_db_reset[user_id] = time.time() + 30
        return "[!] 确认清空所有数据？\n此操作不可恢复！\n请在 30 秒内输入 /db confirm 确认执行。"

    elif action == "confirm":
        pending_time = plugin._pending_db_reset.get(user_id, 0)

        if time.time() > pending_time:
            plugin._pending_db_reset.pop(user_id, None)
            return "操作已超时，请重新输入 /db reset"

        results = await dao.reset_all_data()
        plugin._pending_db_reset.pop(user_id, None)

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


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    return event.is_admin() or (plugin.admin_users and str(event.get_sender_id()) in plugin.admin_users)
