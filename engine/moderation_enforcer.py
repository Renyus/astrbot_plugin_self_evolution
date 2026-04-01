"""
Phase 6: Moderation Enforcement Layer

职责：
- 接收完整审核上下文，执行真实或 dry-run 处罚
- 落库 evidence（审核结果与执行结果分离）
- 通过 NapCat/OneBot API 执行 delete_msg / set_group_ban / set_group_kick
- 强制保留 dry-run 模式作为安全网
- 默认 enforcement_disabled=True（安全优先）
- 发送 reaction 消息和处罚理由消息
"""

import dataclasses
import random
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger

from .caption_service import CaptionResult
from .moderation_classifier import (
    ModerationCategory,
    ModerationResult,
    RiskLevel,
    SuggestedAction,
)
from .moderation_executor import (
    execute_ban_user,
    execute_delete_message,
    execute_kick_user,
)


def _get_trigger_category(nsfw_result: ModerationResult, promo_result: ModerationResult) -> ModerationCategory:
    action_order = {SuggestedAction.DELETE: 3, SuggestedAction.REVIEW: 2, SuggestedAction.IGNORE: 1}
    nsfw_score = action_order.get(nsfw_result.suggested_action, 0)
    promo_score = action_order.get(promo_result.suggested_action, 0)
    if nsfw_score >= promo_score:
        return ModerationCategory.NSFW
    return ModerationCategory.PROMO


def _get_reason_message(
    category: ModerationCategory,
    nsfw_reason: str,
    promo_reason: str,
    ban_duration_minutes: int,
) -> str:
    msg = nsfw_reason if category == ModerationCategory.NSFW else promo_reason
    return msg.replace("{duration}", str(ban_duration_minutes))


@dataclasses.dataclass
class EnforcementResult:
    dry_run: bool
    evidence_written: bool
    final_action: str
    delete_attempted: bool = False
    delete_success: bool = False
    ban_attempted: bool = False
    ban_success: bool = False
    kick_attempted: bool = False
    kick_success: bool = False
    violation_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "evidence_written": self.evidence_written,
            "final_action": self.final_action,
            "delete_attempted": self.delete_attempted,
            "delete_success": self.delete_success,
            "ban_attempted": self.ban_attempted,
            "ban_success": self.ban_success,
            "kick_attempted": self.kick_attempted,
            "kick_success": self.kick_success,
            "violation_id": self.violation_id,
        }


