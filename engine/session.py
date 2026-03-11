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

        max_tokens = self.max_tokens
        msg = f"[{sender_name}]({user_id}): {msg_text}"
        tokens = self._estimate_tokens(msg)

        if group_id not in self.session_buffers:
            self.session_buffers[group_id] = {"messages": [], "token_count": 0}

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

    def get_context(self, group_id: str) -> str:
        """获取滑动窗口上下文"""
        if group_id not in self.session_buffers:
            return ""

        buffer = self.session_buffers[group_id]
        if not buffer.get("messages"):
            return ""

        return "\n".join(buffer["messages"])

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
                await self._trigger_interjection(group_id)

        except Exception as e:
            logger.warning(f"[Session] 定时插话检查异常: {e}")

    async def _trigger_interjection(self, group_id: str):
        """触发插话评估"""
        from astrbot.api.message_components import Plain
        from astrbot.api.all import AstrMessageEvent

        self.processing_sessions.add(group_id)

        try:
            context = self.get_context(group_id)
            if not context:
                return

            llm_provider = getattr(self.plugin, "context", None)
            if not llm_provider:
                llm_provider = llm_provider.get_using_provider("qq")

            if not llm_provider:
                logger.warning("[Session] 无法获取 LLM 提供者")
                return

            system_prompt = getattr(self.plugin, "prompt_eavesdrop_system", "")
            if not system_prompt:
                system_prompt = (
                    "你是黑塔，一个理性的天才。你可以根据群聊上下文决定是否插话。"
                )

            prompt = f"{system_prompt}\n\n【群聊最近对话】\n{context}\n\n请判断是否需要插话，格式：\n- [IGNORE] 不插话\n- [COMMENT] + 内容：插话并给出评论"

            response = await llm_provider.text_chat(
                prompt=prompt, system_prompt="", conversation=None
            )

            response_text = response.completion_text if response else ""

            if not response_text:
                return

            response_text = response_text.strip()

            if response_text.startswith("[IGNORE]"):
                logger.info(f"[Session] 评估决定不插话: {response_text}")
                return

            if response_text.startswith("[COMMENT]"):
                comment = response_text[9:].strip()
                if comment:
                    logger.info(f"[Session] 触发插话: {comment}")
                    # TODO: 发送消息到群聊
                    # 需要通过 event 发送，这里暂时无法直接发送

        except Exception as e:
            logger.warning(f"[Session] 插话评估异常: {e}")

        finally:
            self.processing_sessions.discard(group_id)

    def clear(self):
        """清理所有缓冲"""
        self.session_buffers.clear()
