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
/system help          - 查看当前指令帮助
/system version       - 查看插件版本
/reflect              - 手动触发一次自我反省
/affinity             - 查看 AI 对你的好感度评分
/san show             - 查看当前 SAN 状态
/今日老婆             - 查看今日老婆
/profile view [用户ID]   - 查看用户画像（普通用户只能看自己，管理员可指定用户）
/profile create [用户ID] - 手动创建画像（普通用户只能给自己创建，管理员可指定用户）
/profile update [用户ID] - 手动更新画像（普通用户只能更新自己，管理员可指定用户）

说明：
- 普通用户可使用 /san show 查看当前 SAN 状态
- 管理员可使用 /san set [数值] 查看或修改 SAN 值
- /sticker list [页码] 支持分页查看"""

    if is_admin:
        help_text += """

【管理员指令】（仅管理员可用）
/set_affinity <用户ID> <分数> - 强制重置指定用户的好感度（0-100）
/san set [数值]              - 查看或设置当前 SAN
/profile delete <用户ID>     - 删除指定用户的画像
/profile stats              - 查看画像系统统计信息
/sticker list [页码]         - 分页查看表情包
/sticker delete <UUID>      - 删除指定表情包
/sticker clear              - 清空所有表情包
/sticker stats              - 查看表情包统计
/evolution review [页码]    - 查看待审人格进化
/evolution approve <ID>     - 批准人格进化
/evolution reject <ID>      - 拒绝人格进化
/evolution clear            - 清空待审人格进化
/shut <分钟>                 - 让AI在当前群闭嘴（0取消）
/db                          - 数据库管理（show/reset/rebuild）"""

    return help_text
