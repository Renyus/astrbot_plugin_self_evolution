import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("astrbot")

PRIVATE_SCOPE_PREFIX = "private_"


class SessionMemoryStore:
    def __init__(self, plugin):
        self.plugin = plugin

    def _debug(self, msg: str):
        if hasattr(self.plugin, "cfg") and self.plugin.cfg.memory_debug_enabled:
            logger.debug(msg)

    def _is_private_scope(self, scope_id: str) -> bool:
        return scope_id.startswith(PRIVATE_SCOPE_PREFIX)

    def _get_private_scope_user_id(self, scope_id: str) -> str:
        if self._is_private_scope(scope_id):
            return scope_id[len(PRIVATE_SCOPE_PREFIX) :]
        return ""

    def _get_scope_kb_name(self, scope_id: str) -> str:
        memory_kb_name = getattr(self.plugin.cfg, "memory_kb_name", "self_evolution_memory")
        if self._is_private_scope(scope_id):
            return f"{memory_kb_name}__scope__p_{self._get_private_scope_user_id(scope_id)}"
        return f"{memory_kb_name}__scope__g_{scope_id}"

    def _get_default_embedding_provider_id(self) -> str | None:
        """从 provider_manager 获取默认 embedding provider ID。"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return None
            provider_manager = getattr(kb_manager, "provider_manager", None)
            if not provider_manager:
                return None
            insts = getattr(provider_manager, "embedding_provider_insts", [])
            if insts:
                return insts[0].provider_config.get("id")
        except Exception:
            pass
        return None

    async def _ensure_scope_kb(self, scope_id: str, umo: str | None = None):
        """确保 scope 对应的知识库存在并绑定"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return None

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
                if kb_helper:
                    return kb_helper
            except Exception:
                pass

            embedding_provider_id = self._get_default_embedding_provider_id()
            if not embedding_provider_id:
                logger.warning(f"[MemoryStore] 没有可用的 embedding provider，无法创建知识库")
                return None

            try:
                await kb_manager.create_kb(
                    kb_name=scope_kb_name,
                    description=f"会话记忆 scope={scope_id}",
                    embedding_provider_id=embedding_provider_id,
                )
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
                return kb_helper
            except Exception as e:
                logger.warning(f"[MemoryStore] 创建知识库失败: {e}")
                return None

        except Exception as e:
            logger.warning(f"[MemoryStore] _ensure_scope_kb 出错: {e}")
            return None

    async def _resolve_active_kb_names_for_umo(self, umo: str):
        """解析当前会话正在使用的知识库列表与检索参数"""
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
        """同步 scope 与知识库的绑定关系"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
                if kb_helper:
                    return
            except Exception:
                pass

            try:
                embedding_provider_id = self._get_default_embedding_provider_id()
                if not embedding_provider_id:
                    self._debug(f"[MemoryStore] scope={scope_id} kb_bind=失败: 没有可用的embedding provider")
                    return
                await kb_manager.create_kb(
                    kb_name=scope_kb_name,
                    description=f"会话记忆 scope={scope_id}",
                    embedding_provider_id=embedding_provider_id,
                )
            except Exception as e:
                self._debug(f"[MemoryStore] scope={scope_id} kb_bind=失败: {e}")

        except Exception as e:
            self._debug(f"[MemoryStore] scope={scope_id} kb_bind=错误: {e}")

    async def save_daily_summary(
        self,
        scope_id: str,
        memory: str,
        summary_date: str,
    ) -> str:
        """保存每日总结到知识库"""
        try:
            kb_helper = await asyncio.wait_for(self._ensure_scope_kb(scope_id), timeout=10.0)
            if not kb_helper:
                return f"知识库不可用，无法保存总结"

            file_prefix = f"memory_{scope_id}_{summary_date}_"

            scope_label = (
                f"用户ID: {self._get_private_scope_user_id(scope_id)}"
                if self._is_private_scope(scope_id)
                else f"群号: {scope_id}"
            )

            if hasattr(kb_helper, "list_documents"):
                docs = await kb_helper.list_documents()
                for doc in docs:
                    doc_id = getattr(doc, "doc_id", None)
                    doc_name = getattr(doc, "doc_name", "")
                    if doc_id and doc_name.startswith(file_prefix):
                        await kb_helper.delete_document(doc_id)

            try:
                memory_data = None
                try:
                    memory_data = json.loads(memory)
                except Exception:
                    pass

                if memory_data:
                    key_facts = memory_data.get("key_facts", [])
                    key_entities = memory_data.get("key_entities", [])
                    tags = memory_data.get("tags", [])
                    overview = memory_data.get("overview", "")

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
                    content_for_upload = chunks
                else:
                    content_for_upload = [f"【每日会话总结】\n日期: {summary_date}\n范围: {scope_label}\n---\n{memory}"]
            except Exception:
                content_for_upload = [f"【每日会话总结】\n日期: {summary_date}\n范围: {scope_label}\n---\n{memory}"]

            await kb_helper.upload_document(
                file_name=f"{file_prefix}{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=content_for_upload,
            )
            self._debug(f"[MemoryStore] scope={scope_id} date={summary_date} saved=yes")
            return f"总结已保存: {summary_date}"

        except Exception as e:
            logger.warning(f"[MemoryStore] 保存总结失败: {e}")
            return f"保存总结失败: {e}"

    async def save_session_event(
        self,
        scope_id: str,
        session_event: dict,
    ) -> bool:
        """保存 session_event 到知识库"""
        try:
            kb_helper = await asyncio.wait_for(self._ensure_scope_kb(scope_id), timeout=10.0)
            if not kb_helper:
                logger.warning(f"[MemoryStore] 会话 {scope_id} 的隔离知识库不可用")
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
            return True

        except Exception as e:
            logger.warning(f"[MemoryStore] save_session_event 失败: {e}")
            return False

    async def get_summary_by_date(self, scope_id: str, summary_date: str) -> str:
        """按日期精确获取某天的会话总结 - 使用稳定的doc_id/chunks方式"""
        from datetime import datetime, timedelta

        try:
            resolved_date = summary_date.strip().lower()
            if resolved_date == "today":
                resolved_date = datetime.now().strftime("%Y-%m-%d")
            elif resolved_date == "yesterday":
                resolved_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                try:
                    datetime.strptime(resolved_date, "%Y-%m-%d")
                except ValueError:
                    return ""

            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return ""

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            except Exception:
                return ""

            if not kb_helper:
                return ""

            if not hasattr(kb_helper, "list_documents"):
                return ""

            docs = await kb_helper.list_documents()
            target_prefix = f"memory_{scope_id}_{resolved_date}_"
            matching_docs = []

            for doc in docs:
                doc_name = getattr(doc, "doc_name", "")
                if doc_name.startswith(target_prefix):
                    matching_docs.append(doc)

            if not matching_docs:
                return ""

            matching_docs.sort(key=lambda d: getattr(d, "doc_name", ""), reverse=True)
            latest_doc = matching_docs[0]

            doc_id = getattr(latest_doc, "doc_id", None)
            if not doc_id:
                return ""

            chunks = []
            if hasattr(kb_helper, "get_chunks_by_doc_id"):
                chunks = await asyncio.wait_for(
                    kb_helper.get_chunks_by_doc_id(doc_id),
                    timeout=5.0,
                )

            if not chunks:
                return ""

            content_parts = []
            for chunk in chunks:
                chunk_text = chunk.get("content", "") if isinstance(chunk, dict) else ""
                if chunk_text:
                    content_parts.append(chunk_text)

            content = "\n".join(content_parts)
            if not content:
                return ""

            return content

        except Exception as e:
            logger.warning(f"[MemoryStore] get_summary_by_date 失败: {e}")
            return ""

    async def retrieve_events(
        self,
        scope_id: str,
        query: str,
        max_results: int = 3,
    ) -> list[str]:
        """检索 session_event 类型的记忆"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return []

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            except Exception:
                return []

            if not kb_helper:
                return []

            try:
                results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=query,
                        kb_names=[scope_kb_name],
                        top_m_final=max_results,
                    ),
                    timeout=5.0,
                )
            except Exception:
                if hasattr(kb_helper, "retrieve"):
                    results = await asyncio.wait_for(
                        kb_helper.retrieve(query=query, top_k=max_results),
                        timeout=5.0,
                    )
                else:
                    return []

            if not results:
                return []

            events = []
            for r in results:
                content = ""
                if isinstance(r, dict):
                    content = r.get("content", "") or r.get("text", "")
                elif isinstance(r, str):
                    content = r
                if content and "session_event" in content.lower():
                    events.append(content)

            return events[:max_results]

        except Exception as e:
            logger.warning(f"[MemoryStore] retrieve_events 失败: {e}")
            return []

    async def retrieve_summary(
        self,
        scope_id: str,
        query: str,
        max_results: int = 3,
    ) -> str:
        """检索总结类记忆"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return ""

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            except Exception:
                return ""

            if not kb_helper:
                return ""

            try:
                results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=query,
                        kb_names=[scope_kb_name],
                        top_m_final=max_results,
                    ),
                    timeout=5.0,
                )
            except Exception:
                if hasattr(kb_helper, "retrieve"):
                    results = await asyncio.wait_for(
                        kb_helper.retrieve(query=query, top_k=max_results),
                        timeout=5.0,
                    )
                else:
                    return ""

            if not results:
                return ""

            chunks = results.get("results", []) if isinstance(results, dict) else results
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

            result_text = "\n".join(lines)
            logger.debug(
                f"[MemoryStore] retrieve_summary: scope={scope_id}, query={query[:30]}..., returned {shown} results"
            )
            return result_text

        except Exception as e:
            logger.warning(f"[MemoryStore] retrieve_summary 失败: {e}")
            return ""

    async def clear_summary(self, scope_id: str, confirm: bool = False) -> str:
        """清空指定 scope 的所有总结（只删 memory_ 文档，保留 event_ 文档）"""
        if not confirm:
            return "操作已取消"

        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return "知识库管理器不可用"

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            except Exception:
                return "知识库不存在"

            if not kb_helper:
                return "知识库不存在"

            if not hasattr(kb_helper, "list_documents"):
                return "知识库不支持清空操作"

            docs = await kb_helper.list_documents()
            deleted = 0
            summary_prefix = f"memory_{scope_id}_"
            for doc in docs:
                doc_name = getattr(doc, "doc_name", "") if not isinstance(doc, str) else doc
                if not doc_name.startswith(summary_prefix):
                    continue
                doc_id = getattr(doc, "doc_id", None) or doc
                try:
                    await kb_helper.delete_document(doc_id)
                    deleted += 1
                except Exception:
                    pass
            return f"已删除 {deleted} 份总结"

        except Exception as e:
            logger.warning(f"[MemoryStore] clear_summary 失败: {e}")
            return f"清空失败: {e}"

    async def clear_kb(self, scope_id: str) -> str:
        """清空指定 scope 的整个知识库（包括文档和实例）"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return "知识库管理器不可用"

            scope_kb_name = self._get_scope_kb_name(scope_id)

            try:
                kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(scope_kb_name), timeout=5.0)
            except Exception:
                return f"知识库不存在（scope: {scope_id}）"

            if not kb_helper:
                return f"知识库不存在（scope: {scope_id}）"

            kb_id = getattr(kb_helper.kb, "kb_id", None)
            if not kb_id:
                return f"无法获取知识库 ID（scope: {scope_id}）"

            success = await kb_manager.delete_kb(kb_id)
            if success:
                return f"已删除知识库及其所有文档（scope: {scope_id}）"
            return f"删除知识库失败（scope: {scope_id}）"

        except Exception as e:
            logger.warning(f"[MemoryStore] clear_kb 失败: {e}")
            return f"清空失败: {e}"

    async def clear_all_kb(self) -> str:
        """清空所有 scope 的所有知识库（包括文档和实例），保留主知识库"""
        try:
            kb_manager = getattr(self.plugin.context, "kb_manager", None)
            if not kb_manager:
                return "知识库管理器不可用"

            deleted_count = 0
            kbs = await kb_manager.list_kbs()
            memory_kb_prefix = getattr(self.plugin.cfg, "memory_kb_name", "self_evolution_memory")

            for kb in kbs:
                kb_name = getattr(kb, "kb_name", "") if not isinstance(kb, str) else kb
                if not kb_name.startswith(memory_kb_prefix):
                    continue
                if "__scope__" not in kb_name:
                    continue

                kb_id = getattr(kb, "kb_id", None)
                if not kb_id:
                    continue

                try:
                    success = await kb_manager.delete_kb(kb_id)
                    if success:
                        deleted_count += 1
                except Exception:
                    pass

            return f"已删除 {deleted_count} 个 scope 知识库"

        except Exception as e:
            logger.warning(f"[MemoryStore] clear_all_kb 失败: {e}")
            return f"清空失败: {e}"
