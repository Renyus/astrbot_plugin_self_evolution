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
from .engine.memory import MemoryManager
from .engine.persona import PersonaManager
from .engine.profile import ProfileManager
from .engine.chat_logger import ChatLogger


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
    "具备主动环境感知及插嘴引擎的 CognitionCore 6.0 数字生命。",
    "3.4.0",
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
            self.memory = MemoryManager(self)
            self.persona = PersonaManager(self)
            self.profile = ProfileManager(self)
            self.chat_logger = ChatLogger(self)
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, MetaInfra, Memory, Persona, Profile, ChatLogger) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 6.0: 状态容器
        self.active_buffers = {}  # {session_id: [msg_list]}
        self.processing_sessions = set()
        self._lock = None  # 用于元编程写锁
        self.daily_reflection_pending = False

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

    @property
    def profile_slide_window(self):
        return int(self.config.get("profile_slide_window", 3))

    @property
    def enable_profile_update(self):
        return self._parse_bool(self.config.get("enable_profile_update"), True)

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
        session_id = event.session_id

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
        ai_context_info = ""
        at_targets = []

        for comp in event.get_messages():
            if type(comp).__name__ == "Reply":
                reply_sender = getattr(comp, "sender_nickname", "")
                reply_content = getattr(comp, "message_str", "")
                reply_sender_id = getattr(comp, "sender_id", "")

                # 检测是否引用了 AI 的消息
                if reply_sender == self.persona_name or str(reply_sender_id) == "AI":
                    # 从日志中查找 AI 最近的回复
                    if hasattr(self, "chat_logger"):
                        ai_replies = await self.chat_logger.get_ai_reply_for_context(
                            session_id
                        )
                        if ai_replies:
                            latest_ai_msg = ai_replies[0].get("content", "")
                            ai_context_info = (
                                f"\n【重要】用户正在引用你之前的发言进行追问：\n"
                                f"你之前说：{latest_ai_msg}\n"
                                f"请针对你之前的这句话回答用户的问题。"
                            )
                            quoted_info = f"，你在之前说：{latest_ai_msg[:50]}..."
                        else:
                            quoted_info = f"，你在之前说：{reply_content[:30]}..."
                            ai_context_info = "\n【重要】用户正在引用你之前的发言进行追问，请针对你之前的发言回答。"
                    else:
                        quoted_info = f"，你在之前说：{reply_content[:30]}..."
                        ai_context_info = "\n【重要】用户正在引用你之前的发言进行追问，请针对你之前的发言回答。"
                else:
                    quoted_info = f"，你正在回复用户 {reply_sender} 的消息：{reply_content[:30]}..."
            elif type(comp).__name__ == "At":
                at_targets.append(str(getattr(comp, "qq", "")))

        at_info = f"，消息中提到了: {', '.join(at_targets)}" if at_targets else ""

        # 构造上下文注入
        context_info = f"\n\n[当前交互上下文环境信息]：\n- 发送者ID: {sender_id}\n- 发送者昵称: {sender_name}{role_info}\n- 情感积分: {affinity}/100\n"
        if is_group:
            context_info += f"- 来源：群聊\n- 交互上下文: 你{quoted_info}{at_info}\n"
        else:
            context_info += "- 来源：私聊\n"

        # 注入 AI 上下文（如果用户引用了 AI 的话）
        if ai_context_info:
            context_info += ai_context_info

        context_info += (
            "\n【核心认知指令 - 身份隔离与动态心跳 (CognitionCore 6.0)】：\n"
            "1. 【重要】你只能看到当前这句话的内容，不要误以为之前群里的其他人的发言也是当前用户说的。\n"
            "2. 严格区分当前发送者与历史记录中其他群成员。不要因他人的恶意攻击当前发送者。\n"
            "3. 情感评分（当前:{affinity}/100）是动态的。请对当前发送者的【每一句话】进行实时情感归因评估。\n"
            "4. 即使分数已满，也请保持审视。若对方表现优秀请维持评分；若出现废话、无礼或违规，请果断调用 `update_affinity` 进行微调（建议波动范围: ±1~5）。\n"
            "5. 在回复引用内容时，请确保逻辑闭环，并明确回复对象。"
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
        await self.memory.auto_recall_inject(event, req)

        # 5. 用户画像注入 (Profile) - 合并到潜意识记忆区
        if self.enable_profile_update:
            profile_summary = await self.profile.get_profile_summary(user_id)
            if profile_summary:
                req.system_prompt += f"\n\n[当前发言用户画像 (Sender_ID: {user_id})]\n- {profile_summary}\n"

        # 6. 交流准则注入
        req.system_prompt += (
            "\n\n【交流准则】\n"
            "像平时在群里和朋友聊天一样自然地回复。\n"
            "用人类正常交流的语气，不需要机械性地解释系统机制。\n"
            "如果用户问的是你已经记住的信息，直接回答即可。"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 6.0: 被动监听转发至 EavesdroppingEngine"""
        # 自动学习触发：检测关键场景
        await self.memory.auto_learn_trigger(event)

        async for result in self.eavesdropping.handle_message(event):
            yield result

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

            # 注册画像清理任务（每天凌晨 4 点）
            cleanup_job_name = "SelfEvolution_ProfileCleanup"
            await cron_mgr.add_basic_job(
                name=cleanup_job_name,
                cron_expression="0 4 * * *",
                handler=self._scheduled_profile_cleanup,
                description="自我进化插件：清理过期用户画像。",
            )
            logger.info("[SelfEvolution] 已注册画像清理任务: 0 4 * * *")

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

    async def _scheduled_profile_cleanup(self):
        """画像清理定时任务"""
        logger.info("[Profile] 开始清理过期画像...")
        await self.profile.cleanup_expired_profiles()
        logger.info("[Profile] 画像清理完成。")

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
        """根据用户的言行调整其情感积分。

        Args:
            delta(number): 调整值（如 -10 表示冒犯, +5 表示赞赏）。积分跌至 0 将导致系统自动拦截。
            reason(string): 调整理由（必须说明用户具体哪项言行导致了积分变动）。
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
        """当你需要调整自己的语言风格或行为准则时，调用此工具来修改你的系统提示词。

        Args:
            new_system_prompt(string): 新的完整系统提示词
            reason(string): 修改理由
        """
        return await self.persona.evolve_persona(event, new_system_prompt, reason)

    @filter.command("review_evolutions")
    async def review_evolutions(self, event: AstrMessageEvent, page: int = 1):
        """【管理员接口】列出待审核的人格进化请求，支持分页查询。"""
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.review_evolutions(event, page))

    @filter.command("approve_evolution")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """【管理员接口】批准指定 ID 的人格进化请求。"""
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(
            await self.persona.approve_evolution(event, request_id)
        )

    @filter.command("reject_evolution")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """【管理员接口】拒绝指定 ID 的人格进化请求。"""
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.reject_evolution(event, request_id))

    @filter.command("clear_evolutions")
    async def clear_evolutions(self, event: AstrMessageEvent):
        """【管理员接口】一键清空所有待审核的进化请求。"""
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
        """当你发现了一些关于用户的重要的、需要永久记住的事实时，调用此工具将该事实存入你的长期记忆库。

        Args:
            fact(string): 需要记住的具体事实或信息
        """
        return await self.memory.commit_to_memory(event, fact)

    @filter.llm_tool(name="recall_memories")
    async def recall_memories(self, event: AstrMessageEvent, query: str) -> str:
        """当你需要回想起以前记住的事情、用户的偏好或过去的约定知识时，调用此工具。

        Args:
            query(string): 搜索关键词或问题
        """
        return await self.memory.recall_memories(event, query)

    @filter.llm_tool(name="learn_from_context")
    async def learn_from_context(
        self, event: AstrMessageEvent, key_info: str = ""
    ) -> str:
        """从当前对话中自动提取关键信息并存入长期记忆。

        Args:
            key_info(string): 需要记住的关键信息（如果留空，将自动提取当前对话中的关键内容）
        """
        return await self.memory.learn_from_context(event, key_info)

    @filter.llm_tool(name="clear_all_memory")
    async def clear_all_memory(
        self, event: AstrMessageEvent, confirm: bool = False
    ) -> str:
        """清空指定知识库中的所有记忆条目。谨慎使用！

        Args:
            confirm(boolean): 必须传入 true 才能执行清空操作（防止误操作）
        """
        return await self.memory.clear_all_memory(event, confirm)

    @filter.llm_tool(name="list_memories")
    async def list_memories(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """列出当前存储在知识库中的记忆条目。

        Args:
            limit(number): 最多显示的记忆条目数量，默认10条
        """
        return await self.memory.list_memories(event, limit)

    @filter.llm_tool(name="delete_memory")
    async def delete_memory(self, event: AstrMessageEvent, doc_id: str) -> str:
        """删除知识库中的单条记忆。

        Args:
            doc_id(string): 要删除的记忆条目ID
        """
        return await self.memory.delete_memory(event, doc_id)

    @filter.llm_tool(name="auto_recall")
    async def auto_recall(self, event: AstrMessageEvent, topic: str = "") -> str:
        """当检测到当前对话涉及历史记忆时，主动将相关记忆注入上下文。

        Args:
            topic(string): 当前对话涉及的话题关键词（如果留空，将使用当前消息内容）
        """
        return await self.memory.auto_recall(event, topic)

    @filter.llm_tool(name="save_group_knowledge")
    async def save_group_knowledge(
        self,
        event: AstrMessageEvent,
        knowledge: str,
        knowledge_type: str = "约定活动",
        source_uuids: list = None,
    ) -> str:
        """当群聊中出现具体的约定、重要群规或者集体共识时，立即调用此工具。严禁保存日常闲聊或毫无信息量的废话。

        触发场景：
        - 群主/管理员宣布群规
        - 群友约定活动时间/内容（如"今晚八点开会"）
        - 重要事件
        - 值得记住的群文化

        Args:
            knowledge(string): 用最简练的冷白描手法记录事实。必须包含明确的时间状语（如：今晚八点开会）。（必填）
            knowledge_type(string): 记忆的分类：群规/约定活动/群共识（默认约定活动）
            source_uuids(list): 必须提供触发记录的原始消息 UUID 列表，用于后期溯源。（必填）
        """
        return await self.memory.save_group_knowledge(
            event, knowledge, knowledge_type, source_uuids
        )

    @filter.llm_tool(name="list_tools")
    async def list_tools(self, event: AstrMessageEvent) -> str:
        """
        列出当前所有已注册的工具及其激活状态。
        """
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            tools = tool_mgr.func_list

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
        """动态激活或停用某个工具。

        Args:
            tool_name(string): 工具名称
            enable(boolean): True 表示激活，False 表示停用
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
        """Level 4: 元编程。读取本插件的源码，以便进行自我分析或修改请求。

        Args:
            mod_name(string): 模块名，可选: main, dao, eavesdropping, meta_infra, memory, persona
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
        """Level 4: 元编程。针对本插件提出代码修改建议。

        Args:
            new_code(string): 全新的、完整的 python 代码字符串
            description(string): 为什么要修改代码
            target_file(string): 目标文件名，默认 main.py
        """
        return await self.meta_infra.update_plugin_source(
            new_code, description, target_file
        )

    @filter.llm_tool(name="get_user_profile")
    async def get_user_profile(self, event: AstrMessageEvent) -> str:
        """获取当前用户的画像信息，了解用户的兴趣和性格特征。

        Returns:
            用户画像JSON字符串
        """
        user_id = event.get_sender_id()
        profile = await self.profile.load_profile(user_id)
        import json

        return json.dumps(profile, ensure_ascii=False, indent=2)

    @filter.command("view_profile")
    async def view_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """查看指定用户的画像信息。"""
        target = user_id if user_id else event.get_sender_id()
        yield event.plain_result(await self.profile.view_profile(target))

    @filter.command("delete_profile")
    async def delete_profile_cmd(self, event: AstrMessageEvent, user_id: str):
        """【管理员】删除指定用户的画像。"""
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        yield event.plain_result(await self.profile.delete_profile(user_id))

    @filter.command("profile_stats")
    async def profile_stats_cmd(self, event: AstrMessageEvent):
        """【管理员】查看画像统计信息。"""
        if not event.is_admin() and (
            not self.admin_users or str(event.get_sender_id()) not in self.admin_users
        ):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        stats = await self.profile.list_profiles()
        yield event.plain_result(
            f"画像统计：\n- 用户数: {stats['total_users']}\n- 兴趣标签: {stats['total_tags']}\n- 性格特征: {stats['total_traits']}"
        )
