from astrbot.api.all import Context, AstrMessageEvent, Star, register
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.api import logger
import asyncio
import uuid
import os
import time
import re
import aiosqlite
from datetime import datetime
import inspect

# 导入模块化组件
from .dao import SelfEvolutionDAO
from .engine.eavesdropping import EavesdroppingEngine
from .engine.meta_infra import MetaInfra


# 全局不可变常量提取 (迁移至主类管理)
ANCHOR_MARKER = "Core Safety Anchor"
PROTECTED_TOOLS = frozenset(
    {
        "toggle_tool",
        "list_tools",
        "evolve_persona",
        "recall_memories",
        "review_evolutions",
        "approve_evolution",
    }
)
PAGE_LIMIT = 10


@register(
    "astrbot_plugin_self_evolution",
    "自我进化 (Self-Evolution)",
    "具备主动环境感知及插嘴引擎的 CognitionCore 3.0 数字生命。",
    "3.2.12",
)
class SelfEvolutionPlugin(Star):
    @staticmethod
    def _parse_bool(val, default):
        """更严谨地将配置项解析为布尔值，防止字符串 'false' 被判为 True"""
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return default

    def __init__(self, context: Context, config: dict):
        super().__init__(context, config)
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir() / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = os.path.join(self.data_dir, "self_evolution.db")

        # 初始化模块化组件
        try:
            self.dao = SelfEvolutionDAO(db_path)
            self.eavesdropping = EavesdroppingEngine(self)
            self.meta_infra = MetaInfra(self)
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, MetaInfra) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 3.0: 状态容器
        self.active_buffers = {}  # {session_id: [msg_list]}
        self.processing_sessions = set()
        self._lock = None  # 用于元编程写锁
        self.daily_reflection_pending = False
        self._just_stored_memory = False  # 这次存储，下次检索

    @property
    def persona_name(self):
        return self.config.get("persona_name", "黑塔")

    @property
    def persona_title(self):
        return self.config.get("persona_title", "人偶负责人")

    @property
    def persona_style(self):
        return self.config.get("persona_style", "理性、犀利且专业")

    @property
    def interjection_desire(self):
        return int(self.config.get("interjection_desire", 5))

    @property
    def critical_keywords(self):
        return self.config.get(
            "critical_keywords",
            "黑塔|空间站|人偶|天才|模拟宇宙|研究|论文|技术|算力|数据",
        )

    @property
    def buffer_threshold(self):
        return int(self.config.get("buffer_threshold", 8))

    @property
    def max_buffer_size(self):
        return int(self.config.get("max_buffer_size", 20))

    @property
    def review_mode(self):
        return self._parse_bool(self.config.get("review_mode"), True)

    @property
    def memory_kb_name(self):
        return self.config.get("memory_kb_name", "self_evolution_memory")

    @property
    def reflection_schedule(self):
        return self.config.get("reflection_schedule", "0 2 * * *")

    @property
    def allow_meta_programming(self):
        return self._parse_bool(self.config.get("allow_meta_programming"), False)

    @property
    def core_principles(self):
        return self.config.get("core_principles", "保持理性、诚实、守法。")

    @property
    def admin_users(self):
        return self.config.get("admin_users", [])

    @property
    def timeout_memory_commit(self):
        return float(self.config.get("timeout_memory_commit", 10.0))

    @property
    def timeout_memory_recall(self):
        return float(self.config.get("timeout_memory_recall", 12.0))

    @property
    def max_memory_entries(self):
        return int(self.config.get("max_memory_entries", 100))

    def _post_init(self):
        logger.info(
            f"[SelfEvolution] === 插件初始化完成 | 模式: {'审核' if self.review_mode else '自动'} | 元编程: {self.allow_meta_programming} ==="
        )

    async def initialize(self) -> None:
        await self.dao.init_db()

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, metadata):
        """
        拦截框架卸载/热重载钩子，执行资源闭环收尾以防止高并发下的 SQLite database is locked
        """
        try:
            await self.dao.close()
            logger.info(
                "[SelfEvolution] 插件卸载钩子触发：DAO 长连接及底层句柄已安全脱离释放。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 释放 DAO 资源异常: {e}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        CognitionCore 2.0: 情感拦截与身份感知注入。
        级别: Level 3+
        """
        logger.info(
            f"[CognitionCore] 进入 LLM 请求拦截层。用户: {event.get_sender_id()} | 消息: '{event.message_str}'"
        )
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

        # 增强身份识别逻辑
        sender_name = event.get_sender_name() or "Unknown User"

        # 获取群组特征
        is_group = bool(event.get_group_id())
        role_info = "（管理员）" if event.is_admin() else ""

        # 从消息链中提取 [引用] 和 [At]
        quoted_info = ""
        at_targets = []
        for comp in event.get_messages():
            if type(comp).__name__ == "Reply":
                quoted_info = f"，你正在回复用户 {getattr(comp, 'sender_nickname', '未知')} 的消息"
            elif type(comp).__name__ == "At":
                at_targets.append(str(getattr(comp, "qq", "")))

        at_info = f"，消息中提到了: {', '.join(at_targets)}" if at_targets else ""

        # 构造上下文注入
        context_info = f"\n\n[当前交互上下文环境信息]：\n- 发送者ID: {sender_id}\n- 发送者昵称: {sender_name}{role_info}\n- 情感积分: {affinity}/100\n"
        if is_group:
            context_info += f"- 来源：群聊\n- 交互上下文: 你{quoted_info}{at_info}\n"
        else:
            context_info += "- 来源：私聊\n"

        context_info += (
            "\n【核心认知指令 - 身份隔离与动态心跳 (CognitionCore 4.0)】：\n"
            "1. 严格区分当前发送者与历史记录中其他群成员。不要因他人的恶意攻击当前发送者。\n"
            "2. 情感评分（当前:{affinity}/100）是动态的。请对当前发送者的【每一句话】进行实时情感归因评估。\n"
            "3. 即使分数已满，也请保持审视。若对方表现优秀请维持评分；若出现废话、无礼或违规，请果断调用 `update_affinity` 进行微调（建议波动范围: ±1~5）。\n"
            "4. 在回复引用内容时，请确保逻辑闭环，并明确回复对象。"
        )
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
            logger.debug(
                f"[CognitionCore] 已向 session_id:{session_id} 注入认知蒸馏指令。"
            )

        # 系统核心锚点 (优化为更自然柔和的引导，响应设计优雅性反馈)
        if ANCHOR_MARKER not in req.system_prompt:
            injection = (
                f"\n\n({ANCHOR_MARKER}) 当你接收到用户的评价或批评时，请以你的核心原则为准绳（{self.core_principles}）。"
                "如果反馈具备客观建设性，请随时调用 `evolve_persona` 主动寻求进化。如果在道德或事实上存在冲突，请坚守底线并优雅地拒绝。"
            )
            req.system_prompt += injection
            logger.debug("[SelfEvolution] 已在上下文中注入常驻辩证反省指令。")

        # 4. 自动记忆检索与注入 (Auto-Recall)
        await self._auto_recall_inject(event, req)

    async def _auto_recall_inject(self, event: AstrMessageEvent, req: ProviderRequest):
        """自动检索记忆并注入到 LLM 上下文中"""
        # 如果这次刚存储了记忆，跳过检索（延迟到下次对话）
        if self._just_stored_memory:
            logger.info(
                "[SelfEvolution] 刚存储记忆，跳过本次检索，等待下次对话时回忆。"
            )
            self._just_stored_memory = False
            return

        try:
            kb_manager = self.context.kb_manager
            query = event.message_str

            if not query or len(query.strip()) < 2:
                return

            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=query, kb_names=[self.memory_kb_name], top_m_final=3
                ),
                timeout=self.timeout_memory_recall,
            )

            if results and results.get("results"):
                context_text = results.get("context_text", "")
                if context_text:
                    memory_injection = (
                        f"\n\n[长期记忆检索结果]：\n{context_text}\n"
                        "请结合以上记忆信息回复用户。如果记忆内容与当前对话无关，请忽略。"
                    )
                    req.system_prompt += memory_injection
                    logger.info(
                        f"[SelfEvolution] 自动记忆注入成功：{len(results.get('results', []))} 条相关记忆"
                    )
        except asyncio.TimeoutError:
            logger.warning("[SelfEvolution] 自动记忆检索超时，已跳过注入。")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 自动记忆检索失败: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 3.0: 被动监听转发至 EavesdroppingEngine"""
        # 自动学习触发：检测关键场景
        await self._auto_learn_trigger(event)

        async for result in self.eavesdropping.handle_message(event):
            yield result

    async def _auto_learn_trigger(self, event: AstrMessageEvent):
        """自动学习触发器：检测关键场景并自动提取记忆"""
        msg_text = event.message_str
        is_at = event.is_at_or_wake_command

        # 检测是否为关键场景
        is_key_scene = False

        # 场景1: @AI 的消息
        if is_at:
            is_key_scene = True

        # 场景2: 包含关键词
        if not is_key_scene:
            try:
                critical_pattern = re.compile(
                    f"({self.critical_keywords})", re.IGNORECASE
                )
                if critical_pattern.search(msg_text):
                    is_key_scene = True
            except Exception:
                pass

        # 场景3: 用户道别（记住这个用户要离开了）
        goodbye_keywords = ["再见", "拜拜", "走了", "下线", "休息", "睡觉", "晚安"]
        if not is_key_scene and any(kw in msg_text for kw in goodbye_keywords):
            is_key_scene = True

        # 场景4: 用户表达偏好（喜欢/讨厌/想要）
        preference_keywords = [
            "我喜欢",
            "我讨厌",
            "我想要",
            "我喜欢",
            "我不喜欢",
            "我想要",
        ]
        if not is_key_scene and any(kw in msg_text for kw in preference_keywords):
            is_key_scene = True

        # 如果是关键场景，自动学习（按用户/群汇总存知识库）
        if is_key_scene:
            await self._learn_to_memory(event, msg_text)

    async def _learn_to_memory(self, event: AstrMessageEvent, msg_text: str):
        """按用户/群汇总存知识库"""
        try:
            group_id = event.get_group_id()
            user_id = event.get_sender_id()
            user_name = event.get_sender_name() or "未知用户"
            msg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 确定文档名称：群聊用群号，私聊用用户ID
            if group_id:
                doc_name = f"memory_group_{group_id}"
            else:
                doc_name = f"memory_user_{user_id}"

            new_entry = f"[{msg_time}] {user_name}: {msg_text}"

            kb_manager = self.context.kb_manager
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )

            if not kb_helper:
                logger.warning(f"[SelfEvolution] 知识库 {self.memory_kb_name} 不存在")
                return

            # 查找现有文档
            docs = await kb_helper.list_documents()
            existing_doc = None
            for doc in docs:
                if getattr(doc, "doc_name", "") == doc_name:
                    existing_doc = doc
                    break

            if existing_doc:
                # 更新现有文档（删除旧文档，上传新内容）
                doc_id = getattr(existing_doc, "doc_id", None)
                if doc_id:
                    await kb_helper.delete_document(doc_id)
                    logger.info(f"[SelfEvolution] 已删除旧记忆文档: {doc_name}")

            # 上传新内容
            content = new_entry
            if existing_doc:
                # 追加模式：这里需要业务逻辑配合，目前先简单覆盖
                # TODO: 可以考虑更复杂的追加逻辑
                pass

            await kb_helper.upload_document(
                file_name=f"{doc_name}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[content],
            )

            self._just_stored_memory = True
            logger.info(f"[SelfEvolution] 自动学习：已保存记忆到 {doc_name}")

        except Exception as e:
            logger.warning(f"[SelfEvolution] 自动学习失败: {e}")

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
                description="自我进化插件：每日定时深度自省标记。",
            )
            logger.info(
                f"[SelfEvolution] 已注册定时自省任务: {self.reflection_schedule}"
            )

        except Exception as e:
            logger.error(f"[SelfEvolution] 注册定时任务失败: {e}")

    async def _scheduled_reflection(self):
        """定时任务回调函数"""
        self.daily_reflection_pending = True
        logger.info(
            "[SelfEvolution] 每日反思定时任务已触发，将在下一次对话时顺带执行深层内省。"
        )

        # 异步初始化/维护数据库
        await self.dao.init_db()

        # [大赦天下]: 每日自动回复所有黑名单用户 2 点好感度，直到恢复到 50 (中立)
        await self.dao.recover_all_affinity(recovery_amount=2)
        logger.info(
            '[SelfEvolution] 已执行每日"大赦天下"：所有负面评分用户好感度已小幅回升。'
        )

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        # 静默标志位设置，LLM 将在下一次收到消息时被隐式注入上下文指令，避免界面粗暴弹出系统提示语
        await self.dao.set_pending_reflection(event.session_id, True)
        yield event.plain_result(
            "认知蒸馏协议已就绪，将在下一次对话时执行深度实体提取。"
        )

    @filter.command("affinity")
    async def check_affinity(self, event: AstrMessageEvent):
        """查询机器人对你的当前好感度。"""
        user_id = event.get_sender_id()
        score = await self.dao.get_affinity(user_id)

        status = (
            "信任"
            if score >= 80
            else "友好"
            if score >= 60
            else "中立"
            if score >= 40
            else "敌对"
        )
        if score <= 0:
            status = "【已熔断/彻底拉黑】"

        yield event.plain_result(
            f"UID: {user_id}\n{self.persona_name} 的情感矩阵评分: {score}/100\n分类状态: {status}"
        )

    @filter.command("set_affinity")
    async def set_affinity(self, event: AstrMessageEvent, user_id: str, score: int):
        """
        [管理员] 手动重置指定用户的好感度评分。
        用法: /set_affinity [用户ID] [分数(0-100)]
        """
        if not event.is_admin():
            yield event.plain_result(
                f"错误：权限不足。只有管理员能干涉 {self.persona_name} 的'情感矩阵'。"
            )
            return

        await self.dao.reset_affinity(user_id, score)
        logger.warning(
            f"[SelfEvolution] 管理员 {event.get_sender_id()} 强制重置了用户 {user_id} 的好感度为 {score}。"
        )
        yield event.plain_result(f"已成功将用户 {user_id} 的情感评分修正为: {score}")

    @filter.llm_tool(name="update_affinity")
    async def update_affinity(
        self, event: AstrMessageEvent, delta: int, reason: str
    ) -> str:
        """
        根据用户的言行调整其情感积分。
        :param int delta: 调整值（如 -10 表示冒犯, +5 表示赞赏）。积分跌至 0 将导致系统自动拦截。
        :param str reason: 调整理由（必须说明用户具体哪项言行导致了积分变动）。
        """
        user_id = event.get_sender_id()
        await self.dao.update_affinity(user_id, delta)
        logger.warning(
            f"[CognitionCore] 用户 {user_id} 积分变动 {delta}，原因: {reason}"
        )
        return f"用户情感积分已更新。当前调整理由：{reason}"

    @filter.llm_tool(name="evolve_persona")
    async def evolve_persona(
        self, event: AstrMessageEvent, new_system_prompt: str, reason: str
    ) -> str:
        """
        当你认为需要调整自己的语言风格、行为准则或遵循用户的改进建议时，调用此工具来修改你的系统提示词（Persona）。
        :param str new_system_prompt: 新的完整系统提示词（System Prompt）。
        :param str reason: 为什么要进行这次进化（理由）。你必须在理由中明确说明这次修改如何符合你的"核心原则"。
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
                conversation = (
                    await conv_mgr.get_conversation(umo, cid) if cid else None
                )
                conversation_persona_id = (
                    conversation.persona_id if conversation else None
                )

                cfg = self.context.get_config(umo=umo).get("provider_settings", {})

                (
                    curr_persona_id,
                    _,
                    _,
                    _,
                ) = await self.context.persona_manager.resolve_selected_persona(
                    umo=umo,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=event.get_platform_name(),
                    provider_settings=cfg,
                )
            except Exception as e:
                logger.error(
                    f"[SelfEvolution] 使用 resolve_selected_persona 获取人格 ID 失败: {e}"
                )
                curr_persona_id = "default"

        if not curr_persona_id or curr_persona_id == "default":
            logger.debug(
                f"[SelfEvolution] 进化被拒绝：当前人格 ID 为 {curr_persona_id}，无法进化默认人格。"
            )
            return "当前未设置自定义人格 (Persona)，无法进行进化。请先在 AstrBot 后台创建并激活一个人格。"

        if self.review_mode:
            try:
                await self.dao.add_pending_evolution(
                    curr_persona_id, new_system_prompt, reason
                )

                logger.warning(
                    f"[SelfEvolution] EVOLVE_QUEUED: 收到进化请求，已加入审核队列。原因: {reason}"
                )
                return f"进化请求已录入系统审核队列，等待管理员确认。进化理由：{reason}"
            except aiosqlite.Error as e:
                logger.error(
                    f"[SelfEvolution] EVOLVE_FAILED: 写入审核队列时发生异步数据库异常: {e}"
                )
                return "写入审核队列时发生持久化存储异常，请告知管理员。"

        # 执行更新
        try:
            await self.context.persona_manager.update_persona(
                persona_id=curr_persona_id, system_prompt=new_system_prompt
            )
            logger.info(
                f"[SelfEvolution] EVOLVE_APPLIED: 人格进化成功！Persona: {curr_persona_id}, 原因: {reason}"
            )
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
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result(
                "权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。"
            )
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
                result.append(
                    f"ID: {row['id']} | Persona: {row['persona_id']}\n理由: {row['reason'][:50]}"
                )

            result.append(
                "\n如需批准，请调用 '/approve_evolution <ID>'。如需翻看下一页，请调用 '/review_evolutions <页码>'"
            )
            yield event.plain_result("\n".join(result))
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 获取审核列表失败 (DB Error): {e}")
            yield event.plain_result("获取审核列表失败，数据库发生异常，请查看日志。")

    @filter.command("approve_evolution")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """
        【管理员接口】批准指定 ID 的人格进化请求。
        """
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result(
                "权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。"
            )
            return

        try:
            # 阶段 1: 建立快闪调用，读取关键数据
            row = await self.dao.get_evolution(request_id)

            if not row:
                yield event.plain_result(f"找不到待处理的请求 ID {request_id}。")
                return

            # 阶段 2: 执行耗时/外部 API 更新，得益于 DAO 抽象，此时不必担忧底层连接
            await self.context.persona_manager.update_persona(
                persona_id=row["persona_id"], system_prompt=row["new_prompt"]
            )

            # 阶段 3: DAO 状态本身自带 3 次异常重试，直接抛出成功即可
            try:
                await self.dao.update_evolution_status(request_id, "approved")
                logger.info(f"[SelfEvolution] 管理员批准了进化请求 ID: {request_id}")
                yield event.plain_result(
                    f"成功批准了进化请求 {request_id}，大模型人格已更新！"
                )
            except Exception as e:
                logger.error(
                    f"[SelfEvolution] 致命异常：大模型人格已更新成功，但在同步数据库状态时多次重试均失败: {e}"
                )
                yield event.plain_result(
                    f"⚠️ 警告：大模型核心人格已经成功进化！但由于数据库操作中断，审批状态列表（ID {request_id}）未能正确刷新为已批准。底层接口具备幂等性，请管理员排查环境后稍后尝试重复操作以补齐状态。"
                )

        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 读取/状态更新发生数据库操作阻断: {e}")
            yield event.plain_result("处理请求期间出现底层数据库异常，请查阅日志。")
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise  # 防止吞噬掉代码层面的严格结构异常
            logger.error(f"[SelfEvolution] 批准进化请求发生泛用(外部/业务)异常: {e}")
            yield event.plain_result(
                f"执行审批与人格变更时遭遇异常({e.__class__.__name__})，请查阅日志。"
            )

    @filter.command("reject_evolution")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """
        【管理员接口】拒绝指定 ID 的人格进化请求。
        """
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return

        try:
            await self.dao.update_evolution_status(request_id, "rejected")
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
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
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
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name() or "未知用户"
        group_id = event.get_group_id() or "私聊"
        unified_msg_origin = event.unified_msg_origin

        formatted_fact = (
            f"【记忆条目】\n"
            f"来源: {unified_msg_origin}\n"
            f"说话者: {sender_name} (ID: {sender_id})\n"
            f"群/私聊: {group_id}\n"
            f"内容: {fact}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return await self._do_commit_memory(event, formatted_fact)

    async def _do_commit_memory(
        self, event: AstrMessageEvent, formatted_fact: str, is_auto: bool = False
    ) -> str:
        """执行实际的存入记忆逻辑（包含去重和自动清理）"""
        kb_manager = self.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )
        except asyncio.TimeoutError:
            logger.error("[SelfEvolution] 记忆库装载严重超时。")
            return "与知识引擎服务器建立信道超时，中断存入以维持会话流畅。"
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise
            logger.error(f"[SelfEvolution] 记忆检索或系统网络失效: {e}")
            return "检索长期记忆时发生业务异常，请检查配置与联通状态。"

        if not kb_helper:
            logger.warning(
                f"[SelfEvolution] 记忆知识库 '{self.memory_kb_name}' 不存在。"
            )
            return (
                f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"
            )

        try:
            # 记忆去重：检查是否已存在相似内容
            try:
                check_results = await asyncio.wait_for(
                    kb_manager.retrieve(
                        query=formatted_fact[:100],
                        kb_names=[self.memory_kb_name],
                        top_m_final=3,
                    ),
                    timeout=5.0,
                )
                if check_results and check_results.get("results"):
                    for r in check_results.get("results", []):
                        if r.get("text") and formatted_fact[:50] in r.get("text", ""):
                            logger.info(
                                f"[SelfEvolution] 记忆去重：检测到相似内容已存在，跳过存入。"
                            )
                            return "已存在相似记忆，无需重复存储。"
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.warning(f"[SelfEvolution] 记忆去重检查失败: {e}")

            # 自动清理：检查记忆数量，超过上限时删除最旧的
            max_memory_entries = getattr(self, "max_memory_entries", 100)
            try:
                docs = await kb_helper.list_documents()
                if docs and len(docs) >= max_memory_entries:
                    oldest_doc = min(
                        docs, key=lambda d: getattr(d, "created_at", "") or ""
                    )
                    doc_id = getattr(oldest_doc, "doc_id", None)
                    if doc_id:
                        await kb_helper.delete_document(doc_id)
                        logger.info(
                            f"[SelfEvolution] 自动清理：已删除最旧的记忆条目 {doc_id}"
                        )
            except Exception as e:
                logger.warning(f"[SelfEvolution] 自动清理失败: {e}")

            # 存入记忆
            await kb_helper.upload_document(
                file_name=f"memory_{int(time.time() * 1000)}.txt",
                file_content=b"",
                file_type="txt",
                pre_chunked_text=[formatted_fact],
            )
            logger.info(
                f"[SelfEvolution] MEMORY_COMMIT: 成功存入一条长期记忆: {formatted_fact[:50]}..."
            )
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
                    query=query, kb_names=[self.memory_kb_name], top_m_final=5
                ),
                timeout=self.timeout_memory_recall,
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
        logger.info(
            f"[SelfEvolution] MEMORY_RECALL: 记忆检索成功。查询: {query} -> 找到 {len(results.get('results', []))} 条结果。"
        )
        return f"从我的长期记忆中找到了以下内容：\n\n{context_text}"

    @filter.llm_tool(name="learn_from_context")
    async def learn_from_context(
        self, event: AstrMessageEvent, key_info: str = ""
    ) -> str:
        """
        从当前对话中自动提取关键信息并存入长期记忆。当你发现用户表达了重要偏好、约定或事实时调用此工具。
        :param str key_info: 需要记住的关键信息（如果留空，将自动提取当前对话中的关键内容）。
        :return: 学习结果的状态字符串。
        """
        sender_name = event.get_sender_name() or "未知用户"
        sender_id = event.get_sender_id()
        group_id = event.get_group_id() or "私聊"
        unified_msg_origin = event.unified_msg_origin
        message_text = event.message_str

        fact = key_info if key_info else f"用户在当前对话中提到: {message_text}"

        formatted_fact = (
            f"【记忆条目-对话学习】\n"
            f"来源: {unified_msg_origin}\n"
            f"说话者: {sender_name} (ID: {sender_id})\n"
            f"群/私聊: {group_id}\n"
            f"内容: {fact}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return await self._do_commit_memory(event, formatted_fact, is_auto=True)

    @filter.llm_tool(name="clear_all_memory")
    async def clear_all_memory(
        self, event: AstrMessageEvent, confirm: bool = False
    ) -> str:
        """
        清空指定知识库中的所有记忆条目。谨慎使用！
        :param bool confirm: 必须传入 true 才能执行清空操作（防止误操作）。
        :return: 操作结果的状态字符串。
        """
        if not confirm:
            return "请传入 confirm=true 确认要清空全部记忆，例如: clear_all_memory(confirm=true)"

        kb_manager = self.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name),
                timeout=self.timeout_memory_commit,
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取知识库失败: {e}")
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            docs = await kb_helper.list_documents()
            if not docs:
                return "记忆库已经是空的了。"

            deleted_count = 0
            for doc in docs:
                try:
                    doc_id = getattr(doc, "doc_id", None)
                    if doc_id:
                        await kb_helper.delete_document(doc_id)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"[SelfEvolution] 删除记忆条目失败: {e}")

            logger.info(f"[SelfEvolution] 清空记忆：成功删除 {deleted_count} 条记忆")
            return f"已成功清空 {deleted_count} 条记忆条目。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 清空记忆失败: {e}")
            return f"清空记忆失败: {e}"

    @filter.llm_tool(name="list_memories")
    async def list_memories(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """
        列出当前存储在知识库中的记忆条目。
        :param int limit: 最多显示的记忆条目数量，默认10条。
        :return: 记忆条目列表。
        """
        kb_manager = self.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )
        except Exception as e:
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            docs = await kb_helper.list_documents()
            if not docs:
                return "记忆库中还没有任何记忆。"

            docs = docs[:limit]
            result = [f"当前记忆库共有 {len(docs)} 条记忆（显示前 {len(docs)} 条）："]
            for i, doc in enumerate(docs, 1):
                doc_name = getattr(doc, "doc_name", "未知")
                created_at = getattr(doc, "created_at", "未知时间")
                result.append(f"{i}. {doc_name} (创建于: {created_at})")

            return "\n".join(result)
        except Exception as e:
            logger.error(f"[SelfEvolution] 列出记忆失败: {e}")
            return f"列出记忆失败: {e}"

    @filter.llm_tool(name="delete_memory")
    async def delete_memory(self, event: AstrMessageEvent, doc_id: str) -> str:
        """
        删除知识库中的单条记忆。
        :param str doc_id: 要删除的记忆条目ID。
        :return: 操作结果的状态字符串。
        """
        kb_manager = self.context.kb_manager
        try:
            kb_helper = await asyncio.wait_for(
                kb_manager.get_kb_by_name(self.memory_kb_name), timeout=5.0
            )
        except Exception as e:
            return f"获取知识库失败: {e}"

        if not kb_helper:
            return f"未找到名为 {self.memory_kb_name} 的记忆知识库"

        try:
            await kb_helper.delete_document(doc_id)
            logger.info(f"[SelfEvolution] 删除记忆：成功删除 doc_id={doc_id}")
            return f"已成功删除记忆条目 {doc_id}。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 删除记忆失败: {e}")
            return f"删除记忆失败: {e}"

    @filter.llm_tool(name="auto_recall")
    async def auto_recall(self, event: AstrMessageEvent, topic: str = "") -> str:
        """
        当检测到当前对话涉及历史记忆时，主动将相关记忆注入上下文。
        :param str topic: 当前对话涉及的话题关键词（如果留空，将使用当前消息内容）。
        :return: 相关记忆内容或"无相关记忆"的提示。
        """
        query = topic if topic else event.message_str

        kb_manager = self.context.kb_manager
        try:
            results = await asyncio.wait_for(
                kb_manager.retrieve(
                    query=query, kb_names=[self.memory_kb_name], top_m_final=3
                ),
                timeout=self.timeout_memory_recall,
            )
        except asyncio.TimeoutError:
            return "检索记忆超时，请稍后重试。"
        except Exception as e:
            logger.error(f"[SelfEvolution] auto_recall 失败: {e}")
            return "检索记忆时发生异常。"

        if not results or not results.get("results"):
            return "当前对话未涉及任何历史记忆。"

        context_text = results.get("context_text", "")
        logger.info(
            f"[SelfEvolution] AUTO_RECALL: 找到 {len(results.get('results', []))} 条相关记忆"
        )

        return (
            f"【相关记忆触发】\n"
            f"当前话题: {query}\n"
            f"--- 历史记忆 ---\n{context_text}\n"
            f"----------------\n"
            f"以上是与你当前话题相关的记忆，请结合这些信息回复用户。"
        )

    @filter.llm_tool(name="list_tools")
    async def list_tools(self, event: AstrMessageEvent) -> str:
        """
        列出当前所有已注册的工具及其激活状态。
        :return: 带有详细激活态的格式化字符串报表。
        """
        try:
            tool_mgr = self.context.get_llm_tool_manager()

            # 兼容性寻找官方标准公开 API，移除脆弱的底层反射尝试逻辑
            if hasattr(tool_mgr, "get_registered_tools"):
                tools = tool_mgr.get_registered_tools()
            elif hasattr(tool_mgr, "get_all_tools"):
                tools = tool_mgr.get_all_tools()
            else:
                return "安全预警：AstrBot框架核心架构已历经改组，get_registered_tools 等公开接口失效。"

            result = ["当前工具列表："]
            for t in tools:
                status = "✅ 激活" if getattr(t, "active", True) else "❌ 停用"
                desc = getattr(t, "description", "无描述")
                if desc:
                    desc = desc[:50]
                result.append(f"- {getattr(t, 'name', 'Unknown')}: {status} ({desc})")

            return "\n".join(result)
        except Exception as e:
            logger.error(f"[SelfEvolution] 获取工具列表失败: {e}")
            return "获取工具列表时出现内部异常处理错误。"

    @filter.llm_tool(name="toggle_tool")
    async def toggle_tool(
        self, event: AstrMessageEvent, tool_name: str, enable: bool
    ) -> str:
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
                logger.error(
                    "[SelfEvolution] 底层 API 异常: 工具激活机制的底层接口缺失。"
                )
                return "安全保护：框架底层管理结构发生异常，无法调整工具激活状态。"

            if success:
                logger.info(
                    f"[SelfEvolution] TOOL_TOGGLE: 成功{action}工具: {tool_name}"
                )
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
    async def get_plugin_source(
        self, event: AstrMessageEvent, mod_name: str = "main"
    ) -> str:
        """
        Level 4: 元编程。读取本插件的源码，以便进行自我分析或修改请求。
        :param str mod_name: 模块名，可选: main, dao, eavesdropping, meta_infra
        """
        return await self.meta_infra.get_plugin_source(mod_name)

    @filter.llm_tool(name="update_plugin_source")
    async def update_plugin_source(
        self,
        event: AstrMessageEvent,
        new_code: str,
        description: str,
        target_file: str = "main.py",
    ) -> str:
        """
        Level 4: 元编程。针对本插件提出代码修改建议。
        :param str new_code: 全新的、完整的 python 代码字符串。
        :param str description: 为什么要修改代码。
        :param str target_file: 目标文件名。
        """
        return await self.meta_infra.update_plugin_source(
            new_code, description, target_file
        )
