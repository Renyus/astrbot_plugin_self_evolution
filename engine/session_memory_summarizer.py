import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from astrbot.api import logger

from .session_memory_store import SessionMemoryStore

SUMMARY_CHUNK_CHAR_LIMIT = 12000
SUMMARY_CHUNK_MAX_MESSAGES = 200

SESSION_MEMORY_PROMPT = """你是会话记忆分析师。请分析以下 {summary_date} 的聊天记录，提取结构化的记忆信息。

请以JSON格式输出：
{{
    "overview": "一段200-500字的总结，描述当日主要话题、氛围和重要事件",
    "key_facts": ["关键事实1", "关键事实2", "关键事实3", ...],
    "key_entities": ["重要人物或对象1", "重要人物或对象2", ...],
    "tags": ["标签1", "标签2", ...]
}}

规则：
- key_facts 至少3条，最多8条，每条不超过50字
- key_entities 列出当日活跃人物、重要讨论对象、群规、约定、项目名等
- tags 用简洁的词或短语标注主题，最多5个
- 只输出JSON，不要其他内容

消息列表：
{messages}
"""

PARTIAL_MEMORY_PROMPT = """你是会话记忆分析师。以下是 {summary_date} 的聊天记录分段（第 {index}/{total} 段）。

请提取这一段中的关键信息，输出JSON：
{{
    "overview": "这一段的主要话题（1-2句）",
    "key_facts": ["事实1", "事实2", ...],
    "key_entities": ["人物或对象1", "人物或对象2", ...],
    "tags": ["标签1", "标签2"]
}}

消息列表：
{messages}
"""

MERGE_MEMORY_PROMPT = """你是会话记忆分析师。以下是 {summary_date} 的分段记忆分析，请整合成最终的结构化JSON。

整合要求：
1. 合并重复信息
2. overview 整合成一段200-500字的完整总结
3. key_facts 控制在3-8条，每条不超过50字
4. key_entities 合并，去重
5. tags 不超过5个
6. 只输出JSON
"""


