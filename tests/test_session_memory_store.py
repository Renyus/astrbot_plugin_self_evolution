from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from tests._helpers import load_engine_module

SessionMemoryStore = load_engine_module("session_memory_store").SessionMemoryStore


def _fake_kb_helper(name_prefix=""):
    helper = MagicMock()
    helper.kb = MagicMock()
    helper.kb.kb_name = f"{name_prefix}kb"
    helper.list_documents = AsyncMock(return_value=[])
    helper.delete_document = AsyncMock()
    helper.upload_document = AsyncMock()
    helper.get_kb_by_name = AsyncMock(return_value=helper)
    return helper


class EnsureScopeKbTests(IsolatedAsyncioTestCase):
    """Tests for _ensure_scope_kb() KB creation fix."""

    async def test_kb_exists_returns_helper_without_creating(self):
        """KB already exists → returns helper without calling create."""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        fake_helper = _fake_kb_helper()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(return_value=fake_helper)
        plugin.context.kb_manager.create_kb_if_not_exists = AsyncMock()

        store = SessionMemoryStore(plugin)
        result = await store._ensure_scope_kb("g_123")

        self.assertEqual(result, fake_helper)
        plugin.context.kb_manager.create_kb_if_not_exists.assert_not_called()

    async def test_kb_missing_creates_it_without_umo(self):
        """KB missing → calls create_kb_if_not_exists without error (umo=None)."""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(side_effect=[None, _fake_kb_helper()])
        plugin.context.kb_manager.create_kb_if_not_exists = AsyncMock()
        plugin.cfg.memory_kb_name = "self_evolution_memory"

        store = SessionMemoryStore(plugin)
        result = await store._ensure_scope_kb("g_456")

        self.assertIsNotNone(result)
        plugin.context.kb_manager.create_kb_if_not_exists.assert_called_once()
        call_args = plugin.context.kb_manager.create_kb_if_not_exists.call_args
        kb_name_arg = call_args.kwargs.get("kb_name") if call_args.kwargs else None
        self.assertIn("__scope__g_g_456", kb_name_arg or "")

    async def test_kb_missing_creates_it_with_umo(self):
        """KB missing with umo provided → passes umo to create_kb_if_not_exists."""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(side_effect=[None, _fake_kb_helper()])
        plugin.context.kb_manager.create_kb_if_not_exists = AsyncMock()

        store = SessionMemoryStore(plugin)
        result = await store._ensure_scope_kb("g_789", umo="test_umo")

        self.assertIsNotNone(result)
        plugin.context.kb_manager.create_kb_if_not_exists.assert_called_once()
        call_args = plugin.context.kb_manager.create_kb_if_not_exists.call_args
        self.assertEqual(call_args.kwargs.get("umo"), "test_umo")

    async def test_no_kb_manager_returns_none(self):
        """No kb_manager → returns None without error."""
        plugin = MagicMock(spec=["context"])
        del plugin.context

        store = SessionMemoryStore(plugin)
        result = await store._ensure_scope_kb("g_999")

        self.assertIsNone(result)


class SaveDailySummaryKbCreationTests(IsolatedAsyncioTestCase):
    """Tests for save_daily_summary using KB creation path."""

    async def test_save_daily_summary_works_when_kb_missing_and_created(self):
        """KB missing initially → _ensure_scope_kb creates it → summary saved."""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        created_helper = _fake_kb_helper()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(side_effect=[None, created_helper])
        plugin.context.kb_manager.create_kb_if_not_exists = AsyncMock()

        store = SessionMemoryStore(plugin)
        result = await store.save_daily_summary(
            scope_id="g_123",
            memory="这是一段测试总结",
            summary_date="2025-01-01",
        )

        plugin.context.kb_manager.create_kb_if_not_exists.assert_called_once()
        self.assertIn("已保存", result)


