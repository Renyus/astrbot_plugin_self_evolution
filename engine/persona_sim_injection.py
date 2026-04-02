"""
Persona Sim Injection - snapshot 转 prompt 片段

只做"翻译"，不存状态，不过滤（过滤在上层或 engine 做）。
"""


def snapshot_to_prompt(snapshot, max_chars: int = 300) -> str:
    """把 PersonaSnapshot 转成极简 prompt 片段。"""
    parts: list[str] = []
    remaining = max_chars

    active = snapshot.active_effects
    if active:
        hints = [e.prompt_hint for e in active if e.prompt_hint]
        if hints:
            hint_str = "、".join(hints[:3])
            chunk = f"[当前状态] {hint_str}"
            if len(chunk) <= remaining:
                parts.append(chunk)
                remaining -= len(chunk)

    todos = snapshot.pending_todos
    if todos:
        top = todos[0]
        chunk = f"[内心] {top.title}"
        if len(chunk) <= remaining:
            parts.append(chunk)
            remaining -= len(chunk)

    state = snapshot.state
    if state.energy < 40:
        chunk = "[活力]偏低"
        if len(chunk) <= remaining:
            parts.append(chunk)
            remaining -= len(chunk)
    if state.mood < 40:
        chunk = "[心情]低落"
        if len(chunk) <= remaining:
            parts.append(chunk)
            remaining -= len(chunk)

    if not parts:
        return ""
    return " ".join(parts)


def snapshot_to_debug_str(snapshot) -> str:
    """可读的调试字符串。"""
    lines = [
        f"[PersonaSim] energy={snapshot.state.energy:.0f} mood={snapshot.state.mood:.0f} "
        f"social={snapshot.state.social_need:.0f} satiety={snapshot.state.satiety:.0f}",
    ]
    if snapshot.active_effects:
        names = "/".join(e.name for e in snapshot.active_effects)
        lines.append(f"  effects: {names}")
    if snapshot.pending_todos:
        titles = "; ".join(t.title for t in snapshot.pending_todos[:3])
        lines.append(f"  todos: {titles}")
    return "\n".join(lines)
