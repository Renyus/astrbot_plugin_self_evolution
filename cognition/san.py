"""
SAN 值系统 - 心智疲劳与精力管理
"""

import time
import logging

logger = logging.getLogger("astrbot")


class SANSystem:
    """SAN (Sanity/精力值) 系统 - 模拟心智疲劳"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._san_value = None
        self._san_last_recovery = None

    @property
    def enabled(self):
        return getattr(self.plugin, "san_enabled", True)

    @property
    def max_value(self):
        return int(getattr(self.plugin, "san_max", 100))

    @property
    def cost_per_message(self):
        return float(getattr(self.plugin, "san_cost_per_message", 2.0))

    @property
    def recovery_per_hour(self):
        return int(getattr(self.plugin, "san_recovery_per_hour", 10))

    @property
    def low_threshold(self):
        return int(getattr(self.plugin, "san_low_threshold", 20))

    def initialize(self):
        if not self.enabled:
            return
        if self._san_value is None:
            self._san_value = self.max_value
            self._san_last_recovery = time.time()
            logger.info(f"[SAN] 精力值系统初始化: {self._san_value}/{self.max_value}")

    def update(self):
        if not self.enabled:
            return True

        current_time = time.time()
        elapsed = current_time - (self._san_last_recovery or current_time)

        if elapsed > 3600:
            recovered = int(elapsed / 3600) * self.recovery_per_hour
            self._san_value = min(self.max_value, self._san_value + recovered)
            self._san_last_recovery = current_time
            logger.debug(f"[SAN] 精力恢复: {self._san_value}/{self.max_value}")

        if self._san_value <= 0:
            return False

        self._san_value = max(0, self._san_value - self.cost_per_message)
        return True

    def get_status(self):
        if not self.enabled:
            return ""
        ratio = self._san_value / self.max_value
        if ratio < 0.2:
            return "疲惫不堪"
        elif ratio < 0.5:
            return "略有疲态"
        return "精力充沛"

    def get_prompt_injection(self):
        if not self.enabled:
            return ""
        return f"\n\n【当前状态】{self.get_status()}"

    @property
    def value(self):
        return self._san_value or 0
