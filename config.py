"""
配置系统 - 从主类中解耦所有配置属性
"""

import logging
import os

logger = logging.getLogger("astrbot")


class PluginConfig:
    """插件配置类 - 集中管理所有配置项"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._prompts = None

    @property
    def _config(self):
        return self.plugin.config

    @property
    def _parse_bool(self):
        return self.plugin._parse_bool

    def _get_prompts(self):
        """获取提示词管理器"""
        if self._prompts is None:
            try:
                from .prompts import get_prompt_manager

                self._prompts = get_prompt_manager(self.plugin)
            except Exception as e:
                logger.warning(f"[Config] 加载提示词管理器失败: {e}")
                from .prompts import DEFAULT_PROMPTS

                self._prompts = DEFAULT_PROMPTS
        return self._prompts

    def _prompt(self, key_path: str, default: str = "") -> str:
        """从 prompts.yaml 获取提示词"""
        try:
            pm = self._get_prompts()
            if hasattr(pm, "format_prompt"):
                return pm.format_prompt(key_path) or default
            elif isinstance(pm, dict):
                keys = key_path.split(".")
                value = pm
                for k in keys:
                    value = value.get(k, default)
                return value or default
            return default
        except Exception:
            return default

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
    def profile_group_whitelist(self):
        """用户画像构建的群号白名单，空列表表示所有群"""
        whitelist = self._config.get("profile_group_whitelist", [])
        if isinstance(whitelist, str):
            whitelist = [g.strip() for g in whitelist.split(",") if g.strip()]
        return [str(g) for g in whitelist]

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
        return self._prompt("persona.meltdown", "错误：权限已熔断。")

    @property
    def prompt_communication_guidelines(self):
        return self._prompt(
            "persona.communication", "像平时在群里和朋友聊天一样自然地回复。"
        )

    @property
    def prompt_dream_user_summary(self):
        return self._prompt("memory.user_summary", "总结用户的特征和偏好。")

    @property
    def prompt_dream_user_incremental(self):
        return self._prompt("memory.user_incremental", "增量更新用户画像。")

    @property
    def prompt_dream_user_system(self):
        return self._prompt(
            "memory.user_system", "你是一个记忆助手，只输出精简的文本描述。"
        )

    @property
    def prompt_dream_group_system(self):
        return self._prompt(
            "memory.group_system", "你是一个群记忆助手，只输出精简的文本描述。"
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
        return float(self._config.get("interest_boost", 2.0))

    @property
    def daily_chat_boost(self):
        return float(self._config.get("daily_chat_boost", 1.0))

    @property
    def desire_cooldown_messages(self):
        return int(self._config.get("desire_cooldown_messages", 5))

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
    def eavesdrop_interval_minutes(self):
        return int(self._config.get("eavesdrop_interval_minutes", 10))

    @property
    def eavesdrop_message_threshold(self):
        return int(self._config.get("eavesdrop_message_threshold", 20))

    @property
    def eavesdrop_threshold_min(self):
        return int(self._config.get("eavesdrop_threshold_min", 10))

    @property
    def eavesdrop_threshold_max(self):
        return int(self._config.get("eavesdrop_threshold_max", 50))

    @property
    def session_cleanup_timeout(self):
        return int(self._config.get("session_cleanup_timeout", 600))

    @property
    def session_auto_commit(self):
        return self._parse_bool(self._config.get("session_auto_commit"), True)

    @property
    def private_session_enabled(self):
        return self._parse_bool(self._config.get("private_session_enabled"), True)

    @property
    def auto_memory_recall_enabled(self):
        return self._parse_bool(self._config.get("auto_memory_recall_enabled"), True)

    @property
    def session_evicted_max(self):
        return int(self._config.get("session_evicted_max", 30))

    @property
    def session_commit_threshold(self):
        return int(self._config.get("session_commit_threshold", 5))

    def get(self, key, default=None):
        """通用获取配置"""
        return self._config.get(key, default)
