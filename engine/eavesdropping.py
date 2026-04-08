import re
import time
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent

from .engagement_planner import EngagementPlanner
from .engagement_stats import EngagementStats
from .opportunity_cache import ActiveMotive, MotiveType, OpportunityCache, OpportunityScore
from .output_guard import OutputGuard
from .reply_executor import ReplyExecutor
from .reply_intent import IntentSource, ReplyIntent, process_intent
from .reply_policy import ReplyPolicy
from .reply_recorder import ReplyRecorder
from .reply_state import ConversationMomentum
from .social_state import EngagementLevel, GroupSocialState, SceneType
from .event_context import extract_interaction_context


_ACTIVE_WINDOW_SECONDS = 30.0
_MESSAGE_WINDOW_SECONDS = 120.0


@dataclass
class ActiveScopeStore:
    _data: dict[str, dict[str, float]] | None = None

    def __post_init__(self):
        self._data = {}

    def record(self, scope_id: str, user_id: str, now: float | None = None):
        if now is None:
            now = time.time()
        if scope_id not in self._data:
            self._data[scope_id] = {}
        self._data[scope_id][user_id] = now + _ACTIVE_WINDOW_SECONDS

    def get_active_scopes(self) -> list[str]:
        now = time.time()
        result = []
        dead_scopes = []
        for scope_id, users in self._data.items():
            expired = [uid for uid, end in users.items() if now > end]
            for uid in expired:
                del users[uid]
            if users:
                result.append(scope_id)
            else:
                dead_scopes.append(scope_id)
        for scope_id in dead_scopes:
            del self._data[scope_id]
        return result


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._active_scopes = ActiveScopeStore()
        self._recorder = ReplyRecorder(plugin)
        self._output_guard = OutputGuard(plugin)
        self._stats = EngagementStats()
        self._opportunity_cache = OpportunityCache()

    def record_activity(self, scope_id: str, user_id: str, now: float | None = None):
        self._active_scopes.record(scope_id, user_id, now)

    def get_active_scopes(self) -> list[str]:
        return self._active_scopes.get_active_scopes()

    async def get_stats_summary(self, scope_id: str = "") -> str:
        """返回行为统计摘要。空 scope_id 返回全局所有 scope 的摘要（从 DB 枚举，重启后仍有效）。"""
        import json

        if scope_id:
            if not self._stats.is_loaded(scope_id):
                stats_json = await self.plugin.dao.get_scope_stats(scope_id)
                if stats_json:
                    try:
                        data = json.loads(stats_json)
                        # 兼容旧版扁平格式（无 lifetime/windowed 键）
                        if "lifetime" in data:
                            self._stats.from_dict(scope_id, data["lifetime"])
                            self._stats.from_windowed_dict(scope_id, data.get("windowed", {}))
                        elif "active_text_count" in data:
                            self._stats.from_dict(scope_id, data)
                        else:
                            self._stats.from_dict(scope_id, data)
                    except Exception:
                        pass
            return self._stats.get_summary(scope_id)
        db_scope_ids = await self.plugin.dao.list_scope_stats_ids()
        for sid in db_scope_ids:
            if not self._stats.is_loaded(sid):
                stats_json = await self.plugin.dao.get_scope_stats(sid)
                if stats_json:
                    try:
                        data = json.loads(stats_json)
                        if "lifetime" in data:
                            self._stats.from_dict(sid, data["lifetime"])
                            self._stats.from_windowed_dict(sid, data.get("windowed", {}))
                        elif "active_text_count" in data:
                            self._stats.from_dict(sid, data)
                        else:
                            self._stats.from_dict(sid, data)
                    except Exception:
                        pass
        lines = []
        for sid in self._stats._lifetime:
            lines.append(self._stats.get_summary(sid))
        return "\n".join(lines) if lines else "[EngagementStats] 无数据"

    async def persist_stats(self, scope_id: str):
        """将指定 scope 的 lifetime + rolling_24h 统计持久化到 DB。"""
        import json

        stats_dict = self._stats.to_dict(scope_id)
        windowed_dict = self._stats.to_windowed_dict(scope_id)
        combined = {"lifetime": stats_dict, "windowed": windowed_dict}
        if stats_dict or windowed_dict:
            await self.plugin.dao.save_scope_stats(scope_id, json.dumps(combined))

    async def sync_framework_reply_state(self, scope_id: str, level: str = "full") -> bool:
        """由框架正常回复链路调用，同步社交模块冷却状态。"""
        state_dict = await self.plugin.dao.get_engagement_state(scope_id)
        if not state_dict:
            return False
        momentum = ConversationMomentum.from_dict(state_dict)
        now = time.time()
        if not momentum.is_wave_active(now, _MESSAGE_WINDOW_SECONDS):
            momentum.reset_wave(now)
        await self._recorder.record_framework_normal(scope_id, momentum, level=level)
        return True

    async def check_engagement(self, group_id: str) -> bool:
        """主动参与入口 - 定时任务调度用"""
        if group_id in self.plugin._shut_until_by_group:
            if time.time() < self.plugin._shut_until_by_group[group_id]:
                return False
        try:
            state_dict = await self.plugin.dao.get_engagement_state(group_id)
            now = time.time()
            momentum = (
                ConversationMomentum.from_dict(state_dict) if state_dict else ConversationMomentum(scope_id=group_id)
            )

            reply_received = bool(momentum.message_count_window > 0 and momentum.consecutive_bot_replies == 0)
            silence_seconds = now - momentum.last_message_time if momentum.last_message_time > 0 else 999
            await self._recorder.check_and_record_outcome(group_id, reply_received, silence_seconds)

            if not momentum.is_wave_active(now, _MESSAGE_WINDOW_SECONDS):
                momentum.reset_wave(now)

            planner = EngagementPlanner(self.plugin)

            pending = await self._opportunity_cache.peek(group_id)
            motive = None
            pending_anchor_type = ""
            pending_trigger_reason = ""
            pending_message_ids: list[str] = []
            pending_anchor_text = ""
            trigger_text = ""
            trigger_user_id = ""
            trigger_user_name = ""
            if pending:
                top = pending[0]
                if top.is_high_score():
                    motive = top.motive
                    trigger_text = top.anchor_text
                    pending_anchor_type = top.anchor_type
                    pending_trigger_reason = top.trigger_reason
                    pending_message_ids = top.message_ids
                    pending_anchor_text = top.anchor_text
                    trigger_user_id = top.trigger_user_id
                    trigger_user_name = top.trigger_user_name
                    logger.debug(
                        f"[ActiveEngagement] 使用预热机会 group={group_id} score={top.score.total:.2f} motive={motive.motive.value} reason={top.trigger_reason}"
                    )
                else:
                    pending = []
            if not pending:
                motive = await planner._compute_motive(group_id)

            thread_anchor = await planner._analyze_thread(group_id)

            intent = ReplyIntent(
                source=IntentSource.ACTIVE,
                scope_id=group_id,
                is_active_trigger=True,
                thread_anchor=thread_anchor,
                active_motive=motive,
                trigger_text=trigger_text,
                pending_anchor_type=pending_anchor_type,
                pending_anchor_text=pending_anchor_text,
                pending_trigger_reason=pending_trigger_reason,
                pending_message_ids=pending_message_ids,
                user_id=trigger_user_id,
                sender_name=trigger_user_name or "群成员",
            )
            executor = ReplyExecutor(self.plugin, planner, output_guard=self._output_guard, stats=self._stats)
            policy = ReplyPolicy(self.plugin)

            result = await process_intent(self.plugin, intent, momentum, planner, executor, policy, self._recorder)
            if result and pending:
                await self._opportunity_cache.remove_one(group_id, top)
            await self.persist_stats(group_id)
            return result
        except Exception as e:
            logger.warning(f"[ActiveEngagement] 群 {group_id} 检查失败: {e}", exc_info=True)
            return False

    async def process_passive_engagement(self, event: AstrMessageEvent):
        """被动互动唯一入口"""
        group_id = event.get_group_id()
        if not group_id:
            return

        try:
            now = time.time()
            user_id = str(event.get_user_id()) if hasattr(event, "get_user_id") else ""
            sender_name = event.get_sender_name() if hasattr(event, "get_sender_name") else "群成员"
            if user_id:
                self.record_activity(group_id, user_id, now)

            state_dict = await self.plugin.dao.get_engagement_state(group_id)
            momentum = (
                ConversationMomentum.from_dict(state_dict) if state_dict else ConversationMomentum(scope_id=group_id)
            )

            if not momentum.is_wave_active(now, _MESSAGE_WINDOW_SECONDS):
                momentum.reset_wave(now)

            momentum.user_message_arrived(now)
            if momentum.bot_has_spoken_in_current_wave:
                momentum.new_user_after_bot()

            msg_text = event.message_str or ""
            if not msg_text.strip():
                msg_text = "[图片]"

            if "?？" in msg_text or "?" in msg_text or "？" in msg_text:
                if len(msg_text.strip()) >= 4:
                    momentum.set_unanswered_question(msg_text)
            joke_starters = ["为什么", "你知道吗", "猜猜", "笑话", "讲个", "段子", "哈哈哈", "笑死我了", "笑死"]
            if any(kw in msg_text for kw in joke_starters) and len(msg_text) < 30:
                momentum.set_unfinished_joke(msg_text)

            is_at = event.get_extra("is_at", False)
            has_reply = event.get_extra("has_reply", False)
            has_mention = is_at or has_reply

            # 提取 message_id 供 emoji reaction 和 reply 引用使用
            message_id = ""
            try:
                raw_msg = getattr(getattr(event, "message_obj", None), "raw_message", None)
                if raw_msg and isinstance(raw_msg, dict):
                    message_id = str(raw_msg.get("message_id", ""))
                if not message_id:
                    message_id = str(getattr(event, "message_id", ""))
            except Exception:
                pass

            interaction = extract_interaction_context(
                event.get_messages(),
                persona_name=getattr(self.plugin, "persona_name", "黑塔"),
                bot_id=self.plugin._get_bot_id(),
            )
            quoted_info = interaction.get("quoted_info", "") or ""
            at_info = interaction.get("at_info", "") or ""

            messages_for_scene = [{"text": msg_text, "message": []}]
            planner = EngagementPlanner(self.plugin)
            executor = ReplyExecutor(self.plugin, planner, output_guard=self._output_guard, stats=self._stats)

            momentum.message_count_window = max(int(momentum.message_count_window), 0) + 1
            computed = planner.compute_scene_windows(messages_for_scene, None)
            if computed:
                momentum.question_count_window = max(int(momentum.question_count_window), 0) + computed.get(
                    "question_count_window", 0
                )
                momentum.emotion_count_window = max(int(momentum.emotion_count_window), 0) + computed.get(
                    "emotion_count_window", 0
                )

            motive = await planner._compute_motive(group_id)
            state_for_score = GroupSocialState(
                scope_id=group_id,
                last_message_time=momentum.last_message_time,
                last_bot_message_time=momentum.last_bot_message_at,
                message_count_window=momentum.message_count_window,
                question_count_window=momentum.question_count_window,
                emotion_count_window=momentum.emotion_count_window,
                consecutive_bot_replies=momentum.consecutive_bot_replies,
                last_unanswered_question=getattr(momentum, "last_unanswered_question", ""),
                last_unfinished_joke=getattr(momentum, "last_unfinished_joke", ""),
            )
            score = planner._compute_opportunity_score(state_for_score, msg_text, motive)

            did_warm = score.total >= 0.20 and not score.is_blocked
            if did_warm:
                await self._opportunity_cache.warm(
                    scope_id=group_id,
                    score=score,
                    anchor_text=msg_text,
                    anchor_type="passive_message",
                    motive=motive,
                    message_ids=[message_id] if message_id else [],
                    trigger_reason=f"被动触发了{score.total:.2f}分机会",
                    trigger_user_id=user_id,
                    trigger_user_name=sender_name,
                )
                logger.debug(
                    f"[PassiveEngagement] 预热机会 group={group_id} score={score.total:.2f} motive={motive.motive.value}"
                )

            recent_kw: set[str] = set()
            if msg_text and len(msg_text.strip()) >= 2:
                words = re.findall(r"[\u4e00-\u9fa5]{2,}", msg_text.strip())
                for word in words:
                    if len(word) >= 2:
                        recent_kw.add(word)

            intent = ReplyIntent(
                source=IntentSource.PASSIVE,
                scope_id=group_id,
                user_id=user_id,
                sender_name=sender_name,
                trigger_text=msg_text,
                quoted_info=quoted_info,
                at_info=at_info,
                has_mention=has_mention,
                has_reply_to_bot=has_reply,
                message_id=message_id,
                is_passive_trigger=False,
                active_motive=motive,
                recent_keywords=recent_kw,
            )

            policy = ReplyPolicy(self.plugin)

            executed = await process_intent(self.plugin, intent, momentum, planner, executor, policy, self._recorder)
            if did_warm:
                await self._opportunity_cache.remove_by_anchor(group_id, "passive_message", msg_text)
            await self.persist_stats(group_id)
        except Exception as e:
            logger.warning(f"[PassiveEngagement] 群 {group_id} 处理失败: {e}", exc_info=True)
