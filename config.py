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

    def _get_nested_float(self, group: str, key: str, default=0.0):
        val = self._get_nested(group, key, default)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _get_nested_int(self, group: str, key: str, default=0):
        val = self._get_nested(group, key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _get_nested_str(self, group: str, key: str, default=""):
        val = self._get_nested(group, key, default)
        if val is None:
            return default
        return str(val)

    def _get_nested_list(self, group: str, key: str, default=None):
        """读取 list 类型配置项。兼容旧 string（| 或 , 分割）后返回清洗后的 list。

        优先级：| 分割为主（与新 list 格式一致）；若无 | 但含逗号，则按逗号分割（兼容旧配置）。
        """
        val = self._get_nested(group, key, default)
        if val is None:
            return default if default is not None else []
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        if isinstance(val, str):
            if "|" in val:
                return [k.strip() for k in val.split("|") if k.strip()]
            if "," in val:
                return [k.strip() for k in val.split(",") if k.strip()]
            return [val.strip()] if val.strip() else []
        return [str(val).strip()]

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
        return self._get_nested_list("base", "admin_users", [])

    @property
    def target_scopes(self):
        return self._get_nested_list("base", "target_scopes", [])

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
        return self._get_nested("memory_summary", "memory_summary_schedule", "0 6 * * *")

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

    # dropout
    @property
    def dropout_enabled(self):
        return self._get_nested_bool("prompt", "dropout_enabled", True)

    @property
    def dropout_edge_rate(self):
        return float(self._get_nested("prompt", "dropout_edge_rate", 0.2))

    # surprise and monologue
    @property
    def surprise_enabled(self):
        return self._get_nested_bool("prompt", "surprise_enabled", True)

    @property
    def surprise_boost_keywords(self):
        return self._get_nested_list(
            "prompt",
            "surprise_boost_keywords",
            ["突然", "惊讶", "没想到", "居然"],
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
        return self._get_nested_list("sticker", "sticker_target_qq", [])

    @property
    def sticker_total_limit(self):
        return int(self._get_nested("sticker", "sticker_total_limit", 100))

    @property
    def sticker_send_cooldown(self):
        return int(self._get_nested("sticker", "sticker_send_cooldown", 30))

    @property
    def sticker_freq_threshold(self):
        return int(self._get_nested("sticker", "sticker_freq_threshold", 2))

    @property
    def meal_max_items(self):
        return int(self._get_nested("sticker", "meal_max_items", 100))

    @property
    def meal_eat_keywords(self):
        return self._get_nested_list(
            "sticker",
            "meal_eat_keywords",
            ["吃啥", "吃什么", "今天吃啥", "今天吃什么", "吃点啥"],
        )

    @property
    def meal_banquet_keywords(self):
        return self._get_nested_list(
            "sticker",
            "meal_banquet_keywords",
            ["摆酒席", "开席", "整一桌", "来一桌", "上菜"],
        )

    @property
    def meal_banquet_count(self):
        return int(self._get_nested("sticker", "meal_banquet_count", 5))

    @property
    def meal_banquet_cooldown_minutes(self):
        return int(self._get_nested("sticker", "meal_banquet_cooldown_minutes", 5))

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

    # moderation
    @property
    def moderation_enforcement_enabled(self):
        return self._get_nested_bool("moderation", "enforcement_enabled", False)

    @property
    def moderation_enabled(self):
        return self._get_nested_bool("moderation", "enabled", True)

    @property
    def moderation_nsfw_keywords(self):
        default = [
            "nsfw",
            "nude",
            "naked",
            "porn",
            "explicit",
            "色情",
            "裸体",
            "成人内容",
            "成人向",
            "露点",
            "性交",
            " AV ",
            "色情内容",
            "羞红",
            "sm ",
            "擦边",
            "软色情",
            "肉体",
            "肌肤",
            "身材",
            "诱惑",
            "挑逗",
            "性感",
        ]
        val = self._get_nested_list("moderation", "nsfw_keywords", default)
        return val if val else default

    @property
    def moderation_promo_keywords(self):
        default = [
            "二维码",
            "加群",
            "加我",
            "联系方式",
            "扫码",
            "邀请",
            "入群",
            "群二维码",
            "QQ号",
            "微信号",
            "TG",
            "Telegram",
            "引流",
            "推广",
            "宣传",
            "广告",
        ]
        val = self._get_nested_list("moderation", "promo_keywords", default)
        return val if val else default

    @property
    def moderation_refusal_keywords(self):
        default = [
            "无法提供",
            "无法描述",
            "无法对此",
            "无法为",
            "不适合提供",
            "不适宜提供",
            "拒绝",
            "拒绝传播",
            "无法总结",
            "色情",
            "低俗",
            "不符合",
            "不遵守",
            "无法处理",
            "无法进行",
            "不当信息",
            "不当内容",
        ]
        val = self._get_nested_list("moderation", "refusal_keywords", default)
        return val if val else default

    @property
    def moderation_nsfw_refusal_confidence(self):
        return self._get_nested_float("moderation", "nsfw_refusal_confidence", 0.9)

    @property
    def moderation_promo_refusal_confidence(self):
        return self._get_nested_float("moderation", "promo_refusal_confidence", 0.7)

    @property
    def moderation_weak_keyword_confidence(self):
        return self._get_nested_float("moderation", "weak_keyword_confidence", 0.5)

    @property
    def moderation_confidence_threshold(self):
        return self._get_nested_float("moderation", "confidence_threshold", 0.6)

    @property
    def moderation_escalation_threshold(self):
        return self._get_nested_int("moderation", "escalation_threshold", 2)

    @property
    def moderation_ban_duration_minutes(self):
        return self._get_nested_int("moderation", "ban_duration_minutes", 60)

    # moderation messages
    @property
    def moderation_nsfw_warning_message(self):
        return self._get_nested_list("moderation", "nsfw_warning_message", ["我草，色图", "我靠，这啥", "离谱"])

    @property
    def moderation_nsfw_ban_reason_message(self):
        return self._get_nested_str("moderation", "nsfw_ban_reason_message", "检测到不当内容，已处理")

    @property
    def moderation_promo_warning_message(self):
        return self._get_nested_list(
            "moderation", "promo_warning_message", ["二维码？引流是吧", "加群？爬", "引流司马"]
        )

    @property
    def moderation_promo_ban_reason_message(self):
        return self._get_nested_str("moderation", "promo_ban_reason_message", "检测到引流内容，已处理")

    # sticker_reply
    @property
    def sticker_reply_enabled(self):
        return self._get_nested_bool("sticker_reply", "enabled", False)

    @property
    def sticker_reply_chance(self):
        return self._get_nested_int("sticker_reply", "chance", 20)

    @property
    def sticker_reply_max_per_hour(self):
        return self._get_nested_int("sticker_reply", "max_per_hour", 10)

    @property
    def sticker_reply_min_text_length(self):
        return self._get_nested_int("sticker_reply", "min_text_length", 5)

    # update_notify
    @property
    def update_notify_group_id(self):
        return self._get_nested_list("update_notify", "update_notify_group_id", [])

    @property
    def update_notify_user_ids(self):
        return self._get_nested_list("update_notify", "update_notify_user_ids", [])

    @property
    def update_notify_repo(self):
        return self._get_nested("update_notify", "update_notify_repo", "Renyus/astrbot_plugin_self_evolution")

    @property
    def update_notify_branch(self):
        return self._get_nested("update_notify", "update_notify_branch", "master")

    @property
    def update_check_interval(self):
        return self._get_nested_int("update_notify", "update_check_interval", 30)
