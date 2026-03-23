"""
Sticker Commands - 表情包管理命令实现
薄适配层：只负责参数解析、权限校验，调用 dao。
"""

from .common import CommandContext, RESP_MESSAGES


async def handle_sticker(event, plugin, action: str = "list", param: str = ""):
    """表情包管理命令"""
    dao = plugin.dao

    if action == "list":
        if param:
            if not param.isdigit():
                return RESP_MESSAGES["invalid_param"]
            page = int(param)
            if page <= 0:
                return RESP_MESSAGES["invalid_param"]
        else:
            page = 1
        page_size = 10
        offset = (page - 1) * page_size

        stickers = await dao.get_stickers(page_size, offset=offset)
        total = await dao.get_sticker_count()
        today = await dao.get_today_sticker_count()

        if not stickers:
            if page == 1:
                return "暂无表情包。"
            return f"第 {page} 页暂无表情包。"

        total_pages = max(1, (total + page_size - 1) // page_size)
        has_next = page < total_pages

        result = [f"【表情包列表】（第 {page}/{total_pages} 页，共 {total} 张，今日新增 {today} 张）\n"]
        for s in stickers:
            result.append(f"UUID:{s['uuid']} | 用户:{s['user_id']}")
        result.append(f"\n是否还有下一页：{'有' if has_next else '无'}")
        result.append("\n【管理指令】")
        result.append("/sticker list [页码]        # 查看指定页")
        result.append("/sticker preview <UUID>     # 预览指定UUID的表情包")
        result.append("/sticker delete <UUID>     # 删除指定UUID的表情包")
        result.append("/sticker clear             # 清空所有表情包")
        result.append("/sticker stats             # 查看统计")
        return "\n".join(result)

    elif action == "delete":
        if not param:
            return "请提供要删除的表情包UUID"

        sticker_uuid = param.strip()
        deleted = await dao.delete_sticker_by_uuid(sticker_uuid)
        if deleted:
            return f"已删除表情包: {sticker_uuid}"
        else:
            return f"未找到表情包: {sticker_uuid}"

    elif action == "preview":
        if not param:
            return "请提供要预览的表情包UUID"

        sticker_uuid = param.strip()
        sticker = await dao.get_sticker_by_uuid(sticker_uuid)
        if not sticker:
            return f"未找到表情包: {sticker_uuid}"

        return {"image_url": sticker["url"], "uuid": sticker_uuid}

    elif action == "clear":
        count = await dao.get_sticker_count()
        if count == 0:
            return "表情包库已经是空的"

        deleted = 0
        for _ in range(count):
            if await dao.delete_oldest_sticker():
                deleted += 1

        return f"已清空 {deleted} 张表情包"

    elif action == "stats":
        stats = await dao.get_sticker_stats()
        return f"【表情包统计】\n总计: {stats['total']} 张\n今日新增: {stats['today']} 张"

    else:
        return (
            "【表情包管理】（全局）\n"
            "/sticker list [页码]          # 列出表情包\n"
            "/sticker preview <UUID>       # 预览指定UUID的表情包\n"
            "/sticker delete <UUID>        # 删除指定UUID的表情包\n"
            "/sticker clear               # 清空所有表情包\n"
            "/sticker stats               # 查看统计"
        )


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    ctx = CommandContext.from_event(event, plugin)
    return ctx.is_admin
