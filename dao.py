import logging
import asyncio
import aiosqlite
import time
import uuid
from datetime import datetime
from functools import wraps

logger = logging.getLogger("astrbot")


def with_db_retry(retries=3, delay=0.5):
    """
    异步指数退避重试装饰器，用于封装 DAO 的数据库读写。
    消除重复样板代码，提升可维护性和 DRY 性。
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                    else:
                        raise e

        return wrapper

    return decorator


class SelfEvolutionDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db_conn = None
        self._db_lock = None
        self._write_lock = None
        # 好感度内存缓存（60秒过期）
        self._affinity_cache = {}
        self._affinity_cache_time = {}
        self._cache_ttl = 60  # 缓存60秒
        # 连接探针优化：每10次请求才探针一次
        self._probe_counter = 0
        self._probe_interval = 10  # 每10次请求探针一次
        self._last_probe_time = 0
        self._probe_interval_seconds = 30  # 至少30秒探针一次

    async def _get_cached_affinity(self, user_id: str) -> int | None:
        """从缓存获取好感度"""
        if user_id in self._affinity_cache:
            cache_time = self._affinity_cache_time.get(user_id, 0)
            if time.time() - cache_time < self._cache_ttl:
                return self._affinity_cache[user_id]
        return None

    async def _set_cached_affinity(self, user_id: str, score: int):
        """设置好感度缓存"""
        self._affinity_cache[user_id] = score
        self._affinity_cache_time[user_id] = time.time()
        # 定期清理过期缓存
        if len(self._affinity_cache) > 1000:
            current_time = time.time()
            expired = [
                k
                for k, t in self._affinity_cache_time.items()
                if current_time - t >= self._cache_ttl
            ]
            for k in expired:
                self._affinity_cache.pop(k, None)
                self._affinity_cache_time.pop(k, None)

    async def init_db(self):
        """兼容旧接口，内部实际上已融入 get_conn 的连接池锁机制，从而规避初始化并发造成的 WAL 锁定冲突"""
        try:
            await self.get_conn()
            logger.info(
                "[SelfEvolution] DAO: 成功在长连接池状态机的保护下建立/验证数据库。"
            )
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] DAO: 初始化 aiosqlite 数据库失败: {e}")

    async def _init_schema(self, db):
        """内部集中化执行数据库 DDL 初始构建"""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_evolutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                new_prompt TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_reflections (
                session_id TEXT PRIMARY KEY,
                is_pending INTEGER NOT NULL DEFAULT 1
            )
        """)
        # CognitionCore 2.0: 情感关系矩阵表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                user_id TEXT PRIMARY KEY,
                affinity_score INTEGER NOT NULL DEFAULT 50,
                last_interaction TEXT NOT NULL
            )
        """)
        # GraphRAG: 用户互动关系表（替代 JSON 文件）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_user_id TEXT NOT NULL,
                target_user_id TEXT,
                group_id TEXT NOT NULL,
                interaction_count INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL,
                traits TEXT,
                UNIQUE(source_user_id, target_user_id, group_id)
            )
        """)
        # 表情包表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT UNIQUE NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                base64_data TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(group_id, user_id, base64_data)
            )
        """)
        # 迁移旧表：添加 uuid 列（如果不存在）
        try:
            await db.execute("ALTER TABLE stickers ADD COLUMN uuid TEXT")
        except:
            pass  # 列已存在忽略错误

        # 迁移旧数据：给已有记录生成 uuid
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM stickers WHERE uuid IS NULL"
        )
        row = await cursor.fetchone()
        if row and row["cnt"] > 0:
            await db.execute(
                "UPDATE stickers SET uuid = ? WHERE uuid IS NULL",
                (uuid.uuid4().hex,),
            )
            await db.commit()

    async def get_conn(self):
        """带有存活检测的全局连接获取器，兼顾长连接性能与雪崩恢复，防阻塞分离读写锁"""
        if self._db_lock is None:
            self._db_lock = asyncio.Lock()
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()

        async with self._db_lock:
            if self.db_conn is None:
                self.db_conn = await aiosqlite.connect(self.db_path)
                await self.db_conn.execute("PRAGMA journal_mode=WAL;")
                self.db_conn.row_factory = aiosqlite.Row
                await self._init_schema(self.db_conn)
                self._last_probe_time = time.time()

        # 优化探针频率：每隔一定次数或时间才探针
        self._probe_counter += 1
        current_time = time.time()
        should_probe = (
            self._probe_counter >= self._probe_interval
            or current_time - self._last_probe_time >= self._probe_interval_seconds
        )

        if not should_probe:
            return self.db_conn

        # 执行探针
        self._probe_counter = 0
        self._last_probe_time = current_time

        try:

            async def probe():
                async with self.db_conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()

            await asyncio.wait_for(probe(), timeout=2.0)
        except Exception:
            logger.warning(
                "[SelfEvolution] DAO: 侦测到 SQLite 长连接句柄丢失或断裂，尝试热重连机制..."
            )
            async with self._db_lock:
                # Double-check 预防并发协程在等待锁时已经被前面的人重设连接，同样增加时限防护
                try:

                    async def p_probe():
                        async with self.db_conn.execute("SELECT 1") as cursor:
                            await cursor.fetchone()

                    await asyncio.wait_for(p_probe(), timeout=2.0)
                except Exception:
                    if self.db_conn:
                        try:
                            # 显式关闭旧连接，确保操作系统回收底层文件描述符
                            await self.db_conn.close()
                        except Exception:
                            pass
                    try:
                        self.db_conn = await aiosqlite.connect(self.db_path)
                        await self.db_conn.execute("PRAGMA journal_mode=WAL;")
                        self.db_conn.row_factory = aiosqlite.Row
                        await self._init_schema(self.db_conn)
                    except Exception as e:
                        logger.error(
                            f"[SelfEvolution] DAO重连与建表崩溃, 数据库文件极可能已被移出损毁: {e}"
                        )
                        self.db_conn = None
                        raise
        return self.db_conn

    async def close(self):
        """带死锁防范的优雅停机"""
        if self._db_lock is not None:
            try:
                # 尝试拿锁，但强制赋予极短的界限，若遭遇他方恶意挂起占锁，则强行击穿进行底层脱轨回收
                await asyncio.wait_for(self._db_lock.acquire(), timeout=3.0)
                try:
                    if self.db_conn is not None:
                        try:
                            await self.db_conn.close()
                        except Exception:
                            pass
                        self.db_conn = None
                finally:
                    self._db_lock.release()
            except asyncio.TimeoutError:
                logger.error(
                    "[SelfEvolution] 紧急关闭：_db_lock 被阻断超时！强制越权解除底层 aiosqlite 绑定以防宿主平台卸载雪崩。"
                )
                if self.db_conn:
                    try:
                        await self.db_conn.close()
                    except Exception:
                        pass
                self.db_conn = None

    @with_db_retry()
    async def add_pending_evolution(
        self, persona_id: str, new_prompt: str, reason: str
    ):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO pending_evolutions (timestamp, persona_id, new_prompt, reason, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    persona_id,
                    new_prompt,
                    reason,
                    "pending_approval",
                ),
            )
            await db.commit()

    @with_db_retry()
    async def get_pending_evolutions(self, limit: int, offset: int):
        db = await self.get_conn()
        async with db.execute(
            "SELECT id, persona_id, reason, status FROM pending_evolutions WHERE status = 'pending_approval' ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            return await cursor.fetchall()

    @with_db_retry()
    async def get_evolution(self, request_id: int):
        db = await self.get_conn()
        async with db.execute(
            "SELECT persona_id, new_prompt FROM pending_evolutions WHERE id = ? AND status = 'pending_approval'",
            (request_id,),
        ) as cursor:
            return await cursor.fetchone()

    @with_db_retry()
    async def update_evolution_status(self, request_id: int, status: str):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "UPDATE pending_evolutions SET status = ? WHERE id = ?",
                (status, request_id),
            )
            await db.commit()

    @with_db_retry()
    async def clear_pending_evolutions(self):
        """批量清理（标记为已清除）所有待审批的进化请求"""
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "UPDATE pending_evolutions SET status = 'cleared' WHERE status = 'pending_approval'"
            )
            await db.commit()

    @with_db_retry()
    async def set_pending_reflection(self, session_id: str, is_pending: bool):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO pending_reflections (session_id, is_pending) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET is_pending=?",
                (session_id, int(is_pending), int(is_pending)),
            )
            await db.commit()

    @with_db_retry()
    async def pop_pending_reflection(self, session_id: str) -> bool:
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "UPDATE pending_reflections SET is_pending = 0 WHERE session_id = ? AND is_pending = 1",
                (session_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    # --- CognitionCore 2.0: 情感矩阵 DAO ---
    @with_db_retry()
    async def get_affinity(self, user_id: str) -> int:
        user_id = str(user_id)  # 确保类型一致
        # 优先从缓存获取
        cached = await self._get_cached_affinity(user_id)
        if cached is not None:
            return cached
        # 缓存未命中，查询数据库
        db = await self.get_conn()
        async with db.execute(
            "SELECT affinity_score FROM user_relationships WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            score = row["affinity_score"] if row else 50
            # 写入缓存
            await self._set_cached_affinity(user_id, score)
            return score

    @with_db_retry()
    async def update_affinity(self, user_id: str, delta: int):
        user_id = str(user_id)  # 确保类型一致
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "SELECT affinity_score FROM user_relationships WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                new_score = max(0, min(100, row["affinity_score"] + delta))
                await db.execute(
                    "UPDATE user_relationships SET affinity_score = ?, last_interaction = ? WHERE user_id = ?",
                    (new_score, datetime.now().isoformat(), user_id),
                )
            else:
                new_score = max(0, min(100, 50 + delta))
                await db.execute(
                    "INSERT INTO user_relationships (user_id, affinity_score, last_interaction) VALUES (?, ?, ?)",
                    (user_id, new_score, datetime.now().isoformat()),
                )
            await db.commit()
            # 更新缓存
            await self._set_cached_affinity(user_id, new_score)

    @with_db_retry()
    async def recover_all_affinity(self, recovery_amount: int = 1):
        """
        [大赦天下]: 统一恢复所有人的好感度（用于定时任务）。
        通常用于缓解长期黑名单导致的死局。
        """
        db = await self.get_conn()
        async with self._write_lock:
            # 仅给积分小于 50 的人慢慢恢复，上限 50
            await db.execute(
                """
                UPDATE user_relationships 
                SET affinity_score = MIN(50, affinity_score + ?)
                WHERE affinity_score < 50
            """,
                (recovery_amount,),
            )
            await db.commit()

    @with_db_retry()
    async def reset_affinity(self, user_id: str, score: int = 50):
        """管理员强制重置好感度"""
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                """
                INSERT INTO user_relationships (user_id, affinity_score, last_interaction)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    affinity_score = ?,
                    last_interaction = ?
            """,
                (
                    user_id,
                    score,
                    datetime.now().isoformat(),
                    score,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    @with_db_retry()
    async def record_interaction(
        self, source_user_id: str, target_user_id: str, group_id: str
    ):
        """记录用户互动"""
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                """
                INSERT INTO user_interactions (source_user_id, target_user_id, group_id, interaction_count, last_seen)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(source_user_id, target_user_id, group_id) DO UPDATE SET
                    interaction_count = interaction_count + 1,
                    last_seen = excluded.last_seen
                """,
                (source_user_id, target_user_id, group_id, datetime.now().isoformat()),
            )
            await db.commit()

    @with_db_retry()
    async def get_frequent_interactors(self, user_id: str, limit: int = 5):
        """获取与用户互动最频繁的用户列表"""
        db = await self.get_conn()
        async with db.execute(
            """
            SELECT target_user_id, interaction_count 
            FROM user_interactions 
            WHERE source_user_id = ? AND target_user_id IS NOT NULL
            ORDER BY interaction_count DESC 
            LIMIT ?
        """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(row[0], row[1]) for row in rows]

    @with_db_retry()
    async def get_user_groups(self, user_id: str):
        """获取用户所在的所有群"""
        db = await self.get_conn()
        async with db.execute(
            "SELECT DISTINCT group_id FROM user_interactions WHERE source_user_id = ?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    # --- 图片描述缓存 ---
    @with_db_retry()
    async def add_image_cache(self, image_hash: str, caption: str, summary: str):
        """存入图片描述缓存（完整描述 + 简短标签）"""
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT OR REPLACE INTO user_interactions (source_user_id, target_user_id, group_id, interaction_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    source_user_id,
                    target_user_id,
                    group_id,
                    interaction_type,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    # ========== 表情包相关方法 ==========

    @with_db_retry()
    async def add_sticker(
        self, group_id: str, user_id: str, base64_data: str, tags: str = ""
    ) -> bool:
        """添加表情包到数据库"""
        db = await self.get_conn()
        async with self._write_lock:
            try:
                sticker_uuid = uuid.uuid4().hex
                await db.execute(
                    "INSERT INTO stickers (uuid, group_id, user_id, base64_data, tags, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                    (sticker_uuid, group_id, user_id, base64_data, tags),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    @with_db_retry()
    async def get_sticker_count(self) -> int:
        """获取表情包总数"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM stickers")
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    @with_db_retry()
    async def get_today_sticker_count(self) -> int:
        """获取今日新增表情包数量"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM stickers WHERE date(created_at) = date('now')"
            )
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    @with_db_retry()
    async def get_untagged_stickers(self, limit: int = 1) -> list:
        """获取未打标签的表情包"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT id, group_id, user_id, base64_data FROM stickers WHERE tags = '' OR tags IS NULL LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                }
                for row in rows
            ]

    @with_db_retry()
    async def update_sticker_tags_by_uuid(self, sticker_uuid: str, tags: str) -> bool:
        """根据UUID更新表情包标签"""
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "UPDATE stickers SET tags = ? WHERE uuid = ?", (tags, sticker_uuid)
            )
            await db.commit()
            return cursor.rowcount > 0

    @with_db_retry()
    async def get_stickers_by_tags(
        self, tags: str = None, limit: int = 10, offset: int = 0
    ) -> list:
        """根据标签搜索表情包（全局）"""
        db = await self.get_conn()
        async with self._db_lock:
            if tags:
                cursor = await db.execute(
                    "SELECT id, uuid, group_id, user_id, base64_data, tags, created_at FROM stickers WHERE tags LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (f"%{tags}%", limit, offset),
                )
            else:
                cursor = await db.execute(
                    "SELECT id, uuid, group_id, user_id, base64_data, tags, created_at FROM stickers ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "uuid": row["uuid"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "tags": row["tags"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    @with_db_retry()
    async def get_random_sticker(self) -> dict | None:
        """随机获取一张表情包（全局）"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT id, group_id, user_id, base64_data, tags, created_at FROM stickers WHERE tags != '' ORDER BY RANDOM() LIMIT 1"
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "tags": row["tags"],
                    "created_at": row["created_at"],
                }
            return None

    @with_db_retry()
    async def delete_sticker_by_uuid(self, sticker_uuid: str) -> bool:
        """根据UUID删除表情包"""
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "DELETE FROM stickers WHERE uuid = ?",
                (sticker_uuid,),
            )
            await db.commit()
            return cursor.rowcount > 0

    @with_db_retry()
    async def delete_oldest_sticker(self) -> bool:
        """删除最旧的表情包"""
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "DELETE FROM stickers WHERE id = (SELECT id FROM stickers ORDER BY created_at ASC LIMIT 1)"
            )
            await db.commit()
            return cursor.rowcount > 0

    @with_db_retry()
    async def get_sticker_stats(self) -> dict:
        """获取表情包统计（全局）"""
        db = await self.get_conn()
        async with self._db_lock:
            total = await db.execute("SELECT COUNT(*) as cnt FROM stickers")
            today = await db.execute(
                "SELECT COUNT(*) as cnt FROM stickers WHERE date(created_at) = date('now')"
            )
            total_row = await total.fetchone()
            today_row = await today.fetchone()
            return {
                "total": total_row["cnt"] if total_row else 0,
                "today": today_row["cnt"] if today_row else 0,
            }

    # ========== 内心独白相关方法 ==========

    @with_db_retry()
    async def get_db_stats(self) -> dict:
        """获取数据库统计信息"""
        db = await self.get_conn()
        stats = {}

        tables = [
            "pending_evolutions",
            "pending_reflections",
            "user_relationships",
            "user_interactions",
            "stickers",
        ]

        async with self._db_lock:
            for table in tables:
                try:
                    cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                    row = await cursor.fetchone()
                    stats[table] = row["cnt"] if row else 0
                except Exception as e:
                    stats[table] = 0

        return stats

    @with_db_retry()
    async def reset_all_data(self) -> dict:
        """清空所有数据表，返回每个表清空的数量"""
        db = await self.get_conn()
        results = {}

        tables = [
            "pending_evolutions",
            "pending_reflections",
            "user_relationships",
            "user_interactions",
            "stickers",
        ]

        async with self._write_lock:
            for table in tables:
                try:
                    cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                    row = await cursor.fetchone()
                    count = row["cnt"] if row else 0

                    await db.execute(f"DELETE FROM {table}")
                    results[table] = count
                except Exception as e:
                    results[table] = f"错误: {e}"

            await db.commit()
            return results
