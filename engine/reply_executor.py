import random
from typing import Optional

from astrbot.api import logger

from .engagement_planner import EngagementPlanner
from .output_guard import OutputGuard
from .social_state import (
    EngagementExecutionResult,
    EngagementLevel,
    EngagementPlan,
    GroupSocialState,
)


class ReplyExecutor:
    """统一执行层：文本和 sticker 都走这里。

    文本走 generate_social_reply（主链路人格），sticker 走 entertainment 模块。
    执行失败不影响状态（由 Recorder 统一处理）。
    """

    def __init__(self, plugin, planner: EngagementPlanner, output_guard=None, stats=None):
        self.plugin = plugin
        self.planner = planner
        self.cfg = plugin.cfg
        self.output_guard = output_guard if output_guard is not None else OutputGuard(plugin)
        self._stats = stats

    async def execute(
        self,
        plan: EngagementPlan,
        state: GroupSocialState,
        trigger_text: str = "",
        user_id: str = "",
        sender_name: str = "群成员",
        quoted_info: str = "",
        at_info: str = "",
        is_active_trigger: bool = False,
    ) -> EngagementExecutionResult:
        if plan.level == EngagementLevel.IGNORE:
            self._debug(
                f"[ReplyExecutor] scope={getattr(state, 'scope_id', '?')} level=IGNORE action=none reason={plan.reason}"
            )
            scope_id = getattr(state, "scope_id", "unknown")
            if self._stats and plan.reason:
                self._stats.record_skip(scope_id, plan.reason)
            return EngagementExecutionResult(
                executed=False,
                level=plan.level,
                action="none",
                reason=plan.reason,
            )

        if plan.level == EngagementLevel.REACT:
            return await self._execute_sticker(plan, state, is_active_trigger)

        if plan.level == EngagementLevel.FULL:
            return await self._execute_text(
                plan, state, trigger_text, user_id, sender_name, quoted_info, at_info, is_active_trigger
            )

        return EngagementExecutionResult(
            executed=False,
            level=plan.level,
            action="none",
            reason="未知级别",
        )

    async def _execute_sticker(
        self, plan: EngagementPlan, state: GroupSocialState, is_active_trigger: bool
    ) -> EngagementExecutionResult:
        scope_id = getattr(state, "scope_id", "unknown")
        filename = await self._try_send_sticker(state.scope_id)
        if filename:
            if self._stats:
                if is_active_trigger:
                    self._stats.record_active_emoji(scope_id)
                else:
                    self._stats.record_passive_emoji(scope_id)
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.REACT,
                action="sticker",
                reason=plan.reason,
                actual_text=filename,
            )

        return EngagementExecutionResult(
            executed=False,
            level=EngagementLevel.REACT,
            action="none",
            reason="无表情包",
        )

    async def _execute_text(
        self,
        plan: EngagementPlan,
        state: GroupSocialState,
        trigger_text: str = "",
        user_id: str = "",
        sender_name: str = "群成员",
        quoted_info: str = "",
        at_info: str = "",
        is_active_trigger: bool = False,
    ) -> EngagementExecutionResult:
        final_prob = getattr(self.cfg, "interject_trigger_probability", 0.5)
        if random.random() > final_prob:
            return EngagementExecutionResult(
                executed=False,
                level=EngagementLevel.FULL,
                action="none",
                reason=f"概率门未通过({final_prob})",
            )

        group_id = state.scope_id

        try:
            umo = (
                getattr(self.plugin, "get_group_umo", lambda g: None)(group_id)
                if hasattr(self.plugin, "get_group_umo")
                else None
            )
            if not umo:
                return EngagementExecutionResult(
                    executed=False,
                    level=EngagementLevel.FULL,
                    action="none",
                    reason="无umo",
                )

            if is_active_trigger:
                effective_user_id = self.plugin._get_bot_id()
                effective_sender_name = getattr(self.plugin, "persona_name", "黑塔")
            else:
                effective_user_id = user_id or "unknown"
                effective_sender_name = sender_name

            decision = plan.to_speech_decision()
            if is_active_trigger and decision.text_mode == "reply":
                decision.text_mode = "interject"

            req = await self.plugin.build_generation_spec(
                group_id=group_id,
                user_id=effective_user_id,
                sender_name=effective_sender_name,
                trigger_text=trigger_text,
                scene=plan.scene.value,
                decision=decision,
                anchor_text=plan.anchor_text,
                quoted_info=quoted_info,
                at_info=at_info,
            )
            if not req:
                return EngagementExecutionResult(
                    executed=False,
                    level=EngagementLevel.FULL,
                    action="none",
                    reason="prompt构建失败",
                )

            text = await self.plugin.inject_and_chat(req, umo)

            if text:
                result = self.output_guard.check(text, decision)
                if result.status == "pass":
                    success = await self._send_message(group_id, text)
                    if success:
                        if self._stats:
                            if is_active_trigger:
                                self._stats.record_active_text(
                                    group_id, anchor_type=getattr(plan, "anchor_type", None) or ""
                                )
                            else:
                                self._stats.record_passive_text(group_id)
                        return EngagementExecutionResult(
                            executed=True,
                            level=EngagementLevel.FULL,
                            action="text",
                            reason=plan.reason,
                            actual_text=text,
                        )
                elif result.status == "downgrade_to_emoji":
                    logger.debug(f"[ReplyExecutor] OutputGuard: {result.reason}，降级表情包")
                    if self._stats:
                        self._stats.record_guard_blocked(group_id, result.reason)
                        self._stats.record_degraded(group_id, result.reason)
                    sticker = await self._try_send_sticker(state.scope_id)
                    if sticker:
                        if self._stats:
                            if is_active_trigger:
                                self._stats.record_active_emoji(group_id)
                            else:
                                self._stats.record_passive_emoji(group_id)
                        return EngagementExecutionResult(
                            executed=True,
                            level=EngagementLevel.FULL,
                            action="sticker",
                            reason=f"内容审查降级: {result.reason}",
                            actual_text=sticker,
                        )
                    return EngagementExecutionResult(
                        executed=False,
                        level=EngagementLevel.FULL,
                        action="none",
                        reason=f"内容审查降级但无表情包",
                    )
                elif result.status == "retry_shorter":
                    logger.debug(f"[ReplyExecutor] OutputGuard RETRY: {result.reason}")
                    if self._stats:
                        self._stats.record_guard_blocked(group_id, result.reason)
                    return EngagementExecutionResult(
                        executed=False,
                        level=EngagementLevel.FULL,
                        action="none",
                        reason=f"内容审查需缩短: {result.reason}",
                    )
                else:
                    logger.debug(f"[ReplyExecutor] OutputGuard DROP: {result.reason}")
                    if self._stats:
                        self._stats.record_guard_blocked(group_id, result.reason)
                    return EngagementExecutionResult(
                        executed=False,
                        level=EngagementLevel.FULL,
                        action="none",
                        reason=f"内容审查丢弃: {result.reason}",
                    )
        except Exception as e:
            logger.warning(f"[ReplyExecutor] Full回复生成失败: {e}")

        sticker = await self._try_send_sticker(state.scope_id)
        if sticker:
            if self._stats:
                if is_active_trigger:
                    self._stats.record_active_emoji(group_id)
                else:
                    self._stats.record_passive_emoji(group_id)
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.FULL,
                action="sticker",
                reason="LLM失败降级",
                actual_text=sticker,
            )

        return EngagementExecutionResult(
            executed=False,
            level=EngagementLevel.FULL,
            action="none",
            reason="执行失败",
        )

    async def _try_send_sticker(self, group_id: str) -> Optional[str]:
        if not hasattr(self.plugin, "entertainment"):
            return None

        try:
            sticker_engine = self.plugin.entertainment
            if not hasattr(sticker_engine, "send_sticker_for_engagement"):
                return None

            filename = await sticker_engine.send_sticker_for_engagement(group_id)
            return filename

        except Exception as e:
            logger.debug(f"[ReplyExecutor] 表情包发送失败: {e}")
            return None

        return None

    async def _send_message(self, group_id: str, text: str) -> bool:
        try:
            if not self.plugin.context.platform_manager.platform_insts:
                return False

            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.bot

            await bot.send_group_msg(group_id=int(group_id), message=[{"type": "text", "data": {"text": text}}])
            return True
        except Exception as e:
            logger.warning(f"[ReplyExecutor] 发送消息失败: {e}")
            return False
