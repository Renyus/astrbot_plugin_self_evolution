import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockPlugin:
    """模拟插件对象"""

    def __init__(self, config=None):
        self.config = config or {}

    @staticmethod
    def _parse_bool(val, default):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return default


class TestPluginConfig:
    """配置系统测试"""

    def test_persona_name(self):
        """测试角色名称配置"""
        from config import PluginConfig

        plugin = MockPlugin({"persona_name": "螺丝咕姆"})
        cfg = PluginConfig(plugin)

        assert cfg.persona_name == "螺丝咕姆"

    def test_persona_name_default(self):
        """测试角色名称默认值"""
        from config import PluginConfig

        plugin = MockPlugin({})
        cfg = PluginConfig(plugin)

        assert cfg.persona_name == "黑塔"

    def test_interjection_desire(self):
        """测试插嘴意愿"""
        from config import PluginConfig

        plugin = MockPlugin({"interjection_desire": 8})
        cfg = PluginConfig(plugin)

        assert cfg.interjection_desire == 8

    def test_review_mode_bool(self):
        """测试审核模式布尔解析"""
        from config import PluginConfig

        plugin = MockPlugin({"review_mode": "false"})
        cfg = PluginConfig(plugin)

        assert cfg.review_mode is False

    def test_review_mode_true(self):
        """测试审核模式真值"""
        from config import PluginConfig

        plugin = MockPlugin({"review_mode": True})
        cfg = PluginConfig(plugin)

        assert cfg.review_mode is True

    def test_san_config(self):
        """测试SAN值配置"""
        from config import PluginConfig

        plugin = MockPlugin(
            {
                "san_enabled": True,
                "san_max": 200,
                "san_cost_per_message": 5.0,
                "san_recovery_per_hour": 20,
                "san_low_threshold": 30,
            }
        )
        cfg = PluginConfig(plugin)

        assert cfg.san_enabled is True
        assert cfg.san_max == 200
        assert cfg.san_cost_per_message == 5.0
        assert cfg.san_recovery_per_hour == 20
        assert cfg.san_low_threshold == 30

    def test_growth_config(self):
        """测试成长系统配置"""
        from config import PluginConfig

        plugin = MockPlugin(
            {
                "growth_enabled": True,
                "growth_stage": "少年",
                "experience_points": 500,
                "total_messages": 1000,
                "birth_timestamp": 1700000000,
                "vocabulary_complexity": 5,
                "emotional_dependence": 6,
            }
        )
        cfg = PluginConfig(plugin)

        assert cfg.growth_enabled is True
        assert cfg.growth_stage == "少年"
        assert cfg.experience_points == 500
        assert cfg.total_messages == 1000
        assert cfg.birth_timestamp == 1700000000
        assert cfg.vocabulary_complexity == 5
        assert cfg.emotional_dependence == 6

    def test_debate_config(self):
        """测试辩论配置"""
        from config import PluginConfig

        plugin = MockPlugin({"debate_enabled": True, "debate_rounds": 5})
        cfg = PluginConfig(plugin)

        assert cfg.debate_enabled is True
        assert cfg.debate_rounds == 5

    def test_boredom_config(self):
        """测试无聊机制配置"""
        from config import PluginConfig

        plugin = MockPlugin(
            {
                "boredom_enabled": True,
                "boredom_threshold": 0.5,
                "boredom_consecutive_count": 8,
                "boredom_sarcastic_reply": True,
            }
        )
        cfg = PluginConfig(plugin)

        assert cfg.boredom_enabled is True
        assert cfg.boredom_threshold == 0.5
        assert cfg.boredom_consecutive_count == 8
        assert cfg.boredom_sarcastic_reply is True

    def test_get_method(self):
        """测试通用get方法"""
        from config import PluginConfig

        plugin = MockPlugin({"custom_key": "custom_value"})
        cfg = PluginConfig(plugin)

        assert cfg.get("custom_key") == "custom_value"
        assert cfg.get("nonexistent", "default") == "default"

    def test_parse_bool_string(self):
        """测试字符串布尔解析"""
        from config import PluginConfig

        plugin = MockPlugin({"test_bool": "true"})
        cfg = PluginConfig(plugin)

        assert cfg._parse_bool("true", False) is True
        assert cfg._parse_bool("false", True) is False
        assert cfg._parse_bool("1", False) is True
        assert cfg._parse_bool("0", True) is False
        assert cfg._parse_bool("yes", False) is True
        assert cfg._parse_bool("no", True) is False

    def test_parse_bool_bool(self):
        """测试原生布尔值"""
        from config import PluginConfig

        plugin = MockPlugin()
        cfg = PluginConfig(plugin)

        assert cfg._parse_bool(True, False) is True
        assert cfg._parse_bool(False, True) is False

    def test_parse_bool_default(self):
        """测试默认值"""
        from config import PluginConfig

        plugin = MockPlugin()
        cfg = PluginConfig(plugin)

        assert cfg._parse_bool(None, True) is True
        assert cfg._parse_bool(123, False) is False