async def enforce_moderation(
    event,
    group_id: str | int,
    user_id: str | int,
    message_id: str | int,
    caption_result: CaptionResult,
    nsfw_result: Optional[ModerationResult],
    promo_result: Optional[ModerationResult],
    merged_result: ModerationResult,
    enforcement_enabled: bool = False,
    dao=None,
    escalation_threshold: int = 2,
    ban_duration_minutes: int = 60,
    nsfw_warning_message: str = "我草，色图",
    nsfw_ban_reason_message: str = "检测到不当内容，已处理",
    promo_warning_message: str = "二维码？引流是吧",
    promo_ban_reason_message: str = "检测到引流内容，已处理",
) -> EnforcementResult:
    """Moderation enforcement 统一入口。

    始终先写 evidence，再根据 enforcement_enabled 决定是否执行真实动作。
    review 动作在 24h 内累计 >= 2 次时自动升级为 kick。
    发送 warning 消息（执行前）和 ban/kick 理由消息（执行后）。
    """
    logger.info(
        f"[ModerationEnforcer] enforce_moderation 收到: message_id={message_id!r} (type={type(message_id).__name__}) group={group_id} user={user_id}"
    )
    dry_run = not enforcement_enabled
    action = merged_result.suggested_action

    trigger_category = ModerationCategory.NSFW
    if nsfw_result and promo_result:
        trigger_category = _get_trigger_category(nsfw_result, promo_result)

    warning_msgs = nsfw_warning_message if trigger_category == ModerationCategory.NSFW else promo_warning_message

    async def _send_warning():
        if not warning_msgs:
            return
        msg = random.choice(warning_msgs) if isinstance(warning_msgs, list) else warning_msgs
        try:
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.message.components import Plain

            chain = MessageChain([Plain(msg)])
            await event.send(chain)
            logger.info(f"[ModerationEnforcer] 发送 warning 消息: {msg}")
        except Exception as e:
            logger.warning(f"[ModerationEnforcer] 发送 warning 消息失败: {e}")

    async def _send_reason():
        reason = _get_reason_message(
            trigger_category, nsfw_ban_reason_message, promo_ban_reason_message, ban_duration_minutes
        )
        if not reason:
            return
        try:
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.message.components import Plain

            chain = MessageChain([Plain(reason)])
            await event.send(chain)
            logger.info(f"[ModerationEnforcer] 发送 ban 理由消息: {reason}")
        except Exception as e:
            logger.warning(f"[ModerationEnforcer] 发送 ban 理由消息失败: {e}")

    evidence_written = False
    violation_id = None

    if dao:
        try:
            violation_id = await dao.add_moderation_violation(
                group_id=str(group_id),
                user_id=str(user_id),
                message_id=str(message_id),
                category=merged_result.category,
                confidence=merged_result.confidence,
                risk_level=merged_result.risk_level,
                nsfw_category=nsfw_result.category,
                nsfw_confidence=nsfw_result.confidence,
                nsfw_risk=nsfw_result.risk_level,
                promo_category=promo_result.category,
                promo_confidence=promo_result.confidence,
                promo_risk=promo_result.risk_level,
                caption_text=caption_result.text if caption_result else "",
                action_taken=f"dryrun_{action}" if dry_run else action,
                reasons="|".join(merged_result.reasons) if merged_result.reasons else "",
            )
            evidence_written = True
            logger.info(
                f"[ModerationEnforcer] evidence 落库: violation_id={violation_id} "
                f"group={group_id} user={user_id} category={merged_result.category}"
            )
        except Exception as e:
            logger.warning(f"[ModerationEnforcer] evidence 落库失败: {e}")

    result = EnforcementResult(
        dry_run=dry_run,
        evidence_written=evidence_written,
        final_action=f"dryrun_{action}" if dry_run else action,
        violation_id=violation_id,
    )

    if action == SuggestedAction.IGNORE:
        logger.info(
            f"[ModerationEnforcer] {'[DRY-RUN]' if dry_run else '[EXEC]'} "
            f"忽略: group={group_id} user={user_id} action=ignore"
        )
        return result

    action_log_name = {
        SuggestedAction.DELETE: "delete",
        SuggestedAction.REVIEW: "review",
        SuggestedAction.KICK: "kick",
    }.get(action, action)

    if dry_run:
        logger.info(
            f"[ModerationEnforcer] [DRY-RUN] "
            f"would_{action_log_name} "
            f"group={group_id} user={user_id} msg={message_id} "
            f"category={merged_result.category} confidence={merged_result.confidence}"
        )
        return result

    if action == SuggestedAction.DELETE:
        await _send_warning()
        result.delete_attempted = True
        result.delete_success = await execute_delete_message(event, message_id)
        result.final_action = "delete_success" if result.delete_success else "delete_failed"
        logger.info(
            f"[ModerationEnforcer] [EXEC] "
            f"delete {'成功' if result.delete_success else '失败'}: "
            f"group={group_id} user={user_id} msg={message_id}"
        )
        if dao and violation_id:
            try:
                await dao.update_moderation_violation_action(violation_id, result.final_action)
            except Exception:
                pass
        return result

    if action == SuggestedAction.REVIEW:
        await _send_warning()
        result.delete_attempted = True
        result.delete_success = await execute_delete_message(event, message_id)
        logger.info(
            f"[ModerationEnforcer] [EXEC] "
            f"review 前置 delete {'成功' if result.delete_success else '失败'}: "
            f"group={group_id} user={user_id} msg={message_id}"
        )
        should_kick = False
        if dao:
            try:
                cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
                recent = await dao.count_user_violations_since(str(group_id), str(user_id), cutoff)
                if recent >= escalation_threshold:
                    should_kick = True
                    logger.info(
                        f"[ModerationEnforcer] [EXEC] 升级 kick: group={group_id} user={user_id} 24h内违规{recent}次"
                    )
            except Exception as e:
                logger.warning(f"[ModerationEnforcer] 违规计数查询失败: {e}")

        if should_kick:
            result.kick_attempted = True
            result.kick_success = await execute_kick_user(event, group_id, user_id)
            result.final_action = "kick_success" if result.kick_success else "kick_failed"
            logger.info(
                f"[ModerationEnforcer] [EXEC] "
                f"kick {'成功' if result.kick_success else '失败'}: "
                f"group={group_id} user={user_id}"
            )
        else:
            result.ban_attempted = True
            result.ban_success = await execute_ban_user(event, group_id, user_id, duration_minutes=ban_duration_minutes)
            result.final_action = "ban_success" if result.ban_success else "ban_failed"
            logger.info(
                f"[ModerationEnforcer] [EXEC] "
                f"ban {'成功' if result.ban_success else '失败'}: "
                f"group={group_id} user={user_id} duration={ban_duration_minutes}min"
            )
        if dao and violation_id:
            try:
                await dao.update_moderation_violation_action(violation_id, result.final_action)
            except Exception:
                pass
        await _send_reason()
        return result

    if action == SuggestedAction.KICK:
        await _send_warning()
        result.kick_attempted = True
        result.kick_success = await execute_kick_user(event, group_id, user_id)
        result.final_action = "kick_success" if result.kick_success else "kick_failed"
        logger.info(
            f"[ModerationEnforcer] [EXEC] "
            f"kick {'成功' if result.kick_success else '失败'}: "
            f"group={group_id} user={user_id}"
        )
        if dao and violation_id:
            try:
                await dao.update_moderation_violation_action(violation_id, result.final_action)
            except Exception:
                pass
        await _send_reason()
        return result

    logger.info(f"[ModerationEnforcer] [EXEC] 未知 action={action}，跳过: group={group_id} user={user_id}")
    return result
