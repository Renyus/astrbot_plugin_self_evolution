from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

MemoryManager = load_engine_module("memory").MemoryManager


class MemoryManagerTests(IsolatedAsyncioTestCase):
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
            cfg=SimpleNamespace(profile_group_whitelist=[]),
            eavesdropping=SimpleNamespace(active_users={"6001": {}, "private_7001": {}}),
        )
        manager = MemoryManager(plugin)

        scopes = await manager._get_target_scopes()

        self.assertEqual(scopes, ["6001", "private_7001"])

    async def test_summarize_group_passes_cached_group_umo(self):
        plugin = SimpleNamespace(get_group_umo=MagicMock(return_value="qq:group:6001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hello"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_group("6001")

        manager._llm_summarize.assert_awaited_once_with(["Alice: hello"], umo="qq:group:6001")
        manager._save_to_knowledge_base.assert_awaited_once_with("6001", "summary")

    async def test_summarize_private_scope_passes_cached_private_umo(self):
        plugin = SimpleNamespace(get_scope_umo=MagicMock(return_value="qq:private:7001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hi in private"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_scope("private_7001")

        manager._llm_summarize.assert_awaited_once_with(["Alice: hi in private"], umo="qq:private:7001")
        manager._save_to_knowledge_base.assert_awaited_once_with("private_7001", "summary")

    async def test_fetch_scope_messages_uses_friend_history_for_private_scope(self):
        bot = SimpleNamespace(
            call_action=AsyncMock(
                return_value={
                    "messages": [
                        {
                            "sender": {"user_id": 7001, "nickname": "Alice", "role": "member"},
                            "message": [{"type": "text", "data": {"text": "hello"}}],
                        }
                    ]
                }
            )
        )
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_msg_count=20),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])
            )
        )
        manager = MemoryManager(plugin)

        messages = await manager._fetch_scope_messages("private_7001")

        self.assertEqual(messages, ["Alice: hello"])
        bot.call_action.assert_awaited_once_with("get_friend_msg_history", user_id=7001, count=plugin.cfg.memory_msg_count)

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

        await manager._save_to_knowledge_base("6001", "summary")

        scope_kb.upload_document.assert_awaited_once()
        base_kb.upload_document.assert_not_awaited()

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
                get_config=lambda umo=None: {"kb_names": ["self_evolution_memory", "company_docs"], "kb_final_top_k": 4},
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
