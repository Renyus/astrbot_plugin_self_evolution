import asyncio
import random
from datetime import datetime
from typing import Optional

from astrbot.api import logger

from .engagement_planner import EngagementPlanner
from .engagement_stats import InteractionKind
from .output_guard import OutputGuard
from .social_state import (
    EngagementExecutionResult,
    EngagementLevel,
    EngagementPlan,
    GroupSocialState,
    SceneType,
)

# QQ 原生表情分类映射
_MOOD_FACES = {
    "happy": [178, 179, 14, 3],
    "agree": [179, 63, 21],
    "laugh": [178, 14, 49],
    "thinking": [32, 33, 34],
    "neutral": [0, 1, 2],
}

# 消息表情回应映射（NapCat set_msg_emoji_like 的 emoji_id）
_REACTION_EMOJIS = {
    SceneType.CASUAL: ["128077", "128514", "128516"],  # 👍😂😄
    SceneType.HELP: ["128588", "128170", "128077"],  # 🙌👊👍
    SceneType.DEBATE: ["129300", "128064"],  # 🤔👀
    SceneType.IDLE: ["128077"],  # 👍
}


def _split_message_naturally(text: str) -> list[str]:
    """将长消息自然切分为多条短消息，模拟真人分段发送。"""
    if len(text) <= 25:
        return [text]

    splitters = {"。", "？", "！", "!", "?", "\n", "…"}
    segments: list[str] = []
    current = ""

    for char in text:
        current += char
        if char in splitters and len(current) >= 6:
            segments.append(current.strip())
            current = ""

    if current.strip():
        segments.append(current.strip())

    # 合并过短的段落（<4字）到前一段
    merged: list[str] = []
    for seg in segments:
        if merged and len(seg) < 4:
            merged[-1] += seg
        else:
            merged.append(seg)

    # 最多 3 段
    return merged[:3] if len(merged) > 3 else merged


