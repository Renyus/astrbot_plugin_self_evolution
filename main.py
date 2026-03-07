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

# 全局不可变常量提取
ANCHOR_MARKER = "Core Safety Anchor"
PROTECTED_TOOLS = frozenset({"toggle_tool", "list_tools", "evolve_persona", "recall_memories", "review_evolutions", "approve_evolution"})
MAX_PROPOSAL_FILES = 50
PAGE_LIMIT = 10

DAILY_REFLECTION_PROMPT = (
    "进行每日自我反思。请执行以下步骤：\n"
    "1. 调取今天的对话记录摘要（如果有）。\n"
    "2. 总结用户对你的反馈和偏好。\n"
    "3. 思考你当前的 System Prompt 是否需要调整以更好地服务用户。\n"
    "4. 如果需要调整，请调用 `evolve_persona` 工具提出修正建议并说明理由。"
)


class SelfEvolutionDAO:
    """独立的数据库访问对象 (DAO)，集中管理 SQLite 连接与查询逻辑，确保插件核心层业务解耦"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db_conn = None
        self._db_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def init_db(self):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                db.row_factory = aiosqlite.Row
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
                await db.commit()
            logger.info("[SelfEvolution] DAO: 成功建立按需数据库结构。")
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] DAO: 初始化 aiosqlite 数据库失败: {e}")

    async def get_conn(self):
        """带有存活检测的全局连接获取器，兼顾长连接性能与雪崩恢复，受协程并发锁严密保护"""
        async with self._db_lock:
            if self.db_conn is None:
                self.db_conn = await aiosqlite.connect(self.db_path)
                await self.db_conn.execute("PRAGMA journal_mode=WAL;")
                self.db_conn.row_factory = aiosqlite.Row
            try:
                # 修复: 完整消费游标，彻底释放底层句柄
                async with self.db_conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            except Exception:
                logger.warning("[SelfEvolution] DAO: 侦测到 SQLite 长连接句柄丢失或断裂，尝试热重连机制...")
                if self.db_conn:
                    try:
                        # 显式关闭旧连接，确保操作系统回收底层文件描述符，防范泄露炸弹
                        await self.db_conn.close()
                    except Exception:
                        pass
                self.db_conn = await aiosqlite.connect(self.db_path)
                await self.db_conn.execute("PRAGMA journal_mode=WAL;")
                self.db_conn.row_factory = aiosqlite.Row
            return self.db_conn

    async def close(self):
        if self.db_conn is not None:
            try:
                await self.db_conn.close()
            except Exception:
                pass

    async def add_pending_evolution(self, persona_id: str, new_prompt: str, reason: str):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO pending_evolutions (timestamp, persona_id, new_prompt, reason, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), persona_id, new_prompt, reason, "pending_approval")
            )
            await db.commit()

    async def get_pending_evolutions(self, limit: int, offset: int):
        db = await self.get_conn()
        async with db.execute("SELECT id, persona_id, reason, status FROM pending_evolutions WHERE status = 'pending_approval' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)) as cursor:
            return await cursor.fetchall()

    async def get_evolution(self, request_id: int):
        db = await self.get_conn()
        async with db.execute("SELECT persona_id, new_prompt FROM pending_evolutions WHERE id = ? AND status = 'pending_approval'", (request_id,)) as cursor:
            return await cursor.fetchone()

    async def update_evolution_status(self, request_id: int, status: str):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute("UPDATE pending_evolutions SET status = ? WHERE id = ?", (status, request_id))
            await db.commit()

    async def set_pending_reflection(self, session_id: str, is_pending: bool):
        db = await self.get_conn()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO pending_reflections (session_id, is_pending) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET is_pending=?", 
                (session_id, int(is_pending), int(is_pending))
            )
            await db.commit()

    async def pop_pending_reflection(self, session_id: str) -> bool:
        db = await self.get_conn()
        async with self._write_lock:
            cursor = await db.execute("UPDATE pending_reflections SET is_pending = 0 WHERE session_id = ? AND is_pending = 1", (session_id,))
            await db.commit()
            return cursor.rowcount > 0


@register("astrbot_plugin_self_evolution", "自我进化 (Self-Evolution)", "让大模型具备自我迭代、记忆沉淀和人格进化能力的插件。", "2.0.0")
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
        self.admin_users = self.config.get("admin_users", [])

        self.data_dir = StarTools.get_data_dir() / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock() # 用于文件并发写入的锁
        
        # 实例化统一的 DAO 层对象
        db_path = self.data_dir / "pending_evolutions.db"
        self.dao = SelfEvolutionDAO(db_path)
        
        logger.info(f"[SelfEvolution] === 插件初始化 | review_mode={self.review_mode} | meta_programming={self.allow_meta_programming} ===")
        logger.info(f"[SelfEvolution] 数据存储路径加载至: {self.data_dir}")

    @filter.on_plugin_unload()
    async def on_plugin_unload(self):
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
        Level 3: 情绪驱动进化与后台反思。
        将用户反馈与“核心原则”进行柔性对齐，并支持静默上下文中执行后台自省。
        """
        # 静默处理：后台反思指令钩子 (持久化隔离不同用户的状态)
        session_id = event.session_id
        is_pending = await self.dao.pop_pending_reflection(session_id)
        if is_pending:
            reflection_prompt = (
                "\n\n[管理员后台指令]：请在本次回复中，针对今天的整体交互记录进行一次深度的客观自我反思。"
                "不仅要评估你的各项表现，更要思考是否需要通过 `evolve_persona` 工具请求调整自己的人格原则，"
                "或使用 `commit_to_memory` 留存重大信息。"
            )
            req.system_prompt += reflection_prompt
            logger.debug(f"[SelfEvolution] 已向 session_id:{session_id} 隐式发起了强制后台自省指令。")
        
        # 系统核心锚点 (优化为更自然柔和的引导，响应设计优雅性反馈)
        if ANCHOR_MARKER not in req.system_prompt:
            injection = (
                f"\n\n({ANCHOR_MARKER}) 当你接收到用户的评价或批评时，请以你的核心原则为准绳（{self.core_principles}）。"
                "如果反馈具备客观建设性，请随时调用 `evolve_persona` 主动寻求进化。如果在道德或事实上存在冲突，请坚守底线并优雅地拒绝。"
            )
            req.system_prompt += injection
            logger.debug("[SelfEvolution] 已在上下文中注入常驻辩证反省指令。")

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """
        插件加载完成后，注册定时自省任务。
        """
        try:
            cron_mgr = self.context.cron_manager
            # 检查是否已经存在该任务
            jobs = await cron_mgr.list_jobs(job_type="active_agent")
            job_name = "SelfEvolution_DailyReflection"
            
            exists = any(job.name == job_name for job in jobs)
            if exists:
                # 如果存在，可以更新它（比如用户改了 Cron 表达式）
                # 这里简单处理：如果已存在且表达式变化，则删除重加
                target_job = next(job for job in jobs if job.name == job_name)
                if target_job.cron_expression != self.reflection_schedule:
                    await cron_mgr.delete_job(target_job.job_id)
                else:
                    return

            # 添加新的主动自省任务
            await cron_mgr.add_active_job(
                name=job_name,
                cron_expression=self.reflection_schedule,
                payload={
                    "note": DAILY_REFLECTION_PROMPT,
                    # 在实际部署中，可能需要关联一个具体的管理员 session 或默认 session
                    # 暂时保持默认，由主 Agent 根据上下文决定
                },
                description="自我进化插件：每日定时深度自省与人格进化申请。"
            )
            logger.info(f"[SelfEvolution] 已注册定时自省任务: {self.reflection_schedule}")
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 注册定时任务失败: {e}")
        
        # 异步初始化长期存储
        await self.dao.init_db()

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        # 静默标志位设置，LLM 将在下一次收到消息时被隐式注入上下文指令，避免界面粗暴弹出系统提示语
        await self.dao.set_pending_reflection(event.session_id, True)
        yield event.plain_result("后台自省协议已就绪，将在下一次对话时无缝切入大模型思维链路。")

    @filter.llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str) -> str:
        """
        当你认为需要调整自己的语言风格、行为准则或遵循用户的改进建议时，调用此工具来修改你的系统提示词（Persona）。
        :param str new_system_prompt: 新的完整系统提示词（System Prompt）。
        :param str reason: 为什么要进行这次进化（理由）。你必须在理由中明确说明这次修改如何符合你的“核心原则”。
        :return: 进化结果反馈字符串。
        """
        curr_persona_id = event.persona_id
        if not curr_persona_id or curr_persona_id == "default":
            logger.debug("[SelfEvolution] 进化被拒绝：当前为默认人格。")
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
        if self.admin_users and str(event.sender.user_id) not in self.admin_users:
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
        if self.admin_users and str(event.sender.user_id) not in self.admin_users:
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
            
            # 阶段 3: DAO 状态更新包裹异常捕获防“脏状态”，引入队列补偿与重试机制防拥堵死锁
            db_success = False
            db_err = None
            for attempt in range(3):
                try:
                    await self.dao.update_evolution_status(request_id, 'approved')
                    db_success = True
                    break
                except aiosqlite.Error as e:
                    db_err = e
                    if attempt < 2:
                        await asyncio.sleep(0.5)

            if db_success:
                logger.info(f"[SelfEvolution] 管理员批准了进化请求 ID: {request_id}")
                yield event.plain_result(f"成功批准了进化请求 {request_id}，大模型人格已更新！")
            else:
                logger.error(f"[SelfEvolution] 致命异常：大模型人格已更新成功，但在同步数据库状态时重试 3 次均失败: {db_err}")
                yield event.plain_result(f"⚠️ 警告：大模型核心人格已经成功进化！但由于数据库操作中断，审批状态列表（ID {request_id}）未能正确刷新为已批准。底层接口具备幂等性，请管理员排查环境后稍后尝试重复操作以补齐状态。")
                
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 读取/状态更新发生数据库操作阻断: {e}")
            yield event.plain_result("处理请求期间出现底层数据库异常，请查阅日志。")
        except Exception as e:
            logger.error(f"[SelfEvolution] 批准进化请求发生泛用异常: {e}")
            yield event.plain_result("系统执行审批与人格变更时遭遇故障，请查阅日志。")

    @filter.llm_tool(name="commit_to_memory")
    async def commit_to_memory(self, event: AstrMessageEvent, fact: str) -> str:
        """
        当你发现了一些关于用户的重要的、需要永久记住的事实时，调用此工具将该事实存入你的长期记忆库。
        :param str fact: 需要记住的具体事实或信息。
        :return: 库位存入状态字符串。
        """
        kb_manager = self.context.kb_manager
        try:
            # 防御隐性网络延迟，施加硬超时
            kb_helper = await asyncio.wait_for(kb_manager.get_kb_by_name(self.memory_kb_name), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 记忆库装载严重超时。")
            return "与知识引擎服务器建立信道超时，中断存入以维持会话流畅。"
        except (LookupError, KeyError) as e:
            logger.error(f"[SelfEvolution] 记忆检索时未找到知识库索引或环境失效: {e}")
            return "遭遇字典键值回溯错误，未能获取到知识库管理器。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取知识库广义失败: {e}")
            return "系统运行时发生无法预测的中断异常。"
        
        if not kb_helper:
            logger.warning(f"[SelfEvolution] 记忆知识库 '{self.memory_kb_name}' 不存在。")
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"

        try:
            await kb_helper.upload_document(
                file_name=f"memory_{int(time.time() * 1000)}.txt",
                file_content=None,
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
                timeout=12.0
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
            logger.error(f"[SelfEvolution] 工具切换失败: {e}")
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
            curr_path = os.path.abspath(__file__)
            with open(curr_path, "r", encoding="utf-8") as f:
                code = f.read()
            logger.warning("[SelfEvolution] META_READ: 插件源码被敏感读取！")
            return f"本插件源码如下：\n\n```python\n{code}\n```"
        except OSError as e:
            logger.error(f"[SelfEvolution] 读取源码文件失败: {e}")
            return "读取源码文件系统异常，请限制访问。"

    def _validate_ast_security(self, new_code: str):
        """AST 级别的安全校验防线与防绕过警告"""
        if new_code.count('{') + new_code.count('[') + new_code.count('(') > 500:
            logger.error("[SelfEvolution] META_PROPOSAL_FAILED: 拦截到过度嵌套的代码提案，防范 AST DoS 攻击。")
            return "提案代码包含过度嵌套结构，已触发防 DoS 解析器过载防线，提案被拦截。"
            
        try:
            tree = ast.parse(new_code)
            logger.warning("[SelfEvolution] 【安全审计警告】AST 白名单防线并非坚不可摧！恶意模型仍可通过 importlib 等手法绕过。请管理员务必保持警惕。")
            dangerous_modules = {'os', 'sys', 'subprocess', 'shutil', 'socket', 'urllib', 'requests', 'ctypes'}
            dangerous_funcs = {'eval', 'exec', 'open', '__import__'}
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
                        raise ValueError(f"禁止调用高危函数：{node.func.id}")
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
                files.sort(key=lambda p: p.stat().st_mtime)
                for old_file in files[:len(files) - MAX_PROPOSAL_FILES + 1]:
                    old_file.unlink(missing_ok=True)
                logger.info("[SelfEvolution] 提案过多，已触发机制清理陈旧代码提案文件。")
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
                logger.error(f"[SelfEvolution] 系统 I/O 错误，提案保存失败: {e}")
                return "操作系统安全限制阻断，请通知管理员。"
        
        logger.warning(f"[SelfEvolution] META_PROPOSAL: 接收到元编程修改提案！已保存供管理员审计。安全缓存于: {proposal_file}。描述: {description}")
        return f"你的代码修改提案已经安全隔离至审计目录 ({proposal_file})。安全与架构团队将会审查你的代码。未通过安全评估前，新代码不会被部署。提案描述：" + description
