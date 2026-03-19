from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests._helpers import (
    cleanup_workspace_temp_dir,
    install_aiosqlite_stub,
    make_workspace_temp_dir,
)

install_aiosqlite_stub()

from dao import SelfEvolutionDAO


class SelfEvolutionDAOTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("dao")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "self_evolution_test.db"))
        await self.dao.init_db()

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_get_untagged_stickers_returns_created_at(self):
        sticker_uuid = await self.dao.add_sticker("100", "200", "ZmFrZS1iYXNlNjQ=")

        rows = await self.dao.get_untagged_stickers(limit=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uuid"], sticker_uuid)
        self.assertIn("created_at", rows[0])
        self.assertTrue(rows[0]["created_at"])

    async def test_reset_affinity_refreshes_cached_value(self):
        self.assertEqual(await self.dao.get_affinity("42"), 50)

        await self.dao.reset_affinity("42", 10)

        self.assertEqual(await self.dao.get_affinity("42"), 10)

    async def test_recover_all_affinity_clears_stale_cache(self):
        await self.dao.reset_affinity("42", 10)
        self.assertEqual(await self.dao.get_affinity("42"), 10)

        await self.dao.recover_all_affinity(5)

        self.assertEqual(await self.dao.get_affinity("42"), 15)
