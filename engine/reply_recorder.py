import time

from astrbot.api import logger

from .reply_state import BotMessageKind, ConversationMomentum


_MESSAGE_WINDOW_SECONDS = 120.0


class ReplyRecorder:
    """统一记录 bot 发言后的状态回写。

    所有来源（框架主回复/被动社交/主动插话/表情包）的 bot 发言成功后
    都调用同一个入口，保证状态更新逻辑完全一致。
    """

    def __init__(self, plugin):
        self.plugin = plugin

    async def record(
        self,
        scope_id: str,
        executed: bool,
        momentum: ConversationMomentum,
        *,
        level: str = None,
    ) -> None:
        """记录 bot 发言后的状态。

        调用方应先调用 bot_spoke()，再调用 record()。

        Args:
            scope_id: 群 ID
            executed: 是否成功发送
            momentum: 已经过 bot_spoke() 更新后的 momentum
            level: engagement level
        """
        now = time.time()

        if not executed:
            momentum.consecutive_bot_replies = 0

        saved = momentum.to_dict()
        if level:
            saved["last_bot_engagement_level"] = level
            saved["last_bot_engagement_at"] = now

        await self.plugin.dao.save_engagement_state(scope_id, saved)

    async def record_framework_normal(
        self,
        scope_id: str,
        momentum: ConversationMomentum,
        level: str = "full",
    ) -> None:
        """框架主回复（FRAMEWORK_NORMAL）专用记录入口。"""
        momentum.bot_spoke(time.time(), BotMessageKind.NORMAL, start_new_wave=False)
        await self.record(scope_id=scope_id, executed=True, momentum=momentum, level=level)
