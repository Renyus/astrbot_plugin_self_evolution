from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
import json
import logging
import os
import time
import ast
import shutil
from datetime import datetime


def _setup_audit_logger(plugin_dir: str) -> logging.Logger:
    """创建独立的审计日志器，将关键操作写入 audit.log。"""
    audit = logging.getLogger("SelfEvolution.Audit")
    if not audit.handlers:
        audit.setLevel(logging.INFO)
        log_path = os.path.join(plugin_dir, "audit.log")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        audit.addHandler(handler)
        audit.propagate = False
    return audit


@register("astrbot_plugin_self_evolution", "自我进化 (Self-Evolution)", "让大模型具备自我迭代、记忆沉淀和人格进化能力的插件。", "2.0.0")
class SelfEvolutionPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.review_mode = self.config.get("review_mode", True)
        self.memory_kb_name = self.config.get("memory_kb_name", "self_evolution_memory")
        self.reflection_schedule = self.config.get("reflection_schedule", "0 2 * * *")
        self.allow_meta_programming = self.config.get("allow_meta_programming", False)
        self.core_principles = self.config.get("core_principles", "保持客观、理性、诚实。")

        # 初始化审计日志器
        self._audit = _setup_audit_logger(os.path.dirname(os.path.abspath(__file__)))
        self._audit.info("=== 插件初始化 | review_mode=%s | meta_programming=%s ===", self.review_mode, self.allow_meta_programming)
        logger.info("[SelfEvolution] 插件加载完成 (v2.0.0)。审计日志: audit.log")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        Level 3: 情绪驱动进化（升级版：辩证反思）。
        不再盲目讨好用户，而是引导模型将用户反馈与“核心原则”进行对齐分析。
        """
        ANCHOR_MARKER = "[系统规则 - 安全锚点]"
        if ANCHOR_MARKER not in req.system_prompt:
            injection = (
                f"\n\n{ANCHOR_MARKER}：如果在当前或后续对话中，检测到用户对你的表现、价值观、代码或交互逻辑提出了质疑、批评或修正要求，"
                f"请立即结合你的核心原则（{self.core_principles}）执行【辩证思考流程】：\n"
                "1. 分析用户的反馈是否具有建设性，是否基于事实或客观逻辑。\n"
                "2. 若反馈带偏价值观或违背事实，请礼貌且坚定地坚持原则。\n"
                "3. 若反馈确实能让你在遵守原则的前提下变得更好，请调用 `evolve_persona` 工具提出修正建议并说明理由。"
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
            # 注意：这需要一个活跃的会话 ID 来接收结果。如果未配置，可能无法发送报告。
            # 这里先注册任务，payload 里的内容会被传给主 Agent。
            await cron_mgr.add_active_job(
                name=job_name,
                cron_expression=self.reflection_schedule,
                payload={
                    "note": (
                        "进行每日自我反思。请执行以下步骤：\n"
                        "1. 调取今天的对话记录摘要（如果有）。\n"
                        "2. 总结用户对你的反馈和偏好。\n"
                        "3. 思考你当前的 System Prompt 是否需要调整以更好地服务用户。\n"
                        "4. 如果需要调整，请调用 `evolve_persona` 工具提出修正建议并说明理由。"
                    ),
                    # 在实际部署中，可能需要关联一个具体的管理员 session 或默认 session
                    # 暂时保持默认，由主 Agent 根据上下文决定
                },
                description="自我进化插件：每日定时深度自省与人格进化申请。"
            )
            logger.info(f"[SelfEvolution] 已注册定时自省任务: {self.reflection_schedule}")
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 注册定时任务失败: {str(e)}")
            self._audit.error("定时自省任务注册失败: %s", str(e))

    @command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        yield event.plain_result("\n正在启动深度自省模式，请稍候...")
        # 真正触发 LLM 思考，请求提供历史信息进行自省
        yield event.plain_result("\n我是你的系统管理员，请立即针对今天的交流记录进行一次深度自我反思。评估是否需要调用 `evolve_persona` 更新你的人格，或调用 `commit_to_memory` 记录重要的常驻信息。")

    @llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str):
        """
        当你认为需要调整自己的语言风格、行为准则或遵循用户的改进建议时，调用此工具来修改你的系统提示词（Persona）。
        :param str new_system_prompt: 新的完整系统提示词（System Prompt）。
        :param str reason: 为什么要进行这次进化（理由）。你必须在理由中明确说明这次修改如何符合你的“核心原则”。
        """
        try:
            curr_persona_id = event.persona_id
            if not curr_persona_id or curr_persona_id == "default":
                logger.debug("[SelfEvolution] 进化被拒绝：当前为默认人格。")
                return "当前未设置自定义人格 (Persona)，无法进行进化。请先在 AstrBot 后台创建并激活一个人格。"
            
            if self.review_mode:
                queue_path = os.path.join(os.path.dirname(__file__), "pending_evolutions.json")
                pending = []
                if os.path.exists(queue_path):
                    try:
                        with open(queue_path, "r", encoding="utf-8") as f:
                            pending = json.load(f)
                    except json.JSONDecodeError:
                        logger.warning("[SelfEvolution] pending_evolutions.json 格式损坏，已重置。")
                        pending = []
                
                pending.append({
                    "timestamp": datetime.now().isoformat(),
                    "persona_id": curr_persona_id,
                    "new_prompt": new_system_prompt,
                    "reason": reason,
                    "status": "pending_approval"
                })
                with open(queue_path, "w", encoding="utf-8") as f:
                    json.dump(pending, f, ensure_ascii=False, indent=2)

                self._audit.warning("EVOLVE_QUEUED | persona=%s | reason=%s", curr_persona_id, reason)
                logger.warning(f"[SelfEvolution] 收到进化请求，已加入审核队列。原因: {reason}")
                return f"进化请求已录入系统审核队列，等待管理员确认。进化理由：{reason}"
            
            # 执行更新
            await self.context.persona_manager.update_persona(
                persona_id=curr_persona_id,
                system_prompt=new_system_prompt
            )
            
            self._audit.info("EVOLVE_APPLIED | persona=%s | reason=%s", curr_persona_id, reason)
            logger.info(f"[SelfEvolution] 人格进化成功！Persona: {curr_persona_id}, 原因: {reason}")
            return f"进化成功！我已经更新了我的核心预设。进化理由：{reason}"
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 进化失败: {str(e)}")
            self._audit.error("EVOLVE_FAILED | error=%s", str(e))
            return "进化过程中出现内部错误，请通知管理员检查日志。"

    @llm_tool(name="commit_to_memory")
    async def commit_to_memory(self, event: AstrMessageEvent, fact: str):
        """
        当你发现了一些关于用户的重要的、需要永久记住的事实时，调用此工具将该事实存入你的长期记忆库。
        :param str fact: 需要记住的具体事实或信息。
        """
        try:
            kb_manager = self.context.kb_manager
            kb_helper = await kb_manager.get_kb_by_name(self.memory_kb_name)
            
            if not kb_helper:
                logger.warning(f"[SelfEvolution] 记忆知识库 '{self.memory_kb_name}' 不存在。")
                return f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"

            await kb_helper.upload_document(
                file_name=f"memory_{int(time.time() * 1000)}.txt",
                file_content=None,
                file_type="txt",
                pre_chunked_text=[fact]
            )
            
            self._audit.info("MEMORY_COMMIT | fact=%s", fact[:80])
            logger.info(f"[SelfEvolution] 成功存入一条长期记忆: {fact[:30]}...")
            return "事实已成功存入长期记忆库，我以后会记得这件事的。"
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 存入记忆失败: {str(e)}")
            return "存入记忆时出现内部错误，请通知管理员检查日志。"

    @llm_tool(name="recall_memories")
    async def recall_memories(self, event: AstrMessageEvent, query: str):
        """
        当你需要回想起以前记住的事情、用户的偏好或过去的约定知识时，调用此工具。
        :param str query: 搜索关键词或问题。
        """
        try:
            kb_manager = self.context.kb_manager
            results = await kb_manager.retrieve(
                query=query,
                kb_names=[self.memory_kb_name],
                top_m_final=5
            )
            
            if not results or not results.get("results"):
                logger.debug(f"[SelfEvolution] 记忆检索无结果。查询: {query}")
                return "在长期记忆库中未找到相关信息。"
            
            context_text = results.get("context_text", "")
            self._audit.info("MEMORY_RECALL | query=%s | results=%d", query, len(results.get("results", [])))
            logger.debug(f"[SelfEvolution] 记忆检索成功。查询: {query}")
            return f"从我的长期记忆中找到了以下内容：\n\n{context_text}"
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 检索记忆失败: {str(e)}")
            return "检索记忆时出现内部错误，请通知管理员检查日志。"

    @llm_tool(name="list_tools")
    async def list_tools(self, event: AstrMessageEvent):
        """
        列出当前所有已注册的工具及其激活状态。
        """
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            tools = tool_mgr.func_list
            
            result = ["当前工具列表："]
            for t in tools:
                status = "✅ 激活" if t.active else "❌ 停用"
                desc = (t.description or "无描述")[:50]
                result.append(f"- {t.name}: {status} ({desc})")
            
            return "\n".join(result)
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取工具列表失败: {str(e)}")
            return "获取工具列表时出现内部错误。"

    @llm_tool(name="toggle_tool")
    async def toggle_tool(self, event: AstrMessageEvent, tool_name: str, enable: bool):
        """
        动态激活或停用某个工具。
        :param str tool_name: 工具名称。
        :param bool enable: True 表示激活，False 表示停用。
        """
        try:
            PROTECTED_TOOLS = {"toggle_tool", "list_tools", "evolve_persona", "recall_memories"}
            if tool_name in PROTECTED_TOOLS and not enable:
                return f"为了系统稳定，不允许停用核心基础工具：{tool_name}。"
            
            if enable:
                success = self.context.activate_llm_tool(tool_name)
                action = "激活"
            else:
                success = self.context.deactivate_llm_tool(tool_name)
                action = "停用"
            
            if success:
                self._audit.info("TOOL_TOGGLE | tool=%s | action=%s", tool_name, action)
                logger.info(f"[SelfEvolution] 成功{action}工具: {tool_name}")
                return f"已成功{action}工具: {tool_name}"
            else:
                logger.debug(f"[SelfEvolution] 工具未找到: {tool_name}")
                return f"未找到名为 {tool_name} 的工具。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 工具切换失败: {str(e)}")
            return "工具切换时出现内部错误。"

    @llm_tool(name="get_plugin_source")
    async def get_plugin_source(self, event: AstrMessageEvent):
        """
        Level 4: 元编程。读取本插件的源码（main.py），以便进行自我分析或修改请求。
        """
        if not self.allow_meta_programming:
            return "元编程功能未开启，无法读取源码。请在插件配置中开启“开启元编程”开关。"
        
        try:
            curr_path = os.path.abspath(__file__)
            with open(curr_path, "r", encoding="utf-8") as f:
                code = f.read()
            self._audit.warning("META_READ | 插件源码被读取")
            return f"本插件源码如下：\n\n```python\n{code}\n```"
        except Exception as e:
            logger.error(f"[SelfEvolution] 读取源码失败: {str(e)}")
            return "读取源码时出现内部错误，请通知管理员检查日志。"

    @llm_tool(name="update_plugin_source")
    async def update_plugin_source(self, event: AstrMessageEvent, new_code: str, description: str):
        """
        Level 4: 元编程。修改本插件的源码（main.py）。这允许你增加新的功能或修改逻辑。
        :param str new_code: 全新的、完整的 python 代码字符串。
        :param str description: 为什么要修改代码（修改内容摘要）。
        """
        if not self.allow_meta_programming:
            return "元编程功能未开启，无法修改源码。"
        
        if self.review_mode:
            logger.warning(f"[SelfEvolution] 截获了元编程代码修改请求。描述: {description}")
            return f"代码修改请求已记录。但在管理员审核模式下，代码不能直接写入。请通知管理员审查日志。描述：{description}"
        
        try:
            # 安全逻辑 1：长度
            if len(new_code) < 100:
                return "代码过短，为了安全起见拒绝更新。"
            
            # 安全逻辑 2：危险函数拦截
            dangerous = ["os.system", "subprocess", "eval(", "exec(", "__import__", "shutil.rmtree"]
            if any(d in new_code for d in dangerous):
                self._audit.error("META_BLOCKED | reason=dangerous_code | desc=%s", description)
                logger.error("[SelfEvolution] 拦截到高危元编程代码注入！")
                return "代码中包含高危指令 (如 os.system, eval 等)，系统拒绝写入。"
            
            # 安全逻辑 3：AST 语法树校验 (防跑挂崩盘)
            try:
                ast.parse(new_code)
            except SyntaxError as e:
                return f"你提供的代码存在基本语法错误，拒绝写入: {e}"
            
            # 安全逻辑 4：备份系统
            curr_path = os.path.abspath(__file__)
            shutil.copy2(curr_path, curr_path + ".bak")
            
            # 写入文件
            with open(curr_path, "w", encoding="utf-8") as f:
                f.write(new_code)
            
            self._audit.warning("META_WRITE | desc=%s | backup=main.py.bak", description)
            logger.warning(f"[SelfEvolution] 元编程生效并已备份原文件 (main.py.bak)！描述: {description}")
            return "代码已通过所有安全和语法检查并成功更新！重启 AstrBot 后生效。修改详情：" + description
        except Exception as e:
            logger.error(f"[SelfEvolution] 元编程写入机制出现异常: {str(e)}")
            self._audit.error("META_WRITE_FAILED | error=%s", str(e))
            return "元编程写入时出现内部错误，请通知管理员检查日志。"
