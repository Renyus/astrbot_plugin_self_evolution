"""
SAN 值系统 - 心智疲劳与精力管理
定时获取群消息，分析群状态，动态调整 SAN 值
"""

import time
import logging
import asyncio
import json

logger = logging.getLogger("astrbot")

SAN_ANALYZE_PROMPT = """分析以下群聊消息，输出 JSON：

{{
    "activity": "high/medium/low",
    "emotion": "positive/neutral/negative",
    "has_drama": true/false,
    "summary": "一句话总结"
}}

消息列表：
{msg_content}
"""


class SANSystem:
    """SAN (Sanity/精力值) 系统 - 模拟心智疲劳，动态分析群状态"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._san_value = None
        self._san_last_recovery = None
        self._last_analyze_time = 0

    @property
    def enabled(self):
        return self.plugin.cfg.san_enabled

    @property
    def max_value(self):
        return self.plugin.cfg.san_max

    @property
    def cost_per_message(self):
        return self.plugin.cfg.san_cost_per_message

    @property
    def recovery_per_hour(self):
        return self.plugin.cfg.san_recovery_per_hour

    @property
    def low_threshold(self):
        return self.plugin.cfg.san_low_threshold

    @property
    def auto_analyze_enabled(self):
        return self.plugin.cfg.san_auto_analyze_enabled

    @property
    def analyze_interval(self):
        return self.plugin.cfg.san_analyze_interval

    @property
    def msg_count_per_group(self):
        return self.plugin.cfg.san_msg_count_per_group

    @property
    def high_activity_boost(self):
        return self.plugin.cfg.san_high_activity_boost

    @property
    def low_activity_drain(self):
        return self.plugin.cfg.san_low_activity_drain

    @property
    def positive_vibe_bonus(self):
        return self.plugin.cfg.san_positive_vibe_bonus

    @property
    def negative_vibe_penalty(self):
        return self.plugin.cfg.san_negative_vibe_penalty

    def initialize(self):
        if not self.enabled:
            return
        if self._san_value is None:
            self._san_value = self.max_value
            self._san_last_recovery = time.time()
            self._last_analyze_time = time.time()
            logger.info(f"[SAN] 精力值系统初始化: {self._san_value}/{self.max_value}")

    def update(self):
        if not self.enabled:
            return True

        if self._san_value is None:
            self._san_value = self.max_value
            self._san_last_recovery = time.time()
            logger.info(f"[SAN] 精力值已初始化: {self._san_value}/{self.max_value}")

        current_time = time.time()
        elapsed = current_time - (self._san_last_recovery or current_time)

        if elapsed > 3600:
            recovered = int(elapsed / 3600) * self.recovery_per_hour
            self._san_value = min(self.max_value, self._san_value + recovered)
            self._san_last_recovery = current_time
            logger.info(
                f"[SAN] 精力恢复 +{recovered}: {self._san_value}/{self.max_value}"
            )

        if self._san_value <= 0:
            logger.warning(f"[SAN] 精力耗尽，拒绝服务")
            return False

        consumed = self.cost_per_message
        self._san_value = max(0, self._san_value - consumed)
        logger.debug(f"[SAN] 精力消耗 -{consumed}: {self._san_value}/{self.max_value}")
        return True

    def get_status(self):
        if not self.enabled:
            return ""
        if self._san_value is None:
            status = "精力充沛"
            logger.debug(f"[SAN] 获取状态: {status}")
            return status
        ratio = self._san_value / self.max_value
        if ratio < 0.2:
            status = "疲惫不堪"
        elif ratio < 0.5:
            status = "略有疲态"
        else:
            status = "精力充沛"
        logger.debug(f"[SAN] 获取状态: {status} ({self._san_value}/{self.max_value})")
        return status

    def get_prompt_injection(self):
        if not self.enabled:
            return ""
        return f"\n\n【当前状态】{self.get_status()}"

    @property
    def value(self):
        return self._san_value or 0

    async def analyze_all_groups(self):
        """定时分析所有群的消息，动态调整 SAN 值"""
        if not self.enabled or not self.auto_analyze_enabled:
            return

        current_time = time.time()
        if current_time - self._last_analyze_time < self.analyze_interval * 60:
            logger.debug(f"[SAN] 距上次分析不足 {self.analyze_interval} 分钟，跳过")
            return

        self._last_analyze_time = current_time
        logger.info(f"[SAN] 开始分析群状态...")

        try:
            listened_groups = self._get_listened_groups()
            if not listened_groups:
                logger.debug("[SAN] 无监听的群，跳过分析")
                return

            total_change = 0
            for group_id in listened_groups:
                change = await self._analyze_group(group_id)
                if change != 0:
                    total_change += change
                    logger.info(f"[SAN] 群 {group_id} 分析完成，SAN 变化: {change:+d}")

            if total_change != 0:
                self._san_value = max(
                    0, min(self.max_value, self._san_value + total_change)
                )
                logger.info(
                    f"[SAN] 群分析完成，总 SAN 变化: {total_change:+d}, 当前值: {self._san_value}/{self.max_value}"
                )

        except Exception as e:
            logger.warning(f"[SAN] 群分析异常: {e}")

    def _get_listened_groups(self):
        """获取需要监听的群列表"""
        if hasattr(self.plugin, "eavesdropping") and hasattr(
            self.plugin.eavesdropping, "active_users"
        ):
            return list(self.plugin.eavesdropping.active_users.keys())
        whitelist = getattr(self.plugin.cfg, "profile_group_whitelist", [])
        return whitelist if whitelist else []

    async def _analyze_group(self, group_id: str) -> int:
        """分析单个群的状态，返回 SAN 值变化"""
        try:
            messages = await self._fetch_group_messages(group_id)
            if not messages:
                logger.debug(f"[SAN] 群 {group_id} 无消息")
                return self.low_activity_drain

            analysis = await self._llm_analyze(messages)
            if not analysis:
                return 0

            return self._calculate_san_change(analysis)

        except Exception as e:
            logger.warning(f"[SAN] 群 {group_id} 分析失败: {e}")
            return 0

    async def _fetch_group_messages(self, group_id: str) -> list:
        """通过 NapCat API 获取群消息"""
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                logger.debug("[SAN] 无平台实例")
                return []

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                logger.debug("[SAN] 平台不支持获取 bot")
                return []

            bot = platform.get_client()
            if not bot:
                logger.debug("[SAN] 无法获取 bot 实例")
                return []

            result = await bot.call_action(
                "get_group_msg_history",
                group_id=int(group_id),
                count=self.msg_count_per_group,
            )

            messages = result.get("messages", [])
            formatted = []
            for msg in messages:
                sender = msg.get("sender", {})
                nickname = sender.get("nickname", "未知")
                message = msg.get("message", "")
                if message:
                    formatted.append(f"{nickname}: {message}")

            return formatted

        except Exception as e:
            logger.warning(f"[SAN] 获取群消息失败: {e}")
            return []

    async def _llm_analyze(self, messages: list) -> dict:
        """调用 LLM 分析群状态"""
        if not messages:
            return None

        try:
            llm_provider = self.plugin.context.get_using_provider("qq")
            if not llm_provider:
                logger.warning("[SAN] 无法获取 LLM Provider")
                return None

            msg_content = "\n".join(messages[:20])
            prompt = SAN_ANALYZE_PROMPT.format(msg_content=msg_content)

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个群聊分析助手，只输出 JSON，不要其他内容。",
            )

            text = res.completion_text.strip()
            if "{" in text:
                json_str = text[text.find("{") : text.rfind("}") + 1]
                return json.loads(json_str)

        except Exception as e:
            logger.warning(f"[SAN] LLM 分析失败: {e}")

        return None

    def _calculate_san_change(self, analysis: dict) -> int:
        """根据分析结果计算 SAN 值变化"""
        change = 0

        activity = analysis.get("activity", "medium")
        emotion = analysis.get("emotion", "neutral")
        has_drama = analysis.get("has_drama", False)

        if activity == "high":
            change += self.high_activity_boost
        elif activity == "low":
            change += self.low_activity_drain

        if emotion == "positive":
            change += self.positive_vibe_bonus
        elif emotion == "negative":
            change += self.negative_vibe_penalty

        if has_drama:
            change += self.negative_vibe_penalty

        return change
