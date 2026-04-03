"""Tests for help command display helpers."""

import importlib.util
import sys
import tempfile
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, relative_path: str):
    path = PLUGIN_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


help_catalog = _load_module("help_catalog_test", "engine/help_catalog.py")

HELP_CATALOG_VERSION = help_catalog.HELP_CATALOG_VERSION
get_admin_commands = help_catalog.get_admin_commands
get_commands_by_group = help_catalog.get_commands_by_group
get_user_commands = help_catalog.get_user_commands
format_text_help = help_catalog.format_text_help


class TestHelpCatalog:
    def test_catalog_version_is_set(self):
        assert HELP_CATALOG_VERSION == 4

    def test_user_commands_excludes_admin_commands(self):
        user_cmds = get_user_commands()
        admin_cmds = get_admin_commands()
        assert len(user_cmds) < len(admin_cmds)

    def test_user_commands_has_no_admin_only(self):
        user_cmds = get_user_commands()
        for cmd in user_cmds:
            assert not cmd.admin_only

    def test_admin_commands_includes_all(self):
        admin_cmds = get_admin_commands()
        user_cmds = get_user_commands()
        for cmd in user_cmds:
            assert cmd in admin_cmds

    def test_commands_have_required_fields(self):
        all_cmds = get_admin_commands()
        valid_groups = {"base", "social", "meal", "profile", "sticker", "evolution", "database", "persona"}
        for cmd in all_cmds:
            assert cmd.group in valid_groups
            assert cmd.command.startswith("/")
            assert len(cmd.desc) > 0

    def test_no_combined_commands(self):
        all_cmds = get_admin_commands()
        combined_patterns = [
            "/evolution approve/reject",
            "/sticker delete/disable/enable",
            "/db show/reset/rebuild/confirm",
        ]
        for cmd in all_cmds:
            for pattern in combined_patterns:
                assert pattern not in cmd.command

    def test_grouped_commands_structure(self):
        groups = get_commands_by_group(include_admin=True)
        assert "base" in groups
        assert "social" in groups
        assert "database" in groups
        assert "persona" in groups

    def test_format_text_help_user_version(self):
        text = format_text_help(is_admin=False)
        assert "【基础】" in text
        assert "【互动】" in text
        assert "【数据库】" not in text
        assert "/se help" in text

    def test_format_text_help_admin_version(self):
        text = format_text_help(is_admin=True)
        assert "【基础】" in text
        assert "【互动】" in text
        assert "【数据库】" in text
        assert "【Persona】" in text

    def test_no_duplicate_commands(self):
        all_cmds = get_admin_commands()
        commands_seen = set()
        for cmd in all_cmds:
            assert cmd.command not in commands_seen
            commands_seen.add(cmd.command)
