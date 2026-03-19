from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

MetaInfra = load_engine_module("meta_infra").MetaInfra


class MetaInfraTests(IsolatedAsyncioTestCase):
    async def test_run_debate_uses_umo_for_provider_lookup(self):
        provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="[PASS] ok")))
        get_using_provider = MagicMock(return_value=provider)
        plugin = SimpleNamespace(
            context=SimpleNamespace(get_using_provider=get_using_provider),
            cfg=SimpleNamespace(
                debate_rounds=1,
                debate_criteria="quality",
                debate_agents=[{"name": "Reviewer", "system_prompt": "review strictly"}],
                debate_system_prompt="review strictly",
            ),
        )
        meta = MetaInfra(plugin)

        result = await meta._run_debate("print('ok')", "desc", "main.py", umo="qq:group:meta-1")

        get_using_provider.assert_called_once_with(umo="qq:group:meta-1")
        self.assertTrue(result["passed"])
