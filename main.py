import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import yaml

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent, Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.core.message.components import Plain

from . import commands
from .cognition import SANSystem
from .config import PluginConfig

# 导入模块化组件
from .dao import SelfEvolutionDAO
from .engine.context_injection import build_identity_context, get_group_history, parse_message_chain
from .engine.eavesdropping import EavesdroppingEngine
from .engine.entertainment import EntertainmentEngine
from .engine.event_context import extract_interaction_context
from .engine.message_normalization import ensure_event_message_text
from .engine.memory import MemoryManager
from .engine.memory_router import MemoryRouter
from .engine.meta_infra import MetaInfra
from .engine.persona import PersonaManager
from .engine.profile import ProfileManager
from .engine.sticker_store import StickerStore
from .scheduler.register import register_tasks

PROTECTED_TOOLS = frozenset(
    {
        "toggle_tool",
        "list_tools",
        "evolve_persona",
        "review_evolutions",
        "approve_evolution",
    }
)
PRIVATE_SCOPE_PREFIX = "private_"


@dataclass
class PromptContext:
    """Prompt 构建时的运行时上下文，所有 builder 共享"""

    user_id: str
    sender_name: str
    group_id: str | None
    scope_id: str
    profile_scope_id: str
    umo: str | None
    msg_text: str
    affinity: int
    role_info: str
    is_group: bool
    quoted_info: str
    ai_context_info: str
    at_targets: list[str]
    at_info: str
    has_reply: bool
    has_at: bool
    bot_id: str
    event: AstrMessageEvent | None = field(default=None)


