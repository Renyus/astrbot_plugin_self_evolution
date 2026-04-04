"""
表情包资产存储模块 - 基于本地文件系统的资产管理
"""

import asyncio
import hashlib
import json
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger


class StickerStore:
    """
    表情包本地资产存储
    管理 index.json 元数据和 files/ 目录下的图片文件
    """

    INDEX_VERSION = 1

    def __init__(self, stickers_dir: Path):
        self.stickers_dir = Path(stickers_dir)
        self.files_dir = self.stickers_dir / "files"
        self.index_file = self.stickers_dir / "index.json"
        self._lock = asyncio.Lock()
        self._index_cache: dict | None = None

    async def _ensure_dirs(self):
        """确保目录结构存在"""
        self.stickers_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    async def load_index(self) -> dict:
        """加载索引文件"""
        async with self._lock:
            if self._index_cache is not None:
                return self._index_cache

            await self._ensure_dirs()

            if not self.index_file.exists():
                index = {"version": self.INDEX_VERSION, "stickers": []}
                self._index_cache = index
                return index

            try:

                def _read():
                    with open(self.index_file, "r", encoding="utf-8") as f:
                        return json.load(f)

                self._index_cache = await asyncio.to_thread(_read)
                return self._index_cache
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[StickerStore] 加载索引失败，使用空索引: {e}")
                index = {"version": self.INDEX_VERSION, "stickers": []}
                self._index_cache = index
                return index

    async def save_index(self, index: dict):
        """保存索引文件"""
        async with self._lock:
            await self._ensure_dirs()
            temp_file = self.index_file.with_suffix(".json.tmp")

            def _write():
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(index, f, ensure_ascii=False, indent=2)

            await asyncio.to_thread(_write)
            temp_file.replace(self.index_file)
            self._index_cache = index

    def _invalidate_cache(self):
        """使缓存失效"""
        self._index_cache = None

    async def _save_sticker_file(self, content: bytes, file_hash: str, mime_type: str) -> Path:
        """保存图片文件到本地"""
        ext = mimetypes.guess_extension(mime_type)
        if not ext or ext == ".jpe":
            ext = ".jpg"

        file_path = self.files_dir / f"{file_hash}{ext}"

        async with self._lock:
            if not file_path.exists():

                def _write():
                    with open(file_path, "wb") as f:
                        f.write(content)

                await asyncio.to_thread(_write)

        return file_path

    async def _compute_content_hash(self, content: bytes) -> str:
        """计算内容 MD5"""
        return hashlib.md5(content).hexdigest()

    async def add_sticker(
        self,
        group_id: str,
        user_id: str,
        content: bytes,
        mime_type: str = "image/jpeg",
        source_url: str = "",
    ) -> dict | None:
        """
        添加表情包

        Args:
            group_id: 群号
            user_id: 用户号
            content: 图片内容
            mime_type: MIME 类型
            source_url: 来源 URL（可选）

        Returns:
            新增的表情包信息，或 None（如果已存在）
        """
        await self._ensure_dirs()

        file_hash = await self._compute_content_hash(content)
        index = await self.load_index()

        if any(s["hash"] == file_hash for s in index["stickers"]):
            logger.debug(f"[StickerStore] 表情包已存在: hash={file_hash[:8]}")
            return None

        sticker_uuid = uuid.uuid4().hex[:12]

        ext = mimetypes.guess_extension(mime_type)
        if not ext or ext == ".jpe":
            ext = ".jpg"
        filename = f"{file_hash}{ext}"

        file_path = await self._save_sticker_file(content, file_hash, mime_type)

        sticker = {
            "uuid": sticker_uuid,
            "hash": file_hash,
            "filename": filename,
            "group_id": group_id,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "source_url": source_url,
            "mime_type": mime_type,
            "disabled": False,
        }

        index["stickers"].append(sticker)
        await self.save_index(index)

        logger.debug(f"[StickerStore] 添加表情包: uuid={sticker_uuid}, hash={file_hash[:8]}")
        return sticker

    async def add_sticker_from_file(
        self,
        file_path: Path,
        group_id: str,
        user_id: str,
        source_url: str = "",
    ) -> dict | None:
        """从本地文件添加表情包"""
        try:

            def _read():
                with open(file_path, "rb") as f:
                    return f.read()

            content = await asyncio.to_thread(_read)

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = "image/jpeg"

            return await self.add_sticker(group_id, user_id, content, mime_type, source_url)
        except Exception as e:
            logger.warning(f"[StickerStore] 从文件添加表情包失败: {file_path}, {e}")
            return None

    async def delete_sticker(self, sticker_uuid: str) -> bool:
        """删除表情包"""
        index = await self.load_index()

        sticker = None
        for s in index["stickers"]:
            if s["uuid"] == sticker_uuid:
                sticker = s
                break

        if not sticker:
            return False

        index["stickers"] = [s for s in index["stickers"] if s["uuid"] != sticker_uuid]
        await self.save_index(index)

        file_path = self.files_dir / sticker["filename"]
        if file_path.exists():
            other_refs = any(other["filename"] == sticker["filename"] for other in index["stickers"])
            if not other_refs:
                file_path.unlink(missing_ok=True)

        logger.debug(f"[StickerStore] 删除表情包: uuid={sticker_uuid}")
        return True

    async def delete_oldest_sticker(self) -> bool:
        """删除最旧的表情包"""
        index = await self.load_index()

        if not index["stickers"]:
            return False

        sorted_stickers = sorted(index["stickers"], key=lambda s: s["created_at"])
        oldest = sorted_stickers[0]

        return await self.delete_sticker(oldest["uuid"])

    async def clear_stickers(self):
        """清空所有表情包"""
        index = await self.load_index()
        index["stickers"] = []
        await self.save_index(index)

        for f in self.files_dir.iterdir():
            if f.is_file():
                f.unlink()

        logger.debug("[StickerStore] 清空所有表情包")

    async def get_sticker(self, sticker_uuid: str) -> dict | None:
        """获取指定 UUID 的表情包"""
        index = await self.load_index()
        for s in index["stickers"]:
            if s["uuid"] == sticker_uuid:
                return s
        return None

    async def disable_sticker(self, sticker_uuid: str) -> bool:
        """禁用指定表情包"""
        index = await self.load_index()
        for s in index["stickers"]:
            if s["uuid"] == sticker_uuid:
                s["disabled"] = True
                await self.save_index(index)
                logger.debug(f"[StickerStore] 禁用表情包: uuid={sticker_uuid}")
                return True
        return False

    async def enable_sticker(self, sticker_uuid: str) -> bool:
        """启用指定表情包"""
        index = await self.load_index()
        for s in index["stickers"]:
            if s["uuid"] == sticker_uuid:
                s["disabled"] = False
                await self.save_index(index)
                logger.debug(f"[StickerStore] 启用表情包: uuid={sticker_uuid}")
                return True
        return False

    async def get_random_sticker(self) -> dict | None:
        """随机获取一张可用表情包"""
        import random

        index = await self.load_index()
        available = [s for s in index["stickers"] if not s.get("disabled", False)]

        if not available:
            return None

        return random.choice(available)

    def get_random_sticker_sync(self) -> dict | None:
        """同步版：使用缓存的 index 随机获取一张可用表情包。缓存未初始化时返回 None。"""
        import random

        if self._index_cache is None:
            return None
        available = [s for s in self._index_cache.get("stickers", []) if not s.get("disabled", False)]
        if not available:
            return None
        return random.choice(available)

    async def list_stickers(self, limit: int = 10, offset: int = 0) -> tuple[list[dict], int]:
        """获取表情包列表"""
        index = await self.load_index()
        total = len(index["stickers"])
        stickers = index["stickers"][offset : offset + limit]
        return stickers, total

    async def get_stats(self) -> dict:
        """获取统计信息"""
        index = await self.load_index()
        today = datetime.now().strftime("%Y-%m-%d")

        total = len(index["stickers"])
        today_count = sum(1 for s in index["stickers"] if s["created_at"].startswith(today))

        return {"total": total, "today": today_count}

    def get_sticker_path(self, sticker: dict) -> Path | None:
        """获取表情包文件的绝对路径"""
        file_path = self.files_dir / sticker["filename"]
        if file_path.exists():
            return file_path
        return None

    async def sync_from_files(self) -> dict:
        """
        同步本地文件到索引
        扫描 files/ 目录，将不在索引中的文件添加到索引
        """
        await self._ensure_dirs()

        index = await self.load_index()
        existing_hashes = {s["hash"] for s in index["stickers"]}

        added = 0
        for file_path in self.files_dir.iterdir():
            if not file_path.is_file():
                continue

            try:

                def _read():
                    with open(file_path, "rb") as f:
                        return f.read()

                content = await asyncio.to_thread(_read)
                file_hash = await self._compute_content_hash(content)

                if file_hash in existing_hashes:
                    continue

                mime_type, _ = mimetypes.guess_type(str(file_path))
                if not mime_type:
                    mime_type = "image/jpeg"

                ext = mimetypes.guess_extension(mime_type)
                if not ext or ext == ".jpe":
                    ext = ".jpg"

                sticker_uuid = uuid.uuid4().hex[:12]
                sticker = {
                    "uuid": sticker_uuid,
                    "hash": file_hash,
                    "filename": file_path.name,
                    "group_id": "local",
                    "user_id": "local_admin",
                    "created_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                    "source_url": "",
                    "mime_type": mime_type,
                    "disabled": False,
                }

                index["stickers"].append(sticker)
                existing_hashes.add(file_hash)
                added += 1
                logger.debug(f"[StickerStore] 同步本地文件: {file_path.name}")

            except Exception as e:
                logger.warning(f"[StickerStore] 同步文件失败: {file_path}, {e}")

        if added > 0:
            await self.save_index(index)

        removed = 0
        for sticker in index["stickers"]:
            file_path = self.files_dir / sticker["filename"]
            if not file_path.exists():
                index["stickers"].remove(sticker)
                removed += 1
                logger.debug(f"[StickerStore] 移除孤立记录: {sticker['uuid']}")

        if removed > 0:
            await self.save_index(index)

        logger.info(f"[StickerStore] 同步完成: 新增 {added}, 移除孤立 {removed}")
        return {"added": added, "removed": removed}

    async def cleanup_orphaned_files(self) -> int:
        """清理没有元数据引用的文件"""
        index = await self.load_index()
        referenced_files = {s["filename"] for s in index["stickers"]}

        removed = 0
        for file_path in self.files_dir.iterdir():
            if file_path.is_file() and file_path.name not in referenced_files:
                file_path.unlink()
                removed += 1
                logger.debug(f"[StickerStore] 清理孤立文件: {file_path.name}")

        return removed

    async def migrate_from_db(self, dao) -> dict:
        """
        【一次性迁移工具】从旧数据库迁移表情包到文件资产。

        警告：此方法为迁移期一次性使用。迁移完成后请勿再调用。
        迁移完成后应删除此方法。

        Args:
            dao: 旧 DAO 实例

        Returns:
            dict: {"success": int, "failed": int, "errors": list}
        """
        import aiohttp

        try:
            all_stickers = await dao.get_all_stickers()
        except Exception as e:
            logger.warning(f"[StickerStore] 迁移失败，无法读取旧数据库: {e}")
            return {"success": 0, "failed": 0, "errors": [str(e)]}

        success = 0
        failed = 0
        errors = []

        for sticker in all_stickers:
            url = sticker.get("url", "")
            if not url:
                errors.append(f"sticker {sticker['uuid']}: 无URL")
                failed += 1
                continue

            if not url.startswith("http://") and not url.startswith("https://"):
                errors.append(f"sticker {sticker['uuid']}: 非HTTP URL，跳过")
                continue

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            errors.append(f"sticker {sticker['uuid']}: 下载失败 HTTP {resp.status}")
                            failed += 1
                            continue
                        image_content = await resp.read()

                mime_type = resp.headers.get("Content-Type", "image/jpeg")

                result = await self.add_sticker(
                    group_id=sticker.get("group_id", "migrated"),
                    user_id=sticker.get("user_id", "migrated"),
                    content=image_content,
                    mime_type=mime_type,
                    source_url=url,
                )

                if result:
                    success += 1
                    logger.debug(f"[StickerStore] 迁移成功: {sticker['uuid']}")
                else:
                    logger.debug(f"[StickerStore] 迁移跳过（已存在）: {sticker['uuid']}")

            except Exception as e:
                errors.append(f"sticker {sticker['uuid']}: {str(e)}")
                failed += 1
                logger.warning(f"[StickerStore] 迁移失败: {sticker['uuid']}, {e}")

        logger.info(f"[StickerStore] 迁移完成: 成功 {success}, 失败 {failed}")
        return {"success": success, "failed": failed, "errors": errors}
