import asyncio
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id TEXT PRIMARY KEY,
                group_name TEXT NOT NULL DEFAULT '',
                interest_score REAL NOT NULL DEFAULT 0.0,
                last_interaction TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_id TEXT UNIQUE NOT NULL,
                scope_type TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                profile_data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                report_date TEXT NOT NULL,
                report_content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(group_id, report_date)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                user_id TEXT PRIMARY KEY,
                affinity_score INTEGER NOT NULL DEFAULT 50,
                last_interaction TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS affinity_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                delta INTEGER NOT NULL,
                triggered_at TEXT NOT NULL,
                triggered_date TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_interactions (
                user_id TEXT PRIMARY KEY,
                last_interaction_date TEXT NOT NULL,
                consecutive_days INTEGER NOT NULL DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS known_scopes (
                scope_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_state (
                scope_id TEXT PRIMARY KEY,
                last_bot_engagement_at TEXT,
                last_bot_engagement_level TEXT,
                last_seen_message_seq INTEGER,
                scene_type TEXT DEFAULT 'casual',
                message_count_window INTEGER DEFAULT 0,
                question_count_window INTEGER DEFAULT 0,
                emotion_count_window INTEGER DEFAULT 0,
                consecutive_bot_replies INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_evolutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                new_prompt TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_reflections (
                session_id TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                facts TEXT NOT NULL DEFAULT '',
                bias TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_daily_reports (
                group_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, created_at)
            )
        """)

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
    async def can_apply_affinity_signal(
        self, user_id: str, signal_type: str, cooldown_minutes: int = 60, daily_limit: int = 0
    ) -> tuple[bool, str]:
        user_id = str(user_id)
        db = await self.get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()
        triggered_at = now.isoformat()

        async with self._write_lock:
            cursor = await db.execute(
                "SELECT triggered_at, triggered_date, delta FROM affinity_signals WHERE user_id = ? AND signal_type = ? ORDER BY id DESC LIMIT 1",
                (user_id, signal_type),
            )
            row = await cursor.fetchone()

            if row:
                last_date = row["triggered_date"]
                last_time = datetime.fromisoformat(row["triggered_at"])
                delta_minutes = (now - last_time).total_seconds() / 60

                if last_date == today and daily_limit > 0:
                    daily_count_cursor = await db.execute(
                        "SELECT COUNT(*) as cnt FROM affinity_signals WHERE user_id = ? AND signal_type = ? AND triggered_date = ?",
                        (user_id, signal_type, today),
                    )
                    daily_row = await daily_count_cursor.fetchone()
                    if daily_row and daily_row["cnt"] >= daily_limit:
                        return False, f"daily_limit_reached ({daily_limit}/day)"

                if delta_minutes < cooldown_minutes:
                    remaining = int(cooldown_minutes - delta_minutes)
                    return False, f"cooldown_active ({remaining}min remaining)"

            return True, "ok"

    @with_db_retry()
    async def record_affinity_signal(self, user_id: str, scope_id: str, signal_type: str, delta: int):
        user_id = str(user_id)
        db = await self.get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()

        async with self._write_lock:
            await db.execute(
                "INSERT INTO affinity_signals (user_id, scope_id, signal_type, delta, triggered_at, triggered_date) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, scope_id, signal_type, delta, now.isoformat(), today),
            )
            await db.commit()

    @with_db_retry()
    async def check_returning_user(self, user_id: str) -> bool:
        user_id = str(user_id)
        db = await self.get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        async with self._write_lock:
            cursor = await db.execute(
                "SELECT last_interaction_date, consecutive_days FROM user_interactions WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()

            if row:
                last_date = row["last_interaction_date"]
                if last_date == today:
                    return False
                if last_date == yesterday:
                    await db.execute(
                        "UPDATE user_interactions SET last_interaction_date = ?, consecutive_days = consecutive_days + 1 WHERE user_id = ?",
                        (today, user_id),
                    )
                else:
                    await db.execute(
                        "UPDATE user_interactions SET last_interaction_date = ?, consecutive_days = 1 WHERE user_id = ?",
                        (today, user_id),
                    )
            else:
                await db.execute(
                    "INSERT INTO user_interactions (user_id, last_interaction_date, consecutive_days) VALUES (?, ?, 1)",
                    (user_id, today),
                )
            await db.commit()
            return row is not None and row["last_interaction_date"] == yesterday

    @with_db_retry()
    async def get_affinity_debug_info(self, user_id: str) -> dict:
        user_id = str(user_id)
        db = await self.get_conn()

        cursor = await db.execute(
            "SELECT affinity_score, last_interaction FROM user_relationships WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        affinity_score = row["affinity_score"] if row else 50
        last_interaction = row["last_interaction"] if row else None

        cursor = await db.execute(
            "SELECT signal_type, delta, triggered_at, triggered_date FROM affinity_signals WHERE user_id = ? ORDER BY id DESC LIMIT 20",
            (user_id,),
        )
        signals = await cursor.fetchall()

        cursor = await db.execute(
            "SELECT last_interaction_date, consecutive_days FROM user_interactions WHERE user_id = ?",
            (user_id,),
        )
        interaction_row = await cursor.fetchone()

        return {
            "user_id": user_id,
            "affinity_score": affinity_score,
            "last_interaction": last_interaction,
            "recent_signals": [
                {
                    "signal_type": s["signal_type"],
                    "delta": s["delta"],
                    "triggered_at": s["triggered_at"],
                    "triggered_date": s["triggered_date"],
                }
                for s in signals
            ],
            "returning_user": {
                "last_date": interaction_row["last_interaction_date"] if interaction_row else None,
                "consecutive_days": interaction_row["consecutive_days"] if interaction_row else 0,
            }
            if interaction_row
            else None,
        }

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

    # ========== 表情包相关方法（已废弃，统一使用 StickerStore） ==========

    # @deprecated: 请使用 StickerStore

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

    async def delete_and_rebuild(self) -> dict:
        """删除数据库文件并重建空数据库。"""
        db_file = Path(self.db_path)
        related_files = [
            db_file,
            db_file.with_suffix(f"{db_file.suffix}-wal"),
            db_file.with_suffix(f"{db_file.suffix}-shm"),
        ]
        deleted_files = []

        await self.close()

        for file_path in related_files:
            if file_path.exists():
                with suppress(FileNotFoundError):
                    file_path.unlink()
                    deleted_files.append(file_path.name)

        self._affinity_cache.clear()
        self._affinity_cache_time.clear()
        self._probe_counter = 0
        self._last_probe_time = 0

        await self.init_db()

        return {
            "deleted_files": deleted_files,
            "rebuilt": True,
            "db_path": str(db_file),
        }

    @with_db_retry()
    async def get_engagement_state(self, scope_id: str) -> Optional[dict]:
        db = await self.get_conn()
        async with self._db_lock:
            cursor = await db.execute(
                "SELECT * FROM engagement_state WHERE scope_id = ?",
                (scope_id,),
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    @with_db_retry()
    async def save_engagement_state(self, scope_id: str, state: dict):
        db = await self.get_conn()
        now = datetime.now().isoformat()
        async with self._write_lock:
            await db.execute(
                """INSERT OR REPLACE INTO engagement_state 
                   (scope_id, last_bot_engagement_at, last_bot_engagement_level, 
                    last_seen_message_seq, scene_type, message_count_window,
                    question_count_window, emotion_count_window, consecutive_bot_replies, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope_id,
                    state.get("last_bot_engagement_at"),
                    state.get("last_bot_engagement_level"),
                    state.get("last_seen_message_seq"),
                    state.get("scene_type", "casual"),
                    state.get("message_count_window", 0),
                    state.get("question_count_window", 0),
                    state.get("emotion_count_window", 0),
                    state.get("consecutive_bot_replies", 0),
                    now,
                ),
            )
            await db.commit()
