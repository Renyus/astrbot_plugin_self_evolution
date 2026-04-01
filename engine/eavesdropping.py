import time
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent

from .engagement_planner import EngagementPlanner
from .engagement_stats import EngagementStats
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
        for scope_id, users in self._data.items():
            expired = [uid for uid, end in users.items() if now > end]
            for uid in expired:
                del users[uid]
            if users:
                result.append(scope_id)
            else:
                del self._data[scope_id]
        return result


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._active_scopes = ActiveScopeStore()
        self._recorder = ReplyRecorder(plugin)
        self._output_guard = OutputGuard(plugin)
        self._stats = EngagementStats()

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

            if not momentum.is_wave_active(now, _MESSAGE_WINDOW_SECONDS):
                momentum.reset_wave(now)

            planner = EngagementPlanner(self.plugin)
            thread_anchor = await planner._analyze_thread(group_id)

            intent = ReplyIntent(
                source=IntentSource.ACTIVE,
                scope_id=group_id,
                is_active_trigger=True,
                thread_anchor=thread_anchor,
            )
            executor = ReplyExecutor(self.plugin, planner, output_guard=self._output_guard, stats=self._stats)
            policy = ReplyPolicy(self.plugin)

            result = await process_intent(self.plugin, intent, momentum, planner, executor, policy, self._recorder)
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

            is_at = event.get_extra("is_at", False)
            has_reply = event.get_extra("has_reply", False)
            has_mention = is_at or has_reply

            msg_text = event.message_str or ""
            if not msg_text.strip():
                msg_text = "[图片]"

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
            )

            policy = ReplyPolicy(self.plugin)

            await process_intent(self.plugin, intent, momentum, planner, executor, policy, self._recorder)
            await self.persist_stats(group_id)
        except Exception as e:
            logger.warning(f"[PassiveEngagement] 群 {group_id} 处理失败: {e}", exc_info=True)
