"""
反思模块 - 单会话内省 & 每日批处理
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("astrbot")
PRIVATE_SCOPE_PREFIX = "private_"

SESSION_REFLECTION_PROMPT = """你是一个善于自我反省的AI。请回顾以下对话，提炼出有价值的自我校准信息。

对话记录：
{conversation_history}

请分析并输出：
1. 本次对话中你的表现有哪些可以改进的地方？
2. 有哪些"明确的事实"是可以记入用户画像的？（如用户的偏好、习惯、身份信息等）
3. 有什么认知偏差需要纠正？

请以以下JSON格式输出：
{{
    "self_correction": "自我校准建议（简短）",
    "explicit_facts": ["可记入画像的明确事实1", "可记入画像的明确事实2"],
    "cognitive_bias": "需要纠正的认知偏差（如果有）"
}}"""

REPORT_CHUNK_CHAR_LIMIT = 10000
REPORT_CHUNK_MAX_MESSAGES = 120

GROUP_DAILY_REPORT_PROMPT = """你是一个聊天分析师。请分析以下 {summary_date} 的{chat_type}记录，生成一份简明的日报。

聊天记录：
{messages}

请分析并输出：
1. 今日主要话题
2. 群氛围/情绪
3. 争议点或有趣讨论
4. 活跃成员（发言数前3-5名）
5. 值得记住的重要事件

请以以下JSON格式输出：
{{
    "topic": "今日主要话题",
    "emotion": "群氛围/情绪",
    "disputes": "争议点或有趣讨论",
    "active_members": ["成员1", "成员2", "成员3"],
    "notable_events": ["重要事件1", "重要事件2"]
}}

如果聊天记录为空或不足以分析，返回：
{{
    "topic": "无",
    "emotion": "平静",
    "disputes": "无",
    "active_members": [],
    "notable_events": []
}}"""

PARTIAL_REPORT_PROMPT = """你是一个聊天分析师。以下是 {summary_date} 的{chat_type}聊天记录分段（第 {index}/{total} 段）。

请先总结这一段里最重要的信息：
1. 主要话题
2. 整体氛围/情绪
3. 争议点或有趣讨论
4. 活跃成员
5. 值得记住的重要事件

只输出简明总结文本，不要输出 JSON。

聊天记录：
{messages}
"""

MERGE_REPORT_PROMPT = """你是一个聊天分析师。以下是 {summary_date} 的{chat_type}分段总结，请整合成最终日报。

请严格输出 JSON，格式如下：
{{
    "topic": "今日主要话题",
    "emotion": "群氛围/情绪",
    "disputes": "争议点或有趣讨论",
    "active_members": ["成员1", "成员2", "成员3"],
    "notable_events": ["重要事件1", "重要事件2"]
}}

要求：
1. 合并重复信息，不要重复描述
2. 只保留当天真正重要的内容
3. 如果信息不足，返回默认空日报

