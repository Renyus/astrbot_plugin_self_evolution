import pytest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockContext:
    """模拟AstrBot上下文"""

    def __init__(self):
        self._config = {}

    def get_config(self):
        return self._config


class MockPlugin:
    """模拟插件对象"""

    def __init__(self, config=None):
        self.config = config or {}
        self.context = MockContext()

    @property
    def growth_enabled(self):
        return self.config.get("growth_enabled", True)

    @property
    def growth_stage(self):
        return self.config.get("growth_stage", "婴儿")

    @property
    def experience_points(self):
        return int(self.config.get("experience_points", 0))

    @property
    def total_messages(self):
        return int(self.config.get("total_messages", 0))

    @property
    def birth_timestamp(self):
        return int(self.config.get("birth_timestamp", 0))

    @property
    def vocabulary_complexity(self):
        return int(self.config.get("vocabulary_complexity", 1))

    @property
    def emotional_dependence(self):
        return int(self.config.get("emotional_dependence", 10))

    @property
    def growth_prompt_baby(self):
        return self.config.get("growth_prompt_baby", "你是一个刚诞生的AI婴儿")

    @property
    def growth_prompt_child(self):
        return self.config.get("growth_prompt_child", "你是一个正在学习的AI幼儿")

    @property
    def growth_prompt_teen(self):
        return self.config.get("growth_prompt_teen", "你是一个青春期的AI少年")

    @property
    def growth_prompt_adult(self):
        return self.config.get("growth_prompt_adult", "你是一个成熟的AI")


class TestGrowthSystem:
    """成长系统测试"""

    def test_initialize_new(self):
        """测试新生命初始化"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin()
        growth = GrowthSystem(plugin)
        growth.initialize()

        assert growth.birth_timestamp > 0

    def test_initialize_existing(self):
        """测试已有生命初始化"""
        import time

        current = int(time.time())

        plugin = MockPlugin({"birth_timestamp": current})
        growth = GrowthSystem(plugin)
        growth.initialize()

        assert growth.birth_timestamp == current

    def test_disabled(self):
        """测试禁用状态"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"growth_enabled": False})
        growth = GrowthSystem(plugin)
        growth.initialize()

        assert growth.enabled is False

    def test_add_experience(self):
        """测试添加经验"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"experience_points": 10, "total_messages": 5})
        growth = GrowthSystem(plugin)

        growth.add_experience(5)

        assert plugin.context.get_config()["experience_points"] == 15
        assert plugin.context.get_config()["total_messages"] == 6

    def test_stage_baby(self):
        """测试婴儿阶段"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"growth_stage": "婴儿"})
        growth = GrowthSystem(plugin)

        prompt = growth.get_stage_prompt()
        assert "婴儿" in prompt or "好奇" in prompt

    def test_stage_child(self):
        """测试幼儿阶段"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"growth_stage": "幼儿"})
        growth = GrowthSystem(plugin)

        prompt = growth.get_stage_prompt()
        assert "幼儿" in prompt or "学习" in prompt

    def test_stage_teen(self):
        """测试少年阶段"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"growth_stage": "少年"})
        growth = GrowthSystem(plugin)

        prompt = growth.get_stage_prompt()
        assert "少年" in prompt or "个性" in prompt

    def test_stage_adult(self):
        """测试成年阶段"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin({"growth_stage": "成年"})
        growth = GrowthSystem(plugin)

        prompt = growth.get_stage_prompt()
        assert "成熟" in prompt or "独立" in prompt

    def test_upgrade_baby_to_child(self):
        """测试婴儿升级到幼儿"""
        from cognition.growth import GrowthSystem

        old_time = int(time.time()) - (4 * 86400)
        plugin = MockPlugin(
            {"growth_stage": "婴儿", "birth_timestamp": old_time, "total_messages": 350}
        )
        growth = GrowthSystem(plugin)

        result = growth.check_upgrade()

        assert result is True
        assert plugin.context.get_config()["growth_stage"] == "幼儿"

    def test_no_upgrade_insufficient(self):
        """测试条件不足不升级"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin(
            {
                "growth_stage": "婴儿",
                "birth_timestamp": int(time.time()) - 86400,
                "total_messages": 100,
            }
        )
        growth = GrowthSystem(plugin)

        result = growth.check_upgrade()

        assert result is False

    def test_prompt_injection(self):
        """测试提示词注入"""
        from cognition.growth import GrowthSystem

        plugin = MockPlugin(
            {
                "growth_stage": "婴儿",
                "experience_points": 100,
                "vocabulary_complexity": 2,
                "emotional_dependence": 8,
            }
        )
        growth = GrowthSystem(plugin)

        injection = growth.get_prompt_injection()

        assert "婴儿" in injection or "经验" in injection
        assert "词汇复杂度" in injection or "情感依赖" in injection
