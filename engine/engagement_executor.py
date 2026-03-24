import random
import time
from typing import Optional

from astrbot.api import logger

from .engagement_planner import EngagementPlanner
from .social_state import (
    EngagementExecutionResult,
    EngagementLevel,
    EngagementPlan,
    GroupSocialState,
)


class EngagementExecutor:
    REACT_TEMPLATES = [
        "嗯",
        "哦",
        "有点意思",
        "继续",
        "哈",
        "是嘛",
        "哦对",
        "好吧",
        "这样啊",
        "哦豁",
    ]

    BRIEF_TEMPLATES = [
        "这问题有意思",
        "确实",
        "可以这么理解",
        "值得想想",
        "有道理",
        "有点东西",
        "怎么说呢",
    ]

    def __init__(self, plugin, planner: EngagementPlanner):
        self.plugin = plugin
        self.planner = planner
        self.cfg = plugin.cfg

    def _debug(self, msg: str):
        if getattr(self.cfg, "engagement_debug_enabled", False):
            logger.debug(msg)

    async def execute(self, plan: EngagementPlan, state: GroupSocialState) -> EngagementExecutionResult:
        if plan.level == EngagementLevel.IGNORE:
            self._debug(
                f"[Engagement] execute=yes group={getattr(state, 'group_id', '?')} level=IGNORE action=none reason={plan.reason}"
            )
            return EngagementExecutionResult(
                executed=False,
                level=plan.level,
                action="none",
                reason=plan.reason,
            )

        if plan.level == EngagementLevel.REACT:
            result = await self._execute_react(plan, state)
            self._debug(
                f"[Engagement] execute=yes group={getattr(state, 'group_id', '?')} level=REACT action={result.action}"
            )
            return result

        if plan.level == EngagementLevel.BRIEF:
            result = await self._execute_brief(plan, state)
            self._debug(
                f"[Engagement] execute=yes group={getattr(state, 'group_id', '?')} level=BRIEF action={result.action}"
            )
            return result

        if plan.level == EngagementLevel.FULL:
            result = await self._execute_full(plan, state)
            self._debug(
                f"[Engagement] execute=yes group={getattr(state, 'group_id', '?')} level=FULL action={result.action}"
            )
            return result

        return EngagementExecutionResult(
            executed=False,
            level=plan.level,
            action="none",
            reason="未知级别",
        )

    async def _execute_react(self, plan: EngagementPlan, state: GroupSocialState) -> EngagementExecutionResult:
        sticker = await self._try_send_sticker(state.scope_id)
        if sticker:
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.REACT,
                action="sticker",
                reason=plan.reason,
                actual_text=sticker,
            )

        text = random.choice(self.REACT_TEMPLATES)
        success = await self._send_message(state.scope_id, text)
        if success:
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.REACT,
                action="text",
                reason=plan.reason,
                actual_text=text,
            )

        return EngagementExecutionResult(
            executed=False,
            level=EngagementLevel.REACT,
            action="none",
            reason="发送失败",
        )

    async def _execute_brief(self, plan: EngagementPlan, state: GroupSocialState) -> EngagementExecutionResult:
        text = random.choice(self.BRIEF_TEMPLATES)
        success = await self._send_message(state.scope_id, text)
        if success:
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.BRIEF,
                action="text",
                reason=plan.reason,
                actual_text=text,
            )

        return EngagementExecutionResult(
            executed=False,
            level=EngagementLevel.BRIEF,
            action="none",
            reason="发送失败",
        )

    async def _execute_full(self, plan: EngagementPlan, state: GroupSocialState) -> EngagementExecutionResult:
        final_prob = getattr(self.cfg, "interject_trigger_probability", 0.5)
        if random.random() > final_prob:
            return EngagementExecutionResult(
                executed=False,
                level=EngagementLevel.FULL,
                action="none",
                reason=f"概率门未通过({final_prob})",
            )

        group_id = state.scope_id
        persona = self.cfg.persona_name or "黑塔"

        identity_ctx = f"[身份] 你是在群聊中的{persona}，以自然的方式参与讨论。"

        prompt = (
            f"你是{persona}。\n"
            f"{identity_ctx}\n"
            f"当前场景：{plan.scene.value}\n"
            f"参与原因：{plan.reason}\n"
            f"请生成一段简短自然的回复，不超过50字。\n"
        )

        try:
            group_umo = self.plugin.get_group_umo(group_id) if hasattr(self.plugin, "get_group_umo") else None
            if not group_umo:
                return EngagementExecutionResult(
                    executed=False,
                    level=EngagementLevel.FULL,
                    action="none",
                    reason="无UMo provider",
                )

            from ..cognition.san import SANSystem

            san = SANSystem(self.plugin)
            san_ctx = san.get_injection_context()
            prompt = f"{prompt}\n{san_ctx}"

            llm_provider = self.plugin.context.get_using_provider(umo=group_umo)
            resp = await llm_provider.text_chat(prompt=prompt, contexts=[])
            text = resp.completion_text.strip()[:100] if hasattr(resp, "completion_text") else str(resp).strip()[:100]

            success = await self._send_message(group_id, text)
            if success:
                return EngagementExecutionResult(
                    executed=True,
                    level=EngagementLevel.FULL,
                    action="text",
                    reason=plan.reason,
                    actual_text=text,
                )
        except Exception as e:
            logger.warning(f"[EngagementExecutor] Full回复生成失败: {e}")

        fallback = random.choice(self.BRIEF_TEMPLATES)
        success = await self._send_message(state.scope_id, fallback)
        if success:
            return EngagementExecutionResult(
                executed=True,
                level=EngagementLevel.FULL,
                action="text",
                reason=f"LLM失败降级: {e}",
                actual_text=fallback,
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
            if not hasattr(sticker_engine, "get_random_sticker"):
                return None

            sticker_uuid = await sticker_engine.get_random_sticker()
            if not sticker_uuid:
                return None

            if hasattr(sticker_engine, "send_sticker_by_uuid"):
                await sticker_engine.send_sticker_by_uuid(group_id, sticker_uuid)
                return f"[sticker:{sticker_uuid}]"

        except Exception as e:
            logger.debug(f"[EngagementExecutor] 表情包发送失败: {e}")

        return None

    async def _send_message(self, group_id: str, text: str) -> bool:
        try:
            if not self.plugin.context.platform_manager.platform_insts:
                return False

            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.bot

            await bot.send_group_msg(group_id=int(group_id), message=[{"type": "plain", "data": {"text": text}}])
            return True
        except Exception as e:
            logger.warning(f"[EngagementExecutor] 发送消息失败: {e}")
            return False
