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

        self.data_dir = StarTools.get_data_dir() / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock() # 用于文件并发写入的锁
        self._db_lock = asyncio.Lock() # 用于数据库连接池的并发生成锁
        
        # 定义高性能 SQLite 数据库替代方案 (aiosqlite)，避免并发 locked 错误
        self.db_path = self.data_dir / "pending_evolutions.db"
        self.db_conn = None
        # 修复多用户并发导致的精神分裂漏洞：使用 session_id 隔离反省状态
        self.pending_reflections: dict[str, bool] = {}

        logger.info(f"[SelfEvolution] === 插件初始化 | review_mode={self.review_mode} | meta_programming={self.allow_meta_programming} ===")
        logger.info(f"[SelfEvolution] 数据存储路径加载至: {self.data_dir}")
        
    async def _init_db(self):
        """异步初始化建议列表所用的 SQLite 数据库，使用短连接防止重载资源泄露"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
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
                await db.commit()
            logger.info("[SelfEvolution] 成功建立按需数据库结构。")
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 初始化 aiosqlite 数据库失败: {e}")

    async def _get_db_conn(self):
        """带有存活检测的全局连接获取器，兼顾长连接性能与雪崩恢复，受协程并发锁严密保护"""
        async with self._db_lock:
            if self.db_conn is None:
                self.db_conn = await aiosqlite.connect(self.db_path)
                self.db_conn.row_factory = aiosqlite.Row
            try:
                # 保活探测
                await self.db_conn.execute("SELECT 1")
            except Exception:
                logger.warning("[SelfEvolution] 侦测到 SQLite 长连接句柄丢失或断裂，尝试热重连机制...")
                if self.db_conn:
                    try:
                        # 显式关闭旧连接，确保操作系统回收底层文件描述符，防范泄露炸弹
                        await self.db_conn.close()
                    except Exception:
                        pass
                self.db_conn = await aiosqlite.connect(self.db_path)
                self.db_conn.row_factory = aiosqlite.Row
            return self.db_conn

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        Level 3: 情绪驱动进化与后台反思。
        将用户反馈与“核心原则”进行柔性对齐，并支持静默上下文中执行后台自省。
        """
        # 静默处理：后台反思指令钩子 (通过 session_id 隔离不同用户的状态)
        session_id = event.session_id
        if self.pending_reflections.pop(session_id, False):
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
        await self._init_db()

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        # 静默标志位设置，LLM 将在下一次收到消息时被隐式注入上下文指令，避免界面粗暴弹出系统提示语
        self.pending_reflections[event.session_id] = True
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
                # 采用带保活治愈机制的常驻连接，解决短连接锁文件与框架重载造成的双面性系统难题
                db = await self._get_db_conn()
                await db.execute(
                    "INSERT INTO pending_evolutions (timestamp, persona_id, new_prompt, reason, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), curr_persona_id, new_system_prompt, reason, "pending_approval")
                )
                await db.commit()

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
        try:
            limit = PAGE_LIMIT
            offset = (max(1, page) - 1) * limit
            db = await self._get_db_conn()
            async with db.execute("SELECT id, persona_id, reason, status FROM pending_evolutions WHERE status = 'pending_approval' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)) as cursor:
                rows = await cursor.fetchall()
            
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
        try:
            # 阶段 1: 建立快闪调用，读取关键数据
            db = await self._get_db_conn()
            async with db.execute("SELECT persona_id, new_prompt FROM pending_evolutions WHERE id = ? AND status = 'pending_approval'", (request_id,)) as cursor:
                row = await cursor.fetchone()
                
            if not row:
                yield event.plain_result(f"找不到待处理的请求 ID {request_id}。")
                return
            
            # 阶段 2: 执行耗时/外部 API 更新，得益于上文的脱离上下文特性，此时SQLite连接资源已被释放，杜绝串扰
            await self.context.persona_manager.update_persona(
                persona_id=row['persona_id'],
                system_prompt=row['new_prompt']
            )
            
            # 阶段 3: 重新挂载执行快速状态更新
            await db.execute("UPDATE pending_evolutions SET status = 'approved' WHERE id = ?", (request_id,))
            await db.commit()
                
            logger.info(f"[SelfEvolution] 管理员批准了进化请求 ID: {request_id}")
            yield event.plain_result(f"成功批准了进化请求 {request_id}，大模型人格已更新！")
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 批准进化请求发生数据库操作阻断: {e}")
            yield event.plain_result("批准请求期间出现数据库系统异常，请查阅日志。")
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
            
            # 兼容性寻找官方标准公开 API，若无则平滑降级到底层私有 func_list 属性
            if hasattr(tool_mgr, 'get_registered_tools'):
                tools = tool_mgr.get_registered_tools()
            elif hasattr(tool_mgr, 'get_all_tools'):
                tools = tool_mgr.get_all_tools()
            else:
                tools = getattr(tool_mgr, 'func_list', None)
                
            if tools is None:
                logger.error("[SelfEvolution] 底层 API 警告: 工具管理器的内部注册字段 func_list 已失踪被移除。")
                return "安全预警：AstrBot框架核心架构已历经改组，原预期的内置参数消失，受限于降级运行。"
                
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
    async def toggle_tool(self, event: AstrMessageEvent, tool_name: str, enable: bool):
        """
        动态激活或停用某个工具。
        :param str tool_name: 工具名称。
        :param bool enable: True 表示激活，False 表示停用。
        """
        try:
            if tool_name in PROTECTED_TOOLS and not enable:
                return f"为了系统稳定，不允许停用核心基础工具：{tool_name}。"
            
            # API 级别可用性校验
            if not hasattr(self.context, 'activate_llm_tool') or not hasattr(self.context, 'deactivate_llm_tool'):
                logger.error("[SelfEvolution] 底层 API 异常: 工具激活机制的底层接口缺失。")
                return "安全保护：框架底层管理结构发生异常，无法调整工具激活状态。"
            
            if enable:
                success = self.context.activate_llm_tool(tool_name)
                action = "激活"
            else:
                success = self.context.deactivate_llm_tool(tool_name)
                action = "停用"
            
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
    async def get_plugin_source(self, event: AstrMessageEvent):
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

    @filter.llm_tool(name="update_plugin_source")
    async def update_plugin_source(self, event: AstrMessageEvent, new_code: str, description: str):
        """
        Level 4: 元编程。针对本插件提出代码修改建议。
        【极高危安全警告】：此通道接受大语言模型下发的代码提议！哪怕已转为审核保存模式，也必须对 AI 提供的内容保持最高警惕。
        注意：你不再拥有直接修改正在运行的节点源码的破坏性权限！你的代码会被保存到独立审计目录中，待人类管理员 review。
        :param str new_code: 全新的、完整的 python 代码字符串。
        :param str description: 为什么要修改代码（修改内容摘要）。
        """
        if not self.allow_meta_programming:
            return "元编程功能未开启，系统已拒绝源码提案修改通道。"
        
        # 安全防御：防范 LLM 幻觉或 Prompt 注入导致的大规模磁盘占用 (100KB 限制)
        max_limit_bytes = 100 * 1024
        if len(new_code.encode('utf-8')) > max_limit_bytes:
            logger.error("[SelfEvolution] META_PROPOSAL_FAILED: 拦截到超过 100KB 的超长代码提案，拒绝写入以防范 DoS 风险。")
            return "为了服务器安全，代码修改提案最大限制为 100KB，你提供的代码已超出此限制，操作被拦截。"
            
        # 安全防御：AST 语法树级别的前置严格扫描验证，严防注入异常或混淆语法导致后续解析灾难
        try:
            tree = ast.parse(new_code)
            
            # Rick Sanchez 防线：微型白箱审计。递归遍历所有的语法树节点，绝不允许任何越狱的高危模块被导入，拒绝引发智械危机
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
            logger.error(f"[SelfEvolution] META_PROPOSAL_FAILED: 提案未通过 AST 语法树安全性校验: {e}")
            return f"你的代码提案存在严重语法错误或反常的混淆结构，已被 AST 静态分析防火墙拦截，操作拒绝。错误定位: {e}"
        except ValueError as e:
            logger.error(f"[SelfEvolution] META_PROPOSAL_REJECTED: 拦截到尝试引入系统危险接口的动作: {e}")
            return f"安全防线已激活：你的代码中存在针对底层操作系统或网络 IO 的敏感/非法调用（{e}）。为防止越权沙盒逃逸或恶意行为，该提案已被彻底销毁！"
        
        # 剥离极高危 RCE 漏洞，改为安全保存 Diff proposal 供管理员手动审核
        proposal_dir = self.data_dir / "code_proposals"
        try:
            proposal_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"[SelfEvolution] 建立提案隔离目录系统级 I/O 错误: {e}")
            return "文件系统异常导致隔离目录无法建立，请管理员检查权限。"
        
        # 防御磁盘耗尽：控制审计目录下的最大提案文件数量，滚动删除最旧文件
        async with self._lock:
            try:
                # 修改后缀为 .proposal 去除意外 import 导致的 RCE 执行风险
                files = list(proposal_dir.glob("main_proposed_*.proposal"))
                if len(files) >= MAX_PROPOSAL_FILES:
                    # 按时间排序，删除多余的最旧文件
                    files.sort(key=lambda p: p.stat().st_mtime)
                    for old_file in files[:len(files) - MAX_PROPOSAL_FILES + 1]:
                        old_file.unlink(missing_ok=True)
                    logger.info("[SelfEvolution] 提案数量过多，已触发机制清理陈旧代码提案文件。")
            except OSError as e:
                logger.warning(f"[SelfEvolution] 操作清理陈旧隔离文件引发操作系统异常: {e}")

            # 使用 UUIDv4 并应用防御级安全后缀，完全阻断目录注入
            proposal_file = proposal_dir / f"main_proposed_{uuid.uuid4().hex}.proposal"
            
            try:
                with open(proposal_file, "w", encoding="utf-8") as f:
                    f.write(new_code)
                # 安全防御：严格锁定文件权限极元，切断执行权位防越权滥用 (Linux/Unix-like 生效)
                os.chmod(proposal_file, 0o600)
            except OSError as e:
                logger.error(f"[SelfEvolution] 提供元编程提案生成失败 (I/O Error): {e}")
                return "操作系统安全限制阻断，请通知人类审计员排查文件权限树。"
        
        logger.warning(f"[SelfEvolution] META_PROPOSAL: 接收到元编程修改提案！已保存供管理员审计。安全缓存于: {proposal_file}。描述: {description}")
        return f"你的代码修改提案已经安全隔离至审计目录 ({proposal_file})。安全与架构团队将会审查你的代码。未通过安全评估前，新代码不会被部署。提案描述：" + description
