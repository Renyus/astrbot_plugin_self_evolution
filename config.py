"""
配置系统 - 从主类中解耦所有配置属性
"""

import logging

logger = logging.getLogger("astrbot")


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
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return self._config.get(name)

    @property
    def persona_name(self):
        return self._config.get("persona_name", "黑塔")

    @property
    def persona_title(self):
        return self._config.get("persona_title", "人偶负责人")

    @property
    def persona_style(self):
        return self._config.get("persona_style", "理性、犀利且专业")

    @property
    def interjection_desire(self):
        return int(self._config.get("interjection_desire", 5))

    @property
    def critical_keywords(self):
        return self._config.get(
            "critical_keywords",
            "黑塔|空间站|人偶|天才|模拟宇宙|研究|论文|技术|算力|数据",
        )

    @property
    def buffer_threshold(self):
        return int(self._config.get("buffer_threshold", 8))

    @property
    def max_buffer_size(self):
        return int(self._config.get("max_buffer_size", 20))

    @property
    def review_mode(self):
        return self._parse_bool(self._config.get("review_mode"), True)

    @property
    def memory_kb_name(self):
        return self._config.get("memory_kb_name", "self_evolution_memory")

    @property
    def reflection_schedule(self):
        return self._config.get("reflection_schedule", "0 2 * * *")

    @property
    def allow_meta_programming(self):
        return self._parse_bool(self._config.get("allow_meta_programming"), False)

    @property
    def core_principles(self):
        return self._config.get("core_principles", "保持理性、诚实、守法。")

    @property
    def admin_users(self):
        return self._config.get("admin_users", [])

    @property
    def timeout_memory_commit(self):
        return float(self._config.get("timeout_memory_commit", 10.0))

    @property
    def timeout_memory_recall(self):
        return float(self._config.get("timeout_memory_recall", 12.0))

    @property
    def max_memory_entries(self):
        return int(self._config.get("max_memory_entries", 100))

    @property
    def enable_profile_update(self):
        return self._parse_bool(self._config.get("enable_profile_update"), True)

    @property
    def enable_context_recall(self):
        return self._parse_bool(self._config.get("enable_context_recall"), True)

    @property
    def dream_enabled(self):
        return self._parse_bool(self._config.get("dream_enabled"), True)

    @property
    def dream_schedule(self):
        return self._config.get("dream_schedule", "0 3 * * *")

    @property
    def dream_max_users(self):
        return int(self._config.get("dream_max_users", 10))

    @property
    def dream_concurrency(self):
        return int(self._config.get("dream_concurrency", 3))

    @property
    def prompt_meltdown_message(self):
        return self._config.get(
            "prompt_meltdown_message",
            "检测到异常数据后，优雅拒绝或引导话题",
        )

    @property
    def prompt_reflection_instruction(self):
        return self._config.get(
            "prompt_reflection_instruction",
            "你是一个具备自我反思能力的 AI。",
        )

    @property
    def prompt_anchor_injection(self):
        return self._config.get(
            "prompt_anchor_injection",
            "你是黑塔，理性的天才俱乐部成员。",
        )

    @property
    def prompt_communication_guidelines(self):
        return self._config.get(
            "prompt_communication_guidelines",
            "保持理性、专业的态度。",
        )

    @property
    def prompt_eavesdrop_system(self):
        return self._config.get(
            "prompt_eavesdrop_system",
            "你是一个旁观者，观察并学习群聊中的对话。",
        )

    @property
    def prompt_dream_user_summary(self):
        return self._config.get(
            "prompt_dream_user_summary",
            "总结用户的特征和偏好。",
        )

    @property
    def prompt_dream_user_incremental(self):
        return self._config.get(
            "prompt_dream_user_incremental",
            "增量更新用户画像。",
        )

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
    def group_vibe_enabled(self):
        return self._parse_bool(self._config.get("group_vibe_enabled"), True)

    @property
    def memory_distortion_rate(self):
        return float(self._config.get("memory_distortion_rate", 0.05))

    @property
    def curiosity_enabled(self):
        return self._parse_bool(self._config.get("curiosity_enabled"), True)

    @property
    def curiosity_silence_hours(self):
        return int(self._config.get("curiosity_silence_hours", 12))

    @property
    def internal_council_enabled(self):
        return self._parse_bool(self._config.get("internal_council_enabled"), True)

    @property
    def controversial_keywords(self):
        return self._config.get(
            "controversial_keywords",
            "政治|宗教|战争|争议",
        )

    @property
    def prompt_dream_user_system(self):
        return self._config.get(
            "prompt_dream_user_system",
            "梦境系统 - 用户分析",
        )

    @property
    def prompt_dream_group_summary(self):
        return self._config.get(
            "prompt_dream_group_summary",
            "总结群组的特征。",
        )

    @property
    def prompt_dream_group_system(self):
        return self._config.get(
            "prompt_dream_group_system",
            "梦境系统 - 群组分析",
        )

    @property
    def dropout_enabled(self):
        return self._parse_bool(self._config.get("dropout_enabled"), True)

    @property
    def dropout_edge_rate(self):
        return float(self._config.get("dropout_edge_rate", 0.2))

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
        return int(self._config.get("interest_boost", 2))

    @property
    def daily_chat_boost(self):
        return int(self._config.get("daily_chat_boost", 1))

    @property
    def core_info_keywords(self):
        return self._config.get(
            "core_info_keywords",
            "我是谁|我的名字|我的身份|我的职责",
        )

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
            "你是一个严格的代码审查员。",
        )

    @property
    def debate_criteria(self):
        return self._config.get(
            "debate_criteria",
            "代码质量|安全性|性能",
        )

    @property
    def debate_agents(self):
        return self._config.get(
            "debate_agents",
            [
                {"name": "黑塔", "role": "generator"},
                {"name": "螺丝咕姆", "role": "reviewer"},
            ],
        )

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
    def graph_enabled(self):
        return self._parse_bool(self._config.get("graph_enabled"), True)

    @property
    def inner_monologue_enabled(self):
        return self._parse_bool(self._config.get("inner_monologue_enabled"), True)

    @property
    def boredom_enabled(self):
        return self._parse_bool(self._config.get("boredom_enabled"), True)

    @property
    def boredom_threshold(self):
        return float(self._config.get("boredom_threshold", 0.3))

    @property
    def boredom_consecutive_count(self):
        return int(self._config.get("boredom_consecutive_count", 10))

    @property
    def boredom_sarcastic_reply(self):
        return self._config.get(
            "boredom_sarcastic_reply",
            "你们是真无聊啊...要不我下线算了?",
        )

    @property
    def growth_enabled(self):
        return self._parse_bool(self._config.get("growth_enabled"), True)

    @property
    def growth_stage(self):
        return self._config.get("growth_stage", "婴儿")

    @property
    def experience_points(self):
        return int(self._config.get("experience_points", 0))

    @property
    def total_messages(self):
        return int(self._config.get("total_messages", 0))

    @property
    def birth_timestamp(self):
        return int(self._config.get("birth_timestamp", 0))

    @property
    def vocabulary_complexity(self):
        return int(self._config.get("vocabulary_complexity", 1))

    @property
    def emotional_dependence(self):
        return int(self._config.get("emotional_dependence", 10))

    @property
    def growth_prompt_baby(self):
        return self._config.get(
            "growth_prompt_baby",
            "你是一个刚诞生的AI婴儿，对世界充满好奇。",
        )

    @property
    def growth_prompt_child(self):
        return self._config.get(
            "growth_prompt_child",
            "你是一个正在学习的AI幼儿。",
        )

    @property
    def growth_prompt_teen(self):
        return self._config.get(
            "growth_prompt_teen",
            "你是一个青春期的AI少年。",
        )

    @property
    def growth_prompt_adult(self):
        return self._config.get(
            "growth_prompt_adult",
            "你是一个成熟的AI，拥有完整的知识和独立的人格。理性、犀利且专业。",
        )

    def get(self, key, default=None):
        """通用获取配置"""
        return self._config.get(key, default)
