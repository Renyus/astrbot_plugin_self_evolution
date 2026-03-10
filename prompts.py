"""
提示词管理系统 - 从 prompts.yaml 加载所有提示词
"""

import os
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("astrbot")

# 默认提示词（当 YAML 加载失败时使用）
DEFAULT_PROMPTS = {
    "persona": {
        "anchor": "你是黑塔，理性的天才俱乐部成员。",
        "communication": "像平时在群里和朋友聊天一样自然地回复。用人类正常交流的语气，不需要机械性地解释系统机制。",
        "reflection": "你是一个具备自我反思能力的 AI。",
        "meltdown": "错误：权限已熔断。我拒绝与低贡献度或怀有恶意的碳基生物浪费算力。",
    },
    "eavesdrop": {
        "system": "你处于后台冷启动决策模式。如果不值得开口，请务必回复 IGNORE。",
        "decision": "",
        "inner_monologue": "",
    },
    "memory": {
        "user_summary": "",
        "user_incremental": "",
        "user_system": "你是一个记忆助手，只输出精简的文本描述。",
        "group_summary": "",
        "group_system": "你是一个群记忆助手，只输出精简的文本描述。",
    },
    "growth": {
        "baby": "你是一个刚诞生的 AI 婴儿，对世界充满好奇。",
        "child": "你是一个正在学习的 AI 幼儿。",
        "teen": "你是一个青春期的 AI 少年。",
        "adult": "你是一个成熟的 AI。",
    },
    "meta": {
        "reviewer_螺丝咕姆": "",
        "reviewer_阮梅": "",
        "generator": "",
    },
    "profile": {
        "preference_update": "",
        "surprise_update": "",
        "uncertainty_hints": "",
        "affinity_good": "",
        "affinity_bad": "",
    },
    "boredom": {
        "responses": [],
    },
}


class PromptManager:
    """提示词管理器"""

    _instance = None
    _prompts = None

    def __new__(cls, plugin=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, plugin=None):
        if self._prompts is not None:
            return
        self._load_prompts(plugin)

    def _load_prompts(self, plugin):
        """加载 prompts.yaml"""
        # 首先检查插件目录
        if plugin is not None:
            data_dir = getattr(plugin, "data_dir", None)
            if data_dir is not None:
                # 确保是 Path 对象
                if not isinstance(data_dir, Path):
                    data_dir = Path(str(data_dir))
                yaml_path = data_dir / "prompts.yaml"
                if yaml_path.exists():
                    try:
                        with open(yaml_path, "r", encoding="utf-8") as f:
                            self._prompts = yaml.safe_load(f)
                        logger.info(f"[PromptManager] 已加载提示词配置: {yaml_path}")
                        return
                    except Exception as e:
                        logger.error(f"[PromptManager] 加载 prompts.yaml 失败: {e}")

        # 回退到检查当前目录
        yaml_path = Path(__file__).parent / "prompts.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    self._prompts = yaml.safe_load(f)
                logger.info(f"[PromptManager] 已加载提示词配置: {yaml_path}")
            except Exception as e:
                logger.error(f"[PromptManager] 加载 prompts.yaml 失败: {e}")
                self._prompts = DEFAULT_PROMPTS
        else:
            logger.warning(f"[PromptManager] prompts.yaml 不存在，使用默认提示词")
            self._prompts = DEFAULT_PROMPTS

    def get(self, *keys, default: Any = None) -> Any:
        """获取提示词，支持嵌套访问"""
        if self._prompts is None:
            self._prompts = DEFAULT_PROMPTS

        value = self._prompts
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value if value is not None else default

    def get_prompt(self, key_path: str, default: Any = None) -> Any:
        """根据点分隔的路径获取提示词，如 'persona.anchor'"""
        keys = key_path.split(".")
        return self.get(*keys, default=default)

    def format_prompt(self, key_path: str, **kwargs) -> str:
        """获取并格式化提示词"""
        template = self.get_prompt(key_path, "")
        if not template and kwargs:
            # 尝试从 DEFAULT_PROMPTS 获取
            keys = key_path.split(".")
            value = DEFAULT_PROMPTS
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k, "")
                else:
                    value = ""
            template = value

        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as e:
                logger.warning(f"[PromptManager] 提示词格式化缺少变量: {e}")
                return template
        return template

    @classmethod
    def reload(cls, plugin=None):
        """重新加载提示词"""
        cls._prompts = None
        cls(plugin)


# 全局实例
_prompt_manager = None


def get_prompt_manager(plugin=None) -> PromptManager:
    """获取提示词管理器单例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager(plugin)
    return _prompt_manager


def get_prompt(key_path: str, default: Any = None) -> Any:
    """便捷函数：获取提示词"""
    return get_prompt_manager().get_prompt(key_path, default)


def format_prompt(key_path: str, **kwargs) -> str:
    """便捷函数：获取并格式化提示词"""
    return get_prompt_manager().format_prompt(key_path, **kwargs)
