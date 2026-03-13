"""
会话上下文管理模块 - 滑动窗口 + 定时互动意愿
"""

from astrbot.api import logger
import asyncio
import random
import time
from functools import lru_cache


class SessionManager:
    """滑动上下文窗口管理器"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.session_buffers = {}  # {group_id: {"messages": [msg_list], "token_count": int}}
        self.processing_sessions = set()
        self._token_cache = {}  # Token 估算缓存

    @property
    def max_tokens(self):
        return self.plugin.cfg.session_max_tokens

    @property
    def whitelist(self):
        return self.plugin.cfg.session_whitelist

    @property
    def message_threshold(self):
        return self.plugin.cfg.eavesdrop_message_threshold

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量（中英文混合）- 带缓存"""
        if not text:
            return 0
        # 使用文本前100字符作为缓存键，减少内存占用
        cache_key = text[:100] if len(text) > 100 else text
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - chinese
        result = int(chinese * 0.7 + other * 0.25)
        # 缓存限制，避免无限增长
        if len(self._token_cache) < 5000:
            self._token_cache[cache_key] = result
        return result

    def add_message(self, group_id: str, sender_name: str, user_id: str, msg_text: str):
        """添加消息到滑动窗口（支持群聊和私聊）"""
        if not msg_text:
            logger.debug(f"[Session] 消息内容为空（非文字信息），跳过记录")
            return

        is_private = not group_id
        buffer_key = group_id if group_id else f"private_{user_id}"
        label = f"群 {group_id}" if group_id else f"私聊 {user_id}"

        if not buffer_key:
            logger.debug(f"[Session] 群ID和用户ID都为空，跳过记录")
            return

            logger.debug(
                f"[Session] 记录消息，{label}: {msg_text[:30] if msg_text else '(空)'}"
            )

        max_tokens = self.max_tokens
        msg = f"[{sender_name}]({user_id}): {msg_text}"
        tokens = self._estimate_tokens(msg)

        if buffer_key not in self.session_buffers:
            self.session_buffers[buffer_key] = {
                "messages": [],
                "token_count": 0,
                "last_active": time.time(),
                "eavesdrop_count": 0,
                "threshold": self.plugin.cfg.eavesdrop_message_threshold,
                "evicted_messages": [],
                "is_private": is_private,
            }
            logger.debug(f"[Session] 新建会话缓冲: {label}")

        buffer = self.session_buffers[buffer_key]
        buffer["last_active"] = time.time()

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

        logger.info(
            f"[Session] 消息已记录，{label}，当前 {len(buffer['messages'])} 条，{buffer['token_count']} tokens"
        )

    def get_context(self, group_id: str = None, user_id: str = None) -> str:
        """获取滑动窗口上下文（支持群聊和私聊）"""
        if group_id:
            buffer_key = group_id
            label = f"群 {group_id}"
        elif user_id:
            buffer_key = f"private_{user_id}"
            label = f"私聊 {user_id}"
        else:
            logger.warning(f"[Session] group_id 和 user_id 都为空，无法获取上下文")
            return ""

        logger.debug(f"[Session] 尝试获取上下文，{label}")

        if buffer_key not in self.session_buffers:
            logger.debug(
                f"[Session] {label} 无缓冲，session_buffers 包含: {list(self.session_buffers.keys())}"
            )
            return ""

        buffer = self.session_buffers[buffer_key]
        if not buffer.get("messages"):
            logger.debug(f"[Session] {label} 缓冲为空")
            return ""

        context = "\n".join(buffer["messages"])
        token_count = buffer.get("token_count", 0)
        logger.debug(
            f"[Session] 获取上下文成功，{label}，{len(buffer['messages'])} 条消息，{token_count} tokens"
        )
        return context

    async def cleanup_stale(self):
        """清理过期缓冲（不再自动存入 KB，与框架 KB 功能解耦）"""
        now = time.time()
        timeout = self.plugin.cfg.session_cleanup_timeout

        stale = []
        for gid, buffer in self.session_buffers.items():
            last_active = buffer.get("last_active", 0)
            if now - last_active > timeout:
                stale.append(gid)

        for gid in stale:
            self.session_buffers.pop(gid, None)

        if stale:
            logger.info(f"[Session] 已清理 {len(stale)} 个过期会话: {stale}")

    async def periodic_check(self):
        """定时检查是否需要互动意愿"""
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
                logger.info(f"[Session] 定时互动意愿检查触发，群 {group_id}")
                await self._trigger_interjection_via_eavesdropping(group_id)

        except Exception as e:
            logger.warning(f"[Session] 定时互动意愿检查异常: {e}")

    async def _trigger_interjection_via_eavesdropping(self, group_id: str):
        """通过 EavesdroppingEngine 触发互动意愿评估"""
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
            logger.warning(f"[Session] 通过 EavesdroppingEngine 触发互动意愿异常: {e}")

        finally:
            self.processing_sessions.discard(group_id)

    def clear(self):
        """清理所有缓冲"""
        self.session_buffers.clear()

    def reset_eavesdrop_count(self, group_id: str):
        """重置互动意愿触发计数器"""
        if group_id in self.session_buffers:
            self.session_buffers[group_id]["eavesdrop_count"] = 0

    def reset_threshold(self, group_id: str):
        """重置触发阈值为默认值"""
        if group_id in self.session_buffers:
            self.session_buffers[group_id]["threshold"] = (
                self.plugin.cfg.eavesdrop_message_threshold
            )
