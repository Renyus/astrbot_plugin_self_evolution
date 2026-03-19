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