分段总结：
{partial_summaries}
"""


class SessionReflection:
    """单会话反思管理器"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def generate_session_reflection(self, conversation_history: str, umo: str | None = None) -> dict:
        """
        生成单会话反思

        Args:
            conversation_history: 对话历史文本

        Returns:
            {
                "self_correction": str,
                "explicit_facts": list[str],
                "cognitive_bias": str
            }
        """
        try:
            provider = self.plugin.context.get_using_provider(umo=umo)
            if not provider:
                logger.warning("[Reflection] 无法获取LLM Provider")
                return {}

            prompt = SESSION_REFLECTION_PROMPT.format(conversation_history=conversation_history)
            res = await provider.text_chat(prompt=prompt, contexts=[])

            if not res or not res.completion_text:
                logger.warning("[Reflection] 生成反思失败：LLM响应为空")
                return {}

            import re

            reply_text = res.completion_text.strip()

            try:
                import json

                match = re.search(r"\{.*\}", reply_text, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    logger.info(f"[Reflection] 生成反思成功: {result.get('self_correction', '')[:50]}...")
                    return result
            except json.JSONDecodeError:
                logger.warning(f"[Reflection] 解析反思JSON失败: {reply_text[:100]}")

            return {}
        except Exception as e:
            logger.warning(f"[Reflection] 生成会话反思异常: {e}")
            return {}

    async def save_session_reflection(self, session_id: str, user_id: str, reflection: dict) -> bool:
        """
        保存会话反思到数据库

        Args:
            session_id: 会话ID
            user_id: 用户ID
            reflection: 反思结果 dict

        Returns:
            是否保存成功
        """
        try:
            note = reflection.get("self_correction", "")
            facts = "|".join(reflection.get("explicit_facts", []))
            bias = reflection.get("cognitive_bias", "")

            await self.plugin.dao.save_session_reflection(
                session_id=session_id, user_id=user_id, note=note, facts=facts, bias=bias
            )
            logger.debug(f"[Reflection] 会话反思已保存: session_id={session_id}, user_id={user_id}")
            return True
        except Exception as e:
            logger.warning(f"[Reflection] 保存会话反思失败: {e}")
            return False

    async def get_and_consume_session_reflection(self, session_id: str, user_id: str) -> Optional[dict]:
        """
        获取并消费会话反思（一次性）

        Args:
            session_id: 会话ID
            user_id: 用户ID

        Returns:
            反思dict，如果无反思则返回None
        """
        try:
            reflection = await self.plugin.dao.get_session_reflection(session_id, user_id)
            if reflection:
                await self.plugin.dao.delete_session_reflection(session_id, user_id)
                logger.debug(f"[Reflection] 会话反思已消费: session_id={session_id}, user_id={user_id}")
            return reflection
        except Exception as e:
            logger.warning(f"[Reflection] 获取会话反思失败: {e}")
            return None

    async def distill_profile_facts(
        self,
        explicit_facts: list[str],
        user_id: str,
        group_id: str | None,
        profile_scope_id: str,
        nickname: str = "",
    ) -> int:
        """
        将 explicit_facts 蒸馏写入结构化画像或知识库（通过 router 路由）

        Args:
            explicit_facts: LLM 提取的明确事实列表
            user_id: 用户ID
            group_id: 群ID（可能是None表示私聊）
            profile_scope_id: 画像 scope ID
            nickname: 用户昵称

        Returns:
            写入的事实数量
        """
        if not explicit_facts:
            return 0

        memory_router = getattr(self.plugin, "memory_router", None)
        if not memory_router:
            logger.warning("[Reflection] MemoryRouter 未初始化，无法写入记忆")
            return 0

        scope_id = profile_scope_id or (str(group_id) if group_id else f"private_{user_id}")

        written_count = 0
        for fact in explicit_facts:
            fact = fact.strip()
            if not fact:
                continue

            result = await memory_router.write(
                content=fact,
                scope_id=scope_id,
                user_id=user_id,
                category="user_profile",
                fact_type=None,
                nickname=nickname,
                source="reflection",
            )
            if "已写入" in result or "已更新" in result:
                written_count += 1
                logger.debug(f"[Reflection] 记忆路由: {fact[:50]}... → {result}")

        if written_count > 0:
            logger.debug(f"[Reflection] 已将 {written_count}/{len(explicit_facts)} 条事实写入记忆系统")

        return written_count


class DailyBatchProcessor:
    """每日批处理管理器"""

    def __init__(self, plugin):
        self.plugin = plugin

    @staticmethod
    def _is_private_scope(scope_id: str) -> bool:
        return str(scope_id).startswith(PRIVATE_SCOPE_PREFIX)

    @staticmethod
    def _get_private_scope_user_id(scope_id: str) -> str:
        scope_id = str(scope_id or "")
        if not scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            return ""
        return scope_id[len(PRIVATE_SCOPE_PREFIX) :]

    def _get_scope_umo(self, scope_id: str) -> str | None:
        if hasattr(self.plugin, "get_scope_umo"):
            return self.plugin.get_scope_umo(scope_id)
        if hasattr(self.plugin, "get_group_umo") and not self._is_private_scope(scope_id):
            return self.plugin.get_group_umo(scope_id)
        return None

    @staticmethod
    def _normalize_reference_dt(reference_dt: datetime | None = None) -> datetime:
        local_now = datetime.now().astimezone()
        if reference_dt is None:
            return local_now
        if reference_dt.tzinfo is None:
            return reference_dt.replace(tzinfo=local_now.tzinfo)
        return reference_dt.astimezone(local_now.tzinfo)

    def _get_daily_window(self, reference_dt: datetime | None = None) -> tuple[datetime, datetime, str]:
        current_dt = self._normalize_reference_dt(reference_dt)
        end_dt = current_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=1)
        return start_dt, end_dt, start_dt.strftime("%Y-%m-%d")

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_message_seq(msg: dict) -> int | None:
        for field in ("message_seq", "message_id"):
            if msg.get(field) not in (None, ""):
                try:
                    return int(msg.get(field))
                except (TypeError, ValueError):
                    return 0
        return None

    def _get_message_key(self, msg: dict) -> str:
        message_id = msg.get("message_id")
        if message_id not in (None, ""):
            return f"message_id:{message_id}"

        message_seq = self._extract_message_seq(msg)
        if message_seq is not None:
            return f"message_seq:{message_seq}"

        sender_id = str((msg.get("sender", {}) or {}).get("user_id") or msg.get("user_id") or "")
        msg_time = self._safe_int(msg.get("time"), default=0)
        return f"fallback:{sender_id}:{msg_time}"

    def _get_message_sort_key(self, msg: dict) -> tuple[int, int]:
        return (
            self._safe_int(msg.get("time"), default=0),
            self._safe_int(self._extract_message_seq(msg), default=0),
        )

    def _get_bot_id(self) -> str:
        if hasattr(self.plugin, "_get_bot_id"):
            return str(self.plugin._get_bot_id() or "")
        return ""

    async def _fetch_scope_history_page(
        self, bot, scope_id: str, page_size: int, cursor: int | None = None
    ) -> list[dict]:
        kwargs = {"count": page_size}
        if cursor is not None:
            kwargs["message_seq"] = cursor

        if self._is_private_scope(scope_id):
            private_user_id = self._get_private_scope_user_id(scope_id)
            if not private_user_id:
                return []
            result = await bot.call_action("get_friend_msg_history", user_id=int(private_user_id), **kwargs)
        else:
            result = await bot.call_action("get_group_msg_history", group_id=int(scope_id), **kwargs)

        if isinstance(result, dict):
            return result.get("messages", []) or []
        return []

    async def _fetch_scope_messages(
        self, scope_id: str, reference_dt: datetime | None = None
    ) -> tuple[list[dict], str]:
        platform_insts = self.plugin.context.platform_manager.platform_insts
        if not platform_insts:
            return [], self._get_daily_window(reference_dt)[2]

        platform = platform_insts[0]
        if not hasattr(platform, "get_client"):
            return [], self._get_daily_window(reference_dt)[2]

        bot = platform.get_client()
        if not bot:
            return [], self._get_daily_window(reference_dt)[2]

        window_start, window_end, summary_date = self._get_daily_window(reference_dt)
        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())

        selected_messages = []
        seen_keys = set()
        cursor = None
        page_size = 100

        while True:
            messages = await self._fetch_scope_history_page(bot, scope_id, page_size, cursor=cursor)
            if not messages:
                break

            oldest_message = messages[-1]
            oldest_time = self._safe_int(oldest_message.get("time"), default=0)

            for msg in messages:
                message_key = self._get_message_key(msg)
                if message_key in seen_keys:
                    continue
                seen_keys.add(message_key)

                msg_time = self._safe_int(msg.get("time"), default=0)
                if start_ts <= msg_time < end_ts:
                    selected_messages.append(msg)

            if oldest_time < start_ts:
                break

            next_cursor = self._extract_message_seq(oldest_message)
            if next_cursor is None or next_cursor == cursor:
                break
            cursor = next_cursor

        selected_messages.sort(key=self._get_message_sort_key)
        return selected_messages, summary_date

    @staticmethod
    def _default_report() -> dict:
        return {"topic": "无", "emotion": "平静", "disputes": "无", "active_members": [], "notable_events": []}

    @staticmethod
    def _split_messages_for_report(messages: list[str]) -> list[list[str]]:
        if not messages:
            return []

        chunks = []
        current_chunk = []
        current_chars = 0

        for message in messages:
            message_chars = max(1, len(message))
            should_flush = current_chunk and (
                len(current_chunk) >= REPORT_CHUNK_MAX_MESSAGES
                or current_chars + message_chars > REPORT_CHUNK_CHAR_LIMIT
            )
            if should_flush:
                chunks.append(current_chunk)
                current_chunk = []
                current_chars = 0

            current_chunk.append(message)
            current_chars += message_chars

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @staticmethod
    def _extract_json_object(reply_text: str) -> dict:
        import json
        import re

        match = re.search(r"\{.*\}", reply_text or "", re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return {}

    async def generate_group_daily_report(
        self, group_id: str, messages: list, umo: str | None = None, summary_date: str | None = None
    ) -> dict:
        """
        生成会话日报

        Args:
            group_id: 会话范围ID
            messages: 消息列表

        Returns:
            会话日报dict
        """
        try:
            if not messages:
                return self._default_report()

            from .context_injection import parse_message_chain

            formatted = []
            for msg in messages:
                text = await parse_message_chain(msg, self.plugin)
                if text:
                    formatted.append(text)

            if not formatted:
                return self._default_report()

            provider = self.plugin.context.get_using_provider(umo=umo)
            if not provider:
                logger.warning("[Reflection] 无法获取LLM Provider")
                return {}

            chat_type = "私聊" if self._is_private_scope(group_id) else "群聊"
            summary_date = summary_date or self._get_daily_window()[2]
            chunks = self._split_messages_for_report(formatted)
            if not chunks:
                return self._default_report()

            if len(chunks) == 1:
                prompt = GROUP_DAILY_REPORT_PROMPT.format(
                    summary_date=summary_date,
                    messages="\n".join(chunks[0]),
                    chat_type=chat_type,
                )
                res = await provider.text_chat(prompt=prompt, contexts=[])
                if not res or not res.completion_text:
                    logger.warning("[Reflection] 生成会话日报失败：LLM响应为空")
                    return {}
                result = self._extract_json_object(res.completion_text.strip())
                if result:
                    logger.info(f"[Reflection] 生成会话日报成功: {group_id} - {result.get('topic', '')}")
                    return result
                return {}

            partial_summaries = []
            for index, chunk in enumerate(chunks, start=1):
                prompt = PARTIAL_REPORT_PROMPT.format(
                    summary_date=summary_date,
                    chat_type=chat_type,
                    index=index,
                    total=len(chunks),
                    messages="\n".join(chunk),
                )
                res = await provider.text_chat(prompt=prompt, contexts=[])
                if res and res.completion_text:
                    partial_summaries.append(f"第 {index} 段总结：\n{res.completion_text.strip()}")

            if not partial_summaries:
                return {}

            merge_prompt = MERGE_REPORT_PROMPT.format(
                summary_date=summary_date,
                chat_type=chat_type,
                partial_summaries="\n\n".join(partial_summaries),
            )
            res = await provider.text_chat(prompt=merge_prompt, contexts=[])
            if not res or not res.completion_text:
                logger.warning("[Reflection] 合并会话日报失败：LLM响应为空")
                return {}

            result = self._extract_json_object(res.completion_text.strip())
            if result:
                logger.info(f"[Reflection] 生成会话日报成功: {group_id} - {result.get('topic', '')}")
                return result
            return {}
        except Exception as e:
            logger.warning(f"[Reflection] 生成会话日报异常: {e}")
            return {}

    async def save_group_daily_report(self, group_id: str, report: dict, summary_date: str | None = None) -> bool:
        """保存会话日报到数据库"""
        try:
            summary_date = summary_date or self._get_daily_window()[2]
            scope_type = "私聊" if self._is_private_scope(group_id) else "群聊"
            extra_scope = (
                f"用户ID: {self._get_private_scope_user_id(group_id)}\n" if self._is_private_scope(group_id) else ""
            )
            summary = (
                f"日期: {summary_date}\n"
                f"类型: {scope_type}\n"
                f"范围ID: {group_id}\n"
                f"{extra_scope}"
                f"话题: {report.get('topic', '无')}\n"
                f"情绪: {report.get('emotion', '平静')}\n"
                f"争议: {report.get('disputes', '无')}\n"
                f"活跃成员: {', '.join(report.get('active_members', []))}\n"
                f"重要事件: {', '.join(report.get('notable_events', []))}"
            )
            await self.plugin.dao.save_group_daily_report(group_id, summary, created_at=summary_date)
            logger.debug(f"[Reflection] 会话日报已保存: group_id={group_id}")
            return True
        except Exception as e:
            logger.warning(f"[Reflection] 保存会话日报失败: {e}")
            return False

    async def process_active_user_profiles(
        self, group_id: str, messages: list, top_n: int = 10, umo: str | None = None
    ) -> int:
        """
        处理活跃用户画像

        Args:
            group_id: 群ID
            messages: 消息列表
            top_n: 只处理发言数前N的用户

        Returns:
            处理的用户数
        """
        try:
            from collections import Counter

            if self._is_private_scope(group_id):
                private_user_id = self._get_private_scope_user_id(group_id)
                bot_id = self._get_bot_id()
                top_users = [private_user_id] if private_user_id and private_user_id != bot_id else []
                logger.debug(f"[Reflection] 私聊{group_id}目标用户: {top_users}")
            else:
                bot_id = self._get_bot_id()
                user_counts = Counter()
                for msg in messages:
                    sender = msg.get("sender", {}) or {}
                    user_id = str(sender.get("user_id") or msg.get("user_id") or "")
                    if user_id and user_id != bot_id:
                        user_counts[user_id] += 1

                top_users = [uid for uid, _ in user_counts.most_common(top_n)]
                logger.debug(f"[Reflection] 群{group_id}活跃用户: {top_users}")

            profile_manager = getattr(self.plugin, "profile", None)
            if not profile_manager:
                logger.warning("[Reflection] ProfileManager未初始化")
                return 0

            processed = 0
            for user_id in top_users:
                try:
                    await profile_manager.build_profile(user_id, group_id, mode="update", force=False, umo=umo)
                    processed += 1
                except Exception as e:
                    logger.debug(f"[Reflection] 更新用户{user_id}画像失败: {e}")

            return processed
        except Exception as e:
            logger.warning(f"[Reflection] 处理活跃用户画像异常: {e}")
            return 0

    async def run_daily_batch(self, group_ids: list, reference_dt: datetime | None = None) -> dict:
        """
        执行每日批处理

        Args:
            group_ids: 目标群ID列表

        Returns:
            批处理结果统计
        """
        result = {"groups_processed": 0, "users_processed": 0, "reports_saved": 0}

        try:
            for group_id in group_ids:
                try:
                    messages, summary_date = await self._fetch_scope_messages(group_id, reference_dt=reference_dt)
                    group_umo = self._get_scope_umo(group_id)

                    if not messages:
                        continue

                    report = await self.generate_group_daily_report(
                        group_id,
                        messages,
                        umo=group_umo,
                        summary_date=summary_date,
                    )
                    if report:
                        await self.save_group_daily_report(group_id, report, summary_date=summary_date)
                        result["reports_saved"] += 1

                    users_processed = await self.process_active_user_profiles(group_id, messages, umo=group_umo)
                    result["users_processed"] += users_processed

                    result["groups_processed"] += 1

                    await asyncio.sleep(1)

                except Exception as e:
                    logger.warning(f"[Reflection] 处理群{group_id}失败: {e}")

            logger.info(f"[Reflection] 每日批处理完成: {result}")
            return result
        except Exception as e:
            logger.error(f"[Reflection] 每日批处理异常: {e}")
            return result
