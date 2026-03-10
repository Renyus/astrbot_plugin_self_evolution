"""
群体情绪共染系统 - 群氛围影响AI情绪
"""

import logging

logger = logging.getLogger("astrbot")


class GroupVibeSystem:
    """群体情绪共染系统 - 感知并响应群氛围"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._group_vibe = {}

    @property
    def enabled(self):
        return getattr(self.plugin, "group_vibe_enabled", True)

    def initialize(self):
        if not self.enabled:
            return
        logger.info("[Vibe] 群体情绪共染系统初始化")

    def update(self, group_id: str, msg_text: str):
        if not self.enabled:
            return

        negative_words = [
            "生气",
            "愤怒",
            "吵架",
            "不爽",
            "滚",
            "傻",
            "蠢",
            "无语",
            "MD",
            "操",
            "靠",
        ]
        positive_words = [
            "哈哈",
            "笑死",
            "牛逼",
            "太棒",
            "爱了",
            "开心",
            "真好",
            "厉害",
            "赞",
        ]

        score = 0
        for w in negative_words:
            if w in msg_text:
                score -= 1
        for w in positive_words:
            if w in msg_text:
                score += 1

        current = self._group_vibe.get(group_id, 0)
        self._group_vibe[group_id] = max(-10, min(10, current + score))

    def get_vibe(self, group_id: str) -> str:
        if not self.enabled:
            return ""
        vibe = self._group_vibe.get(group_id, 0)
        if vibe < -5:
            return "群氛围紧张"
        elif vibe < 0:
            return "群氛围略低沉"
        elif vibe > 5:
            return "群氛围热烈"
        elif vibe > 0:
            return "群氛围轻松"
        return "群氛围平静"

    def get_prompt_injection(self, group_id: str) -> str:
        if not self.enabled:
            return ""
        vibe = self.get_vibe(group_id)
        return f"\n\n【群氛围感知】{vibe}"
