import time
from dataclasses import dataclass
from typing import Optional

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent

from .engagement_planner import EngagementPlanner
from .engagement_executor import EngagementExecutor
from .social_state import EngagementLevel, GroupSocialState, SceneType


_ACTIVE_WINDOW_SECONDS = 30.0


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

    def record_activity(self, scope_id: str, user_id: str, now: float | None = None):
        self._active_scopes.record(scope_id, user_id, now)

    def get_active_scopes(self) -> list[str]:
        return self._active_scopes.get_active_scopes()

    async def check_engagement(self, group_id: str) -> bool:
        """主动参与入口 - 定时任务调度用"""
        try:
            state_dict = await self.plugin.dao.get_engagement_state(group_id)
            now = time.time()
            if state_dict:
                state = GroupSocialState(
                    scope_id=group_id,
                    last_message_time=state_dict.get("last_message_time") or (now - 60),
                    last_bot_message_time=float(state_dict.get("last_bot_engagement_at") or 0),
                    last_seen_message_seq=state_dict.get("last_seen_message_seq"),
                    scene=SceneType(state_dict.get("scene_type", "casual"))
                    if state_dict.get("scene_type")
                    else SceneType.CASUAL,
                    message_count_window=state_dict.get("message_count_window", 0),
                    question_count_window=state_dict.get("question_count_window", 0),
                    emotion_count_window=state_dict.get("emotion_count_window", 0),
                    consecutive_bot_replies=state_dict.get("consecutive_bot_replies", 0),
                )
            else:
                state = GroupSocialState(scope_id=group_id, last_message_time=now)

            planner = EngagementPlanner(self.plugin)
            executor = EngagementExecutor(self.plugin, planner)

            computed = planner.compute_scene_windows([], state)
            state.question_count_window = computed["question_count_window"]
            state.emotion_count_window = computed["emotion_count_window"]

            eligibility = planner.check_eligibility(
                state,
                cooldown_seconds=self.plugin.cfg.interject_cooldown,
                min_new_messages=1,
            )
            if not eligibility.allowed:
                return False

            plan = planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
            if plan.level == EngagementLevel.IGNORE:
                return False

            result = await executor.execute(plan, state)
            if result.executed:
                logger.info(f"[ActiveEngagement] 群 {group_id}: executed {result.action}")
            return True
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
            if user_id:
                self.record_activity(group_id, user_id, now)

            state_dict = await self.plugin.dao.get_engagement_state(group_id)
            if state_dict:
                state = GroupSocialState(
                    scope_id=group_id,
                    last_message_time=state_dict.get("last_message_time") or (now - 60),
                    last_bot_message_time=float(state_dict.get("last_bot_engagement_at") or 0),
                    last_seen_message_seq=state_dict.get("last_seen_message_seq"),
                    scene=SceneType(state_dict.get("scene_type", "casual"))
                    if state_dict.get("scene_type")
                    else SceneType.CASUAL,
                    message_count_window=state_dict.get("message_count_window", 0),
                    question_count_window=state_dict.get("question_count_window", 0),
                    emotion_count_window=state_dict.get("emotion_count_window", 0),
                    consecutive_bot_replies=state_dict.get("consecutive_bot_replies", 0),
                )
            else:
                state = GroupSocialState(scope_id=group_id, last_message_time=0, message_count_window=1)

            msg_text = event.message_str or ""
            if not msg_text.strip():
                msg_text = "[图片]"

            messages_for_scene = [{"text": msg_text, "message": []}]
            planner = EngagementPlanner(self.plugin)
            executor = EngagementExecutor(self.plugin, planner)

            computed = planner.compute_scene_windows(messages_for_scene, state)
            state.question_count_window = computed["question_count_window"]
            state.emotion_count_window = computed["emotion_count_window"]
            state.mention_bot_recently = computed["mention_bot_recently"]

            is_at = event.is_at_or_wake_command
            has_reply = event.get_extra("has_reply", False)
            has_mention = is_at or has_reply

            eligibility = planner.check_eligibility(
                state,
                cooldown_seconds=self.plugin.cfg.interject_cooldown,
                min_new_messages=1,
            )
            if not eligibility.allowed:
                logger.debug(
                    f"[PassiveEngagement] scope={group_id} eligible=no reason={eligibility.reason_code} {eligibility.reason_text}"
                )
                return

            plan = planner.plan_engagement(state, eligibility, has_mention=has_mention, has_reply_to_bot=has_reply)
            if plan.level == EngagementLevel.IGNORE:
                return

            logger.debug(f"[PassiveEngagement] scope={group_id} level={plan.level.value} scene={plan.scene.value}")

            result = await executor.execute(plan, state)

            new_state = {
                "last_message_time": now,
                "last_bot_engagement_at": time.time() if result.executed else state.last_bot_message_time,
                "last_bot_engagement_level": result.level.value
                if result.executed
                else (state_dict.get("last_bot_engagement_level") if state_dict else None),
                "last_seen_message_seq": state_dict.get("last_seen_message_seq") if state_dict else None,
                "scene_type": plan.scene.value,
                "message_count_window": 1,
                "question_count_window": state.question_count_window,
                "emotion_count_window": state.emotion_count_window,
                "consecutive_bot_replies": (state.consecutive_bot_replies + 1) if result.executed else 0,
            }
            await self.plugin.dao.save_engagement_state(group_id, new_state)

            if result.executed:
                logger.info(f"[PassiveEngagement] scope={group_id} executed {result.action} ({result.level.value})")

        except Exception as e:
            logger.warning(f"[PassiveEngagement] 群 {group_id} 处理失败: {e}", exc_info=True)
