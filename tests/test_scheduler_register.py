from __future__ import annotations

import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import ROOT


def _load_register_module():
    package_name = "self_evolution_test_scheduler"
    scheduler_dir = ROOT / "scheduler"

    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(scheduler_dir)]
        sys.modules[package_name] = package

    tasks_name = f"{package_name}.tasks"
    if tasks_name not in sys.modules:
        tasks_spec = importlib.util.spec_from_file_location(tasks_name, scheduler_dir / "tasks.py")
        tasks_module = importlib.util.module_from_spec(tasks_spec)
        sys.modules[tasks_name] = tasks_module
        tasks_spec.loader.exec_module(tasks_module)

    register_name = f"{package_name}.register"
    if register_name in sys.modules:
        return sys.modules[register_name]

    register_spec = importlib.util.spec_from_file_location(register_name, scheduler_dir / "register.py")
    register_module = importlib.util.module_from_spec(register_spec)
    sys.modules[register_name] = register_module
    register_spec.loader.exec_module(register_module)
    return register_module


register_mod = _load_register_module()


class RegisterTasksTests(IsolatedAsyncioTestCase):
    async def test_affinity_recovery_registers_without_reflection(self):
        cron_mgr = SimpleNamespace(
            list_jobs=AsyncMock(return_value=[]),
            delete_job=AsyncMock(),
            add_basic_job=AsyncMock(),
        )
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                auto_profile_enabled=False,
                reflection_enabled=False,
                affinity_recovery_enabled=True,
                san_enabled=False,
                san_auto_analyze_enabled=False,
                memory_enabled=False,
                interject_enabled=False,
                reflection_schedule="0 2 * * *",
            ),
            context=SimpleNamespace(cron_manager=cron_mgr),
            reflection_schedule="0 2 * * *",
        )

        await register_mod.register_tasks(plugin)

        job_names = [call.kwargs["name"] for call in cron_mgr.add_basic_job.await_args_list]
        self.assertIn("SelfEvolution_AffinityRecovery", job_names)
        self.assertNotIn("SelfEvolution_DailyReflection", job_names)
