"""
Plugin configuration accessors.
"""

import json


class PluginConfig:
    """Centralized typed config access for the plugin."""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def _config(self):
        return self.plugin.config

    @property
    def _parse_bool(self):
        return self.plugin._parse_bool

    def _get_nested(self, group: str, key: str, default=None):
        """优先读新 object 路径，回退读旧平铺键。"""
        group_data = self._config.get(group, {})
        if isinstance(group_data, dict) and key in group_data:
            return group_data.get(key, default)
        return self._config.get(key, default)

    def _get_nested_bool(self, group: str, key: str, default=False):
        val = self._get_nested(group, key, default)
        return self._parse_bool(val, default)

    def __getattr__(self, name):
        if name.startswith("_") or name in ("plugin", "config"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        val = self._config.get(name)
        if val is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return val

    # base
    @property
    def review_mode(self):
        return self._get_nested_bool("base", "review_mode", True)

    @property
    def persona_name(self):
        return self._get_nested("base", "persona_name", "黑塔")

    @property
    def admin_users(self):
        return self._get_nested("base", "admin_users", [])

    @property
    def target_scopes(self):
        scopes = self._get_nested("base", "target_scopes", [])
        if isinstance(scopes, str):
            scopes = [g.strip() for g in scopes.split(",") if g.strip()]
        return scopes

    # memory_summary
    @property
    def memory_enabled(self):
        return self._get_nested_bool("memory_summary", "memory_enabled", True)

    @property
    def memory_kb_name(self):
        return self._get_nested("memory_summary", "memory_kb_name", "self_evolution_memory")

    @property
    def memory_fetch_page_size(self):
        return int(self._get_nested("memory_summary", "memory_fetch_page_size", 500))

    @property
    def memory_summary_chunk_size(self):
        return int(self._get_nested("memory_summary", "memory_summary_chunk_size", 200))

    @property
    def memory_summary_schedule(self):
        return self._get_nested("memory_summary", "memory_summary_schedule", "0 3 * * *")

    @property
    def enable_kb_memory_recall(self):
        return self.memory_enabled and self._get_nested_bool("memory_summary", "enable_kb_memory_recall", True)

    @property
    def memory_query_fallback_enabled(self):
        return self._get_nested_bool("memory_summary", "memory_query_fallback_enabled", True)

    # profile
    @property
    def profile_msg_count(self):
        return int(self._get_nested("profile", "profile_msg_count", 500))

    @property
    def profile_cooldown_minutes(self):
        return int(self._get_nested("profile", "profile_cooldown_minutes", 10))

    @property
    def enable_profile_injection(self):
        return self._get_nested_bool("profile", "enable_profile_injection", True)

    @property
    def enable_profile_fact_writeback(self):
        return self._get_nested_bool("profile", "enable_profile_fact_writeback", True)

    @property
    def auto_profile_enabled(self):
        return self._get_nested_bool("profile", "auto_profile_enabled", True)

    @property
    def auto_profile_schedule(self):
        return self._get_nested("profile", "auto_profile_schedule", "0 0 * * *")

    @property
    def auto_profile_batch_size(self):
        return int(self._get_nested("profile", "auto_profile_batch_size", 3))

    @property
    def auto_profile_batch_interval(self):
        return int(self._get_nested("profile", "auto_profile_batch_interval", 30))

    # reflection
    @property
    def reflection_enabled(self):
        return self._get_nested_bool("reflection", "reflection_enabled", True)

    @property
    def reflection_schedule(self):
        return self._get_nested("reflection", "reflection_schedule", "0 2 * * *")

    # engagement
    @property
    def interject_enabled(self):
        return self._get_nested_bool("engagement", "interject_enabled", False)

    @property
    def interject_interval(self):
        return int(self._get_nested("engagement", "interject_interval", 30))

    @property
    def interject_cooldown(self):
        return int(self._get_nested("engagement", "interject_cooldown", 30))

    @property
    def interject_trigger_probability(self):
        return float(self._get_nested("engagement", "interject_trigger_probability", 0.5))

    @property
    def engagement_react_probability(self) -> float:
        return float(self._get_nested("engagement", "engagement_react_probability", 0.15))

    # affinity
    @property
    def affinity_auto_enabled(self):
        return self._get_nested_bool("affinity", "affinity_auto_enabled", True)

    @property
    def affinity_recovery_enabled(self) -> bool:
        return self._get_nested_bool("affinity", "affinity_recovery_enabled", True)

    @property
    def affinity_direct_engagement_delta(self):
        return int(self._get_nested("affinity", "affinity_direct_engagement_delta", 1))

    @property
    def affinity_friendly_language_delta(self):
        return int(self._get_nested("affinity", "affinity_friendly_language_delta", 1))

    @property
    def affinity_hostile_language_delta(self):
        return int(self._get_nested("affinity", "affinity_hostile_language_delta", -2))

    @property
    def affinity_returning_user_delta(self):
        return int(self._get_nested("affinity", "affinity_returning_user_delta", 1))

    @property
    def affinity_direct_engagement_cooldown_minutes(self):
        return int(self._get_nested("affinity", "affinity_direct_engagement_cooldown_minutes", 360))

    @property
    def affinity_friendly_daily_limit(self):
        return int(self._get_nested("affinity", "affinity_friendly_daily_limit", 2))

    @property
    def affinity_hostile_cooldown_minutes(self):
        return int(self._get_nested("affinity", "affinity_hostile_cooldown_minutes", 60))

    @property
    def affinity_returning_user_daily_limit(self) -> int:
        return int(self._get_nested("affinity", "affinity_returning_user_daily_limit", 1))

    # san
    @property
    def san_enabled(self):
        return self._get_nested_bool("san", "san_enabled", True)

    @property
    def san_max(self):
        return int(self._get_nested("san", "san_max", 100))

    @property
    def san_cost_per_message(self):
        return float(self._get_nested("san", "san_cost_per_message", 2.0))

    @property
    def san_recovery_per_hour(self):
        return int(self._get_nested("san", "san_recovery_per_hour", 10))

    @property
    def san_low_threshold(self):
        return int(self._get_nested("san", "san_low_threshold", 20))

    @property
    def san_auto_analyze_enabled(self):
        return self._get_nested_bool("san", "san_auto_analyze_enabled", True)

    @property
    def san_analyze_interval(self):
        return int(self._get_nested("san", "san_analyze_interval", 30))

    @property
    def san_msg_count_per_group(self):
        return int(self._get_nested("san", "san_msg_count_per_group", 50))

    @property
    def san_high_activity_boost(self):
        return int(self._get_nested("san", "san_high_activity_boost", 5))

    @property
    def san_low_activity_drain(self):
        return int(self._get_nested("san", "san_low_activity_drain", -3))

    @property
    def san_positive_vibe_bonus(self):
        return int(self._get_nested("san", "san_positive_vibe_bonus", 3))

    @property
    def san_negative_vibe_penalty(self):
        return int(self._get_nested("san", "san_negative_vibe_penalty", -5))

    # dropout
    @property
    def dropout_enabled(self):
        return self._get_nested_bool("prompt", "dropout_enabled", True)

    @property
    def dropout_edge_rate(self):
        return float(self._get_nested("prompt", "dropout_edge_rate", 0.2))

    # meta
    @property
    def meta_enabled(self):
        return self._get_nested_bool("meta", "meta_enabled", True)

    @property
    def allow_meta_programming(self):
        return self.meta_enabled and self._get_nested_bool("meta", "allow_meta_programming", False)

    @property
    def debate_enabled(self):
        return self._get_nested_bool("meta", "debate_enabled", True)

    @property
    def debate_rounds(self):
        return int(self._get_nested("meta", "debate_rounds", 3))

    @property
    def debate_system_prompt(self):
        return self._get_nested(
            "meta",
            "debate_system_prompt",
            "你是一个无情的安全审查员，代号螺丝咔姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。",
        )

    @property
    def debate_criteria(self):
        return self._get_nested(
            "meta",
            "debate_criteria",
            "安全漏洞|逻辑错误|性能问题|代码规范|潜在Bug",
        )

    @property
    def debate_agents(self):
        agents = self._get_nested(
            "meta",
            "debate_agents",
            '[{"name": "螺丝咔姆", "system_prompt": "你是一个无情的安全审查员，代号螺丝咔姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。"}, {"name": "阮梅", "system_prompt": "你是一个天才的生物学博士，代号阮梅。你的职责是从生物学和复杂系统视角审查代码提案，评估其自洽性、涌现行为和演化潜力。你说话温柔但一针见血。"}]',
        )
        if isinstance(agents, str):
            try:
                return json.loads(agents)
            except Exception:
                return []
        return agents

    # surprise and monologue
    @property
    def surprise_enabled(self):
        return self._get_nested_bool("prompt", "surprise_enabled", True)

    @property
    def surprise_boost_keywords(self):
        return self._get_nested(
            "prompt",
            "surprise_boost_keywords",
            "突然|惊讶|没想到|居然",
        )

    # entertainment / sticker
    @property
    def entertainment_enabled(self):
        return self._get_nested_bool("sticker", "entertainment_enabled", True)

    @property
    def sticker_learning_enabled(self):
        return self.entertainment_enabled and self._get_nested_bool("sticker", "sticker_learning_enabled", False)

    @property
    def sticker_target_qq(self):
        return self._get_nested("sticker", "sticker_target_qq", "")

    @property
    def sticker_total_limit(self):
        return int(self._get_nested("sticker", "sticker_total_limit", 100))

    @property
    def sticker_send_cooldown(self):
        return int(self._get_nested("sticker", "sticker_send_cooldown", 30))

    @property
    def sticker_freq_threshold(self):
        return int(self._get_nested("sticker", "sticker_freq_threshold", 2))

    # prompt
    @property
    def disable_framework_contexts(self):
        return self._get_nested_bool("prompt", "disable_framework_contexts", False)

    @property
    def inject_group_history(self):
        return self._get_nested_bool("prompt", "inject_group_history", True)

    @property
    def group_history_count(self):
        return int(self._get_nested("prompt", "group_history_count", 10))

    @property
    def max_prompt_injection_length(self):
        return int(self._get_nested("prompt", "max_prompt_injection_length", 2000))

    # debug
    @property
    def debug_log_enabled(self):
        return self._get_nested_bool("debug", "debug_log_enabled", False)

    @property
    def memory_debug_enabled(self):
        return self._get_nested_bool("debug", "memory_debug_enabled", False)

    @property
    def engagement_debug_enabled(self):
        return self._get_nested_bool("debug", "engagement_debug_enabled", False)

    @property
    def affinity_debug_enabled(self):
        return self._get_nested_bool("debug", "affinity_debug_enabled", False)
