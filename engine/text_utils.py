import re


def clean_result_text(text: str) -> str:
    """清洗 LLM 输出中的换行符，统一留白。

    规则：
    1. \r\n / \r → \n（统一换行符）
    2. 句末标点（。！？）后的换行直接删除
    3. 其余换行（不论多少个）→ ，（中文逗号）
    4. 首尾空白和多余逗号清除
    """
    if not text:
        return text
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"([。！？])\n+", r"\1", text)
    text = re.sub(r"\n+", "，", text)
    text = text.strip()
    text = re.sub(r"^，+|，+$", "", text)
    text = re.sub(r"，+", "，", text)
    return text


def should_clean_result(event) -> bool:
    """判断 on_decorating_result 是否应对该事件执行文本清洗。

    条件（需同时满足）：
    1. 群聊消息（get_group_id 有返回值）
    2. 未被标记为命令回复（self_evolution_command_reply 未设置）
    """
    if not event.get_group_id():
        return False
    if event.get_extra("self_evolution_command_reply"):
        return False
    return True
