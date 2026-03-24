from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from tests._helpers import load_engine_module

memory_types_mod = load_engine_module("memory_types")
MemoryQueryIntent = memory_types_mod.MemoryQueryIntent
MemoryQueryRequest = memory_types_mod.MemoryQueryRequest

qs_module = load_engine_module("memory_query_service")
MemoryQueryService = qs_module.MemoryQueryService


class TestQueryServiceDispatch(IsolatedAsyncioTestCase):
    def _make_plugin(self):
        cfg = SimpleNamespace(
            memory_debug_enabled=False,
            memory_query_fallback_enabled=True,
        )
        return SimpleNamespace(
            profile_summary_service=SimpleNamespace(
                get_structured_summary=AsyncMock(return_value="用户画像摘要"),
            ),
            session_memory_store=SimpleNamespace(
                get_summary_by_date=AsyncMock(return_value="昨天总结"),
                retrieve_events=AsyncMock(return_value=["事件1", "事件2"]),
                retrieve_summary=AsyncMock(return_value="KB检索结果"),
            ),
            get_user_messages_for_tool=AsyncMock(return_value=[{"text": "消息1"}, {"text": "消息2"}]),
            cfg=cfg,
        )

    async def test_dispatch_recent_context(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.RECENT_CONTEXT,
            limit=10,
        )
        with patch(
            "self_evolution_test_engine.context_injection.get_group_history", new_callable=AsyncMock
        ) as mock_history:
            mock_history.return_value = "群上下文"
            result = await service.query(req)

            self.assertEqual(result.intent, MemoryQueryIntent.RECENT_CONTEXT)
            self.assertEqual(result.source, "recent_context")
            self.assertEqual(result.text, "群上下文")

    async def test_dispatch_daily_summary(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.DAILY_SUMMARY,
            limit=3,
            date="yesterday",
        )
        result = await service.query(req)

        self.assertEqual(result.intent, MemoryQueryIntent.DAILY_SUMMARY)
        self.assertEqual(result.source, "daily_summary")
        self.assertEqual(result.text, "昨天总结")

    async def test_dispatch_session_event(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="会议",
            intent=MemoryQueryIntent.SESSION_EVENT,
            limit=5,
        )
        result = await service.query(req)

        self.assertEqual(result.intent, MemoryQueryIntent.SESSION_EVENT)
        self.assertEqual(result.source, "session_event")
        self.assertIn("事件1", result.text)
        self.assertEqual(result.hit_count, 2)

    async def test_dispatch_user_profile(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_PROFILE,
            limit=8,
        )
        result = await service.query(req)

        self.assertEqual(result.intent, MemoryQueryIntent.USER_PROFILE)
        self.assertEqual(result.source, "user_profile")
        self.assertEqual(result.text, "用户画像摘要")

    async def test_dispatch_user_message_history(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_MESSAGE_HISTORY,
            limit=30,
        )
        result = await service.query(req)

        self.assertEqual(result.intent, MemoryQueryIntent.USER_MESSAGE_HISTORY)
        self.assertEqual(result.source, "user_message_history")
        self.assertIn("消息1", result.text)
        self.assertEqual(result.hit_count, 2)

    async def test_dispatch_fallback_kb(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="相关话题",
            intent=MemoryQueryIntent.FALLBACK_KB,
            limit=5,
        )
        result = await service.query(req)

        self.assertEqual(result.intent, MemoryQueryIntent.FALLBACK_KB)
        self.assertEqual(result.source, "fallback_kb")
        self.assertEqual(result.text, "KB检索结果")

    async def test_dispatch_unknown_intent_returns_empty(self):
        plugin = self._make_plugin()
        service = MemoryQueryService(plugin)

        class UnknownIntent:
            pass

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=UnknownIntent(),
            limit=10,
        )
        result = await service.query(req)

        self.assertEqual(result.source, "unknown")
        self.assertEqual(result.text, "")


class TestQueryRecentContext(IsolatedAsyncioTestCase):
    async def test_returns_empty_for_private_scope(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="private_8001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.RECENT_CONTEXT,
            limit=10,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_calls_get_group_history_with_correct_args(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.RECENT_CONTEXT,
            limit=10,
        )

        with patch(
            "self_evolution_test_engine.context_injection.get_group_history", new_callable=AsyncMock
        ) as mock_history:
            mock_history.return_value = "群消息上下文文本"
            result = await service.query(req)

            mock_history.assert_awaited_once_with(plugin, "6001", count=10)
            self.assertEqual(result.text, "群消息上下文文本")
            self.assertEqual(result.hit_count, 1)

    async def test_empty_history_returns_zero_hit_count(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.RECENT_CONTEXT,
            limit=10,
        )

        with patch(
            "self_evolution_test_engine.context_injection.get_group_history", new_callable=AsyncMock
        ) as mock_history:
            mock_history.return_value = ""
            result = await service.query(req)

            self.assertEqual(result.text, "")
            self.assertEqual(result.hit_count, 0)


class TestQueryDailySummary(IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_memory_attribute(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.DAILY_SUMMARY,
            limit=3,
            date="yesterday",
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_delegates_to_memory_get_summary_by_date(self):
        mock_get_summary = AsyncMock(return_value="昨天群聊总结")
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(get_summary_by_date=mock_get_summary),
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.DAILY_SUMMARY,
            limit=3,
            date="yesterday",
        )

        result = await service.query(req)

        mock_get_summary.assert_awaited_once_with("6001", "yesterday")
        self.assertEqual(result.text, "昨天群聊总结")
        self.assertEqual(result.hit_count, 1)

    async def test_defaults_date_to_yesterday(self):
        mock_get_summary = AsyncMock(return_value="总结文本")
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(get_summary_by_date=mock_get_summary),
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.DAILY_SUMMARY,
            limit=3,
        )

        result = await service.query(req)

        mock_get_summary.assert_awaited_once_with("6001", "yesterday")
        self.assertEqual(result.hit_count, 1)

    async def test_zero_hit_count_when_no_summary(self):
        mock_get_summary = AsyncMock(return_value="")
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(get_summary_by_date=mock_get_summary),
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="",
            intent=MemoryQueryIntent.DAILY_SUMMARY,
            limit=3,
            date="2025-01-01",
        )

        result = await service.query(req)

        self.assertEqual(result.hit_count, 0)


