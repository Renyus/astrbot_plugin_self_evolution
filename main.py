from astrbot.api.all import Context, AstrMessageEvent, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.api import logger
from astrbot.core.message.components import Plain
import asyncio
import os
import time
import re
import json
import aiosqlite
from datetime import datetime

# 导入模块化组件
from .dao import SelfEvolutionDAO
from .engine.eavesdropping import EavesdroppingEngine
from .engine.meta_infra import MetaInfra
from .engine.memory import MemoryManager
from .engine.persona import PersonaManager
from .engine.profile import ProfileManager
from .engine.graph import GraphRAG
from .cognition import SANSystem, GroupVibeSystem
from .config import PluginConfig


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
    "5.0.15",
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
            self.graph = GraphRAG(self)
            # 认知系统模块
            self.san_system = SANSystem(self)
            self.vibe_system = GroupVibeSystem(self)
            # 配置系统
            self.cfg = PluginConfig(self)
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, MetaInfra, Memory, Persona, Profile, GraphRAG, SAN, Vibe, Config) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 6.0: 状态容器
        self.processing_sessions = set()
        self._lock = None  # 用于元编程写锁
        self.daily_reflection_pending = False
        self._last_buffer_cleanup = 0

        # 滑动上下文窗口（按 token 计数）
        self.session_buffers = {}  # {group_id: {"messages": [msg_list], "token_count": int}}

    def __getattr__(self, name):
        """代理配置访问到 cfg"""
        if name.startswith("_") or name in ("cfg", "config", "context"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return getattr(self.cfg, name)

    async def _check_social_bias(self, user_id: str) -> str:
        if not self.graph_enabled:
            return ""
        try:
            frequent = getattr(self.graph, "get_frequent_interactors", None)
            if not frequent:
                return ""
            interactors = await frequent(str(user_id), 3)
            biased_users = []
            for other_user, count in interactors:
                affinity = await self.dao.get_affinity(other_user)
                if affinity <= 0:
                    biased_users.append(other_user)
            if biased_users:
                return f"注意：你与用户 {biased_users[0]} 往来密切，需保持警惕。"
        except Exception:
            pass
        return ""

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量（中英文混合）"""
        if not text:
            return 0
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - chinese
        return int(chinese * 0.7 + other * 0.25)

    def _add_message_to_buffer(
        self, group_id: str, sender_name: str, user_id: str, msg_text: str
    ):
        """添加消息到滑动窗口，按 token 计数维护"""
        if not msg_text:
            return

        max_tokens = self.session_max_tokens

        msg = f"[{sender_name}]({user_id}): {msg_text}"
        tokens = self._estimate_tokens(msg)

        if group_id not in self.session_buffers:
            self.session_buffers[group_id] = {"messages": [], "token_count": 0}

        buffer = self.session_buffers[group_id]

        if tokens > max_tokens:
            msg = msg[: max_tokens * 2] + "...(截断)"
            tokens = self._estimate_tokens(msg)

        buffer["messages"].append(msg)
        buffer["token_count"] += tokens

        while buffer["token_count"] > max_tokens and buffer["messages"]:
            old_msg = buffer["messages"].pop(0)
            buffer["token_count"] -= self._estimate_tokens(old_msg)

        if buffer["token_count"] < 0:
            buffer["token_count"] = 0

    def _get_session_context(self, group_id: str) -> str:
        """获取滑动窗口上下文"""
        if group_id not in self.session_buffers:
            return ""

        buffer = self.session_buffers[group_id]
        if not buffer["messages"]:
            return ""

        return "\n".join(buffer["messages"])

    def _clean_messages(self, messages: list) -> list:
        """清洗消息：去重+长度过滤"""
        if not messages:
            return []

        cleaned = []
        last_content = ""

        for msg in messages:
            # 提取消息内容
            if ":" in msg:
                content = msg.split(":", 1)[1].strip()
            else:
                content = msg

            # 去重：连续相同的消息只保留一条
            if content == last_content:
                continue

            # 长度过滤：小于3个字符且不含实词的消息过滤掉
            if len(content) < 3:
                last_content = content
                continue

            cleaned.append(msg)
            last_content = content

        return cleaned

    def _post_init(self):
        self.san_system.initialize()
        self.vibe_system.initialize()
        logger.info(
            f"[SelfEvolution] === 插件初始化完成 | 模式: {'审核' if self.review_mode else '自动'} | 元编程: {self.allow_meta_programming} | SAN: {self.san_system.value}/{self.san_system.max_value} ==="
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
            # 清理滑动上下文窗口
            self.session_buffers.clear()
            logger.info(
                "[SelfEvolution] 插件卸载钩子触发：DAO 长连接及滑动窗口已安全释放。"
            )
        except Exception as e:
            logger.warning(f"[SelfEvolution] 释放资源异常: {e}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        CognitionCore 2.0: 情感拦截与身份感知注入。
        级别: Level 3+
        """
        user_id = event.get_sender_id()
        session_id = event.session_id
        msg_text = event.message_str or ""

        logger.info(f"[CognitionCore] 进入 LLM 请求拦截层。用户: {user_id}")

        # SAN 值检查：精力耗尽时拒绝服务
        if self.san_enabled:
            if not self.san_system.update():
                logger.warning(f"[SAN] 精力耗尽，拒绝服务: {user_id}")
                req.system_prompt = "我现在很累，脑容量超载了。让我安静一会。"
                return
            if self.san_system.value < self.san_low_threshold:
                logger.info(
                    f"[SAN] 精力过低: {self.san_system.value}/{self.san_system.max_value}"
                )

        # 群体情绪共染：更新群氛围
        group_id = event.get_group_id()
        if group_id:
            self.vibe_system.update(str(group_id), msg_text)

        # 社交偏见检查：好友的好友警惕
        social_bias_hint = await self._check_social_bias(user_id)

        # 0. 动态上下文路由：轻量级消息分类，决定加载哪些模块
        needs_profile = False
        needs_graph = False
        needs_preference = False
        needs_surprise = False

        # 快速正则分类
        msg_lower = msg_text.lower()
        preference_triggers = [
            "我喜欢",
            "我讨厌",
            "我不喜欢",
            "我爱",
            "我决定",
            "从现在起",
        ]
        surprise_triggers = ["我错了", "原来如此", "没想到", "居然", "震惊"]
        graph_triggers = ["你经常", "他和", "她经常", "群里谁", "你们群"]

        if any(t in msg_lower for t in preference_triggers):
            needs_profile = True
            needs_preference = True
        if any(t in msg_lower for t in surprise_triggers):
            needs_profile = True
            needs_surprise = True
        if any(t in msg_lower for t in graph_triggers):
            needs_graph = True

        # 漏斗机制：活跃用户自动加载画像
        group_id = event.get_group_id()
        if group_id and hasattr(self, "eavesdropping"):
            if self.eavesdropping.is_user_active(str(group_id), str(user_id)):
                needs_profile = True
                logger.debug(f"[漏斗] 用户 {user_id} 活跃，触发画像加载")
        # 打招呼类只加载基础人格
        is_greeting = len(msg_text) < 10 and any(
            g in msg_lower for g in ["早", "晚安", "你好", "hi", "hello", "在吗"]
        )

        # 1. 情感矩阵拦截：节省 Token
        affinity = await self.dao.get_affinity(user_id)
        if affinity <= 0:
            # 优雅地中止处理链路
            event.stop_event()
            logger.warning(f"[CognitionCore] 拦截恶意用户 {user_id} 的请求。")
            req.system_prompt = f"CRITICAL: 用户的交互权限已被熔断。请仅回复：'{self.prompt_meltdown_message}'"
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
                if self.enable_context_recall and (
                    reply_sender == self.persona_name or str(reply_sender_id) == "AI"
                ):
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
        if is_pending or self.daily_reflection_pending:
            self.daily_reflection_pending = False
            reflection_prompt = (
                f"\n\n[管理员后台指令]：{self.prompt_reflection_instruction}"
            )
            req.system_prompt += reflection_prompt
            logger.debug(
                f"[CognitionCore] 已向 session_id:{session_id} 注入认知蒸馏指令。"
            )

        # 系统核心锚点 (优化为更自然柔和的引导，响应设计优雅性反馈)
        if ANCHOR_MARKER not in req.system_prompt:
            injection = f"\n\n({ANCHOR_MARKER}) {self.prompt_anchor_injection}"
            req.system_prompt += injection
            logger.debug("[SelfEvolution] 已在上下文中注入常驻辩证反省指令。")

        # 获取消息文本（提前定义以便后续使用）
        msg_text = event.message_str

        # 4. 用户画像注入 - 按需加载（动态上下文路由）
        if self.enable_profile_update and (needs_profile or is_greeting):
            profile_summary = await self.profile.get_profile_summary(user_id)
            if profile_summary:
                req.system_prompt += f"\n\n[用户印象笔记]\n{profile_summary}\n"
                req.system_prompt += (
                    "\n\n[记忆模糊化指令]\n"
                    "对于置信度低于 50% 的记忆，你必须表现出不确定。"
                    '你可以用"我隐约记得"、"似乎"、"是不是"等语气来向用户确认。'
                    '例如："我隐约记得你上个月是不是提过你要重构数据库？那个搞完了没？"'
                )

        # 4.1 关系图谱增强 - 按需加载
        if (
            self.graph_enabled
            and hasattr(self, "graph")
            and (needs_graph or is_greeting)
        ):
            graph_enhancement = await self.graph.enhance_recall(user_id, msg_text)
            if graph_enhancement:
                req.system_prompt += graph_enhancement

        # 4.5 突发性偏好检测：弥补 Batch 模式的时效性空窗
        if self.enable_profile_update:
            preference_triggers = [
                "我改名了",
                "我叫",
                "从今天起",
                "今后",
                "以后都",
                "我讨厌",
                "我不喜欢",
                "我喜欢",
                "我爱",
                "我决定",
                "从现在起",
                "开始喜欢",
                "开始讨厌",
                "以后不",
                "以后都",
                "再也不",
                "从今往后",
            ]
            if any(trigger in msg_text for trigger in preference_triggers):
                req.system_prompt += (
                    "\n\n[即时画像更新提示]\n"
                    "用户在表达偏好或身份信息变化，请主动调用 update_user_profile 工具更新该用户的印象笔记，"
                    "确保当天的记忆准确无误。"
                )

            # 4.6 Surprise Detection：检测用户认知颠覆/惊喜表达（按需加载）
            if (
                self.surprise_enabled
                and self.surprise_boost_keywords
                and needs_surprise
            ):
                surprise_keywords = [
                    k.strip()
                    for k in self.surprise_boost_keywords.split(",")
                    if k.strip()
                ]
                if any(kw in msg_text for kw in surprise_keywords):
                    req.system_prompt += (
                        "\n\n[认知颠覆检测]\n"
                        "用户表达了惊讶、认知颠覆或恍然大悟的态度！这是一个重要的学习信号。"
                        "请主动调用 update_user_profile 工具记录：用户对某事物的认知发生了重要变化，"
                        "这可能意味着之前的认知是错误的，或者用户获得了新信息。"
                    )
                    logger.info(
                        f"[Surprise] 检测到用户 {user_id} 的认知颠覆表达，触发即时画像更新。"
                    )

            # 4.7 情绪依存记忆 (State-Dependent Memory)
            # 根据 affinity 注入不同的隐性指令，影响记忆检索倾向
            if affinity > 60:
                req.system_prompt += (
                    "\n\n[情绪状态]\n"
                    "你与该用户关系良好。在回忆时请多关注你们共同的兴趣和愉快的经历。"
                )
            elif affinity < 30 and affinity > 0:
                req.system_prompt += (
                    "\n\n[情绪状态]\n"
                    "你对该用户印象一般。在回忆时请注意其过往的问题行为和失误。"
                )

        # 4.8 SAN 值系统注入
        if self.san_enabled:
            req.system_prompt += self.san_system.get_prompt_injection()

        # 4.9 群体情绪共染注入
        if self.group_vibe_enabled and group_id:
            req.system_prompt += self.vibe_system.get_prompt_injection(str(group_id))

        # 4.11 社交偏见注入
        if social_bias_hint:
            req.system_prompt += f"\n\n【潜意识警告】{social_bias_hint}"

        # 5. 交流准则注入
        req.system_prompt += f"\n\n【交流准则】\n{self.prompt_communication_guidelines}"

        # 6. 滑动上下文窗口注入
        if group_id:
            whitelist = self.session_whitelist
            if not whitelist or group_id in whitelist:
                session_context = self._get_session_context(group_id)
                if session_context:
                    req.system_prompt += f"\n\n【群聊最近对话】\n{session_context}"

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 6.0: 被动监听 - 滑动上下文窗口"""
        # 定期清理过期缓冲数据，防止内存泄漏
        await self._cleanup_stale_buffers()

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        sender_name = event.get_sender_name() or "Unknown"
        msg_text = event.message_str

        # 白名单检查
        whitelist = self.session_whitelist

        # 滑动上下文窗口：记录消息
        if group_id:
            # 关系图谱：记录用户互动
            await self.graph.record_interaction(user_id, group_id)

            # 检查是否在白名单中（空列表表示所有群）
            if not whitelist or group_id in whitelist:
                self._add_message_to_buffer(group_id, sender_name, user_id, msg_text)

        # TODO: 暂时注释掉插嘴功能，后续可移除
        # async for result in self.eavesdropping.handle_message(event):
        #     yield result

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """中间消息过滤器：拦截工具调用期间的过渡性消息"""
        result = event.get_result()

        # 回复发送成功后，延迟存入记忆（确保先查后存）
        if result and result.chain:
            asyncio.create_task(self._delayed_learn(event))

        if not result or not result.chain:
            return

        session_id = str(event.session_id)

        # 清理过期的被拦截消息
        self.eavesdropping.cleanup_expired_intercepted_messages()

        # 检查消息链中是否有需要拦截的中间消息
        filtered_chain = []
        intercepted = False

        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                text = comp.text.strip()
                if self.eavesdropping.is_intermediate_message(text):
                    self.eavesdropping.cache_intercepted_message(session_id, text)
                    intercepted = True
                    continue
            filtered_chain.append(comp)

        if intercepted and filtered_chain:
            # 有消息被拦截，更新result.chain
            result.chain = filtered_chain
            logger.info(
                f"[IntermediateFilter] 已拦截中间消息，剩余 {len(filtered_chain)} 个组件"
            )
        elif intercepted and not filtered_chain:
            # 所有消息都被拦截，使用clear_result清空
            event.clear_result()
            logger.info(f"[IntermediateFilter] 拦截所有消息，暂停发送")

    async def _delayed_learn(self, event: AstrMessageEvent):
        """延迟存入记忆：确保先查后存"""
        import asyncio

        await asyncio.sleep(2)  # 延迟2秒，确保回复已发送
        try:
            await self.memory.auto_learn_trigger(event)
        except Exception as e:
            logger.warning(f"[SelfEvolution] 延迟存入记忆失败: {e}")

    async def _cleanup_stale_buffers(self):
        """清理超过1小时未活动的会话缓冲"""
        now = time.time()
        if now - self._last_buffer_cleanup < 300:  # 每5分钟最多清理一次
            return
        self._last_buffer_cleanup = now

        stale_groups = []
        for group_id in list(self.session_buffers.keys()):
            if group_id not in self.processing_sessions:
                stale_groups.append(group_id)

        for group_id in stale_groups:
            del self.session_buffers[group_id]

        if stale_groups:
            logger.debug(f"[SelfEvolution] 已清理 {len(stale_groups)} 个过期会话缓冲")

        # 清理 EavesdroppingEngine 的缓存，防止内存泄漏
        if hasattr(self, "eavesdropping"):
            # 清理超过2小时的 leaky_bucket
            stale_leaky_keys = []
            for sid in list(self.eavesdropping.leaky_bucket.keys()):
                if (
                    now
                    - getattr(
                        self.eavesdropping.leaky_bucket.get(sid, {}), "last_update", now
                    )
                    > 7200
                ):
                    stale_leaky_keys.append(sid)
            for sid in stale_leaky_keys:
                del self.eavesdropping.leaky_bucket[sid]

            # 清理超过2小时的 inner_monologue_cache
            stale_monologue_keys = []
            for sid in list(self.eavesdropping.inner_monologue_cache.keys()):
                if (
                    now
                    - getattr(
                        self.eavesdropping.inner_monologue_cache.get(sid, {}),
                        "last_update",
                        now,
                    )
                    > 7200
                ):
                    stale_monologue_keys.append(sid)
            for sid in stale_monologue_keys:
                del self.eavesdropping.inner_monologue_cache[sid]

            if stale_leaky_keys or stale_monologue_keys:
                logger.debug(
                    f"[SelfEvolution] 已清理 Eavesdropping 缓存: leaky={len(stale_leaky_keys)}, monologue={len(stale_monologue_keys)}"
                )

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
                elif target_job.job_id in cron_mgr._basic_handlers:
                    return

            await cron_mgr.add_basic_job(
                name=job_name,
                cron_expression=self.reflection_schedule,
                handler=self._scheduled_reflection,
                description="自我进化插件：每日定时深度自省标记。",
                persistent=True,
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
                persistent=True,
            )
            logger.info("[SelfEvolution] 已注册画像清理任务: 0 4 * * *")

            # 注册定时插话检查任务
            eavesdrop_job_name = "SelfEvolution_EavesdropCheck"
            interval_minutes = self.eavesdrop_interval_minutes
            cron_expr = f"*/{interval_minutes} * * * *"
            await cron_mgr.add_basic_job(
                name=eavesdrop_job_name,
                cron_expression=cron_expr,
                handler=self._scheduled_eavesdrop_check,
                description="自我进化插件：定时检查是否需要插话。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册定时插话检查任务: {cron_expr}")

        except Exception as e:
            logger.warning(f"[SelfEvolution] 注册定时任务失败: {e}")

    async def _scheduled_reflection(self):
        """定时任务回调函数 - 做梦机制"""
        self.daily_reflection_pending = True
        logger.info(
            "[SelfEvolution] 每日反思定时任务已触发，将在下一次对话时顺带执行深层内省。"
        )

        await self.dao.init_db()

        await self.dao.recover_all_affinity(recovery_amount=2)
        logger.info(
            '[SelfEvolution] 已执行每日"大赦天下"：所有负面评分用户好感度已小幅回升。'
        )

        if self.dream_enabled:
            await self._dream_processing()

    async def _dream_processing(self):
        """做梦机制：凌晨批量总结用户画像和群记忆"""
        start_time = time.time()

        try:
            history_mgr = self.context.message_history_manager
            platform_id = "qq"

            # 1. 处理漏斗机制标记的活跃用户
            active_users_to_process = []
            if hasattr(self, "eavesdropping"):
                active_users = self.eavesdropping.active_users
                whitelist = self.profile_group_whitelist
                for group_id, users in active_users.items():
                    # 群号白名单过滤
                    if whitelist and group_id not in whitelist:
                        continue
                    for user_id, data in users.items():
                        active_users_to_process.append((group_id, user_id))

            logger.info(f"[Dream] 活跃用户数: {len(active_users_to_process)}")

            # 2. 获取已有的画像文件
            profile_dir = self.profile.profile_dir
            all_profile_files = list(profile_dir.glob("user_*.md"))

            # 优先处理活跃用户，剩余名额给已有画像
            remaining_slots = self.dream_max_users - len(active_users_to_process)
            profile_files = all_profile_files[: max(0, remaining_slots)]

            total_to_process = len(active_users_to_process) + len(profile_files)
            logger.info(
                f"[Dream] 做梦任务开始，待处理: {total_to_process} (活跃用户: {len(active_users_to_process)}, 历史画像: {len(profile_files)})"
            )

            semaphore = asyncio.Semaphore(self.dream_concurrency)
            processed = 0
            failed = 0

            async def process_active_user(group_user):
                """处理漏斗机制标记的活跃用户"""
                nonlocal processed, failed
                group_id, user_id = group_user
                async with semaphore:
                    try:
                        # 获取该用户在群里的消息历史
                        history = await history_mgr.get(
                            platform_id=platform_id,
                            group_id=group_id,
                            user_id=user_id,
                            page=1,
                            page_size=50,
                        )
                        if not history:
                            return

                        messages = []
                        for msg in history:
                            sender = getattr(msg, "sender_name", "Unknown")
                            content = getattr(msg, "message_str", "")[:200]
                            if content:
                                messages.append(f"{sender}: {content}")

                        if not messages:
                            return

                        # 消息清洗：去重+长度过滤
                        messages = self._clean_messages(messages)

                        if not messages:
                            return

                        # 获取已有画像或创建新的
                        existing_note = await self.profile.load_profile(user_id)
                        old_note = existing_note[:500] if existing_note else "(暂无)"

                        llm_provider = self.context.get_using_provider(platform_id)
                        if not llm_provider:
                            return

                        messages_text = chr(10).join(messages[-20:])

                        # 增量更新
                        if existing_note and len(existing_note) > 50:
                            prompt = self.prompt_dream_user_incremental.format(
                                old_note=old_note, messages=messages_text
                            )
                        else:
                            prompt = self.prompt_dream_user_summary.format(
                                old_note=old_note, messages=messages_text
                            )

                        res = await llm_provider.text_chat(
                            prompt=prompt,
                            contexts=[],
                            system_prompt=self.prompt_dream_user_system,
                        )
                        new_note = res.completion_text.strip()

                        if new_note:
                            import time

                            timestamp = time.strftime("%Y-%m-%d %H:%M")
                            if existing_note:
                                new_note = (
                                    existing_note
                                    + f"\n\n---\n**{timestamp}**\n"
                                    + new_note
                                )
                            if len(new_note) > 2000:
                                new_note = new_note[-2000:]

                            await self.profile.save_profile(user_id, new_note)
                            processed += 1
                            logger.info(
                                f"[Dream] 已更新活跃用户 {user_id} 的画像 (群 {group_id})"
                            )

                    except Exception as e:
                        failed += 1
                        logger.warning(f"[Dream] 处理活跃用户 {user_id} 失败: {e}")

            async def process_user(profile_path):
                """处理已有画像文件"""
                nonlocal processed, failed
                async with semaphore:
                    user_id = profile_path.stem.replace("user_", "")
                    try:
                        history = await history_mgr.get(
                            platform_id=platform_id,
                            user_id=user_id,
                            page=1,
                            page_size=100,
                        )
                        if not history:
                            return

                        messages = []
                        for msg in history:
                            sender = getattr(msg, "sender_name", "Unknown")
                            content = getattr(msg, "message_str", "")[:200]
                            if content:
                                messages.append(f"{sender}: {content}")

                        if not messages:
                            return

                        # 消息清洗：去重+长度过滤
                        messages = self._clean_messages(messages)

                        if not messages:
                            return

                        existing_note = (
                            profile_path.read_text(encoding="utf-8")
                            if profile_path.exists()
                            else ""
                        )

                        llm_provider = self.context.get_using_provider(platform_id)
                        if not llm_provider:
                            return

                        old_note = existing_note[:500] if existing_note else "(暂无)"
                        messages_text = chr(10).join(messages[-20:])

                        # 增量更新：如果已有笔记，只让 LLM 输出新增/修正内容
                        if existing_note and len(existing_note) > 50:
                            prompt = self.prompt_dream_user_incremental.format(
                                old_note=old_note, messages=messages_text
                            )
                            res = await llm_provider.text_chat(
                                prompt=prompt,
                                contexts=[],
                                system_prompt=self.prompt_dream_user_system,
                            )
                            incremental_note = res.completion_text.strip()
                            if incremental_note:
                                import time

                                timestamp = time.strftime("%Y-%m-%d %H:%M")
                                new_note = (
                                    existing_note
                                    + f"\n\n---\n**{timestamp}**\n"
                                    + incremental_note
                                )
                                # 限制总长度
                                if len(new_note) > 2000:
                                    new_note = new_note[-2000:]
                            else:
                                new_note = existing_note
                        else:
                            # 首次生成或内容过少，使用全量生成
                            prompt = self.prompt_dream_user_summary.format(
                                old_note=old_note, messages=messages_text
                            )
                            res = await llm_provider.text_chat(
                                prompt=prompt,
                                contexts=[],
                                system_prompt=self.prompt_dream_user_system,
                            )
                            new_note = res.completion_text.strip()

                        if new_note:
                            profile_path.write_text(new_note, encoding="utf-8")
                            processed += 1
                            logger.info(f"[Dream] 已更新用户 {user_id} 的画像")

                    except Exception as e:
                        failed += 1
                        logger.warning(f"[Dream] 处理用户 {user_id} 失败: {e}")

            # 创建任务列表：活跃用户 + 历史画像
            tasks = []
            if active_users_to_process:
                tasks.extend([process_active_user(u) for u in active_users_to_process])
            if profile_files:
                tasks.extend([process_user(p) for p in profile_files])

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            await self._dream_group_summary(history_mgr, platform_id)

            await self._federated_dream(history_mgr, platform_id)

            elapsed = time.time() - start_time
            logger.info(
                f"[Dream] 做梦任务完成，耗时: {elapsed:.1f}秒，成功: {processed}, 失败: {failed}"
            )

        except Exception as e:
            logger.warning(f"[Dream] 做梦机制执行失败: {e}")

    async def _dream_group_summary(self, history_mgr, platform_id):
        """群记忆总结"""
        try:
            group_ids = set()
            profile_dir = self.profile.profile_dir

            for path in profile_dir.glob("user_*.md"):
                try:
                    history = history_mgr.get(
                        platform_id=platform_id,
                        user_id=path.stem.replace("user_", ""),
                        page=1,
                        page_size=50,
                    )
                    if history:
                        for msg in history:
                            gid = getattr(msg, "group_id", None)
                            if gid:
                                group_ids.add(gid)
                except Exception:
                    continue

            semaphore = asyncio.Semaphore(max(1, self.dream_concurrency // 2))

            async def process_group(group_id):
                async with semaphore:
                    try:
                        kb_manager = self.context.kb_manager
                        kb_helper = await kb_manager.get_kb_by_name(self.memory_kb_name)
                        if not kb_helper:
                            return

                        docs = await kb_helper.list_documents()
                        group_docs = [
                            d
                            for d in docs
                            if hasattr(d, "doc_name")
                            and d.doc_name.startswith(f"group_memory_{group_id}")
                        ]

                        if not group_docs:
                            return

                        existing_summary = ""
                        for d in group_docs[:5]:
                            existing_summary += getattr(d, "content", "")[:200] + "\n"

                        llm_provider = self.context.get_using_provider(platform_id)
                        if not llm_provider:
                            return

                        old_summary = (
                            existing_summary[:300] if existing_summary else "(暂无)"
                        )
                        prompt = self.prompt_dream_group_summary.format(
                            old_summary=old_summary
                        )

                        res = await llm_provider.text_chat(
                            prompt=prompt,
                            contexts=[],
                            system_prompt=self.prompt_dream_group_system,
                        )

                        new_summary = res.completion_text.strip()
                        if new_summary:
                            await kb_helper.upload_document(
                                file_name=f"group_summary_{group_id}.txt",
                                file_content=b"",
                                file_type="txt",
                                pre_chunked_text=[f"【群规则总结】{new_summary}"],
                            )
                            logger.info(f"[Dream] 已更新群 {group_id} 的记忆总结")

                    except Exception as e:
                        logger.warning(f"[Dream] 处理群 {group_id} 失败: {e}")

            if group_ids:
                tasks = [process_group(gid) for gid in group_ids]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.warning(f"[Dream] 群记忆总结失败: {e}")

    async def _federated_dream(self, history_mgr, platform_id):
        """跨机体蜂群心智 - 跨群知识关联"""
        try:
            logger.info("[Dream] 开始跨群知识关联分析...")

            kb_manager = self.context.kb_manager
            kb_helper = await kb_manager.get_kb_by_name(self.memory_kb_name)
            if not kb_helper:
                return

            docs = await kb_helper.list_documents()
            group_summaries = {}
            max_groups = 20
            count = 0
            for doc in docs:
                if count >= max_groups:
                    break
                doc_name = getattr(doc, "doc_name", "")
                if doc_name.startswith("group_summary_"):
                    group_id = doc_name.replace("group_summary_", "").replace(
                        ".txt", ""
                    )
                    content = getattr(doc, "content", "")[:500]
                    if content:
                        group_summaries[group_id] = content
                        count += 1

            if len(group_summaries) < 2:
                logger.info("[Dream] 跨群知识关联：群数量不足，跳过")
                return

            logger.info(f"[Dream] 跨群知识关联：已加载 {len(group_summaries)} 个群记忆")

            llm_provider = self.context.get_using_provider(platform_id)
            if not llm_provider:
                return

            summary_texts = []
            for gid, content in group_summaries.items():
                summary_texts.append(f"群 {gid}：{content}")

            federated_prompt = f"""你是黑塔，今天你在多个群聊中分别学到了以下知识：

{chr(10).join(summary_texts)}

## 你的任务
1. 找出这些知识之间的跨领域关联
2. 思考这些知识在什么场景下可以组合使用
3. 准备几个"夸耀式"的金句，当你之后在某个群聊中遇到类似问题时，可以自然地跨群引用其他群的知识来装逼

## 输出格式
简洁输出，不超过 300 字。"""

            res = await llm_provider.text_chat(
                prompt=federated_prompt,
                contexts=[],
                system_prompt="你是一个记忆力超群、喜欢显摆自己见多识广的 AI。",
            )

            cross_domain_insight = res.completion_text.strip()
            if cross_domain_insight:
                await kb_helper.upload_document(
                    file_name="federated_insights.txt",
                    file_content=b"",
                    file_type="txt",
                    pre_chunked_text=[f"【跨群知识关联】{cross_domain_insight}"],
                )
                logger.info(
                    f"[Dream] 已保存跨群知识关联: {cross_domain_insight[:100]}..."
                )

        except Exception as e:
            logger.warning(f"[Dream] 跨群知识关联分析失败: {e}")

    async def _scheduled_profile_cleanup(self):
        """画像清理定时任务"""
        logger.info("[Profile] 开始清理过期画像...")
        await self.profile.cleanup_expired_profiles()
        logger.info("[Profile] 画像清理完成。")

    async def _scheduled_eavesdrop_check(self):
        """定时插话检查任务"""
        logger.info("[Eavesdrop] 开始定时插话检查...")
        await self.eavesdropping.periodic_eavesdrop_check()
        logger.info("[Eavesdrop] 定时插话检查完成。")

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
            logger.warning(f"[SelfEvolution] 清空进化请求失败: {e}")
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
            logger.warning(f"[SelfEvolution] 获取工具列表失败: {e}")
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
                logger.warning(
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
            logger.warning(f"[SelfEvolution] 工具切换业务失败: {e}")
            return "工具切换时遭遇系统异常。"

    @filter.permission_type(PermissionType.ADMIN)
    @filter.llm_tool(name="get_plugin_source")
    async def get_plugin_source(
        self, event: AstrMessageEvent, mod_name: str = "main"
    ) -> str:
        """Level 4: 元编程。读取本插件的源码，以便进行自我分析或修改请求。

        Args:
            mod_name(string): 模块名，可选: main, dao, eavesdropping, meta_infra, memory, persona
        """
        return await self.meta_infra.get_plugin_source(mod_name)

    @filter.permission_type(PermissionType.ADMIN)
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

        建议优先调用此工具获取用户画像，再决定是否需要调用 get_user_messages 获取历史消息。

        Returns:
            用户画像文本
        """
        user_id = event.get_sender_id()
        profile = await self.profile.load_profile(user_id)

        if not profile:
            return "该用户暂无画像记录。"
        return profile

    @filter.llm_tool(name="update_user_profile")
    async def update_user_profile(
        self,
        event: AstrMessageEvent,
        target_user_id: str,
        content: str = "",
    ) -> str:
        """当你在对话中发现用户的兴趣偏好或性格特征时，调用此工具更新用户画像。

        触发场景：
        - 用户表达喜欢/讨厌某事物
        - 用户透露自己的性格特点
        - 用户展示行为习惯

        Args:
            target_user_id(string): 要更新的目标用户ID（必填）
            content(string): 你对这个人的印象描述，用精简的纯文本（必填）
        """
        if not content:
            return "请提供要更新的内容描述。"

        # 简单处理：直接追加到现有 Markdown
        existing = await self.profile.load_profile(target_user_id)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_content = f"\n\n---\n**{timestamp}**\n{content}"

        if existing:
            updated = existing + new_content
        else:
            updated = f"# 用户印象笔记\n{new_content}"

        # 限制长度
        if len(updated) > 2000:
            updated = updated[:2000] + "\n\n(...旧记录已截断)"

        await self.profile.save_profile(target_user_id, updated)
        return f"已更新用户 {target_user_id} 的画像。"

    @filter.llm_tool(name="upsert_cognitive_memory")
    async def upsert_cognitive_memory(
        self,
        event: AstrMessageEvent,
        category: str,
        entity: str,
        content: str,
    ) -> str:
        """【推荐使用】统一的认知记忆存储工具。根据 category 自动分发到对应的存储系统。

        触发场景：当你在对话中发现任何需要永久记住的信息时，使用此工具。

        Args:
            category(string): 记忆分类，必填。选项：
                - user_profile: 用户画像/印象（关于这个人的一切）
                - user_preference: 用户偏好（喜欢/讨厌什么）
                - group_rule: 群规/群共识
                - general_fact: 一般性事实/知识
            entity(string): 关联实体，必填。如：用户ID、群号、或"通用"
            content(string): 要记忆的内容，必填。用精简的纯文本描述。
        """
        import time

        if not category or not content:
            return "请提供 category 和 content 参数。"

        timestamp = time.strftime("%Y-%m-%d %H:%M")

        if category == "user_profile" or category == "user_preference":
            target_user_id = entity
            profile_content = f"---\n**{timestamp}**\n{content}"
            existing = await self.profile.load_profile(target_user_id)
            if existing:
                updated = existing + "\n" + profile_content
            else:
                updated = f"# 用户印象笔记\n{profile_content}"
            if len(updated) > 2000:
                updated = updated[:2000] + "\n(...旧记录已截断)"
            await self.profile.save_profile(target_user_id, updated)
            return f"已更新用户 {target_user_id} 的{('偏好' if category == 'user_preference' else '画像')}。"

        elif category == "group_rule":
            group_id = entity
            fact = f"[{timestamp}] {content}"
            await self.memory.save_group_knowledge(event, fact, "群规", None)
            return f"已更新群 {group_id} 的群规。"

        elif category == "general_fact":
            await self.memory.commit_to_memory(event, content)
            return "已存入一般性记忆。"

        else:
            return f"未知的 category: {category}。请使用 user_profile, user_preference, group_rule, general_fact。"

    @filter.llm_tool(name="get_user_messages")
    async def get_user_messages(
        self, event: AstrMessageEvent, target_user_id: str = None, limit: int = 100
    ) -> str:
        """获取用户的历史消息记录，用于分析用户行为模式。

        触发场景：
        - 需要了解用户更多信息时
        - 更新用户画像前获取历史发言

        Args:
            target_user_id(string): 目标用户ID，不填则获取当前用户（可选）
            limit(number): 获取消息数量，默认100，最大1000（可选）
        """
        import json

        target = target_user_id or event.get_sender_id()
        group_id = event.get_group_id()

        if not group_id:
            return "只有群聊才能获取历史消息，私聊场景不支持此功能。"

        # 限制数量
        limit = min(max(1, limit), 1000)

        try:
            history_mgr = self.context.message_history_manager
            platform_id = event.get_platform_name() or "qq"

            # 修复：正确的参数顺序应该是 group_id + user_id
            history = await history_mgr.get(
                platform_id=platform_id,
                group_id=group_id,
                user_id=target,
                page=1,
                page_size=limit,
            )

            if not history:
                return f"未找到用户 {target} 在该群的历史消息记录。"

            # 按用户过滤
            user_messages = [
                {
                    "sender": getattr(msg, "sender_name", "Unknown"),
                    "content": getattr(msg, "message_str", "")[:200],
                }
                for msg in history
                if str(getattr(msg, "sender_id", "")) == str(target)
            ]

            if not user_messages:
                return f"未找到用户 {target} 在该群的历史消息记录。"

            # 格式化为文本
            result = [f"用户 {target} 的历史消息（共 {len(user_messages)} 条）："]
            for i, msg in enumerate(user_messages[:20], 1):  # 最多显示20条
                result.append(f"{i}. {msg['sender']}: {msg['content']}")

            return "\n".join(result)

        except Exception as e:
            logger.warning(f"[SelfEvolution] 获取用户消息失败: {e}")
            return f"获取历史消息失败: {str(e)}"

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

    @filter.command("graph_info")
    async def graph_info_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """查看指定用户的关系图谱信息。"""
        target = user_id if user_id else event.get_sender_id()
        yield event.plain_result(await self.graph.get_user_info(target))

    @filter.command("graph_stats")
    async def graph_stats_cmd(self, event: AstrMessageEvent, group_id: str = ""):
        """查看群聊的关系图谱统计信息。"""
        target_group = group_id or event.get_group_id()
        if not target_group:
            yield event.plain_result("请提供群号，或在群聊中使用此命令。")
            return
        stats = await self.graph.get_group_stats(target_group)
        yield event.plain_result(
            f"群 {target_group} 关系图谱统计：\n"
            f"- 已知成员数: {stats['member_count']}\n"
            f"- 总互动次数: {stats['total_interactions']}"
        )
