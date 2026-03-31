import re
from typing import Optional

from .speech_types import OutputResult


AI_STARTS = [
    "作为一个人工智能",
    "作为一个语言模型",
    "根据我的分析",
    "从客观角度来看",
    "从技术层面来说",
    "需要明确的是",
    "首先需要说明",
    "这是一个常见的问题",
    "这个问题涉及到",
]

GENERIC_EXPLANATORY = [
    "首先",
    "其次",
    "最后",
    "综上所述",
    "总的来说",
    "简单来说",
    "通俗地讲",
    "换句话说",
    "也就是说",
    "一方面",
    "另一方面",
]

TOOL_LIKE_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十\d]+[步点部分章]"),
    re.compile(r"^步骤[一二三四五六]"),
    re.compile(r"^下面是[具体详细]?"),
    re.compile(r"^请参考"),
    re.compile(r"^参考以下"),
    re.compile(r"^推荐以下"),
]

CONTEXT_FREE_STARTS = [
    "今天来聊聊",
    "给大家介绍",
    "我想说的是",
    "我来给大家",
    "让我们来",
    "接下来",
]

ECHO_PATTERNS = [
    re.compile(r"^没错，"),
    re.compile(r"^是的，"),
    re.compile(r"^对，"),
    re.compile(r"^确实"),
    re.compile(r"^同意"),
]


