import asyncio
import time
import re

from astrbot.api import logger

from .social_state import (
    EngagementEligibility,
    EngagementLevel,
    EngagementPlan,
    GroupSocialState,
    SceneType,
)
from .speech_types import AnchorType, OpportunityKind, SpeechOpportunity, ThreadAnchor


QUESTION_WORDS = {"吗", "呢", "怎么", "如何", "为什么", "啥", "什么", "是不是", "能不能", "要不要", "?", "？"}
EMOTION_WORDS = {"哈哈", "哈哈哈", "笑死", "卧槽", "牛逼", "厉害", "赞", "哭", "笑", "怒", "生气", "烦"}
DEBATE_INDICATORS = {"不对", "不是", "但是", "可是", "虽然", "然而", "错的", "胡说", "滚", "傻"}
HELP_INDICATORS = {"帮", "帮我", "请问", "请教", "求助", "救命", "急", "在线等"}

RHETORICAL_PATTERNS = [
    re.compile(r"^真的吗[？?。.]?$"),
    re.compile(r"^不是吧[。.]?$"),
    re.compile(r"^不会吧[。.]?$"),
    re.compile(r"^真的假的[？?。.]?$"),
    re.compile(r"^我去[，,]?这也能"),
    re.compile(r"^说实话[，,]?"),
    re.compile(r"^讲真[，,]?"),
    re.compile(r"^话说[，,]?"),
    re.compile(r"^话说回来"),
]
GREETING_QUESTION_PATTERNS = [
    re.compile(r"^[吃喝睡在去哪好没].{0,4}[吗呢嘛]$"),
    re.compile(r"^吃了没"),
    re.compile(r"^在吗$"),
    re.compile(r"^在不在$"),
    re.compile(r"^还好吗$"),
]
ACTION_OR_REACTION_PATTERNS = [
    re.compile(r"^[卧我噢哦诶唉呃哈嘿]+[。,]?"),
    re.compile(r"^笑死我了[。]?$"),
    re.compile(r"^[太真可]?[很就都还挺]"),
    re.compile(r"^无语[，,]?"),
    re.compile(r"^离谱[。]?$"),
    re.compile(r"^绝了[。]?$"),
    re.compile(r"^牛[。]?[年月日]?$"),
]
MEMORABLE_KEYWORDS = {
    "笑死我了",
    "笑死",
    "笑不活",
    "笑到",
    "卧槽",
    "我擦",
    "我天",
    "我去",
    "牛逼",
    "牛啊",
    "太牛了",
    "离谱",
    "太离谱",
    "真离谱",
    "绝了",
    "太绝了",
    "绝绝子",
    "芭比q",
    "完了完了",
    "救命",
    "救命啊",
    "哭死",
    "哭唧唧",
}
PERSONA_HOOK_MIN_LENGTH = 3


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

    async def plan_engagement(
        self,
        state: GroupSocialState,
        eligibility: EngagementEligibility,
        has_mention: bool = False,
        has_reply_to_bot: bool = False,
        trigger_text: str = "",
    ) -> EngagementPlan:
        scene = self.classify_scene_from_state(state)
        confidence = min(eligibility.silence_seconds / 120.0, 1.0) * 0.5 + 0.5

        plan = self._build_base_plan(scene, eligibility, confidence, has_mention, has_reply_to_bot, trigger_text, state)

        drive = await self._get_persona_drive(state.scope_id)
        plan = self._apply_persona_drive(plan, drive)

        self._debug(
            f"[Engagement] scene={scene.value} eligible={'yes' if eligibility.allowed else 'no'} level={plan.level.value} reason={plan.reason}"
        )
        return plan

    def _build_base_plan(
        self,
        scene: SceneType,
        eligibility: EngagementEligibility,
        confidence: float,
        has_mention: bool,
        has_reply_to_bot: bool,
        trigger_text: str,
        state: GroupSocialState,
    ) -> EngagementPlan:
        if scene == SceneType.IDLE:
            if has_mention or has_reply_to_bot:
                return EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="idle场景但被明确唤醒",
                    confidence=0.7,
                    scene=scene,
                )
            return EngagementPlan(
                level=EngagementLevel.IGNORE,
                reason="idle场景且无明确唤醒",
                confidence=0.8,
                scene=scene,
            )

        if scene == SceneType.HELP:
            if has_mention or has_reply_to_bot:
                confidence = min(confidence + 0.2, 1.0)
                return EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="help场景且被明确唤醒",
                    confidence=confidence,
                    scene=scene,
                )
            return EngagementPlan(
                level=EngagementLevel.FULL,
                reason="help场景低相关",
                confidence=0.4,
                scene=scene,
            )

        if scene == SceneType.DEBATE:
            if has_mention or has_reply_to_bot:
                confidence = min(confidence + 0.15, 1.0)
                return EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="debate场景但被明确唤醒",
                    confidence=confidence,
                    scene=scene,
                )
            return EngagementPlan(
                level=EngagementLevel.IGNORE,
                reason="debate场景且无唤醒",
                confidence=0.6,
                scene=scene,
            )

        if scene == SceneType.CASUAL:
            if has_mention or has_reply_to_bot:
                return EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason="casual场景且被明确唤醒",
                    confidence=0.7,
                    scene=scene,
                )
            opportunity = self.recognize_opportunity(state, False, False, trigger_text)
            if opportunity.kind == OpportunityKind.EMOJI_REACT:
                return EngagementPlan(
                    level=EngagementLevel.REACT,
                    reason=f"emoji参与: {opportunity.reason}",
                    confidence=opportunity.confidence,
                    scene=scene,
                    anchor_type=opportunity.anchor_type,
                    anchor_text=opportunity.anchor_text,
                )
            elif opportunity.kind in (OpportunityKind.ACTIVE_CONTINUATION, OpportunityKind.TOPIC_HOOK):
                return EngagementPlan(
                    level=EngagementLevel.FULL,
                    reason=f"主动文本: {opportunity.reason}",
                    confidence=opportunity.confidence,
                    scene=scene,
                    anchor_type=opportunity.anchor_type,
                    anchor_text=opportunity.anchor_text,
                )
            return EngagementPlan(
                level=EngagementLevel.IGNORE,
                reason=f"无锚点不参与: {opportunity.reason}",
                confidence=0.7,
                scene=scene,
            )

        return EngagementPlan(
            level=EngagementLevel.IGNORE,
            reason="默认忽略",
            confidence=1.0,
            scene=scene,
        )

    def classify_scene_from_state(self, state: GroupSocialState) -> SceneType:
        if state.emotion_count_window >= 4:
            return SceneType.DEBATE
        if state.question_count_window >= 3:
            return SceneType.HELP
        if state.message_count_window < 3 and state.last_message_time > 0:
            return SceneType.IDLE
        return SceneType.CASUAL

    async def _get_persona_drive(self, scope_id: str) -> dict | None:
        """读取 persona sim snapshot，返回 drive 信息用于影响 engagement plan。

        增强版：纳入 interaction semantics 和 effect 来源感。
        """
        try:
            persona_sim = getattr(self.plugin, "persona_sim", None)
            if not persona_sim:
                return None
            snapshot = await persona_sim.get_snapshot(scope_id)
            if not snapshot:
                return None
            state = snapshot.state
            active_effects = snapshot.active_effects
            effect_ids = {e.effect_id for e in active_effects}

            short_reply = 0.0
            warmth = 0.0
            initiative = 0.0
            playfulness = 0.0

            if state.energy < 40 or "low_energy" in effect_ids or "tired" in effect_ids or "sleepy" in effect_ids:
                short_reply += 0.3

            if "irritated" in effect_ids:
                warmth -= 0.3

            if "wronged" in effect_ids:
                warmth -= 0.3
                for e in active_effects:
                    if e.effect_id == "wronged" and "主动" in e.source_detail:
                        initiative -= 0.2

            if "lonely" in effect_ids:
                warmth += 0.1
                initiative += 0.3

            if "thriving" in effect_ids:
                warmth += 0.2
                initiative += 0.1
                playfulness += 0.2

            if "relieved" in effect_ids:
                warmth += 0.2
                playfulness += 0.1

            if "satisfied" in effect_ids:
                warmth += 0.2
                playfulness += 0.1

            if "curious" in effect_ids:
                playfulness += 0.1
                initiative += 0.1

            recent = snapshot.recent_events
            interaction_events = [e for e in recent if e.event_type.name == "INTERACTION"]
            if interaction_events:
                last = interaction_events[-1]
                outcome = getattr(last, "interaction_outcome", "")
                mode = getattr(last, "interaction_mode", "")
                if outcome == "missed" and mode == "active":
                    initiative -= 0.2
                    warmth -= 0.1
                elif outcome == "connected":
                    initiative += 0.1
                    warmth += 0.1

            social_todos = [t for t in snapshot.pending_todos if t.todo_type.value == "social"]
            if social_todos:
                top_todo = social_todos[0]
                if "没说完" in top_todo.title or "继续聊" in top_todo.title:
                    initiative += 0.1
                elif "想找人" in top_todo.title:
                    initiative += 0.15

            drive = {
                "energy": state.energy,
                "mood": state.mood,
                "social_need": state.social_need,
                "satiety": state.satiety,
                "effect_ids": effect_ids,
                "active_effects": active_effects,
                "short_reply_bias": short_reply,
                "warmth_bias": warmth,
                "initiative_bias": initiative,
                "playfulness_bias": playfulness,
            }
            return drive
        except Exception:
            return None

    def _apply_persona_drive(self, plan: EngagementPlan, drive: dict | None) -> EngagementPlan:
        """根据 persona drive 调整 engagement plan。"""
        if not drive:
            return plan

        energy = drive.get("energy", 80)
        mood = drive.get("mood", 70)
        social_need = drive.get("social_need", 50)
        effect_ids = drive.get("effect_ids", set())
        short_reply_bias = drive.get("short_reply_bias", 0.0)
        warmth_bias = drive.get("warmth_bias", 0.0)
        initiative_bias = drive.get("initiative_bias", 0.0)
        playfulness_bias = drive.get("playfulness_bias", 0.0)

        adjusted = plan
        confidence_adjustment = 0.0
        level_override = False

        if energy < 30:
            confidence_adjustment -= 0.15
            if plan.level == EngagementLevel.FULL and plan.scene != SceneType.HELP:
                adjusted = EngagementPlan(
                    level=EngagementLevel.REACT,
                    reason=plan.reason + f" (能量不足:{energy:.0f})",
                    confidence=max(0.3, plan.confidence - 0.15),
                    scene=plan.scene,
                    anchor_type=plan.anchor_type,
                    anchor_text=plan.anchor_text,
                    short_reply_bias=short_reply_bias,
                    warmth_bias=warmth_bias,
                    initiative_bias=initiative_bias,
                    playfulness_bias=playfulness_bias,
                )
                level_override = True

        if mood < 30 and not level_override:
            confidence_adjustment -= 0.1

        if mood < 20 and "irritated" in effect_ids:
            if plan.level == EngagementLevel.FULL and plan.scene == SceneType.DEBATE:
                adjusted = EngagementPlan(
                    level=EngagementLevel.REACT,
                    reason=plan.reason + " (情绪不佳)",
                    confidence=max(0.3, plan.confidence - 0.2),
                    scene=plan.scene,
                    anchor_type=plan.anchor_type,
                    anchor_text=plan.anchor_text,
                    short_reply_bias=short_reply_bias,
                    warmth_bias=warmth_bias,
                    initiative_bias=initiative_bias,
                    playfulness_bias=playfulness_bias,
                )
                level_override = True

        if "lonely" in effect_ids or social_need > 80:
            confidence_adjustment += 0.1
            if plan.level == EngagementLevel.IGNORE:
                adjusted = EngagementPlan(
                    level=EngagementLevel.REACT,
                    reason=plan.reason + " (渴望互动)",
                    confidence=min(0.9, plan.confidence + 0.1),
                    scene=plan.scene,
                    anchor_type=plan.anchor_type,
                    anchor_text=plan.anchor_text,
                    short_reply_bias=short_reply_bias,
                    warmth_bias=warmth_bias,
                    initiative_bias=initiative_bias,
                    playfulness_bias=playfulness_bias,
                )

        if "thriving" in effect_ids:
            confidence_adjustment += 0.05

        if not level_override and confidence_adjustment != 0:
            new_confidence = max(0.1, min(0.95, plan.confidence + confidence_adjustment))
            if new_confidence != plan.confidence or short_reply_bias != 0.0:
                adjusted = EngagementPlan(
                    level=plan.level,
                    reason=plan.reason,
                    confidence=new_confidence,
                    scene=plan.scene,
                    anchor_type=plan.anchor_type,
                    anchor_text=plan.anchor_text,
                    short_reply_bias=short_reply_bias,
                    warmth_bias=warmth_bias,
                    initiative_bias=initiative_bias,
                    playfulness_bias=playfulness_bias,
                )

        self._debug(
            f"[Engagement][PersonaDrive] energy={energy:.0f} mood={mood:.0f} social_need={social_need:.0f} "
            f"effects={list(effect_ids) if effect_ids else 'none'} "
            f"biases=[short={short_reply_bias:.2f} warm={warmth_bias:.2f} init={initiative_bias:.2f} play={playfulness_bias:.2f}] "
            f"orig_conf={plan.confidence:.2f} adj_conf={adjusted.confidence:.2f} level={plan.level.value}->{adjusted.level.value}"
        )
        return adjusted

    def recognize_opportunity(
        self,
        state: GroupSocialState,
        has_mention: bool = False,
        has_reply_to_bot: bool = False,
        trigger_text: str = "",
    ) -> SpeechOpportunity:
        """识别当前是否存在说话机会。

        主动文本发言必须有锚点，没有锚点时只能 IGNORE 或 EMOJI_REACT。
        顺序：先检查负面过滤 -> 再检查锚点 -> 最后才到 emoji/react。
        线程锚点（thread_anchor）已在 plan_engagement 中构建并传入 state。
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

        if self._is_negative_filter(trigger_text, state):
            return SpeechOpportunity.ignore(scope_id, reason="负面信号，不插嘴")

        thread_anchor = getattr(state, "thread_anchor", None)

        if self._is_question_unanswered(trigger_text, state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.ACTIVE_CONTINUATION,
                anchor_type=AnchorType.QUESTION_UNANSWERED,
                confidence=0.55,
                reason="检测到值得接话的真问题",
                anchor_text=trigger_text,
            )

        if self._is_persona_hook(trigger_text):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.TOPIC_HOOK,
                anchor_type=AnchorType.PERSONA_HOOK,
                confidence=0.65,
                reason="话题触发角色关注点",
                anchor_text=trigger_text,
            )

        if self._is_memorable_hook(trigger_text, state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.TOPIC_HOOK,
                anchor_type=AnchorType.MEMORABLE_HOOK,
                confidence=0.55,
                reason="触发值得接梗的内容",
                anchor_text=trigger_text,
            )

        if thread_anchor and thread_anchor.is_sufficient() and self._is_natural_landing(state):
            combined_confidence = min(0.4 + thread_anchor.confidence * 0.3, 0.7)
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.ACTIVE_CONTINUATION,
                anchor_type=thread_anchor.anchor_type,
                confidence=combined_confidence,
                reason=f"线程锚点支撑: {thread_anchor.anchor_text[:30]}",
                anchor_text=thread_anchor.anchor_text,
            )

        if self._is_natural_landing(state):
            return SpeechOpportunity(
                scope_id=scope_id,
                kind=OpportunityKind.ACTIVE_CONTINUATION,
                anchor_type=AnchorType.NATURAL_LANDING,
                confidence=0.4,
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

    def _is_negative_filter(self, text: str, state: GroupSocialState) -> bool:
        """负面信号：不该插嘴的情况。"""
        if not text:
            return False
        text_stripped = text.strip()

        if len(text_stripped) <= 2:
            return True

        if state.emotion_count_window >= 5 and state.message_count_window >= 8:
            return True

        if state.consecutive_bot_replies >= 2:
            return True

        emoji_dominant = self._is_emoji_heavy(text)
        if emoji_dominant and state.emotion_count_window >= 3:
            return True

        return False

    def _is_emoji_heavy(self, text: str) -> bool:
        emoji_pattern = re.compile(
            r"[\U0001F000-\U0001FFFF]" r"|[\u2600-\u26FF]" r"|[\u2700-\u27BF]" r"|[\U0001F600-\U0001F64F]"
        )
        emoji_count = len(emoji_pattern.findall(text))
        return emoji_count >= 3 and emoji_count / max(len(text), 1) > 0.2

    def _is_question_unanswered(self, text: str, state: GroupSocialState) -> bool:
        """检查是否是值得接话的真问题。"""
        if not text:
            return False
        text_stripped = text.strip()
        if len(text_stripped) < 4:
            return False
        if len(text_stripped) > 60:
            return False

        for pattern in RHETORICAL_PATTERNS:
            if pattern.match(text_stripped):
                return False

        for pattern in GREETING_QUESTION_PATTERNS:
            if pattern.match(text_stripped):
                return False

        for pattern in ACTION_OR_REACTION_PATTERNS:
            if pattern.match(text_stripped):
                return False

        if text_stripped.count("?") + text_stripped.count("？") > 2:
            return False

        question_found = False
        for qw in QUESTION_WORDS:
            if qw in text_stripped:
                question_found = True
                break
        if not question_found:
            return False

        if state.question_count_window == 0:
            return False

        return True

    def _is_persona_hook(self, text: str) -> bool:
        if not text:
            return False
        if len(text.strip()) < PERSONA_HOOK_MIN_LENGTH:
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
        text_lower = text.lower()
        text_stripped = text.strip()
        if len(text_stripped) < 3:
            return False

        matched = False
        for kw in MEMORABLE_KEYWORDS:
            if kw in text_lower:
                matched = True
                break
        if not matched:
            return False

        if state.emotion_count_window < 1:
            return False

        return True

    def _is_natural_landing(self, state: GroupSocialState) -> bool:
        if state.scene == SceneType.HELP:
            return False
        if state.scene == SceneType.DEBATE:
            return False

        if state.scene == SceneType.CASUAL:
            if 2 <= state.message_count_window <= 3:
                silence = time.time() - state.last_message_time
                if silence >= 10:
                    return True
        return False

    async def _analyze_thread(self, scope_id: str, count: int = 10) -> ThreadAnchor:
        """轻量线程分析：复用 context_injection 的缓存，
        从原始消息提取关键词集合、问句特征和 message_ids。不用 LLM。
        """
        import re

        try:
            from .context_injection import get_group_history_raw

            raw_messages = await get_group_history_raw(self.plugin, scope_id, count)
        except Exception:
            raw_messages = []

        if not raw_messages:
            return ThreadAnchor(
                anchor_type=AnchorType.NONE,
                anchor_text="",
                confidence=0.0,
                topic_keywords=set(),
            )

        # 从原始消息提取文本和 message_ids
        texts: list[str] = []
        msg_ids: list[str] = []
        for msg in raw_messages:
            mid = str(msg.get("message_id", ""))
            if mid:
                msg_ids.append(mid)
            # 拼接消息段文本
            segments = msg.get("message", [])
            parts = []
            for seg in segments:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            if parts:
                texts.append("".join(parts))

        # 过滤虚词/功能词，避免 confidence 虚高
        _STOPWORDS = {
            "什么",
            "怎么",
            "为什么",
            "如何",
            "是不是",
            "能不能",
            "要不要",
            "的是",
            "一个",
            "这个",
            "那个",
            "就是",
            "可以",
            "不是",
            "没有",
            "我们",
            "你们",
            "他们",
            "自己",
            "现在",
            "已经",
            "还是",
            "但是",
            "因为",
            "所以",
            "如果",
            "虽然",
            "而且",
            "或者",
            "不过",
            "然后",
            "知道",
            "觉得",
            "时候",
            "问题",
            "东西",
        }

        keywords: set[str] = set()
        question_in_thread = False

        topic_word_pattern = re.compile(r"[\u4e00-\u9fa5]{2,}")
        for text in texts[-6:]:
            found = topic_word_pattern.findall(text)
            for word in found:
                if word not in _STOPWORDS:
                    keywords.add(word)

        for text in texts[-4:]:
            text_lower = text.lower()
            if "?" in text_lower or "？" in text_lower or any(qw in text_lower for qw in QUESTION_WORDS):
                question_in_thread = True
                break

        confidence = 0.0
        if len(keywords) >= 3:
            confidence = min(0.3 + (len(keywords) - 3) * 0.05, 0.6)
        if question_in_thread:
            confidence = min(confidence + 0.15, 0.7)
        if len(texts) >= 3:
            confidence = min(confidence + 0.1, 0.7)

        anchor_type = AnchorType.TOPIC_CONCLUSION if confidence >= 0.5 else AnchorType.NONE

        return ThreadAnchor(
            anchor_type=anchor_type,
            anchor_text=" ".join(list(keywords)[:10]),
            confidence=confidence,
            topic_keywords=keywords,
            message_ids=msg_ids[-6:],  # 保留最近 6 条的 message_id
        )
