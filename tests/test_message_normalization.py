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

    def get_group_id(self):
        return self._group_id

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value


class _FakeImage:
    def __init__(self, base64_data="image-data"):
        self.base64_data = base64_data

    async def convert_to_base64(self):
        return self.base64_data


class MessageNormalizationTests(IsolatedAsyncioTestCase):
    async def test_normalize_plain_text_event(self):
        event = _FakeEvent(message_str="plain text", components=[])
        dao = SimpleNamespace(get_sticker_by_hash=AsyncMock())

        text, has_image = await message_normalization.normalize_event_message_text(event, dao)

        self.assertEqual(text, "plain text")
        self.assertFalse(has_image)
        dao.get_sticker_by_hash.assert_not_called()

    async def test_normalize_image_uses_sticker_description(self):
        event = _FakeEvent(message_str="", components=[_FakeImage("abc")])
        dao = SimpleNamespace(get_sticker_by_hash=AsyncMock(return_value={"description": "开心表情"}))

        text, has_image = await message_normalization.normalize_event_message_text(event, dao)

        self.assertEqual(text, "[开心表情]")
        self.assertTrue(has_image)

    async def test_normalize_image_uses_sticker_tags_when_description_missing(self):
        event = _FakeEvent(message_str="", components=[_FakeImage("abc")])
        dao = SimpleNamespace(get_sticker_by_hash=AsyncMock(return_value={"tags": "猫猫"}))

        text, has_image = await message_normalization.normalize_event_message_text(event, dao)

        self.assertEqual(text, '[收到一张"猫猫"表情包]')
        self.assertTrue(has_image)

    async def test_ensure_event_message_text_sets_cache_and_image_flag(self):
        event = _FakeEvent(message_str="", components=[_FakeImage("abc")])
        dao = SimpleNamespace(get_sticker_by_hash=AsyncMock(return_value={"description": "开心表情"}))

        text = await message_normalization.ensure_event_message_text(event, dao)

        self.assertEqual(text, "[开心表情]")
        self.assertEqual(event.get_extra("self_evolution_message_text"), "[开心表情]")
        self.assertTrue(event._image_processed)

    async def test_ensure_event_message_text_reuses_cached_extra(self):
        event = _FakeEvent(message_str="fallback", components=[_FakeImage("abc")])
        event.set_extra("self_evolution_message_text", "cached text")
        dao = SimpleNamespace(get_sticker_by_hash=AsyncMock())

        text = await message_normalization.ensure_event_message_text(event, dao)

        self.assertEqual(text, "cached text")
        dao.get_sticker_by_hash.assert_not_called()
