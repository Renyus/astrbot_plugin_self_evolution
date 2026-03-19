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