class SaveSessionEventKbCreationTests(IsolatedAsyncioTestCase):
    """Tests for save_session_event using KB creation path."""

    async def test_save_session_event_works_when_kb_missing_and_created(self):
        """KB missing initially → _ensure_scope_kb creates it → event saved."""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        created_helper = _fake_kb_helper()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(side_effect=[None, created_helper])
        plugin.context.kb_manager.create_kb_if_not_exists = AsyncMock()

        store = SessionMemoryStore(plugin)
        result = await store.save_session_event(
            scope_id="g_456",
            session_event={"content": "测试事件", "source": "test", "date": "2025-01-01"},
        )

        plugin.context.kb_manager.create_kb_if_not_exists.assert_called_once()
        self.assertTrue(result)


class ClearSummaryTests(IsolatedAsyncioTestCase):
    """Tests for clear_summary() selective deletion fix."""

    def _make_doc(self, doc_name: str, doc_id: str = "id_1"):
        return SimpleNamespace(doc_name=doc_name, doc_id=doc_id)

    async def test_clear_summary_deletes_only_memory_prefix(self):
        """只删除 memory_{scope_id}_ 开头的文档，保留 event_ 文档。"""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        fake_helper = _fake_kb_helper()
        fake_helper.list_documents = AsyncMock(
            return_value=[
                self._make_doc("memory_g_123_2025-01-01_1.txt", "id_mem_1"),
                self._make_doc("memory_g_123_2025-01-02_2.txt", "id_mem_2"),
                self._make_doc("event_g_123_2025-01-01_3.txt", "id_event_1"),
                self._make_doc("memory_g_456_2025-01-01_4.txt", "id_mem_3"),
            ]
        )
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(return_value=fake_helper)

        store = SessionMemoryStore(plugin)
        result = await store.clear_summary("g_123", confirm=True)

        self.assertEqual(result, "已删除 2 份总结")
        deleted_ids = [call.args[0] for call in fake_helper.delete_document.call_args_list]
        self.assertIn("id_mem_1", deleted_ids)
        self.assertIn("id_mem_2", deleted_ids)
        self.assertNotIn("id_event_1", deleted_ids)
        self.assertNotIn("id_mem_3", deleted_ids)

    async def test_clear_summary_ignores_other_scopes_memory(self):
        """只删除指定 scope 的 memory 文档，不管其他 scope 的。"""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        fake_helper = _fake_kb_helper()
        fake_helper.list_documents = AsyncMock(
            return_value=[
                self._make_doc("memory_g_999_2025-01-01_1.txt", "id_other_scope"),
            ]
        )
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(return_value=fake_helper)

        store = SessionMemoryStore(plugin)
        result = await store.clear_summary("g_123", confirm=True)

        self.assertEqual(result, "已删除 0 份总结")
        fake_helper.delete_document.assert_not_called()

    async def test_clear_summary_kb_not_exist_returns_gracefully(self):
        """KB 不存在 → 优雅返回，不崩。"""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(side_effect=Exception("not found"))

        store = SessionMemoryStore(plugin)
        result = await store.clear_summary("g_999", confirm=True)

        self.assertEqual(result, "知识库不存在")

    async def test_clear_summary_without_confirm_returns_cancel(self):
        """confirm=False → 返回操作已取消。"""
        store = MagicMock(spec=SessionMemoryStore)
        store._get_scope_kb_name = MagicMock()
        store._ensure_scope_kb = AsyncMock()

        result = await SessionMemoryStore(store).clear_summary("g_123", confirm=False)

        self.assertEqual(result, "操作已取消")

    async def test_clear_summary_private_scope(self):
        """private scope 的 summary 也能正确删除。"""
        plugin = MagicMock()
        plugin.context.kb_manager = MagicMock()
        fake_helper = _fake_kb_helper()
        fake_helper.list_documents = AsyncMock(
            return_value=[
                self._make_doc("memory_private_7001_2025-01-01_1.txt", "id_priv"),
                self._make_doc("event_private_7001_2025-01-01_2.txt", "id_priv_event"),
            ]
        )
        plugin.context.kb_manager.get_kb_by_name = AsyncMock(return_value=fake_helper)

        store = SessionMemoryStore(plugin)
        result = await store.clear_summary("private_7001", confirm=True)

        self.assertEqual(result, "已删除 1 份总结")
        deleted_ids = [call.args[0] for call in fake_helper.delete_document.call_args_list]
        self.assertIn("id_priv", deleted_ids)
        self.assertNotIn("id_priv_event", deleted_ids)
