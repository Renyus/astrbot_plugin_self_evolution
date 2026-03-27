from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock

from astrbot.core.message.components import Plain

from engine.text_utils import clean_result_text, should_clean_result


class ResultTextCleanTests(TestCase):
    def _clean(self, text: str) -> str:
        return clean_result_text(text)

    def test_single_newline_becomes_fullwidth_comma(self):
        result = self._clean("hello\nworld")
        self.assertEqual(result, "hello，world")

    def test_multiple_newlines_become_single_comma(self):
        self.assertEqual(self._clean("hello\n\nworld"), "hello，world")
        self.assertEqual(self._clean("first\n\n\nsecond"), "first，second")

    def test_mixed_newlines_all_collapsed(self):
        self.assertEqual(self._clean("A\n\n\nB"), "A，B")
        self.assertEqual(self._clean("A\n\n\n\n\nB"), "A，B")

    def test_windows_line_endings_normalized(self):
        self.assertEqual(self._clean("hello\r\nworld"), "hello，world")
        self.assertEqual(self._clean("hello\rworld"), "hello，world")

    def test_leading_trailing_whitespace_and_commas_stripped(self):
        self.assertEqual(self._clean("  hello  "), "hello")
        self.assertEqual(self._clean("\n\nhello\n\n"), "hello")
        self.assertEqual(self._clean("  hello，world  "), "hello，world")

    def test_no_newlines_unchanged(self):
        self.assertEqual(self._clean("helloworld"), "helloworld")

    def test_empty_string_returns_empty(self):
        self.assertEqual(self._clean(""), "")

    def test_fullwidth_comma_used_not_ascii(self):
        result = self._clean("hello\nworld")
        self.assertIn("，", result)
        self.assertNotIn(",", result)

    def test_multiline_article_collapsed(self):
        text = "article title\n\nparagraph one\n\nparagraph two"
        self.assertEqual(self._clean(text), "article title，paragraph one，paragraph two")


class CommandReplyGatingTests(TestCase):
    def _mock_event(self, group_id="123456", extra=None):
        event = MagicMock()
        event.get_group_id.return_value = group_id
        event.get_extra.side_effect = lambda k, **kw: (extra or {}).get(k, kw.get("default"))
        result = MagicMock()
        result.chain = []
        event.get_result.return_value = result
        return event, result, extra or {}

    def test_no_group_id_returns_false(self):
        """无群ID时 should_clean_result 返回 False。"""
        event, _, _ = self._mock_event(group_id=None)
        self.assertFalse(should_clean_result(event))

    def test_command_reply_flag_returns_false(self):
        """设置了 self_evolution_command_reply 标志时返回 False。"""
        event, _, _ = self._mock_event(group_id="123456", extra={"self_evolution_command_reply": True})
        self.assertFalse(should_clean_result(event))

    def test_group_chat_without_flag_returns_true(self):
        """群聊且未标记命令时返回 True。"""
        event, _, _ = self._mock_event(group_id="123456", extra={})
        self.assertTrue(should_clean_result(event))

    def test_private_chat_returns_false(self):
        """私聊（无 group_id）返回 False。"""
        event, _, _ = self._mock_event(group_id=None)
        self.assertFalse(should_clean_result(event))


class PlainComponentCleaningTests(TestCase):
    """验证 on_decorating_result 对 Plain 组件的清洗行为（无 mock self）。"""

    def _clean_chain(self, text: str, group_id="123456", extra=None) -> str:
        event = MagicMock()
        event.get_group_id.return_value = group_id
        event.get_extra.side_effect = lambda k, **kw: (extra or {}).get(k, kw.get("default"))

        result = MagicMock()
        plain = Plain(text)
        result.chain = [plain]
        event.get_result.return_value = result

        if not should_clean_result(event):
            return text

        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                comp.text = clean_result_text(comp.text)
        return result.chain[0].text

    def test_plain_text_cleaned_when_no_flag(self):
        self.assertEqual(self._clean_chain("hello\nworld", extra={}), "hello，world")

    def test_plain_text_preserved_when_flag_set(self):
        self.assertEqual(
            self._clean_chain("hello\nworld", extra={"self_evolution_command_reply": True}), "hello\nworld"
        )

    def test_plain_text_preserved_when_no_group(self):
        self.assertEqual(self._clean_chain("hello\nworld", group_id=None), "hello\nworld")
