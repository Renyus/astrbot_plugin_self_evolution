from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

eavesdropping_module = load_engine_module("eavesdropping")
EavesdroppingEngine = eavesdropping_module.EavesdroppingEngine


class EavesdroppingInterjectTests(IsolatedAsyncioTestCase):
    def _build_engine(self, *, require_at: bool):
        provider = SimpleNamespace(
            text_chat=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text='{"urgency_score": 95, "should_interject": true, "reason": "interesting", "suggested_response": "hello"}'
                )
            )
        )
        bot = SimpleNamespace()

        async def call_action(action, **kwargs):
            if action == "get_group_msg_history":
                return {
                    "messages": [
                        {
                            "time": 1,
                            "message_seq": 101,
                            "sender": {"user_id": "20001", "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "hello"}}],
                        }
                    ]
                }
            if action == "get_login_info":
                return {"user_id": "99999"}
            raise AssertionError(f"Unexpected action: {action}")

        bot.call_action = AsyncMock(side_effect=call_action)
        platform = SimpleNamespace(get_client=lambda: bot, client_self_id="99999")
        get_using_provider = MagicMock(return_value=provider)
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[platform]),
            get_using_provider=get_using_provider,
        )
        cfg = SimpleNamespace(
            target_group_scopes=[],
            group_history_count=5,
            interject_cooldown=0,
            interject_silence_timeout=0,
            interject_min_msg_count=1,
            interject_local_filter_enabled=False,
            interject_require_at=require_at,
            interject_analyze_count=5,
            interject_urgency_threshold=80,
            interject_dry_run=False,
        )
        plugin = SimpleNamespace(
            context=context,
            cfg=cfg,
            _shut_until_by_group={},
            get_group_umo=MagicMock(return_value="qq:group:12345"),
        )
        engine = EavesdroppingEngine(plugin)
        engine._get_interject_prompt = AsyncMock(return_value="prompt")
        engine._do_interject = AsyncMock()
        return engine, provider, get_using_provider

    async def test_interject_skips_without_at_when_gate_enabled(self):
        engine, provider, get_using_provider = self._build_engine(require_at=True)
        eavesdropping_module.parse_message_chain = AsyncMock(return_value="Alice: hello")

        await engine.interject_check_group("12345")

        provider.text_chat.assert_not_called()
        get_using_provider.assert_not_called()
        engine._do_interject.assert_not_called()
        self.assertEqual(engine._interject_history["12345"]["last_msg_seq"], 101)

    async def test_interject_can_continue_without_at_when_gate_disabled(self):
        engine, provider, get_using_provider = self._build_engine(require_at=False)
        eavesdropping_module.parse_message_chain = AsyncMock(return_value="Alice: hello")

        await engine.interject_check_group("12345")

        get_using_provider.assert_called_once_with(umo="qq:group:12345")
        engine._get_interject_prompt.assert_awaited_once_with(umo="qq:group:12345")
        provider.text_chat.assert_awaited_once()
        engine._do_interject.assert_awaited_once_with(
            "12345",
            "hello",
            [
                {
                    "time": 1,
                    "message_seq": 101,
                    "sender": {"user_id": "20001", "nickname": "Alice"},
                    "message": [{"type": "text", "data": {"text": "hello"}}],
                }
            ],
        )

    async def test_get_interject_prompt_uses_cached_umo_for_persona_lookup(self):
        persona_manager = SimpleNamespace(get_default_persona_v3=AsyncMock(return_value={"prompt": "persona prompt"}))
        plugin = SimpleNamespace(
            context=SimpleNamespace(persona_manager=persona_manager),
            persona_name="Bot",
            _prompts_injection={},
        )

        engine = EavesdroppingEngine(plugin)

        prompt = await engine._get_interject_prompt(umo="qq:group:session-1")

        persona_manager.get_default_persona_v3.assert_awaited_once_with(umo="qq:group:session-1")
        self.assertIn("persona prompt", prompt)

    async def test_interject_gracefully_skips_invalid_json_response(self):
        engine, provider, get_using_provider = self._build_engine(require_at=False)
        provider.text_chat = AsyncMock(return_value=SimpleNamespace(completion_text="{bad json}"))
        eavesdropping_module.parse_message_chain = AsyncMock(return_value="Alice: hello")

        await engine.interject_check_group("12345")

        get_using_provider.assert_called_once_with(umo="qq:group:12345")
        engine._do_interject.assert_not_called()
        self.assertEqual(engine._interject_history["12345"]["last_msg_seq"], 101)
