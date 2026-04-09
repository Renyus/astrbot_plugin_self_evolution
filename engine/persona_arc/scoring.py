def score_memory_pour(text: str, *, direct: bool = False) -> float:
    text = (text or "").strip()
    if len(text) < 40:
        return 0.0

    score = 0.2

    if len(text) >= 120:
        score += 0.6
    if len(text) >= 300:
        score += 1.0

    story_keywords = (
        "以前",
        "后来",
        "那时候",
        "我记得",
        "其实",
        "经历",
        "难过",
        "开心",
        "害怕",
        "舍不得",
        "后悔",
        "想起来",
        "朋友",
        "家里",
        "梦到",
        "失去",
        "等了",
        "一直",
    )
    if any(k in text for k in story_keywords):
        score += 1.0

    emotional_keywords = ("难受", "喜欢", "讨厌", "孤独", "撑不住", "谢谢", "陪我", "别走")
    if any(k in text for k in emotional_keywords):
        score += 0.8

    if direct:
        score *= 1.3

    return min(score, 4.0)


def score_consolidation_bonus(stats: dict) -> float:
    good = stats.get("good_interactions", 0)
    connected = stats.get("connected", 0)
    relief = 1 if stats.get("trajectory") in ("向上", "有落差") and stats.get("recovery") else 0

    bonus = 0.0
    bonus += min(good * 0.4, 2.0)
    bonus += min(connected * 0.2, 1.0)
    bonus += relief * 1.0
    return min(bonus, 3.0)
