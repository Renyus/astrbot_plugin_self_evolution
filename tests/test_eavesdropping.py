from __future__ import annotations

import time
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
            target_scopes=[],
            group_history_count=5,
            interject_cooldown=0,
            interject_silence_timeout=0,
            interject_min_msg_count=1,
            interject_local_filter_enabled=False,
            interject_require_at=require_at,
            interject_analyze_count=5,
            interject_urgency_threshold=80,
            interject_dry_run=False,
            interject_random_bypass_rate=0.5,
            interject_trigger_probability=1.0,
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


class EavesdroppingEligibilityTests(IsolatedAsyncioTestCase):
    """单元测试：_evaluate_eligibility 的各条路径"""

    def _make_engine(self, **cfg_overrides):
        defaults = dict(
            interject_cooldown=30,
            interject_silence_timeout=15,
            interject_min_msg_count=5,
            interject_require_at=True,
            interject_local_filter_enabled=False,
            interject_urgency_threshold=80,
            interject_dry_run=False,
            interject_trigger_probability=1.0,
        )
        defaults.update(cfg_overrides)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(**defaults),
            _interject_history={},
        )
        engine = EavesdroppingEngine(plugin)
        return engine

    def test_eligibility_rejects_empty_messages(self):
        engine = self._make_engine()
        result = engine._evaluate_eligibility("12345", [], None, 0.0, "bot")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "L1_NO_MESSAGES")

    def test_eligibility_rejects_bot_self_message(self):
        engine = self._make_engine()
        msg = {"time": 1000, "message_seq": 10, "sender": {"user_id": "bot"}}
        result = engine._evaluate_eligibility("12345", [msg], 10, 1000.0, "bot")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "L1_BOT_SELF_MSG")

    def test_eligibility_rejects_in_cooldown(self):
        engine = self._make_engine(interject_cooldown=30)
        engine._interject_history["12345"] = {"last_time": time.time() - 60, "last_msg_seq": 5}
        msg = {"time": 1000, "message_seq": 10, "sender": {"user_id": "user1"}}
        result = engine._evaluate_eligibility("12345", [msg], 10, 1000.0, "other_bot")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "L2_COOLDOWN")

    def test_eligibility_rejects_silence_not_met(self):
        engine = self._make_engine(interject_silence_timeout=15, interject_cooldown=0)
        msg = {"time": time.time() - 5, "message_seq": 10, "sender": {"user_id": "user1"}}
        result = engine._evaluate_eligibility("12345", [msg], 10, time.time() - 5, "bot")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "L2_SILENCE")

    def test_eligibility_rejects_insufficient_new_messages(self):
        engine = self._make_engine(interject_min_msg_count=5)
        engine._interject_history["12345"] = {"last_time": 0, "last_msg_seq": 100}
        msg = {"time": 1000, "message_seq": 5, "sender": {"user_id": "user1"}}
        result = engine._evaluate_eligibility("12345", [msg], 5, 1000.0, "bot")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "L3_MSG_COUNT")

    def test_eligibility_passes_all_checks(self):
        engine = self._make_engine(interject_min_msg_count=2, interject_silence_timeout=1, interject_cooldown=0)
        engine._interject_history["12345"] = {"last_time": 0, "last_msg_seq": None}
        msg1 = {"time": time.time() - 10, "message_seq": 10, "sender": {"user_id": "user1"}}
        msg2 = {"time": time.time() - 20, "message_seq": 9, "sender": {"user_id": "user2"}}
        result = engine._evaluate_eligibility("12345", [msg1, msg2], 10, time.time() - 10, "bot")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, "ELIGIBLE")
        self.assertEqual(result.new_message_count, 2)


