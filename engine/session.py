"""
会话上下文管理模块 - 滑动窗口 + 定时互动意愿
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

        logger.info(
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
                "threshold": self.plugin.eavesdrop_message_threshold,
                "evicted_messages": [],
                "is_private": is_private,
            }
            logger.info(f"[Session] 新建会话缓冲: {label}")

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
            evicted = buffer.get("evicted_messages", [])
            evicted.append(old_msg)
            evicted_max = getattr(self.plugin, "session_evicted_max", 30)
            if len(evicted) > evicted_max:
                evicted.pop(0)
            buffer["evicted_messages"] = evicted
            logger.debug(
                f"[Session] 滑动窗口溢出，收集被移除消息，当前 evicted 队列: {len(evicted)} 条"
            )

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

        logger.info(f"[Session] 尝试获取上下文，{label}")

        if buffer_key not in self.session_buffers:
            logger.warning(
                f"[Session] {label} 无缓冲，session_buffers 包含: {list(self.session_buffers.keys())}"
            )
            return ""

        buffer = self.session_buffers[buffer_key]
        if not buffer.get("messages"):
            logger.warning(f"[Session] {label} 缓冲为空")
            return ""

        context = "\n".join(buffer["messages"])
        token_count = buffer.get("token_count", 0)
        logger.info(
            f"[Session] 获取上下文成功，{label}，{len(buffer['messages'])} 条消息，{token_count} tokens"
        )
        return context

    async def cleanup_stale(self):
        """清理过期缓冲"""
        now = time.time()
        timeout = getattr(self.plugin, "session_cleanup_timeout", 600)
        logger.info(
            f"[Session] cleanup_stale 检查，当前缓冲: {list(self.session_buffers.keys())}，超时时间: {timeout}秒"
        )

        stale = []
        for gid, buffer in self.session_buffers.items():
            last_active = buffer.get("last_active", 0)
            if now - last_active > timeout:
                stale.append(gid)

        for gid in stale:
            buffer = self.session_buffers.get(gid)
            if buffer:
                messages = buffer.get("messages", [])
                evicted_messages = buffer.get("evicted_messages", [])
                if messages:
                    try:
                        await self._commit_session_to_memory(
                            messages, gid, evicted_messages
                        )
                        self.session_buffers.pop(gid, None)
                    except Exception as e:
                        import traceback

                        logger.warning(
                            f"[Session] 存入失败，保留缓冲: {gid}, 异常: {e}, 堆栈: {traceback.format_exc()}"
                        )
            else:
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
                self.plugin.eavesdrop_message_threshold
            )

    async def _commit_session_to_memory(
        self, messages: list, group_id: str, evicted_messages: list = None
    ):
        """将会话内容存入知识库"""
        if evicted_messages is None:
            evicted_messages = []

        auto_commit = getattr(self.plugin, "session_auto_commit", True)
        threshold = getattr(self.plugin, "session_commit_threshold", 5)

        if not auto_commit:
            return

        total_messages = len(messages) + len(evicted_messages)
        if total_messages < threshold:
            logger.info(
                f"[Session] 消息数 {total_messages}（滑动窗口 {len(messages)} + 溢出 {len(evicted_messages)}）少于阈值 {threshold}，跳过存入"
            )
            return

        try:
            memory_kb_name = getattr(
                self.plugin, "memory_kb_name", "self_evolution_memory"
            )
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await kb_manager.get_kb_by_name(memory_kb_name)

            if not kb_helper:
                logger.warning(f"[Session] 知识库 {memory_kb_name} 不存在，跳过存入")
                return

            content = "\n".join(messages)
            formatted = f"""【群聊会话记录】
群号: {group_id}
消息数: {len(messages)}（滑动窗口）+ {len(evicted_messages)}（溢出）
---
{content}
"""

            if evicted_messages:
                evicted_text = "\n".join(evicted_messages)
                formatted += f"""
---
【滑动窗口溢出时被移除的对话】
{evicted_text}
"""

            await kb_helper.upload_document(
                file_name=f"session_{group_id}_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[formatted],
            )
            logger.info(
                f"[Session] 已将会话（含 {len(evicted_messages)} 条溢出消息）存入知识库，群 {group_id}，共 {total_messages} 条"
            )
        except Exception as e:
            logger.warning(f"[Session] 存入知识库失败: {e}")
