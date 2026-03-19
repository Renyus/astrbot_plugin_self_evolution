"""
配置系统 - 从主类中解耦所有配置属性
"""

import json


class PluginConfig:
    """插件配置类 - 集中管理所有配置项"""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def _config(self):
        return self.plugin.config

    @property
    def _parse_bool(self):
        return self.plugin._parse_bool

    def __getattr__(self, name):
        """代理所有配置访问"""
        if name.startswith("_") or name in ("plugin", "config"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return self._config.get(name)

    # ========== 基础设置 ==========
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
    def critical_keywords(self):
        return self._config.get(
            "critical_keywords",
            "黑塔|空间站|人偶|天才|模拟宇宙|研究|论文|技术|算力|数据",
        )

    @property
    def allow_meta_programming(self):
        return self._parse_bool(self._config.get("allow_meta_programming"), False)

    @property
    def reflection_schedule(self):
        return self._config.get("reflection_schedule", "0 2 * * *")

    # ========== 记忆系统 ==========
    @property
    def memory_kb_name(self):
        return self._config.get("memory_kb_name", "self_evolution_memory")

    @property
    def memory_msg_count(self):
        return int(self._config.get("memory_msg_count", 500))

    @property
    def memory_summary_schedule(self):
        return self._config.get("memory_summary_schedule", "0 3 * * *")

    # ========== 画像系统 ==========
    @property
    def profile_msg_count(self):
        return int(self._config.get("profile_msg_count", 500))

    @property
    def profile_cooldown_minutes(self):
        return int(self._config.get("profile_cooldown_minutes", 10))

    @property
    def enable_profile_update(self):
        return self._parse_bool(self._config.get("enable_profile_update"), True)

    @property
    def profile_group_whitelist(self):
        whitelist = self._config.get("profile_group_whitelist", [])
        if isinstance(whitelist, str):
            whitelist = [g.strip() for g in whitelist.split(",") if g.strip()]
        return whitelist

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

    @property
    def core_info_keywords(self):
        return self._config.get(
            "core_info_keywords",
            "我是谁,我的名字,我的身份,我的职责",
        )

    # ========== 插嘴系统 ==========
    @property
    def interject_enabled(self):
        return self._parse_bool(self._config.get("interject_enabled"), False)

    @property
    def interject_whitelist(self):
        whitelist = self._config.get("interject_whitelist", [])
        if isinstance(whitelist, str):
            whitelist = [g.strip() for g in whitelist.split(",") if g.strip()]
        return whitelist

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
    def interject_random_bypass_rate(self):
        return float(self._config.get("interject_random_bypass_rate", 0.1))

    @property
    def interject_analyze_count(self):
        return int(self._config.get("interject_analyze_count", 15))

    # ========== 阈值系统 ==========
    @property
    def eavesdrop_message_threshold(self):
        return int(self._config.get("eavesdrop_message_threshold", 20))

    @property
    def eavesdrop_threshold_min(self):
        return int(self._config.get("eavesdrop_threshold_min", 10))

    @property
    def eavesdrop_threshold_max(self):
        return int(self._config.get("eavesdrop_threshold_max", 50))

    # ========== SAN 精力系统 ==========
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

    # ========== 欲望系统 ==========
    @property
    def leaky_integrator_enabled(self):
        return self._parse_bool(self._config.get("leaky_integrator_enabled"), True)

    @property
    def leaky_decay_factor(self):
        return float(self._config.get("leaky_decay_factor", 0.9))

    @property
    def leaky_trigger_threshold(self):
        return int(self._config.get("leaky_trigger_threshold", 5))

    @property
    def interest_boost(self):
        return float(self._config.get("interest_boost", 2.0))

    @property
    def daily_chat_boost(self):
        return float(self._config.get("daily_chat_boost", 1.0))

    @property
    def desire_cooldown_messages(self):
        return int(self._config.get("desire_cooldown_messages", 5))

    @property
    def desire_cooldown_seconds(self):
        return int(self._config.get("desire_cooldown_seconds", 60))

    # ========== 分层失活 ==========
    @property
    def dropout_enabled(self):
        return self._parse_bool(self._config.get("dropout_enabled"), True)

    @property
    def dropout_edge_rate(self):
        return float(self._config.get("dropout_edge_rate", 0.2))

    # ========== 辩论系统 ==========
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
            "你是一个无情的安全审查员，代号螺丝咕姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。",
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
            '[{"name": "螺丝咕姆", "system_prompt": "你是一个无情的安全审查员，代号螺丝咕姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。"}, {"name": "阮梅", "system_prompt": "你是一个天才的生物学博士，代号阮梅。你的职责是从生物学和复杂系统角度审查代码提案，评估其自洽性、涌现行为和演化潜力。你说话温柔但一针见血。"}]',
        )
        if isinstance(agents, str):
            try:
                return json.loads(agents)
            except Exception:
                return []
        return agents

    # ========== 惊奇/内心独白/无聊 ==========
    @property
    def surprise_enabled(self):
        return self._parse_bool(self._config.get("surprise_enabled"), True)

    @property
    def surprise_boost_keywords(self):
        return self._config.get(
            "surprise_boost_keywords",
            "突然|惊讶|没想到|居然",
        )

    @property
    def inner_monologue_enabled(self):
        return self._parse_bool(self._config.get("inner_monologue_enabled"), True)

    @property
    def boredom_enabled(self):
        return self._parse_bool(self._config.get("boredom_enabled"), True)

    @property
    def boredom_consecutive_count(self):
        return int(self._config.get("boredom_consecutive_count", 10))

    # ========== 表情包 ==========
    @property
    def sticker_learning_enabled(self):
        return self._parse_bool(self._config.get("sticker_learning_enabled"), False)

    @property
    def sticker_target_qq(self):
        return self._config.get("sticker_target_qq", "")

    @property
    def sticker_tag_cooldown(self):
        return int(self._config.get("sticker_tag_cooldown", 5))

    @property
    def sticker_daily_limit(self):
        return int(self._config.get("sticker_daily_limit", 50))

    @property
    def sticker_total_limit(self):
        return int(self._config.get("sticker_total_limit", 100))

    @property
    def sticker_send_cooldown(self):
        return int(self._config.get("sticker_send_cooldown", 30))

    # ========== 其他 ==========
    @property
    def debug_log_enabled(self):
        return self._parse_bool(self._config.get("debug_log_enabled"), False)

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
