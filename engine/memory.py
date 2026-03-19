"""
每日会话总结系统 - 定时获取群聊/私聊消息，LLM 总结后存入知识库
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime

logger = logging.getLogger("astrbot")

PRIVATE_SCOPE_PREFIX = "private_"

SUMMARY_PROMPT = """你是聊天总结助手。请分析以下聊天消息，输出一段详细的总结：

总结要求（尽可能详细，保留关键信息）：
1. 群聊的主要话题和讨论内容（详细描述）
2. 群内发生的重要事件或达成的共识
3. 活跃的成员或重要人物
4. 热门话题或讨论焦点
5. 任何有价值的信息

消息列表：
{messages}
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
    def memory_msg_count(self):
        return self.plugin.cfg.memory_msg_count

    async def daily_summary(self):
        """执行每日会话总结"""
        logger.debug("[Memory] 开始每日会话总结...")

        try:
            scopes = await self._get_target_scopes()
            if not scopes:
                logger.debug("[Memory] 无目标会话，跳过总结")
                return

            for scope_id in scopes:
                await self._summarize_scope(scope_id)

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
            logger.debug(
                f"[Memory] 已绑定会话知识库: umo={umo}, scope={scope_id}, kbs={deduped_names}"
            )
        except Exception as e:
            logger.warning(f"[Memory] 同步会话知识库绑定失败: {e}")

    async def _get_target_scopes(self):
        """获取需要总结的会话范围列表"""
        # 方式1: 白名单配置
        whitelist = getattr(self.plugin.cfg, "profile_group_whitelist", [])
        if whitelist:
            logger.debug(f"[Memory] 使用白名单群列表: {whitelist}")
            return [str(group_id) for group_id in whitelist]
        # 方式2: eavesdropping active_users
        if hasattr(self.plugin, "eavesdropping") and hasattr(self.plugin.eavesdropping, "active_users"):
            scopes = list(self.plugin.eavesdropping.active_users)
            if scopes:
                logger.debug(f"[Memory] 使用 eavesdropping 活跃会话列表: {scopes}")
                return scopes
        # 方式3: 通过 platform 获取 bot 加入的群列表
        return await self._fetch_groups_from_platform()

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

    async def _summarize_scope(self, scope_id: str):
        """总结单个会话范围的消息"""
        try:
            messages = await self._fetch_scope_messages(scope_id)
            if not messages:
                logger.debug(f"[Memory] 会话 {scope_id} 无消息")
                return

            scope_umo = self.plugin.get_scope_umo(scope_id) if hasattr(self.plugin, "get_scope_umo") else None
            if not scope_umo and hasattr(self.plugin, "get_group_umo") and not self._is_private_scope(scope_id):
                scope_umo = self.plugin.get_group_umo(scope_id)
            summary = await self._llm_summarize(messages, umo=scope_umo)
            if not summary:
                return

            await self._save_to_knowledge_base(scope_id, summary)
            logger.debug(f"[Memory] 会话 {scope_id} 总结已保存")

        except Exception as e:
            logger.warning(f"[Memory] 会话 {scope_id} 总结失败: {e}")

    async def _summarize_group(self, group_id: str):
        """兼容旧调用，按 scope 总结消息。"""
        await self._summarize_scope(group_id)

    async def _fetch_scope_messages(self, scope_id: str):
        """通过 NapCat API 获取群聊/私聊消息"""
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

            if self._is_private_scope(scope_id):
                private_user_id = self._get_private_scope_user_id(scope_id)
                if not private_user_id:
                    return []
                result = await bot.call_action(
                    "get_friend_msg_history",
                    user_id=int(private_user_id),
                    count=self.memory_msg_count,
                )
            else:
                result = await bot.call_action(
                    "get_group_msg_history",
                    group_id=int(scope_id),
                    count=self.memory_msg_count,
                )

            messages = result.get("messages", [])
            if not messages:
                logger.debug(f"[Memory] 会话 {scope_id}: 无消息")
                return []

            from .context_injection import parse_message_chain

            formatted = await asyncio.gather(*[parse_message_chain(msg, self.plugin) for msg in messages])

            formatted = [f for f in formatted if f]

            if not formatted:
                logger.debug(f"[Memory] 会话 {scope_id}: 消息格式化为空")
                return []

            latest_messages = (
                formatted[-self.memory_msg_count :] if len(formatted) > self.memory_msg_count else formatted
            )

            logger.debug(
                f"[Memory] 会话 {scope_id}: 获取到 {len(formatted)} 条消息，取最新的 {len(latest_messages)} 条进行总结"
            )

            return latest_messages

        except Exception as e:
            logger.warning(f"[Memory] 获取会话消息失败: {e}")
            return []

    async def _fetch_group_messages(self, group_id: str):
        """兼容旧调用，按 scope 获取消息。"""
        return await self._fetch_scope_messages(group_id)

    async def _llm_summarize(self, messages: list, umo: str | None = None) -> str:
        """调用 LLM 总结消息"""
        try:
            llm_provider = self.plugin.context.get_using_provider(umo=umo)
            if not llm_provider:
                return None

            prompt = SUMMARY_PROMPT.format(messages="\n".join(messages))

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个会话总结助手，只输出精简的总结文本。",
            )

            return res.completion_text.strip() if res.completion_text else None

        except Exception as e:
            logger.warning(f"[Memory] LLM 总结失败: {e}")
            return None

    async def _save_to_knowledge_base(self, scope_id: str, summary: str):
        """保存总结到知识库"""
        try:
            kb_helper = await asyncio.wait_for(self._ensure_scope_kb(scope_id), timeout=10.0)

            if not kb_helper:
                logger.warning(f"[Memory] 会话 {scope_id} 的隔离知识库不可用")
                return

            chat_type = "私聊" if self._is_private_scope(scope_id) else "群聊"
            extra_scope_line = (
                f"用户ID: {self._get_private_scope_user_id(scope_id)}"
                if self._is_private_scope(scope_id)
                else f"群号: {scope_id}"
            )
            formatted = (
                f"【会话总结】\n"
                f"类型: {chat_type}\n"
                f"范围ID: {scope_id}\n"
                f"{extra_scope_line}\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d')}\n"
                f"内容: {summary}"
            )

            await kb_helper.upload_document(
                file_name=f"summary_{scope_id}_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[formatted],
            )

        except Exception as e:
            logger.warning(f"[Memory] 保存总结失败: {e}")

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
