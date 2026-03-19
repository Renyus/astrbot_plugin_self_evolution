from __future__ import annotations

from unittest import TestCase

from tests._helpers import load_engine_module

event_context = load_engine_module("event_context")


class Reply:
    def __init__(self, sender_nickname="", sender_id=""):
        self.sender_nickname = sender_nickname
        self.sender_id = sender_id


class At:
    def __init__(self, qq=""):
        self.qq = qq


class EventContextTests(TestCase):
    def test_extract_interaction_context_detects_reply_to_ai_by_name(self):
        result = event_context.extract_interaction_context(
            [Reply(sender_nickname="黑塔", sender_id="12345")],
            persona_name="黑塔",
            bot_id="99999",
        )

        self.assertEqual(result["quoted_info"], "回复了你")
        self.assertTrue(result["ai_context_info"])
        self.assertEqual(result["at_targets"], [])

    def test_extract_interaction_context_detects_reply_to_ai_by_id(self):
        result = event_context.extract_interaction_context(
            [Reply(sender_nickname="Someone", sender_id="99999")],
            persona_name="黑塔",
            bot_id="99999",
        )

        self.assertEqual(result["quoted_info"], "回复了你")

    def test_extract_interaction_context_detects_at_bot_and_at_all(self):
        result = event_context.extract_interaction_context(
            [At(qq="99999"), At(qq="all")],
            persona_name="黑塔",
            bot_id="99999",
        )

        self.assertEqual(result["at_info"], "at了你")
        self.assertEqual(result["at_targets"], ["99999", "all"])

    def test_extract_interaction_context_ignores_reply_and_at_for_others(self):
        result = event_context.extract_interaction_context(
            [Reply(sender_nickname="别人", sender_id="123"), At(qq="88888")],
            persona_name="黑塔",
            bot_id="99999",
        )

        self.assertEqual(result["quoted_info"], "")
        self.assertEqual(result["ai_context_info"], "")
        self.assertEqual(result["at_info"], "")
