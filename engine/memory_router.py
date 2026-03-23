"""
记忆路由层 - 统一决策记忆写入目标

三层记忆写入类型：
- profile_fact: 人物稳定信息（identity/preference/trait）→ 写入 profile YAML
- session_event: 当天事件/约定/决策 → 写入 KB
- reflection_hint: 仅影响回复风格的自我纠偏 → 一次性注入后丢弃
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("astrbot")


class MemoryTarget(Enum):
    PROFILE = "profile"
    KNOWLEDGE_BASE = "knowledge_base"
    REFLECTION_HINT = "reflection_hint"
    DROP = "drop"


@dataclass
class MemoryDecision:
    target: MemoryTarget
    fact_type: str | None = None
    confidence: float = 1.0
    reason: str = ""


SESSION_EVENT_KEYWORDS = [
    "决定",
    "约定",
    "共识",
    "规则",
    "制度",
    "联机",
    "开会",
    "活动",
    "比赛",
    "项目",
    "报名",
    "参加",
    "时间",
    "地点",
    "安排",
    "下周",
    "明天",
    "周日",
    "周一",
    "晚上",
    "群规",
    "公告",
    "通知",
    "任务",
]

LONG_TERM_KEYWORDS = [
    "每周",
    "每月",
    "定期",
    "习惯",
    "长期",
    "一直",
    "从来",
    "永远",
    "始终",
]


class MemoryRouter:
    """
    统一路由层 - 决定记忆内容写入哪个存储目标

    路由规则：
    1. 含 SESSION_EVENT_KEYWORDS → session_event → KB
    2. 含 LONG_TERM_KEYWORDS → long_term_note → profile
    3. 身份/偏好/性格关键词 → 对应 profile 字段
    4. 自我纠偏类（语气/风格/态度）→ reflection_hint
    5. 其他 → 根据 category 参数决定
    """

    def __init__(self, plugin):
        self.plugin = plugin

    def classify(
        self,
        content: str,
        category: str | None = None,
        fact_type: str | None = None,
    ) -> MemoryDecision:
        """
        决策内容应写入哪个记忆层

        Args:
            content: 记忆内容
            category: 调用方传入的分类（user_profile/user_preference）
            fact_type: 已知的 fact_type

        Returns:
            MemoryDecision(target, fact_type, confidence, reason)
        """
        content = str(content or "").strip()
        if not content:
            return MemoryDecision(target=MemoryTarget.DROP, reason="空内容")

        content_lower = content.lower()

        if fact_type:
            return MemoryDecision(
                target=MemoryTarget.PROFILE,
                fact_type=fact_type,
                confidence=0.9,
                reason=f"显式指定类型: {fact_type}",
            )

        if self._is_reflection_hint(content):
            return MemoryDecision(
                target=MemoryTarget.REFLECTION_HINT,
                fact_type="reflection_hint",
                confidence=0.9,
                reason="自我纠偏/风格类内容",
            )

        if self._is_session_event(content, content_lower):
            return MemoryDecision(
                target=MemoryTarget.KNOWLEDGE_BASE,
                fact_type="session_event",
                confidence=0.85,
                reason="事件/约定/决策类内容",
            )

        if category == "user_preference":
            return MemoryDecision(
                target=MemoryTarget.PROFILE,
                fact_type="preference",
                confidence=0.9,
                reason="用户偏好",
            )

        if category is None or category == "user_profile":
            detected_type = self._auto_detect_fact_type(content_lower)
            reason_str = f"用户画像/自动分类: {detected_type}"
            return MemoryDecision(
                target=MemoryTarget.PROFILE,
                fact_type=detected_type,
                confidence=0.8,
                reason=reason_str,
            )

        return MemoryDecision(
            target=MemoryTarget.PROFILE,
            fact_type="recent_update",
            confidence=0.6,
            reason="默认写入 recent_update",
        )

    def _is_reflection_hint(self, content: str) -> bool:
        """判断是否为仅影响回复风格的自我纠偏"""
        hint_keywords = [
            "语气",
            "态度",
            "说话方式",
            "风格",
            "口吻",
            "误解",
            "搞错",
            "错了",
            "不对",
            "这个人",
            "回答时",
            "回复时",
            "要注意",
        ]
        return any(kw in content for kw in hint_keywords)

    def _is_session_event(self, content: str, content_lower: str) -> bool:
        """判断是否为当天事件/约定/决策"""
        for kw in SESSION_EVENT_KEYWORDS:
            if kw in content:
                return True

        decision_patterns = [
            r"决定(.+?)了",
            r"约定(.+?)了",
            r"^(我们|他们|大家)(.+?)说",
            r"^(.+?)说了(.+?)规则",
        ]
        for pattern in decision_patterns:
            if re.search(pattern, content):
                return True

        return False

    def _auto_detect_fact_type(self, content_lower: str) -> str:
        """自动检测 profile fact 类型"""
        identity_keywords = [
            "是",
            "职业",
            "工作",
            "身份",
            "角色",
            "年龄",
            "岁",
            "学生",
            "老师",
            "程序员",
            "工程师",
            "医生",
            "养",
            "有",
            "住在",
            "来自",
            "城市",
            "公司",
            "学校",
            "年级",
            "专业",
            "学历",
        ]
        if any(kw in content_lower for kw in identity_keywords):
            return "identity"

        preference_keywords = ["喜欢", "爱", "讨厌", "偏好", "想", "要", "不爱", "不喜", "讨厌", "恨", "支持", "反对"]
        if any(kw in content_lower for kw in preference_keywords):
            return "preference"

        trait_keywords = [
            "说话",
            "性格",
            "风格",
            "直接",
            "简洁",
            "话多",
            "话少",
            "活跃",
            "安静",
            "幽默",
            "内向",
            "外向",
        ]
        if any(kw in content_lower for kw in trait_keywords):
            return "trait"

        return "recent_update"

    async def write(
        self,
        content: str,
        scope_id: str,
        user_id: str,
        category: str | None = None,
        fact_type: str | None = None,
        nickname: str = "",
        source: str = "manual",
    ) -> str:
        """
        统一写入入口

        Args:
            content: 记忆内容
            scope_id: 会话范围
            user_id: 用户ID
            category: 调用方分类
            fact_type: 已知类型
            nickname: 昵称
            source: 来源

        Returns:
            写入结果描述
        """
        if category == "session_event":
            return await self._write_to_kb(
                scope_id=scope_id,
                content=content,
                user_id=user_id,
                source=source,
            )

        decision = self.classify(content, category, fact_type)

        logger.debug(
            f"[MemoryRouter] 路由决策: content={content[:50]}... → {decision.target.value}, "
            f"fact_type={decision.fact_type}, confidence={decision.confidence}, reason={decision.reason}"
        )

        if decision.target == MemoryTarget.DROP:
            return "内容为空，已丢弃"

        if decision.target == MemoryTarget.REFLECTION_HINT:
            return "reflection_hint 不持久化，由调用方注入"

        if decision.target == MemoryTarget.KNOWLEDGE_BASE:
            return await self._write_to_kb(
                scope_id=scope_id,
                content=content,
                user_id=user_id,
                source=source,
            )

        if decision.target == MemoryTarget.PROFILE:
            if not fact_type:
                fact_type = decision.fact_type

            profile_manager = getattr(self.plugin, "profile", None)
            if not profile_manager:
                return "ProfileManager 未初始化"

            success = await profile_manager.upsert_fact(
                scope_id=scope_id,
                user_id=user_id,
                fact_type=fact_type,
                content=content,
                source=source,
                replace_similar=True,
                nickname=nickname,
            )
            if success:
                return f"已写入画像（类型：{fact_type}）"
            else:
                return "内容已存在，无需重复写入"

        return "未知目标"

    def _should_reject_session_event(self, content: str) -> bool:
        """判断是否为失败态/元话语内容，应拒绝写入 session_event"""
        if not content or not content.strip():
            return True

        content_lower = content.lower()

        failure_phrases = [
            "我不知道",
            "我不记得",
            "没有相关记忆",
            "没有记忆",
            "查不到",
            "无法确认",
            "未找到",
            "没有找到",
            "找不到",
            "不确定",
            "不知道",
            "不清楚",
            "无法回答",
            "无法查找",
            "没有相关信息",
            "未找到相关",
        ]

        for phrase in failure_phrases:
            if phrase in content_lower:
                return True

        if content.strip().startswith("工具调用失败"):
            return True
        if content.strip().startswith("查询失败"):
            return True
        if content.strip().startswith("获取失败"):
            return True

        meta_patterns = [
            r"^调用工具.*?结果.*?$",
            r"^工具返回.*?$",
            r"^根据.*?返回.*?$",
            r"^\[.*?\].*?$",
        ]
        import re

        for pattern in meta_patterns:
            if re.match(pattern, content.strip()):
                return True

        return False

    async def _write_to_kb(
        self,
        scope_id: str,
        content: str,
        user_id: str,
        source: str,
    ) -> str:
        """写入知识库 - session_event"""
        if self._should_reject_session_event(content):
            logger.debug(f"[MemoryRouter] 拒绝写入失败态内容: {content[:50]}...")
            return "内容为失败态/元话语，已拒绝写入"

        try:
            memory_manager = getattr(self.plugin, "memory", None)
            if not memory_manager:
                return "MemoryManager 未初始化"

            from datetime import datetime

            session_event = {
                "type": "session_event",
                "scope_id": scope_id,
                "content": content,
                "user_id": user_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "source": source,
            }

            success = await memory_manager.save_session_event(
                scope_id=scope_id,
                session_event=session_event,
            )

            if success:
                return "已写入知识库（session_event）"
            else:
                return "写入知识库失败"
        except Exception as e:
            logger.warning(f"[MemoryRouter] 写入 KB 失败: {e}")
            return f"写入知识库失败: {e}"
