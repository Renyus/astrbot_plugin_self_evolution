import json
import asyncio
import logging
import random
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("astrbot")


class ProfileManager:
    """用户画像管理器 - Markdown 文本格式存储，支持分层失活"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.profile_dir = plugin.data_dir / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.locks = defaultdict(asyncio.Lock)
        # 画像内存缓存 {user_id: content}
        self._profile_cache = {}
        self._cache_ttl = 300  # 缓存5分钟
        self._cache_access_time = {}  # 记录缓存访问时间
        self._last_cache_cleanup = 0

    @property
    def precision_mode(self):
        return self.plugin.config.get("profile_precision_mode", "simple")

    @property
    def dropout_enabled(self):
        return getattr(self.plugin, "dropout_enabled", True)

    @property
    def dropout_edge_rate(self):
        return getattr(self.plugin, "dropout_edge_rate", 0.15)

    @property
    def core_info_keywords(self):
        keywords = getattr(
            self.plugin,
            "core_info_keywords",
            "群主,管理员,OP,owner,admin,好感度,身份,职业,生日",
        )
        return [k.strip() for k in keywords.split(",")]

    def _get_profile_path(self, user_id: str) -> Path:
        return self.profile_dir / f"user_{user_id}.md"

    def _is_core_info(self, line: str) -> bool:
        """判断是否为核心信息（永不丢失）"""
        line_lower = line.lower()
        for keyword in self.core_info_keywords:
            if keyword.lower() in line_lower:
                return True
        return False

    def _cleanup_expired_cache(self):
        """清理过期的缓存"""
        import time

        now = time.time()
        if now - self._last_cache_cleanup < 300:  # 每5分钟最多清理一次
            return
        self._last_cache_cleanup = now

        expired_users = []
        for user_id, access_time in self._cache_access_time.items():
            if now - access_time > self._cache_ttl:
                expired_users.append(user_id)

        for user_id in expired_users:
            self._profile_cache.pop(user_id, None)
            self._cache_access_time.pop(user_id, None)

        if expired_users:
            logger.debug(f"[Profile] 已清理 {len(expired_users)} 个过期缓存")

    async def load_profile(self, user_id: str) -> str:
        """读取用户画像（Markdown 文本），无则返回空"""
        # 定期清理过期缓存
        self._cleanup_expired_cache()

        # 先从缓存读取
        if user_id in self._profile_cache:
            self._cache_access_time[user_id] = time.time()
            return self._profile_cache[user_id]

        path = self._get_profile_path(user_id)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                # 存入缓存
                self._profile_cache[user_id] = content
                self._cache_access_time[user_id] = time.time()
                return content
            except IOError as e:
                logger.warning(f"[Profile] 读取画像失败 {user_id}: {e}")
        return ""

    async def save_profile(self, user_id: str, content: str):
        """保存用户画像（Markdown 文本）"""
        # 定期清理过期缓存
        self._cleanup_expired_cache()

        path = self._get_profile_path(user_id)
        path.write_text(content, encoding="utf-8")
        # 更新缓存
        self._profile_cache[user_id] = content
        self._cache_access_time[user_id] = time.time()
        logger.info(f"[Profile] 已保存用户画像: {user_id}")

    async def get_profile_summary(self, user_id: str) -> str:
        """获取画像摘要（用于注入 LLM）- 支持分层失活"""
        logger.debug(f"[Profile] 获取画像摘要: {user_id}")
        content = await self.load_profile(user_id)
        if not content:
            return ""

        lines = content.split("\n")

        if not self.dropout_enabled:
            preview = "\n".join(lines[:10])
            if len(content) > 500:
                preview += "\n..."
            return preview

        core_lines = []
        edge_lines = []

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if self._is_core_info(line):
                core_lines.append(line)
            else:
                edge_lines.append(line)

        kept_edge = []
        for line in edge_lines:
            if random.random() > self.dropout_edge_rate:
                kept_edge.append(line)

        all_kept = core_lines + kept_edge
        result = "\n".join(all_kept[:10])

        if len(all_kept) > 10:
            result += f"\n... (共 {len(all_kept)} 条，已随机保留)"

        return result

    async def cleanup_expired_profiles(self, days: int = 90):
        """清理过期画像 - 根据文件修改时间删除长时间未更新的画像"""
        try:
            from datetime import timedelta

            cutoff_time = time.time() - (days * 86400)
            deleted_count = 0

            for profile_path in self.profile_dir.glob("user_*.md"):
                try:
                    mtime = profile_path.stat().st_mtime
                    if mtime < cutoff_time:
                        profile_path.unlink()
                        deleted_count += 1
                        logger.info(f"[Profile] 已删除过期画像: {profile_path.name}")
                except Exception as e:
                    logger.warning(f"[Profile] 删除画像失败 {profile_path.name}: {e}")

            if deleted_count > 0:
                logger.info(f"[Profile] 清理完成，共删除 {deleted_count} 个过期画像")
            return deleted_count
        except Exception as e:
            logger.warning(f"[Profile] 清理过期画像失败: {e}")
            return 0

    async def view_profile(self, user_id: str) -> str:
        """查看用户画像"""
        logger.info(f"[Profile] 查看用户画像: {user_id}")
        content = await self.load_profile(user_id)
        if not content:
            return f"用户 {user_id} 暂无画像记录。"
        return f"用户ID: {user_id}\n\n{content}"

    async def delete_profile(self, user_id: str) -> str:
        """删除用户画像"""
        path = self._get_profile_path(user_id)
        if path.exists():
            path.unlink()
            logger.info(f"[Profile] 已删除用户画像: {user_id}")
            return f"已删除用户 {user_id} 的画像。"
        return f"用户 {user_id} 不存在画像记录。"

    async def list_profiles(self) -> dict:
        """列出所有画像统计"""
        logger.info("[Profile] 列出所有画像统计")
        files = list(self.profile_dir.glob("user_*.md"))
        return {
            "total_users": len(files),
            "total_tags": 0,
            "total_traits": 0,
        }
