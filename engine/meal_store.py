"""
群菜单存储模块 - 基于本地文件系统的群菜单资产
每群一个 JSON 文件，按 group_id 隔离
"""

import asyncio
import json
import os
from pathlib import Path

from astrbot.api import logger


class MealStore:
    """
    群菜单本地资产存储
    每个群一个 JSON 文件，文件名为 group_id.json
    """

    INDEX_VERSION = 1

    def __init__(self, meals_dir: Path):
        self.meals_dir = Path(meals_dir)
        self._lock = asyncio.Lock()
        self._cache: dict[str, list[str]] = {}

    async def _ensure_dir(self):
        """确保目录存在"""
        self.meals_dir.mkdir(parents=True, exist_ok=True)

    def _get_meal_file(self, group_id: str) -> Path:
        """获取群的菜单文件路径"""
        safe_group_id = str(group_id).replace("/", "_").replace("\\", "_")
        return self.meals_dir / f"{safe_group_id}.json"

    async def load_meals(self, group_id: str) -> list[str]:
        """加载群的菜单"""
        async with self._lock:
            if group_id in self._cache:
                return self._cache[group_id].copy()

            await self._ensure_dir()
            meal_file = self._get_meal_file(group_id)

            if not meal_file.exists():
                self._cache[group_id] = []
                return []

            try:
                with open(meal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                meals = data.get("meals", [])
                self._cache[group_id] = meals
                return meals.copy()
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[MealStore] 加载群 {group_id} 菜单失败，使用空菜单: {e}")
                self._cache[group_id] = []
                return []

    async def save_meals(self, group_id: str, meals: list[str]):
        """保存群的菜单"""
        async with self._lock:
            await self._ensure_dir()
            meal_file = self._get_meal_file(group_id)
            temp_file = meal_file.with_suffix(".json.tmp")
            data = {"version": self.INDEX_VERSION, "meals": meals}
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            for _ in range(3):
                try:
                    os.replace(temp_file, meal_file)
                    break
                except PermissionError:
                    await asyncio.sleep(0.05)
            self._cache[group_id] = meals

    async def add_meal(self, group_id: str, meal: str, max_items: int = 100) -> tuple[bool, str]:
        """
        添加菜品到群菜单

        Args:
            group_id: 群号
            meal: 菜名
            max_items: 最大菜品数量

        Returns:
            (success, message) - success 表示是否添加成功
        """
        meals = await self.load_meals(group_id)
        meal = meal.strip()

        if not meal:
            return False, "菜名不能为空"

        if meal in meals:
            return False, f"'{meal}' 已在菜单中"

        hard_limit = 500
        effective_max = min(max_items, hard_limit)

        if len(meals) >= effective_max:
            return False, f"菜单已满（{effective_max} 道），请先删除一些菜品"

        meals.append(meal)
        await self.save_meals(group_id, meals)
        return True, f"已添加：{meal}（当前 {len(meals)} 道菜）"

    async def del_meal(self, group_id: str, meal: str) -> tuple[bool, str]:
        """
        从群菜单删除菜品

        Args:
            group_id: 群号
            meal: 菜名

        Returns:
            (success, message)
        """
        meals = await self.load_meals(group_id)
        meal = meal.strip()

        if meal not in meals:
            return False, f"'{meal}' 不在菜单中"

        meals.remove(meal)
        await self.save_meals(group_id, meals)
        return True, f"已删除：{meal}（剩余 {len(meals)} 道菜）"

    async def get_random_meals(self, group_id: str, count: int = 1) -> list[str]:
        """
        随机获取菜品

        Args:
            group_id: 群号
            count: 获取数量

        Returns:
            菜品列表
        """
        import random

        meals = await self.load_meals(group_id)
        if not meals:
            return []

        if count >= len(meals):
            return meals.copy()

        return random.sample(meals, count)

    async def clear_cache(self, group_id: str | None = None):
        """清除缓存"""
        async with self._lock:
            if group_id:
                self._cache.pop(group_id, None)
            else:
                self._cache.clear()
