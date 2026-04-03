"""
Persona Sim Injection - snapshot 转 prompt 片段

只做"翻译"，不存状态，不过滤（过滤在上层或 engine 做）。

风格目标：心理旁白，而不是状态报表。
"""

from .persona_sim_types import EventType


def _build_recent_context(snapshot) -> str:
    """从 snapshot 中提取最近的显著事件，用于近因感注入。

    风格：像人在心里怎么想这件事，而不是描述事件本身。
    会根据 mood 对齐，避免矛盾。
    """
    recent = snapshot.recent_events
    if not recent:
        return ""

    interaction_events = [e for e in recent if e.event_type == EventType.INTERACTION]
    if not interaction_events:
        return ""

    state = snapshot.state
    last = interaction_events[-1]
    quality = None
    mode = getattr(last, "interaction_mode", "")
    outcome = getattr(last, "interaction_outcome", "")
    for c in last.causes:
        if c.startswith("quality="):
            quality = c.split("=")[1]
            break

    if quality == "bad":
        if state.mood < 60:
            if outcome == "missed" and mode == "active":
                return "刚主动说了话但被冷落，还有点堵着"
            return "刚才那下有点受挫"
        return ""
    elif quality == "awkward":
        if state.mood < 55:
            return "那通聊得有点别扭"
        return ""
    elif quality == "good":
        if state.mood > 55:
            if outcome == "connected" and mode == "active":
                return "刚才那通聊得挺顺的，还想继续"
            return "最近聊得挺开心的"
        return ""
    elif quality == "relief":
        if state.mood > 50:
            return "终于有人把话接上了，轻松了不少"
        return ""
    elif quality == "brief":
        return "就那么一来一回，没说上什么"
    elif quality == "normal":
        return "最近有和人说话"

    return ""


def _build_top_effect_desc(snapshot) -> str:
    """提取最主导的 effect，用感受型语言描述（而非 effect 名字）。"""
    active = snapshot.active_effects
    if not active:
        return ""
    debuffs = [e for e in active if e.prompt_hint and e.effect_type.value == "debuff"]
    if debuffs:
        return debuffs[0].prompt_hint
    buffs = [e for e in active if e.prompt_hint and e.effect_type.value == "buff"]
    if buffs:
        return buffs[0].prompt_hint
    return ""


def _build_top_todo_desc(snapshot) -> str:
    """提取最优先的 todo，格式已经是脑内挂念风格，直接返回 title。"""
    todos = snapshot.pending_todos
    if not todos:
        return ""
    top = todos[0]
    todo_word = "想" if top.todo_type.value == "social" else "有点想"
    return f"{todo_word}{top.title[2:]}" if top.title.startswith("想") else f"有点{top.title}"


def snapshot_to_prompt(snapshot) -> str:
    """把 PersonaSnapshot 转成心理旁白片段。

    风格要求：
    - 像真人在心里怎么感受，不是系统状态报告
    - 不输出原始数值
    - 短小，一两句话
    - 让 LLM 能直接当内心独白用
    """
    thought = getattr(snapshot.state, "thought_process", "")
    if thought:
        return f"[内心] {thought}"

    parts: list[str] = []

    recent_ctx = _build_recent_context(snapshot)
    if recent_ctx:
        parts.append(recent_ctx)

    effect_desc = _build_top_effect_desc(snapshot)
    if effect_desc:
        parts.append(effect_desc)

    todo_desc = _build_top_todo_desc(snapshot)
    if todo_desc:
        parts.append(todo_desc)

    if not parts:
        return ""
    return "；".join(parts)


def snapshot_to_persona_system_context(snapshot) -> str:
    """生成人格系统级上下文（用于 system prompt 注入）。

    在生成内心独白后，state/effects/todos 通过此函数注入到 persona_prompt，
    随 system_prompt 一起发给 LLM，作为背景信息不显式输出。

    格式：生活化描述，用"|"分隔，不堆砌状态词。
    """
    parts: list[str] = []

    effect_desc = _build_top_effect_desc(snapshot)
    if effect_desc:
        parts.append(f"当前状态：{effect_desc}")

    todo_desc = _build_top_todo_desc(snapshot)
    if todo_desc:
        parts.append(f"心里挂着：{todo_desc}")

    recent_ctx = _build_recent_context(snapshot)
    if recent_ctx:
        parts.append(f"最近：{recent_ctx}")

    if not parts:
        return ""
    return " | ".join(parts)


def snapshot_to_debug_str(snapshot) -> str:
    """可读的调试字符串。"""
    lines = [
        f"活力: {snapshot.state.energy:.0f}　心情: {snapshot.state.mood:.0f}　"
        f"社交: {snapshot.state.social_need:.0f}　饱腹: {snapshot.state.satiety:.0f}",
    ]
    if snapshot.active_effects:
        names = "/".join(e.name for e in snapshot.active_effects)
        lines.append(f"  效果: {names}")
    if snapshot.pending_todos:
        titles = "; ".join(t.title for t in snapshot.pending_todos[:3])
        lines.append(f"  待办: {titles}")
    return "\n".join(lines)
