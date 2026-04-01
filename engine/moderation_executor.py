"""
Phase 5: Moderation Execution Layer

职责：
- 接收 ModerationResult + 执行上下文
- 通过 NapCat/OneBot API 执行真实处罚动作
- 支持：delete_msg / set_group_ban / set_group_kick
- 只在 suggested_action == "delete" 或 "review" 时执行
"""

from typing import Optional

from astrbot.api import logger

from .moderation_classifier import ModerationResult


def _unwrap_action_response(ret):
    if not isinstance(ret, dict):
        return {}
    data = ret.get("data")
    if isinstance(data, dict):
        return data
    return ret


async def _is_bot_admin_in_group(event, group_id: str | int) -> bool:
    """检查 bot 是否是群管理员或群主。"""
    try:
        from astrbot.core.utils.quoted_message.onebot_client import OneBotClient

        client = OneBotClient(event)
        if client._call_action is None:
            return False

        bot_user_id = str(event.get_self_id())
        result = await client._call_action(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(bot_user_id),
        )
        if not result:
            return False
        data = result.get("data", result) if isinstance(result, dict) else result
        role = data.get("role", "member") if isinstance(data, dict) else "member"
        return role in ("admin", "owner")
    except Exception:
        return False


async def _get_ob_client(event) -> Optional[callable]:
    """从 event 解析 OneBotClient。"""
    try:
        from astrbot.core.utils.quoted_message.onebot_client import OneBotClient

        client = OneBotClient(event)
        logger.debug(f"[ModerationExecutor] OneBotClient 初始化成功, _call_action={client._call_action}")
        if client._call_action is not None:
            return client
    except Exception as e:
        logger.warning(f"[ModerationExecutor] OneBotClient 初始化失败: {e}")
    return None


async def execute_delete_message(event, message_id: str | int) -> bool:
    """通过 NapCat 删除指定消息。"""
    logger.info(
        f"[ModerationExecutor] execute_delete_message 开始: message_id={message_id} (type={type(message_id).__name__})"
    )
    client = await _get_ob_client(event)
    if not client:
        logger.warning(f"[ModerationExecutor] 无法获取 OneBotClient，删除失败")
        return False

    try:
        msg_id_str = str(message_id)
        logger.info(f"[ModerationExecutor] 准备调用 delete_msg: message_id={msg_id_str}")
        raw_result = await client._call_action(
            "delete_msg", message_id=int(msg_id_str) if msg_id_str.isdigit() else msg_id_str
        )
        logger.info(
            f"[ModerationExecutor] delete_msg 原始返回: raw_result={raw_result} (type={type(raw_result).__name__})"
        )
        result = _unwrap_action_response(raw_result)
        logger.info(f"[ModerationExecutor] delete_msg unwrap后: result={result}")
        if not result:
            logger.warning(f"[ModerationExecutor] delete_msg 返回 falsy result: {result}")
            return False
        logger.info(f"[ModerationExecutor] 消息已删除: message_id={message_id}")
        return True
    except Exception as e:
        logger.warning(f"[ModerationExecutor] delete_msg 失败: {e}, message_id={message_id}")
        return False


async def execute_ban_user(
    event,
    group_id: str | int,
    user_id: str | int,
    duration_minutes: int = 60,
) -> bool:
    """通过 NapCat 禁言指定用户。Duration_minutes=0 为解除禁言。"""
    client = await _get_ob_client(event)
    if not client:
        logger.warning(f"[ModerationExecutor] 无法获取 OneBotClient，禁言失败")
        return False

    try:
        duration_seconds = duration_minutes * 60
        result = await client.call(
            "set_group_ban",
            {
                "group_id": str(group_id),
                "user_id": str(user_id),
                "duration": duration_seconds,
            },
        )
        if result is None:
            logger.warning(f"[ModerationExecutor] set_group_ban 返回 None")
            return False
        action = "解除禁言" if duration_seconds == 0 else f"禁言 {duration_minutes} 分钟"
        logger.info(f"[ModerationExecutor] 用户已{action}: group={group_id} user={user_id}")
        return True
    except Exception as e:
        logger.warning(f"[ModerationExecutor] set_group_ban 失败: {e}")
        return False


async def execute_kick_user(
    event,
    group_id: str | int,
    user_id: str | int,
    reject_add_request: bool = False,
) -> bool:
    """通过 NapCat 将指定用户踢出群聊。"""
    client = await _get_ob_client(event)
    if not client:
        logger.warning(f"[ModerationExecutor] 无法获取 OneBotClient，踢人失败")
        return False

    try:
        result = await client.call(
            "set_group_kick",
            {
                "group_id": str(group_id),
                "user_id": str(user_id),
                "reject_add_request": reject_add_request,
            },
        )
        if result is None:
            logger.warning(f"[ModerationExecutor] set_group_kick 返回 None")
            return False
        logger.info(
            f"[ModerationExecutor] 用户已被踢出: group={group_id} user={user_id} reject_add={reject_add_request}"
        )
        return True
    except Exception as e:
        logger.warning(f"[ModerationExecutor] set_group_kick 失败: {e}")
        return False


async def execute_moderation(
    result: ModerationResult,
    event,
    group_id: str | int,
    user_id: str | int,
    message_id: str | int,
) -> str:
    """根据 ModerationResult 执行对应处罚。

    返回执行结果描述字符串。
    - "executed_delete" / "executed_ban" / "executed_kick" / "skipped" / "failed"
    """
    action = result.suggested_action

    if action == "ignore":
        logger.info(f"[ModerationExecutor] 放过: group={group_id} user={user_id} action={action}")
        return "skipped"

    if action == "delete":
        ok = await execute_delete_message(event, message_id)
        return "executed_delete" if ok else "failed"

    if action == "review":
        ok = await execute_ban_user(event, group_id, user_id, duration_minutes=60)
        return "executed_ban" if ok else "failed"

    logger.info(f"[ModerationExecutor] 未知 action={action}，跳过: group={group_id} user={user_id}")
    return "skipped"
