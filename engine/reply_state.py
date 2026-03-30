from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BotMessageKind(Enum):
    NORMAL = "normal"
    PASSIVE = "passive"
    ACTIVE = "active"
    STICKER = "sticker"


@dataclass
class ConversationMomentum:
    """统一群聊节奏状态。

    只负责状态，不负责决策。记录：
    - 群最近发生了什么（用户消息时间戳）
    - bot 最近说了什么（发言时间、类型）
    - 当前 wave 是否被 bot 占住（bot_has_spoken_in_current_wave）
    - 用户在 bot 发言后是否有新消息（new_user_message_after_bot）
    """

    scope_id: str

    last_message_time: float = 0.0
    last_bot_message_at: float = 0.0
    last_bot_message_kind: BotMessageKind = BotMessageKind.NORMAL

    scene_type: str = "casual"

    message_count_window: int = 0
    question_count_window: int = 0
    emotion_count_window: int = 0

    wave_started_at: float = 0.0
    bot_has_spoken_in_current_wave: bool = False
    new_user_message_after_bot: bool = False

    consecutive_bot_replies: int = 0

    last_seen_message_seq: Optional[int] = None

    def user_message_arrived(self, now: float) -> None:
        """用户发消息：开启或续活 wave，新 turn 开始，允许 bot 重新响应。"""
        self.last_message_time = now
        self.new_user_message_after_bot = False
        self.bot_has_spoken_in_current_wave = False

    def bot_spoke(self, now: float, kind: BotMessageKind, start_new_wave: bool = False) -> None:
        """bot 发消息：占住当前 wave，并递增连发计数。"""
        self.last_bot_message_at = now
        self.last_bot_message_kind = kind
        self.bot_has_spoken_in_current_wave = True
        self.new_user_message_after_bot = False
        self.consecutive_bot_replies = self.consecutive_bot_replies + 1
        if start_new_wave:
            self.wave_started_at = now

    def new_user_after_bot(self) -> None:
        """用户消息到达时检测到 bot 之前已发言。"""
        self.new_user_message_after_bot = True

    def reset_wave(self, now: float) -> None:
        """窗口失效，开启新 wave。"""
        self.wave_started_at = now
        self.bot_has_spoken_in_current_wave = False
        self.new_user_message_after_bot = False
        self.message_count_window = 0
        self.question_count_window = 0
        self.emotion_count_window = 0
        self.scene_type = "casual"
        self.consecutive_bot_replies = 0

    def is_wave_active(self, now: float, window_seconds: float = 120.0) -> bool:
        return self.last_message_time > 0 and (now - self.last_message_time) <= window_seconds

    def to_dict(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "last_message_time": self.last_message_time,
            "last_bot_message_at": self.last_bot_message_at,
            "last_bot_message_kind": self.last_bot_message_kind.value,
            "scene_type": self.scene_type,
            "message_count_window": self.message_count_window,
            "question_count_window": self.question_count_window,
            "emotion_count_window": self.emotion_count_window,
            "wave_started_at": self.wave_started_at,
            "bot_has_spoken_in_current_wave": int(self.bot_has_spoken_in_current_wave),
            "new_user_message_after_bot": int(self.new_user_message_after_bot),
            "consecutive_bot_replies": self.consecutive_bot_replies,
            "last_seen_message_seq": self.last_seen_message_seq,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationMomentum":
        m = cls(scope_id=data.get("scope_id", ""))
        m.last_message_time = float(data.get("last_message_time") or 0)
        m.last_bot_message_at = float(data.get("last_bot_message_at") or 0)
        kind_str = data.get("last_bot_message_kind", "normal")
        try:
            m.last_bot_message_kind = BotMessageKind(kind_str)
        except ValueError:
            m.last_bot_message_kind = BotMessageKind.NORMAL
        m.scene_type = data.get("scene_type", "casual")
        m.message_count_window = int(data.get("message_count_window") or 0)
        m.question_count_window = int(data.get("question_count_window") or 0)
        m.emotion_count_window = int(data.get("emotion_count_window") or 0)
        m.wave_started_at = float(data.get("wave_started_at") or 0)
        if m.wave_started_at == 0 and m.last_message_time > 0:
            m.wave_started_at = m.last_message_time
        m.bot_has_spoken_in_current_wave = bool(int(data.get("bot_has_spoken_in_current_wave") or 0))
        m.new_user_message_after_bot = bool(int(data.get("new_user_message_after_bot") or 0))
        m.consecutive_bot_replies = int(data.get("consecutive_bot_replies") or 0)
        m.last_seen_message_seq = data.get("last_seen_message_seq")
        return m
