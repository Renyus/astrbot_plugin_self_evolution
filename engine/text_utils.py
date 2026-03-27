import re


def clean_result_text(text: str) -> str:
    """清洗 LLM 输出中的换行符，替换为中文逗号，统一留白。

    规则：
    1. \\r\\n / \\r → \\n（统一换行符）
    2. 所有 \\n（不论多少个）→ ，（中文逗号）
    3. 首尾空白和多余逗号清除
    """
    if not text:
        return text
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n+", "，", text)
    text = text.strip()
    text = re.sub(r"^，+|，+$", "", text)
    text = re.sub(r"，+", "，", text)
    return text
