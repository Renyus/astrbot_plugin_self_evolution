# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests._helpers import cleanup_workspace_temp_dir, load_engine_module, make_workspace_temp_dir

MealStore = load_engine_module("meal_store").MealStore


class MealStoreTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("meal_store")
        self.store = MealStore(Path(self.temp_dir))

    async def asyncTearDown(self):
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_addmeal_success(self):
        success, msg = await self.store.add_meal("group1", "红烧肉", max_items=100)
        self.assertTrue(success)
        self.assertIn("红烧肉", msg)
        meals = await self.store.load_meals("group1")
        self.assertIn("红烧肉", meals)

    async def test_addmeal_deduplication(self):
        await self.store.add_meal("group1", "红烧肉", max_items=100)
        success, msg = await self.store.add_meal("group1", "红烧肉", max_items=100)
        self.assertFalse(success)
        self.assertIn("已在菜单中", msg)
        meals = await self.store.load_meals("group1")
        self.assertEqual(len([m for m in meals if m == "红烧肉"]), 1)

    async def test_addmeal_exceed_max_items(self):
        max_items = 5
        for i in range(max_items):
            await self.store.add_meal("group1", f"菜{i}", max_items=max_items)

        success, msg = await self.store.add_meal("group1", "新菜", max_items=max_items)
        self.assertFalse(success)
        self.assertIn("菜单已满", msg)

    async def test_addmeal_hard_limit_500(self):
        max_items = 1000
        hard_limit = 500
        for i in range(hard_limit + 10):
            result = await self.store.add_meal("group2", f"菜{i}", max_items=max_items)
            if i >= hard_limit:
                self.assertFalse(result[0], f"第 {i} 道菜不应该添加成功")

    async def test_delmeal_success(self):
        await self.store.add_meal("group1", "红烧肉", max_items=100)
        success, msg = await self.store.del_meal("group1", "红烧肉")
        self.assertTrue(success)
        self.assertIn("已删除", msg)
        meals = await self.store.load_meals("group1")
        self.assertNotIn("红烧肉", meals)

    async def test_delmeal_not_found(self):
        success, msg = await self.store.del_meal("group1", "不存在的菜")
        self.assertFalse(success)
        self.assertIn("不在菜单中", msg)

    async def test_get_random_meals_single(self):
        await self.store.add_meal("group1", "红烧肉", max_items=100)
        await self.store.add_meal("group1", "糖醋排骨", max_items=100)
        meals = await self.store.get_random_meals("group1", count=1)
        self.assertEqual(len(meals), 1)
        self.assertIn(meals[0], ["红烧肉", "糖醋排骨"])

    async def test_get_random_meals_multiple(self):
        for i in range(15):
            await self.store.add_meal("group1", f"菜{i}", max_items=100)
        meals = await self.store.get_random_meals("group1", count=10)
        self.assertLessEqual(len(meals), 10)
        self.assertEqual(len(set(meals)), len(meals))

    async def test_get_random_meals_empty(self):
        meals = await self.store.get_random_meals("group_nonexistent", count=5)
        self.assertEqual(meals, [])

    async def test_meal_store_per_group_isolation(self):
        await self.store.add_meal("group1", "红烧肉", max_items=100)
        await self.store.add_meal("group2", "糖醋排骨", max_items=100)
        meals1 = await self.store.load_meals("group1")
        meals2 = await self.store.load_meals("group2")
        self.assertIn("红烧肉", meals1)
        self.assertNotIn("红烧肉", meals2)
        self.assertIn("糖醋排骨", meals2)
        self.assertNotIn("糖醋排骨", meals1)
