import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockPlugin:
    """模拟插件对象"""

    def __init__(self, config=None):
        self.config = config or {}

    @property
    def group_vibe_enabled(self):
        return self.config.get("group_vibe_enabled", True)


class TestGroupVibeSystem:
    """群氛围系统测试"""

    def test_initialize(self):
        """测试初始化"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)
        vibe.initialize()

        assert vibe.enabled is True

    def test_disabled(self):
        """测试禁用状态"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin({"group_vibe_enabled": False})
        vibe = GroupVibeSystem(plugin)
        vibe.initialize()

        assert vibe.enabled is False

    def test_negative_update(self):
        """测试负面情绪更新"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)

        group_id = "123456"
        vibe.update(group_id, "生气愤怒吵架")

        assert vibe._group_vibe[group_id] < 0

    def test_positive_update(self):
        """测试正面情绪更新"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)

        group_id = "123456"
        vibe.update(group_id, "哈哈笑死太棒了")

        assert vibe._group_vibe[group_id] > 0

    def test_vibe_clamp(self):
        """测试氛围值边界"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)

        group_id = "123456"
        for _ in range(20):
            vibe.update(group_id, "哈哈笑死太棒了")

        assert vibe._group_vibe[group_id] <= 10

    def test_get_vibe_tense(self):
        """测试紧张氛围"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)
        vibe._group_vibe["123456"] = -8

        result = vibe.get_vibe("123456")
        assert "紧张" in result

    def test_get_vibe_heated(self):
        """测试热烈氛围"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)
        vibe._group_vibe["123456"] = 8

        result = vibe.get_vibe("123456")
        assert "热烈" in result

    def test_get_vibe_calm(self):
        """测试平静氛围"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)
        vibe._group_vibe["123456"] = 0

        result = vibe.get_vibe("123456")
        assert "平静" in result

    def test_prompt_injection(self):
        """测试提示词注入"""
        from cognition.vibe import GroupVibeSystem

        plugin = MockPlugin()
        vibe = GroupVibeSystem(plugin)
        vibe._group_vibe["123456"] = 5

        injection = vibe.get_prompt_injection("123456")
        assert "群氛围" in injection or "氛围" in injection
