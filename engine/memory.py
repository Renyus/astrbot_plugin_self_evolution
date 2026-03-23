"""
每日会话总结系统 - 定时获取群聊/私聊消息，LLM 总结后存入知识库
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger("astrbot")

PRIVATE_SCOPE_PREFIX = "private_"
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

分段分析：
{partial_results}
"""


class MemoryManager:
    """每日会话总结管理器"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._kb_create_lock = asyncio.Lock()

    @property
    def memory_kb_name(self):
        return self.plugin.cfg.memory_kb_name

    @property
    def memory_fetch_page_size(self):
        return self.plugin.cfg.memory_fetch_page_size

    @property
    def memory_summary_chunk_size(self):
        return self.plugin.cfg.memory_summary_chunk_size

    async def daily_summary(self, reference_dt: datetime | None = None):
        """执行每日会话总结"""
        logger.debug("[Memory] 开始每日会话总结...")

        try:
            if not getattr(getattr(self.plugin, "cfg", None), "memory_enabled", True):
                logger.debug("[Memory] memory_enabled=False, skip daily summary")
                return
            scopes = await self._get_target_scopes()
            if not scopes:
                logger.debug("[Memory] 无目标会话，跳过总结")
                return

            for scope_id in scopes:
                await self._summarize_scope(scope_id, reference_dt=reference_dt)

            logger.debug("[Memory] 每日会话总结完成")

        except Exception as e:
            logger.error(f"[Memory] 每日会话总结异常: {e}", exc_info=True)

    @staticmethod
    def _is_private_scope(scope_id: str) -> bool:
        return str(scope_id).startswith(PRIVATE_SCOPE_PREFIX)

    @staticmethod
    def _get_private_scope_user_id(scope_id: str) -> str:
        scope_id = str(scope_id or "")
        if not scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            return ""
        return scope_id[len(PRIVATE_SCOPE_PREFIX) :]

    def _get_scope_kb_token(self, scope_id: str) -> str:
        scope_id = str(scope_id or "")
        if self._is_private_scope(scope_id):
            private_user_id = self._get_private_scope_user_id(scope_id) or "unknown"
            return f"p_{private_user_id}"
        return f"g_{scope_id}"

    def _get_scope_kb_name(self, scope_id: str) -> str:
        base_name = str(self.memory_kb_name or "self_evolution_memory")
        suffix = f"__scope__{self._get_scope_kb_token(scope_id)}"
        kb_name = f"{base_name}{suffix}"
        if len(kb_name) <= 100:
            return kb_name
        digest = hashlib.sha1(str(scope_id).encode("utf-8")).hexdigest()[:10]
        trimmed_base = base_name[: max(1, 100 - len("__scope__") - len(digest))].rstrip("_-")
        return f"{trimmed_base}__scope__{digest}"

    @staticmethod
    def _normalize_reference_dt(reference_dt: datetime | None = None) -> datetime:
        local_now = datetime.now().astimezone()
        if reference_dt is None:
            return local_now
        if reference_dt.tzinfo is None:
            return reference_dt.replace(tzinfo=local_now.tzinfo)
        return reference_dt.astimezone(local_now.tzinfo)

    def _get_daily_summary_window(self, reference_dt: datetime | None = None) -> tuple[datetime, datetime, str]:
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

        if isinstance(result, dict):
            return result.get("messages", []) or []
        return []

    async def _format_scope_messages(self, messages: list[dict]) -> list[str]:
        if not messages:
            return []

        from .context_injection import parse_message_chain

        formatted = await asyncio.gather(*[parse_message_chain(msg, self.plugin) for msg in messages])
        return [item for item in formatted if item]

    def _split_messages_for_summary(self, messages: list[str]) -> list[list[str]]:
        if not messages:
            return []

        max_messages_per_chunk = max(50, min(self.memory_summary_chunk_size, SUMMARY_CHUNK_MAX_MESSAGES))
        chunks = []
        current_chunk = []
        current_chars = 0

        for message in messages:
            message_chars = max(1, len(message))
            should_flush = current_chunk and (
                len(current_chunk) >= max_messages_per_chunk or current_chars + message_chars > SUMMARY_CHUNK_CHAR_LIMIT
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

    async def _request_summary(self, llm_provider, prompt: str) -> str | None:
        res = await llm_provider.text_chat(
            prompt=prompt,
            contexts=[],
            system_prompt="你是一个会话总结助手，只输出精简的总结文本。",
        )
        return res.completion_text.strip() if res and res.completion_text else None

    async def _ensure_scope_kb(self, scope_id: str):
        """确保某个 scope 对应的隔离知识库存在。"""
        kb_manager = getattr(self.plugin.context, "kb_manager", None)
        if not kb_manager:
            logger.warning("[Memory] 无法获取知识库管理器")
            return None

        scope_kb_name = self._get_scope_kb_name(scope_id)
        kb_helper = await kb_manager.get_kb_by_name(scope_kb_name)
        if kb_helper:
            return kb_helper

        template_helper = await kb_manager.get_kb_by_name(self.memory_kb_name)
        if not template_helper:
            logger.warning(f"[Memory] 基础知识库 {self.memory_kb_name} 不存在，无法创建 scope 知识库")
            return None

        template_kb = template_helper.kb
        if not getattr(template_kb, "embedding_provider_id", None):
            logger.warning(f"[Memory] 基础知识库 {self.memory_kb_name} 缺少 embedding_provider_id，无法复用")
            return None

        async with self._kb_create_lock:
            kb_helper = await kb_manager.get_kb_by_name(scope_kb_name)
            if kb_helper:
                return kb_helper

            logger.info(f"[Memory] 创建会话隔离知识库: {scope_kb_name}")
            return await kb_manager.create_kb(
                kb_name=scope_kb_name,
                description=f"Self-Evolution 会话总结隔离知识库 ({scope_id})",
                emoji=getattr(template_kb, "emoji", "📚"),
                embedding_provider_id=template_kb.embedding_provider_id,
                rerank_provider_id=getattr(template_kb, "rerank_provider_id", None),
                chunk_size=getattr(template_kb, "chunk_size", 512),
                chunk_overlap=getattr(template_kb, "chunk_overlap", 50),
                top_k_dense=getattr(template_kb, "top_k_dense", 50),
                top_k_sparse=getattr(template_kb, "top_k_sparse", 50),
                top_m_final=getattr(template_kb, "top_m_final", 5),
            )

    async def _resolve_active_kb_names_for_umo(self, umo: str):
        """解析当前会话正在使用的知识库列表与检索参数。"""
        from astrbot.core import sp

        kb_manager = getattr(self.plugin.context, "kb_manager", None)
        if not kb_manager:
            return [], 5, {}, "none"

        config = self.plugin.context.get_config(umo=umo) if hasattr(self.plugin.context, "get_config") else {}
        global_kb_names = list(config.get("kb_names", []) or [])
        global_top_k = config.get("kb_final_top_k", 5)
        session_config = await sp.session_get(umo, "kb_config", default={}) or {}

        if session_config.get("_self_evolution_scope_binding"):
            return global_kb_names, global_top_k, session_config, "plugin_bound"

        if "kb_ids" in session_config:
            kb_ids = session_config.get("kb_ids", []) or []
            if not kb_ids:
                return [], session_config.get("top_k", global_top_k), session_config, "session_disabled"

            kb_names = []
            for kb_id in kb_ids:
                kb_helper = await kb_manager.get_kb(kb_id)
                if kb_helper:
                    kb_names.append(kb_helper.kb.kb_name)
            return kb_names, session_config.get("top_k", global_top_k), session_config, "session_custom"

        return global_kb_names, global_top_k, session_config, "global"

    async def sync_scope_kb_binding(self, scope_id: str, umo: str | None):
        """将当前会话的知识库配置绑定到对应 scope 的隔离知识库。"""
        if not scope_id or not umo:
            return

        if not getattr(getattr(self.plugin, "cfg", None), "memory_enabled", True):
            return ""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return

            from astrbot.core import sp

            active_kb_names, top_k, session_config, source = await self._resolve_active_kb_names_for_umo(str(umo))
            base_kb_name = self.memory_kb_name
            scope_kb_name = self._get_scope_kb_name(scope_id)

            if source == "session_disabled":
                return

            if base_kb_name not in active_kb_names and scope_kb_name not in active_kb_names:
                if session_config.get("_self_evolution_scope_binding"):
                    await sp.session_remove(str(umo), "kb_config")
                return

            scope_kb_helper = await self._ensure_scope_kb(scope_id)
            if not scope_kb_helper:
                return

            desired_kb_names = []
            for kb_name in active_kb_names:
                if kb_name == base_kb_name:
                    desired_kb_names.append(scope_kb_name)
                else:
                    desired_kb_names.append(kb_name)

            if scope_kb_name not in desired_kb_names:
                desired_kb_names.append(scope_kb_name)

            deduped_names = []
            for kb_name in desired_kb_names:
                if kb_name not in deduped_names:
                    deduped_names.append(kb_name)

            desired_kb_ids = []
            for kb_name in deduped_names:
                kb_helper = await kb_manager.get_kb_by_name(kb_name)
                if kb_helper:
                    desired_kb_ids.append(kb_helper.kb.kb_id)

            new_session_config = {
                "kb_ids": desired_kb_ids,
                "top_k": top_k,
                "_self_evolution_scope_binding": True,
                "_self_evolution_memory_base": base_kb_name,
                "_self_evolution_scope_id": str(scope_id),
            }

            if session_config == new_session_config:
                return

            await sp.session_put(str(umo), "kb_config", new_session_config)
            logger.debug(f"[Memory] 已绑定会话知识库: umo={umo}, scope={scope_id}, kbs={deduped_names}")
        except Exception as e:
            logger.warning(f"[Memory] 同步会话知识库绑定失败: {e}")

    async def _get_target_scopes(self):
        """获取需要总结的会话范围列表"""
        scopes = []

        # 方式1: 白名单配置（仅约束群聊）
        whitelist = self.plugin.cfg.target_scopes
        if whitelist:
            logger.debug(f"[Memory] 使用白名单群列表: {whitelist}")
            scopes.extend(str(group_id) for group_id in whitelist)
        else:
            # 方式2: eavesdropping active_users
            if hasattr(self.plugin, "eavesdropping") and hasattr(self.plugin.eavesdropping, "active_users"):
                active_scopes = list(self.plugin.eavesdropping.active_users)
                if active_scopes:
                    logger.debug(f"[Memory] 使用 eavesdropping 活跃会话列表: {active_scopes}")
                    scopes.extend(active_scopes)
            # 方式3: 通过 platform 获取 bot 加入的群列表
            if not scopes:
                scopes.extend(await self._fetch_groups_from_platform())

        # 方式4: 通过数据库恢复历史私聊 scope，避免重启后后台任务丢失私聊目标
        dao = getattr(self.plugin, "dao", None)
        if dao and hasattr(dao, "list_known_scopes"):
            known_private_scopes = await dao.list_known_scopes(scope_type="private")
            if known_private_scopes:
                logger.debug(f"[Memory] 使用已持久化的私聊会话列表: {known_private_scopes}")
                scopes.extend(known_private_scopes)

        deduped_scopes = []
        for scope_id in scopes:
            normalized_scope_id = str(scope_id or "").strip()
            if normalized_scope_id and normalized_scope_id not in deduped_scopes:
                deduped_scopes.append(normalized_scope_id)
        return deduped_scopes

    async def _get_target_groups(self):
        """兼容旧调用，返回会话范围列表。"""
        return await self._get_target_scopes()

    async def _fetch_groups_from_platform(self):
        """从 platform 获取 bot 加入的群列表"""
        try:
            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.get_client()
            try:
                result = await bot.call_action("get_group_list")
                return self._parse_group_list(result)
            except Exception:
                return []
        except Exception as e:
            logger.debug(f"[Memory] 获取群列表失败: {e}")
            return []

    def _parse_group_list(self, result):
        """解析群列表结果"""
        if isinstance(result, list):
            groups_data = result
        elif isinstance(result, dict):
            groups_data = result.get("data", [])
        else:
            groups_data = []
        return [str(g.get("group_id", "")) for g in groups_data if g.get("group_id")]

    async def _summarize_scope(self, scope_id: str, reference_dt: datetime | None = None):
        """总结单个会话范围的消息"""
        try:
            _, _, summary_date = self._get_daily_summary_window(reference_dt)
            messages = await self._fetch_scope_messages(scope_id, reference_dt=reference_dt)
            if not messages:
                logger.debug(f"[Memory] 会话 {scope_id} 在 {summary_date} 无可总结消息")
                return

            scope_umo = self.plugin.get_scope_umo(scope_id) if hasattr(self.plugin, "get_scope_umo") else None
            if not scope_umo and hasattr(self.plugin, "get_group_umo") and not self._is_private_scope(scope_id):
                scope_umo = self.plugin.get_group_umo(scope_id)
            summary = await self._llm_summarize(messages, umo=scope_umo, summary_date=summary_date)
            if not summary:
                return

            await self._save_to_knowledge_base(scope_id, summary, summary_date=summary_date)
            logger.debug(f"[Memory] 会话 {scope_id} 在 {summary_date} 的总结已保存")

        except Exception as e:
            logger.warning(f"[Memory] 会话 {scope_id} 总结失败: {e}")

    async def _summarize_group(self, group_id: str, reference_dt: datetime | None = None):
        """兼容旧调用，按 scope 总结消息。"""
        await self._summarize_scope(group_id, reference_dt=reference_dt)

    async def _fetch_scope_messages(self, scope_id: str, reference_dt: datetime | None = None):
        """通过 NapCat API 获取前一自然日的群聊/私聊消息。"""
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
                        logger.debug(f"[Memory] 会话 {scope_id}: 无历史消息")
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

                logger.debug(
                    f"[Memory] 会话 {scope_id}: 第 {page_index} 页获取 {len(messages)} 条，命中 {page_hit_count} 条 {summary_date} 消息"
                )

                if oldest_time < start_ts:
                    break

                next_cursor = self._extract_message_seq(oldest_message)
                if next_cursor is None:
                    break

                if cursor is not None and next_cursor == cursor and page_hit_count == 0:
                    logger.debug(f"[Memory] 会话 {scope_id}: 历史游标未推进，停止继续翻页")
                    break

                cursor = next_cursor

            if not selected_messages:
                logger.debug(f"[Memory] 会话 {scope_id}: {summary_date} 无可总结消息")
                return []

            selected_messages.sort(key=self._get_message_sort_key)
            formatted = await self._format_scope_messages(selected_messages)

            if not formatted:
                logger.debug(f"[Memory] 会话 {scope_id}: {summary_date} 消息格式化为空")
                return []

            logger.debug(
                f"[Memory] 会话 {scope_id}: 获取到 {len(formatted)} 条 {summary_date} 消息并按时间顺序进行总结"
            )

            return formatted

        except Exception as e:
            logger.warning(f"[Memory] 获取会话消息失败: {e}")
            return []

    async def _fetch_group_messages(self, group_id: str):
        """兼容旧调用，按 scope 获取消息。"""
        return await self._fetch_scope_messages(group_id)

    async def _llm_summarize(
        self, messages: list, umo: str | None = None, summary_date: str | None = None
    ) -> dict | None:
        """调用 LLM 总结消息，返回结构化 dict"""
        import json
        import re

        try:
            llm_provider = self.plugin.context.get_using_provider(umo=umo)
            if not llm_provider:
                return None

            summary_date = summary_date or self._get_daily_summary_window()[2]
            chunks = self._split_messages_for_summary(messages)
            if not chunks:
                return None

            def parse_json_response(text: str) -> dict | None:
                """从 LLM 输出中提取 JSON"""
                text = text.strip()
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
                return None

            if len(chunks) == 1:
                prompt = SESSION_MEMORY_PROMPT.format(summary_date=summary_date, messages="\n".join(chunks[0]))
                text = await self._request_summary(llm_provider, prompt)
                if text:
                    result = parse_json_response(text)
                    if result:
                        return result
                return None

            partial_results = []
            for index, chunk in enumerate(chunks, start=1):
                partial_prompt = PARTIAL_MEMORY_PROMPT.format(
                    summary_date=summary_date,
                    index=index,
                    total=len(chunks),
                    messages="\n".join(chunk),
                )
                text = await self._request_summary(llm_provider, partial_prompt)
                if text:
                    result = parse_json_response(text)
                    if result:
                        partial_results.append(result)

            if not partial_results:
                return None

            merge_prompt = MERGE_MEMORY_PROMPT.format(
                summary_date=summary_date,
                partial_results="\n".join([json.dumps(r, ensure_ascii=False) for r in partial_results]),
            )
            text = await self._request_summary(llm_provider, merge_prompt)
            if text:
                result = parse_json_response(text)
                if result:
                    return result

            return None

        except Exception as e:
            logger.warning(f"[Memory] LLM 总结失败: {e}")
            return None

    async def _save_to_knowledge_base(
        self,
        scope_id: str,
        memory: dict,
        summary_date: str | None = None,
    ):
        """
        保存结构化记忆到知识库

        memory 格式:
        {
            "overview": "总摘要",
            "key_facts": ["事实1", "事实2", ...],
            "key_entities": ["实体1", "实体2", ...],
            "tags": ["标签1", "标签2", ...]
        }
        """
        try:
            kb_helper = await asyncio.wait_for(self._ensure_scope_kb(scope_id), timeout=10.0)

            if not kb_helper:
                logger.warning(f"[Memory] 会话 {scope_id} 的隔离知识库不可用")
                return

            summary_date = summary_date or self._get_daily_summary_window()[2]
            file_prefix = f"memory_{scope_id}_{summary_date}_"
            if hasattr(kb_helper, "list_documents"):
                docs = await kb_helper.list_documents()
                for doc in docs:
                    doc_id = getattr(doc, "doc_id", None)
                    doc_name = getattr(doc, "doc_name", "")
                    if doc_id and doc_name.startswith(file_prefix):
                        await kb_helper.delete_document(doc_id)

            chat_type = "私聊" if self._is_private_scope(scope_id) else "群聊"
            scope_label = (
                f"用户ID: {self._get_private_scope_user_id(scope_id)}"
                if self._is_private_scope(scope_id)
                else f"群号: {scope_id}"
            )

            key_facts = memory.get("key_facts", [])
            key_entities = memory.get("key_entities", [])
            tags = memory.get("tags", [])
            overview = memory.get("overview", "")

            chunks = []

            chunks.append(
                f"【会话记忆】\n"
                f"类型: session_memory\n"
                f"范围ID: {scope_id}\n"
                f"{scope_label}\n"
                f"日期: {summary_date}\n"
                f"标签: {', '.join(tags) if tags else '无'}"
            )

            if overview:
                chunks.append(f"【总摘要】\n{overview}")

            if key_facts:
                chunks.append("【关键事实】\n" + "\n".join(f"- {f}" for f in key_facts))

            if key_entities:
                chunks.append("【关键人物/对象】\n" + "\n".join(f"- {e}" for e in key_entities))

            await kb_helper.upload_document(
                file_name=f"{file_prefix}{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=chunks,
            )

        except Exception as e:
            logger.warning(f"[Memory] 保存总结失败: {e}")

    async def save_session_event(
        self,
        scope_id: str,
        session_event: dict,
    ) -> bool:
        """
        保存 session_event 到知识库

        session_event 格式:
        {
            "type": "session_event",
            "scope_id": str,
            "content": str,
            "user_id": str,
            "date": str,
            "source": str,
        }
        """
        try:
            kb_helper = await asyncio.wait_for(self._ensure_scope_kb(scope_id), timeout=10.0)
            if not kb_helper:
                logger.warning(f"[Memory] 会话 {scope_id} 的隔离知识库不可用")
                return False

            date = session_event.get("date", datetime.now().strftime("%Y-%m-%d"))
            file_prefix = f"event_{scope_id}_{date}_"

            scope_label = (
                f"用户ID: {self._get_private_scope_user_id(scope_id)}"
                if self._is_private_scope(scope_id)
                else f"群号: {scope_id}"
            )

            content = session_event.get("content", "")
            chunks = [
                f"【会话事件】\n"
                f"类型: session_event\n"
                f"范围ID: {scope_id}\n"
                f"{scope_label}\n"
                f"日期: {date}\n"
                f"来源: {session_event.get('source', 'unknown')}\n"
                f"内容: {content}",
            ]

            await kb_helper.upload_document(
                file_name=f"{file_prefix}{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=chunks,
            )

            logger.debug(f"[Memory] session_event 已保存: {content[:50]}...")
            return True

        except Exception as e:
            logger.warning(f"[Memory] 保存 session_event 失败: {e}")
            return False

    async def view_summary(self, group_id: str = None) -> str:
        """查看会话总结"""
        logger.debug(f"[Memory] 查看总结: {group_id}")

        if not group_id:
            return "请指定会话范围ID"

        try:
            kb_manager = self.plugin.context.kb_manager
            kb_names = []
            scope_kb_name = self._get_scope_kb_name(group_id)
            scope_kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            if scope_kb_helper:
                kb_names.append(scope_kb_name)

            base_kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0)
            if base_kb_helper:
                kb_names.append(self.memory_kb_name)

            if not kb_names:
                return f"知识库 {self.memory_kb_name} 不存在"

            results = None
            for kb_name in kb_names:
                results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=f"范围ID: {group_id}",
                        kb_names=[kb_name],
                        top_m_final=3,
                    ),
                    timeout=5.0,
                )
                if results and results.get("results"):
                    break

            if not results or not results.get("results"):
                return f"会话 {group_id} 暂无总结"

            context_text = results.get("context_text", "")
            return f"会话 {group_id} 的总结：\n\n{context_text}"

        except Exception as e:
            logger.warning(f"[Memory] 查看总结失败: {e}")
            return f"查看总结失败: {e}"

    async def clear_summary(self, group_id: str = None, confirm: bool = False) -> str:
        """清空会话总结"""
        logger.debug(f"[Memory] 清空总结: {group_id}, confirm={confirm}")

        if not group_id:
            return "请指定要清空的会话范围ID"

        if not confirm:
            return "请传入 confirm=true 确认要清空总结"

        try:
            kb_manager = self.plugin.context.kb_manager
            deleted_count = 0
            scope_kb_name = self._get_scope_kb_name(group_id)

            scope_kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            if scope_kb_helper:
                docs = await scope_kb_helper.list_documents()
                for doc in docs:
                    doc_id = getattr(doc, "doc_id", None)
                    if doc_id:
                        await scope_kb_helper.delete_document(doc_id)
                        deleted_count += 1

            base_kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0)
            if base_kb_helper:
                legacy_prefix = f"summary_{group_id}_"
                docs = await base_kb_helper.list_documents()
                for doc in docs:
                    doc_id = getattr(doc, "doc_id", None)
                    doc_name = getattr(doc, "doc_name", "")
                    if doc_id and doc_name.startswith(legacy_prefix):
                        await base_kb_helper.delete_document(doc_id)
                        deleted_count += 1

            if deleted_count == 0:
                return f"会话 {group_id} 暂无可删除的总结"

            return f"已成功删除 {deleted_count} 条总结"

        except Exception as e:
            logger.warning(f"[Memory] 清空总结失败: {e}")
            return f"清空总结失败: {e}"

    async def smart_retrieve(
        self,
        scope_id: str,
        query: str,
        max_results: int = 3,
    ) -> str:
        """
        智能检索知识库，只返回 1-3 条最相关结果

        用于注入 LLM 的会话记忆上下文

        Returns:
            格式化的记忆字符串，无结果时返回空字符串
        """
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return ""

            scope_kb_name = self._get_scope_kb_name(scope_id)
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            if not kb_helper:
                return ""

            if hasattr(kb_manager, "retrieve"):
                results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=query,
                        kb_names=[scope_kb_name],
                        top_m_final=max_results,
                    ),
                    timeout=5.0,
                )
            elif hasattr(kb_helper, "retrieve"):
                # Compatibility fallback for tests or older helper stubs.
                results = await asyncio.wait_for(
                    kb_helper.retrieve(
                        query=query,
                        top_m_final=max_results,
                    ),
                    timeout=5.0,
                )
            else:
                return ""

            if not results or not results.get("results"):
                return ""

            chunks = results.get("results", [])
            if not chunks:
                return ""

            lines = ["【相关记忆】"]
            shown = 0
            for chunk in chunks:
                if shown >= max_results:
                    break
                text = chunk.get("text", "") or chunk.get("content", "") or ""
                if not text:
                    continue
                if len(text) > 500:
                    text = text[:500] + "..."
                lines.append(f"- {text}")
                shown += 1

            result = "\n".join(lines)
            logger.debug(f"[Memory] smart_retrieve: scope={scope_id}, query={query[:30]}..., returned {shown} results")
            return result

        except Exception as e:
            logger.warning(f"[Memory] smart_retrieve 失败: {e}")
            return ""
