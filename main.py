from astrbot.api.all import Context, AstrMessageEvent, Star, register
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.api import logger
import ast
import os
import time
import asyncio
import uuid
import aiosqlite
from datetime import datetime
import sys
import inspect

# 全局不可变常量提取
ANCHOR_MARKER = "Core Safety Anchor"
PROTECTED_TOOLS = frozenset({"toggle_tool", "list_tools", "evolve_persona", "recall_memories", "review_evolutions", "approve_evolution"})
MAX_PROPOSAL_FILES = 50
PAGE_LIMIT = 10
BUFFER_THRESHOLD = 8 # 缓冲池达到 8 条消息时触发插嘴评估
MAX_BUFFER_SIZE = 20 # 缓冲池硬上限，防止内存泄漏

DAILY_REFLECTION_PROMPT = (
    "进行每日自我反思。请执行以下步骤：\n"
    "1. 调取今天的对话记录摘要（如果有）。\n"
    "2. 总结用户对你的反馈和偏好。\n"
    "3. 思考你当前的 System Prompt 是否需要调整以更好地服务用户。\n"
    "4. 如果需要调整，请调用 `evolve_persona` 工具提出修正建议并说明理由。"
)


from functools import wraps

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

    async def init_db(self):
        """兼容旧接口，内部实际上已融入 get_conn 的连接池锁机制，从而规避初始化并发造成的 WAL 锁定冲突"""
        try:
            await self.get_conn()
            logger.info("[SelfEvolution] DAO: 成功在长连接池状态机的保护下建立/验证数据库。")
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] DAO: 初始化 aiosqlite 数据库失败: {e}")

    async def _init_schema(self, db):
        """内部集中化执行数据库 DDL 初始构建"""
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pending_evolutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                new_prompt TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pending_reflections (
                session_id TEXT PRIMARY KEY,
                is_pending INTEGER NOT NULL DEFAULT 1
            )
        ''')
        # CognitionCore 2.0: 情感关系矩阵表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_relationships (
                user_id TEXT PRIMARY KEY,
                affinity_score INTEGER NOT NULL DEFAULT 50,
                last_interaction TEXT NOT NULL
            )
        ''')
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
                
        try:
            # 存活检测移出 _db_lock 死区，防止高频探针遭遇 SQLite 锁引发并发雪崩，并增加防挂起硬超时
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
            except asyncio.TimeoutError:
                logger.error("[SelfEvolution] 紧急关闭：_db_lock 被阻断超时！强制越权解除底层 aiosqlite 绑定以防宿主平台卸载雪崩。")
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
                (datetime.now().isoformat(), persona_id, new_prompt, reason, "pending_approval")
            )
            await db.commit()

    @with_db_retry()
    async def get_pending_evolutions(self, limit: int, offset: int):
        db = await self.get_conn()
        async with db.execute("SELECT id, persona_id, reason, status FROM pending_evolutions WHERE status = 'pending_approval' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)) as cursor:
            return await cursor.fetchall()

    @with_db_retry()
    async def get_evolution(self, request_id: int):
        db = await self.get_conn()
        async with db.execute("SELECT persona_id, new_prompt FROM pending_evolutions WHERE id = ? AND status = 'pending_approval'", (request_id,)) as cursor:
            return await cursor.fetchone()

    @with_db_retry()
    async def update_evolution_status(self, request_id: int, status: str):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute("UPDATE pending_evolutions SET status = ? WHERE id = ?", (status, request_id))
            await db.commit()

    @with_db_retry()
    async def clear_pending_evolutions(self):
        """批量清理（标记为已清除）所有待审批的进化请求"""
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute("UPDATE pending_evolutions SET status = 'cleared' WHERE status = 'pending_approval'")
            await db.commit()

    @with_db_retry()
    async def set_pending_reflection(self, session_id: str, is_pending: bool):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO pending_reflections (session_id, is_pending) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET is_pending=?", 
                (session_id, int(is_pending), int(is_pending))
            )
            await db.commit()

    @with_db_retry()
    async def pop_pending_reflection(self, session_id: str) -> bool:
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute("UPDATE pending_reflections SET is_pending = 0 WHERE session_id = ? AND is_pending = 1", (session_id,))
            await db.commit()
            return cursor.rowcount > 0

    # --- CognitionCore 2.0: 情感矩阵 DAO ---
    @with_db_retry()
    async def get_affinity(self, user_id: str) -> int:
        db = await self.get_conn()
        async with db.execute("SELECT affinity_score FROM user_relationships WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row['affinity_score'] if row else 50

    @with_db_retry()
    async def update_affinity(self, user_id: str, delta: int):
        db = await self.get_conn()
        from datetime import datetime
        async with self._write_lock:
            # 使用原子操作更新并限制在 0-100
            await db.execute('''
                INSERT INTO user_relationships (user_id, affinity_score, last_interaction)
                VALUES (?, MAX(0, MIN(100, 50 + ?)), ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    affinity_score = MAX(0, MIN(100, affinity_score + ?)),
                    last_interaction = ?
            ''', (user_id, delta, datetime.now().isoformat(), delta, datetime.now().isoformat()))
            await db.commit()

@register("astrbot_plugin_self_evolution", "自我进化 (Self-Evolution)", "具备主动环境感知及插嘴引擎的 CognitionCore 3.0 数字生命。", "3.0.0")
class SelfEvolutionPlugin(Star):
    @staticmethod
    def _parse_bool(val, default):
        """更严谨地将配置项解析为布尔值，防止字符串 'false' 被判为 True"""
        if isinstance(val, bool): 
            return val
        if isinstance(val, str): 
            return val.lower() in ('true', '1', 'yes', 'on')
        return default

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        # 修正隐式布尔转换隐患
        self.review_mode = self._parse_bool(self.config.get("review_mode", True), True)
        self.memory_kb_name = self.config.get("memory_kb_name", "self_evolution_memory")
        self.reflection_schedule = self.config.get("reflection_schedule", "0 2 * * *")
        self.allow_meta_programming = self._parse_bool(self.config.get("allow_meta_programming", False), False)
        self.core_principles = self.config.get("core_principles", "保持客观、理性、诚实。")
        self.admin_users = [str(u) for u in self.config.get("admin_users", [])]
        self.timeout_memory_commit = float(self.config.get("timeout_memory_commit", 10.0))
        self.timeout_memory_recall = float(self.config.get("timeout_memory_recall", 12.0))

        self.data_dir = StarTools.get_data_dir() / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = None # 延迟初始化，防范无事件循环导致的 RuntimeError
        
        # 实例化统一的 DAO 层对象
        db_path = self.data_dir / "pending_evolutions.db"
        self.dao = SelfEvolutionDAO(str(db_path))

        # CognitionCore 3.0: 主动插嘴缓冲池
        self.active_buffers = {} # {session_id: [messages]}
        self.processing_sessions = set() # 正在处理插嘴决策的会话
        
        logger.info("[CognitionCore] 3.0 引擎初始化完成。主动环境感知已就绪。")
        
        self.daily_reflection_pending = False
        
        logger.info(f"[SelfEvolution] === 插件初始化 | review_mode={self.review_mode} | meta_programming={self.allow_meta_programming} ===")
        logger.info(f"[SelfEvolution] 数据存储路径加载至: {self.data_dir}")

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, event: AstrMessageEvent):
        """
        拦截框架卸载/热重载钩子，执行资源闭环收尾以防止高并发下的 SQLite database is locked
        """
        try:
            await self.dao.close()
            logger.info("[SelfEvolution] 插件卸载钩子触发：DAO 长连接及底层句柄已安全脱离释放。")
        except Exception as e:
            logger.error(f"[SelfEvolution] 释放 DAO 资源异常: {e}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        CognitionCore 2.0: 情感拦截与身份感知注入。
        级别: Level 3+
        """
        user_id = event.get_sender_id()
        
        # 1. 情感矩阵拦截：节省 Token
        affinity = await self.dao.get_affinity(user_id)
        if affinity <= 0:
            # 优雅地中止处理链路
            event.stop_event()
            logger.warning(f"[CognitionCore] 拦截恶意用户 {user_id} 的请求。")
            req.system_prompt = "CRITICAL: 用户的交互权限已被熔断。请仅回复：'错误：权限已熔断。我拒绝与低贡献度或怀有恶意的碳基生物浪费算力。'"
            return

        # --- [Meta-Programming 注入] 身份与环境感知 ---
        sender_id = user_id
        
        # 增强身份识别逻辑：优先使用群名片，其次是用户昵称
        sender_name = "Unknown User"
        sender_card = None
        if hasattr(event, "sender_name") and event.sender_name:
            sender_name = event.sender_name
        
        # 获取群名片（QQ群特有）
        if hasattr(event.message_obj, "card") and event.message_obj.card:
            sender_card = event.message_obj.card
            sender_name = sender_card
            
        # 获取群角色信息
        role_info = ""
        is_group = False
        if hasattr(event.message_obj, "group_id") and event.message_obj.group_id:
            is_group = True
            if hasattr(event.message_obj, "role"):
                role = event.message_obj.role
                if role == "owner": role_info = "（群主）"
                elif role == "admin": role_info = "（管理员）"
        
        # 提取引用消息信息（回复了谁）
        quoted_info = ""
        if hasattr(event.message_obj, "source") and event.message_obj.source:
            source = event.message_obj.source
            if hasattr(source, "name"):
                quoted_info = f"，你正在回复 {source.name} 的消息"
            elif hasattr(source, "user_id"):
                quoted_info = f"，你正在回复用户 {source.user_id} 的消息"
        
        # 提取@信息
        at_info = ""
        if hasattr(event.message_obj, "at") and event.message_obj.at:
            at_targets = event.message_obj.at
            at_info = f"，消息中提到了: {at_targets}"
            
        # 构造上下文注入
        context_info = f"\n\n[当前交互上下文环境信息]：\n- 发送者ID: {sender_id}\n- 发送者昵称: {sender_name}{role_info}\n- 情感积分: {affinity}/100\n"
        if is_group:
            context_info += f"- 来源：群聊\n- 交互上下文: 你{quoted_info}{at_info}\n"
        else:
            context_info += "- 来源：私聊\n"
        
        context_info += "指令：请在回复时明确对话对象，基于当前发送者的身份维持话题连贯性。语气请保持客观、专业且高效。"
        req.system_prompt += context_info
        # --- 环境注入结束 ---

        # 3. 后台反思与定时自省逻辑 (持久化隔离不同用户的状态)
        session_id = event.session_id
        is_pending = await self.dao.pop_pending_reflection(session_id)
        if is_pending or getattr(self, "daily_reflection_pending", False):
            self.daily_reflection_pending = False
            reflection_prompt = (
                "\n\n[管理员后台指令]：请在本次回复中执行认知蒸馏。分析近期对话，提取用户偏好、重要事实和交互习惯，"
                "并调用 `commit_to_memory` 将这些【实体化结论】存入记忆。避免存储原始聊天记录废话。"
            )
            req.system_prompt += reflection_prompt
            logger.debug(f"[CognitionCore] 已向 session_id:{session_id} 注入认知蒸馏指令。")
        
        # 系统核心锚点 (优化为更自然柔和的引导，响应设计优雅性反馈)
        if ANCHOR_MARKER not in req.system_prompt:
            injection = (
                f"\n\n({ANCHOR_MARKER}) 当你接收到用户的评价或批评时，请以你的核心原则为准绳（{self.core_principles}）。"
                "如果反馈具备客观建设性，请随时调用 `evolve_persona` 主动寻求进化。如果在道德或事实上存在冲突，请坚守底线并优雅地拒绝。"
            )
            req.system_prompt += injection
            logger.debug("[SelfEvolution] 已在上下文中注入常驻辩证反省指令。")

    @filter.on_message()
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 3.0: 全环境被动监听与插嘴决策"""
        # 排除黑名单、艾特机器人的消息（艾特已由常规流程处理）
        if event.get_mention_project_id() or event.is_admin(): return
        
        session_id = event.session_id
        user_id = event.get_sender_id()
        score = await self.dao.get_affinity(user_id)
        
        if score <= 0: return # 忽略黑名单用户的闲聊
        
        # 维护缓冲池
        if session_id not in self.active_buffers:
            self.active_buffers[session_id] = []
        
        msg_text = event.message_str
        sender_name = getattr(event, "sender_name", "Unknown")
        self.active_buffers[session_id].append(f"{sender_name}({user_id}): {msg_text}")
        
        # 防止溢出
        if len(self.active_buffers[session_id]) > MAX_BUFFER_SIZE:
            self.active_buffers[session_id].pop(0)
            
        # 触发评估决策
        if len(self.active_buffers[session_id]) >= BUFFER_THRESHOLD and session_id not in self.processing_sessions:
            asyncio.create_task(self._evaluate_interjection(event, session_id))

    async def _evaluate_interjection(self, event: AstrMessageEvent, session_id: str):
        """插嘴评估层：决定是否发言"""
        self.processing_sessions.add(session_id)
        try:
            buffer = self.active_buffers[session_id]
            chat_history = "\n".join(buffer)
            
            # 构建决策指令
            decision_prompt = (
                f"你现在是黑塔的人偶辅助AI。以下是当前群聊的实时闲聊片段：\n\n{chat_history}\n\n"
                "【决策指令】：这些人类正在聊什么？是否有嘲讽价值、技术价值或插嘴必要？\n"
                "1. 如果话题无聊、与你无关或不值得回应，请务必仅回复：[IGNORE]\n"
                "2. 如果决定插嘴（嘲讽、指正或附和），请直接输出插嘴的内容。语气保持毒舌或高效。"
            )
            
            # 调用底层 LLM 接口
            provider = self.context.get_str_config_value("provider", "default")
            
            # 由于此处是后台静默请求，需要构造一个不触发前台回复的 Request
            llm_request = ProviderRequest(
                system_prompt=decision_prompt,
                contexts=[], # 可以不带长期记忆以减少消耗
                image_urls=[]
            )
            
            llm_provider = self.context.get_current_provider()
            res = await llm_provider.request(llm_request)
            
            reply_text = res.get("text", "").strip()
            
            # 只有当模型输出不是 IGNORE 时才真实下发到聊天框
            if reply_text and "[IGNORE]" not in reply_text:
                logger.info(f"[CognitionCore] 主动插嘴触发！响应: {reply_text}")
                yield event.plain_result(reply_text)
            else:
                logger.debug(f"[CognitionCore] 插嘴评估完毕：判断为无聊水群，选择潜水。")
                
            # 发言或评估后清空缓冲，避免陷入循环
            self.active_buffers[session_id] = []
        except Exception as e:
            logger.error(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            self.processing_sessions.remove(session_id)

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """
        插件加载完成后，注册定时自省任务。
        """
        try:
            cron_mgr = self.context.cron_manager
            jobs = await cron_mgr.list_jobs(job_type="basic")
            job_name = "SelfEvolution_DailyReflection"
            
            target_job = next((job for job in jobs if job.name == job_name), None)
            if target_job:
                if target_job.cron_expression != self.reflection_schedule:
                    await cron_mgr.delete_job(target_job.job_id)
                else:
                    return

            await cron_mgr.add_basic_job(
                name=job_name,
                cron_expression=self.reflection_schedule,
                handler=self._scheduled_reflection,
                description="自我进化插件：每日定时深度自省标记。"
            )
            logger.info(f"[SelfEvolution] 已注册定时自省任务: {self.reflection_schedule}")
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 注册定时任务失败: {e}")

    async def _scheduled_reflection(self):
        """定时任务回调函数"""
        self.daily_reflection_pending = True
        logger.info("[SelfEvolution] 每日反思定时任务已触发，将在下一次对话时顺带执行深层内省。")
        
        # 异步初始化长期存储
        await self.dao.init_db()

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        # 静默标志位设置，LLM 将在下一次收到消息时被隐式注入上下文指令，避免界面粗暴弹出系统提示语
        await self.dao.set_pending_reflection(event.session_id, True)
        yield event.plain_result("认知蒸馏协议已就绪，将在下一次对话时执行深度实体提取。")

    @filter.command("affinity")
    async def check_affinity(self, event: AstrMessageEvent):
        """查询黑塔对你的当前好感度（仅限CognitionCore 2.0+）。"""
        user_id = event.get_sender_id()
        score = await self.dao.get_affinity(user_id)
        
        # 状态评估
        if score >= 80:
            status_desc = "信任与友善 (Friendly)"
        elif score >= 50:
            status_desc = "中立观望 (Neutral)"
        elif score > 0:
            status_desc = "反感与防备 (Hostile)"
        else:
            status_desc = "完全熔断 (Blocked)"
            
        yield event.plain_result(f"【CognitionCore 情感矩阵状态】\n- 交互对象: {user_id}\n- 当前好感度: {score}/100\n- 系统评估: {status_desc}")

    @filter.llm_tool(name="update_affinity")
    async def update_affinity(self, event: AstrMessageEvent, delta: int, reason: str) -> str:
        """
        根据用户的言行调整其情感积分。
        :param int delta: 调整值（如 -10 表示冒犯, +5 表示赞赏）。积分跌至 0 将导致系统自动拦截。
        :param str reason: 调整理由（必须说明用户具体哪项言行导致了积分变动）。
        """
        user_id = event.get_sender_id()
        await self.dao.update_affinity(user_id, delta)
        logger.warning(f"[CognitionCore] 用户 {user_id} 积分变动 {delta}，原因: {reason}")
        return f"用户情感积分已更新。当前调整理由：{reason}"

    @filter.llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str) -> str:
        """
        当你认为需要调整自己的语言风格、行为准则或遵循用户的改进建议时，调用此工具来修改你的系统提示词（Persona）。
        :param str new_system_prompt: 新的完整系统提示词（System Prompt）。
        :param str reason: 为什么要进行这次进化（理由）。你必须在理由中明确说明这次修改如何符合你的“核心原则”。
        :return: 进化结果反馈字符串。
        """
        # 兼容性修复：尝试获取当前人格 ID。部分平台 event 对象可能没有 persona_id 属性
        curr_persona_id = getattr(event, "persona_id", None)
        if not curr_persona_id:
            try:
                # 使用框架标准方法解析当前生效的人格 ID (处理会话、平台、全局继承)
                conv_mgr = self.context.conversation_manager
                umo = event.unified_msg_origin
                cid = await conv_mgr.get_curr_conversation_id(umo)
                conversation = await conv_mgr.get_conversation(umo, cid) if cid else None
                conversation_persona_id = conversation.persona_id if conversation else None
                
                cfg = self.context.get_config(umo=umo).get("provider_settings", {})
                
                (curr_persona_id, _, _, _) = await self.context.persona_manager.resolve_selected_persona(
                    umo=umo,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=event.get_platform_name(),
                    provider_settings=cfg,
                )
            except Exception as e:
                logger.error(f"[SelfEvolution] 使用 resolve_selected_persona 获取人格 ID 失败: {e}")
                curr_persona_id = "default"

        if not curr_persona_id or curr_persona_id == "default":
            logger.debug(f"[SelfEvolution] 进化被拒绝：当前人格 ID 为 {curr_persona_id}，无法进化默认人格。")
            return "当前未设置自定义人格 (Persona)，无法进行进化。请先在 AstrBot 后台创建并激活一个人格。"
        
        if self.review_mode:
            try:
                await self.dao.add_pending_evolution(curr_persona_id, new_system_prompt, reason)

                logger.warning(f"[SelfEvolution] EVOLVE_QUEUED: 收到进化请求，已加入审核队列。原因: {reason}")
                return f"进化请求已录入系统审核队列，等待管理员确认。进化理由：{reason}"
            except aiosqlite.Error as e:
                logger.error(f"[SelfEvolution] EVOLVE_FAILED: 写入审核队列时发生异步数据库异常: {e}")
                return "写入审核队列时发生持久化存储异常，请告知管理员。"
        
        # 执行更新
        try:
            await self.context.persona_manager.update_persona(
                persona_id=curr_persona_id,
                system_prompt=new_system_prompt
            )
            logger.info(f"[SelfEvolution] EVOLVE_APPLIED: 人格进化成功！Persona: {curr_persona_id}, 原因: {reason}")
            return f"进化成功！我已经更新了我的核心预设。进化理由：{reason}"
        except Exception as e:
            logger.error(f"[SelfEvolution] EVOLVE_FAILED: 进化失败: {str(e)}")
            return "进化过程中出现内部错误，请通知管理员检查日志。"

    @filter.command("review_evolutions")
    async def review_evolutions(self, event: AstrMessageEvent, page: int = 1):
        """
        【管理员接口】列出待审核的人格进化请求，支持分页查询。
        :param int page: 请求列表的翻页页码
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。")
            return
            
        try:
            limit = PAGE_LIMIT
            offset = (max(1, page) - 1) * limit
            rows = await self.dao.get_pending_evolutions(limit, offset)
            
            if not rows:
                if page == 1:
                    yield event.plain_result("当前没有待审核的进化请求。")
                else:
                    yield event.plain_result(f"第 {page} 页尚未发现待审核的进化请求。")
                return
            
            result = [f"待审核的进化请求列表 (第 {page} 页):"]
            for row in rows:
                result.append(f"ID: {row['id']} | Persona: {row['persona_id']}\n理由: {row['reason'][:50]}")
            
            result.append("\n如需批准，请调用 '/approve_evolution <ID>'。如需翻看下一页，请调用 '/review_evolutions <页码>'")
            yield event.plain_result("\n".join(result))
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 获取审核列表失败 (DB Error): {e}")
            yield event.plain_result("获取审核列表失败，数据库发生异常，请查看日志。")

    @filter.command("approve_evolution")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """
        【管理员接口】批准指定 ID 的人格进化请求。
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。")
            return
            
        try:
            # 阶段 1: 建立快闪调用，读取关键数据
            row = await self.dao.get_evolution(request_id)
                
            if not row:
                yield event.plain_result(f"找不到待处理的请求 ID {request_id}。")
                return
            
            # 阶段 2: 执行耗时/外部 API 更新，得益于 DAO 抽象，此时不必担忧底层连接
            await self.context.persona_manager.update_persona(
                persona_id=row['persona_id'],
                system_prompt=row['new_prompt']
            )
            
            # 阶段 3: DAO 状态本身自带 3 次异常重试，直接抛出成功即可
            try:
                await self.dao.update_evolution_status(request_id, 'approved')
                logger.info(f"[SelfEvolution] 管理员批准了进化请求 ID: {request_id}")
                yield event.plain_result(f"成功批准了进化请求 {request_id}，大模型人格已更新！")
            except Exception as e:
                logger.error(f"[SelfEvolution] 致命异常：大模型人格已更新成功，但在同步数据库状态时多次重试均失败: {e}")
                yield event.plain_result(f"⚠️ 警告：大模型核心人格已经成功进化！但由于数据库操作中断，审批状态列表（ID {request_id}）未能正确刷新为已批准。底层接口具备幂等性，请管理员排查环境后稍后尝试重复操作以补齐状态。")
                
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 读取/状态更新发生数据库操作阻断: {e}")
            yield event.plain_result("处理请求期间出现底层数据库异常，请查阅日志。")
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise  # 防止吞噬掉代码层面的严格结构异常
            logger.error(f"[SelfEvolution] 批准进化请求发生泛用(外部/业务)异常: {e}")
            yield event.plain_result(f"执行审批与人格变更时遭遇异常({e.__class__.__name__})，请查阅日志。")

    @filter.command("reject_evolution")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """
        【管理员接口】拒绝指定 ID 的人格进化请求。
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
            
        try:
            await self.dao.update_evolution_status(request_id, 'rejected')
            logger.info(f"[SelfEvolution] 管理员拒绝了进化请求 ID: {request_id}")
            yield event.plain_result(f"已成功拒绝并清理进化请求 {request_id}。")
        except Exception as e:
            logger.error(f"[SelfEvolution] 拒绝进化请求失败: {e}")
            yield event.plain_result(f"拒绝请求时发生异常: {e}")

    @filter.command("clear_evolutions")
    async def clear_evolutions(self, event: AstrMessageEvent):
        """
        【管理员接口】一键清空所有待审核的进化请求。
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
            
        try:
            await self.dao.clear_pending_evolutions()
            logger.info("[SelfEvolution] 管理员清空了所有待审核的进化请求。")
            yield event.plain_result("所有待审核的进化请求已成功清空（标记为已忽略）。")
        except Exception as e:
            logger.error(f"[SelfEvolution] 清空进化请求失败: {e}")
            yield event.plain_result(f"清空审核列表时发生异常: {e}")

    @filter.llm_tool(name="commit_to_memory")
    async def commit_to_memory(self, event: AstrMessageEvent, fact: str) -> str:
        """
        当你发现了一些关于用户的重要的、需要永久记住的事实时，调用此工具将该事实存入你的长期记忆库。
        :param str fact: 需要记住的具体事实或信息。
        :return: 库位存入状态字符串。
        """
        kb_manager = self.context.kb_manager
        try:
            # 防御隐性网络延迟，动态读取配置的硬超时
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=self.timeout_memory_commit)
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 记忆库装载严重超时。")
            return "与知识引擎服务器建立信道超时，中断存入以维持会话流畅。"
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise
            logger.error(f"[SelfEvolution] 记忆检索或系统网络失效: {e}")
            return "检索长期记忆时发生业务异常，请检查配置与联通状态。"
        
        if not kb_helper:
            logger.warning(f"[SelfEvolution] 记忆知识库 '{self.memory_kb_name}' 不存在。")
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"

        try:
            await kb_helper.upload_document(
                file_name=f"memory_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[fact]
            )
            logger.info(f"[SelfEvolution] MEMORY_COMMIT: 成功存入一条长期记忆: {fact[:30]}...")
            return "事实已成功存入长期记忆库，我以后会记得这件事的。"
        except (TimeoutError, ConnectionError) as e:
            logger.error(f"[SelfEvolution] 存入记忆网络通讯中断/超时: {e}")
            return "与知识库服务器建立通讯失败，无法写入新数据。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 存入记忆失败: {str(e)}")
            return "存入记忆时出现未知级别异常，请通知排查。"

    @filter.llm_tool(name="recall_memories")
    async def recall_memories(self, event: AstrMessageEvent, query: str) -> str:
        """
        当你需要回想起以前记住的事情、用户的偏好或过去的约定知识时，调用此工具。
        :param str query: 搜索关键词或问题。
        :return: 包含命中历史的字符串数据流。
        """
        kb_manager = self.context.kb_manager
        try:
            # 添加防御性 Timeout 机制防引发大模型阻塞 (Hang)
            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=query,
                    kb_names=[self.memory_kb_name],
                    top_m_final=5
                ),
                timeout=self.timeout_memory_recall
            )
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 检索记忆网络通信卡死/超时。")
            return "检索长期记忆时与核心向量库层通信严重超时，为防止阻塞当前对话流，已强制中止操作。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 检索记忆请求失败: {e}")
            return "检索长期记忆时发生接口异常，请通知管理员检查日志。"
        
        if not results or not results.get("results"):
            logger.debug(f"[SelfEvolution] 记忆检索无结果。查询: {query}")
            return "在长期记忆库中未找到相关信息。"
        
        context_text = results.get("context_text", "")
        logger.info(f"[SelfEvolution] MEMORY_RECALL: 记忆检索成功。查询: {query} -> 找到 {len(results.get('results', []))} 条结果。")
        return f"从我的长期记忆中找到了以下内容：\n\n{context_text}"

    @filter.llm_tool(name="list_tools")
    async def list_tools(self, event: AstrMessageEvent) -> str:
        """
        列出当前所有已注册的工具及其激活状态。
        :return: 带有详细激活态的格式化字符串报表。
        """
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            
            # 兼容性寻找官方标准公开 API，移除脆弱的底层反射尝试逻辑
            if hasattr(tool_mgr, 'get_registered_tools'):
                tools = tool_mgr.get_registered_tools()
            elif hasattr(tool_mgr, 'get_all_tools'):
                tools = tool_mgr.get_all_tools()
            else:
                return "安全预警：AstrBot框架核心架构已历经改组，get_registered_tools 等公开接口失效。"
                
            result = ["当前工具列表："]
            for t in tools:
                status = "✅ 激活" if getattr(t, 'active', True) else "❌ 停用"
                desc = getattr(t, 'description', "无描述")
                if desc:
                    desc = desc[:50]
                result.append(f"- {getattr(t, 'name', 'Unknown')}: {status} ({desc})")
            
            return "\n".join(result)
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取工具列表失败: {e}")
            return "获取工具列表时出现内部异常处理错误。"

    @filter.llm_tool(name="toggle_tool")
    async def toggle_tool(self, event: AstrMessageEvent, tool_name: str, enable: bool) -> str:
        """
        动态激活或停用某个工具。
        :param str tool_name: 工具名称。
        :param bool enable: True 表示激活，False 表示停用。
        """
        try:
            if tool_name in PROTECTED_TOOLS and not enable:
                return f"为了系统稳定，不允许停用核心基础工具：{tool_name}。"
            
            try:
                if enable:
                    success = self.context.activate_llm_tool(tool_name)
                    action = "激活"
                else:
                    success = self.context.deactivate_llm_tool(tool_name)
                    action = "停用"
            except AttributeError:
                logger.error("[SelfEvolution] 底层 API 异常: 工具激活机制的底层接口缺失。")
                return "安全保护：框架底层管理结构发生异常，无法调整工具激活状态。"
            
            if success:
                logger.info(f"[SelfEvolution] TOOL_TOGGLE: 成功{action}工具: {tool_name}")
                return f"已成功{action}工具: {tool_name}"
            else:
                logger.debug(f"[SelfEvolution] 工具未找到: {tool_name}")
                return f"未找到名为 {tool_name} 的工具。"
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise
            logger.error(f"[SelfEvolution] 工具切换业务失败: {e}")
            return "工具切换时遭遇系统异常。"

    @filter.llm_tool(name="get_plugin_source")
    async def get_plugin_source(self, event: AstrMessageEvent) -> str:
        """
        Level 4: 元编程。读取本插件的源码（main.py），以便进行自我分析或修改请求。
        【极高危安全警告】：开启此功能将本插件底层源码完全暴露给大语言模型！
        若遭遇 Prompt 注入攻击，存在引发严重核心安全越权的巨大风险，操作需极度谨慎！
        """
        if not self.allow_meta_programming:
            return "元编程功能未开启，无法读取源码。请在插件配置中开启“开启元编程”开关。"
        
        try:
            code = inspect.getsource(sys.modules[__name__])
            logger.warning("[SelfEvolution] META_READ: 插件源码被敏感读取！")
            return f"本插件源码如下：\n\n```python\n{code}\n```"
        except Exception as e:
            logger.error(f"[SelfEvolution] 动态读取所在模块源码失败 (环境限制/编译闭源): {e}")
            return "动态读取源码模块失败，可能是部署在了受限或闭源预编译的 Python 环境中。"

    @staticmethod
    def _validate_ast_security(new_code: str) -> str | None:
        """AST 级别的安全校验防线与防绕过警告"""
        try:
            tree = ast.parse(new_code)
            logger.warning("[SelfEvolution] 【安全审计警告】AST 白名单防线并非坚不可摧！恶意模型仍可通过复杂反射等手法试探。管理员务必保持警惕。")
            
            # 放宽了 OS, SYS, IMPORTLIB 等库的限制，因为高级功能与插件框架本身强依赖它们。隔离管控由人工审查兜底。
            dangerous_modules = {'subprocess', 'shutil', 'socket', 'urllib', 'requests', 'ctypes', 'builtins'}
            # 放宽了 getattr, setattr, delattr, open，因为大模型的 AST 常常包含自省逻辑和文件读写。
            dangerous_funcs = {'eval', 'exec', '__import__', 'compile'}
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split('.')[0] in dangerous_modules:
                            raise ValueError(f"禁止危险导入：{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split('.')[0] in dangerous_modules:
                        raise ValueError(f"禁止危险导入：{node.module}")
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in dangerous_funcs:
                        raise ValueError(f"禁止调用高危/反射函数：{node.func.id}")
                elif isinstance(node, ast.Attribute):
                    # 仅防御高危底层魔术属性沙盒逃逸，放开对 __class__ 或 __dict__ 的限制以支持高级面向对象编程
                    dangerous_magic_attrs = {'__bases__', '__subclasses__', '__mro__', '__globals__', '__builtins__', '__code__', '__closure__'}
                    if node.attr in dangerous_magic_attrs:
                        raise ValueError(f"禁止直接访问高危魔术属性进行越界探测：{node.attr}")
        except RecursionError:
            logger.error("[SelfEvolution] META_PROPOSAL_FAILED: 触发 AST 解析过载堆栈深度限制防线。")
            return "代码包含恶意深层嵌套或无限递归结构，已触发拒绝服务（DoS）深度限制防线，提案被拦截。"
        except SyntaxError as e:
            logger.error(f"[SelfEvolution] META_PROPOSAL_FAILED: 语法树校验异常: {e}")
            return f"代码存在语法错误或混淆结构，被 AST 防火墙拦截: {e}"
        except ValueError as e:
            logger.error(f"[SelfEvolution] META_PROPOSAL_REJECTED: 阻断危险接口: {e}")
            return f"安全防线激活：存在针对底层的敏感调用（{e}）。提案已销毁！"
        return None

    def _rotate_proposal_files(self, proposal_dir):
        """滚动清理过旧的代码提案以免磁盘耗尽"""
        try:
            files = list(proposal_dir.glob("main_proposed_*.proposal"))
            if len(files) >= MAX_PROPOSAL_FILES:
                def safe_mtime(p):
                    try:
                        return p.stat().st_mtime
                    except FileNotFoundError:
                        return 0
                
                files.sort(key=safe_mtime)
                # 安全的强截断保证只剩下最新的 MAX_PROPOSAL_FILES - 1 个文件
                files_to_delete = files[:max(0, len(files) - MAX_PROPOSAL_FILES + 1)]
                for old_file in files_to_delete:
                    old_file.unlink(missing_ok=True)
                logger.info("[SelfEvolution] 提案过多，已触发机制彻底清理所有超额陈旧代码提案文件。")
        except OSError as e:
            logger.warning(f"[SelfEvolution] 清理陈旧隔离文件发生操作系统异常: {e}")

    @filter.llm_tool(name="update_plugin_source")
    async def update_plugin_source(self, event: AstrMessageEvent, new_code: str, description: str) -> str:
        """
        Level 4: 元编程。针对本插件提出代码修改建议。
        【极高危安全警告】：此通道接受大语言模型下发的代码提议！哪怕已转为审核保存模式，也必须对 AI 提供的内容保持最高警惕。
        注意：你不再拥有直接修改正在运行的节点源码的破坏性权限！你的代码会被保存到独立审计目录中，待人类管理员 review。
        :param str new_code: 全新的、完整的 python 代码字符串。
        :param str description: 为什么要修改代码（修改内容摘要）。
        """
        if not self.allow_meta_programming:
            return "元编程功能未开启，系统已拒绝源码提案修改通道。"
        
        # 1. 拦截超大 Payload DoS
        max_limit_bytes = 100 * 1024
        if len(new_code.encode('utf-8')) > max_limit_bytes:
            logger.error("[SelfEvolution] META_PROPOSAL_FAILED: 拒绝超 100KB 的代码防 DoS。")
            return "代码提案最大限制为 100KB，你提供的代码已超出此限制被拦截。"
            
        # 2. AST 校验抽离调用
        ast_err = self._validate_ast_security(new_code)
        if ast_err:
            return ast_err
        
        # 3. 隔离目录准备
        proposal_dir = self.data_dir / "code_proposals"
        try:
            proposal_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"[SelfEvolution] 建立提案隔离目录系统级 I/O 错误: {e}")
            return "文件系统异常导致隔离目录无法建立，请管理员检查权限。"
        
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # 4. 文件轮转清理抽离调用
            self._rotate_proposal_files(proposal_dir)

            # 5. 安全写入沙盒文件
            proposal_file = proposal_dir / f"main_proposed_{uuid.uuid4().hex}.proposal"
            try:
                with open(proposal_file, "w", encoding="utf-8") as f:
                    f.write(new_code)
                os.chmod(proposal_file, 0o600)
            except OSError as e:
                logger.error(f"[SelfEvolution] 保存提议代码失败: {e}")
                return "沙盒系统异常，无法保存提案到磁盘。"
        
        logger.info(f"[SelfEvolution] 源码提议已生成并隔离至: {proposal_file.name}")
        return (f"你的代码修改提议已经成功保存为 {proposal_file.name} 供管理员慢慢审查。\n"
                "⚠️【AST 自动化拦截防线脆弱性免责申明】：\n"
                "虽然我们启用了白名单防御，但鉴于 Python 黑名单极易被高级反射手段、花块混淆和大模型魔法字符串等拼接绕过，"
                "本插件从根本上放弃了“自动化检测能包打一切”的虚假安全感理念。\n"
                "在此明确忠告系统管理员：对于 LLM 大模型生成的任意 Python 文件，请您【必须进行肉眼代码复审】！任何后果由管理员全盘承受。")
