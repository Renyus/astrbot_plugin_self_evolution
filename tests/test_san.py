from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import ROOT, load_module_from_path

san_module = load_module_from_path("cognition_san", ROOT / "cognition" / "san.py")
SANSystem = san_module.SANSystem


class SANSystemTests(IsolatedAsyncioTestCase):
    async def test_llm_analyze_uses_group_umo_for_provider_lookup(self):
        provider = SimpleNamespace(
            text_chat=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text='{"activity":"high","emotion":"positive","has_drama":false,"summary":"ok"}'
                )
            )
        )
        get_using_provider = MagicMock(return_value=provider)
        plugin = SimpleNamespace(
            context=SimpleNamespace(get_using_provider=get_using_provider),
            cfg=SimpleNamespace(),
        )
        san = SANSystem(plugin)

        result = await san._llm_analyze(["Alice: hi"], umo="qq:group:7001")

        get_using_provider.assert_called_once_with(umo="qq:group:7001")
        self.assertEqual(result["activity"], "high")

    async def test_analyze_group_passes_cached_group_umo(self):
        plugin = SimpleNamespace(
            get_group_umo=MagicMock(return_value="qq:group:7001"),
            cfg=SimpleNamespace(),
        )
        san = SANSystem(plugin)
        san._fetch_group_messages = AsyncMock(return_value=["Alice: hi"])
        san._llm_analyze = AsyncMock(return_value={"activity": "medium", "emotion": "neutral", "has_drama": False})
        san._calculate_san_change = MagicMock(return_value=2)

        result = await san._analyze_group("7001")

        plugin.get_group_umo.assert_called_once_with("7001")
        san._llm_analyze.assert_awaited_once_with(["Alice: hi"], umo="qq:group:7001")
        self.assertEqual(result, 2)