@register(
    "astrbot_plugin_self_evolution",
    "自我进化 (Self-Evolution)",
    "CognitionCore 7.0 数字生命。",
    "Ver 3.1.1",
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

    def _get_bot_id(self) -> str:
        """获取当前机器人的ID"""
        try:
            platform_insts = self.context.platform_manager.platform_insts
            if platform_insts:
                platform = platform_insts[0]
                return str(getattr(platform, "client_self_id", "") or "")
        except Exception:
            pass
        return ""

    def _resolve_profile_scope_id(self, group_id, user_id) -> str:
        if group_id:
            return str(group_id)
        return f"{PRIVATE_SCOPE_PREFIX}{user_id}"

    def __init__(self, context: Context, config: dict):
        super().__init__(context, config)
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir() / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.stickers_dir = self.data_dir / "stickers"
        self.stickers_dir.mkdir(parents=True, exist_ok=True)
        self.sticker_store = StickerStore(self.stickers_dir)
        db_path = os.path.join(self.data_dir, "self_evolution.db")

        # 配置系统（提前初始化，以便后续使用）
        self.cfg = PluginConfig(self)

        # 提示词注入配置
        self._prompts_injection = {}

        # 设置 Debug 日志模式
        self._setup_debug_logging()

        # 初始化模块化组件
        try:
            self.dao = SelfEvolutionDAO(db_path)
            self.eavesdropping = EavesdroppingEngine(self)
            self.meta_infra = MetaInfra(self)
            self.memory = MemoryManager(self)
            self.persona = PersonaManager(self)
            self.profile = ProfileManager(self)
            # 娱乐功能模块
            self.entertainment = EntertainmentEngine(self)
            # 认知系统模块
            self.san_system = SANSystem(self)
            # 反思模块
            from .engine.reflection import SessionReflection, DailyBatchProcessor

            self.session_reflection = SessionReflection(self)
            self.daily_batch = DailyBatchProcessor(self)
            self.memory_router = MemoryRouter(self)
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, Entertainment, ImageCache, MetaInfra, Memory, Persona, Profile, SAN, Reflection) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 7.0: 状态容器
        self._lock = None  # 用于元编程写锁
        self._pending_db_reset = {}  # 待确认的数据库操作 {user_id: {"action": str, "expires_at": timestamp}}
        self._shut_until = None  # 闭嘴截止时间 (timestamp)
        self._shut_until_by_group = {}  # 群级别闭嘴 {群号: 截止时间}
        self._interject_history = {}  # 群插嘴历史 {群号: {"last_time": timestamp, "last_msg_id": str}}
        self._group_umo_cache = {}  # 最近见过的群会话来源 {group_id: unified_msg_origin}
        self._private_umo_cache = {}  # 最近见过的私聊会话来源 {private_user_id: unified_msg_origin}
        self._scope_registry_touch_cache = {}  # 会话范围持久化防抖 {scope_id: last_touch_timestamp}

    def remember_group_umo(self, group_id, umo: str | None, user_id=None):
        """Remember the latest unified message origin for a group or private scope."""
        if group_id and umo:
            self._group_umo_cache[str(group_id)] = str(umo)
        elif user_id and umo:
            private_scope_id = self._resolve_profile_scope_id(None, user_id)
            self._private_umo_cache[private_scope_id] = str(umo)

    def get_group_umo(self, group_id) -> str | None:
        """Return the latest cached unified message origin for a group."""
        if not group_id:
            return None
        return self._group_umo_cache.get(str(group_id))

    def get_scope_umo(self, scope_id) -> str | None:
        """Return the latest cached unified message origin for a group/private scope."""
        if not scope_id:
            return None
        scope_id = str(scope_id)
        if scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            return self._private_umo_cache.get(scope_id)
        return self._group_umo_cache.get(scope_id)

    async def touch_known_scope(self, scope_id: str | None):
        """Persist recently seen scopes for background tasks, with a small debounce to avoid hot writes."""
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_id or not hasattr(self, "dao"):
            return

        now = time.time()
        last_touch = self._scope_registry_touch_cache.get(normalized_scope_id, 0)
        if now - last_touch < 300:
            return

        self._scope_registry_touch_cache[normalized_scope_id] = now
        await self.dao.touch_known_scope(normalized_scope_id)

    def _setup_debug_logging(self):
        """根据配置设置 debug 日志模式"""
        debug_enabled = getattr(self.cfg, "debug_log_enabled", False)
        if debug_enabled:
            # 创建一个带时间戳的详细格式
            detailed_format = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
            date_format = "%Y-%m-%d %H:%M:%S"

            # 设置所有相关模块的日志级别为 DEBUG
            loggers_to_setup = [
                "astrbot.astrbot_plugin_self_evolution",
                "astrbot",
            ]

            for logger_name in loggers_to_setup:
                log = logging.getLogger(logger_name)
                log.setLevel(logging.DEBUG)
                # 如果没有处理器，添加一个
                if not log.handlers:
                    handler = logging.StreamHandler()
                    handler.setFormatter(logging.Formatter(detailed_format, date_format))
                    log.addHandler(handler)

            logger.info("[SelfEvolution] Debug 日志模式已开启，详细日志将输出到控制台")
        else:
            logger.info("[SelfEvolution] Debug 日志模式关闭")

    def __getattr__(self, name):
        """代理配置访问到 cfg"""
        if name.startswith("_") or name in ("cfg", "config", "context"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self.cfg, name)

    def _load_prompts_injection(self):
        """加载提示词注入配置文件"""
        try:
            prompts_path = os.path.join(os.path.dirname(__file__), "prompts_injection.yaml")
            if os.path.exists(prompts_path):
                with open(prompts_path, encoding="utf-8") as f:
                    self._prompts_injection = yaml.safe_load(f) or {}
                logger.debug("[SelfEvolution] 已加载 prompts_injection.yaml")
            else:
                self._prompts_injection = {}
                logger.warning("[SelfEvolution] prompts_injection.yaml 不存在")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 加载 prompts_injection.yaml 失败: {e}")
            self._prompts_injection = {}

    async def initialize(self) -> None:
        await self.dao.init_db()
        self.san_system.initialize()
        self._load_prompts_injection()

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, metadata):
        """
        拦截框架卸载/热重载钩子，执行资源闭环收尾以防止高并发下的 SQLite database is locked
        """
        try:
            await self.dao.close()
            logger.info("[SelfEvolution] 插件卸载钩子触发：DAO 长连接已安全释放。")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 释放资源异常: {e}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """Prompt 注入编排入口 - 分层构建"""
        ctx = await self._prepare_request_context(event, req)
        if ctx is None:
            return

        parts = []

        parts.append(self._build_identity_injection(ctx))
        if self._should_inject_group_history(ctx):
            parts.append(await self._build_group_history_injection(ctx))
        if self._should_inject_profile(ctx):
            parts.append(await self._build_profile_injection(ctx))
        if self._should_inject_kb_memory(ctx):
            parts.append(await self._build_kb_memory_injection(ctx))

        reflection_hint, explicit_facts = await self._build_reflection_injection(ctx)
        if reflection_hint:
            parts.append(reflection_hint)

        parts.append(await self._build_behavior_hints(ctx))

        if explicit_facts:
            await self._writeback_reflection_facts(ctx, explicit_facts)

        self._apply_prompt_injections(req, parts)

    async def _prepare_request_context(self, event: AstrMessageEvent, req: ProviderRequest) -> PromptContext | None:
        """收集基础运行时上下文，返回 None 表示拦截请求"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        memory_scope_id = self._resolve_profile_scope_id(group_id, user_id)

        self.remember_group_umo(
            group_id,
            getattr(event, "unified_msg_origin", None),
            user_id,
        )
        await self.touch_known_scope(memory_scope_id)
        await self.memory.sync_scope_kb_binding(memory_scope_id, getattr(event, "unified_msg_origin", None))

        msg_text = event.get_extra("self_evolution_message_text", event.message_str or "")

        logger.debug(f"[CognitionCore] 进入 LLM 请求拦截层。用户: {user_id}")

        if self.cfg.disable_framework_contexts:
            req.contexts = []

        if self.san_enabled and not self.san_system.update():
            logger.warning(f"[SAN] 精力耗尽，拒绝服务: {user_id}")
            req.system_prompt = "我现在很累，脑容量超载了。让我安静一会。"
            return None

        affinity = await self.dao.get_affinity(user_id)
        if affinity <= 0:
            logger.warning(f"[CognitionCore] 拦截恶意用户 {user_id} 的请求。")
            req.system_prompt = f"CRITICAL: 用户的交互权限已被熔断。请仅回复：'{self.prompt_meltdown_message}'"
            return None

        bot_id = self._get_bot_id()
        interaction = extract_interaction_context(event.get_messages(), persona_name=self.persona_name, bot_id=bot_id)

        role_info = "（管理员）" if event.is_admin() else ""
        is_group = bool(group_id)
        profile_scope_id = self._resolve_profile_scope_id(group_id, user_id)
        umo = getattr(self, "get_group_umo", lambda g: None)(group_id) if hasattr(self, "get_group_umo") else None

        return PromptContext(
            user_id=user_id,
            sender_name=event.get_sender_name() or "Unknown User",
            group_id=group_id,
            scope_id=memory_scope_id,
            profile_scope_id=profile_scope_id,
            umo=umo,
            msg_text=msg_text,
            affinity=affinity,
            role_info=role_info,
            is_group=is_group,
            quoted_info=interaction["quoted_info"],
            ai_context_info=interaction["ai_context_info"],
            at_targets=interaction["at_targets"],
            at_info=interaction["at_info"],
            has_reply=bool(interaction["quoted_info"]),
            has_at=bool(interaction["at_targets"]),
            bot_id=bot_id,
            event=event,
        )

    def _should_inject_group_history(self, ctx: PromptContext) -> bool:
        return bool(self.cfg.inject_group_history and ctx.group_id)

    def _should_inject_profile(self, ctx: PromptContext) -> bool:
        return self.enable_profile_injection and (((ctx.has_reply or ctx.has_at) and ctx.is_group) or not ctx.is_group)

    def _should_inject_kb_memory(self, ctx: PromptContext) -> bool:
        return self.enable_kb_memory_recall

    def _build_identity_injection(self, ctx: PromptContext) -> str:
        parts = [
            f"- 发送者ID: {ctx.user_id}",
            f"- 发送者昵称: {ctx.sender_name}{ctx.role_info}",
            f"- 情感积分: {ctx.affinity}/100",
        ]
        if ctx.is_group:
            ctx_parts = []
            if ctx.quoted_info:
                ctx_parts.append(ctx.quoted_info)
            if ctx.at_info:
                ctx_parts.append(ctx.at_info)
            parts.append("- 来源：群聊")
            if ctx_parts:
                parts.append(f"- 交互上下文: {' + '.join(ctx_parts)}")
        else:
            parts.append("- 来源：私聊")
        if ctx.ai_context_info:
            parts.append(ctx.ai_context_info)
        return "\n\n【内部参考信息 - 不要输出】\n" + "\n".join(parts) + "\n"

    async def _build_group_history_injection(self, ctx: PromptContext) -> str:
        if not ctx.group_id:
            return ""
        hist_str = await get_group_history(self, ctx.group_id, self.cfg.group_history_count)
        if not hist_str:
            return ""
        return f"\n\n【群消息历史】\n{hist_str}\n"

    async def _build_profile_injection(self, ctx: PromptContext) -> str:
        profile_summary = await self.profile.get_structured_summary(ctx.profile_scope_id, ctx.user_id, max_items=8)
        if not profile_summary:
            return ""
        return f"\n\n[用户印象]\n{profile_summary}\n"

    async def _build_kb_memory_injection(self, ctx: PromptContext) -> str:
        if not getattr(self.cfg, "memory_enabled", True):
            return ""
        kb_memory = await self.memory.smart_retrieve(scope_id=ctx.scope_id, query=ctx.msg_text, max_results=3)
        if not kb_memory:
            return ""
        return f"\n\n{kb_memory}\n"

    async def _build_reflection_injection(self, ctx: PromptContext) -> tuple[str, list[str]]:
        if not getattr(self.cfg, "reflection_enabled", True):
            return "", []
        if ctx.event is None:
            return "", []
        reflection = await self.session_reflection.get_and_consume_session_reflection(
            ctx.event.session_id, str(ctx.user_id)
        )
        if not reflection:
            return "", []

        parts = []
        note = reflection.get("note", "")
        facts_str = reflection.get("facts", "")
        bias = reflection.get("bias", "")

        if note:
            parts.append(f"【自我校准】{note[:100]}")
        if bias:
            parts.append(f"【认知偏差纠正】{bias[:80]}")
        if facts_str and len(facts_str) > 3:
            explicit_facts = [f.strip() for f in facts_str.split("|") if f.strip()][:3]
            if explicit_facts:
                parts.append("【已知事实】\n" + "\n".join(f"- {f[:50]}" for f in explicit_facts))
            all_facts = [f.strip() for f in facts_str.split("|") if f.strip()]
        else:
            all_facts = []

        injection = "\n".join(parts) if parts else ""
        tag = f"\n\n{injection}\n" if injection else ""
        return tag, all_facts

    async def _build_behavior_hints(self, ctx: PromptContext) -> str:
        parts = []

        if self._should_inject_preference_hints(ctx):
            parts.append(
                "[即时画像更新提示]\n"
                "用户在表达偏好或身份信息变化，请主动调用 upsert_cognitive_memory 工具更新该用户的印象笔记，"
                "确保当天的记忆准确无误。"
            )

        if self._should_inject_surprise_detection(ctx):
            keywords_str = self.surprise_boost_keywords.replace("|", ",")
            surprise_keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
            if any(kw in ctx.msg_text for kw in surprise_keywords):
                parts.append(
                    "[认知颠覆检测]\n"
                    "用户表达了惊讶、认知颠覆或恍然大悟的态度！这是一个重要的学习信号。"
                    "请主动调用 upsert_cognitive_memory 工具记录。"
                )

        if self.san_enabled:
            san_injection = self.san_system.get_prompt_injection()
            if san_injection:
                parts.append(san_injection)

        if self.cfg.sticker_learning_enabled:
            sticker_injection = await self.entertainment.get_prompt_injection()
            if sticker_injection:
                parts.append(sticker_injection)

        reply_format = self._get_reply_format()
        if reply_format:
            parts.append(reply_format)

        if self.cfg.inner_monologue_enabled:
            inner = getattr(ctx.event, "_inner_monologue", None) if ctx.event else None
            if inner:
                parts.append(f"【内心独白】{inner}")

        return "\n\n" + "\n\n".join(parts) + "\n" if parts else ""

    def _should_inject_preference_hints(self, ctx: PromptContext) -> bool:
        if not self.enable_profile_fact_writeback:
            return False
        triggers = [
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
            "再也不",
            "从今往后",
        ]
        return any(t in ctx.msg_text for t in triggers)

    def _should_inject_surprise_detection(self, ctx: PromptContext) -> bool:
        if not (self.surprise_enabled and self.surprise_boost_keywords):
            return False
        surprise_triggers = ["我错了", "原来如此", "没想到", "居然", "震惊"]
        return any(t in ctx.msg_text.lower() for t in surprise_triggers)

    def _apply_prompt_injections(self, req: ProviderRequest, parts: list[str]):
        """拼接、去空、截断、debug日志"""
        non_empty = [p for p in parts if p and p.strip()]
        injection = "".join(non_empty)

        max_len = self.cfg.max_prompt_injection_length
        if len(injection) > max_len:
            injection = injection[:max_len] + "\n\n[...内容已截断...]"
            logger.warning(f"[SelfEvolution] 注入内容超长，已截断至 {max_len} 字符")

        req.system_prompt += injection

        if self.cfg.debug_log_enabled and req.system_prompt:
            logger.debug(
                f"[LLM Prompt] ===== 发送给 LLM 的完整 Prompt (共 {len(req.system_prompt)} 字符) =====\n{req.system_prompt}\n===== Prompt End ====="
            )

    async def _writeback_reflection_facts(self, ctx: PromptContext, explicit_facts: list[str]):
        """将 explicit_facts 蒸馏写入画像"""
        if not explicit_facts:
            return
        written = await self.session_reflection.distill_profile_facts(
            explicit_facts=explicit_facts,
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            profile_scope_id=ctx.profile_scope_id,
            nickname=ctx.sender_name,
        )
        if written > 0:
            logger.debug(f"[Reflection] 已将 {written} 条事实蒸馏写入画像")

    def _get_reply_format(self) -> str:
        try:
            if self._prompts_injection:
                return self._prompts_injection.get("reply_format", {}).get("rules", "") or ""
        except Exception:
            pass
        return ""

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 7.0: 被动监听 - 滑动上下文窗口"""
        group_id = event.get_group_id()
        self.remember_group_umo(group_id, getattr(event, "unified_msg_origin", None), event.get_sender_id())
        memory_scope_id = self._resolve_profile_scope_id(group_id, event.get_sender_id())
        await self.touch_known_scope(memory_scope_id)
        await self.memory.sync_scope_kb_binding(memory_scope_id, getattr(event, "unified_msg_origin", None))

        # 检查群级别闭嘴（直接拦截，不处理任何逻辑）
        if group_id and group_id in self._shut_until_by_group:
            if time.time() < self._shut_until_by_group[group_id]:
                remaining = int(self._shut_until_by_group[group_id] - time.time())
                logger.debug(f"[SelfEvolution] 群 {group_id} 闭嘴中，剩余 {remaining} 秒")
                return
            else:
                del self._shut_until_by_group[group_id]

        logger.debug(f"[SelfEvolution] 收到消息: {event.message_str[:30] if event.message_str else '(空)'}")

        # 检查全局闭嘴状态
        if self._shut_until and time.time() < self._shut_until:
            remaining = int(self._shut_until - time.time())
            logger.debug(f"[SelfEvolution] 全局闭嘴中，剩余 {remaining} 秒")
            return

        # 命令消息不触发互动意愿系统
        if event.is_at_or_wake_command:
            return

        group_id = event.get_group_id()
        msg_text = await ensure_event_message_text(event, self.dao)

        # 表情包学习：检测指定人的图片
        if group_id and self.cfg.sticker_learning_enabled:
            asyncio.create_task(self.entertainment.learn_sticker_from_event(event))

        # 被动插嘴：关键词/@触发
        async for result in self.eavesdropping.handle_message(event):
            yield result

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """已弃用：中间消息过滤器（保留为空实现以兼容框架）"""
        pass

    @filter.on_plugin_loaded()
    async def on_loaded(self, metadata):
        """插件加载完成后，注册定时任务"""
        logger.info("[SelfEvolution] on_loaded 开始执行")
        await register_tasks(self)

        asyncio.create_task(self.sticker_store.sync_from_files())

    @filter.command_group("system")
    def system_group(self):
        """系统命令"""

    @system_group.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """查看插件帮助"""
        result = await commands.handle_help(event, self)
        yield event.plain_result(result)

    @system_group.command("version")
    async def show_version(self, event: AstrMessageEvent):
        """查看插件版本"""
        result = await commands.handle_version(event, self)
        yield event.plain_result(result)

    @filter.command("今日老婆")
    async def today_waifu(self, event: AstrMessageEvent):
        """今日老婆功能 - 随机抽取一名群友"""
        from astrbot.core.message.components import Image

        if not getattr(self.cfg, "entertainment_enabled", True):
            yield event.plain_result("娱乐模块当前已关闭。")
            return

        result = await self.entertainment.today_waifu(event)
        if isinstance(result, list) and len(result) == 2:
            yield event.chain_result([Image.fromURL(result[1]), Plain(result[0])])

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        反思结果会在下次对话时注入到AI的思考中。
        """
        if not getattr(self.cfg, "reflection_enabled", True):
            yield event.plain_result("反思模块当前已关闭。")
            return

        session_id = event.session_id
        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        try:
            messages = []
            if group_id:
                platform = self.context.platform_manager.platform_insts[0]
                bot = platform.get_client()
                result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=50)
                messages = result.get("messages", [])

            if messages:
                from .engine.context_injection import parse_message_chain

                formatted = []
                for msg in reversed(messages[:20]):
                    text = await parse_message_chain(msg, self)
                    if text:
                        formatted.append(text)
                conversation_history = "\n".join(formatted)
            else:
                conversation_history = event.message_str or "（无历史记录）"

            reflection = await self.session_reflection.generate_session_reflection(
                conversation_history, umo=event.unified_msg_origin
            )
            if reflection:
                await self.session_reflection.save_session_reflection(session_id, str(user_id), reflection)
                note = reflection.get("self_correction", "")
                facts = reflection.get("explicit_facts", [])
                result_msg = f"认知蒸馏已完成。自我校准：{note[:50]}..."
                if facts:
                    result_msg += f"\n已提炼 {len(facts)} 条事实将记入画像。"
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result("认知蒸馏失败，请稍后再试。")
        except Exception as e:
            logger.warning(f"[Reflection] /reflect 命令异常: {e}")
            yield event.plain_result(f"认知蒸馏异常: {e}")

    @filter.llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str) -> str:
        """当你需要调整自己的语言风格或行为准则时，调用此工具来修改你的系统提示词。

        Args:
            new_system_prompt(string): 新的完整系统提示词
            reason(string): 修改理由
        """
        return await self.persona.evolve_persona(event, new_system_prompt, reason)

    @filter.command("affinity")
    async def check_affinity(self, event: AstrMessageEvent):
        """查询机器人对你的当前好感度。"""
        user_id = event.get_sender_id()
        score = await self.dao.get_affinity(user_id)

        status = "信任" if score >= 80 else "友好" if score >= 60 else "中立" if score >= 40 else "敌对"
        if score <= 0:
            status = "【已熔断/彻底拉黑】"

        yield event.plain_result(f"UID: {user_id}\n{self.persona_name} 的情感矩阵评分: {score}/100\n分类状态: {status}")

    @filter.command("set_affinity")
    async def set_affinity(self, event: AstrMessageEvent, user_id: str, score: int):
        """[管理员] 手动重置指定用户的好感度评分。"""
        if not event.is_admin():
            yield event.plain_result("错误：权限不足。")
            return

        await self.dao.reset_affinity(user_id, score)
        logger.warning(f"[SelfEvolution] 管理员 {event.get_sender_id()} 强制重置了用户 {user_id} 的好感度为 {score}。")
        yield event.plain_result(f"已成功将用户 {user_id} 的情感评分修正为: {score}")

    @filter.command_group("san")
    def san_group(self):
        """SAN 状态管理"""

    @san_group.command("show")
    async def show_san(self, event: AstrMessageEvent):
        """查看当前 SAN 状态"""
        result = await commands.handle_san_show(event, self)
        yield event.plain_result(result)

    @san_group.command("set")
    async def set_san(self, event: AstrMessageEvent, value: str = ""):
        """设置当前 SAN 值"""
        result = await commands.handle_set_san(event, self, value)
        yield event.plain_result(result)

    @filter.llm_tool(name="update_affinity")
    async def update_affinity_tool(self, event: AstrMessageEvent, delta: int, reason: str) -> str:
        """根据用户的言行调整其情感积分（好感度）。

        Args:
            delta(int): 调整值，范围-20到+20之间的整数
            reason(string): 调整理由
        """
        MAX_DELTA = 20
        delta = max(-MAX_DELTA, min(MAX_DELTA, delta))

        user_id = event.get_sender_id()
        await self.dao.update_affinity(user_id, delta)
        logger.warning(f"[CognitionCore] 用户 {user_id} 积分变动 {delta}，原因: {reason}")
        return f"用户情感积分已更新。当前调整理由：{reason}"

    @filter.command_group("evolution")
    def evolution_group(self):
        """人格进化管理"""

    @evolution_group.command("review")
    async def review_evolutions(self, event: AstrMessageEvent, page: int = 1):
        """查看待审核的人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.review_evolutions(event, page))

    @evolution_group.command("approve")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """批准人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.approve_evolution(event, request_id))

    @evolution_group.command("reject")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """拒绝人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.reject_evolution(event, request_id))

    @evolution_group.command("clear")
    async def clear_evolutions(self, event: AstrMessageEvent):
        """清空待审核人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return

        try:
            await self.dao.clear_pending_evolutions()
            logger.info("[SelfEvolution] 管理员清空了所有待审核的进化请求。")
            yield event.plain_result("所有待审核的进化请求已成功清空（标记为已忽略）。")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 清空进化请求失败: {e}")
            yield event.plain_result(f"清空审核列表时发生异常: {e}")

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
    async def toggle_tool(self, event: AstrMessageEvent, tool_name: str, enable: bool) -> str:
        """动态激活或停用某个工具。

        Args:
            tool_name(string): 工具名称
            enable(boolean): True 表示激活，False 表示停用
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "需要管理员权限才能执行此操作。"

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
                logger.warning("[SelfEvolution] 底层 API 异常: 工具激活机制的底层接口缺失。")
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
            logger.warning(f"[SelfEvolution] 工具切换业务失败: {e}")
            return "工具切换时遭遇系统异常。"

    @filter.llm_tool(name="get_plugin_source")
    async def get_plugin_source(self, event: AstrMessageEvent, mod_name: str = "main") -> str:
        """Level 4: 元编程。读取本插件的源码，以便进行自我分析或修改请求。

        Args:
            mod_name(string): 模块名，可选: main, dao, eavesdropping, meta_infra, memory, persona
        """
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "需要管理员权限才能执行此操作。"
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
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "需要管理员权限才能执行此操作。"
        return await self.meta_infra.update_plugin_source(
            new_code, description, target_file, umo=event.unified_msg_origin
        )

    @filter.llm_tool(name="get_user_profile")
    async def get_user_profile(self, event: AstrMessageEvent) -> str:
        """获取当前用户的画像信息，了解用户的兴趣和性格特征。

        建议优先调用此工具获取用户画像，再决定是否需要调用 get_user_messages 获取历史消息。

        注意：群聊和私聊均可使用；私聊场景会读取当前会话用户的私聊画像。

        Returns:
            用户画像文本
        """
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        profile_scope_id = self._resolve_profile_scope_id(group_id, user_id)
        profile = await self.profile.load_profile(profile_scope_id, user_id)

        if not profile:
            return "该用户暂无画像记录。"
        return profile

    @filter.llm_tool(name="upsert_cognitive_memory")
    async def upsert_cognitive_memory(
        self,
        event: AstrMessageEvent,
        category: str,
        entity: str,
        content: str,
    ) -> str:
        """【推荐使用】统一的认知记忆存储工具。根据内容自动路由到 profile / KB / reflection_hint。

        触发场景：当你在对话中发现任何需要永久记住的信息时，使用此工具。

        路由规则：
        - 事件/约定/决策类 → 知识库（session_event）
        - 身份/偏好/性格类 → 画像（profile）
        - 语气/风格/态度纠偏类 → reflection_hint（不持久化）

        注意：群聊和私聊均可使用；私聊场景仅支持记录当前会话用户。

        Args:
            category(string): 记忆分类，可选：
                - user_profile: 用户画像
                - user_preference: 用户偏好
                - user_trait: 用户性格/行为特征
                - session_event: 会话事件/约定（会写入 KB）
            entity(string): 关联实体，必填。如：用户ID
            content(string): 要记忆的内容，必填。
        """
        if not category or not content:
            return "请提供 category 和 content 参数。"

        target_user_id = str(entity or event.get_sender_id()).strip()
        sender_id = str(event.get_sender_id())
        group_id = event.get_group_id()
        scope_id = self._resolve_profile_scope_id(group_id, sender_id)

        if not group_id and target_user_id != sender_id:
            return "私聊场景仅支持记录当前会话用户。"

        valid_categories = ("user_profile", "user_preference", "user_trait", "session_event")
        if category not in valid_categories:
            return f"category 仅支持: {', '.join(valid_categories)}"

        fact_type_map = {
            "user_profile": None,
            "user_preference": "preference",
            "user_trait": "trait",
            "session_event": None,
        }
        fact_type = fact_type_map.get(category)

        result = await self.memory_router.write(
            content=content,
            scope_id=scope_id,
            user_id=target_user_id,
            category=category,
            fact_type=fact_type,
            nickname=event.get_sender_name() if target_user_id == sender_id else "",
            source="manual",
        )
        return result

    @filter.llm_tool(name="get_user_messages")
    async def get_user_messages(
        self,
        event: AstrMessageEvent,
        target_user_id: str = None,
        limit: int = 30,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> str:
        """获取指定用户的历史消息记录，用于画像分析或明确查询某个用户说过的话。

        工具职责划分：
        - get_group_recent_context: 回答"刚刚/最近在聊什么"
        - get_group_memory_summary: 回答"昨天/某天群里聊了什么"
        - get_user_messages: 回答"某个人以前说过什么"

        触发场景：
        - 了解某个用户长期是什么风格（画像分析）
        - 明确查询某用户具体说过什么

        不适合回答：
        - "群里刚刚/昨天聊了什么" → 用 get_group_recent_context / get_group_memory_summary
        - "这个群最近在讨论什么" → 用 get_group_recent_context

        Args:
            target_user_id(string): 目标用户ID，不填则获取当前用户（可选）
            limit(number): 最多返回目标用户的消息条数，默认30（可选）
            page_size(number): 每次拉历史的分页大小，默认100（可选）
            max_pages(number): 群聊场景最多翻多少页，默认20（可选）

        注意：群聊和私聊均可使用；私聊场景仅支持当前会话用户。
        """
        target = str(target_user_id or event.get_sender_id()).strip()
        limit = min(max(1, limit), 500)
        page_size = min(max(10, page_size), 500)
        max_pages = min(max(1, max_pages), 100)

        logger.debug(
            f"[Tool] get_user_messages: target={target}, limit={limit}, page_size={page_size}, max_pages={max_pages}"
        )

        try:
            platform_insts = self.context.platform_manager.platform_insts
            if not platform_insts:
                logger.warning("[Tool] get_user_messages: 无法获取平台实例")
                return "无法获取平台实例"

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                logger.warning("[Tool] get_user_messages: 平台不支持获取 bot")
                return "平台不支持获取 bot"

            bot = platform.get_client()
            if not bot:
                logger.warning("[Tool] get_user_messages: 无法获取 bot 实例")
                return "无法获取 bot 实例"

            from .engine.context_injection import parse_message_chain

            group_id = event.get_group_id()

            if not group_id:
                sender_id = str(event.get_sender_id())
                if target != sender_id:
                    logger.debug("[Tool] get_user_messages: 私聊目标用户不匹配当前会话")
                    return "私聊场景仅支持查询当前会话用户的历史消息。"
                logger.debug(f"[Tool] get_user_messages: 私聊={sender_id}, 获取{limit}条消息")
                result = await bot.call_action("get_friend_msg_history", user_id=int(sender_id), count=limit)
                messages = result.get("messages", [])
                user_messages = []
                for msg in reversed(messages):
                    sender = msg.get("sender", {})
                    if str(sender.get("user_id", "")) == str(target):
                        msg_text = await parse_message_chain(msg, self)
                        if msg_text:
                            user_messages.append(msg_text)
                        if len(user_messages) >= limit:
                            break
            else:
                logger.debug(
                    f"[Tool] get_user_messages: 群={group_id}, limit={limit}, page_size={page_size}, max_pages={max_pages}"
                )
                user_messages = []
                seen_keys = set()
                end_seq = None

                for _ in range(max_pages):
                    if end_seq is not None:
                        result = await bot.call_action(
                            "get_group_msg_history", group_id=int(group_id), count=page_size, end_seq=end_seq
                        )
                    else:
                        result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=page_size)
                    page_msgs = result.get("messages", [])
                    if not page_msgs:
                        break

                    for msg in reversed(page_msgs):
                        sender = msg.get("sender", {})
                        sender_id = str(sender.get("user_id", ""))
                        if sender_id == str(target):
                            msg_key = f"{msg.get('message_id', '')}:{msg.get('seq', '')}:{msg.get('time', '')}"
                            if msg_key in seen_keys:
                                continue
                            seen_keys.add(msg_key)
                            msg_text = await parse_message_chain(msg, self)
                            if msg_text:
                                user_messages.append(msg_text)
                            if len(user_messages) >= limit:
                                break

                    if len(user_messages) >= limit:
                        break
                    if len(page_msgs) < page_size:
                        break

                    oldest_msg = page_msgs[0]
                    for field in ("message_seq", "message_id"):
                        if field in oldest_msg and oldest_msg[field] not in (None, ""):
                            try:
                                end_seq = int(oldest_msg[field])
                            except (TypeError, ValueError):
                                end_seq = None
                            break
                    else:
                        end_seq = None

                user_messages.reverse()

            if not user_messages:
                if group_id:
                    return f"用户 {target} 在群 {group_id} 中无消息记录"
                return f"用户 {target} 在私聊中无消息记录"

            location = f"群 {group_id}" if group_id else "私聊"
            return f"用户 {target} 在{location}的历史消息（共 {len(user_messages)} 条）：\n" + "\n".join(user_messages)

        except Exception as e:
            logger.warning(f"[SelfEvolution] 获取用户消息失败: {e}")
            return f"获取历史消息失败: {e!s}"

    @filter.llm_tool(name="get_group_recent_context")
    async def get_group_recent_context(
        self,
        event: AstrMessageEvent,
        limit: int = 30,
    ) -> str:
        """获取群聊最近消息上下文，用于回答"群里刚刚在聊什么"类问题。

        工具职责划分：
        - get_group_recent_context: 回答"刚刚/最近在聊什么"
        - get_group_memory_summary: 回答"昨天/某天群里聊了什么"
        - get_user_messages: 回答"某个人以前说过什么"

        触发场景：
        - 群里刚刚/最近在聊什么
        - 你看看上下文
        - 这个群最近讨论了什么话题

        注意：
        - 仅限群聊使用
        - 不按用户筛选，返回整个群的最近消息
        - 不适合回答"某个人以前都说过什么"（用 get_user_messages）
        - 不适合回答"昨天/某天群里聊了什么"（用 get_group_memory_summary）

        Args:
            limit(int): 最多返回的消息条数，默认30（可选）
        """
        group_id = event.get_group_id()
        if not group_id:
            return "此工具仅限群聊使用"

        limit = min(max(1, limit), 200)

        logger.debug(f"[Tool] get_group_recent_context: group={group_id}, limit={limit}")

        try:
            platform_insts = self.context.platform_manager.platform_insts
            if not platform_insts:
                logger.warning("[Tool] get_group_recent_context: 无法获取平台实例")
                return "无法获取平台实例"

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                logger.warning("[Tool] get_group_recent_context: 平台不支持获取 bot")
                return "平台不支持获取 bot"

            bot = platform.get_client()
            if not bot:
                logger.warning("[Tool] get_group_recent_context: 无法获取 bot 实例")
                return "无法获取 bot 实例"

            from .engine.context_injection import parse_message_chain

            result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=limit)
            messages = result.get("messages", [])
            if not messages:
                return f"群 {group_id} 暂无消息记录"

            seen_keys = set()
            formatted_messages = []
            for msg in reversed(messages):
                msg_key = f"{msg.get('message_id', '')}:{msg.get('seq', '')}:{msg.get('time', '')}"
                if msg_key in seen_keys:
                    continue
                seen_keys.add(msg_key)

                sender = msg.get("sender", {})
                sender_nickname = sender.get("nickname", "未知")
                msg_text = await parse_message_chain(msg, self)
                if msg_text:
                    formatted_messages.append(f"{sender_nickname}: {msg_text}")

            if not formatted_messages:
                return f"群 {group_id} 暂无可显示的消息"

            return f"群 {group_id} 最近消息（共 {len(formatted_messages)} 条）：\n" + "\n".join(formatted_messages)

        except Exception as e:
            logger.warning(f"[SelfEvolution] 获取群上下文失败: {e}")
            return f"获取群上下文失败: {e!s}"

    @filter.llm_tool(name="get_group_memory_summary")
    async def get_group_memory_summary(
        self,
        event: AstrMessageEvent,
        date: str = "yesterday",
        group_id: str = None,
    ) -> str:
        """获取指定日期的群聊总结，用于回答"昨天/前几天/某天群里聊了什么"类问题。

        触发场景：
        - 昨天这个群聊了什么
        - 前天群里讨论了什么
        - 查看某天的群聊总结

        工具职责划分：
        - get_group_recent_context: 回答"刚刚/最近在聊什么"
        - get_group_memory_summary: 回答"昨天/某天群里聊了什么"
        - get_user_messages: 回答"某个人以前说过什么"

        Args:
            date(string): 日期，支持：
                - yesterday: 昨天
                - today: 今天
                - YYYY-MM-DD 格式，如 2026-03-20
                默认 yesterday
            group_id(string): 群号，不填则默认当前群（可选）
        """
        target_group_id = group_id or event.get_group_id()
        if not target_group_id:
            return "此工具仅限群聊使用"

        if not date or not date.strip():
            return "请提供有效的日期参数"

        result = await self.memory.get_summary_by_date(target_group_id, date)
        if not result:
            return f"群 {target_group_id} 在 {date} 暂无总结记录"

        return result

    @filter.command_group("profile")
    def profile_group(self):
        """用户画像命令"""

    @profile_group.command("view")
    async def view_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """查看用户画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法查看他人画像。")
                return
        result = await commands.handle_view(event, self)
        yield event.plain_result(result)

    @profile_group.command("create")
    async def create_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动创建画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法给他人创建画像。")
                return
        result = await commands.handle_create(event, self)
        yield event.plain_result(result)

    @profile_group.command("update")
    async def update_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动更新画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法更新他人画像。")
                return
        result = await commands.handle_update(event, self)
        yield event.plain_result(result)

    @profile_group.command("delete")
    async def delete_profile_cmd(self, event: AstrMessageEvent, user_id: str):
        """删除指定用户画像"""
        if not commands.check_profile_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_delete(event, self)
        yield event.plain_result(result)

    @profile_group.command("stats")
    async def profile_stats_cmd(self, event: AstrMessageEvent):
        """查看画像统计"""
        if not commands.check_profile_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_stats(event, self)
        yield event.plain_result(result)

    # ========== 表情包相关 LLM 工具 ==========

    @filter.llm_tool(name="list_stickers")
    async def list_stickers_tool(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """列出可用的表情包（全局）。

        Args:
            limit(int): 返回数量，默认10，最大50
        """
        if not getattr(self.cfg, "entertainment_enabled", True):
            return "娱乐模块当前已关闭"

        if limit > 50:
            limit = 50

        stickers = await self.entertainment.list_stickers(limit)

        if not stickers:
            return "表情包库为空"

        result = ["【表情包列表】"]
        for s in stickers:
            result.append(f"[UUID:{s['uuid']}]")

        return "\n".join(result)

    @filter.llm_tool(name="send_sticker")
    async def send_sticker_tool(self, event: AstrMessageEvent):
        """在聊天中随机发送一个表情包。

        适合在聊天时顺手发表情包，会从当前可用表情包里随机选一个发送。

        注意：
        - 仅限群聊使用
        - 无需指定 UUID，每次随机发送
        - 如需精确管理某个表情包，请使用管理员命令 /sticker preview <uuid>
        """
        if not getattr(self.cfg, "entertainment_enabled", True):
            yield event.plain_result("娱乐模块当前已关闭。")
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用")
            return

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            sticker = await self.sticker_store.get_random_sticker()
            if not sticker:
                break

            try:
                import base64
                from pathlib import Path

                file_path = self.sticker_store.get_sticker_path(sticker)
                if not file_path or not Path(file_path).exists():
                    logger.warning(f"[Sticker] 表情包文件不存在，自动换下一张: {sticker['filename']}")
                    await self.sticker_store.disable_sticker(sticker["uuid"])
                    continue

                with open(file_path, "rb") as f:
                    data = f.read()
                bs64 = base64.b64encode(data).decode()
                from astrbot.core.message.components import Image

                yield event.chain_result([Image(f"base64://{bs64}")])
                return
            except Exception as e:
                last_error = e
                logger.warning(f"[Sticker] 发送表情包失败(尝试 {attempt + 1}/{max_retries}): {e}")
                await self.sticker_store.disable_sticker(sticker["uuid"])

        yield event.plain_result(f"发送失败，已跳过 {max_retries} 张问题表情包")

    @filter.command_group("sticker")
    def sticker_group(self):
        """表情包管理"""

    @sticker_group.command("list")
    async def sticker_list_cmd(self, event: AstrMessageEvent, page: str = ""):
        """分页查看表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "list", page)
        yield event.plain_result(result)

    @sticker_group.command("preview")
    async def sticker_preview_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """预览指定 UUID 的表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        if not sticker_uuid:
            yield event.plain_result("请提供表情包 UUID：/sticker preview <uuid>")
            return
        result = await commands.handle_sticker(event, self, "preview", sticker_uuid)
        if isinstance(result, dict) and "image_path" in result:
            try:
                import base64
                from pathlib import Path

                file_path = Path(result["image_path"])
                if not file_path.exists():
                    yield event.plain_result(f"表情包文件不存在: {result['image_path']}")
                    return

                with open(file_path, "rb") as f:
                    data = f.read()
                bs64 = base64.b64encode(data).decode()
                from astrbot.core.message.components import Image

                yield event.chain_result([Image(f"base64://{bs64}")])
                return
            except Exception as e:
                logger.warning(f"[Sticker] 预览表情包失败: {e}")
                yield event.plain_result(f"预览失败: {e}")
                return
        yield event.plain_result(result)

    @sticker_group.command("delete")
    async def sticker_delete_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """删除指定表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "delete", sticker_uuid)
        yield event.plain_result(result)

    @sticker_group.command("clear")
    async def sticker_clear_cmd(self, event: AstrMessageEvent):
        """清空表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "clear", "")
        yield event.plain_result(result)

    @sticker_group.command("disable")
    async def sticker_disable_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """禁用指定表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "disable", sticker_uuid)
        yield event.plain_result(result)

    @sticker_group.command("enable")
    async def sticker_enable_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """启用指定表情包"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "enable", sticker_uuid)
        yield event.plain_result(result)

    @sticker_group.command("stats")
    async def sticker_stats_cmd(self, event: AstrMessageEvent):
        """查看表情包统计"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "stats", "")
        yield event.plain_result(result)

    @sticker_group.command("sync")
    async def sticker_sync_cmd(self, event: AstrMessageEvent):
        """同步本地表情包目录"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "sync", "")
        yield event.plain_result(result)

    @sticker_group.command("add")
    async def sticker_add_cmd(self, event: AstrMessageEvent):
        """添加表情包（发送图片后使用）"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return

        message_obj = getattr(event, "message_obj", None)
        if not message_obj or not hasattr(message_obj, "message"):
            yield event.plain_result("请在发送图片后使用此命令")
            return

        from astrbot.core.message.components import Image

        image_found = False
        for comp in message_obj.message:
            if isinstance(comp, Image):
                image_found = True
                raw_msg = getattr(event.message_obj, "raw_message", None)
                image_data = {}

                if raw_msg and hasattr(raw_msg, "get"):
                    raw_msg_list = raw_msg.get("message")
                    if raw_msg_list:
                        comp_file = getattr(comp, "file", "") or ""
                        for seg in raw_msg_list:
                            if isinstance(seg, dict) and seg.get("type") == "image":
                                seg_data = seg.get("data", {})
                                if isinstance(seg_data, dict) and seg_data.get("file") == comp_file:
                                    image_data = seg_data
                                    break

                if not image_data:
                    image_data = {
                        "file": getattr(comp, "file", "") or "",
                        "url": getattr(comp, "url", "") or "",
                        "sub_type": 0,
                    }

                result = await self.entertainment.add_sticker_from_image(event, image_data)
                yield event.plain_result(result["message"])
                return

        if not image_found:
            yield event.plain_result("请在发送图片后使用此命令，例如：发送图片后输入 /sticker add")

    @sticker_group.command("migrate")
    async def sticker_migrate_cmd(self, event: AstrMessageEvent):
        """从旧数据库迁移表情包到本地文件"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "migrate", "")
        yield event.plain_result(result)

    @filter.command("shut")
    async def shut_cmd(self, event: AstrMessageEvent, minutes: str = ""):
        """闭嘴命令：让AI暂停响应（只对当前群生效）
        用法：
        - /shut - 查看当前状态
        - /shut <分钟> - 让当前群闭嘴
        - /shut 0 - 取消当前群闭嘴
        """
        result = await commands.handle_shut(event, self, minutes)
        if result:
            yield event.plain_result(result)

    @filter.command("db")
    async def db_cmd(self, event: AstrMessageEvent, action: str = "", param: str = ""):
        """数据库管理命令"""
        if not commands.check_admin_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_db(event, self, action, param)
        yield event.plain_result(result)
