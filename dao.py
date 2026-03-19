import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import aiosqlite

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
            expired = [k for k, t in self._affinity_cache_time.items() if current_time - t >= self._cache_ttl]
            for k in expired:
                self._affinity_cache.pop(k, None)
                self._affinity_cache_time.pop(k, None)

    async def init_db(self):
        """兼容旧接口，内部实际上已融入 get_conn 的连接池锁机制，从而规避初始化并发造成的 WAL 锁定冲突"""
        try:
            await self.get_conn()
            logger.info("[SelfEvolution] DAO: 成功在长连接池状态机的保护下建立/验证数据库。")
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
        # 表情包表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT UNIQUE NOT NULL,
                hash TEXT UNIQUE NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                base64_data TEXT NOT NULL,
                tags TEXT DEFAULT '',
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(group_id, user_id, base64_data)
            )
        """)
        # 会话反思表（单会话内省）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_reflections (
                session_id TEXT PRIMARY KEY,
                note TEXT,
                facts TEXT,
                bias TEXT,
                created_at TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )
        """)
        # 会话日报表（沿用 group_daily_reports 表名以兼容旧数据）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(group_id, created_at)
            )
        """)
        # 已知会话范围表（用于后台任务在重启后恢复群聊/私聊目标）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS known_scopes (
                scope_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
        """)
        # 好感度关系表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                user_id TEXT PRIMARY KEY,
                affinity_score INTEGER NOT NULL DEFAULT 50,
                last_interaction TEXT NOT NULL
            )
        """)
        # 迁移旧表：添加 uuid 列（如果不存在）
        try:
            await db.execute("ALTER TABLE stickers ADD COLUMN uuid TEXT")
        except:
            pass  # 列已存在忽略错误

        # 迁移旧表：添加 hash 列（如果不存在）
        try:
            await db.execute("ALTER TABLE stickers ADD COLUMN hash TEXT")
        except:
            pass  # 列已存在忽略错误

        # 迁移旧表：添加 description 列（如果不存在）
        try:
            await db.execute("ALTER TABLE stickers ADD COLUMN description TEXT")
        except:
            pass  # 列已存在忽略错误

        # 迁移旧数据：给已有记录生成 uuid 和 hash（基于 base64_data 计算）
        cursor = await db.execute("SELECT id, uuid, base64_data FROM stickers WHERE uuid IS NULL OR hash IS NULL")
        rows = await cursor.fetchall()
        for row in rows:
            row_id = row["id"]
            base64_data = row["base64_data"]
            if base64_data:
                new_hash = hashlib.md5(base64_data.encode()).hexdigest()
            else:
                new_hash = uuid.uuid4().hex
            new_uuid = row["uuid"] if row["uuid"] else uuid.uuid4().hex
            await db.execute(
                "UPDATE stickers SET uuid = ?, hash = ? WHERE id = ?",
                (new_uuid, new_hash, row_id),
            )
        if rows:
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
            logger.warning("[SelfEvolution] DAO: 侦测到 SQLite 长连接句柄丢失或断裂，尝试热重连机制...")
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
                        logger.error(f"[SelfEvolution] DAO重连与建表崩溃, 数据库文件极可能已被移出损毁: {e}")
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
            except TimeoutError:
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
    async def add_pending_evolution(self, persona_id: str, new_prompt: str, reason: str):
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
    async def save_session_reflection(self, session_id: str, user_id: str, note: str, facts: str = "", bias: str = ""):
        """保存会话反思（使用session_id + user_id作为复合键，避免群聊串用户）"""
        db = await self.get_conn()
        reflection_key = f"{session_id}_{user_id}"
        async with self._write_lock:
            await db.execute(
                "INSERT OR REPLACE INTO session_reflections (session_id, note, facts, bias, created_at, consumed) VALUES (?, ?, ?, ?, ?, 0)",
                (reflection_key, note, facts, bias, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            await db.commit()
            logger.debug(f"[DAO] 已保存会话反思: key={reflection_key}")

    @with_db_retry()
    async def get_session_reflection(self, session_id: str, user_id: str) -> Optional[dict]:
        """获取未消费的会话反思（使用session_id + user_id作为复合键）"""
        db = await self.get_conn()
        reflection_key = f"{session_id}_{user_id}"
        async with self._write_lock:
            cursor = await db.execute(
                "SELECT session_id, note, facts, bias, created_at FROM session_reflections WHERE session_id = ? AND consumed = 0",
                (reflection_key,),
            )
            row = await cursor.fetchone()
            if row:
                return {"session_id": row[0], "note": row[1], "facts": row[2], "bias": row[3], "created_at": row[4]}
            return None

    @with_db_retry()
    async def delete_session_reflection(self, session_id: str, user_id: str):
        """删除（消费）会话反思"""
        db = await self.get_conn()
        reflection_key = f"{session_id}_{user_id}"
        async with self._write_lock:
            await db.execute(
                "UPDATE session_reflections SET consumed = 1 WHERE session_id = ?",
                (reflection_key,),
            )
            await db.commit()
            logger.debug(f"[DAO] 已消费会话反思: key={reflection_key}")

    @with_db_retry()
    async def save_group_daily_report(self, group_id: str, summary: str, created_at: str | None = None):
        """保存会话日报"""
        db = await self.get_conn()
        async with self._write_lock:
            report_date = created_at or datetime.now().astimezone().strftime("%Y-%m-%d")
            await db.execute(
                "INSERT OR REPLACE INTO group_daily_reports (group_id, summary, created_at) VALUES (?, ?, ?)",
                (group_id, summary, report_date),
            )
            await db.commit()
            logger.debug(f"[DAO] 已保存会话日报: group_id={group_id}, date={report_date}")

    @with_db_retry()
    async def get_latest_group_report(self, group_id: str) -> Optional[dict]:
        """获取最新的会话日报"""
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "SELECT group_id, summary, created_at FROM group_daily_reports WHERE group_id = ? ORDER BY created_at DESC LIMIT 1",
                (group_id,),
            )
            row = await cursor.fetchone()
            if row:
                return {"group_id": row[0], "summary": row[1], "created_at": row[2]}
            return None

    @with_db_retry()
    async def get_group_reports(self, group_id: str, days: int = 7) -> list:
        """获取最近N天的会话日报"""
        db = await self.get_conn()
        async with self._write_lock:
            cutoff_date = (datetime.now().astimezone().date() - timedelta(days=max(days, 0))).strftime("%Y-%m-%d")
            cursor = await db.execute(
                "SELECT group_id, summary, created_at FROM group_daily_reports WHERE group_id = ? AND created_at >= ? ORDER BY created_at DESC",
                (group_id, cutoff_date),
            )
            rows = await cursor.fetchall()
            return [{"group_id": r[0], "summary": r[1], "created_at": r[2]} for r in rows]

    @with_db_retry()
    async def touch_known_scope(self, scope_id: str):
        """记录最近出现过的会话范围，供后台任务恢复使用。"""
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_id:
            return

        scope_type = "private" if normalized_scope_id.startswith("private_") else "group"
        last_seen_at = datetime.now().astimezone().isoformat()
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT OR REPLACE INTO known_scopes (scope_id, scope_type, last_seen_at) VALUES (?, ?, ?)",
                (normalized_scope_id, scope_type, last_seen_at),
            )
            await db.commit()

    @with_db_retry()
    async def list_known_scopes(self, scope_type: str | None = None) -> list[str]:
        """列出最近见过的会话范围。"""
        db = await self.get_conn()
        if scope_type:
            cursor = await db.execute(
                "SELECT scope_id FROM known_scopes WHERE scope_type = ? ORDER BY last_seen_at DESC",
                (scope_type,),
            )
        else:
            cursor = await db.execute("SELECT scope_id FROM known_scopes ORDER BY last_seen_at DESC")
        rows = await cursor.fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

    @with_db_retry()
    async def get_affinity(self, user_id: str) -> int:
        user_id = str(user_id)
        cached = await self._get_cached_affinity(user_id)
        if cached is not None:
            return cached
        db = await self.get_conn()
        async with db.execute(
            "SELECT affinity_score FROM user_relationships WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            score = row["affinity_score"] if row else 50
            await self._set_cached_affinity(user_id, score)
            return score

    @with_db_retry()
    async def update_affinity(self, user_id: str, delta: int):
        user_id = str(user_id)
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
            await self._set_cached_affinity(user_id, new_score)

    @with_db_retry()
    async def recover_all_affinity(self, recovery_amount: int = 1):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "UPDATE user_relationships SET affinity_score = MIN(50, affinity_score + ?) WHERE affinity_score < 50",
                (recovery_amount,),
            )
            await db.commit()
            self._affinity_cache.clear()
            self._affinity_cache_time.clear()

    @with_db_retry()
    async def reset_affinity(self, user_id: str, score: int = 50):
        user_id = str(user_id)
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO user_relationships (user_id, affinity_score, last_interaction) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET affinity_score = ?, last_interaction = ?",
                (
                    user_id,
                    score,
                    datetime.now().isoformat(),
                    score,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()
            await self._set_cached_affinity(user_id, score)

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
            await db.execute("UPDATE pending_evolutions SET status = 'cleared' WHERE status = 'pending_approval'")
            await db.commit()

    # ========== 表情包相关方法 ==========

    @with_db_retry()
    async def add_sticker(
        self,
        group_id: str,
        user_id: str,
        base64_data: str,
        tags: str = "",
        sticker_hash: str = None,
        description: str = "",
    ) -> str | None:
        """添加表情包到数据库，返回uuid或None"""
        db = await self.get_conn()
        async with self._write_lock:
            try:
                sticker_uuid = uuid.uuid4().hex
                if sticker_hash is None:
                    sticker_hash = hashlib.md5(base64_data.encode()).hexdigest()
                await db.execute(
                    "INSERT INTO stickers (uuid, hash, group_id, user_id, base64_data, tags, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        sticker_uuid,
                        sticker_hash,
                        group_id,
                        user_id,
                        base64_data,
                        tags,
                        description,
                    ),
                )
                await db.commit()
                return sticker_uuid
            except aiosqlite.IntegrityError:
                return None

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
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM stickers WHERE date(created_at) = date('now')")
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    @with_db_retry()
    async def get_untagged_stickers(self, limit: int = 1) -> list:
        """获取未打标签的表情包"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT id, uuid, group_id, user_id, base64_data, created_at "
                "FROM stickers WHERE tags = '' OR tags IS NULL ORDER BY id ASC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "uuid": row["uuid"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    @with_db_retry()
    async def update_sticker_tags_by_uuid(self, sticker_uuid: str, tags: str, description: str = "") -> bool:
        """根据UUID更新表情包标签和描述"""
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute(
                "UPDATE stickers SET tags = ?, description = ? WHERE uuid = ?",
                (tags, description, sticker_uuid),
            )
            await db.commit()
            return cursor.rowcount > 0

    @with_db_retry()
    async def get_stickers_by_tags(self, tags: str = None, limit: int = 10, offset: int = 0) -> list:
        """根据标签搜索表情包（全局）"""
        db = await self.get_conn()
        async with self._db_lock:
            if tags:
                cursor = await db.execute(
                    "SELECT id, uuid, group_id, user_id, base64_data, tags, description, created_at FROM stickers WHERE tags LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (f"%{tags}%", limit, offset),
                )
            else:
                cursor = await db.execute(
                    "SELECT id, uuid, group_id, user_id, base64_data, tags, description, created_at FROM stickers ORDER BY id DESC LIMIT ? OFFSET ?",
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
                    "description": row["description"] or "",
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
                "SELECT id, group_id, user_id, base64_data, tags, description, created_at FROM stickers WHERE tags != '' ORDER BY RANDOM() LIMIT 1"
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "tags": row["tags"],
                    "description": row["description"] or "",
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
            today = await db.execute("SELECT COUNT(*) as cnt FROM stickers WHERE date(created_at) = date('now')")
            total_row = await total.fetchone()
            today_row = await today.fetchone()
            return {
                "total": total_row["cnt"] if total_row else 0,
                "today": today_row["cnt"] if today_row else 0,
            }

    @with_db_retry()
    async def get_sticker_by_hash(self, sticker_hash: str) -> dict | None:
        """根据hash获取表情包信息"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT id, uuid, hash, group_id, user_id, base64_data, tags, description, created_at FROM stickers WHERE hash = ?",
                (sticker_hash,),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "uuid": row["uuid"],
                    "hash": row["hash"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "tags": row["tags"],
                    "description": row["description"] or "",
                    "created_at": row["created_at"],
                }
            return None

    @with_db_retry()
    async def get_sticker_by_uuid(self, sticker_uuid: str) -> dict | None:
        """根据uuid获取表情包信息"""
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT id, uuid, hash, group_id, user_id, base64_data, tags, description, created_at FROM stickers WHERE uuid = ?",
                (sticker_uuid,),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "uuid": row["uuid"],
                    "hash": row["hash"],
                    "group_id": row["group_id"],
                    "user_id": row["user_id"],
                    "base64_data": row["base64_data"],
                    "tags": row["tags"],
                    "description": row["description"] or "",
                    "created_at": row["created_at"],
                }
            return None

    # ========== 内心独白相关方法 ==========

    @with_db_retry()
    async def get_db_stats(self) -> dict:
        """获取数据库统计信息"""
        db = await self.get_conn()
        stats = {}

        tables = [
            "pending_evolutions",
            "session_reflections",
            "group_daily_reports",
            "user_relationships",
            "stickers",
        ]

        async with self._db_lock:
            for table in tables:
                try:
                    cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                    row = await cursor.fetchone()
                    stats[table] = row["cnt"] if row else 0
                except Exception:
                    stats[table] = 0

        return stats

    @with_db_retry()
    async def reset_all_data(self) -> dict:
        """清空所有数据表，返回每个表清空的数量"""
        db = await self.get_conn()
        results = {}

        tables = [
            "pending_evolutions",
            "session_reflections",
            "group_daily_reports",
            "user_relationships",
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
