"""
上下文注入模块 - 共享的身份隔离与认知指令
"""

import re

from astrbot.api import logger


async def parse_message_chain(msg: dict, plugin=None) -> str:
    """解析消息链为可读文本

    Args:
        msg: 消息字典，包含 sender 和 message 字段
        plugin: 插件实例，用于获取引用消息原文（可选）
    """
    nickname = msg.get("sender", {}).get("nickname", "未知")
    message = msg.get("message", [])

    if isinstance(message, str):
        return f"{nickname}: {message}"

    parts = []
    for i, seg in enumerate(message):
        seg_type = seg.get("type")
        data = seg.get("data", {})

        if seg_type == "text":
            text = data.get("text", "")
            if text:
                parts.append(text)
        elif seg_type == "image":
            sub_type = data.get("sub_type", 0)
            if sub_type == 1:
                parts.append("[动画表情]")
            else:
                parts.append("[图片]")
        elif seg_type == "at":
            qq = data.get("qq", "")
            if qq == "all":
                parts.append("@全体成员")
            else:
                parts.append(f"@{qq}")
        elif seg_type == "face":
            parts.append(f"[表情{data.get('id', '')}]")
        elif seg_type == "reply":
            msg_id = data.get("id", "")
            if msg_id and plugin:
                try:
                    platform_insts = plugin.context.platform_manager.platform_insts
                    if platform_insts:
                        platform = platform_insts[0]
                        if hasattr(platform, "get_client"):
                            bot = platform.get_client()
                            if bot:
                                result = await bot.call_action("get_msg", message_id=int(msg_id))
                                orig_msg = result.get("message", [])
                                sender_info = result.get("sender", {})
                                orig_sender = (
                                    sender_info.get("nickname")
                                    or sender_info.get("card")
                                    or str(sender_info.get("user_id", "未知"))
                                )
                                # 解析原文内容，去掉 @ 开头的部分
                                orig_content_list = []
                                for seg in orig_msg:
                                    if seg.get("type") == "text":
                                        text = seg.get("data", {}).get("text", "")
                                        # 去掉开头的 @xxx
                                        text = re.sub(r"^@\S+\s*", "", text)
                                        if text:
                                            orig_content_list.append(text)
                                orig_content = "".join(orig_content_list) if orig_content_list else "消息内容"
                                parts.append(f"[回复了 {orig_sender}: {orig_content}]")
                except Exception:
                    parts.append(f"[回复消息ID:{msg_id}]")
            else:
                parts.append(f"[回复消息ID:{msg_id}]")
        elif seg_type == "record":
            parts.append("[语音]")
        elif seg_type == "video":
            parts.append("[视频]")
        elif seg_type == "share":
            title = data.get("title", "")
            if title:
                parts.append(f"[分享: {title}]")

    content = "".join(parts) if parts else "[消息]"
    return f"{nickname}: {content}"


async def get_group_history(plugin, group_id: str, count: int = 10) -> str:
    """
    获取群消息历史（使用 NapCat API）

    Args:
        plugin: 插件实例
        group_id: 群号
        count: 获取消息数量

    Returns:
        格式化的群消息历史字符串
    """
    try:
        platform_insts = plugin.context.platform_manager.platform_insts
        if not platform_insts:
            return ""

        platform = platform_insts[0]
        if not hasattr(platform, "get_client"):
            return ""

        bot = platform.get_client()
        if not bot:
            return ""

        result = await bot.call_action(
            "get_group_msg_history",
            group_id=int(group_id),
            message_seq=0,
            count=count,
        )

        messages = result.get("messages", [])
        if not messages:
            return ""

        import asyncio

        results = await asyncio.gather(*[parse_message_chain(msg, plugin) for msg in messages])
        return "\n".join(results)
    except Exception as e:
        logger.debug(f"[ContextInjection] 获取群消息历史失败: {e}")
        return ""


def build_identity_context(
    user_id: str,
    user_name: str = "Unknown User",
    affinity: int = 50,
    role_info: str = "",
    is_group: bool = True,
) -> str:
    """
    构建身份隔离上下文指令（用于插嘴场景）

    Args:
        user_id: 用户ID
        user_name: 用户昵称
        affinity: 好感度 (0-100)
        role_info: 角色信息，如"（管理员）"
        is_group: 是否为群聊

    Returns:
        格式化的身份上下文字符串
    """
    chat_type = "群聊" if is_group else "私聊"

    # 好感度状态描述
    if affinity >= 80:
        affinity_status = "友好"
    elif affinity >= 60:
        affinity_status = "正常"
    elif affinity >= 40:
        affinity_status = "冷淡"
    elif affinity >= 20:
        affinity_status = "警惕"
    else:
        affinity_status = "敌对"

    context = f"""
【当前对话上下文 - 请严格遵守】：
- 当前对话类型：{chat_type}
- 当前说话用户：{user_name}{role_info}
- 用户ID：{user_id}
- 情感积分：{affinity}/100（状态：{affinity_status}）

【重要行为准则 - 必须严格遵守】：
1. 当前用户ID是 {user_id}，你是对这个ID的用户说话
2. 之前骂你的不是这个ID的人！是其他人！
3. 严格区分当前发送者（ID:{user_id}）与历史记录中其他群成员
4. 不要把别人骂你的账算到当前用户头上！
5. 情感评分是动态的，请根据当前用户的言行实时评估（可用 update_affinity 调整，范围 ±1~5）
6. 在回复引用内容时，请确保逻辑闭环，并明确回复对象
"""
    return context


def build_core_cognition_instructions(affinity: int = 50) -> str:
    """
    构建核心认知指令（精简版，用于插嘴场景）

    Args:
        affinity: 好感度

    Returns:
        核心认知指令字符串
    """
    return f"""
[当前用户] ID:xxx | 好感度:{affinity}/100
请严格区分当前用户与群里其他人的发言。
"""
