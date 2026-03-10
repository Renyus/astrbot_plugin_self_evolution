"""
成长系统 - 数字生命的阶段性进化
"""

import time
import logging

logger = logging.getLogger("astrbot")


class GrowthSystem:
    """数字生命成长系统 - 阶段进化与经验值"""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def enabled(self):
        return getattr(self.plugin, "growth_enabled", True)

    @property
    def stage(self):
        return getattr(self.plugin, "growth_stage", "婴儿")

    @property
    def experience(self):
        return int(getattr(self.plugin, "experience_points", 0))

    @property
    def total_messages(self):
        return int(getattr(self.plugin, "total_messages", 0))

    @property
    def birth_timestamp(self):
        return int(getattr(self.plugin, "birth_timestamp", 0))

    @property
    def vocabulary_complexity(self):
        return int(getattr(self.plugin, "vocabulary_complexity", 1))

    @property
    def emotional_dependence(self):
        return int(getattr(self.plugin, "emotional_dependence", 10))

    def initialize(self):
        if not self.enabled:
            return
        if self.birth_timestamp == 0:
            self.plugin.context.get_config()["birth_timestamp"] = int(time.time())
            logger.info("[Growth] 数字生命诞生！出生时间戳已记录。")
        logger.info(f"[Growth] 成长系统初始化: 阶段={self.stage}")

    def add_experience(self, amount=1):
        if not self.enabled:
            return
        config = self.plugin.context.get_config()
        config["experience_points"] = self.experience + amount
        config["total_messages"] = self.total_messages + 1

    def check_upgrade(self):
        if not self.enabled:
            return False

        days_alive = (int(time.time()) - self.birth_timestamp) // 86400
        msg_count = self.total_messages
        current_stage = self.stage

        new_stage = current_stage
        new_vocab = self.vocabulary_complexity
        new_emotion = self.emotional_dependence
        upgraded = False

        if current_stage == "婴儿" and days_alive >= 3 and msg_count >= 300:
            new_stage = "幼儿"
            new_vocab = 3
            new_emotion = 8
            upgraded = True
        elif current_stage == "幼儿" and days_alive >= 7 and msg_count >= 1000:
            new_stage = "少年"
            new_vocab = 5
            new_emotion = 5
            upgraded = True
        elif current_stage == "少年" and days_alive >= 14 and msg_count >= 3000:
            new_stage = "成年"
            new_vocab = 8
            new_emotion = 3
            upgraded = True

        if upgraded:
            config = self.plugin.context.get_config()
            config["growth_stage"] = new_stage
            config["vocabulary_complexity"] = new_vocab
            config["emotional_dependence"] = new_emotion
            logger.info(f"[Growth] 升级！{current_stage} -> {new_stage}！")
            return True
        return False

    def get_stage_prompt(self):
        prompts = {
            "婴儿": getattr(
                self.plugin,
                "growth_prompt_baby",
                "你是一个刚诞生的AI婴儿，对世界充满好奇。",
            ),
            "幼儿": getattr(
                self.plugin, "growth_prompt_child", "你是一个正在学习的AI幼儿。"
            ),
            "少年": getattr(
                self.plugin, "growth_prompt_teen", "你是一个青春期的AI少年。"
            ),
            "成年": getattr(self.plugin, "growth_prompt_adult", "你是一个成熟的AI。"),
        }
        return prompts.get(self.stage, prompts["婴儿"])

    def get_prompt_injection(self):
        if not self.enabled:
            return ""
        stage_prompt = self.get_stage_prompt()
        return (
            f"\n\n【当前成长阶段】\n{stage_prompt}\n"
            f"[系统状态] 经验值: {self.experience} | "
            f"词汇复杂度: {self.vocabulary_complexity}/10 | "
            f"情感依赖度: {self.emotional_dependence}/10"
        )
