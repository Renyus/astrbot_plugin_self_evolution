"""
会话上下文管理模块 - 滑动窗口 + 定时插话
"""

from astrbot.api import logger
import asyncio
import random
import time


class SessionManager:
    """滑动上下文窗口管理器"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.session_buffers = {}  # {group_id: {"messages": [msg_list], "token_count": int}}
        self.processing_sessions = set()
        self._last_cleanup = 0

    @property
    def max_tokens(self):
        return getattr(self.plugin, "session_max_tokens", 4000)

    @property
    def whitelist(self):
        return getattr(self.plugin, "session_whitelist", [])

    @property
    def message_threshold(self):
        return getattr(self.plugin, "eavesdrop_message_threshold", 20)

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量（中英文混合）"""
        if not text:
            return 0
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - chinese
        return int(chinese * 0.7 + other * 0.25)

    def add_message(self, group_id: str, sender_name: str, user_id: str, msg_text: str):
        """添加消息到滑动窗口"""
        if not msg_text or not group_id:
            return

        logger.debug(f"[Session] 收到消息，群 {group_id}: {msg_text[:30]}")

        max_tokens = self.max_tokens
        msg = f"[{sender_name}]({user_id}): {msg_text}"
        tokens = self._estimate_tokens(msg)

        if group_id not in self.session_buffers:
            self.session_buffers[group_id] = {"messages": [], "token_count": 0}
            logger.debug(f"[Session] 新建会话缓冲: {group_id}")

        buffer = self.session_buffers[group_id]

        if tokens > max_tokens:
            msg = msg[: max_tokens * 2] + "...(截断)"
            tokens = self._estimate_tokens(msg)

        buffer["messages"].append(msg)
        buffer["token_count"] += tokens

        while buffer["token_count"] > max_tokens and buffer["messages"]:
            old_msg = buffer["messages"].pop(0)
            buffer["token_count"] -= self._estimate_tokens(old_msg)

        if buffer["token_count"] < 0:
            buffer["token_count"] = 0

        logger.debug(
            f"[Session] 消息已记录，群 {group_id}，当前 {len(buffer['messages'])} 条，{buffer['token_count']} tokens"
        )

    def get_context(self, group_id: str) -> str:
        """获取滑动窗口上下文"""
        if group_id not in self.session_buffers:
            logger.debug(f"[Session] 获取上下文失败，群 {group_id} 无缓冲")
            return ""

        buffer = self.session_buffers[group_id]
        if not buffer.get("messages"):
            return ""

        context = "\n".join(buffer["messages"])
        logger.debug(
            f"[Session] 获取上下文，群 {group_id}，{len(buffer['messages'])} 条消息"
        )
        return context

    def cleanup_stale(self):
        """清理过期缓冲"""
        now = time.time()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now

        stale = [
            gid for gid in self.session_buffers if gid not in self.processing_sessions
        ]
        for gid in stale:
            del self.session_buffers[gid]

        if stale:
            logger.debug(f"[Session] 已清理 {len(stale)} 个过期会话")

    async def periodic_check(self):
        """定时检查是否需要插话"""
        try:
            if not self.session_buffers:
                return

            threshold = self.message_threshold
            whitelist = self.whitelist

            candidates = []
            for group_id, buffer in self.session_buffers.items():
                if not isinstance(buffer, dict):
                    continue
                msg_count = len(buffer.get("messages", []))
                if msg_count < threshold:
                    continue
                if whitelist and group_id not in whitelist:
                    continue
                if group_id in self.processing_sessions:
                    continue
                candidates.append(group_id)

            if not candidates:
                return

            target_groups = random.sample(candidates, min(2, len(candidates)))

            for group_id in target_groups:
                logger.info(f"[Session] 定时插话检查触发，群 {group_id}")
                await self._trigger_interjection_via_eavesdropping(group_id)

        except Exception as e:
            logger.warning(f"[Session] 定时插话检查异常: {e}")

    async def _trigger_interjection_via_eavesdropping(self, group_id: str):
        """通过 EavesdroppingEngine 触发插话评估"""
        self.processing_sessions.add(group_id)

        try:
            eavesdropping = getattr(self.plugin, "eavesdropping", None)
            if not eavesdropping:
                logger.warning("[Session] EavesdroppingEngine 未初始化")
                return

            context = self.get_context(group_id)
            if not context:
                return

            class DummyEvent:
                def __init__(self, gid):
                    self.session_id = gid
                    self._group_id = gid
                    self.message_str = ""
                    self.is_at_or_wake_command = False

                def get_group_id(self):
                    return self._group_id

                def get_sender_id(self):
                    return "periodic_check"

                def get_sender_name(self):
                    return "System"

            dummy_event = DummyEvent(group_id)

            async for _ in eavesdropping._evaluate_interjection(
                dummy_event, group_id, force_immediate=True
            ):
                pass

        except Exception as e:
            logger.warning(f"[Session] 通过 EavesdroppingEngine 触发插话异常: {e}")

        finally:
            self.processing_sessions.discard(group_id)

    def clear(self):
        """清理所有缓冲"""
        self.session_buffers.clear()
