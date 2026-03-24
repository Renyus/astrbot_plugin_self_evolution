import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from astrbot.api.all import AstrMessageEvent

logger = logging.getLogger("astrbot")


@dataclass
class AffinitySignal:
    signal_type: str
    delta: int
    reason: str
    timestamp: float = field(default_factory=time.time)


class AffinityEngine:
    DIRECT_ENGAGEMENT = "direct_engagement"
    FRIENDLY_LANGUAGE = "friendly_language"
    HOSTILE_LANGUAGE = "hostile_language"
    RETURNING_USER = "returning_user"

    FRIENDLY_WORDS = {"谢谢", "辛苦了", "爱你", "厉害", "牛", "靠谱", "感谢", "太棒了", "真棒", "赞", "好评", "给力"}
    HOSTILE_WORDS = {"滚", "傻", "废物", "垃圾", "烦死了", "去死", "白痴", "智障", "恶心", "讨厌", "呸", "shut up"}

    def __init__(self, plugin):
        self.plugin = plugin
        self.cfg = plugin.cfg
        self.dao = plugin.dao

    def _debug(self, msg: str):
        if getattr(self.cfg, "affinity_debug_enabled", False):
            logger.debug(msg)

    def _get_param(self, name: str, default):
        return getattr(self.cfg, name, default)

    @property
    def enabled(self) -> bool:
        return self._get_param("affinity_auto_enabled", True)

    @property
    def direct_engagement_delta(self) -> int:
        return self._get_param("affinity_direct_engagement_delta", 1)

    @property
    def friendly_delta(self) -> int:
        return self._get_param("affinity_friendly_language_delta", 1)

    @property
    def hostile_delta(self) -> int:
        return self._get_param("affinity_hostile_language_delta", -2)

    @property
    def returning_user_delta(self) -> int:
        return self._get_param("affinity_returning_user_delta", 1)

    @property
    def direct_engagement_cooldown_minutes(self) -> int:
        return self._get_param("affinity_direct_engagement_cooldown_minutes", 360)

    @property
    def friendly_daily_limit(self) -> int:
        return self._get_param("affinity_friendly_daily_limit", 2)

    @property
    def hostile_cooldown_minutes(self) -> int:
        return self._get_param("affinity_hostile_cooldown_minutes", 60)

    @property
    def returning_user_daily_limit(self) -> int:
        return self._get_param("affinity_returning_user_daily_limit", 1)

    def _is_command_message(self, msg_text: str) -> bool:
        import re

        command_prefixes = {"/", "！", "!", "。", ".", "?", "？"}
        stripped = msg_text.strip()
        if not stripped:
            return True
        for prefix in command_prefixes:
            if stripped.startswith(prefix):
                rest = stripped[len(prefix) :]
                if not rest:
                    return True
                if re.match(r"^[\w\u4e00-\u9fff]", rest):
                    return True
                return False
        return False

    def _detect_friendly(self, msg_text: str) -> bool:
        return any(word in msg_text for word in self.FRIENDLY_WORDS)

    def _detect_hostile(self, msg_text: str) -> bool:
        return any(word in msg_text.lower() for word in self.HOSTILE_WORDS)

    async def process_message(self, event: AstrMessageEvent) -> list[AffinitySignal]:
        if not self.enabled:
            return []

        signals: list[AffinitySignal] = []
        user_id = str(event.get_sender_id())
        group_id = event.get_group_id()
        msg_text = event.message_str or ""

        bot_id = str(self.plugin._get_bot_id()) if hasattr(self.plugin, "_get_bot_id") else ""
        if bot_id and user_id == bot_id:
            return []

        is_at = event.get_extra("is_at", False)
        has_reply = event.get_extra("has_reply", False)

        is_command_only = self._is_command_message(msg_text)

        if is_command_only:
            return []

        if group_id:
            scope_id = str(group_id)
        else:
            scope_id = f"private_{user_id}"

        if is_at or has_reply or not group_id:
            can_direct, reason = await self.dao.can_apply_affinity_signal(
                user_id, self.DIRECT_ENGAGEMENT, self.direct_engagement_cooldown_minutes
            )
            if can_direct:
                delta = self.direct_engagement_delta
                await self.dao.record_affinity_signal(user_id, scope_id, self.DIRECT_ENGAGEMENT, delta)
                signals.append(AffinitySignal(self.DIRECT_ENGAGEMENT, delta, reason))
                self._debug(
                    f"[Affinity] user={user_id} signal={self.DIRECT_ENGAGEMENT} delta={delta} applied=yes reason={reason}"
                )
            else:
                self._debug(
                    f"[Affinity] user={user_id} signal={self.DIRECT_ENGAGEMENT} delta={self.direct_engagement_delta} applied=no reason={reason}"
                )

        if self._detect_friendly(msg_text):
            can_friendly, reason = await self.dao.can_apply_affinity_signal(
                user_id, self.FRIENDLY_LANGUAGE, 1440, daily_limit=self.friendly_daily_limit
            )
            if can_friendly:
                delta = self.friendly_delta
                await self.dao.record_affinity_signal(user_id, scope_id, self.FRIENDLY_LANGUAGE, delta)
                signals.append(AffinitySignal(self.FRIENDLY_LANGUAGE, delta, reason))
                self._debug(
                    f"[Affinity] user={user_id} signal={self.FRIENDLY_LANGUAGE} delta={delta} applied=yes reason={reason}"
                )
            else:
                self._debug(
                    f"[Affinity] user={user_id} signal={self.FRIENDLY_LANGUAGE} delta={self.friendly_delta} applied=no reason={reason}"
                )

        if self._detect_hostile(msg_text):
            can_hostile, reason = await self.dao.can_apply_affinity_signal(
                user_id, self.HOSTILE_LANGUAGE, self.hostile_cooldown_minutes
            )
            if can_hostile:
                delta = self.hostile_delta
                await self.dao.record_affinity_signal(user_id, scope_id, self.HOSTILE_LANGUAGE, delta)
                signals.append(AffinitySignal(self.HOSTILE_LANGUAGE, delta, reason))
                self._debug(
                    f"[Affinity] user={user_id} signal={self.HOSTILE_LANGUAGE} delta={delta} applied=yes reason={reason}"
                )
            else:
                self._debug(
                    f"[Affinity] user={user_id} signal={self.HOSTILE_LANGUAGE} delta={self.hostile_delta} applied=no reason={reason}"
                )

        can_returning, reason = await self.dao.can_apply_affinity_signal(
            user_id, self.RETURNING_USER, 1440, daily_limit=self.returning_user_daily_limit
        )
        if can_returning:
            was_recent = await self.dao.check_returning_user(user_id)
            if was_recent:
                delta = self.returning_user_delta
                await self.dao.record_affinity_signal(user_id, scope_id, self.RETURNING_USER, delta)
                signals.append(AffinitySignal(self.RETURNING_USER, delta, reason))
                self._debug(
                    f"[Affinity] user={user_id} signal={self.RETURNING_USER} delta={delta} applied=yes reason={reason}"
                )
            else:
                self._debug(
                    f"[Affinity] user={user_id} signal={self.RETURNING_USER} delta={self.returning_user_delta} applied=no reason=not_returning"
                )

        for sig in signals:
            await self.dao.update_affinity(user_id, sig.delta)

        return signals
