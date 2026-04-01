from __future__ import annotations

import importlib.util
import logging
import shutil
import sqlite3
import sys
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engine"
FAKE_ENGINE_PACKAGE = "self_evolution_test_engine"
TEST_TMP_DIR = ROOT / "tests" / ".tmp"


def install_astrbot_stubs() -> None:
    """Install minimal astrbot stubs required by isolated unit tests."""
    if "astrbot.api" in sys.modules:
        return

    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    api_all_module = types.ModuleType("astrbot.api.all")
    api_module.logger = logging.getLogger("astrbot-test")
    api_all_module.AstrMessageEvent = object
    astrbot_module.api = api_module

    sys.modules["astrbot"] = astrbot_module
    sys.modules["astrbot.api"] = api_module
    sys.modules["astrbot.api.all"] = api_all_module


def install_yaml_stub() -> None:
    """Install a tiny yaml stub for tests that only need simple key/value parsing."""
    if "yaml" in sys.modules:
        return

    yaml_module = types.ModuleType("yaml")

    def safe_load(content: str):
        data = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip("'\"")
        return data

    yaml_module.safe_load = safe_load
    sys.modules["yaml"] = yaml_module


def install_aiosqlite_stub() -> None:
    """Install a minimal sqlite-backed async stub for DAO unit tests."""
    if "aiosqlite" in sys.modules:
        return

    aiosqlite_module = types.ModuleType("aiosqlite")

    class CursorWrapper:
        def __init__(self, cursor):
            self._cursor = cursor

        async def fetchone(self):
            return self._cursor.fetchone()

        async def fetchall(self):
            return self._cursor.fetchall()

        async def close(self):
            self._cursor.close()

        @property
        def rowcount(self):
            return self._cursor.rowcount

        @property
        def lastrowid(self):
            return getattr(self._cursor, "lastrowid", None)

    class ExecuteContext:
        def __init__(self, connection, sql: str, params=()):
            self._connection = connection
            self._sql = sql
            self._params = params or ()
            self._cursor_wrapper = None

        async def _execute(self):
            if self._cursor_wrapper is None:
                cursor = self._connection._conn.execute(self._sql, self._params)
                self._cursor_wrapper = CursorWrapper(cursor)
            return self._cursor_wrapper

        def __await__(self):
            return self._execute().__await__()

        async def __aenter__(self):
            return await self._execute()

        async def __aexit__(self, exc_type, exc, tb):
            if self._cursor_wrapper is not None:
                await self._cursor_wrapper.close()
            return False

    class ConnectionWrapper:
        def __init__(self, conn):
            self._conn = conn

        @property
        def row_factory(self):
            return self._conn.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._conn.row_factory = value

        def execute(self, sql: str, params=()):
            return ExecuteContext(self, sql, params)

        async def commit(self):
            self._conn.commit()

        async def close(self):
            self._conn.close()

    async def connect(path: str):
        conn = sqlite3.connect(path)
        return ConnectionWrapper(conn)

    aiosqlite_module.connect = connect
    aiosqlite_module.Row = sqlite3.Row
    aiosqlite_module.Error = sqlite3.Error
    aiosqlite_module.IntegrityError = sqlite3.IntegrityError
    aiosqlite_module.DatabaseError = sqlite3.DatabaseError
    aiosqlite_module.NotSupportedError = sqlite3.NotSupportedError
    aiosqlite_module.OperationalError = sqlite3.OperationalError
    aiosqlite_module.Warning = sqlite3.Warning
    aiosqlite_module.InterfaceError = sqlite3.InterfaceError
    aiosqlite_module.DataError = sqlite3.DataError
    aiosqlite_module.InternalError = sqlite3.InternalError
    aiosqlite_module.ProgrammingError = sqlite3.ProgrammingError
    aiosqlite_module.sqlite_version = sqlite3.sqlite_version
    aiosqlite_module.sqlite_version_info = sqlite3.sqlite_version_info
    sys.modules["aiosqlite"] = aiosqlite_module


def _ensure_package(package_name: str, package_dir: Path) -> None:
    if package_name in sys.modules:
        return

    package = types.ModuleType(package_name)
    package.__path__ = [str(package_dir)]
    sys.modules[package_name] = package


def load_engine_module(module_name: str):
    """Load an engine module without importing the package __init__ tree."""
    install_astrbot_stubs()
    install_yaml_stub()
    _ensure_package(FAKE_ENGINE_PACKAGE, ENGINE_DIR)

    if module_name != "context_injection" and f"{FAKE_ENGINE_PACKAGE}.context_injection" not in sys.modules:
        load_engine_module("context_injection")

    full_name = f"{FAKE_ENGINE_PACKAGE}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    module_path = ENGINE_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module {module_name} from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def load_module_from_path(module_name: str, module_path: Path):
    """Load a standalone module from an arbitrary file path."""
    full_name = f"self_evolution_test_dynamic.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module {module_name} from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def load_commands_module(module_name: str):
    """Load a commands sub-module, ensuring common.py is importable within it."""
    ROOT = Path(__file__).resolve().parents[1]
    COMMANDS_DIR = ROOT / "commands"

    _ensure_package("self_evolution_test_dynamic_commands", COMMANDS_DIR)

    common_full = f"self_evolution_test_dynamic_commands.common"
    if common_full not in sys.modules:
        common_path = COMMANDS_DIR / "common.py"
        spec = importlib.util.spec_from_file_location(common_full, common_path)
        if spec and spec.loader:
            common_module = importlib.util.module_from_spec(spec)
            sys.modules[common_full] = common_module
            spec.loader.exec_module(common_module)

    module_full = f"self_evolution_test_dynamic_commands.{module_name}"
    if module_full in sys.modules:
        return sys.modules[module_full]

    module_path = COMMANDS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_full, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load commands.{module_name} from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_full] = module
    spec.loader.exec_module(module)
    return module


def make_workspace_temp_dir(prefix: str) -> Path:
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_DIR / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def cleanup_workspace_temp_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
