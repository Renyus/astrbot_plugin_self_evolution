import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockPlugin:
    """模拟插件对象用于测试"""

    def __init__(self, config=None):
        self.config = config or {}
        self._parse_bool = self._parse_bool_impl

    @staticmethod
    def _parse_bool_impl(val, default):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return default

    @property
    def san_enabled(self):
        return self._parse_bool(self.config.get("san_enabled"), True)

    @property
    def san_max(self):
        return int(self.config.get("san_max", 100))

    @property
    def san_cost_per_message(self):
        return float(self.config.get("san_cost_per_message", 2.0))

    @property
    def san_recovery_per_hour(self):
        return int(self.config.get("san_recovery_per_hour", 10))

    @property
    def san_low_threshold(self):
        return int(self.config.get("san_low_threshold", 20))


class TestSANSystem:
    """SAN值系统测试"""

    def test_initialize(self):
        """测试初始化"""
        from cognition.san import SANSystem

        plugin = MockPlugin()
        san = SANSystem(plugin)
        san.initialize()

        assert san.value == 100
        assert san.max_value == 100

    def test_disabled(self):
        """测试禁用状态"""
        from cognition.san import SANSystem

        plugin = MockPlugin({"san_enabled": False})
        san = SANSystem(plugin)
        san.initialize()

        assert san.enabled is False
        assert san.update() is True

    def test_consume(self):
        """测试精力消耗"""
        from cognition.san import SANSystem

        plugin = MockPlugin()
        san = SANSystem(plugin)
        san.initialize()

        initial = san.value
        san.update()

        assert san.value <= initial

    def test_depleted(self):
        """测试精力耗尽"""
        from cognition.san import SANSystem

        plugin = MockPlugin({"san_cost_per_message": 200})
        san = SANSystem(plugin)
        san.initialize()

        result = san.update()

        assert result is False

    def test_status_exhausted(self):
        """测试疲惫状态"""
        from cognition.san import SANSystem

        plugin = MockPlugin({"san_max": 100})
        san = SANSystem(plugin)
        san._san_value = 10
        san._san_last_recovery = 0

        status = san.get_status()
        assert status == "疲惫不堪"

    def test_status_tired(self):
        """测试略有疲态"""
        from cognition.san import SANSystem

        plugin = MockPlugin()
        san = SANSystem(plugin)
        san._san_value = 40

        status = san.get_status()
        assert status == "略有疲态"

    def test_status_energetic(self):
        """测试精力充沛"""
        from cognition.san import SANSystem

        plugin = MockPlugin()
        san = SANSystem(plugin)
        san._san_value = 80

        status = san.get_status()
        assert status == "精力充沛"

    def test_prompt_injection(self):
        """测试提示词注入"""
        from cognition.san import SANSystem

        plugin = MockPlugin()
        san = SANSystem(plugin)
        san._san_value = 50

        injection = san.get_prompt_injection()
        assert "精力" in injection or "状态" in injection
