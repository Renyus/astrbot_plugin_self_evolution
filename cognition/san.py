"""
SAN 值系统 - 心智疲劳与精力管理
定时获取群消息，分析群状态，动态调整 SAN 值
"""

import asyncio
import json
import logging
import time

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

    def initialize(self):
        if not self.enabled:
            return
        if self._san_value is None:
            self._san_value = self.max_value
            self._san_last_recovery = time.time()
            self._last_analyze_time = time.time()
            logger.debug(f"[SAN] 精力值系统初始化: {self._san_value}/{self.max_value}")

    async def update(self):
        if not self.enabled:
            return True

        persona_sim = getattr(self.plugin, "persona_sim", None)
        if persona_sim:
            scope_id = getattr(self.plugin, "_current_scope_id", None)
            if scope_id:
                logger.debug("[SAN] update 跳过，Persona Sim 负责能量管理")
            return True

        if self._san_value is None:
            self._san_value = self.max_value
            self._san_last_recovery = time.time()
            logger.debug(f"[SAN] 精力值已初始化: {self._san_value}/{self.max_value}")

        current_time = time.time()
        elapsed = current_time - (self._san_last_recovery or current_time)

        if elapsed > 3600:
            recovered = int(elapsed / 3600) * self.recovery_per_hour
            self._san_value = min(self.max_value, self._san_value + recovered)
            self._san_last_recovery = current_time
            logger.debug(f"[SAN] 精力恢复 +{recovered}: {self._san_value}/{self.max_value}")

        if self._san_value <= 0:
            logger.warning("[SAN] 精力耗尽，拒绝服务")
            return False

        consumed = self.cost_per_message
        self._san_value = max(0, self._san_value - consumed)
        logger.debug(f"[SAN] 精力消耗 -{consumed}: {self._san_value}/{self.max_value}")
        return True

    def get_status(self):
        if not self.enabled:
            return ""

        persona_sim = getattr(self.plugin, "persona_sim", None)
        if persona_sim:
            logger.debug("[SAN] get_status 跳过，Persona Sim 提供状态叙事")
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
        if getattr(self.plugin, "persona_sim", None):
            return ""
        status = self.get_status()
        if not status:
            return ""
        return f"\n\n【当前状态】{status}"

    @property
    def value(self):
        return self._san_value or 0

    def set_value(self, new_value: int) -> int:
        """管理员手动设置精力值，返回实际设置值（会裁剪到合法区间 [0, max_value]）"""
        if not self.enabled:
            return -1
        self._san_value = max(0, min(self.max_value, int(new_value)))
        return self._san_value

    async def analyze_all_groups(self):
        """定时分析所有群的消息，将结果注入 Persona Sim"""
        if not self.enabled or not self.auto_analyze_enabled:
            return

        current_time = time.time()
        if current_time - self._last_analyze_time < self.analyze_interval * 60:
            logger.debug(f"[SAN] 距上次分析不足 {self.analyze_interval} 分钟，跳过")
            return

        self._last_analyze_time = current_time
        logger.debug("[SAN] 开始分析群状态...")

        persona_sim = getattr(self.plugin, "persona_sim", None)

        try:
            listened_groups = await self._get_listened_groups()
            if not listened_groups:
                logger.debug("[SAN] 无监听的群，跳过分析")
                return

            for group_id in listened_groups:
                analysis = await self._analyze_group(group_id)
                if analysis is None:
                    continue

                quality = self._analysis_to_quality(analysis)
                if quality and persona_sim:
                    try:
                        await persona_sim.tick(str(group_id), interaction_quality=quality)
                        logger.debug(f"[SAN] 群 {group_id} 分析→{quality}，已注入 Persona Sim")
                    except Exception as e:
                        logger.warning(f"[SAN] 群 {group_id} 注入 Persona Sim 失败: {e}")

        except Exception as e:
            logger.warning(f"[SAN] 群分析异常: {e}")

    async def _get_listened_groups(self):
        """获取需要监听的群列表"""
        # 方式1: 白名单配置
        whitelist = self.plugin.cfg.target_scopes
        if whitelist:
            logger.debug(f"[SAN] 使用白名单群列表: {whitelist}")
            return whitelist
        # 方式2: eavesdropping get_active_scopes()
        eavesdropping = getattr(self.plugin, "eavesdropping", None)
        if eavesdropping and hasattr(eavesdropping, "get_active_scopes"):
            groups = [g for g in eavesdropping.get_active_scopes() if not g.startswith("private_")]
            if groups:
                logger.debug(f"[SAN] 使用 eavesdropping 活跃群列表: {groups}")
                return groups
        # 方式3: 通过 platform 获取 bot 加入的群列表
        return await self._fetch_groups_from_platform()

    async def _fetch_groups_from_platform(self):
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                return []

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                return []

            bot = platform.get_client()
            if not bot:
                return []

            result = await bot.call_action("get_group_list")
            if isinstance(result, list):
                groups_data = result
            elif isinstance(result, dict):
                groups_data = result.get("data", [])
            else:
                groups_data = []
            return [str(g.get("group_id", "")) for g in groups_data if g.get("group_id")]
        except Exception as e:
            logger.debug(f"[SAN] 获取群列表失败: {e}")
            return []

    async def _analyze_group(self, group_id: str) -> dict | None:
        """分析单个群的状态，返回完整分析结果"""
        try:
            messages = await self._fetch_group_messages(group_id)
            if not messages:
                return None

            group_umo = self.plugin.get_group_umo(group_id) if hasattr(self.plugin, "get_group_umo") else None
            analysis = await self._llm_analyze(messages, umo=group_umo)
            return analysis if analysis else None

        except Exception as e:
            logger.warning(f"[SAN] 群 {group_id} 分析失败: {e}")
            return None

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
            import asyncio
            from ..engine.context_injection import parse_message_chain

            formatted = await asyncio.gather(*[parse_message_chain(msg, self.plugin) for msg in messages])
            return [f for f in formatted if f]

        except Exception as e:
            logger.warning(f"[SAN] 获取群消息失败: {e}")
            return []

    async def _llm_analyze(self, messages: list, umo: str | None = None) -> dict:
        """调用 LLM 分析群状态"""
        if not messages:
            return None

        try:
            llm_provider = self.plugin.context.get_using_provider(umo=umo)
            if not llm_provider:
                logger.warning("[SAN] 无法获取 LLM Provider")
                return None

            msg_content = "\n".join(messages[:20])
            prompt = SAN_ANALYZE_PROMPT.format(msg_content=msg_content)

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个会话分析助手，只输出 JSON，不要其他内容。",
            )

            text = res.completion_text.strip()
            if "{" in text:
                json_str = text[text.find("{") : text.rfind("}") + 1]
                return json.loads(json_str)

        except Exception as e:
            logger.warning(f"[SAN] LLM 分析失败: {e}")

        return None

    def _analysis_to_quality(self, analysis: dict) -> str | None:
        """将 LLM 分析结果映射为 interaction_quality"""
        activity = analysis.get("activity", "medium")
        emotion = analysis.get("emotion", "neutral")
        has_drama = analysis.get("has_drama", False)

        if has_drama:
            return "bad"
        if activity == "low" and emotion == "negative":
            return "bad"
        if activity == "low":
            return "awkward"
        if emotion == "negative":
            return "awkward"
        if activity == "high" and emotion == "positive":
            return "good"
        return "normal"
