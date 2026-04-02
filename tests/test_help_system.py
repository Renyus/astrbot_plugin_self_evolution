"""
Tests for help system components.
"""

import asyncio
import os
import tempfile
import pytest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from engine.help_catalog import (
    HELP_CATALOG_VERSION,
    HelpCommand,
    get_admin_commands,
    get_commands_by_group,
    get_user_commands,
    format_text_help,
)
from engine.help_theme_store import HelpThemeStore, HelpTheme, DEFAULT_BLUR, DEFAULT_BG_NAME


class TestHelpCatalog:
    def test_catalog_version_is_set(self):
        assert HELP_CATALOG_VERSION == 1

    def test_user_commands_excludes_admin_commands(self):
        user_cmds = get_user_commands()
        admin_cmds = get_admin_commands()
        assert len(user_cmds) < len(admin_cmds)

    def test_user_commands_has_no_admin_only(self):
        user_cmds = get_user_commands()
        for cmd in user_cmds:
            assert not cmd.admin_only, f"User command {cmd.command} should not be admin_only"

    def test_admin_commands_includes_all(self):
        admin_cmds = get_admin_commands()
        user_cmds = get_user_commands()
        for cmd in user_cmds:
            assert cmd in admin_cmds, f"Admin commands should include all user commands"

    def test_commands_have_required_fields(self):
        all_cmds = get_admin_commands()
        for cmd in all_cmds:
            assert cmd.group in ("base", "user", "admin", "persona")
            assert cmd.command.startswith("/")
            assert len(cmd.desc) > 0

    def test_no_combined_commands(self):
        all_cmds = get_admin_commands()
        combined_patterns = ["/evolution approve/reject", "/sticker delete/disable/enable"]
        for cmd in all_cmds:
            for pattern in combined_patterns:
                assert pattern not in cmd.command, f"Command {cmd.command} should not be combined"

    def test_commands_use_correct_param_format(self):
        all_cmds = get_admin_commands()
        for cmd in all_cmds:
            if "<" in cmd.command and ">" in cmd.command:
                assert "[" not in cmd.command or "]" not in cmd.command or "<" not in cmd.command

    def test_grouped_commands_structure(self):
        groups = get_commands_by_group(include_admin=True)
        assert "base" in groups
        assert "user" in groups
        assert "admin" in groups
        assert "persona" in groups

    def test_format_text_help_user_version(self):
        text = format_text_help(is_admin=False)
        assert "【基础】" in text
        assert "【用户】" in text
        assert "【管理】" not in text
        assert "/system help" in text

    def test_format_text_help_admin_version(self):
        text = format_text_help(is_admin=True)
        assert "【基础】" in text
        assert "【用户】" in text
        assert "【管理】" in text
        assert "【Persona】" in text

    def test_no_duplicate_commands(self):
        all_cmds = get_admin_commands()
        commands_seen = set()
        for cmd in all_cmds:
            assert cmd.command not in commands_seen, f"Duplicate command: {cmd.command}"
            commands_seen.add(cmd.command)


class TestHelpThemeStore(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = HelpThemeStore(Path(self.temp_dir))
        await self.store.init()

    async def asyncTearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_default_theme_values(self):
        assert self.store.theme.bg_name == DEFAULT_BG_NAME
        assert self.store.theme.blur == DEFAULT_BLUR

    async def test_theme_is_valid(self):
        assert self.store.theme.is_valid()

    async def test_list_backgrounds_includes_default(self):
        backgrounds = await self.store.list_backgrounds()
        assert DEFAULT_BG_NAME in backgrounds

    async def test_set_background_valid_name(self):
        success, msg = await self.store.set_background("default")
        assert success
        assert self.store.theme.bg_name == "default"

    async def test_set_background_invalid_name(self):
        success, msg = await self.store.set_background("nonexistent_bg_xyz")
        assert not success
        assert "不存在" in msg

    async def test_set_blur_valid_values(self):
        for blur in [0, 10, 20, 30]:
            success, msg = await self.store.set_blur(blur)
            assert success, f"Failed to set blur {blur}"
            assert self.store.theme.blur == blur

    async def test_set_blur_invalid_values(self):
        for invalid in [-1, 31, 100, -10]:
            success, msg = await self.store.set_blur(invalid)
            assert not success
            assert "0" in msg.split("-")[-1].strip() or "30" in msg

    async def test_set_blur_non_integer(self):
        success, msg = await self.store.set_blur("abc")
        assert not success
        assert "无效" in msg

    async def test_reset_theme(self):
        await self.store.set_background("default")
        await self.store.set_blur(20)
        success, msg = await self.store.reset()
        assert success
        assert self.store.theme.bg_name == DEFAULT_BG_NAME
        assert self.store.theme.blur == DEFAULT_BLUR

    def test_cache_path_generation(self):
        path = self.store.get_cache_path(version=HELP_CATALOG_VERSION, is_admin=False, bg_name="default", blur=16)
        assert "help_user" in str(path)
        assert "default" in str(path)
        assert "blur-16" in str(path)

    def test_cache_path_admin_differs_from_user(self):
        user_path = self.store.get_cache_path(version=HELP_CATALOG_VERSION, is_admin=False, bg_name="default", blur=16)
        admin_path = self.store.get_cache_path(version=HELP_CATALOG_VERSION, is_admin=True, bg_name="default", blur=16)
        assert user_path != admin_path

    async def test_get_bg_path_returns_path_for_existing(self):
        path = await self.store.get_bg_path("default")
        assert path is not None
        assert path.exists()

    async def test_get_bg_path_returns_none_for_nonexistent(self):
        path = await self.store.get_bg_path("nonexistent_bg_xyz")
        assert path is None


class TestHelpRenderer(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = HelpThemeStore(Path(self.temp_dir))
        await self.store.init()

    async def asyncTearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_render_returns_path_on_success(self):
        from engine.help_renderer import render_help_image

        path, success = await render_help_image(self.store, is_admin=False)
        assert success
        assert path is not None

    async def test_render_creates_cache_file(self):
        from engine.help_renderer import render_help_image

        path, success = await render_help_image(self.store, is_admin=False)
        assert success
        assert path.exists()

    async def test_render_different_cache_keys_for_different_blur(self):
        from engine.help_renderer import render_help_image

        path1, _ = await render_help_image(self.store, is_admin=False)
        await self.store.set_blur(10)
        path2, _ = await render_help_image(self.store, is_admin=False)
        assert path1 != path2

    async def test_render_different_cache_keys_for_admin_vs_user(self):
        from engine.help_renderer import render_help_image

        user_path, _ = await render_help_image(self.store, is_admin=False)
        admin_path, _ = await render_help_image(self.store, is_admin=True)
        assert user_path != admin_path