class EavesdroppingGateTests(IsolatedAsyncioTestCase):
    """单元测试：_apply_final_gate 的各条路径"""

    def _make_engine(self, **cfg_overrides):
        cfg = dict(
            interject_urgency_threshold=80,
            interject_dry_run=False,
            interject_trigger_probability=0.5,
        )
        cfg.update(cfg_overrides)
        plugin = SimpleNamespace(cfg=SimpleNamespace(**cfg))
        engine = EavesdroppingEngine(plugin)
        return engine

    def test_gate_rejects_urgency_below_threshold(self):
        engine = self._make_engine(interject_urgency_threshold=80)
        decision = eavesdropping_module.InterjectDecision(True, 50, "reason", "response")
        gate = engine._apply_final_gate("12345", decision, 10)
        self.assertFalse(gate.proceed)
        self.assertEqual(gate.reason_code, "GATE_THRESHOLD")

    def test_gate_rejects_no_suggested_response(self):
        engine = self._make_engine(interject_urgency_threshold=80)
        decision = eavesdropping_module.InterjectDecision(True, 90, "reason", "")
        gate = engine._apply_final_gate("12345", decision, 10)
        self.assertFalse(gate.proceed)
        self.assertEqual(gate.reason_code, "GATE_NO_RESPONSE")

    def test_gate_rejects_dry_run(self):
        engine = self._make_engine(interject_urgency_threshold=80, interject_dry_run=True)
        decision = eavesdropping_module.InterjectDecision(True, 90, "reason", "hello")
        gate = engine._apply_final_gate("12345", decision, 10)
        self.assertFalse(gate.proceed)
        self.assertEqual(gate.reason_code, "GATE_DRY_RUN")

    def test_gate_passes_all_checks(self):
        engine = self._make_engine(
            interject_urgency_threshold=80, interject_dry_run=False, interject_trigger_probability=1.0
        )
        decision = eavesdropping_module.InterjectDecision(True, 90, "reason", "hello")
        gate = engine._apply_final_gate("12345", decision, 10)
        self.assertTrue(gate.proceed)
        self.assertEqual(gate.reason_code, "GATE_PASS")


class EavesdroppingStateUpdateTests(IsolatedAsyncioTestCase):
    """单元测试：_finalize_interject_state 的两种情况"""

    def _make_engine(self):
        plugin = SimpleNamespace(cfg=SimpleNamespace())
        engine = EavesdroppingEngine(plugin)
        return engine

    def test_finalize_updates_cursor_on_no_interject(self):
        engine = self._make_engine()
        engine._interject_history["12345"] = {"last_time": 1000, "last_msg_seq": 5}
        engine._finalize_interject_state("12345", 10, did_interject=False)
        self.assertEqual(engine._interject_history["12345"]["last_msg_seq"], 10)
        self.assertEqual(engine._interject_history["12345"]["last_time"], 1000)

    def test_finalize_sets_interject_time_on_interject(self):
        engine = self._make_engine()
        engine._interject_history["12345"] = {"last_time": 1000, "last_msg_seq": 5}
        before = time.time()
        engine._finalize_interject_state("12345", 10, did_interject=True)
        self.assertEqual(engine._interject_history["12345"]["last_msg_seq"], 10)
        self.assertGreaterEqual(engine._interject_history["12345"]["last_time"], before)


class EavesdroppingJudgeTests(IsolatedAsyncioTestCase):
    """单元测试：_judge_interjection 的各条路径"""

    def _make_engine(self, provider):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                interject_analyze_count=5,
                interject_urgency_threshold=80,
            ),
            context=SimpleNamespace(
                get_using_provider=MagicMock(return_value=provider),
                platform_manager=SimpleNamespace(
                    platform_insts=[
                        SimpleNamespace(
                            get_client=lambda: SimpleNamespace(call_action=AsyncMock(return_value={"user_id": "99999"}))
                        )
                    ]
                ),
            ),
            get_group_umo=MagicMock(return_value="qq:group:12345"),
        )
        engine = EavesdroppingEngine(plugin)
        return engine

    async def test_judge_returns_invalid_json_on_bad_response(self):
        provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="{bad json")))
        engine = self._make_engine(provider)
        engine._get_interject_prompt = AsyncMock(return_value="system prompt")
        result = await engine._judge_interjection("12345", ["msg1"], 5, None)
        self.assertFalse(result.should_interject)
        self.assertEqual(result.reason, "LLM 返回无法解析 JSON")

    async def test_judge_returns_llm_fail_on_exception(self):
        async def raise_err(*args, **kwargs):
            raise RuntimeError("network error")

        provider = SimpleNamespace(text_chat=raise_err)
        engine = self._make_engine(provider)
        result = await engine._judge_interjection("12345", ["msg1"], 5, None)
        self.assertFalse(result.should_interject)
        self.assertIn("LLM 调用失败", result.reason)
