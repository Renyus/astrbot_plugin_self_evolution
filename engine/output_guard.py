import re
from typing import Optional

from .speech_types import OutputResult


class OutputGuard:
    """输出审查层。

    生成之后，检查是否"像这个人"。
    不像就降级表情包。
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.cfg = plugin.cfg
        self._recent_texts: list[str] = []
        self._max_recent = 10

    def check(self, text: str, decision) -> OutputResult:
        """审查输出文本。

        Returns:
            OutputResult with status PASS, RETRY_SHORTER, DOWNGRADE_TO_EMOJI, or DROP
        """
        if not text or not text.strip():
            return OutputResult(status=OutputResult.DROP, reason="空文本", fallback_action="emoji")

        if self._check_action_only(text):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="纯动作描写", fallback_action="text")

        if self._check_too_many_newlines(text):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="空行太多", fallback_action="text")

        if self._check_repetitive(text):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="重复表达", fallback_action="text")

        if self._check_not_persona_voice(text):
            return OutputResult(status=OutputResult.DOWNGRADE_TO_EMOJI, reason="不像角色语气", fallback_action="emoji")

        if self._check_too_long(text, decision):
            return OutputResult(
                status=OutputResult.RETRY_SHORTER, reason=f"超出最大长度({decision.max_chars})", fallback_action="text"
            )

        self._add_recent(text)

        return OutputResult(status=OutputResult.PASS, text=text)

    def _check_action_only(self, text: str) -> bool:
        action_pattern = re.compile(r"^【.*?】", re.IGNORECASE)
        lines = text.strip().split("\n")
        if len(lines) == 1 and action_pattern.match(lines[0]):
            return True
        return False

    def _check_too_many_newlines(self, text: str) -> bool:
        return text.count("\n\n") >= 2

    def _check_repetitive(self, text: str) -> bool:
        text_lower = text.lower().strip()
        for recent in self._recent_texts:
            if recent.lower() == text_lower:
                return True
        if len(text) >= 20:
            half = len(text) // 2
            if text[:half] in text[half:]:
                return True
        return False

    def _check_not_persona_voice(self, text: str) -> bool:
        text_lower = text.lower()
        unnatural_starts = ["作为一个人工智能", "作为一个语言模型", "根据我的分析"]
        for start in unnatural_starts:
            if text_lower.startswith(start):
                return True
        return False

    def _check_too_long(self, text: str, decision) -> bool:
        max_chars = getattr(decision, "max_chars", 200)
        return len(text) > max_chars

    def _add_recent(self, text: str) -> None:
        self._recent_texts.append(text)
        if len(self._recent_texts) > self._max_recent:
            self._recent_texts.pop(0)

    def clear_recent(self) -> None:
        self._recent_texts.clear()
