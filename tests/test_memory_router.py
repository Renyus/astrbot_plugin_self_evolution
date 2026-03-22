from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_module_from_path
from pathlib import Path

router_module = load_module_from_path(
    "memory_router", Path(__file__).resolve().parents[1] / "engine" / "memory_router.py"
)

MemoryRouter = router_module.MemoryRouter
MemoryTarget = router_module.MemoryTarget


class MemoryRouterTests(IsolatedAsyncioTestCase):
    def _make_plugin(self):
        return SimpleNamespace(
            profile=SimpleNamespace(
                upsert_fact=AsyncMock(return_value=True),
            ),
            memory=SimpleNamespace(
                save_session_event=AsyncMock(return_value=True),
            ),
        )

    def test_classify_session_event_routes_to_kb(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("群里决定周日晚上8点联机")

        self.assertEqual(decision.target, MemoryTarget.KNOWLEDGE_BASE)
        self.assertEqual(decision.fact_type, "session_event")

    def test_classify_decision_routes_to_kb(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("我们约定了明天开会讨论项目进度")

        self.assertEqual(decision.target, MemoryTarget.KNOWLEDGE_BASE)

    def test_classify_reflection_hint_returns_drop(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("回答这个人要更简洁一些")

        self.assertEqual(decision.target, MemoryTarget.REFLECTION_HINT)

    def test_classify_misunderstanding_returns_reflection_hint(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("上次我误解了他的语气")

        self.assertEqual(decision.target, MemoryTarget.REFLECTION_HINT)

    def test_classify_preference_routes_to_profile(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("他喜欢玩 Galgame")

        self.assertEqual(decision.target, MemoryTarget.PROFILE)
        self.assertEqual(decision.fact_type, "preference")

    def test_classify_identity_routes_to_profile(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        content = "用户是一名程序员"

        decision = router.classify(content)

        self.assertEqual(decision.target, MemoryTarget.PROFILE)
        self.assertEqual(decision.fact_type, "identity")

    def test_classify_trait_routes_to_profile(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("他说话很简洁直接")

        self.assertEqual(decision.target, MemoryTarget.PROFILE)
        self.assertEqual(decision.fact_type, "trait")

    def test_classify_explicit_fact_type_uses_it(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("some content", fact_type="preference")

        self.assertEqual(decision.target, MemoryTarget.PROFILE)
        self.assertEqual(decision.fact_type, "preference")

    def test_classify_user_preference_category(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        decision = router.classify("some content", category="user_preference")

        self.assertEqual(decision.target, MemoryTarget.PROFILE)
        self.assertEqual(decision.fact_type, "preference")

    async def test_write_routes_preference_to_profile(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        result = await router.write(
            content="喜欢玩 Galgame",
            scope_id="6001",
            user_id="8001",
            category="user_profile",
            fact_type="preference",
            source="test",
        )

        plugin.profile.upsert_fact.assert_awaited_once()
        plugin.memory.save_session_event.assert_not_awaited()
        self.assertIn("画像", result)

    async def test_write_routes_session_event_to_kb(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        result = await router.write(
            content="群里决定周日联机",
            scope_id="6001",
            user_id="8001",
            category="session_event",
            source="test",
        )

        plugin.memory.save_session_event.assert_awaited_once()
        self.assertIn("知识库", result)

    async def test_write_reflection_hint_returns_non_persist(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        result = await router.write(
            content="回答时要注意简洁",
            scope_id="6001",
            user_id="8001",
            category="user_profile",
            source="test",
        )

        plugin.profile.upsert_fact.assert_not_awaited()
        plugin.memory.save_session_event.assert_not_awaited()
        self.assertIn("不持久化", result)

    def test_auto_detect_fact_type_identity(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        self.assertEqual(router._auto_detect_fact_type("他是学生"), "identity")
        self.assertEqual(router._auto_detect_fact_type("工作在互联网公司"), "identity")

    def test_auto_detect_fact_type_preference(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        self.assertEqual(router._auto_detect_fact_type("喜欢喝咖啡"), "preference")
        self.assertEqual(router._auto_detect_fact_type("讨厌被嘲笑"), "preference")

    def test_auto_detect_fact_type_trait(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        self.assertEqual(router._auto_detect_fact_type("说话很直接"), "trait")
        self.assertEqual(router._auto_detect_fact_type("性格内向"), "trait")

    def test_auto_detect_fact_type_default(self):
        plugin = self._make_plugin()
        router = MemoryRouter(plugin)

        self.assertEqual(router._auto_detect_fact_type("今天群里聊了游戏"), "recent_update")
