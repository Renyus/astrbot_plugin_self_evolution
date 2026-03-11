"""
群体情绪共染系统 - 群氛围影响AI情绪
"""

import logging
import time

logger = logging.getLogger("astrbot")


class GroupVibeSystem:
    """群体情绪共染系统 - 感知并响应群氛围"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._group_vibe = {}
        self._vibe_access_time = {}
        self._last_cleanup = 0
        self._cleanup_interval = 3600  # 每小时清理一次

    @property
    def enabled(self):
        return getattr(self.plugin, "group_vibe_enabled", True)

    def initialize(self):
        if not self.enabled:
            return
        logger.info("[Vibe] 群体情绪共染系统初始化")

    def _cleanup_stale_vibes(self):
        """清理长时间不活跃的群氛围数据"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        stale_groups = []
        for group_id, access_time in self._vibe_access_time.items():
            if now - access_time > 86400:  # 24小时无活动视为过期
                stale_groups.append(group_id)

        for group_id in stale_groups:
            self._group_vibe.pop(group_id, None)
            self._vibe_access_time.pop(group_id, None)

        if stale_groups:
            logger.debug(f"[Vibe] 已清理 {len(stale_groups)} 个过期群氛围数据")

    def update(self, group_id: str, msg_text: str):
        if not self.enabled:
            return

        logger.debug(f"[Vibe] 收到群消息，群 {group_id}: {msg_text[:20]}")

        # 定期清理过期数据
        self._cleanup_stale_vibes()

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
        self._vibe_access_time[group_id] = time.time()

    def get_vibe(self, group_id: str) -> str:
        if not self.enabled:
            return ""
        vibe = self._group_vibe.get(group_id, 0)
        logger.debug(f"[Vibe] 获取群氛围，群 {group_id}: {vibe}")
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
        logger.debug(f"[Vibe] 生成群氛围提示注入，群 {group_id}: {vibe}")
        return f"\n\n【群氛围感知】{vibe}"
