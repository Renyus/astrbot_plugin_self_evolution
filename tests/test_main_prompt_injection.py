"""tests/test_main_prompt_injection.py - Prompt 注入层 builder 单元测试"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = "D:/skills/GD/astrbot_plugin_self_evolution"
sys.path.insert(0, ROOT)


class PromptContext:
    """本地复现 PromptContext（与 main.py 保持同步）"""

    def __init__(
        self,
        user_id: str,
        sender_name: str,
        group_id,
        scope_id: str,
        profile_scope_id: str,
        umo,
        msg_text: str,
        affinity: int,
        role_info: str,
        is_group: bool,
        quoted_info: str,
        ai_context_info: str,
        at_targets: list,
        at_info: str,
        has_reply: bool,
        has_at: bool,
        bot_id: str,
        event=None,
    ):
        self.user_id = user_id
        self.sender_name = sender_name
        self.group_id = group_id
        self.scope_id = scope_id
        self.profile_scope_id = profile_scope_id
        self.umo = umo
        self.msg_text = msg_text
        self.affinity = affinity
        self.role_info = role_info
        self.is_group = is_group
        self.quoted_info = quoted_info
        self.ai_context_info = ai_context_info
        self.at_targets = at_targets
        self.at_info = at_info
        self.has_reply = has_reply
        self.has_at = has_at
        self.bot_id = bot_id
        self.event = event


def _make_engine(**cfg_defaults):
    defaults = dict(
        inject_group_history=True,
        group_history_count=10,
        enable_profile_injection=True,
        enable_profile_fact_writeback=True,
        enable_kb_memory_recall=True,
        sticker_learning_enabled=False,
        inner_monologue_enabled=False,
        max_prompt_injection_length=2000,
        debug_log_enabled=False,
        surprise_enabled=True,
        surprise_boost_keywords="我错了|原来如此|没想到",
    )
    defaults.update(cfg_defaults)

    class _FakeEngine:
        def __init__(self):
            self.cfg = SimpleNamespace(**defaults)
            self.enable_profile_injection = defaults.get("enable_profile_injection", True)
            self.enable_kb_memory_recall = defaults.get("enable_kb_memory_recall", True)
            self.enable_profile_fact_writeback = defaults.get("enable_profile_fact_writeback", True)
            self.surprise_enabled = defaults.get("surprise_enabled", True)
            self.surprise_boost_keywords = defaults.get("surprise_boost_keywords", defaults["surprise_boost_keywords"])
            self.san_enabled = False
            self.persona_name = "Bot"

        def _should_inject_group_history(self, ctx):
            return bool(self.cfg.inject_group_history and ctx.group_id)

        def _should_inject_profile(self, ctx):
            return self.enable_profile_injection and (
                ((ctx.has_reply or ctx.has_at) and ctx.is_group) or not ctx.is_group
            )

        def _should_inject_kb_memory(self, ctx):
            return self.enable_kb_memory_recall

        def _should_inject_preference_hints(self, ctx):
            if not self.enable_profile_fact_writeback:
                return False
            triggers = [
                "我改名了",
                "我叫",
                "从今天起",
                "今后",
                "以后都",
                "我讨厌",
                "我不喜欢",
                "我喜欢",
                "我爱",
                "我决定",
                "从现在起",
                "开始喜欢",
                "开始讨厌",
                "以后不",
                "再也不",
                "从今往后",
            ]
            return any(t in ctx.msg_text for t in triggers)

        def _should_inject_surprise_detection(self, ctx):
            if not (self.surprise_enabled and self.surprise_boost_keywords):
                return False
            surprise_triggers = ["我错了", "原来如此", "没想到", "居然", "震惊"]
            return any(t in ctx.msg_text.lower() for t in surprise_triggers)

        def _build_identity_injection(self, ctx):
            parts = [
                f"- 发送者ID: {ctx.user_id}",
                f"- 发送者昵称: {ctx.sender_name}{ctx.role_info}",
                f"- 情感积分: {ctx.affinity}/100",
            ]
            if ctx.is_group:
                ctx_parts = []
                if ctx.quoted_info:
                    ctx_parts.append(ctx.quoted_info)
                if ctx.at_info:
                    ctx_parts.append(ctx.at_info)
                parts.append("- 来源：群聊")
                if ctx_parts:
                    parts.append(f"- 交互上下文: {' + '.join(ctx_parts)}")
            else:
                parts.append("- 来源：私聊")
            if ctx.ai_context_info:
                parts.append(ctx.ai_context_info)
            return "\n\n【内部参考信息 - 不要输出】\n" + "\n".join(parts) + "\n"

        def _apply_prompt_injections(self, req, parts):
            non_empty = [p for p in parts if p and p.strip()]
            injection = "".join(non_empty)
            max_len = self.cfg.max_prompt_injection_length
            if len(injection) > max_len:
                injection = injection[:max_len] + "\n\n[...内容已截断...]"
            req.system_prompt += injection

    return _FakeEngine()


class PromptContextTests(IsolatedAsyncioTestCase):
    """PromptContext dataclass 基本功能"""

    def test_prompt_context_accepts_all_fields(self):
        ctx = PromptContext(
            user_id="u1",
            sender_name="Alice",
            group_id="12345",
            scope_id="private_u1",
            profile_scope_id="private_u1",
            umo="qq:group:12345",
            msg_text="hello",
            affinity=80,
            role_info="（管理员）",
            is_group=True,
            quoted_info="> prev",
            ai_context_info="[AI:xxx]",
            at_targets=["bot"],
            at_info="提到了",
            has_reply=True,
            has_at=True,
            bot_id="99999",
            event=None,
        )
        self.assertEqual(ctx.user_id, "u1")
        self.assertEqual(ctx.affinity, 80)
        self.assertTrue(ctx.is_group)
        self.assertTrue(ctx.has_reply)
        self.assertTrue(ctx.has_at)


class IdentityInjectionTests(IsolatedAsyncioTestCase):
    """_build_identity_injection 测试"""

    def test_identity_injection_contains_sender_info(self):
        ctx = PromptContext(
            user_id="u1",
            sender_name="Alice",
            group_id="12345",
            scope_id="scope",
            profile_scope_id="scope",
            umo=None,
            msg_text="hello",
            affinity=80,
            role_info="",
            is_group=True,
            quoted_info="",
            ai_context_info="",
            at_targets=[],
            at_info="",
            has_reply=False,
            has_at=False,
            bot_id="99999",
            event=None,
        )
        engine = _make_engine()
        result = engine._build_identity_injection(ctx)
        self.assertIn("u1", result)
        self.assertIn("Alice", result)
        self.assertIn("情感积分", result)
        self.assertIn("群聊", result)

    def test_identity_injection_group_with_context(self):
        ctx = PromptContext(
            user_id="u1",
            sender_name="Bob",
            group_id="12345",
            scope_id="scope",
            profile_scope_id="scope",
            umo=None,
            msg_text="hello",
            affinity=50,
            role_info="（管理员）",
            is_group=True,
            quoted_info="> previous",
            ai_context_info="",
            at_targets=["bot"],
            at_info="提到了机器人",
            has_reply=True,
            has_at=True,
            bot_id="99999",
            event=None,
        )
        engine = _make_engine()
        result = engine._build_identity_injection(ctx)
        self.assertIn("Bob", result)
        self.assertIn("（管理员）", result)
        self.assertIn("previous", result)
        self.assertIn("提到了机器人", result)

    def test_identity_injection_private_chat(self):
        ctx = PromptContext(
            user_id="u1",
            sender_name="Alice",
            group_id=None,
            scope_id="private_u1",
            profile_scope_id="private_u1",
            umo=None,
            msg_text="hello",
            affinity=80,
            role_info="",
            is_group=False,
            quoted_info="",
            ai_context_info="",
            at_targets=[],
            at_info="",
            has_reply=False,
            has_at=False,
            bot_id="99999",
            event=None,
        )
        engine = _make_engine()
        result = engine._build_identity_injection(ctx)
        self.assertIn("私聊", result)


class ShouldInjectTests(IsolatedAsyncioTestCase):
    """_should_inject_* 判定函数测试"""

    def _ctx(self, **kwargs):
        defaults = dict(
            user_id="u1",
            sender_name="Alice",
            group_id="12345",
            scope_id="scope",
            profile_scope_id="scope",
            umo=None,
            msg_text="hello",
            affinity=80,
            role_info="",
            is_group=True,
            quoted_info="",
            ai_context_info="",
            at_targets=[],
            at_info="",
            has_reply=False,
            has_at=False,
            bot_id="99999",
            event=None,
        )
        defaults.update(kwargs)
        return PromptContext(**defaults)

    def test_should_inject_profile_true_when_group_with_reply(self):
        ctx = self._ctx(has_reply=True, has_at=False, is_group=True)
        engine = _make_engine(enable_profile_injection=True)
        self.assertTrue(engine._should_inject_profile(ctx))

    def test_should_inject_profile_true_when_group_with_at(self):
        ctx = self._ctx(has_reply=False, has_at=True, is_group=True)
        engine = _make_engine(enable_profile_injection=True)
        self.assertTrue(engine._should_inject_profile(ctx))

    def test_should_inject_profile_true_when_private(self):
        ctx = self._ctx(group_id=None, is_group=False, has_reply=False, has_at=False)
        engine = _make_engine(enable_profile_injection=True)
        self.assertTrue(engine._should_inject_profile(ctx))

    def test_should_inject_profile_false_when_switch_off(self):
        ctx = self._ctx(has_reply=True, is_group=True)
        engine = _make_engine(enable_profile_injection=False)
        self.assertFalse(engine._should_inject_profile(ctx))

    def test_should_inject_group_history_true_when_config_on_and_group(self):
        ctx = self._ctx(group_id="12345", is_group=True)
        engine = _make_engine(inject_group_history=True)
        self.assertTrue(engine._should_inject_group_history(ctx))

    def test_should_inject_group_history_false_when_no_group(self):
        ctx = self._ctx(group_id=None, is_group=False)
        engine = _make_engine(inject_group_history=True)
        self.assertFalse(engine._should_inject_group_history(ctx))

    def test_should_inject_group_history_false_when_config_off(self):
        ctx = self._ctx(group_id="12345", is_group=True)
        engine = _make_engine(inject_group_history=False)
        self.assertFalse(engine._should_inject_group_history(ctx))

    def test_should_inject_kb_memory_respects_switch(self):
        ctx = self._ctx()
        engine_on = _make_engine(enable_kb_memory_recall=True)
        engine_off = _make_engine(enable_kb_memory_recall=False)
        self.assertTrue(engine_on._should_inject_kb_memory(ctx))
        self.assertFalse(engine_off._should_inject_kb_memory(ctx))


class BehaviorHintsTests(IsolatedAsyncioTestCase):
    """行为提示判定函数测试"""

    def _ctx(self, msg_text="hello", **kwargs):
        defaults = dict(
            user_id="u1",
            sender_name="Alice",
            group_id="12345",
            scope_id="scope",
            profile_scope_id="scope",
            umo=None,
            msg_text=msg_text,
            affinity=80,
            role_info="",
            is_group=True,
            quoted_info="",
            ai_context_info="",
            at_targets=[],
            at_info="",
            has_reply=False,
            has_at=False,
            bot_id="99999",
            event=None,
        )
        defaults.update(kwargs)
        return PromptContext(**defaults)

    def test_preference_hints_triggered(self):
        ctx = self._ctx(msg_text="我决定以后都叫我新名字了")
        engine = _make_engine(enable_profile_fact_writeback=True)
        self.assertTrue(engine._should_inject_preference_hints(ctx))

    def test_preference_hints_not_triggered_when_switch_off(self):
        ctx = self._ctx(msg_text="我决定以后都叫我新名字了")
        engine = _make_engine(enable_profile_fact_writeback=False)
        self.assertFalse(engine._should_inject_preference_hints(ctx))

    def test_surprise_detection_triggered(self):
        ctx = self._ctx(msg_text="我错了，原来如此！")
        engine = _make_engine(surprise_enabled=True, surprise_boost_keywords="我错了|原来如此|没想到")
        self.assertTrue(engine._should_inject_surprise_detection(ctx))

    def test_surprise_detection_not_triggered_when_disabled(self):
        ctx = self._ctx(msg_text="我错了，原来如此！")
        engine = _make_engine(surprise_enabled=False)
        self.assertFalse(engine._should_inject_surprise_detection(ctx))


class ApplyPromptInjectionsTests(IsolatedAsyncioTestCase):
    """_apply_prompt_injections 测试"""

    def test_concatenates_parts(self):
        engine = _make_engine(max_prompt_injection_length=2000)
        req = SimpleNamespace(system_prompt="BASE")
        parts = ["\n\n[Identity]\nuser:u1\n", "\n\n[Memory]\nfact1\n", ""]
        engine._apply_prompt_injections(req, parts)
        self.assertIn("BASE", req.system_prompt)
        self.assertIn("[Identity]", req.system_prompt)
        self.assertIn("[Memory]", req.system_prompt)

    def test_truncates_long_injection(self):
        engine = _make_engine(max_prompt_injection_length=50)
        req = SimpleNamespace(system_prompt="")
        parts = ["x" * 200]
        engine._apply_prompt_injections(req, parts)
        self.assertIn("[...内容已截断...]", req.system_prompt)
        self.assertLess(len(req.system_prompt), 200)