class SessionMemorySummarizer:
    def __init__(self, plugin):
        self.plugin = plugin
        self.store = SessionMemoryStore(plugin)
        self.memory_fetch_page_size = 100

    def _debug(self, msg: str):
        if getattr(self.plugin, "cfg", None) and self.plugin.cfg.memory_debug_enabled:
            logger.debug(msg)

    def _is_private_scope(self, scope_id: str) -> bool:
        return scope_id.startswith("private_")

    def _get_private_scope_user_id(self, scope_id: str) -> str:
        if self._is_private_scope(scope_id):
            return scope_id[len("private_") :]
        return ""

    def _get_daily_summary_window(self, reference_dt: datetime | None = None) -> tuple[datetime, datetime, str]:
        current_dt = reference_dt or datetime.now()
        end_dt = current_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=1)
        return start_dt, end_dt, start_dt.strftime("%Y-%m-%d")

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _extract_message_seq(self, msg: dict) -> int | None:
        for field in ("message_seq", "message_id"):
            if field in msg and msg.get(field) not in (None, ""):
                return self._safe_int(msg.get(field), default=0) or 0
        return None

    def _get_message_key(self, msg: dict) -> str:
        message_id = msg.get("message_id")
        if message_id not in (None, ""):
            return f"message_id:{message_id}"
        message_seq = self._extract_message_seq(msg)
        if message_seq is not None:
            return f"message_seq:{message_seq}"
        sender_id = msg.get("sender", {}).get("user_id", "")
        msg_time = self._safe_int(msg.get("time"), default=0)
        content = str(msg.get("message", ""))[:200]
        return f"fallback:{sender_id}:{msg_time}:{content}"

    def _get_message_sort_key(self, msg: dict) -> tuple[int, int]:
        return (
            self._safe_int(msg.get("time"), default=0),
            self._safe_int(self._extract_message_seq(msg), default=0),
        )

    async def _get_scope_history_page(self, bot, scope_id: str, count: int, cursor: int | None = None) -> list[dict]:
        kwargs = {"count": count}
        if cursor is not None:
            kwargs["message_seq"] = cursor

        if self._is_private_scope(scope_id):
            private_user_id = self._get_private_scope_user_id(scope_id)
            if not private_user_id:
                return []
            result = await bot.call_action(
                "get_friend_msg_history",
                user_id=int(private_user_id),
                **kwargs,
            )
        else:
            result = await bot.call_action(
                "get_group_msg_history",
                group_id=int(scope_id),
                **kwargs,
            )
        if not isinstance(result, dict):
            return []
        return result.get("messages", [])

    async def _fetch_scope_messages(self, scope_id: str, reference_dt: datetime | None = None) -> list[dict]:
        """通过 NapCat API 获取前一自然日的群聊/私聊消息（按时间窗口过滤）。"""
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                return []

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                return []

            bot = platform.get_client()
            if not bot:
                return []

            window_start, window_end, summary_date = self._get_daily_summary_window(reference_dt)
            start_ts = int(window_start.timestamp())
            end_ts = int(window_end.timestamp())
            page_size = max(1, self.memory_fetch_page_size)

            selected_messages = []
            seen_keys = set()
            cursor = None
            page_index = 0

            while True:
                page_index += 1
                messages = await self._get_scope_history_page(bot, scope_id, page_size, cursor=cursor)
                if not messages:
                    if page_index == 1:
                        self._debug(f"[MemorySummary] scope={scope_id} no_messages=true")
                    break

                oldest_message = messages[-1]
                oldest_time = self._safe_int(oldest_message.get("time"), default=0)
                page_hit_count = 0

                for msg in messages:
                    msg_key = self._get_message_key(msg)
                    if msg_key in seen_keys:
                        continue
                    seen_keys.add(msg_key)

                    msg_time = self._safe_int(msg.get("time"), default=0)
                    if start_ts <= msg_time < end_ts:
                        selected_messages.append(msg)
                        page_hit_count += 1

                self._debug(
                    f"[MemorySummary] scope={scope_id} page={page_index} fetched={len(messages)} hit={page_hit_count} date={summary_date}"
                )

                if oldest_time < start_ts:
                    break

                next_cursor = self._extract_message_seq(oldest_message)
                if next_cursor is None:
                    break

                if cursor is not None and next_cursor == cursor and page_hit_count == 0:
                    self._debug(f"[MemorySummary] scope={scope_id} cursor_stall=true aborting")
                    break

                cursor = next_cursor

            if not selected_messages:
                self._debug(f"[MemorySummary] scope={scope_id} date={summary_date} no_messages_for_window=true")
                return []

            selected_messages.sort(key=self._get_message_sort_key)
            self._debug(f"[MemorySummary] scope={scope_id} date={summary_date} messages={len(selected_messages)}")
            return selected_messages

        except Exception as e:
            logger.warning(f"[Memory] 获取会话消息失败: {e}")
            return []

    def _format_scope_messages(self, messages: list[dict]) -> str:
        """将消息列表格式化为文本"""
        lines = []
        for msg in messages:
            try:
                sender = msg.get("sender_nickname", msg.get("sender", {}).get("nickname", "未知"))
                time_str = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")
                text_parts = []
                for seg in msg.get("message", []):
                    if isinstance(seg, dict):
                        if seg.get("type") == "text":
                            text_parts.append(seg.get("data", {}).get("text", ""))
                text = "".join(text_parts).strip()
                if text:
                    lines.append(f"[{time_str}] {sender}: {text}")
            except Exception:
                pass
        return "\n".join(lines)

    def _split_messages_for_summary(self, messages: list[dict], summary_date: str) -> list[dict]:
        """将消息分块用于总结"""
        chunks = []
        current_chunk = []
        current_char_count = 0

        for msg in messages:
            msg_text = self._format_single_message(msg)
            msg_len = len(msg_text)

            if current_char_count + msg_len > SUMMARY_CHUNK_CHAR_LIMIT and current_chunk:
                chunks.append(
                    {
                        "index": len(chunks) + 1,
                        "total": len(chunks) + 1,
                        "date": summary_date,
                        "messages": "\n".join(current_chunk),
                    }
                )
                current_chunk = []
                current_char_count = 0

            current_chunk.append(msg_text)
            current_char_count += msg_len

        if current_chunk:
            chunks.append(
                {
                    "index": len(chunks) + 1,
                    "total": len(chunks),
                    "date": summary_date,
                    "messages": "\n".join(current_chunk),
                }
            )

        for i, chunk in enumerate(chunks):
            chunk["total"] = len(chunks)

        return chunks

    def _format_single_message(self, msg: dict) -> str:
        sender = msg.get("sender_nickname", msg.get("sender", {}).get("nickname", "未知"))
        time_str = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")
        text_parts = []
        for seg in msg.get("message", []):
            if isinstance(seg, dict) and seg.get("type") == "text":
                text_parts.append(seg.get("data", {}).get("text", ""))
        text = "".join(text_parts).strip()
        return f"[{time_str}] {sender}: {text}" if text else ""

    def _parse_json_response(self, text: str) -> dict | None:
        """从 LLM 输出中提取 JSON"""
        text = text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None

    async def _request_summary(self, prompt: str, umo: str | None) -> str:
        """调用 LLM 生成总结"""
        try:
            llm_provider = self.plugin.context.get_using_provider(umo=umo)
            if not llm_provider:
                return ""
            res = await llm_provider.text_chat(prompt=prompt, contexts=[])
            return res.completion_text.strip() if hasattr(res, "completion_text") else str(res).strip()
        except Exception as e:
            logger.warning(f"[Memory] _request_summary failed: {e}")
            return ""

    async def _summarize_scope(self, scope_id: str, reference_dt: datetime | None = None) -> str:
        """对指定 scope 执行每日总结"""
        try:
            _, _, summary_date = self._get_daily_summary_window(reference_dt)
            messages = await self._fetch_scope_messages(scope_id, reference_dt=reference_dt)
            if not messages:
                return ""

            formatted = self._format_scope_messages(messages)
            if not formatted.strip():
                return ""

            scope_umo = None
            if hasattr(self.plugin, "get_scope_umo"):
                scope_umo = self.plugin.get_scope_umo(scope_id)
            if not scope_umo and hasattr(self.plugin, "get_group_umo") and not self._is_private_scope(scope_id):
                scope_umo = self.plugin.get_group_umo(scope_id)

            chunks = self._split_messages_for_summary(messages, summary_date)

            if len(chunks) <= 1:
                prompt = SESSION_MEMORY_PROMPT.format(
                    summary_date=summary_date,
                    messages=formatted,
                )
                text = await self._request_summary(prompt, umo=scope_umo)
                if text:
                    parsed = self._parse_json_response(text)
                    if parsed:
                        return json.dumps(parsed, ensure_ascii=False)
                return text

            partial_results = []
            for chunk in chunks:
                prompt = PARTIAL_MEMORY_PROMPT.format(
                    summary_date=chunk["date"],
                    index=chunk["index"],
                    total=chunk["total"],
                    messages=chunk["messages"],
                )
                text = await self._request_summary(prompt, umo=scope_umo)
                if text:
                    parsed = self._parse_json_response(text)
                    if parsed:
                        partial_results.append(parsed)

            if not partial_results:
                prompt = SESSION_MEMORY_PROMPT.format(
                    summary_date=summary_date,
                    messages=formatted[:SUMMARY_CHUNK_CHAR_LIMIT],
                )
                text = await self._request_summary(prompt, umo=scope_umo)
                if text:
                    parsed = self._parse_json_response(text)
                    if parsed:
                        return json.dumps(parsed, ensure_ascii=False)
                return text

            merged_overview = " ".join([p.get("overview", "") for p in partial_results])
            merged_facts = []
            for p in partial_results:
                merged_facts.extend(p.get("key_facts", []))
            merged_entities = []
            for p in partial_results:
                merged_entities.extend(p.get("key_entities", []))
            merged_tags = []
            for p in partial_results:
                merged_tags.extend(p.get("tags", []))

            merged = {
                "overview": merged_overview[:500],
                "key_facts": list(dict.fromkeys(merged_facts))[:8],
                "key_entities": list(dict.fromkeys(merged_entities))[:10],
                "tags": list(dict.fromkeys(merged_tags))[:5],
            }

            merge_prompt = MERGE_MEMORY_PROMPT.format(
                summary_date=summary_date,
                partials=json.dumps(merged, ensure_ascii=False),
            )
            final_result = await self._request_summary(merge_prompt, umo=scope_umo)
            if final_result:
                try:
                    parsed = self._parse_json_response(final_result)
                    if parsed:
                        return json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    pass
                return final_result

            return json.dumps(merged, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"[Memory] _summarize_scope failed: {e}")
            return ""

    async def daily_summary(self, reference_dt: datetime | None = None) -> dict:
        """执行每日总结主流程"""
        result = {
            "success_scopes": [],
            "failed_scopes": [],
            "skipped_scopes": [],
        }

        private_scopes = []
        if hasattr(self.plugin.dao, "list_known_scopes"):
            try:
                private_scopes = await self.plugin.dao.list_known_scopes(scope_type="private")
            except Exception:
                pass

        all_scopes = list(private_scopes)

        try:
            if self.plugin.context.platform_manager.platform_insts:
                platform = self.plugin.context.platform_manager.platform_insts[0]
                if hasattr(platform, "get_client"):
                    bot = platform.get_client()
                    if bot:
                        try:
                            group_list_result = await bot.call_action("get_group_list")
                            groups_data = []
                            if isinstance(group_list_result, list):
                                groups_data = group_list_result
                            elif isinstance(group_list_result, dict):
                                groups_data = group_list_result.get("data", []) or []
                            for group in groups_data:
                                group_id = str(group.get("group_id", ""))
                                if group_id:
                                    all_scopes.append(group_id)
                        except Exception:
                            pass
        except Exception:
            pass

        all_scopes = list(dict.fromkeys(all_scopes))

        for scope_id in all_scopes:
            try:
                self._debug(f"[MemorySummary] scope={scope_id} task=starting")
                memory = await self._summarize_scope(scope_id, reference_dt)
                if not memory:
                    self._debug(f"[MemorySummary] scope={scope_id} result=skipped no_memory")
                    result["skipped_scopes"].append(scope_id)
                    continue

                now = reference_dt or datetime.now()
                end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
                start_dt = end_dt - timedelta(days=1)
                summary_date = start_dt.strftime("%Y-%m-%d")

                save_result = await self.store.save_daily_summary(scope_id, memory, summary_date)
                if "失败" in save_result or "不可用" in save_result:
                    self._debug(f"[MemorySummary] scope={scope_id} date={summary_date} saved=failed")
                    result["failed_scopes"].append(scope_id)
                else:
                    self._debug(f"[MemorySummary] scope={scope_id} date={summary_date} saved=yes")
                    result["success_scopes"].append(scope_id)

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning(f"[Memory] scope={scope_id} 总结失败: {e}")
                result["failed_scopes"].append(scope_id)

        return result