class ReplyExecutor:
    """统一执行层：文本、sticker、emoji reaction 都走这里。

    文本走 generate_social_reply（主链路人格），sticker 走 entertainment 模块。
    执行失败不影响状态（由 Recorder 统一处理）。

    v2: 增加人性化延迟、消息分段、reply/at/face 消息段、emoji reaction。
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
        message_id: str = "",
        has_reply_to_bot: bool = False,
        has_mention: bool = False,
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
            return await self._execute_react(plan, state, is_active_trigger, message_id)

        if plan.level == EngagementLevel.FULL:
            return await self._execute_text(
                plan,
                state,
                trigger_text,
                user_id,
                sender_name,
                quoted_info,
                at_info,
                is_active_trigger,
                message_id=message_id,
                has_reply_to_bot=has_reply_to_bot,
                has_mention=has_mention,
            )

        return EngagementExecutionResult(
            executed=False,
            level=plan.level,
            action="none",
            reason="未知级别",
        )

    # ------------------------------------------------------------------
    #  REACT: 优先 emoji reaction，退化到表情包图片
    # ------------------------------------------------------------------

    async def _execute_react(
        self,
        plan: EngagementPlan,
        state: GroupSocialState,
        is_active_trigger: bool,
        message_id: str = "",
    ) -> EngagementExecutionResult:
        scope_id = getattr(state, "scope_id", "unknown")

        # 70% 概率尝试消息表情回应（更像真人），30% 走表情包
        if message_id and random.random() < 0.7:
            reacted = await self._try_emoji_reaction(scope_id, message_id, plan.scene)
            if reacted:
                if self._stats:
                    if is_active_trigger:
                        self._stats.record_active_reaction(scope_id)
                    else:
                        self._stats.record_passive_reaction(scope_id)
                return EngagementExecutionResult(
                    executed=True,
                    level=EngagementLevel.REACT,
                    action="emoji_reaction",
                    reason=plan.reason,
                )

        # 退化到表情包
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
            reason="无表情包且 reaction 失败",
        )

    async def _try_emoji_reaction(self, scope_id: str, message_id: str, scene: SceneType = SceneType.CASUAL) -> bool:
        """尝试用 NapCat set_msg_emoji_like 给消息点赞。"""
        try:
            emoji_ids = _REACTION_EMOJIS.get(scene, _REACTION_EMOJIS[SceneType.CASUAL])
            emoji_id = random.choice(emoji_ids)

            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                return False
            bot = platform_insts[0].bot
            await bot.call_action("set_msg_emoji_like", message_id=int(message_id), emoji_id=emoji_id)
            logger.debug(f"[ReplyExecutor] emoji reaction 成功: scope={scope_id} msg={message_id} emoji={emoji_id}")
            return True
        except Exception as e:
            logger.debug(f"[ReplyExecutor] emoji reaction 失败: {e}")
            return False

    # ------------------------------------------------------------------
    #  FULL TEXT: 生成 → 审查 → 延迟 → 分段发送
    # ------------------------------------------------------------------

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
        message_id: str = "",
        has_reply_to_bot: bool = False,
        has_mention: bool = False,
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
                    # 决定引用策略
                    reply_to = self._decide_reply_to(message_id, has_reply_to_bot, has_mention, is_active_trigger)

                    # 人性化延迟
                    affinity = await self._get_user_affinity(user_id)
                    await self._human_typing_delay(text, affinity)

                    # 分段发送
                    success = await self._send_message_segmented(
                        group_id,
                        text,
                        reply_to_msg_id=reply_to,
                        at_user_id=user_id if (has_mention and not is_active_trigger) else "",
                        scene=plan.scene,
                    )
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
                    # 降级时也优先尝试 emoji reaction
                    if message_id and random.random() < 0.5:
                        reacted = await self._try_emoji_reaction(group_id, message_id, plan.scene)
                        if reacted:
                            if self._stats:
                                if is_active_trigger:
                                    self._stats.record_active_reaction(group_id)
                                else:
                                    self._stats.record_passive_reaction(group_id)
                            return EngagementExecutionResult(
                                executed=True,
                                level=EngagementLevel.FULL,
                                action="emoji_reaction",
                                reason=f"内容审查降级→emoji: {result.reason}",
                            )
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

    # ------------------------------------------------------------------
    #  辅助方法
    # ------------------------------------------------------------------

    def _decide_reply_to(
        self,
        message_id: str,
        has_reply_to_bot: bool,
        has_mention: bool,
        is_active_trigger: bool,
    ) -> str:
        """决定是否使用 reply 消息段引用回复。

        策略：
        - 用户回复了 bot 消息 → 必须引用
        - 用户 @了 bot → 50% 概率引用
        - 主动插嘴 → 不引用（更自然）
        - 被动回应 → 30% 概率引用
        """
        if not message_id:
            return ""
        if is_active_trigger:
            return ""
        if has_reply_to_bot:
            return message_id
        if has_mention and random.random() < 0.5:
            return message_id
        if random.random() < 0.3:
            return message_id
        return ""

    async def _human_typing_delay(self, text: str, affinity: int = 50):
        """模拟真人打字延迟：思考时间 + 打字时间。"""
        base_chars_per_second = 4.0

        # 好感度影响：好感高 → 回复更快
        speed_multiplier = 0.8 + (affinity / 100) * 0.4  # 0.8~1.2

        # 思考时间
        think_time = random.uniform(0.5, 2.5)

        # 打字时间
        char_count = len(text)
        typing_time = char_count / (base_chars_per_second * speed_multiplier)

        # 总延迟（上限 6 秒，防止群聊话题已换）
        total_delay = min(think_time + typing_time, 6.0)

        # 随机波动 ±20%
        total_delay *= random.uniform(0.8, 1.2)

        # 最小 0.8 秒（避免几乎无延迟）
        total_delay = max(total_delay, 0.8)

        logger.debug(
            f"[ReplyExecutor] 人性化延迟: {total_delay:.1f}s (think={think_time:.1f}s type={typing_time:.1f}s affinity={affinity})"
        )
        await asyncio.sleep(total_delay)

    async def _get_user_affinity(self, user_id: str) -> int:
        """获取用户好感度，异常时返回默认值。"""
        try:
            if user_id and hasattr(self.plugin, "dao"):
                return await self.plugin.dao.get_affinity(user_id)
        except Exception:
            pass
        return 50

    async def _send_message_segmented(
        self,
        group_id: str,
        text: str,
        reply_to_msg_id: str = "",
        at_user_id: str = "",
        scene: SceneType = SceneType.CASUAL,
    ) -> bool:
        """分段发送消息，模拟真人打字节奏。

        短消息（<=25字）直接发送；长消息自然切分为多条，每条之间随机间隔。
        第一条消息可携带 reply/at 消息段。
        """
        segments = _split_message_naturally(text)

        for i, seg in enumerate(segments):
            if not seg.strip():
                continue

            message = []

            if i == 0:
                # 第一条消息：可带 reply + at
                if reply_to_msg_id:
                    message.append({"type": "reply", "data": {"id": str(reply_to_msg_id)}})
                if at_user_id:
                    message.append({"type": "at", "data": {"qq": str(at_user_id)}})
                    message.append({"type": "text", "data": {"text": " "}})

            message.append({"type": "text", "data": {"text": seg}})

            # 最后一段：20% 概率追加 QQ 原生表情
            if i == len(segments) - 1:
                self._maybe_append_face(message, scene)

            success = await self._send_raw_message(group_id, message)
            if not success:
                return False

            # 各段之间加随机间隔（模拟分条打字）
            if i < len(segments) - 1:
                inter_delay = random.uniform(0.5, 1.8)
                await asyncio.sleep(inter_delay)

        return True

    def _maybe_append_face(self, message: list, scene: SceneType = SceneType.CASUAL):
        """20% 概率追加一个 QQ 原生表情。"""
        if random.random() >= 0.2:
            return
        mood = "happy" if scene == SceneType.CASUAL else "agree" if scene == SceneType.HELP else "neutral"
        faces = _MOOD_FACES.get(mood, _MOOD_FACES["neutral"])
        face_id = random.choice(faces)
        message.append({"type": "face", "data": {"id": str(face_id)}})

    async def _send_raw_message(self, group_id: str, message: list) -> bool:
        """底层发送：接受完整 message 段列表。"""
        try:
            if not self.plugin.context.platform_manager.platform_insts:
                return False
            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.bot
            await bot.send_group_msg(group_id=int(group_id), message=message)
            return True
        except Exception as e:
            logger.warning(f"[ReplyExecutor] 发送消息失败: {e}")
            return False

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
        """兼容旧调用：纯文本发送，内部转发到 _send_raw_message。"""
        return await self._send_raw_message(group_id, [{"type": "text", "data": {"text": text}}])
