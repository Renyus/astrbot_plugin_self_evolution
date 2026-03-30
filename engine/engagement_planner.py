import time

from astrbot.api import logger

from .social_state import (
    EngagementEligibility,
    EngagementLevel,
    EngagementPlan,
    GroupSocialState,
    SceneType,
)
from .speech_types import AnchorType, OpportunityKind, SpeechOpportunity


QUESTION_WORDS = {"吗", "呢", "怎么", "如何", "为什么", "啥", "什么", "是不是", "能不能", "要不要", "?"}
EMOTION_WORDS = {"哈哈", "哈哈哈", "笑死", "卧槽", "牛逼", "厉害", "赞", "哭", "笑", "怒", "生气", "烦"}
DEBATE_INDICATORS = {"不对", "不是", "但是", "可是", "虽然", "然而", "错的", "胡说", "滚", "傻"}
HELP_INDICATORS = {"帮", "帮我", "请问", "请教", "求助", "救命", "急", "在线等"}


class EngagementPlanner:
    def __init__(self, plugin):
        self.plugin = plugin
        self.cfg = plugin.cfg

    def _debug(self, msg: str):
        if getattr(self.cfg, "engagement_debug_enabled", False):
            logger.debug(msg)

    def classify_scene(self, messages: list[dict], state: GroupSocialState) -> SceneType:
        if not messages:
            return SceneType.IDLE

        now = time.time()
        recent_msgs = messages[:10] if len(messages) > 10 else messages

        question_count = 0
        emotion_count = 0
        debate_count = 0
        help_count = 0
        total_chars = 0

        for msg in recent_msgs:
            text = msg.get("text", "") or ""
            total_chars += len(text)

            text_lower = text.lower()
            for qw in QUESTION_WORDS:
                if qw in text:
                    question_count += 1
                    break

            for ew in EMOTION_WORDS:
                if ew in text:
                    emotion_count += 1
                    break

            for dw in DEBATE_INDICATORS:
                if dw in text:
                    debate_count += 1
                    break

            for hw in HELP_INDICATORS:
                if hw in text:
                    help_count += 1
                    break

        silence_minutes = (now - state.last_message_time) / 60 if state.last_message_time > 0 else 999

        if silence_minutes > 10 and state.message_count_window < 5:
            return SceneType.IDLE

        if debate_count >= 3 or (debate_count >= 2 and emotion_count >= 2):
            return SceneType.DEBATE

        if help_count >= 2 or (help_count >= 1 and question_count >= 2):
            return SceneType.HELP

        return SceneType.CASUAL

    def compute_scene_windows(self, messages: list[dict], state: GroupSocialState) -> dict:
        if not messages:
            return {
                "question_count_window": 0,
                "emotion_count_window": 0,
                "mention_bot_recently": False,
            }

        recent_msgs = messages[:10] if len(messages) > 10 else messages
        bot_id = str(self.plugin._get_bot_id()) if hasattr(self.plugin, "_get_bot_id") else ""

        question_count = 0
        emotion_count = 0
        mention_bot_recently = False

        for msg in recent_msgs:
            text = msg.get("text", "") or ""

            for qw in QUESTION_WORDS:
                if qw in text:
                    question_count += 1
                    break

            for ew in EMOTION_WORDS:
                if ew in text:
                    emotion_count += 1
                    break

            if bot_id:
                for seg in msg.get("message", []):
                    if isinstance(seg, dict) and seg.get("type") == "at":
                        at_qq = str(seg.get("data", {}).get("qq", ""))
                        if at_qq == bot_id or at_qq == "all":
                            mention_bot_recently = True

        return {
            "question_count_window": question_count,
            "emotion_count_window": emotion_count,
            "mention_bot_recently": mention_bot_recently,
        }

    def check_eligibility(
        self, state: GroupSocialState, cooldown_seconds: int = 30, min_new_messages: int = 3
    ) -> EngagementEligibility:
        now = time.time()

        silence_seconds = now - state.last_message_time if state.last_message_time > 0 else 999

        if state.last_bot_message_time > 0:
            bot_silence = now - state.last_bot_message_time
            if bot_silence < cooldown_seconds:
                self._debug(
                    f"[Engagement] eligible=no scope={getattr(state, 'scope_id', '?')} reason=cooldown remaining={int(cooldown_seconds - bot_silence)}s"
                )
                return EngagementEligibility(
                    allowed=False,
                    reason_code="E_COOLDOWN",
                    reason_text=f"Bot冷却中，还需{int(cooldown_seconds - bot_silence)}秒",
                    silence_seconds=bot_silence,
                )

        if silence_seconds < 5:
            self._debug(
                f"[Engagement] eligible=no scope={getattr(state, 'scope_id', '?')} reason=silence_too_short {int(silence_seconds)}s"
            )
            return EngagementEligibility(
                allowed=False,
                reason_code="E_SILENCE",
                reason_text=f"群太活跃，{int(silence_seconds)}秒前才有消息",
                silence_seconds=silence_seconds,
            )

        if state.consecutive_bot_replies >= 2:
            self._debug(
                f"[Engagement] eligible=no scope={getattr(state, 'scope_id', '?')} reason=bot_flood count={state.consecutive_bot_replies}"
            )
            return EngagementEligibility(
                allowed=False,
                reason_code="E_BOT_FLOOD",
                reason_text=f"Bot连续回复{state.consecutive_bot_replies}次，暂缓",
                silence_seconds=silence_seconds,
            )

        if state.message_count_window < min_new_messages:
            self._debug(
                f"[Engagement] eligible=no scope={getattr(state, 'scope_id', '?')} reason=msg_count {state.message_count_window}/{min_new_messages}"
            )
            return EngagementEligibility(
                allowed=False,
                reason_code="E_MSG_COUNT",
                reason_text=f"消息量不足({state.message_count_window}/{min_new_messages})",
                new_message_count=state.message_count_window,
                silence_seconds=silence_seconds,
            )

        self._debug(
            f"[Engagement] eligible=yes scope={getattr(state, 'scope_id', '?')} msgs={state.message_count_window} silence={int(silence_seconds)}s"
        )
        return EngagementEligibility(
            allowed=True,
            reason_code="OK",
            reason_text="资格检测通过",
            new_message_count=state.message_count_window,
            silence_seconds=silence_seconds,
        )

    def plan_engagement(
        self,
        state: GroupSocialState,
        eligibility: EngagementEligibility,
        has_mention: bool = False,
        has_reply_to_bot: bool = False,
        trigger_text: str = "",
    ) -> EngagementPlan:
        scene = self.classify_scene_from_state(state)
        confidence = min(eligibility.silence_seconds / 120.0, 1.0) * 0.5 + 0.5

        if scene == SceneType.IDLE:
            if has_mention or has_reply_to_bot:
                plan = EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="idle场景但被明确唤醒",
                    confidence=0.7,
                    scene=scene,
                )
            else:
                plan = EngagementPlan(
                    level=EngagementLevel.IGNORE,
                    reason="idle场景且无明确唤醒",
                    confidence=0.8,
                    scene=scene,
                )
            self._debug(
                f"[Engagement] scene=idle eligible={'yes' if eligibility.allowed else 'no'} level={plan.level.value} reason={plan.reason}"
            )
            return plan

        if scene == SceneType.HELP:
            if has_mention or has_reply_to_bot:
                confidence = min(confidence + 0.2, 1.0)
                plan = EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="help场景且被明确唤醒",
                    confidence=confidence,
                    scene=scene,
                )
            else:
                plan = EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="help场景低相关",
                    confidence=0.4,
                    scene=scene,
                )
            self._debug(
                f"[Engagement] scene=help eligible={'yes' if eligibility.allowed else 'no'} level={plan.level.value} reason={plan.reason}"
            )
            return plan

        if scene == SceneType.DEBATE:
            if has_mention or has_reply_to_bot:
                confidence = min(confidence + 0.15, 1.0)
                plan = EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="debate场景但被明确唤醒",
                    confidence=confidence,
                    scene=scene,
                )
            else:
                plan = EngagementPlan(
                    level=EngagementLevel.IGNORE,
                    reason="debate场景且无唤醒",
                    confidence=0.6,
                    scene=scene,
                )
            self._debug(
                f"[Engagement] scene=debate eligible={'yes' if eligibility.allowed else 'no'} level={plan.level.value} reason={plan.reason}"
            )
            return plan

        if scene == SceneType.CASUAL:
            if has_mention or has_reply_to_bot:
                plan = EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="casual场景且被明确唤醒",
                    confidence=0.7,
                    scene=scene,
                )
            else:
                opportunity = self.recognize_opportunity(state, False, False, trigger_text)
                if opportunity.kind == OpportunityKind.EMOJI_REACT:
                    plan = EngagementPlan(
                        level=EngagementLevel.REACT,
                        reason=f"emoji参与: {opportunity.reason}",
                        confidence=opportunity.confidence,
                        scene=scene,
                        anchor_type=opportunity.anchor_type,
                        anchor_text=opportunity.anchor_text,
                    )
                elif opportunity.kind in (OpportunityKind.ACTIVE_CONTINUATION, OpportunityKind.TOPIC_HOOK):
                    plan = EngagementPlan(
                        level=EngagementLevel.FULL,
                        reason=f"主动文本: {opportunity.reason}",
                        confidence=opportunity.confidence,
                        scene=scene,
                        anchor_type=opportunity.anchor_type,
                        anchor_text=opportunity.anchor_text,
                    )
                else:
                    plan = EngagementPlan(
                        level=EngagementLevel.IGNORE,
                        reason=f"无锚点不参与: {opportunity.reason}",
                        confidence=0.7,
                        scene=scene,
                    )
            self._debug(
                f"[Engagement] scene=casual eligible={'yes' if eligibility.allowed else 'no'} level={plan.level.value} reason={plan.reason}"
            )
            return plan

        plan = EngagementPlan(
            level=EngagementLevel.IGNORE,
            reason="默认忽略",
            confidence=1.0,
            scene=scene,
        )
        self._debug(f"[Engagement] scene=unknown level=IGNORE reason=default")
        return plan

    def classify_scene_from_state(self, state: GroupSocialState) -> SceneType:
        if state.emotion_count_window >= 4:
            return SceneType.DEBATE
        if state.question_count_window >= 3:
            return SceneType.HELP
        if state.message_count_window < 3 and state.last_message_time > 0:
            return SceneType.IDLE
        return SceneType.CASUAL

    def _high_relevance_check(self, state: GroupSocialState) -> bool:
        return state.mention_bot_recently

    def recognize_opportunity(
        self,
        state: GroupSocialState,
        has_mention: bool = False,
        has_reply_to_bot: bool = False,
        trigger_text: str = "",
    ) -> SpeechOpportunity:
        """识别当前是否存在说话机会。

        主动文本发言必须有锚点，没有锚点时只能 IGNORE 或 EMOJI_REACT。
        """
        scope_id = state.scope_id
        silence_seconds = time.time() - state.last_message_time if state.last_message_time > 0 else 999

        if has_mention or has_reply_to_bot:
            anchor_type = AnchorType.REPLY_TO_BOT if has_reply_to_bot else AnchorType.MENTION
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.DIRECT_REPLY if has_reply_to_bot else OpportunityKind.MENTION_REPLY,
                anchor_type=anchor_type,
                confidence=0.9,
                reason=f"被明确唤醒（{anchor_type.value}）",
                anchor_text=trigger_text,
            )

        if self._is_question_unanswered(trigger_text, state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.ACTIVE_CONTINUATION,
                anchor_type=AnchorType.QUESTION_UNANSWERED,
                confidence=0.6,
                reason="检测到未回答的问题",
                anchor_text=trigger_text,
            )

        if self._is_persona_hook(trigger_text):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.TOPIC_HOOK,
                anchor_type=AnchorType.PERSONA_HOOK,
                confidence=0.7,
                reason="话题触发角色关注点",
                anchor_text=trigger_text,
            )

        if self._is_memorable_hook(trigger_text, state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.TOPIC_HOOK,
                anchor_type=AnchorType.MEMORABLE_HOOK,
                confidence=0.6,
                reason="触发值得接梗的内容",
                anchor_text=trigger_text,
            )

        if self._is_natural_landing(state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.ACTIVE_CONTINUATION,
                anchor_type=AnchorType.NATURAL_LANDING,
                confidence=0.5,
                reason="存在自然落点",
                anchor_text="",
            )

        if state.emotion_count_window >= 2:
            return SpeechOpportunity.emoji_react(
                scope_id,
                reason="情绪活跃，可发表情包",
                confidence=0.4,
            )

        return SpeechOpportunity.ignore(scope_id, reason="无锚点，不主动插嘴")

    def _is_question_unanswered(self, text: str, state: GroupSocialState) -> bool:
        if not text:
            return False
        for qw in QUESTION_WORDS:
            if qw in text:
                return True
        return False

    def _is_persona_hook(self, text: str) -> bool:
        if not text:
            return False
        persona_hooks = getattr(self.cfg, "persona_trigger_keywords", [])
        if not persona_hooks:
            return False
        text_lower = text.lower()
        for hook in persona_hooks:
            if hook.lower() in text_lower:
                return True
        return False

    def _is_memorable_hook(self, text: str, state: GroupSocialState) -> bool:
        if not text:
            return False
        memorable_keywords = {"笑死", "笑死我了", "哈哈", "笑死我了", "卧槽", "牛", "厉害", "离谱", "绝了"}
        text_lower = text.lower()
        for kw in memorable_keywords:
            if kw in text_lower:
                return True
        return False

    def _is_natural_landing(self, state: GroupSocialState) -> bool:
        if state.scene in (SceneType.HELP, SceneType.DEBATE):
            if state.message_count_window >= 5:
                return True
        if state.scene == SceneType.CASUAL:
            if 2 <= state.message_count_window <= 4:
                return True
        return False
