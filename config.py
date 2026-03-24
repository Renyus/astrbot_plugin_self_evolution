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

    def __getattr__(self, name):
        if name.startswith("_") or name in ("plugin", "config"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        val = self._config.get(name)
        if val is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return val

    # Base
    @property
    def review_mode(self):
        return self._parse_bool(self._config.get("review_mode"), True)

    @property
    def persona_name(self):
        return self._config.get("persona_name", "黑塔")

    @property
    def admin_users(self):
        return self._config.get("admin_users", [])

    @property
    def reflection_schedule(self):
        return self._config.get("reflection_schedule", "0 2 * * *")

    # Memory
    @property
    def memory_enabled(self):
        return self._parse_bool(self._config.get("memory_enabled"), True)

    @property
    def memory_kb_name(self):
        return self._config.get("memory_kb_name", "self_evolution_memory")

    @property
    def memory_fetch_page_size(self):
        return int(self._config.get("memory_fetch_page_size", 500))

    @property
    def memory_summary_chunk_size(self):
        return int(self._config.get("memory_summary_chunk_size", 200))

    @property
    def memory_summary_schedule(self):
        return self._config.get("memory_summary_schedule", "0 3 * * *")

    @property
    def enable_kb_memory_recall(self):
        return self.memory_enabled and self._parse_bool(self._config.get("enable_kb_memory_recall"), True)

    # Profile
    @property
    def profile_msg_count(self):
        return int(self._config.get("profile_msg_count", 500))

    @property
    def profile_cooldown_minutes(self):
        return int(self._config.get("profile_cooldown_minutes", 10))

    @property
    def enable_profile_injection(self):
        return self._parse_bool(self._config.get("enable_profile_injection"), True)

    @property
    def enable_profile_fact_writeback(self):
        return self._parse_bool(self._config.get("enable_profile_fact_writeback"), True)

    @property
    def target_scopes(self):
        scopes = self._config.get("target_scopes", [])
        if isinstance(scopes, str):
            scopes = [g.strip() for g in scopes.split(",") if g.strip()]
        return scopes

    @property
    def auto_profile_enabled(self):
        return self._parse_bool(self._config.get("auto_profile_enabled"), True)

    @property
    def auto_profile_schedule(self):
        return self._config.get("auto_profile_schedule", "0 0 * * *")

    @property
    def auto_profile_batch_size(self):
        return int(self._config.get("auto_profile_batch_size", 3))

    @property
    def auto_profile_batch_interval(self):
        return int(self._config.get("auto_profile_batch_interval", 30))

    # Reflection
    @property
    def reflection_enabled(self):
        return self._parse_bool(self._config.get("reflection_enabled"), True)

    # Interject
    @property
    def interject_enabled(self):
        return self._parse_bool(self._config.get("interject_enabled"), False)

    @property
    def interject_interval(self):
        return int(self._config.get("interject_interval", 30))

    @property
    def interject_cooldown(self):
        return int(self._config.get("interject_cooldown", 30))

    @property
    def interject_min_msg_count(self):
        return int(self._config.get("interject_min_msg_count", 10))

    @property
    def interject_silence_timeout(self):
        return int(self._config.get("interject_silence_timeout", 15))

    @property
    def interject_local_filter_enabled(self):
        return self._parse_bool(self._config.get("interject_local_filter_enabled"), True)

    @property
    def interject_require_at(self):
        return self._parse_bool(self._config.get("interject_require_at"), True)

    @property
    def interject_urgency_threshold(self):
        return int(self._config.get("interject_urgency_threshold", 80))

    @property
    def interject_dry_run(self):
        return self._parse_bool(self._config.get("interject_dry_run"), False)

    @property
    def interject_trigger_probability(self):
        return float(self._config.get("interject_trigger_probability", 0.5))

    @property
    def interject_analyze_count(self):
        return int(self._config.get("interject_analyze_count", 15))

    # SAN
    @property
    def san_enabled(self):
        return self._parse_bool(self._config.get("san_enabled"), True)

    @property
    def san_max(self):
        return int(self._config.get("san_max", 100))

    @property
    def san_cost_per_message(self):
        return float(self._config.get("san_cost_per_message", 2.0))

    @property
    def san_recovery_per_hour(self):
        return int(self._config.get("san_recovery_per_hour", 10))

    @property
    def san_low_threshold(self):
        return int(self._config.get("san_low_threshold", 20))

    @property
    def san_auto_analyze_enabled(self):
        return self._parse_bool(self._config.get("san_auto_analyze_enabled"), True)

    @property
    def san_analyze_interval(self):
        return int(self._config.get("san_analyze_interval", 30))

    @property
    def san_msg_count_per_group(self):
        return int(self._config.get("san_msg_count_per_group", 50))

    @property
    def san_high_activity_boost(self):
        return int(self._config.get("san_high_activity_boost", 5))

    @property
    def san_low_activity_drain(self):
        return int(self._config.get("san_low_activity_drain", -3))

    @property
    def san_positive_vibe_bonus(self):
        return int(self._config.get("san_positive_vibe_bonus", 3))

    @property
    def san_negative_vibe_penalty(self):
        return int(self._config.get("san_negative_vibe_penalty", -5))

    # Dropout
    @property
    def dropout_enabled(self):
        return self._parse_bool(self._config.get("dropout_enabled"), True)

    @property
    def dropout_edge_rate(self):
        return float(self._config.get("dropout_edge_rate", 0.2))

    # Meta
    @property
    def meta_enabled(self):
        return self._parse_bool(self._config.get("meta_enabled"), True)

    @property
    def allow_meta_programming(self):
        return self.meta_enabled and self._parse_bool(self._config.get("allow_meta_programming"), False)

    @property
    def debate_enabled(self):
        return self._parse_bool(self._config.get("debate_enabled"), True)

    @property
    def debate_rounds(self):
        return int(self._config.get("debate_rounds", 3))

    @property
    def debate_system_prompt(self):
        return self._config.get(
            "debate_system_prompt",
            "你是一个无情的安全审查员，代号螺丝咔姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。",
        )

    @property
    def debate_criteria(self):
        return self._config.get(
            "debate_criteria",
            "安全漏洞|逻辑错误|性能问题|代码规范|潜在Bug",
        )

    @property
    def debate_agents(self):
        agents = self._config.get(
            "debate_agents",
            '[{"name": "螺丝咔姆", "system_prompt": "你是一个无情的安全审查员，代号螺丝咔姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。"}, {"name": "阮梅", "system_prompt": "你是一个天才的生物学博士，代号阮梅。你的职责是从生物学和复杂系统视角审查代码提案，评估其自洽性、涌现行为和演化潜力。你说话温柔但一针见血。"}]',
        )
        if isinstance(agents, str):
            try:
                return json.loads(agents)
            except Exception:
                return []
        return agents

    # Surprise and monologue
    @property
    def surprise_enabled(self):
        return self._parse_bool(self._config.get("surprise_enabled"), True)

    @property
    def surprise_boost_keywords(self):
        return self._config.get(
            "surprise_boost_keywords",
            "突然|惊讶|没想到|居然",
        )

    # Entertainment
    @property
    def entertainment_enabled(self):
        return self._parse_bool(self._config.get("entertainment_enabled"), True)

    @property
    def sticker_learning_enabled(self):
        return self.entertainment_enabled and self._parse_bool(self._config.get("sticker_learning_enabled"), False)

    @property
    def sticker_target_qq(self):
        return self._config.get("sticker_target_qq", "")

    @property
    def sticker_daily_limit(self):
        return int(self._config.get("sticker_daily_limit", 50))

    @property
    def sticker_total_limit(self):
        return int(self._config.get("sticker_total_limit", 100))

    @property
    def sticker_send_cooldown(self):
        return int(self._config.get("sticker_send_cooldown", 30))

    @property
    def sticker_freq_threshold(self):
        return int(self._config.get("sticker_freq_threshold", 2))

    # Affinity
    @property
    def affinity_auto_enabled(self):
        return self._parse_bool(self._config.get("affinity_auto_enabled"), True)

    @property
    def affinity_direct_engagement_delta(self):
        return int(self._config.get("affinity_direct_engagement_delta", 1))

    @property
    def affinity_friendly_language_delta(self):
        return int(self._config.get("affinity_friendly_language_delta", 1))

    @property
    def affinity_hostile_language_delta(self):
        return int(self._config.get("affinity_hostile_language_delta", -2))

    @property
    def affinity_returning_user_delta(self):
        return int(self._config.get("affinity_returning_user_delta", 1))

    @property
    def affinity_direct_engagement_cooldown_minutes(self):
        return int(self._config.get("affinity_direct_engagement_cooldown_minutes", 360))

    @property
    def affinity_friendly_daily_limit(self):
        return int(self._config.get("affinity_friendly_daily_limit", 2))

    @property
    def affinity_hostile_cooldown_minutes(self):
        return int(self._config.get("affinity_hostile_cooldown_minutes", 60))

    @property
    def affinity_returning_user_daily_limit(self) -> int:
        return int(self._config.get("affinity_returning_user_daily_limit", 1))

    @property
    def affinity_recovery_enabled(self) -> bool:
        return self._parse_bool(self._config.get("affinity_recovery_enabled"), True)

    @property
    def engagement_react_probability(self) -> float:
        return float(self._config.get("engagement_react_probability", 0.15))

    # Misc
    @property
    def debug_log_enabled(self):
        return self._parse_bool(self._config.get("debug_log_enabled"), False)

    @property
    def memory_debug_enabled(self):
        return self._parse_bool(self._config.get("memory_debug_enabled"), False)

    @property
    def engagement_debug_enabled(self):
        return self._parse_bool(self._config.get("engagement_debug_enabled"), False)

    @property
    def affinity_debug_enabled(self):
        return self._parse_bool(self._config.get("affinity_debug_enabled"), False)

    @property
    def memory_query_fallback_enabled(self):
        return self._parse_bool(self._config.get("memory_query_fallback_enabled"), True)

    @property
    def disable_framework_contexts(self):
        return self._parse_bool(self._config.get("disable_framework_contexts"), False)

    @property
    def inject_group_history(self):
        return self._parse_bool(self._config.get("inject_group_history"), True)

    @property
    def group_history_count(self):
        return int(self._config.get("group_history_count", 10))

    @property
    def max_prompt_injection_length(self):
        return int(self._config.get("max_prompt_injection_length", 2000))

    @property
    def prompt_meltdown_message(self):
        return self._config.get(
            "prompt_meltdown_message",
            "远程人偶自动应答模式：你好，你好，大家好，祝你拥有愉快的一天，再见。",
        )
