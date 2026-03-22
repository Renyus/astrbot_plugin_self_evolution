from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import load_engine_module

message_normalization = load_engine_module("message_normalization")


class _FakeEvent:
    def __init__(self, *, message_str="hello", group_id="1001", components=None):
        self.message_str = message_str
        self._group_id = group_id
        self.message_obj = SimpleNamespace(message=components or [])
        self._extra = {}
        self._image_processed = False

    def get_group_id(self):
        return self._group_id

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value


class _FakeImage:
    def __init__(self, url="https://example.com/image.jpg"):
        self.url = url


class MessageNormalizationTests(IsolatedAsyncioTestCase):
    async def test_normalize_plain_text_event(self):
        event = _FakeEvent(message_str="plain text", components=[])
        dao = SimpleNamespace()

        text, has_image = await message_normalization.normalize_event_message_text(event, dao)

        self.assertEqual(text, "plain text")
        self.assertFalse(has_image)

    async def test_normalize_image_returns_图片(self):
        event = _FakeEvent(message_str="", components=[_FakeImage("abc")])
        dao = SimpleNamespace()

        text, has_image = await message_normalization.normalize_event_message_text(event, dao)

        self.assertEqual(text, "[图片]")
        self.assertTrue(has_image)

    async def test_ensure_event_message_text_sets_cache_and_image_flag(self):
        event = _FakeEvent(message_str="", components=[_FakeImage("abc")])
        dao = SimpleNamespace()

        text = await message_normalization.ensure_event_message_text(event, dao)

        self.assertEqual(text, "[图片]")
        self.assertEqual(event.get_extra("self_evolution_message_text"), "[图片]")
        self.assertTrue(event._image_processed)

    async def test_ensure_event_message_text_reuses_cached_extra(self):
        event = _FakeEvent(message_str="fallback", components=[_FakeImage("abc")])
        event.set_extra("self_evolution_message_text", "cached text")
        dao = SimpleNamespace()

        text = await message_normalization.ensure_event_message_text(event, dao)

        self.assertEqual(text, "cached text")
