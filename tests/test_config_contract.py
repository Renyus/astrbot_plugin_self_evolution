from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from unittest import TestCase

from tests._helpers import ROOT


class ConfigContractTests(TestCase):
    def test_config_properties_match_schema_keys(self):
        config_text = (ROOT / "config.py").read_text(encoding="utf-8")
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        properties = set(re.findall(r"@property\n\s+def\s+(\w+)\(", config_text))
        properties -= {"_config", "_parse_bool"}

        self.assertEqual(properties, set(schema.keys()))

    def test_removed_redundant_configs_are_absent(self):
        config_text = (ROOT / "config.py").read_text(encoding="utf-8")
        schema_text = (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        eavesdropping_text = (ROOT / "engine" / "eavesdropping.py").read_text(encoding="utf-8")

        removed_keys = {
            "core_principles",
            "timeout_memory_commit",
            "timeout_memory_recall",
            "max_memory_entries",
            "enable_context_recall",
            "sticker_fetch_interval",
            "boredom_sarcastic_reply",
            "critical_keywords",
            "eavesdrop_message_threshold",
            "eavesdrop_threshold_min",
            "eavesdrop_threshold_max",
            "leaky_integrator_enabled",
            "leaky_decay_factor",
            "leaky_trigger_threshold",
            "interest_boost",
            "daily_chat_boost",
            "desire_cooldown_messages",
            "desire_cooldown_seconds",
            "inner_monologue_enabled",
            "boredom_enabled",
            "boredom_consecutive_count",
            "engagement_new_system_enabled",
        }

        for key in removed_keys:
            self.assertNotIn(f"def {key}(", config_text)
            self.assertNotIn(f'"{key}"', schema_text)
            self.assertNotIn(f"`{key}`", readme_text)

        self.assertNotIn("boredom_sarcastic_reply", eavesdropping_text)

    def test_main_dead_helpers_and_constants_removed(self):
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("ANCHOR_MARKER", main_text)
        self.assertNotIn("PAGE_LIMIT", main_text)
        self.assertNotIn("def _clean_messages(", main_text)
        self.assertNotIn("def _post_init(", main_text)

    def test_eavesdropping_dead_state_removed(self):
        eavesdropping_text = (ROOT / "engine" / "eavesdropping.py").read_text(encoding="utf-8")
        self.assertNotIn("_current_boredom_state", eavesdropping_text)

    def test_schema_contains_newly_exposed_runtime_configs(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        expected_types = {
            "prompt_meltdown_message": "string",
            "san_auto_analyze_enabled": "bool",
            "san_analyze_interval": "int",
            "san_msg_count_per_group": "int",
            "san_high_activity_boost": "int",
            "san_low_activity_drain": "int",
            "san_positive_vibe_bonus": "int",
            "san_negative_vibe_penalty": "int",
            "memory_debug_enabled": "bool",
            "engagement_debug_enabled": "bool",
            "affinity_debug_enabled": "bool",
            "memory_query_fallback_enabled": "bool",
        }

        for key, expected_type in expected_types.items():
            self.assertIn(key, schema)
            self.assertEqual(schema[key]["type"], expected_type)

    def test_zzz_all_cfg_references_exist_in_plugin_config(self):
        import os, re, ast

        config_text = (ROOT / "config.py").read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("cfg_ref_check_module", ROOT / "config.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class _FakeConfig:
            pass

        fake = _FakeConfig()
        cfg_obj = module.PluginConfig(fake)

        plugin_props = set()
        tree = ast.parse(config_text)
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and n.name == "PluginConfig":
                for item in n.body:
                    if isinstance(item, ast.FunctionDef) and len(item.args.args) == 1:
                        plugin_props.add(item.name)

        code_refs = set()
        skip_dirs = {".git", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "tests"}
        skip_files = {"config.py", "_conf_schema.json"}
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if fname in skip_files:
                    continue
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                except Exception:
                    continue
                for m in re.finditer(r"(?:self|plugin)\.cfg\.(\w+)", content):
                    code_refs.add(m.group(1))

        missing = sorted(code_refs - plugin_props)
        self.assertEqual(missing, [], f"cfg.xxx referenced in code but not in PluginConfig: {missing}")

    def test_schema_defaults_match_runtime_defaults(self):
        import importlib.util

        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location("config_contract_module", ROOT / "config.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class _Plugin:
            config = {}

            @staticmethod
            def _parse_bool(val, default):
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.lower() in ("true", "1", "yes", "on")
                return default

        cfg = module.PluginConfig(_Plugin())

        for key, meta in schema.items():
            runtime_default = getattr(cfg, key)
            schema_default = meta["default"]
            if isinstance(runtime_default, (list, dict)) and isinstance(schema_default, str):
                schema_default = json.loads(schema_default)
            self.assertEqual(runtime_default, schema_default, key)
