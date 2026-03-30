import time as time_module
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from astrbot.api import logger

from .engagement_planner import EngagementPlanner
from .engagement_executor import EngagementExecutor
from .reply_policy import ReplyPolicy
from .reply_recorder import ReplyRecorder
from .reply_state import BotMessageKind, ConversationMomentum
from .social_state import EngagementLevel, GroupSocialState, SceneType


class IntentSource(Enum):
    PASSIVE = "passive"
    ACTIVE = "active"
    FRAMEWORK = "framework"


@dataclass
class ReplyIntent:
    """统一"想发言"的请求抽象。

    主动和被动不再是两套系统，只是不同来源（source）的 intent。
    """

    source: IntentSource
    scope_id: str
    user_id: str = ""
    sender_name: str = "群成员"
    trigger_text: str = ""
    quoted_info: str = ""
    at_info: str = ""
    has_mention: bool = False
    has_reply_to_bot: bool = False
    scene: str = "casual"
    reason: str = ""
    is_passive_trigger: bool = False
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = datetime.now().timestamp()


_MESSAGE_WINDOW_SECONDS = 120.0


async def process_intent(
    plugin,
    intent: ReplyIntent,
    momentum: ConversationMomentum,
    planner: EngagementPlanner,
    executor: EngagementExecutor,
    policy: ReplyPolicy,
    recorder: ReplyRecorder,
    is_active: bool = False,
    cfg=None,
) -> bool:
    """统一处理 intent：policy 检查 → eligibility → plan → execute → record。

    Returns:
        True if a reply was executed, False otherwise.
    """
    scope_id = intent.scope_id
    now = time_module.time()
    cfg = cfg or plugin.cfg

    if is_active:
        policy_decision = policy.check(
            momentum,
            cooldown_seconds=cfg.interject_cooldown,
            min_new_messages=1,
            require_new_user_after_bot=True,
            allow_active=True,
        )
    else:
        policy_decision = policy.check(
            momentum,
            cooldown_seconds=cfg.interject_cooldown,
            min_new_messages=1,
            require_new_user_after_bot=False,
            allow_active=False,
        )

    if not policy_decision.allow:
        logger.debug(
            f"[ReplyIntent] scope={scope_id} source={intent.source.value} policy=no reason={policy_decision.reason_code} {policy_decision.reason_text}"
        )
        momentum.consecutive_bot_replies = 0
        await recorder.record(scope_id, executed=False, momentum=momentum)
        return False

    state = GroupSocialState(
        scope_id=scope_id,
        last_message_time=momentum.last_message_time or (now - 60),
        last_bot_message_time=momentum.last_bot_message_at,
        last_seen_message_seq=momentum.last_seen_message_seq,
        scene=SceneType.CASUAL,
        message_count_window=momentum.message_count_window,
        question_count_window=momentum.question_count_window,
        emotion_count_window=momentum.emotion_count_window,
        consecutive_bot_replies=momentum.consecutive_bot_replies,
    )

    if intent.is_passive_trigger:
        state.message_count_window = max(int(state.message_count_window), 0) + 1
        messages_for_scene = [{"text": intent.trigger_text, "message": []}]
        computed = planner.compute_scene_windows(messages_for_scene, state)
        state.question_count_window = max(int(state.question_count_window), 0) + computed["question_count_window"]
        state.emotion_count_window = max(int(state.emotion_count_window), 0) + computed["emotion_count_window"]
        state.mention_bot_recently = computed["mention_bot_recently"]

    eligibility = planner.check_eligibility(
        state,
        cooldown_seconds=cfg.interject_cooldown,
        min_new_messages=1,
    )
    if not eligibility.allowed:
        logger.debug(
            f"[ReplyIntent] scope={scope_id} source={intent.source.value} eligible=no reason={eligibility.reason_code} {eligibility.reason_text}"
        )
        momentum.consecutive_bot_replies = 0
        await recorder.record(scope_id, executed=False, momentum=momentum)
        return False

    plan = planner.plan_engagement(
        state,
        eligibility,
        has_mention=intent.has_mention,
        has_reply_to_bot=intent.has_reply_to_bot,
    )
    if plan.level == EngagementLevel.IGNORE:
        logger.debug(
            f"[ReplyIntent] scope={scope_id} source={intent.source.value} level=IGNORE scene={plan.scene.value}"
        )
        momentum.consecutive_bot_replies = 0
        await recorder.record(scope_id, executed=False, momentum=momentum)
        return False

    logger.debug(
        f"[ReplyIntent] scope={scope_id} source={intent.source.value} level={plan.level.value} scene={plan.scene.value}"
    )

    result = await executor.execute(
        plan,
        state,
        trigger_text=intent.trigger_text,
        user_id=intent.user_id,
        sender_name=intent.sender_name,
        quoted_info=intent.quoted_info,
        at_info=intent.at_info,
    )

    momentum.message_count_window = state.message_count_window
    momentum.question_count_window = state.question_count_window
    momentum.emotion_count_window = state.emotion_count_window
    momentum.scene_type = plan.scene.value
    momentum.last_seen_message_seq = state.last_seen_message_seq

    if result.executed:
        kind = BotMessageKind.ACTIVE if (is_active and plan.level == EngagementLevel.FULL) else BotMessageKind.PASSIVE
        start_new_wave = is_active
        momentum.bot_spoke(now, kind, start_new_wave=start_new_wave)

    await recorder.record(
        scope_id,
        executed=result.executed,
        momentum=momentum,
        level=result.level.value if result.executed else None,
    )

    if result.executed:
        logger.info(
            f"[ReplyIntent] scope={scope_id} source={intent.source.value} executed {result.action} ({result.level.value})"
        )

    return result.executed
