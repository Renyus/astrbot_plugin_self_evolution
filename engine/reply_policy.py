import time
from dataclasses import dataclass
from typing import Optional

from astrbot.api import logger

from .reply_state import BotMessageKind, ConversationMomentum


@dataclass
class ReplyPolicyDecision:
    allow: bool
    reason_code: str
    reason_text: str
    silence_seconds: float = 0.0


class ReplyPolicy:
    """统一仲裁"能不能说"。

    所有入口（主动/被动）在执行前都应先走 policy 判断，
    统一所有决策规则：cooldown、flood、wave 占用、sticker 冷却等。
    """

    def __init__(self, plugin):
        self.plugin = plugin

    def check(
        self,
        momentum: ConversationMomentum,
        *,
        cooldown_seconds: int = 30,
        min_new_messages: int = 1,
        require_new_user_after_bot: bool = False,
        allow_active: bool = True,
        is_direct_addressed: bool = False,
    ) -> ReplyPolicyDecision:
        """统一仲裁入口。

        Args:
            momentum: 当前 wave 状态
            cooldown_seconds: bot 冷却时间（秒）
            min_new_messages: 最小新消息条数
            require_new_user_after_bot: 是否要求"bot 发言后有新用户消息"（用于主动插话）
            allow_active: 是否允许主动插话（被动入口为 False）
            is_direct_addressed: 用户是否直接寻址（@bot、回复bot、私聊）
        """
        from datetime import datetime

        now = time.time()
        scope_id = momentum.scope_id

        # 深夜时段（23:00-06:00）：延长冷却，非直接寻址还提高消息门槛
        hour = datetime.now().hour
        if 23 <= hour or hour < 6:
            cooldown_seconds = int(cooldown_seconds * 3)
            # 直接寻址（@bot / reply bot / 私聊）不提高消息门槛，避免用户叫不醒 bot
            if not is_direct_addressed:
                min_new_messages = max(min_new_messages, 3)

        if require_new_user_after_bot and not momentum.new_user_message_after_bot:
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_NO_NEW_USER_AFTER_BOT",
                reason_text="bot 发言后无新用户消息，禁止主动插话",
            )

        if momentum.bot_has_spoken_in_current_wave:
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_WAVE_CONSUMED",
                reason_text="当前 wave 已被 bot 占住，禁止继续发言",
            )

        silence_seconds = now - momentum.last_message_time if momentum.last_message_time > 0 else 999

        if momentum.last_bot_message_at > 0:
            bot_silence = now - momentum.last_bot_message_at
            if bot_silence < cooldown_seconds:
                return ReplyPolicyDecision(
                    allow=False,
                    reason_code="E_COOLDOWN",
                    reason_text=f"Bot冷却中，还需{int(cooldown_seconds - bot_silence)}秒",
                    silence_seconds=bot_silence,
                )

        if silence_seconds < 5:
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_SILENCE",
                reason_text=f"群太活跃，{int(silence_seconds)}秒前才有消息",
                silence_seconds=silence_seconds,
            )

        if momentum.consecutive_bot_replies >= 2:
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_BOT_FLOOD",
                reason_text=f"Bot连续回复{momentum.consecutive_bot_replies}次，暂缓",
                silence_seconds=silence_seconds,
            )

        if momentum.message_count_window < min_new_messages:
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_MSG_COUNT",
                reason_text=f"消息量不足({momentum.message_count_window}/{min_new_messages})",
                silence_seconds=silence_seconds,
            )

        if not allow_active and not momentum.is_wave_active(now, 120.0):
            return ReplyPolicyDecision(
                allow=False,
                reason_code="E_WINDOW_INACTIVE",
                reason_text="窗口已失效",
                silence_seconds=silence_seconds,
            )

        return ReplyPolicyDecision(
            allow=True,
            reason_code="OK",
            reason_text="Policy 检测通过",
            silence_seconds=silence_seconds,
        )
