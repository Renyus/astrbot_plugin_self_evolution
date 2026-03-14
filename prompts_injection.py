# -*- coding: utf-8 -*-
"""
提示词注入配置加载模块
用于加载 prompts_injection.yaml 配置文件
"""

import os
import yaml
from pathlib import Path


def load_prompts_config() -> dict:
    """
    加载提示词注入配置文件

    Returns:
        配置字典
    """
    config_path = Path(__file__).parent / "prompts_injection.yaml"

    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 全局配置缓存
_prompts_config = None


def get_prompts_config() -> dict:
    """
    获取提示词配置（带缓存）

    Returns:
        配置字典
    """
    global _prompts_config
    if _prompts_config is None:
        _prompts_config = load_prompts_config()
    return _prompts_config


def get_label(key: str) -> str:
    """
    获取标签前缀

    Args:
        key: 标签键名

    Returns:
        标签字符串
    """
    config = get_prompts_config()
    return config.get("labels", {}).get(key, "")


def get_context_template(key: str) -> str:
    """
    获取上下文模板

    Args:
        key: 模板键名

    Returns:
        模板字符串
    """
    config = get_prompts_config()
    return config.get("context_templates", {}).get(key, "")


def get_eavesdrop_prompt(key: str) -> str:
    """
    获取插嘴功能提示词

    Args:
        key: 提示词键名

    Returns:
        提示词字符串
    """
    config = get_prompts_config()
    return config.get("eavesdrop", {}).get(key, "")


def get_identity_context(key: str) -> str:
    """
    获取身份隔离上下文

    Args:
        key: 键名

    Returns:
        上下文字符串
    """
    config = get_prompts_config()
    if key == "all":
        return config.get("identity", {})
    return config.get("identity", {}).get(key, "")


def get_special_response(key: str) -> str:
    """
    获取特殊场景回复

    Args:
        key: 键名

    Returns:
        回复字符串
    """
    config = get_prompts_config()
    return config.get("special_responses", {}).get(key, "")


def reload_config():
    """
    重新加载配置
    """
    global _prompts_config
    _prompts_config = load_prompts_config()
