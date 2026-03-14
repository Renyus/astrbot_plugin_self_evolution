"""
每日群聊总结系统 - 定时获取群消息，LLM 总结后存入知识库
"""

import asyncio
import logging
import time
from datetime import datetime

logger = logging.getLogger("astrbot")

SUMMARY_PROMPT = """你是群聊总结助手。请分析以下群聊消息，输出一段精简的总结：

总结要求：
- 概括群聊的主要话题和讨论内容
- 记录重要的群事件或共识
- 不超过 200 字

消息列表：
{messages}
"""


class MemoryManager:
    """每日群聊总结管理器"""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def memory_kb_name(self):
        return self.plugin.cfg.memory_kb_name

    @property
    def memory_msg_count(self):
        return self.plugin.cfg.memory_msg_count

    async def daily_summary(self):
        """执行每日群聊总结"""
        logger.info("[Memory] 开始每日群聊总结...")

        try:
            groups = self._get_target_groups()
            if not groups:
                logger.info("[Memory] 无目标群，跳过总结")
                return

            for group_id in groups:
                await self._summarize_group(group_id)

            logger.info("[Memory] 每日群聊总结完成")

        except Exception as e:
            logger.error(f"[Memory] 每日群聊总结异常: {e}", exc_info=True)

    def _get_target_groups(self):
        """获取需要总结的群列表"""
        if hasattr(self.plugin, "eavesdropping") and hasattr(
            self.plugin.eavesdropping, "active_users"
        ):
            return list(self.plugin.eavesdropping.active_users.keys())
        return []

    async def _summarize_group(self, group_id: str):
        """总结单个群的消息"""
        try:
            messages = await self._fetch_group_messages(group_id)
            if not messages:
                logger.debug(f"[Memory] 群 {group_id} 无消息")
                return

            summary = await self._llm_summarize(messages)
            if not summary:
                return

            await self._save_to_knowledge_base(group_id, summary)
            logger.info(f"[Memory] 群 {group_id} 总结已保存")

        except Exception as e:
            logger.warning(f"[Memory] 群 {group_id} 总结失败: {e}")

    async def _fetch_group_messages(self, group_id: str):
        """通过 NapCat API 获取群消息"""
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

            result = await bot.call_action(
                "get_group_msg_history",
                group_id=int(group_id),
                count=self.memory_msg_count,
            )

            messages = result.get("messages", [])
            formatted = []
            for msg in messages:
                sender = msg.get("sender", {})
                nickname = sender.get("nickname", "未知")
                content = msg.get("message", "")
                if content:
                    formatted.append(f"{nickname}: {content}")

            return formatted

        except Exception as e:
            logger.warning(f"[Memory] 获取群消息失败: {e}")
            return []

    async def _llm_summarize(self, messages: list) -> str:
        """调用 LLM 总结消息"""
        try:
            llm_provider = self.plugin.context.get_using_provider("qq")
            if not llm_provider:
                return None

            prompt = SUMMARY_PROMPT.format(messages="\n".join(messages[:100]))

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个群聊总结助手，只输出精简的总结文本。",
            )

            return res.completion_text.strip() if res.completion_text else None

        except Exception as e:
            logger.warning(f"[Memory] LLM 总结失败: {e}")
            return None

    async def _save_to_knowledge_base(self, group_id: str, summary: str):
        """保存总结到知识库"""
        try:
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=10.0
            )

            if not kb_helper:
                logger.warning(f"[Memory] 知识库 {self.memory_kb_name} 不存在")
                return

            formatted = (
                f"【群聊总结】\n"
                f"群号: {group_id}\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d')}\n"
                f"内容: {summary}"
            )

            await kb_helper.upload_document(
                file_name=f"summary_{group_id}_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[formatted],
            )

        except Exception as e:
            logger.warning(f"[Memory] 保存总结失败: {e}")

    async def view_summary(self, group_id: str = None) -> str:
        """查看群聊总结"""
        logger.info(f"[Memory] 查看总结: {group_id}")

        if not group_id:
            return "请指定群号"

        try:
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )

            if not kb_helper:
                return f"知识库 {self.memory_kb_name} 不存在"

            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=f"群号: {group_id}",
                    kb_names=[self.memory_kb_name],
                    top_m_final=3,
                ),
                timeout=5.0,
            )

            if not results or not results.get("results"):
                return f"群 {group_id} 暂无总结"

            context_text = results.get("context_text", "")
            return f"群 {group_id} 的总结：\n\n{context_text}"

        except Exception as e:
            logger.warning(f"[Memory] 查看总结失败: {e}")
            return f"查看总结失败: {e}"

    async def clear_summary(self, group_id: str = None, confirm: bool = False) -> str:
        """清空群聊总结"""
        logger.info(f"[Memory] 清空总结: {group_id}, confirm={confirm}")

        if not confirm:
            return "请传入 confirm=true 确认要清空总结"

        try:
            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )

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
