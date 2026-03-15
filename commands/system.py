"""
System Commands - 系统命令实现
"""

import os


async def handle_version(event, plugin):
    """显示插件版本"""
    version = getattr(plugin, "_cached_version", None)
    if version is None:
        metadata_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata.yaml")
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version:"):
                        version = line.split(":", 1)[1].strip()
                        break
        if not version:
            version = "未知"
        plugin._cached_version = version
    return f"【Self-Evolution】版本: {version}"


async def handle_help(event, plugin):
    """显示帮助信息"""
    user_id = event.get_sender_id()
    is_admin = event.is_admin() or (plugin.admin_users and str(user_id) in plugin.admin_users)

    help_text = """【Self-Evolution 指令帮助】

 【用户指令】
/reflect              - 手动触发一次自我反省
/affinity             - 查看 AI 对你的好感度评分
/view [用户ID]        - 查看用户画像（普通用户只能看自己，管理员可指定用户）
/create [用户ID]      - 手动创建画像（普通用户只能给自己创建，管理员可指定用户）
/update [用户ID]      - 手动更新画像（普通用户只能更新自己，管理员可指定用户）"""

    if is_admin:
        help_text += """

【管理员指令】（仅管理员可用）
/set_affinity <用户ID> <分数> - 强制重置指定用户的好感度（0-100）
/delete_profile <用户ID>      - 删除指定用户的画像
/profile_stats               - 查看画像系统统计信息
/sticker                     - 表情包管理
/shut <分钟>                 - 让AI在当前群闭嘴（0取消）
/db                          - 数据库管理"""

    return help_text
