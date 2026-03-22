from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import cleanup_workspace_temp_dir, load_engine_module, make_workspace_temp_dir

ProfileManager = load_engine_module("profile").ProfileManager


class ProfileManagerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = make_workspace_temp_dir("profile")
        self.plugin = SimpleNamespace(
            data_dir=Path(self.temp_dir),
            cfg=SimpleNamespace(
                dropout_enabled=False,
                dropout_edge_rate=0.0,
                core_info_keywords="",
                profile_cooldown_minutes=0,
                profile_msg_count=20,
            ),
        )
        self.manager = ProfileManager(self.plugin)

    def tearDown(self):
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_load_profile_prefers_canonical_file(self):
        canonical = self.manager.profile_dir / "100_200.yaml"
        legacy = self.manager.profile_dir / "100_200_oldname.yaml"

        canonical.write_text("content: canonical-profile\n", encoding="utf-8")
        legacy.write_text("content: legacy-profile\n", encoding="utf-8")
        os.utime(legacy, (canonical.stat().st_mtime + 10, canonical.stat().st_mtime + 10))

        content = await self.manager.load_profile("100", "200")

        self.assertEqual(content, "canonical-profile")

    async def test_load_profile_falls_back_to_latest_legacy_file(self):
        older_legacy = self.manager.profile_dir / "100_200_alpha.yaml"
        newer_legacy = self.manager.profile_dir / "100_200_beta.yaml"

        older_legacy.write_text("content: old-legacy\n", encoding="utf-8")
        newer_legacy.write_text("content: new-legacy\n", encoding="utf-8")
        os.utime(older_legacy, (older_legacy.stat().st_mtime - 10, older_legacy.stat().st_mtime - 10))
        os.utime(newer_legacy, (older_legacy.stat().st_mtime + 20, older_legacy.stat().st_mtime + 20))

        content = await self.manager.load_profile("100", "200")

        self.assertEqual(content, "new-legacy")

    async def test_save_profile_writes_canonical_file_and_cleans_legacy_files(self):
        legacy_a = self.manager.profile_dir / "100_200_alice.yaml"
        legacy_b = self.manager.profile_dir / "100_200_bob.yaml"
        legacy_a.write_text("content: stale-a\n", encoding="utf-8")
        legacy_b.write_text("content: stale-b\n", encoding="utf-8")

        await self.manager.save_profile("100", "200", "content: fresh-profile\n", nickname="latest")

        canonical = self.manager.profile_dir / "100_200.yaml"
        self.assertTrue(canonical.exists())
        self.assertFalse(legacy_a.exists())
        self.assertFalse(legacy_b.exists())
        self.assertEqual(canonical.read_text(encoding="utf-8"), "content: fresh-profile")

    async def test_save_profile_caches_body_instead_of_raw_yaml(self):
        await self.manager.save_profile("100", "200", "content: structured-profile\n", nickname="Alice")

        content = await self.manager.load_profile("100", "200")

        self.assertEqual(content, "structured-profile")

    async def test_append_profile_content_preserves_yaml_metadata(self):
        canonical = self.manager.profile_dir / "100_200.yaml"
        canonical.write_text(
            'user_id: "200"\n'
            'scope_id: "100"\n'
            'nickname: "Alice"\n'
            'updated_at: "2026-03-18 10:00:00"\n'
            "content: |-\n"
            "  # 用户印象笔记\n"
            "  - 喜欢咖啡\n",
            encoding="utf-8",
        )

        await self.manager.append_profile_content("100", "200", "- 新增事实", nickname="")

        raw = canonical.read_text(encoding="utf-8")
        content = await self.manager.load_profile("100", "200")
        self.assertIn('nickname: "Alice"', raw)
        self.assertIn('scope_id: "100"', raw)
        self.assertIn("新增事实", raw)
        self.assertEqual(content, "# 用户印象笔记\n- 喜欢咖啡\n- 新增事实")

    async def test_build_profile_uses_private_friend_history(self):
        async def call_action(action, **kwargs):
            if action == "get_stranger_info":
                self.assertEqual(kwargs, {"user_id": 200, "no_cache": False})
                return {"nickname": "Alice"}
            if action == "get_friend_msg_history":
                self.assertEqual(kwargs, {"user_id": 200, "count": 20})
                return {
                    "messages": [
                        {
                            "sender": {"user_id": 200, "nickname": "Alice", "role": "member"},
                            "message": [{"type": "text", "data": {"text": "你好"}}],
                        }
                    ]
                }
            raise AssertionError(f"Unexpected action: {action}")

        provider = SimpleNamespace(
            text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="content: private-profile\n"))
        )
        get_using_provider = MagicMock(return_value=provider)
        bot = SimpleNamespace(call_action=AsyncMock(side_effect=call_action))
        self.plugin.context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)]),
            get_using_provider=get_using_provider,
        )

        result = await self.manager.build_profile(
            "200",
            "private_200",
            mode="create",
            force=True,
            umo="qq:private:200",
        )

        self.assertIn("创建", result)
        get_using_provider.assert_called_once_with(umo="qq:private:200")
        self.assertTrue((self.manager.profile_dir / "private_200_200.yaml").exists())

    async def test_build_profile_rejects_other_target_in_private_scope(self):
        result = await self.manager.build_profile("201", "private_200", mode="create", force=True)

        self.assertEqual(result, "私聊画像仅支持当前会话用户。")

    async def test_analyze_and_build_profiles_skips_bot_user(self):
        provider = SimpleNamespace(
            text_chat=AsyncMock(
                side_effect=[
                    SimpleNamespace(
                        completion_text='[{"user_id":"3003","nickname":"Alice","reason":"活跃","interested":true}]'
                    ),
                    SimpleNamespace(completion_text="content: built-profile\n"),
                ]
            )
        )
        self.plugin.context = SimpleNamespace(get_using_provider=MagicMock(return_value=provider))
        self.plugin._get_bot_id = MagicMock(return_value="1001")
        self.manager.save_profile = AsyncMock()

        messages = [
            {
                "sender": {"user_id": "1001", "nickname": "Bot"},
                "message": [{"type": "text", "data": {"text": "我是机器人"}}],
            },
            {
                "sender": {"user_id": "3003", "nickname": "Alice"},
                "message": [{"type": "text", "data": {"text": "你好"}}],
            },
        ]

        result = await self.manager.analyze_and_build_profiles("100", messages=messages, umo="qq:group:100")

        self.assertIn("1 位用户", result)
        analyze_prompt = provider.text_chat.await_args_list[0].kwargs["prompt"]
        self.assertNotIn("QQ: 1001", analyze_prompt)

    async def test_classify_fact_identity(self):
        result = self.manager.classify_fact("用户是一名程序员")
        self.assertEqual(result, "identity")

    async def test_classify_fact_preference(self):
        result = self.manager.classify_fact("他喜欢玩 Galgame")
        self.assertEqual(result, "preference")

    async def test_classify_fact_trait(self):
        result = self.manager.classify_fact("这个人说话很简洁")
        self.assertEqual(result, "trait")

    async def test_classify_fact_default(self):
        result = self.manager.classify_fact("今天群里聊了游戏")
        self.assertEqual(result, "recent_update")

    async def test_upsert_fact_preference_overwrites_old(self):
        await self.manager.upsert_fact("6001", "8001", "preference", "喜欢玩 Galgame", source="test")
        await self.manager.upsert_fact("6001", "8001", "preference", "现在不喜欢玩 Galgame 了", source="test")

        content = await self.manager.load_profile("6001", "8001")
        data = self.manager._parse_structured_content(content)
        prefs = data.get("preferences", [])

        self.assertNotIn("喜欢玩 Galgame", prefs)
        self.assertIn("现在不喜欢玩 Galgame 了", prefs)

    async def test_upsert_fact_identity_overwrites_same(self):
        await self.manager.upsert_fact("6001", "8001", "identity", "是一名学生", source="test")
        await self.manager.upsert_fact("6001", "8001", "identity", "刚换了新工作", source="test")

        content = await self.manager.load_profile("6001", "8001")
        data = self.manager._parse_structured_content(content)
        identities = data.get("identity", [])

        self.assertEqual(len(identities), 2)
        self.assertIn("刚换了新工作", identities)

    async def test_upsert_fact_duplicate_overwrites_with_replace_similar(self):
        await self.manager.upsert_fact("6001", "8001", "preference", "喜欢咖啡", source="test")
        result = await self.manager.upsert_fact("6001", "8001", "preference", "喜欢咖啡", source="test")

        self.assertTrue(result)

    async def test_upsert_fact_trait_deduplication(self):
        await self.manager.upsert_fact("6001", "8001", "trait", "说话简洁直接", source="test")
        await self.manager.upsert_fact("6001", "8001", "trait", "说话简洁直接", source="test")

        content = await self.manager.load_profile("6001", "8001")
        data = self.manager._parse_structured_content(content)
        traits = data.get("traits", [])

        self.assertEqual(len(traits), 1)

    async def test_upsert_fact_recent_updates_truncation(self):
        for i in range(12):
            await self.manager.upsert_fact("6001", "8001", "recent_update", f"事件{i}", source="test")

        content = await self.manager.load_profile("6001", "8001")
        data = self.manager._parse_structured_content(content)
        recent = data.get("recent_updates", [])
        long_term = data.get("long_term_notes", [])

        self.assertEqual(len(recent), 10)
        self.assertEqual(len(long_term), 1)
        self.assertIn("事件0", long_term)

    async def test_upsert_fact_long_term_note_not_truncated(self):
        await self.manager.upsert_fact("6001", "8001", "long_term_note", "每周日联机", source="test")
        for i in range(12):
            await self.manager.upsert_fact("6001", "8001", "recent_update", f"事件{i}", source="test")

        content = await self.manager.load_profile("6001", "8001")
        data = self.manager._parse_structured_content(content)
        long_term = data.get("long_term_notes", [])

        self.assertEqual(len(long_term), 2)

    async def test_get_structured_summary_limits_items(self):
        for i in range(5):
            await self.manager.upsert_fact("6001", "8001", "identity", f"身份{i}", source="test")
        for i in range(5):
            await self.manager.upsert_fact("6001", "8001", "preference", f"偏好{i}", source="test")
        for i in range(5):
            await self.manager.upsert_fact("6001", "8001", "trait", f"性格{i}", source="test")
        for i in range(5):
            await self.manager.upsert_fact("6001", "8001", "recent_update", f"事件{i}", source="test")

        summary = await self.manager.get_structured_summary("6001", "8001", max_items=8)

        self.assertTrue(len(summary) > 0)
        lines = [l for l in summary.split("\n") if l.startswith("- ")]
        self.assertLessEqual(len(lines), 8)

    async def test_get_structured_summary_preserves_structure(self):
        await self.manager.upsert_fact("6001", "8001", "identity", "是学生", source="test")
        await self.manager.upsert_fact("6001", "8001", "preference", "喜欢游戏", source="test")
        await self.manager.upsert_fact("6001", "8001", "trait", "话少", source="test")

        summary = await self.manager.get_structured_summary("6001", "8001", max_items=10)

        self.assertIn("[identity]", summary)
        self.assertIn("[preferences]", summary)
        self.assertIn("[traits]", summary)
        self.assertIn("是学生", summary)
        self.assertIn("喜欢游戏", summary)
        self.assertIn("话少", summary)
