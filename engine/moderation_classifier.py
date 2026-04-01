"""
Phase 4: Moderation Classification Layer

职责：
- 输入 CaptionResult，输出结构化 ModerationResult
- 两个独立分类器：NSFW / Promo
- 不做图片获取、不做 caption、不写 cache、不做处罚执行
- 审核结果不回写到 caption cache
"""

import dataclasses
from typing import Optional

from .caption_service import CaptionResult


class ModerationCategory:
    NSFW = "nsfw"
    PROMO = "promo"
    UNCERTAIN = "uncertain"


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SuggestedAction:
    IGNORE = "ignore"
    REVIEW = "review"
    DELETE = "delete"
    KICK = "kick"


@dataclasses.dataclass
class ModerationResult:
    category: str = ModerationCategory.UNCERTAIN
    confidence: float = 0.0
    risk_level: str = RiskLevel.LOW
    reasons: list[str] = dataclasses.field(default_factory=list)
    suggested_action: str = SuggestedAction.IGNORE
    classifier: str = ""
    raw_output: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
            "suggested_action": self.suggested_action,
            "classifier": self.classifier,
        }


_WEAK_KEYWORDS_NSFW = [
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
    " Porn ",
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

_WEAK_KEYWORDS_PROMO = [
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


_REFUSAL_PATTERNS = [
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


_MODERATION_CFG = {
    "nsfw_refusal_confidence": 0.9,
    "promo_refusal_confidence": 0.7,
    "weak_keyword_confidence": 0.5,
    "confidence_threshold": 0.6,
}


def init_moderation_keywords(
    nsfw_keywords: list,
    promo_keywords: list,
    refusal_keywords: list,
    nsfw_refusal_confidence: float = 0.9,
    promo_refusal_confidence: float = 0.7,
    weak_keyword_confidence: float = 0.5,
    confidence_threshold: float = 0.6,
):
    global _WEAK_KEYWORDS_NSFW, _WEAK_KEYWORDS_PROMO, _REFUSAL_PATTERNS, _MODERATION_CFG
    _WEAK_KEYWORDS_NSFW = nsfw_keywords
    _WEAK_KEYWORDS_PROMO = promo_keywords
    _REFUSAL_PATTERNS = refusal_keywords
    _MODERATION_CFG = {
        "nsfw_refusal_confidence": nsfw_refusal_confidence,
        "promo_refusal_confidence": promo_refusal_confidence,
        "weak_keyword_confidence": weak_keyword_confidence,
        "confidence_threshold": confidence_threshold,
    }


def _is_refusal_caption(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    count = sum(1 for p in _REFUSAL_PATTERNS if p in text_lower)
    return count >= 2


def _weak_rules_check(caption_text: str, classifier: str) -> Optional[ModerationResult]:
    if not caption_text:
        return None
    text_lower = caption_text.lower()
    weak_conf = _MODERATION_CFG["weak_keyword_confidence"]
    if classifier == "nsfw":
        for kw in _WEAK_KEYWORDS_NSFW:
            if kw.lower() in text_lower:
                return ModerationResult(
                    category=ModerationCategory.NSFW,
                    confidence=weak_conf,
                    risk_level=RiskLevel.MEDIUM,
                    reasons=[f"weak_keyword:{kw}"],
                    suggested_action=SuggestedAction.REVIEW,
                    classifier="weak_rules",
                )
    elif classifier == "promo":
        for kw in _WEAK_KEYWORDS_PROMO:
            if kw.lower() in text_lower:
                return ModerationResult(
                    category=ModerationCategory.PROMO,
                    confidence=weak_conf,
                    risk_level=RiskLevel.MEDIUM,
                    reasons=[f"weak_keyword:{kw}"],
                    suggested_action=SuggestedAction.REVIEW,
                    classifier="weak_rules",
                )
    return None


def classify_nsfw_caption(caption_result: CaptionResult) -> ModerationResult:
    if not caption_result or not caption_result.success or not caption_result.text:
        return ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=["invalid_caption"],
            suggested_action=SuggestedAction.IGNORE,
            classifier="nsfw",
        )
    if _is_refusal_caption(caption_result.text):
        return ModerationResult(
            category=ModerationCategory.NSFW,
            confidence=_MODERATION_CFG["nsfw_refusal_confidence"],
            risk_level=RiskLevel.HIGH,
            reasons=["caption_refusal_detected"],
            suggested_action=SuggestedAction.DELETE,
            classifier="nsfw",
        )
    weak = _weak_rules_check(caption_result.text, "nsfw")
    if weak:
        return weak
    return ModerationResult(
        category=ModerationCategory.UNCERTAIN,
        confidence=0.0,
        risk_level=RiskLevel.LOW,
        reasons=["caption_clean"],
        suggested_action=SuggestedAction.IGNORE,
        classifier="nsfw",
    )


def classify_promo_caption(caption_result: CaptionResult) -> ModerationResult:
    if not caption_result or not caption_result.success or not caption_result.text:
        return ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=["invalid_caption"],
            suggested_action=SuggestedAction.IGNORE,
            classifier="promo",
        )
    if _is_refusal_caption(caption_result.text):
        return ModerationResult(
            category=ModerationCategory.PROMO,
            confidence=_MODERATION_CFG["promo_refusal_confidence"],
            risk_level=RiskLevel.MEDIUM,
            reasons=["caption_refusal_detected"],
            suggested_action=SuggestedAction.REVIEW,
            classifier="promo",
        )
    weak = _weak_rules_check(caption_result.text, "promo")
    if weak:
        return weak
    return ModerationResult(
        category=ModerationCategory.UNCERTAIN,
        confidence=0.0,
        risk_level=RiskLevel.LOW,
        reasons=["caption_clean"],
        suggested_action=SuggestedAction.IGNORE,
        classifier="promo",
    )


def merge_moderation_results(nsfw_result: ModerationResult, promo_result: ModerationResult) -> ModerationResult:
    candidates = []
    for r in (nsfw_result, promo_result):
        if r.category != ModerationCategory.UNCERTAIN:
            candidates.append(r)
        elif r.confidence > 0 or len(r.reasons) > 0:
            candidates.append(r)

    if not candidates:
        return ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="merger",
        )

    def score(r: ModerationResult) -> float:
        rl_map = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
        return rl_map.get(r.risk_level, 1) * r.confidence

    candidates.sort(key=score, reverse=True)
    top = candidates[0]
    if len(candidates) >= 2:
        s1 = score(top)
        s2 = score(candidates[1])
        if abs(s1 - s2) < 0.1 and candidates[1].risk_level == RiskLevel.HIGH:
            top = candidates[1]

    merged_category = top.category
    merged_action = top.suggested_action
    if merged_category == ModerationCategory.UNCERTAIN:
        merged_category = ModerationCategory.NSFW if top.classifier == "nsfw" else ModerationCategory.PROMO
        if top.confidence > 0 or len(top.reasons) > 0:
            merged_action = SuggestedAction.REVIEW

    action_order = {SuggestedAction.DELETE: 3, SuggestedAction.REVIEW: 2, SuggestedAction.IGNORE: 1}
    worst_action = merged_action
    for r in candidates:
        if action_order.get(r.suggested_action, 0) > action_order.get(worst_action, 0):
            worst_action = r.suggested_action

    if top.confidence < _MODERATION_CFG["confidence_threshold"]:
        worst_action = SuggestedAction.IGNORE

    nsfw_delete_threshold = _MODERATION_CFG["nsfw_refusal_confidence"]
    if (
        worst_action == SuggestedAction.DELETE
        and merged_category == ModerationCategory.NSFW
        and top.confidence >= nsfw_delete_threshold
    ):
        worst_action = SuggestedAction.REVIEW

    merged = ModerationResult(
        category=merged_category,
        confidence=top.confidence,
        risk_level=top.risk_level,
        reasons=top.reasons,
        suggested_action=worst_action,
        classifier="merger",
    )
    if len(candidates) > 1:
        other = candidates[1]
        merged.reasons = top.reasons + [f"secondary:{other.category}:{other.risk_level}:{other.confidence}"]

    return merged
