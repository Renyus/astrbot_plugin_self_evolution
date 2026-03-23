"""
Sticker Commands - 表情包管理命令实现
薄适配层：只负责参数解析、权限校验，调用 StickerStore。
"""

from .common import CommandContext, RESP_MESSAGES


async def handle_sticker(event, plugin, action: str = "list", param: str = ""):
    """表情包管理命令"""
    sticker_store = plugin.sticker_store

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

        stickers, total = await sticker_store.list_stickers(page_size, offset)
        stats = await sticker_store.get_stats()

        if not stickers:
            if page == 1:
                return "暂无表情包。"
            return f"第 {page} 页暂无表情包。"

        total_pages = max(1, (total + page_size - 1) // page_size)
        has_next = page < total_pages

        result = [f"【表情包列表】（第 {page}/{total_pages} 页，共 {total} 张）\n"]
        for s in stickers:
            result.append(f"UUID:{s['uuid']} | 用户:{s['user_id']}")
        result.append(f"\n是否还有下一页：{'有' if has_next else '无'}")
        result.append("\n【管理指令】")
        result.append("/sticker list [页码]        # 查看指定页")
        result.append("/sticker preview <UUID>     # 预览指定UUID的表情包")
        result.append("/sticker delete <UUID>     # 删除指定UUID的表情包")
        result.append("/sticker clear             # 清空所有表情包")
        result.append("/sticker stats             # 查看统计")
        result.append("/sticker sync              # 同步本地文件")
        result.append("/sticker add              # 添加表情包（发送图片后用）")
        result.append("/sticker disable <UUID>   # 禁用表情包")
        result.append("/sticker enable <UUID>    # 启用表情包")
        return "\n".join(result)

    elif action == "delete":
        if not param:
            return "请提供要删除的表情包UUID"

        sticker_uuid = param.strip()
        deleted = await sticker_store.delete_sticker(sticker_uuid)
        if deleted:
            return f"已删除表情包: {sticker_uuid}"
        else:
            return f"未找到表情包: {sticker_uuid}"

    elif action == "disable":
        if not param:
            return "请提供要禁用的表情包UUID"

        sticker_uuid = param.strip()
        disabled = await sticker_store.disable_sticker(sticker_uuid)
        if disabled:
            return f"已禁用表情包: {sticker_uuid}"
        else:
            return f"未找到表情包: {sticker_uuid}"

    elif action == "enable":
        if not param:
            return "请提供要启用的表情包UUID"

        sticker_uuid = param.strip()
        enabled = await sticker_store.enable_sticker(sticker_uuid)
        if enabled:
            return f"已启用表情包: {sticker_uuid}"
        else:
            return f"未找到表情包: {sticker_uuid}"

    elif action == "preview":
        if not param:
            return "请提供要预览的表情包UUID"

        sticker_uuid = param.strip()
        sticker = await sticker_store.get_sticker(sticker_uuid)
        if not sticker:
            return f"未找到表情包: {sticker_uuid}"

        file_path = sticker_store.get_sticker_path(sticker)
        if not file_path or not file_path.exists():
            return f"表情包文件不存在: {sticker['filename']}"

        return {"image_path": str(file_path), "uuid": sticker_uuid}

    elif action == "clear":
        await sticker_store.clear_stickers()
        return "已清空所有表情包"

    elif action == "stats":
        stats = await sticker_store.get_stats()
        return f"【表情包统计】\n总计: {stats['total']} 张"

    elif action == "sync":
        result = await sticker_store.sync_from_files()
        return f"【表情包同步完成】\n新增: {result['added']} 张\n移除孤立记录: {result['removed']} 条"

    elif action == "migrate":
        result = await sticker_store.migrate_from_db(plugin.dao)
        msg = f"【表情包迁移完成】\n成功: {result['success']} 张\n失败: {result['failed']} 张"
        if result["errors"]:
            msg += f"\n失败详情: {'; '.join(result['errors'][:5])}"
            if len(result["errors"]) > 5:
                msg += f" ... 还有 {len(result['errors']) - 5} 个错误"
        return msg

    else:
        return (
            "【表情包管理】（全局）\n"
            "/sticker list [页码]          # 列出表情包\n"
            "/sticker preview <UUID>       # 预览指定UUID的表情包\n"
            "/sticker delete <UUID>       # 删除指定UUID的表情包\n"
            "/sticker disable <UUID>      # 禁用指定UUID的表情包\n"
            "/sticker enable <UUID>       # 启用指定UUID的表情包\n"
            "/sticker clear               # 清空所有表情包\n"
            "/sticker stats              # 查看统计\n"
            "/sticker sync              # 同步本地文件\n"
            "/sticker add               # 添加表情包（发送图片后用）\n"
            "/sticker migrate           # 从旧数据库迁移表情包"
        )


def check_admin(event, plugin):
    """检查是否有管理员权限"""
    ctx = CommandContext.from_event(event, plugin)
    return ctx.is_admin