class OutputGuard:
    """输出审查层。

    按 text_mode 分流：
    - interject: 最严格，主动发言脱离上下文/无意义附和/解释腔强化
    - reply: 正常标准
    - disengage: 放宽长度，允许更短
    - correction: 允许略长，但仍禁工具味/AI味

    四种结果语义：PASS / DOWNGRADE_TO_EMOJI / DROP / RETRY_SHORTER
    RETRY_SHORTER 只在明确可执行场景保留。
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.cfg = plugin.cfg
        self._recent_texts: list[str] = []
        self._max_recent = 10
        self._persona_name: str = getattr(plugin, "persona_name", "黑塔")

    def check(self, text: str, decision) -> OutputResult:
        """审查输出文本。

        Returns:
            OutputResult with status PASS, RETRY_SHORTER, DOWNGRADE_TO_EMOJI, or DROP
        """
        if not text or not text.strip():
            return OutputResult(status=OutputResult.DROP, reason="空文本", fallback_action="emoji")

        stripped = text.strip()
        text_mode = getattr(decision, "text_mode", "reply")

        if self._check_action_only(stripped):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="纯动作描写", fallback_action="text")

        if self._check_too_many_newlines(stripped, text_mode):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="空行太多", fallback_action="text")

        if self._check_repetitive(stripped):
            return OutputResult(status=OutputResult.RETRY_SHORTER, reason="文本重复", fallback_action="emoji")

        if text_mode == "interject":
            if self._check_context_free_interject_strict(stripped):
                return OutputResult(status=OutputResult.DROP, reason="主动发言脱离上下文", fallback_action="emoji")
            if self._check_echo_starts_interject(stripped):
                return OutputResult(status=OutputResult.DROP, reason="无意义附和(主动)", fallback_action="emoji")
            if self._check_generic_explanatory_interject(stripped):
                return OutputResult(status=OutputResult.DROP, reason="泛解释腔(主动)", fallback_action="emoji")
            # interject 也走通用泛解释检查作为兆底，避免 strict 方法漏掉的 pattern
            if self._check_generic_explanatory(stripped, text_mode):
                return OutputResult(status=OutputResult.DROP, reason="泛解释腔(主动)", fallback_action="emoji")
        else:
            if self._check_context_free_interject(stripped, text_mode):
                return OutputResult(
                    status=OutputResult.DOWNGRADE_TO_EMOJI, reason="主动发言脱离上下文", fallback_action="emoji"
                )
            if self._check_echo_starts(stripped):
                return OutputResult(
                    status=OutputResult.DOWNGRADE_TO_EMOJI, reason="无意义附和", fallback_action="emoji"
                )
            if self._check_generic_explanatory(stripped, text_mode):
                return OutputResult(
                    status=OutputResult.DOWNGRADE_TO_EMOJI, reason="泛解释语气", fallback_action="emoji"
                )

        if self._check_tool_like(stripped):
            return OutputResult(status=OutputResult.DOWNGRADE_TO_EMOJI, reason="工具语气", fallback_action="emoji")

        if self._check_ai_voice(stripped):
            return OutputResult(status=OutputResult.DOWNGRADE_TO_EMOJI, reason="AI语气", fallback_action="emoji")

        if self._check_too_long(stripped, decision, text_mode):
            return OutputResult(
                status=OutputResult.RETRY_SHORTER,
                reason=f"超出最大长度({getattr(decision, 'max_chars', 200)})",
                fallback_action="text",
            )

        self._add_recent(stripped)
        return OutputResult(status=OutputResult.PASS, text=stripped)

    def _check_action_only(self, text: str) -> bool:
        action_pattern = re.compile(r"^【.*?】", re.IGNORECASE)
        lines = text.split("\n")
        if len(lines) == 1 and action_pattern.match(lines[0]):
            return True
        return False

    def _check_too_many_newlines(self, text: str, text_mode: str) -> bool:
        threshold = 1 if text_mode == "interject" else 2
        return text.count("\n\n") >= threshold

    def _check_repetitive(self, text: str) -> bool:
        text_lower = text.lower().strip()
        for recent in self._recent_texts:
            if recent.lower() == text_lower:
                return True
        if len(text) >= 20:
            half = len(text) // 2
            if text[:half] in text[half:]:
                return True
        if len(text) >= 10:
            quarter = len(text) // 4
            chunk = text[: quarter + 1]
            if text.count(chunk) >= 3:
                return True
        return False

    def _check_ai_voice(self, text: str) -> bool:
        text_lower = text.lower()
        for start in AI_STARTS:
            if text_lower.startswith(start):
                return True
        return False

    def _check_generic_explanatory(self, text: str, text_mode: str) -> bool:
        if text_mode == "disengage":
            return False
        lines = text.split("\n")
        if len(lines) == 1 and len(text) > 5:
            for pattern in GENERIC_EXPLANATORY:
                if text.startswith(pattern):
                    return True
        if text_mode == "interject" and len(text) > 40:
            for pattern in GENERIC_EXPLANATORY:
                if pattern in text[:30]:
                    return True
        elif len(text) > 80 and text.count("。") > 4:
            for pattern in GENERIC_EXPLANATORY:
                if pattern in text[:20]:
                    return True
        return False

    def _check_generic_explanatory_interject(self, text: str) -> bool:
        if len(text) > 20:
            for pattern in GENERIC_EXPLANATORY:
                if text.startswith(pattern):
                    return True
        if len(text) > 30:
            for phrase in ("总的来说", "综上所述", "简单来说", "换句话说"):
                if phrase in text[:30]:
                    return True
        return False

    def _check_tool_like(self, text: str) -> bool:
        for pattern in TOOL_LIKE_PATTERNS:
            if pattern.match(text.strip()):
                return True
        return False

    def _check_context_free_interject(self, text: str, text_mode: str) -> bool:
        if text_mode not in ("interject", "correction"):
            return False
        for start in CONTEXT_FREE_STARTS:
            if text.startswith(start):
                return True
        if len(text) > 20:
            for phrase in ("今天来", "我想说的", "让我来", "让我们来", "给大家介绍", "接下来"):
                if text.startswith(phrase):
                    return True
        return False

    def _check_context_free_interject_strict(self, text: str) -> bool:
        for start in CONTEXT_FREE_STARTS:
            if text.startswith(start):
                return True
        if len(text) > 15:
            for phrase in ("今天来", "我想说的", "让我来", "让我们来", "给大家介绍", "接下来"):
                if text.startswith(phrase):
                    return True
        return False

    def _check_echo_starts(self, text: str) -> bool:
        stripped = text.strip()
        for pattern in ECHO_PATTERNS:
            match = pattern.match(stripped)
            if match:
                rest = stripped[match.end() :]
                if len(rest) > 10:
                    return False
                return True
        return False

    def _check_echo_starts_interject(self, text: str) -> bool:
        stripped = text.strip()
        for pattern in ECHO_PATTERNS:
            match = pattern.match(stripped)
            if match:
                rest = stripped[match.end() :]
                if len(rest) > 5:
                    return False
                return True
        return False

    def _check_too_long(self, text: str, decision, text_mode: str) -> bool:
        max_chars = getattr(decision, "max_chars", 200)
        if text_mode == "disengage":
            # disengage 放宽正常长度限制，但设 500 字极端上限防止 LLM 失控
            return len(text) > 500
        if text_mode == "correction":
            max_chars = int(max_chars * 1.5)
        return len(text) > max_chars

    def _add_recent(self, text: str) -> None:
        self._recent_texts.append(text)
        if len(self._recent_texts) > self._max_recent:
            self._recent_texts.pop(0)

    def clear_recent(self) -> None:
        self._recent_texts.clear()
