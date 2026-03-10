import logging
import asyncio
import re
import time
from datetime import datetime

logger = logging.getLogger("astrbot")


class MemoryManager:
    """
    CognitionCore 记忆管理模块
    负责记忆的存储、检索等功能
    """

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def memory_kb_name(self):
        return self.plugin.config.get("memory_kb_name", "self_evolution_memory")

    @property
    def timeout_memory_commit(self):
        return float(self.plugin.config.get("timeout_memory_commit", 10.0))

    @property
    def timeout_memory_recall(self):
        return float(self.plugin.config.get("timeout_memory_recall", 12.0))

    @property
    def max_memory_entries(self):
        return int(self.plugin.config.get("max_memory_entries", 100))

    async def auto_learn_trigger(self, event):
        """自动学习触发器：检测关键场景并自动提取记忆"""
        msg_text = event.message_str
        is_at = event.is_at_or_wake_command

        is_key_scene = False

        if is_at:
            is_key_scene = True

        if not is_key_scene:
            try:
                critical_keywords = self.plugin.config.get(
                    "critical_keywords",
                    "黑塔|空间站|人偶|天才|模拟宇宙|研究|论文|技术|算力|数据",
                )
                critical_pattern = re.compile(f"({critical_keywords})", re.IGNORECASE)
                if critical_pattern.search(msg_text):
                    is_key_scene = True
            except Exception:
                pass

        goodbye_keywords = ["再见", "拜拜", "走了", "下线", "休息", "睡觉", "晚安"]
        if not is_key_scene and any(kw in msg_text for kw in goodbye_keywords):
            is_key_scene = True

        preference_keywords = [
            "我喜欢",
            "我讨厌",
            "我想要",
            "我不喜欢",
        ]
        if not is_key_scene and any(kw in msg_text for kw in preference_keywords):
            is_key_scene = True

        if is_key_scene:
            await self._learn_to_memory(event, msg_text)

    async def _learn_to_memory(self, event, msg_text):
        """按用户/群汇总存知识库（按用户隔离）"""
        try:
            group_id = event.get_group_id()
            user_id = event.get_sender_id()
            user_name = event.get_sender_name() or "未知用户"
            msg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if group_id:
                # 群聊时按用户隔离存储
                doc_name = f"memory_group_{group_id}_user_{user_id}"
            else:
                doc_name = f"memory_user_{user_id}"

            new_entry = f"[{msg_time}] {user_name}: {msg_text}"

            kb_manager = self.plugin.context.kb_manager
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )

            if not kb_helper:
                logger.warning(f"[SelfEvolution] 知识库 {self.memory_kb_name} 不存在")
                return

            await kb_helper.upload_document(
                file_name=f"{doc_name}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[new_entry],
            )

            logger.info(f"[SelfEvolution] 自动学习：已追加记忆到 {doc_name}")

        except Exception as e:
            logger.warning(f"[SelfEvolution] 自动学习失败: {e}")

    async def commit_to_memory(self, event, fact: str) -> str:
        """手动存入记忆"""
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name() or "未知用户"
        group_id = event.get_group_id() or "私聊"
        unified_msg_origin = event.unified_msg_origin

        formatted_fact = (
            f"【记忆条目】\n"
            f"来源: {unified_msg_origin}\n"
            f"说话者: {sender_name} (ID: {sender_id})\n"
            f"群/私聊: {group_id}\n"
            f"内容: {fact}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return await self._do_commit_memory(formatted_fact)

    async def save_group_knowledge(
        self,
        event,
        knowledge: str,
        knowledge_type: str = "约定",
        source_uuids: list = None,
    ) -> str:
        """保存群公共知识

        Args:
            knowledge: 用最简练的冷白描手法记录事实，必须包含明确的时间状语
            knowledge_type: 知识类型：群规/约定活动/群共识
            source_uuids: 触发记录的原始消息 UUID 列表
        """
        group_id = event.get_group_id()
        if not group_id:
            return "只有群聊才能保存群公共知识，私聊场景不支持此操作。"

        source_uuids = source_uuids or []
        uuid_str = f"来源UUID:{source_uuids}" if source_uuids else ""

        formatted_knowledge = f"({knowledge_type}) {knowledge} {uuid_str}"

        return await self._do_commit_memory(formatted_knowledge)

    async def _do_commit_memory(
        self, formatted_fact: str, is_auto: bool = False
    ) -> str:
        """执行实际的存入记忆逻辑（包含去重和自动清理）"""
        kb_manager = self.plugin.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 记忆库装载严重超时。")
            return "与知识引擎服务器建立信道超时，中断存入以维持会话流畅。"
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise
            logger.error(f"[SelfEvolution] 记忆检索或系统网络失效: {e}")
            return "检索长期记忆时发生业务异常，请检查配置与联通状态。"

        if not kb_helper:
            logger.warning(
                f"[SelfEvolution] 记忆知识库 '{self.memory_kb_name}' 不存在。"
            )
            return (
                f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"
            )

        try:
            try:
                check_results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=formatted_fact[:100],
                        kb_names=[self.memory_kb_name],
                        top_m_final=3,
                    ),
                    timeout=5.0,
                )
                if check_results and check_results.get("results"):
                    for r in check_results.get("results", []):
                        if r.get("content") and formatted_fact[:50] in r.get(
                            "content", ""
                        ):
                            logger.info(
                                "[SelfEvolution] 记忆去重：检测到相似内容已存在，跳过存入。"
                            )
                            return "已存在相似记忆，无需重复存储。"
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.warning(f"[SelfEvolution] 记忆去重检查失败: {e}")

            try:
                docs = await kb_helper.list_documents()
                if docs and len(docs) >= self.max_memory_entries:
                    oldest_doc = min(
                        docs, key=lambda d: getattr(d, "created_at", "") or ""
                    )
                    doc_id = getattr(oldest_doc, "doc_id", None)
                    if doc_id:
                        await kb_helper.delete_document(doc_id)
                        logger.info(
                            f"[SelfEvolution] 自动清理：已删除最旧的记忆条目 {doc_id}"
                        )
            except Exception as e:
                logger.warning(f"[SelfEvolution] 自动清理失败: {e}")

            await kb_helper.upload_document(
                file_name=f"memory_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[formatted_fact],
            )
            logger.info(
                f"[SelfEvolution] MEMORY_COMMIT: 成功存入一条长期记忆: {formatted_fact[:50]}..."
            )
            return "事实已成功存入长期记忆库，我以后会记得这件事的。"
        except (TimeoutError, ConnectionError) as e:
            logger.error(f"[SelfEvolution] 存入记忆网络通讯中断/超时: {e}")
            return "与知识库服务器建立通讯失败，无法写入新数据。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 存入记忆失败: {str(e)}")
            return "存入记忆时出现未知级别异常，请通知排查。"

    async def recall_memories(self, event, query: str) -> str:
        """检索记忆"""
        kb_manager = self.plugin.context.kb_manager
        try:
            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=query, kb_names=[self.memory_kb_name], top_m_final=5
                ),
                timeout=self.timeout_memory_recall,
            )
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 检索记忆网络通信卡死/超时。")
            return "检索长期记忆时与核心向量库层通信严重超时，为防止阻塞当前对话流，已强制中止操作。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 检索记忆请求失败: {e}")
            return "检索长期记忆时发生接口异常，请通知管理员检查日志。"

        if not results or not results.get("results"):
            logger.debug(f"[SelfEvolution] 记忆检索无结果。查询: {query}")
            return "在长期记忆库中未找到相关信息。"

        context_text = results.get("context_text", "")
        logger.info(
            f"[SelfEvolution] MEMORY_RECALL: 记忆检索成功。查询: {query} -> 找到 {len(results.get('results', []))} 条结果。"
        )
        return f"从我的长期记忆中找到了以下内容：\n\n{context_text}"

    async def learn_from_context(self, event, key_info: str = "") -> str:
        """从当前对话中自动提取关键信息并存入长期记忆"""
        sender_name = event.get_sender_name() or "未知用户"
        sender_id = event.get_sender_id()
        group_id = event.get_group_id() or "私聊"
        unified_msg_origin = event.unified_msg_origin
        message_text = event.message_str

        fact = key_info if key_info else f"用户在当前对话中提到: {message_text}"

        formatted_fact = (
            f"【记忆条目-对话学习】\n"
            f"来源: {unified_msg_origin}\n"
            f"说话者: {sender_name} (ID: {sender_id})\n"
            f"群/私聊: {group_id}\n"
            f"内容: {fact}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return await self._do_commit_memory(formatted_fact, is_auto=True)

    async def clear_all_memory(self, event, confirm: bool = False) -> str:
        """清空所有记忆"""
        if not confirm:
            return "请传入 confirm=true 确认要清空全部记忆，例如: clear_all_memory(confirm=true)"

        kb_manager = self.plugin.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取知识库失败: {e}")
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            docs = await kb_helper.list_documents()
            if not docs:
                return "记忆库已经是空的了。"

            deleted_count = 0
            for doc in docs:
                try:
                    doc_id = getattr(doc, "doc_id", None)
                    if doc_id:
                        await kb_helper.delete_document(doc_id)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"[SelfEvolution] 删除记忆条目失败: {e}")

            logger.info(f"[SelfEvolution] 清空记忆：成功删除 {deleted_count} 条记忆")
            return f"已成功清空 {deleted_count} 条记忆条目。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 清空记忆失败: {e}")
            return f"清空记忆失败: {e}"

    async def list_memories(self, event, limit: int = 10) -> str:
        """列出记忆"""
        kb_manager = self.plugin.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )
        except Exception as e:
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            docs = await kb_helper.list_documents()
            if not docs:
                return "记忆库中还没有任何记忆。"

            docs = docs[:limit]
            result = [f"当前记忆库共有 {len(docs)} 条记忆（显示前 {len(docs)} 条）："]
            for i, doc in enumerate(docs, 1):
                doc_name = getattr(doc, "doc_name", "未知")
                created_at = getattr(doc, "created_at", "未知时间")
                result.append(f"{i}. {doc_name} (创建于: {created_at})")

            return "\n".join(result)
        except Exception as e:
            logger.error(f"[SelfEvolution] 列出记忆失败: {e}")
            return f"列出记忆失败: {e}"

    async def delete_memory(self, event, doc_id: str) -> str:
        """删除单条记忆"""
        kb_manager = self.plugin.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )
        except Exception as e:
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            await kb_helper.delete_document(doc_id)
            logger.info(f"[SelfEvolution] 删除记忆：成功删除 doc_id={doc_id}")
            return f"已成功删除记忆条目 {doc_id}。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 删除记忆失败: {e}")
            return f"删除记忆失败: {e}"

    async def auto_recall(self, event, topic: str = "") -> str:
        """主动将相关记忆注入上下文"""
        query = topic if topic else event.message_str

        kb_manager = self.plugin.context.kb_manager
        try:
            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=query, kb_names=[self.memory_kb_name], top_m_final=3
                ),
                timeout=self.timeout_memory_recall,
            )
        except asyncio.TimeoutError:
            return "检索记忆超时，请稍后重试。"
        except Exception as e:
            logger.error(f"[SelfEvolution] auto_recall 失败: {e}")
            return "检索记忆时发生异常。"

        if not results or not results.get("results"):
            return "当前对话未涉及任何历史记忆。"

        context_text = results.get("context_text", "")
        logger.info(
            f"[SelfEvolution] AUTO_RECALL: 找到 {len(results.get('results', []))} 条相关记忆"
        )

        return (
            f"【相关记忆触发】\n"
            f"当前话题: {query}\n"
            f"--- 历史记忆 ---\n{context_text}\n"
            f"----------------\n"
            f"以上是与你当前话题相关的记忆，请结合这些信息回复用户。"
        )