class TestQuerySessionEvent(IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_memory_attribute(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="会议",
            intent=MemoryQueryIntent.SESSION_EVENT,
            limit=5,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_formats_events_as_dashed_lines(self):
        mock_retrieve = AsyncMock(return_value=["群里决定周日联机", "约好周三开会"])
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(retrieve_events=mock_retrieve),
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="会议",
            intent=MemoryQueryIntent.SESSION_EVENT,
            limit=5,
        )

        result = await service.query(req)

        mock_retrieve.assert_awaited_once_with("6001", "会议", 5)
        self.assertIn("- 群里决定周日联机", result.text)
        self.assertIn("- 约好周三开会", result.text)
        self.assertEqual(result.hit_count, 2)


class TestQueryUserProfile(IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_profile_attribute(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_PROFILE,
            limit=8,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_delegates_to_profile_get_structured_summary(self):
        mock_summary = AsyncMock(return_value="用户是一名程序员，喜欢游戏")
        plugin = SimpleNamespace(
            profile_summary_service=SimpleNamespace(get_structured_summary=mock_summary),
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_PROFILE,
            limit=8,
        )

        result = await service.query(req)

        mock_summary.assert_awaited_once_with("6001", "8001", max_items=8)
        self.assertEqual(result.text, "用户是一名程序员，喜欢游戏")
        self.assertEqual(result.hit_count, 1)


class TestQueryUserMessageHistory(IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_get_user_messages_method(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_MESSAGE_HISTORY,
            limit=30,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_delegates_and_formats_messages(self):
        mock_get_msgs = AsyncMock(
            return_value=[{"text": "消息内容A"}, {"text": "消息内容B"}],
        )
        plugin = SimpleNamespace(
            get_user_messages_for_tool=mock_get_msgs,
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_MESSAGE_HISTORY,
            limit=30,
        )

        result = await service.query(req)

        mock_get_msgs.assert_awaited_once_with(
            user_id="8001",
            group_id="6001",
            fetch_limit=30,
        )
        self.assertIn("- 消息内容A", result.text)
        self.assertIn("- 消息内容B", result.text)
        self.assertEqual(result.hit_count, 2)

    async def test_zero_hit_count_when_no_messages(self):
        mock_get_msgs = AsyncMock(return_value=[])
        plugin = SimpleNamespace(
            get_user_messages_for_tool=mock_get_msgs,
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="8001",
            query="",
            intent=MemoryQueryIntent.USER_MESSAGE_HISTORY,
            limit=30,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)


class TestQueryFallbackKB(IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_memory_attribute(self):
        plugin = SimpleNamespace()
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="相关话题",
            intent=MemoryQueryIntent.FALLBACK_KB,
            limit=5,
        )

        result = await service.query(req)

        self.assertEqual(result.text, "")
        self.assertEqual(result.hit_count, 0)

    async def test_delegates_to_memory_smart_retrieve(self):
        mock_smart = AsyncMock(return_value="知识库检索到的相关内容")
        cfg = SimpleNamespace(memory_query_fallback_enabled=True)
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(retrieve_summary=mock_smart),
            cfg=cfg,
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="相关话题",
            intent=MemoryQueryIntent.FALLBACK_KB,
            limit=5,
        )

        result = await service.query(req)

        mock_smart.assert_awaited_once_with("6001", "相关话题", 5)
        self.assertEqual(result.text, "知识库检索到的相关内容")
        self.assertEqual(result.hit_count, 1)

    async def test_zero_hit_count_when_retrieve_returns_empty(self):
        mock_smart = AsyncMock(return_value="")
        cfg = SimpleNamespace(memory_query_fallback_enabled=True)
        plugin = SimpleNamespace(
            session_memory_store=SimpleNamespace(retrieve_summary=mock_smart),
            cfg=cfg,
        )
        service = MemoryQueryService(plugin)

        req = MemoryQueryRequest(
            scope_id="6001",
            user_id="",
            query="无结果的话题",
            intent=MemoryQueryIntent.FALLBACK_KB,
            limit=5,
        )

        result = await service.query(req)

        self.assertEqual(result.hit_count, 0)
