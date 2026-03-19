"""
每日会话总结系统 - 定时获取群聊/私聊消息，LLM 总结后存入知识库
"""

import asyncio
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
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=10.0)

            if not kb_helper:
                logger.warning(f"[Memory] 知识库 {self.memory_kb_name} 不存在")
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
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0)

            if not kb_helper:
                return f"知识库 {self.memory_kb_name} 不存在"

            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=f"范围ID: {group_id}",
                    kb_names=[self.memory_kb_name],
                    top_m_final=3,
                ),
                timeout=5.0,
            )

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

        if not confirm:
            return "请传入 confirm=true 确认要清空总结"

        try:
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0)

            if not kb_helper:
                return f"知识库 {self.memory_kb_name} 不存在"

            docs = await kb_helper.list_documents()
            if not docs:
                return "知识库已经是空的了"

            deleted_count = 0
            for doc in docs:
                doc_id = getattr(doc, "doc_id", None)
                if doc_id:
                    await kb_helper.delete_document(doc_id)
                    deleted_count += 1

            return f"已成功删除 {deleted_count} 条总结"

        except Exception as e:
            logger.warning(f"[Memory] 清空总结失败: {e}")
            return f"清空总结失败: {e}"
