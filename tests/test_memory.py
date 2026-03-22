from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

MemoryManager = load_engine_module("memory").MemoryManager


class MemoryManagerTests(IsolatedAsyncioTestCase):
    @staticmethod
    def _shanghai_dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0):
        return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=8)))

    @staticmethod
    def _build_message(message_seq: int, when: datetime, text: str, nickname: str = "Alice", user_id: int = 7001):
        return {
            "message_seq": message_seq,
            "message_id": message_seq,
            "time": int(when.timestamp()),
            "sender": {"user_id": user_id, "nickname": nickname, "role": "member"},
            "message": [{"type": "text", "data": {"text": text}}],
        }

    def _install_shared_preferences_stub(self, session_get=None, session_put=None, session_remove=None):
        core_module = types.ModuleType("astrbot.core")
        core_module.sp = SimpleNamespace(
            session_get=session_get or AsyncMock(return_value={}),
            session_put=session_put or AsyncMock(),
            session_remove=session_remove or AsyncMock(),
        )
        sys.modules["astrbot.core"] = core_module
        sys.modules["astrbot"].core = core_module
        return core_module.sp

    async def test_get_target_scopes_keeps_private_active_sessions(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=[]),
            eavesdropping=SimpleNamespace(active_users={"6001": {}, "private_7001": {}}),
            dao=SimpleNamespace(list_known_scopes=AsyncMock(return_value=[])),
        )
        manager = MemoryManager(plugin)

        scopes = await manager._get_target_scopes()

        self.assertEqual(scopes, ["6001", "private_7001"])

    async def test_get_target_scopes_appends_persisted_private_sessions(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=["6001"]),
            dao=SimpleNamespace(list_known_scopes=AsyncMock(return_value=["private_7001"])),
        )
        manager = MemoryManager(plugin)

        scopes = await manager._get_target_scopes()

        self.assertEqual(scopes, ["6001", "private_7001"])

    async def test_summarize_group_passes_cached_group_umo(self):
        reference_dt = self._shanghai_dt(2026, 3, 19, 10, 0)
        plugin = SimpleNamespace(get_group_umo=MagicMock(return_value="qq:group:6001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hello"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_group("6001", reference_dt=reference_dt)

        manager._fetch_scope_messages.assert_awaited_once_with("6001", reference_dt=reference_dt)
        manager._llm_summarize.assert_awaited_once_with(
            ["Alice: hello"],
            umo="qq:group:6001",
            summary_date="2026-03-18",
        )
        manager._save_to_knowledge_base.assert_awaited_once_with(
            "6001",
            "summary",
            summary_date="2026-03-18",
        )

    async def test_summarize_private_scope_passes_cached_private_umo(self):
        reference_dt = self._shanghai_dt(2026, 3, 19, 10, 0)
        plugin = SimpleNamespace(get_scope_umo=MagicMock(return_value="qq:private:7001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hi in private"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_scope("private_7001", reference_dt=reference_dt)

        manager._fetch_scope_messages.assert_awaited_once_with("private_7001", reference_dt=reference_dt)
        manager._llm_summarize.assert_awaited_once_with(
            ["Alice: hi in private"],
            umo="qq:private:7001",
            summary_date="2026-03-18",
        )
        manager._save_to_knowledge_base.assert_awaited_once_with(
            "private_7001",
            "summary",
            summary_date="2026-03-18",
        )

    async def test_fetch_scope_messages_uses_friend_history_for_private_scope(self):
        reference_dt = self._shanghai_dt(2026, 3, 19, 10, 0)
        bot = SimpleNamespace(
            call_action=AsyncMock(
                return_value={
                    "messages": [
                        self._build_message(101, self._shanghai_dt(2026, 3, 18, 12, 30), "hello"),
                        self._build_message(100, self._shanghai_dt(2026, 3, 17, 23, 50), "too old"),
                    ]
                }
            )
        )
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_msg_count=20, memory_fetch_page_size=20, memory_summary_chunk_size=200),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])
            ),
        )
        manager = MemoryManager(plugin)

        messages = await manager._fetch_scope_messages("private_7001", reference_dt=reference_dt)

        self.assertEqual(messages, ["Alice: hello"])
        bot.call_action.assert_awaited_once_with(
            "get_friend_msg_history", user_id=7001, count=plugin.cfg.memory_msg_count
        )

    async def test_fetch_scope_messages_collects_previous_day_across_multiple_pages(self):
        reference_dt = self._shanghai_dt(2026, 3, 19, 10, 0)
        page_1 = {
            "messages": [
                self._build_message(1000, self._shanghai_dt(2026, 3, 19, 9, 0), "today latest"),
                self._build_message(999, self._shanghai_dt(2026, 3, 19, 1, 0), "today early"),
                self._build_message(998, self._shanghai_dt(2026, 3, 18, 23, 0), "yesterday late"),
            ]
        }
        page_2 = {
            "messages": [
                self._build_message(998, self._shanghai_dt(2026, 3, 18, 23, 0), "yesterday late"),
                self._build_message(997, self._shanghai_dt(2026, 3, 18, 12, 0), "yesterday noon"),
                self._build_message(996, self._shanghai_dt(2026, 3, 18, 0, 5), "yesterday early"),
            ]
        }
        page_3 = {
            "messages": [
                self._build_message(996, self._shanghai_dt(2026, 3, 18, 0, 5), "yesterday early"),
                self._build_message(995, self._shanghai_dt(2026, 3, 17, 23, 55), "too old"),
            ]
        }
        bot = SimpleNamespace(call_action=AsyncMock(side_effect=[page_1, page_2, page_3]))
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_msg_count=20, memory_fetch_page_size=20, memory_summary_chunk_size=200),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])
            ),
        )
        manager = MemoryManager(plugin)

        messages = await manager._fetch_scope_messages("6001", reference_dt=reference_dt)

        self.assertEqual(
            messages,
            [
                "Alice: yesterday early",
                "Alice: yesterday noon",
                "Alice: yesterday late",
            ],
        )
        self.assertEqual(bot.call_action.await_count, 3)
        first_call = bot.call_action.await_args_list[0]
        second_call = bot.call_action.await_args_list[1]
        third_call = bot.call_action.await_args_list[2]
        self.assertEqual(first_call.args, ("get_group_msg_history",))
        self.assertEqual(first_call.kwargs, {"group_id": 6001, "count": 20})
        self.assertEqual(second_call.kwargs, {"group_id": 6001, "count": 20, "message_seq": 998})
        self.assertEqual(third_call.kwargs, {"group_id": 6001, "count": 20, "message_seq": 996})

    async def test_save_to_knowledge_base_uses_scope_isolated_kb(self):
        base_kb = SimpleNamespace(
            kb=SimpleNamespace(
                kb_id="base-kb-id",
                kb_name="self_evolution_memory",
                embedding_provider_id="embed-1",
                rerank_provider_id=None,
                emoji="📚",
                chunk_size=512,
                chunk_overlap=50,
                top_k_dense=50,
                top_k_sparse=50,
                top_m_final=5,
            ),
            upload_document=AsyncMock(),
        )
        scope_kb = SimpleNamespace(
            kb=SimpleNamespace(
                kb_id="scope-kb-id",
                kb_name="self_evolution_memory__scope__g_6001",
            ),
            list_documents=AsyncMock(return_value=[]),
            delete_document=AsyncMock(),
            upload_document=AsyncMock(),
        )
        kb_helpers = {
            "self_evolution_memory": base_kb,
            "self_evolution_memory__scope__g_6001": scope_kb,
        }

        async def get_kb_by_name(name):
            return kb_helpers.get(name)

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace(get_kb_by_name=AsyncMock(side_effect=get_kb_by_name))),
        )
        manager = MemoryManager(plugin)

        await manager._save_to_knowledge_base(
            "6001",
            {"overview": "summary", "key_facts": [], "key_entities": [], "tags": []},
            summary_date="2026-03-18",
        )

        scope_kb.upload_document.assert_awaited_once()
        scope_kb.delete_document.assert_not_awaited()
        base_kb.upload_document.assert_not_awaited()

    async def test_save_to_knowledge_base_stores_structured_chunks(self):
        scope_kb = SimpleNamespace(
            kb=SimpleNamespace(
                kb_id="scope-kb-id",
                kb_name="self_evolution_memory__scope__g_6001",
            ),
            list_documents=AsyncMock(return_value=[]),
            upload_document=AsyncMock(),
        )

        async def get_kb_by_name(name):
            if name == "self_evolution_memory__scope__g_6001":
                return scope_kb
            return None

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace(get_kb_by_name=AsyncMock(side_effect=get_kb_by_name))),
        )
        manager = MemoryManager(plugin)

        memory_struct = {
            "overview": "今日主要讨论了游戏",
            "key_facts": ["群里决定周日联机", "小明要加入公会"],
            "key_entities": ["小明", "会长"],
            "tags": ["游戏", "联机"],
        }
        await manager._save_to_knowledge_base("6001", memory_struct, summary_date="2026-03-21")

        scope_kb.upload_document.assert_awaited_once()
        upload_kwargs = scope_kb.upload_document.await_args.kwargs
        chunks = upload_kwargs["pre_chunked_text"]

        self.assertTrue(len(chunks) >= 3)
        first_chunk = chunks[0]
        self.assertIn("session_memory", first_chunk)
        self.assertIn("2026-03-21", first_chunk)
        self.assertTrue(any("关键事实" in c for c in chunks))
        self.assertTrue(any("小明" in c for c in chunks))

    async def test_llm_summarize_returns_structured_dict(self):
        plugin = SimpleNamespace(
            context=SimpleNamespace(
                get_using_provider=MagicMock(
                    return_value=SimpleNamespace(
                        text_chat=AsyncMock(
                            return_value=SimpleNamespace(
                                completion_text='{"overview":"今日讨论了游戏","key_facts":["决定周日联机"],"key_entities":["小明"],"tags":["游戏"]}'
                            )
                        )
                    )
                )
            ),
            cfg=SimpleNamespace(memory_msg_count=100, memory_fetch_page_size=100, memory_summary_chunk_size=200),
        )
        manager = MemoryManager(plugin)
        manager._split_messages_for_summary = MagicMock(return_value=[["msg1", "msg2"]])

        result = await manager._llm_summarize(["msg1", "msg2"], summary_date="2026-03-21")

        self.assertIsInstance(result, dict)
        self.assertIn("overview", result)
        self.assertIn("key_facts", result)
        self.assertIn("key_entities", result)
        self.assertIn("tags", result)
        self.assertEqual(result["overview"], "今日讨论了游戏")

    async def test_smart_retrieve_returns_limited_results(self):
        scope_kb = SimpleNamespace(
            kb=SimpleNamespace(kb_id="scope-kb-id", kb_name="scope__g_6001"),
            retrieve=AsyncMock(
                return_value={
                    "results": [
                        {"text": "记忆1"},
                        {"text": "记忆2"},
                        {"text": "记忆3"},
                        {"text": "记忆4"},
                    ]
                }
            ),
        )

        async def get_kb_by_name(name):
            if name == "self_evolution_memory__scope__g_6001":
                return scope_kb
            return None

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace(get_kb_by_name=AsyncMock(side_effect=get_kb_by_name))),
        )
        manager = MemoryManager(plugin)

        result = await manager.smart_retrieve("6001", "游戏", max_results=3)

        self.assertIn("相关记忆", result)
        lines = [l for l in result.split("\n") if l.startswith("- ")]
        self.assertEqual(len(lines), 3)

    async def test_smart_retrieve_empty_when_no_results(self):
        scope_kb = SimpleNamespace(
            kb=SimpleNamespace(kb_id="scope-kb-id", kb_name="scope__g_6001"),
            retrieve=AsyncMock(return_value={"results": []}),
        )

        async def get_kb_by_name(name):
            if name == "self_evolution_memory__scope__g_6001":
                return scope_kb
            return None

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace(get_kb_by_name=AsyncMock(side_effect=get_kb_by_name))),
        )
        manager = MemoryManager(plugin)

        result = await manager.smart_retrieve("6001", "游戏", max_results=3)

        self.assertEqual(result, "")

    async def test_save_to_knowledge_base_replaces_existing_summary_for_same_day(self):
        scope_kb = SimpleNamespace(
            kb=SimpleNamespace(
                kb_id="scope-kb-id",
                kb_name="self_evolution_memory__scope__g_6001",
            ),
            list_documents=AsyncMock(
                return_value=[
                    SimpleNamespace(doc_id="stale-same-day", doc_name="memory_6001_2026-03-18_1.txt"),
                    SimpleNamespace(doc_id="older-day", doc_name="memory_6001_2026-03-17_1.txt"),
                ]
            ),
            delete_document=AsyncMock(),
            upload_document=AsyncMock(),
        )

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace()),
        )
        manager = MemoryManager(plugin)
        manager._ensure_scope_kb = AsyncMock(return_value=scope_kb)

        await manager._save_to_knowledge_base(
            "6001",
            {"overview": "summary", "key_facts": [], "key_entities": [], "tags": []},
            summary_date="2026-03-18",
        )

        scope_kb.delete_document.assert_awaited_once_with("stale-same-day")
        upload_kwargs = scope_kb.upload_document.await_args.kwargs
        self.assertTrue(upload_kwargs["file_name"].startswith("memory_6001_2026-03-18_"))
        self.assertIn("session_memory", upload_kwargs["pre_chunked_text"][0])

    async def test_sync_scope_kb_binding_replaces_base_kb_with_scope_kb(self):
        sp = self._install_shared_preferences_stub()

        base_kb = SimpleNamespace(
            kb=SimpleNamespace(
                kb_id="base-kb-id",
                kb_name="self_evolution_memory",
                embedding_provider_id="embed-1",
                rerank_provider_id=None,
                emoji="📚",
                chunk_size=512,
                chunk_overlap=50,
                top_k_dense=50,
                top_k_sparse=50,
                top_m_final=5,
            ),
        )
        company_kb = SimpleNamespace(kb=SimpleNamespace(kb_id="company-kb-id", kb_name="company_docs"))
        kb_helpers = {
            "self_evolution_memory": base_kb,
            "company_docs": company_kb,
        }

        async def get_kb_by_name(name):
            return kb_helpers.get(name)

        async def get_kb(kb_id):
            for helper in kb_helpers.values():
                if helper.kb.kb_id == kb_id:
                    return helper
            return None

        async def create_kb(**kwargs):
            helper = SimpleNamespace(
                kb=SimpleNamespace(
                    kb_id="scope-kb-id",
                    kb_name=kwargs["kb_name"],
                    embedding_provider_id=kwargs["embedding_provider_id"],
                    rerank_provider_id=kwargs["rerank_provider_id"],
                    emoji=kwargs["emoji"],
                    chunk_size=kwargs["chunk_size"],
                    chunk_overlap=kwargs["chunk_overlap"],
                    top_k_dense=kwargs["top_k_dense"],
                    top_k_sparse=kwargs["top_k_sparse"],
                    top_m_final=kwargs["top_m_final"],
                )
            )
            kb_helpers[kwargs["kb_name"]] = helper
            return helper

        kb_manager = SimpleNamespace(
            get_kb_by_name=AsyncMock(side_effect=get_kb_by_name),
            get_kb=AsyncMock(side_effect=get_kb),
            create_kb=AsyncMock(side_effect=create_kb),
        )
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(
                kb_manager=kb_manager,
                get_config=lambda umo=None: {
                    "kb_names": ["self_evolution_memory", "company_docs"],
                    "kb_final_top_k": 4,
                },
            ),
        )
        manager = MemoryManager(plugin)

        await manager.sync_scope_kb_binding("6001", "qq:group:6001")

        kb_manager.create_kb.assert_awaited_once()
        sp.session_put.assert_awaited_once()
        put_args = sp.session_put.await_args.args
        self.assertEqual(put_args[0], "qq:group:6001")
        self.assertEqual(put_args[1], "kb_config")
        self.assertEqual(
            put_args[2]["kb_ids"],
            ["scope-kb-id", "company-kb-id"],
        )
        self.assertTrue(put_args[2]["_self_evolution_scope_binding"])

    async def test_clear_summary_only_deletes_matching_scope_docs(self):
        base_kb = SimpleNamespace(
            list_documents=AsyncMock(
                return_value=[
                    SimpleNamespace(doc_id="legacy-1", doc_name="summary_6001_1.txt"),
                    SimpleNamespace(doc_id="legacy-2", doc_name="summary_7001_1.txt"),
                ]
            ),
            delete_document=AsyncMock(),
        )
        scope_kb = SimpleNamespace(
            list_documents=AsyncMock(
                return_value=[
                    SimpleNamespace(doc_id="scope-1", doc_name="summary_6001_new.txt"),
                    SimpleNamespace(doc_id="scope-2", doc_name="summary_6001_newer.txt"),
                ]
            ),
            delete_document=AsyncMock(),
        )
        kb_helpers = {
            "self_evolution_memory": base_kb,
            "self_evolution_memory__scope__g_6001": scope_kb,
        }

        async def get_kb_by_name(name):
            return kb_helpers.get(name)

        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_kb_name="self_evolution_memory"),
            context=SimpleNamespace(kb_manager=SimpleNamespace(get_kb_by_name=AsyncMock(side_effect=get_kb_by_name))),
        )
        manager = MemoryManager(plugin)

        result = await manager.clear_summary("6001", confirm=True)

        self.assertEqual(result, "已成功删除 3 条总结")
        self.assertEqual(scope_kb.delete_document.await_count, 2)
        base_kb.delete_document.assert_awaited_once_with("legacy-1")
