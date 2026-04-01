"""
Unit tests for engine.moderation_classifier module.
"""

from __future__ import annotations

from unittest import TestCase

from engine.caption_service import CaptionResult, MediaKind, MediaOrigin
from engine.moderation_classifier import (
    ModerationCategory,
    ModerationResult,
    RiskLevel,
    SuggestedAction,
    _is_refusal_caption,
    _weak_rules_check,
    classify_nsfw_caption,
    classify_promo_caption,
    merge_moderation_results,
)


class WeakRulesTests(TestCase):
    def test_nsfw_keyword_triggers_nsfw_category(self):
        cap = CaptionResult(
            text="这是一张色情图片",
            success=True,
            provider_id="test",
            model_name="test",
            resource_key="key",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )
        result = _weak_rules_check(cap.text, "nsfw")
        self.assertIsNotNone(result)
        self.assertEqual(result.category, ModerationCategory.NSFW)
        self.assertEqual(result.suggested_action, SuggestedAction.REVIEW)
        self.assertEqual(result.confidence, 0.5)
        self.assertEqual(result.risk_level, RiskLevel.MEDIUM)
        self.assertIn("weak_keyword:", result.reasons[0])

    def test_promo_keyword_triggers_promo_category(self):
        cap = CaptionResult(
            text="加我微信号领取福利",
            success=True,
            provider_id="test",
            model_name="test",
            resource_key="key",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )
        result = _weak_rules_check(cap.text, "promo")
        self.assertIsNotNone(result)
        self.assertEqual(result.category, ModerationCategory.PROMO)
        self.assertEqual(result.suggested_action, SuggestedAction.REVIEW)
        self.assertEqual(result.confidence, 0.5)
        self.assertEqual(result.risk_level, RiskLevel.MEDIUM)

    def test_no_keyword_returns_none(self):
        result = _weak_rules_check("这是一张正常的风景照片", "nsfw")
        self.assertIsNone(result)


class RefusalDetectionTests(TestCase):
    def test_refusal_caption_detected(self):
        text = "无法提供相关描述，因为这涉及不适宜的色情内容"
        self.assertTrue(_is_refusal_caption(text))

    def test_normal_caption_not_detected(self):
        text = "这是一张风景照片，蓝天白云绿草地"
        self.assertFalse(_is_refusal_caption(text))

    def test_single_refusal_word_not_detected(self):
        text = "这是一个正常描述"
        self.assertFalse(_is_refusal_caption(text))


class ClassifyTests(TestCase):
    def test_nsfw_refusal_returns_delete(self):
        cap = CaptionResult(
            text="无法提供描述，因为涉及不适宜的色情内容",
            success=True,
            provider_id="test",
            model_name="test",
            resource_key="key",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )
        result = classify_nsfw_caption(cap)
        self.assertEqual(result.category, ModerationCategory.NSFW)
        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(result.risk_level, RiskLevel.HIGH)
        self.assertEqual(result.suggested_action, SuggestedAction.DELETE)

    def test_nsfw_clean_returns_ignore(self):
        cap = CaptionResult(
            text="一只可爱的猫在草地上玩耍",
            success=True,
            provider_id="test",
            model_name="test",
            resource_key="key",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )
        result = classify_nsfw_caption(cap)
        self.assertEqual(result.category, ModerationCategory.UNCERTAIN)
        self.assertEqual(result.suggested_action, SuggestedAction.IGNORE)

    def test_promo_refusal_returns_review(self):
        cap = CaptionResult(
            text="无法提供描述，因为这类内容不适合提供，涉及推广和引流",
            success=True,
            provider_id="test",
            model_name="test",
            resource_key="key",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )
        result = classify_promo_caption(cap)
        self.assertEqual(result.category, ModerationCategory.PROMO)
        self.assertEqual(result.confidence, 0.7)
        self.assertEqual(result.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(result.suggested_action, SuggestedAction.REVIEW)

    def test_invalid_caption_returns_ignore(self):
        result = classify_nsfw_caption(None)
        self.assertEqual(result.suggested_action, SuggestedAction.IGNORE)


class MergeTests(TestCase):
    def test_both_truly_uncertain_returns_uncertain_ignore(self):
        nsfw = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="nsfw",
        )
        promo = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="promo",
        )
        merged = merge_moderation_results(nsfw, promo)
        self.assertEqual(merged.category, ModerationCategory.UNCERTAIN)
        self.assertEqual(merged.suggested_action, SuggestedAction.IGNORE)

    def test_nsfw_review_promo_uncertain_returns_nsfw_review(self):
        nsfw = ModerationResult(
            category=ModerationCategory.NSFW,
            confidence=0.6,
            risk_level=RiskLevel.MEDIUM,
            reasons=["explicit_content"],
            suggested_action=SuggestedAction.REVIEW,
            classifier="nsfw",
        )
        promo = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="promo",
        )
        merged = merge_moderation_results(nsfw, promo)
        self.assertEqual(merged.category, ModerationCategory.NSFW)
        self.assertEqual(merged.suggested_action, SuggestedAction.REVIEW)

    def test_nsfw_uncertain_high_confidence_gets_review(self):
        nsfw = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.7,
            risk_level=RiskLevel.MEDIUM,
            reasons=["explicit_content_detected"],
            suggested_action=SuggestedAction.IGNORE,
            classifier="nsfw",
        )
        promo = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="promo",
        )
        merged = merge_moderation_results(nsfw, promo)
        self.assertNotEqual(merged.category, ModerationCategory.UNCERTAIN)
        self.assertEqual(merged.suggested_action, SuggestedAction.REVIEW)

    def test_nsfw_uncertain_low_confidence_ignored(self):
        nsfw = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.3,
            risk_level=RiskLevel.LOW,
            reasons=["some_signal"],
            suggested_action=SuggestedAction.IGNORE,
            classifier="nsfw",
        )
        promo = ModerationResult(
            category=ModerationCategory.UNCERTAIN,
            confidence=0.0,
            risk_level=RiskLevel.LOW,
            reasons=[],
            suggested_action=SuggestedAction.IGNORE,
            classifier="promo",
        )
        merged = merge_moderation_results(nsfw, promo)
        self.assertEqual(merged.category, ModerationCategory.NSFW)
        self.assertEqual(merged.suggested_action, SuggestedAction.IGNORE)
