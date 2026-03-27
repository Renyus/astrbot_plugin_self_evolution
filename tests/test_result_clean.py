from __future__ import annotations

import re
from unittest import TestCase


def _clean_result_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\r\n?", r"\n", text)
    text = re.sub(r"\n+", "，", text)
    text = text.strip()
    text = re.sub(r"^，+|，+$", "", text)
    text = re.sub(r"，+", "，", text)
    return text


FULLWIDTH_COMMA = "，"
COMMA = ","


class ResultTextCleanTests(TestCase):
    def _clean(self, text: str) -> str:
        return _clean_result_text(text)

    def test_single_newline_becomes_fullwidth_comma(self):
        result = self._clean("hello\nworld")
        self.assertEqual(result, f"hello{FULLWIDTH_COMMA}world")

    def test_multiple_newlines_become_single_comma(self):
        self.assertEqual(self._clean("hello\n\nworld"), f"hello{FULLWIDTH_COMMA}world")
        self.assertEqual(self._clean("first\n\n\nsecond"), f"first{FULLWIDTH_COMMA}second")

    def test_mixed_newlines_all_collapsed(self):
        self.assertEqual(self._clean("A\n\n\nB"), f"A{FULLWIDTH_COMMA}B")
        self.assertEqual(self._clean("A\n\n\n\n\nB"), f"A{FULLWIDTH_COMMA}B")

    def test_windows_line_endings_normalized(self):
        self.assertEqual(self._clean("hello\r\nworld"), f"hello{FULLWIDTH_COMMA}world")
        self.assertEqual(self._clean("hello\rworld"), f"hello{FULLWIDTH_COMMA}world")

    def test_leading_trailing_whitespace_and_commas_stripped(self):
        self.assertEqual(self._clean("  hello  "), "hello")
        self.assertEqual(self._clean("\n\nhello\n\n"), "hello")
        self.assertEqual(self._clean(f"  hello{FULLWIDTH_COMMA}world  "), f"hello{FULLWIDTH_COMMA}world")

    def test_no_newlines_unchanged(self):
        self.assertEqual(self._clean("helloworld"), "helloworld")

    def test_empty_string_returns_empty(self):
        self.assertEqual(self._clean(""), "")
        self.assertEqual(self._clean(None), None)

    def test_fullwidth_comma_used_not_ascii(self):
        result = self._clean("hello\nworld")
        self.assertIn(FULLWIDTH_COMMA, result)
        self.assertNotIn(COMMA, result)

    def test_multiline_article_collapsed(self):
        text = "article title\n\nparagraph one\n\nparagraph two"
        self.assertEqual(
            self._clean(text), f"article title{FULLWIDTH_COMMA}paragraph one{FULLWIDTH_COMMA}paragraph two"
        )
