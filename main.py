import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field

import yaml

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent, Context, Star, register
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.core.message.components import Plain

from .commands.common import CommandContext, ensure_admin

# 可选组件：不是所有 AstrBot 版本都有，按需 import
try:
    from astrbot.core.message.components import WechatEmoji
except ImportError:
    WechatEmoji = None
try:
    from astrbot.core.message.components import Image as AstrImage
except ImportError:
    AstrImage = None
try:
    from astrbot.core.message.components import Face as AstrFace
except ImportError:
    AstrFace = None
try:
    from astrbot.core.message.components import Video as AstrVideo
except ImportError:
    AstrVideo = None
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry

from . import commands
from .cognition import SANSystem
from .config import PluginConfig

# 导入模块化组件
from .dao import SelfEvolutionDAO
from .engine.context_injection import build_identity_context, get_group_history, parse_message_chain
from .engine.eavesdropping import EavesdroppingEngine
from .engine.affinity import AffinityEngine
from .engine.entertainment import EntertainmentEngine
from .engine.event_context import extract_interaction_context
from .engine.persona_sim_engine import PersonaSimEngine
from .engine.persona_sim_consolidation import PersonaSimConsolidator
from .engine.persona_sim_injection import snapshot_to_debug_str, snapshot_to_prompt
from .engine.persona_arc.manager import PersonaArcManager
from .engine.message_normalization import ensure_event_message_text
from .engine.memory import MemoryManager
from .engine.memory_router import MemoryRouter
from .engine.memory_tools import MemoryTools
from .engine.memory_types import MemoryQueryIntent, MemoryQueryRequest
from .engine.persona import PersonaManager
from .engine.profile import ProfileManager

from .engine.profile_summary_service import ProfileSummaryService
from .engine.session_memory_store import SessionMemoryStore
from .engine.session_memory_summarizer import SessionMemorySummarizer
from .engine.sticker_store import StickerStore
from .engine.meal_store import MealStore
from .engine.text_utils import clean_result_text, should_clean_result
from .engine.caption_service import get_caption_for_target
from .engine.media_extractor import extract_media_targets
from .engine.moderation_classifier import (
    classify_nsfw_caption,
    classify_promo_caption,
    init_moderation_keywords,
    merge_moderation_results,
    ModerationCategory,
    RiskLevel,
)
from .engine.moderation_enforcer import enforce_moderation
from .engine.moderation_executor import _is_bot_admin_in_group
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


async def _poke_reply_async(plugin, target_id: str, user_id: str, group_id: str, sender_id: str):
    """Async implementation of poke reply."""
    complaint = random.choice(plugin.cfg.poke_complaint_texts)
    try:
        platform_insts = plugin.context.platform_manager.platform_insts
        if not platform_insts:
            return
        bot = platform_insts[0].get_client()

        if random.random() * 100 < plugin.cfg.poke_poke_back_chance:
            if group_id:
                await bot.call_action("group_poke", group_id=int(group_id), user_id=int(user_id))
            else:
                await bot.call_action("friend_poke", user_id=int(sender_id))
        else:
            if group_id:
                await bot.send_msg(
                    group_id=int(group_id),
                    message=[{"type": "text", "data": {"text": complaint}}],
                )
            else:
                await bot.send_msg(
                    user_id=int(sender_id),
                    message=[{"type": "text", "data": {"text": complaint}}],
                )
    except Exception as e:
        logger.debug(f"[Poke] 处理失败: {e}, 改发吐槽")
        try:
            platform_insts = plugin.context.platform_manager.platform_insts
            if not platform_insts:
                return
            bot = platform_insts[0].get_client()
            if group_id:
                await bot.send_msg(
                    group_id=int(group_id),
                    message=[{"type": "text", "data": {"text": complaint}}],
                )
            else:
                await bot.send_msg(
                    user_id=int(sender_id),
                    message=[{"type": "text", "data": {"text": complaint}}],
                )
        except Exception:
            pass


@register(
    "astrbot_plugin_self_evolution",
    "自我进化 (Self-Evolution)",
    "CognitionCore 7.0 数字生命。",
    "Ver 5.3.0",
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
        self._sticker_reply_timestamps: dict[str, list[float]] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}
        self.meals_dir = self.data_dir / "meals"
        self.meals_dir.mkdir(parents=True, exist_ok=True)
        self.meal_store = MealStore(self.meals_dir)
        db_path = os.path.join(self.data_dir, "self_evolution.db")

        # 配置系统（提前初始化，以便后续使用）
        self.cfg = PluginConfig(self)

        # 帮助主题存储

        # 初始化审核关键词（从配置读取，支持用户自定义）
        init_moderation_keywords(
            self.cfg.moderation_nsfw_keywords,
            self.cfg.moderation_promo_keywords,
            self.cfg.moderation_refusal_keywords,
            self.cfg.moderation_nsfw_refusal_confidence,
            self.cfg.moderation_promo_refusal_confidence,
            self.cfg.moderation_weak_keyword_confidence,
            self.cfg.moderation_confidence_threshold,
        )

        # 提示词注入配置
        self._prompts_injection = {}

        # 设置 Debug 日志模式
        self._setup_debug_logging()

        # 初始化模块化组件
        try:
            self.dao = SelfEvolutionDAO(db_path)
            self.eavesdropping = EavesdroppingEngine(self)
            self.memory = MemoryManager(self)
            self.persona = PersonaManager(self)
            self.profile = ProfileManager(self)
            # 正式服务对象（facade 背后）
            self.session_memory_store = SessionMemoryStore(self)
            self.session_memory_summarizer = SessionMemorySummarizer(self)
            self.profile_summary_service = ProfileSummaryService(self, self.profile)
            # 娱乐功能模块
            self.entertainment = EntertainmentEngine(self)
            # Persona 生活模拟引擎
            self.persona_sim = PersonaSimEngine(self)
            self.persona_consolidator = PersonaSimConsolidator(self)
            self.persona_arc = PersonaArcManager(self)
            # 关系温度引擎
            self.affinity = AffinityEngine(self)
            # 认知系统模块
            self.san_system = SANSystem(self)
            # 反思模块
            from .engine.reflection import SessionReflection, DailyBatchProcessor

            self.session_reflection = SessionReflection(self)
            self.daily_batch = DailyBatchProcessor(self)
            self.memory_router = MemoryRouter(self)
            self.memory_tools = MemoryTools(self)
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, Entertainment, ImageCache, Memory, Persona, Profile, SAN, Reflection, SessionMemory*, Profile*) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 7.0: 状态容器
        self._pending_db_reset = {}  # 待确认的数据库操作 {user_id: {"action": str, "expires_at": timestamp}}
        self._shut_until_by_group = {}  # 群级别闭嘴 {群号: 截止时间}
        self._group_umo_cache = {}  # 最近见过的群会话来源 {group_id: unified_msg_origin}
        self._private_umo_cache = {}  # 最近见过的私聊会话来源 {private_user_id: unified_msg_origin}
        self._scope_registry_touch_cache = {}  # 会话范围持久化防抖 {scope_id: last_touch_timestamp}
        self._last_cache_cleanup = 0.0  # 上次缓存清理时间

    def _cleanup_stale_caches(self):
        """清理过期缓存条目，防止无限膨胀。"""
        now = time.time()
        if now - self._last_cache_cleanup < 300:
            return
        self._last_cache_cleanup = now

        expired_keys = [(k, v) for k, v in self._group_umo_cache.items() if now - v.get("_cached_at", 0) > 3600]
        for k, _ in expired_keys:
            del self._group_umo_cache[k]

        expired_keys = [(k, v) for k, v in self._private_umo_cache.items() if now - v.get("_cached_at", 0) > 3600]
        for k, _ in expired_keys:
            del self._private_umo_cache[k]

        expired_keys = [k for k, v in self._scope_registry_touch_cache.items() if now - v > 86400]
        for k in expired_keys:
            del self._scope_registry_touch_cache[k]

        expired_keys = [k for k, v in self._pending_db_reset.items() if now > v.get("expires_at", 0)]
        for k in expired_keys:
            del self._pending_db_reset[k]

    def remember_group_umo(self, group_id, umo: str | None, user_id=None):
        """Remember the latest unified message origin for a group or private scope."""
        self._cleanup_stale_caches()
        now = time.time()
        if group_id and umo:
            self._group_umo_cache[str(group_id)] = {"umo": str(umo), "_cached_at": now}
        elif user_id and umo:
            private_scope_id = self._resolve_profile_scope_id(None, user_id)
            self._private_umo_cache[private_scope_id] = {"umo": str(umo), "_cached_at": now}

    def get_group_umo(self, group_id) -> str | None:
        """Return the latest cached unified message origin for a group."""
        if not group_id:
            return None
        entry = self._group_umo_cache.get(str(group_id))
        return entry.get("umo") if entry else None

    def get_scope_umo(self, scope_id) -> str | None:
        """Return the latest cached unified message origin for a group/private scope."""
        if not scope_id:
            return None
        scope_id = str(scope_id)
        if scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            entry = self._private_umo_cache.get(scope_id)
        else:
            entry = self._group_umo_cache.get(scope_id)
        return entry.get("umo") if entry else None

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
        if self._should_inject_private_history(ctx):
            parts.append(await self._build_private_history_injection(ctx))
        if self._should_inject_profile(ctx):
            parts.append(await self._build_profile_injection(ctx))
        if self._should_inject_kb_memory(ctx):
            parts.append(await self._build_kb_memory_injection(ctx))

        reflection_hint, explicit_facts = await self._build_reflection_injection(ctx)
        if reflection_hint:
            parts.append(reflection_hint)

        parts.append(await self._build_behavior_hints(ctx))

        sim_block = ""
        if ctx.scope_id and hasattr(self, "persona_sim") and self.persona_sim:
            try:
                snapshot = await self.persona_sim.get_snapshot(str(ctx.scope_id))
                if snapshot:
                    from .engine.persona_sim_injection import snapshot_to_prompt

                    sim_block = snapshot_to_prompt(snapshot)
            except Exception:
                pass
        if sim_block:
            parts.append(sim_block)

        arc_block = ""
        if ctx.scope_id and hasattr(self, "persona_arc") and self.persona_arc:
            try:
                arc_block = await self.persona_arc.build_prompt(str(ctx.scope_id))
            except Exception:
                pass
        if arc_block:
            parts.append(arc_block)

        if explicit_facts:
            await self._writeback_reflection_facts(ctx, explicit_facts)

        self._apply_prompt_injections(req, parts)

        if self.cfg.memory_debug_enabled:
            profile_hit = self._should_inject_profile(ctx)
            kb_hit = self._should_inject_kb_memory(ctx)
            history_hit = self._should_inject_group_history(ctx)
            total_len = sum(len(p) for p in parts if isinstance(p, str))
            truncated = "yes" if total_len > self.cfg.max_prompt_injection_length else "no"
            logger.debug(
                f"[MemoryInject] profile={'hit' if profile_hit else 'miss'} "
                f"kb={'hit' if kb_hit else 'miss'} history={'hit' if history_hit else 'miss'} "
                f"reflection={'hit' if reflection_hint else 'miss'} persona_sim={'hit' if sim_block else 'miss'} truncated={truncated}"
            )

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

        logger.debug(f"[SelfEvolution] 进入 LLM 请求拦截层。用户: {user_id}")

        if self.cfg.disable_framework_contexts:
            req.contexts = []

        if self.san_enabled and not await self.san_system.update():
            logger.warning(f"[SAN] 精力耗尽，拒绝服务: {user_id}")
            req.system_prompt = "我现在很累，脑容量超载了。让我安静一会。"
            return None

        affinity = await self.dao.get_affinity(user_id)

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

    def _should_inject_private_history(self, ctx: PromptContext) -> bool:
        return bool(self.cfg.inject_group_history and not ctx.group_id and ctx.user_id)

    def _should_inject_profile(self, ctx: PromptContext) -> bool:
        return self.enable_profile_injection and (((ctx.has_reply or ctx.has_at) and ctx.is_group) or not ctx.is_group)

    def _should_inject_kb_memory(self, ctx: PromptContext) -> bool:
        return self.enable_kb_memory_recall

    def _build_identity_injection(self, ctx: PromptContext) -> str:
        affinity = ctx.affinity
        if affinity >= 80:
            affinity_desc = "很熟"
        elif affinity >= 60:
            affinity_desc = "还行"
        elif affinity >= 30:
            affinity_desc = "一般"
        else:
            affinity_desc = "陌生"

        role_str = f"（{ctx.role_info}）" if ctx.role_info else ""

        parts = [
            f"用户：{ctx.sender_name}{role_str}",
            f"你们的关系：{affinity_desc}",
        ]
        if ctx.is_group:
            parts.append("场景：群聊")
            ctx_parts = []
            if ctx.quoted_info:
                ctx_parts.append(f"引用了「{ctx.quoted_info}」")
            if ctx.at_info:
                ctx_parts.append("有人@了你")
            if ctx_parts:
                parts.append("当前：" + "，".join(ctx_parts))
        else:
            parts.append("场景：私聊")
        if ctx.ai_context_info:
            parts.append(ctx.ai_context_info)

        return "\n\n[背景信息]\n" + "\n".join(parts) + "\n"

    async def _build_group_history_injection(self, ctx: PromptContext) -> str:
        if not ctx.group_id:
            return ""
        hist_str = await get_group_history(self, ctx.group_id, self.cfg.group_history_count)
        if not hist_str:
            return ""
        return f"\n\n[最近群消息]\n{hist_str}\n"

    async def _build_private_history_injection(self, ctx: PromptContext) -> str:
        if ctx.group_id or not ctx.user_id:
            return ""
        from .engine.context_injection import get_private_history

        hist_str = await get_private_history(self, ctx.user_id, self.cfg.group_history_count)
        if not hist_str:
            return ""
        return f"\n\n[最近私聊消息]\n{hist_str}\n"

    async def _build_profile_injection(self, ctx: PromptContext) -> str:
        result = await self.memory_tools.query_service.query(
            MemoryQueryRequest(
                scope_id=ctx.profile_scope_id,
                user_id=ctx.user_id,
                query="",
                intent=MemoryQueryIntent.USER_PROFILE,
                limit=8,
            )
        )
        if not result.text:
            return ""
        return f"\n\n[对该用户的了解]\n{result.text}\n"

    async def _build_kb_memory_injection(self, ctx: PromptContext) -> str:
        if not getattr(self.cfg, "memory_enabled", True):
            return ""
        result = await self.memory_tools.query_service.query(
            MemoryQueryRequest(
                scope_id=ctx.scope_id,
                user_id="",
                query=ctx.msg_text,
                intent=MemoryQueryIntent.FALLBACK_KB,
                limit=3,
            )
        )
        if not result.text:
            return ""
        return f"\n\n[知识库记忆]\n{result.text}\n"

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
            parts.append(f"[自我校准] {note[:100]}")
        if bias:
            parts.append(f"[认知纠偏] {bias[:80]}")
        if facts_str and len(facts_str) > 3:
            explicit_facts = [f.strip() for f in facts_str.split("|") if f.strip()][:3]
            if explicit_facts:
                parts.append("[已知事实]\n" + "\n".join(f"- {f[:50]}" for f in explicit_facts))
            all_facts = [f.strip() for f in facts_str.split("|") if f.strip()]
        else:
            all_facts = []

        injection = "\n".join(parts) if parts else ""
        tag = f"\n\n{injection}\n" if injection else ""
        return tag, all_facts

    async def _build_behavior_hints(self, ctx: PromptContext, is_active_trigger: bool = False) -> str:
        parts = []

        if is_active_trigger:
            parts.append(
                "[当前场景]\n"
                "你正在主动参与群聊。看到有意思的话题就自然插一句，不用等被问。\n"
                "短一点，像平时跟朋友聊天那样。"
            )

        if self._should_inject_preference_hints(ctx):
            parts.append("[记忆]\n用户透露了个人信息可以顺手记下来，调用 upsert_cognitive_memory 即可。")

        if self._should_inject_surprise_detection(ctx):
            if any(kw in ctx.msg_text for kw in self.surprise_boost_keywords):
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
                parts.append("[表情包]\n" + sticker_injection)

        # 时间感知注入
        from datetime import datetime

        hour = datetime.now().hour
        time_hint = self._get_time_profile_hint(hour)
        if time_hint:
            parts.append(time_hint)

        # 好感度驱动语气
        affinity_hint = self._get_affinity_profile_hint(ctx.affinity)
        if affinity_hint:
            parts.append(affinity_hint)

        reply_format = self._get_reply_format()
        if reply_format:
            parts.append(reply_format)

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
        self._last_llm_system_prompt = req.system_prompt

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

    def _get_time_profile_hint(self, hour: int) -> str:
        try:
            if not self._prompts_injection:
                return ""
            profiles = self._prompts_injection.get("time_profiles", {})
            if 23 <= hour or hour < 6:
                return profiles.get("late_night", {}).get("hint", "")
            elif 6 <= hour < 9:
                return profiles.get("morning", {}).get("hint", "")
        except Exception:
            pass
        return ""

    def _get_affinity_profile_hint(self, affinity: int) -> str:
        try:
            if not self._prompts_injection:
                return ""
            profiles = self._prompts_injection.get("affinity_profiles", {})
            if affinity >= 80:
                return profiles.get("high", {}).get("hint", "")
            elif affinity >= 60:
                return profiles.get("normal", {}).get("hint", "")
            elif affinity < 30:
                return profiles.get("low", {}).get("hint", "")
        except Exception:
            pass
        return ""

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_media_extraction_listener(self, event: AstrMessageEvent):
        """Phase 1+2+4: 消息媒体目标抽取 -> Caption -> 审核分类。"""
        group_id = event.get_group_id() or ""
        if group_id:
            try:
                asyncio.create_task(self.persona_sim.tick_time_only(group_id))
            except Exception:
                pass

        if not await _is_bot_admin_in_group(event, group_id):
            return

        user_id = str(event.get_sender_id() or "")
        message_id = ""
        try:
            raw_msg = getattr(getattr(event, "message_obj", None), "raw_message", None)
            if raw_msg and isinstance(raw_msg, dict):
                message_id = str(raw_msg.get("message_id", ""))
            if not message_id:
                message_id = str(getattr(event, "message_id", ""))
            if not message_id:
                message_id = str(event.get_id() if hasattr(event, "get_id") else "")
        except Exception:
            pass
        message_id = message_id or ""

        msg_chain = event.get_messages()
        if not msg_chain:
            return

        has_media = any(
            getattr(c, "type", None) in ("image", "video", "forward", "reply", "Forward", "Reply")
            or (AstrImage and isinstance(c, AstrImage))
            or (AstrVideo and isinstance(c, AstrVideo))
            for c in msg_chain
        )
        if not has_media:
            return

        try:
            targets = await extract_media_targets(event)
        except Exception as e:
            logger.warning(f"[MediaExtractor] 抽取异常: {e}", exc_info=True)
            return

        logger.info(
            f"[MediaExtractor] group={group_id} user={user_id} msg={message_id} "
            f"targets={len(targets)} can_process={[t.can_process_now for t in targets]}"
        )
        for t in targets:
            candidates_summary = [
                {k: v[:40] if isinstance(v, str) else v}
                for c in t.resource_candidates
                for k, v in c.to_dict().items()
                if v and k != "raw_component_type"
            ]
            logger.info(
                f"[MediaExtractor] kind={t.kind.value} origin={t.origin.value} "
                f"can={t.can_process_now} reason={t.reason} "
                f"candidates={candidates_summary}"
            )

            cap_result = await get_caption_for_target(t, self.context, self.dao)
            logger.info(
                f"[CaptionService] kind={cap_result.kind.value} origin={cap_result.origin.value} "
                f"success={cap_result.success} cached={getattr(cap_result, 'cache_hit', False)} provider={cap_result.provider_id} "
                f"model={cap_result.model_name} resource={cap_result.resource_key[:30] if cap_result.resource_key else ''} "
                f"reason={cap_result.reason} text={cap_result.text[:80] if cap_result.text else ''!r}"
            )

            if cap_result.success:
                nsfw_res = classify_nsfw_caption(cap_result)
                promo_res = classify_promo_caption(cap_result)
                merged_res = merge_moderation_results(nsfw_res, promo_res)
                logger.info(
                    f"[Moderation] nsfw={nsfw_res.category}/{nsfw_res.confidence}/{nsfw_res.risk_level}/{nsfw_res.suggested_action} "
                    f"promo={promo_res.category}/{promo_res.confidence}/{promo_res.risk_level}/{promo_res.suggested_action} "
                    f"merged={merged_res.category}/{merged_res.confidence}/{merged_res.risk_level}/{merged_res.suggested_action} "
                    f"reasons={merged_res.reasons}"
                )

                logger.info(
                    f"[Moderation] 调用 enforce_moderation: message_id={message_id!r} group={group_id} user={user_id}"
                )
                enf_result = await enforce_moderation(
                    event,
                    group_id=group_id,
                    user_id=user_id,
                    message_id=message_id,
                    caption_result=cap_result,
                    nsfw_result=nsfw_res,
                    promo_result=promo_res,
                    merged_result=merged_res,
                    enforcement_enabled=self.cfg.moderation_enforcement_enabled,
                    dao=self.dao,
                    escalation_threshold=self.cfg.moderation_escalation_threshold,
                    ban_duration_minutes=self.cfg.moderation_ban_duration_minutes,
                    nsfw_warning_message=self.cfg.moderation_nsfw_warning_message,
                    nsfw_ban_reason_message=self.cfg.moderation_nsfw_ban_reason_message,
                    promo_warning_message=self.cfg.moderation_promo_warning_message,
                    promo_ban_reason_message=self.cfg.moderation_promo_ban_reason_message,
                )
                logger.info(
                    f"[Moderation] mode={'dry-run' if enf_result.dry_run else 'execute'} "
                    f"action={enf_result.final_action} "
                    f"evidence={'ok' if enf_result.evidence_written else 'fail'} "
                    f"group={group_id} user={user_id} msg={message_id} "
                    f"category={merged_res.category} confidence={merged_res.confidence}"
                )
            else:
                logger.info(f"[Moderation] skipped - caption failed: {cap_result.reason}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 7.0: 被动监听 - 滑动上下文窗口"""
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj:
            from astrbot.core.message.components import Poke

            for comp in getattr(msg_obj, "message", []):
                if isinstance(comp, Poke) and comp.id:
                    target_id = str(comp.id)
                    sender_id = str(getattr(msg_obj.sender, "user_id", ""))
                    group_id = str(getattr(msg_obj, "group_id", "") or "")
                    bot_id = str(getattr(msg_obj, "self_id", "") or "")
                    if target_id == bot_id:
                        asyncio.create_task(_poke_reply_async(self, target_id, sender_id, group_id, sender_id))
                    event.stop_event()
                    return

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
                if not event.is_admin():
                    event.stop_event()
                    return
                logger.debug(f"[SelfEvolution] 管理员跳过闭嘴拦截")
            else:
                self._shut_until_by_group.pop(group_id, None)

        logger.debug(f"[SelfEvolution] 收到消息: {event.message_str[:30] if event.message_str else '(空)'}")

        # 检查用户好感度熔断
        user_id = event.get_sender_id()
        affinity = await self.dao.get_affinity(user_id)
        if affinity <= 0:
            logger.debug(f"[SelfEvolution] 用户 {user_id} 好感度已熔断，拦截")
            if not event.is_admin():
                event.stop_event()
                return
            logger.debug(f"[SelfEvolution] 管理员跳过好感度熔断")

        # 写入 interaction extras（基于 At/Reply 组件，不依赖 NapCat 原始 payload）
        interaction = extract_interaction_context(
            event.get_messages(),
            persona_name=self.cfg.persona_name,
            bot_id=self._get_bot_id(),
        )
        has_at_to_bot = bool(interaction["at_info"])
        has_reply_to_bot = bool(interaction["quoted_info"])
        event.set_extra("is_at", has_at_to_bot)
        event.set_extra("has_reply", has_reply_to_bot)

        # 纯命令消息（is_at_or_wake_command=True 且无 @/reply 且是群聊）不触发互动意愿系统
        # 私聊始终放行（AstrBot 在 friend_message_needs_wake_prefix=false 时也会设 is_at_or_wake_command=True）
        if event.is_at_or_wake_command and not has_at_to_bot and not has_reply_to_bot and group_id:
            return

        group_id = event.get_group_id()
        msg_text = await ensure_event_message_text(event, self.dao)

        # 关系温度自动积累（弱信号）
        asyncio.create_task(self.affinity.process_message(event))

        # 表情包学习：检测指定人的图片
        if group_id and self.cfg.sticker_learning_enabled:
            asyncio.create_task(self.entertainment.learn_sticker_from_event(event))

        # 被动插嘴：新版社交参与引擎
        await self.eavesdropping.process_passive_engagement(event)

        # 群菜单自然语言触发（跳过机器人自己的消息，防止重入）
        sender_id = str(event.get_sender_id())
        bot_id = self._get_bot_id()
        if group_id and msg_text and sender_id != bot_id:
            asyncio.create_task(self.entertainment.handle_meal_nl_trigger(event, msg_text))

        # PersonaArc 人格弧线浇灌
        if group_id and self.cfg.persona_arc_enabled:
            asyncio.create_task(
                self.persona_arc.pour_from_message(
                    scope_id=str(group_id),
                    text=msg_text,
                    direct=has_at_to_bot or has_reply_to_bot,
                )
            )

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        if not self.cfg.sticker_reply_enabled:
            if not should_clean_result(event):
                return
            result = event.get_result()
            if not result or not result.chain:
                return
            for comp in result.chain:
                if isinstance(comp, Plain) and comp.text:
                    comp.text = clean_result_text(comp.text)
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        group_id = event.get_group_id()
        if not group_id:
            return

        if not all(isinstance(c, Plain) for c in result.chain):
            return

        plain_texts = [c.text for c in result.chain if isinstance(c, Plain) and c.text]
        total_len = sum(len(t) for t in plain_texts)
        if total_len < self.cfg.sticker_reply_min_text_length:
            logger.debug(f"[Sticker] text too short: {total_len} < {self.cfg.sticker_reply_min_text_length}")
            return

        now = time.time()
        hourly_limit = self.cfg.sticker_reply_max_per_hour
        key = f"sticker_reply:{group_id}"
        timestamps = self._sticker_reply_timestamps.get(key, [])
        timestamps = [t for t in timestamps if now - t < 3600]
        if len(timestamps) >= hourly_limit:
            logger.debug(f"[Sticker] hourly limit reached: {len(timestamps)}/{hourly_limit}")
            return
        roll = random.randint(1, 100)
        if roll > self.cfg.sticker_reply_chance:
            logger.debug(f"[Sticker] roll {roll} > chance {self.cfg.sticker_reply_chance}")
            return

        sticker = await self.sticker_store.get_random_sticker()
        if not sticker:
            logger.debug(f"[Sticker] no sticker from store")
            return

        file_path = self.sticker_store.get_sticker_path(sticker)
        if not file_path:
            logger.debug(f"[Sticker] get_sticker_path returned None")
            return
        file_path_str = os.path.normpath(str(file_path))
        if not os.path.exists(file_path_str):
            logger.debug(f"[Sticker] file not found: {file_path_str}")
            return

        logger.debug(f"[Sticker] appending: {file_path_str}")
        if AstrImage:
            result.chain.append(AstrImage.fromFileSystem(file_path_str))
        timestamps.append(now)
        self._sticker_reply_timestamps[key] = timestamps

        if not should_clean_result(event):
            return
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                comp.text = clean_result_text(comp.text)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            return
        result = event.get_result()
        if not result or not hasattr(result, "chain") or not result.chain:
            return
        await self.eavesdropping.sync_framework_reply_state(group_id, level="full")

        has_text = False
        has_emoji = False
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                cleaned = clean_result_text(comp.text)
                if cleaned:
                    self.eavesdropping._output_guard._add_recent(cleaned)
                    has_text = True
            elif WechatEmoji and isinstance(comp, WechatEmoji):
                has_emoji = True
            elif AstrImage and isinstance(comp, AstrImage):
                has_emoji = True
            elif AstrFace and isinstance(comp, AstrFace):
                has_emoji = True
        if has_text:
            self.eavesdropping._stats.record_passive_text(group_id)
        elif has_emoji:
            self.eavesdropping._stats.record_passive_emoji(group_id)
        await self.eavesdropping.persist_stats(group_id)

    async def inject_and_chat(
        self,
        req,
        umo,
        scope_id: str = "",
    ):
        """Builds a generation spec and calls LLM directly without standard
        user-message-driven request hooks.

        Injects: persona, identity, history, profile, memory, behavior hints.
        Does not call on_llm_request hooks since they are designed for
        user-message-driven requests and active trigger is self-initiated.
        Persona sim injection is done here directly instead.
        """
        try:
            if scope_id and hasattr(self, "persona_sim") and self.persona_sim:
                try:
                    snapshot = await self.persona_sim.get_snapshot(scope_id)
                    if snapshot:
                        from .engine.persona_sim_injection import snapshot_to_prompt

                        sim_block = snapshot_to_prompt(snapshot)
                    if sim_block:
                        req.system_prompt += "\n\n" + sim_block
                except Exception:
                    pass

            if self.cfg.debug_log_enabled:
                logger.debug(
                    f"[InjectAndChat] ===== 主动触发 Prompt (共 {len(req.system_prompt)} 字符) =====\n"
                    f"{req.system_prompt}\n"
                    f"===== Prompt End =====\n"
                    f"[InjectAndChat] user_prompt: {req.prompt[:200] if req.prompt else '(none)'}..."
                )
            llm_provider = self.context.get_using_provider(umo=umo)
            resp = await llm_provider.text_chat(
                prompt=req.prompt,
                system_prompt=req.system_prompt,
                contexts=req.contexts,
            )
            return resp.completion_text.strip()[:200] if hasattr(resp, "completion_text") else None
        except Exception as e:
            logger.warning(f"[InjectAndChat] LLM调用失败: {e}")
            return None

    async def build_generation_spec(
        self,
        group_id: str,
        user_id: str,
        sender_name: str,
        trigger_text: str,
        scene: str,
        decision,  # SpeechDecision
        anchor_text: str = "",
        quoted_info: str = "",
        at_info: str = "",
        pending_trigger_hint: str = "",
    ) -> ProviderRequest | None:
        """Build ProviderRequest using unified ContextBuilder.

        All text generation now uses the same prompt injection pipeline.
        The difference between active and passive is only in the decision.mode.
        """
        try:
            memory_scope_id = self._resolve_profile_scope_id(group_id, user_id)
            await self.touch_known_scope(memory_scope_id)

            affinity = await self.dao.get_affinity(user_id)
            bot_id = self._get_bot_id()
            umo = getattr(self, "get_group_umo", lambda g: None)(group_id) if hasattr(self, "get_group_umo") else None
            if not umo:
                return None

            ctx = PromptContext(
                user_id=user_id,
                sender_name=sender_name,
                group_id=group_id,
                scope_id=memory_scope_id,
                profile_scope_id=memory_scope_id,
                umo=umo,
                msg_text=trigger_text,
                affinity=affinity,
                role_info="",
                is_group=True,
                quoted_info=quoted_info,
                ai_context_info="",
                at_targets=[at_info] if at_info else [],
                at_info=at_info,
                has_reply=bool(quoted_info),
                has_at=bool(at_info),
                bot_id=bot_id,
                event=None,
            )

            from .engine.generation_context import ContextBuilder

            builder = ContextBuilder(self)
            gc = await builder.build(ctx, decision, anchor_text, scene, pending_trigger_hint=pending_trigger_hint)
            if self.cfg.debug_log_enabled and gc.persona_prompt:
                logger.debug(f"[BuildSpec] persona_prompt ({len(gc.persona_prompt)} chars): {gc.persona_prompt}")
            spec = builder.build_generation_spec(gc, decision)

            req = ProviderRequest(
                prompt=spec.user_prompt,
                system_prompt=spec.system_prompt,
                contexts=[],
            )
            return req
        except Exception as e:
            logger.warning(f"[BuildGenerationSpec] 构建失败: {e}")
            return None

    async def _get_active_persona_prompt(self, umo: str) -> str:
        """从当前活跃会话获取 persona prompt。"""
        try:
            if not hasattr(self.context, "conversation_manager"):
                return self.persona_name or "你是一个在群聊中自然参与讨论的角色。"
            conv_mgr = self.context.conversation_manager
            cid = await conv_mgr.get_curr_conversation_id(umo)
            if not cid:
                return self.persona_name or "你是一个在群聊中自然参与讨论的角色。"
            conv = await conv_mgr.get_conversation(umo, cid)
            conversation_persona_id = getattr(conv, "persona_id", None) if conv else None
            cfg = self.context.get_config(umo=umo).get("provider_settings", {})
            _, personality, _, _ = await self.context.persona_manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conversation_persona_id,
                platform_name=getattr(self, "_platform_name", "unknown"),
                provider_settings=cfg,
            )
            if personality and isinstance(personality, dict) and personality.get("prompt"):
                return personality["prompt"]
        except Exception:
            pass
        return self.persona_name or "你是一个在群聊中自然参与讨论的角色。"

    @filter.on_plugin_loaded()
    async def on_loaded(self, metadata):
        """插件加载完成后，注册定时任务"""
        logger.info("[SelfEvolution] on_loaded 开始执行")
        await register_tasks(self)

        asyncio.create_task(self.sticker_store.sync_from_files())

    @filter.command_group("se")
    def se_group(self):
        """系统命令"""

    @se_group.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """查看插件帮助"""
        result = await commands.handle_help_text(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @se_group.command("version")
    async def show_version(self, event: AstrMessageEvent):
        """查看插件版本"""
        result = await commands.handle_version(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @filter.command_group("arc")
    def arc_group(self):
        """人格弧线"""

    def _check_arc_admin(self, event: AstrMessageEvent) -> bool:
        return bool(event.is_admin())

    @arc_group.command("status")
    async def arc_status(self, event: AstrMessageEvent, scope: str = ""):
        """查看人格弧线状态"""
        if not self._check_arc_admin(event):
            yield event.plain_result("此指令仅限管理员使用。")
            return

        if not hasattr(self, "persona_arc") or not self.persona_arc:
            yield event.plain_result("Persona Arc 不可用。")
            return

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())

        if scope.strip():
            target_scope = scope.strip()
        elif group_id:
            target_scope = str(group_id)
        else:
            target_scope = self._resolve_profile_scope_id(None, sender_id)

        event.set_extra("self_evolution_command_reply", True)
        result = await self.persona_arc.get_status_text(target_scope)
        yield event.plain_result(result)

    @arc_group.command("prompt")
    async def arc_prompt(self, event: AstrMessageEvent, scope: str = ""):
        """查看当前阶段注入的 prompt"""
        if not self._check_arc_admin(event):
            yield event.plain_result("此指令仅限管理员使用。")
            return

        if not hasattr(self, "persona_arc") or not self.persona_arc:
            yield event.plain_result("Persona Arc 不可用。")
            return

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())

        if scope.strip():
            target_scope = scope.strip()
        elif group_id:
            target_scope = str(group_id)
        else:
            target_scope = self._resolve_profile_scope_id(None, sender_id)

        event.set_extra("self_evolution_command_reply", True)
        result = await self.persona_arc.get_prompt_preview(target_scope)
        yield event.plain_result(result)

    @arc_group.command("emotions")
    async def arc_emotions(self, event: AstrMessageEvent, scope: str = ""):
        """查看当前 scope 已解锁的情感"""
        if not self._check_arc_admin(event):
            yield event.plain_result("此指令仅限管理员使用。")
            return

        if not hasattr(self, "persona_arc") or not self.persona_arc:
            yield event.plain_result("Persona Arc 不可用。")
            return

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())

        if scope.strip():
            target_scope = scope.strip()
        elif group_id:
            target_scope = str(group_id)
        else:
            target_scope = self._resolve_profile_scope_id(None, sender_id)

        if not self.persona_arc.enabled:
            yield event.plain_result("Persona Arc 未启用。")
            return

        arc_id = self.persona_arc.profile.arc_id if self.persona_arc.profile else ""
        emotions = await self.persona_arc.emotions.list_emotions(target_scope, arc_id, limit=20)

        if not emotions:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[Persona Arc 情感图鉴]\n当前暂无已解锁的情感。")
            return

        lines = ["[Persona Arc 情感图鉴]"]
        for em in emotions:
            name = em.get("emotion_name", "")
            definition = em.get("definition_by_user", "")
            confidence = em.get("confidence", 0.8)
            if name and definition:
                lines.append(f"- {name}：{definition}（置信度: {confidence:.0%}）")

        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result("\n".join(lines))

    @arc_group.command("ruminations")
    async def arc_ruminations(self, event: AstrMessageEvent, scope: str = ""):
        """管理员查看最近反刍"""
        if not self._check_arc_admin(event):
            yield event.plain_result("此指令仅限管理员使用。")
            return

        if not hasattr(self, "persona_arc") or not self.persona_arc:
            yield event.plain_result("Persona Arc 不可用。")
            return

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())
        if scope.strip():
            target_scope = scope.strip()
        elif group_id:
            target_scope = str(group_id)
        else:
            target_scope = self._resolve_profile_scope_id(None, sender_id)

        if not self.persona_arc.enabled:
            yield event.plain_result("Persona Arc 未启用。")
            return

        arc_id = self.persona_arc.profile.arc_id if self.persona_arc.profile else ""
        ruminations = await self.persona_arc.ruminations.list_ruminations(target_scope, arc_id, limit=10)

        if not ruminations:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[Persona Arc 反刍记录]\n当前暂无离线反刍。")
            return

        lines = ["[Persona Arc 反刍记录]"]
        for rum in ruminations:
            text = rum.get("text", "")
            injected = rum.get("injected", 0)
            status = "已注入" if injected else "待注入"
            if text:
                lines.append(f"- [{status}] {text}")

        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result("\n".join(lines))

    @arc_group.command("debug_pour")
    async def arc_debug_pour(self, event: AstrMessageEvent, amount: str = "", reason: str = ""):
        """调试：增加 progress（仅管理员，amount > 0）"""
        if not self._check_arc_admin(event):
            yield event.plain_result("此指令仅限管理员使用。")
            return

        if not hasattr(self, "persona_arc") or not self.persona_arc:
            yield event.plain_result("Persona Arc 不可用。")
            return

        try:
            value = float(amount)
        except (ValueError, TypeError):
            yield event.plain_result("用法：/arc debug_pour <amount> [reason]")
            return

        if value <= 0:
            yield event.plain_result("amount 必须大于 0。")
            return

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())
        if group_id:
            target_scope = str(group_id)
        else:
            target_scope = self._resolve_profile_scope_id(None, sender_id)

        event.set_extra("self_evolution_command_reply", True)
        result = await self.persona_arc.debug_add_progress(target_scope, value, reason or "debug")
        yield event.plain_result(result)

    @filter.command("今日老婆")
    async def today_waifu(self, event: AstrMessageEvent):
        """今日老婆功能 - 随机抽取一名群友"""
        from astrbot.core.message.components import Image

        if not getattr(self.cfg, "entertainment_enabled", True):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("娱乐模块当前已关闭。")
            return

        result = await self.entertainment.today_waifu(event)
        if isinstance(result, list) and len(result) == 2:
            event.set_extra("self_evolution_command_reply", True)
            yield event.chain_result([Image.fromURL(result[1]), Plain(result[0])])

    @filter.command_group("meal")
    def meal_group(self):
        """群菜单管理"""
        pass

    @meal_group.command("ban")
    async def meal_ban(self, event: AstrMessageEvent, target_user_id: str = ""):
        """禁止某用户添加菜品（仅管理员）"""
        if not event.is_admin():
            yield event.plain_result("此指令仅限管理员使用。")
            return
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅限群聊使用。")
            return
        raw = target_user_id.strip().lstrip("@")
        if not raw:
            yield event.plain_result("请 @ 要禁止的用户或提供 QQ 号，例如：/meal ban @xxx 或 /meal ban 123456")
            return
        if raw.isdigit():
            qq = raw
        else:
            from .engine.event_context import extract_interaction_context

            bot_id = self._get_bot_id()
            interaction = extract_interaction_context(
                event.get_messages(), persona_name=self.persona_name, bot_id=bot_id
            )
            at_targets = interaction.get("at_targets", [])
            qq = at_targets[0] if at_targets else raw
        success, message = await self.meal_store.ban_user(group_id, qq)
        yield event.plain_result(message)

    @meal_group.command("unban")
    async def meal_unban(self, event: AstrMessageEvent, target_user_id: str = ""):
        """解除某用户添加菜品的限制（仅管理员）"""
        if not event.is_admin():
            yield event.plain_result("此指令仅限管理员使用。")
            return
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅限群聊使用。")
            return
        raw = target_user_id.strip().lstrip("@")
        if not raw:
            yield event.plain_result("请 @ 要解禁的用户或提供 QQ 号，例如：/meal unban @xxx 或 /meal unban 123456")
            return
        if raw.isdigit():
            qq = raw
        else:
            from .engine.event_context import extract_interaction_context

            bot_id = self._get_bot_id()
            interaction = extract_interaction_context(
                event.get_messages(), persona_name=self.persona_name, bot_id=bot_id
            )
            at_targets = interaction.get("at_targets", [])
            qq = at_targets[0] if at_targets else raw
        success, message = await self.meal_store.unban_user(group_id, qq)
        yield event.plain_result(message)

    @filter.command("addmeal")
    async def add_meal(self, event: AstrMessageEvent, meal_name: str = ""):
        """添加菜品到群菜单"""
        group_id = event.get_group_id()
        if not group_id:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("此指令仅限群聊使用。")
            return

        if not meal_name or not meal_name.strip():
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("请提供菜名，例如：/addmeal 红烧肉")
            return

        if not getattr(self.cfg, "entertainment_enabled", True):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("娱乐模块当前已关闭。")
            return

        max_items = getattr(self.cfg, "meal_max_items", 100)
        user_id = event.get_sender_id()
        if await self.meal_store.is_user_banned(group_id, user_id):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("你被管理员禁止添加菜品，如有异议请联系管理员。")
            return
        success, message = await self.meal_store.add_meal(group_id, meal_name.strip(), max_items)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(message)

    @filter.command("delmeal")
    async def del_meal(self, event: AstrMessageEvent, meal_name: str = ""):
        """从群菜单删除菜品"""
        group_id = event.get_group_id()
        if not group_id:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("此指令仅限群聊使用。")
            return

        if not meal_name or not meal_name.strip():
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("请提供菜名，例如：/delmeal 红烧肉")
            return

        if not getattr(self.cfg, "entertainment_enabled", True):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("娱乐模块当前已关闭。")
            return

        if meal_name.strip().lower() == "all":
            if not event.is_admin():
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result("清空菜单仅限管理员使用。")
                return
            success, message = await self.meal_store.clear_meals(group_id)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(message)
            return

        success, message = await self.meal_store.del_meal(group_id, meal_name.strip())
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(message)

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        反思结果会在下次对话时注入到AI的思考中。
        """
        if not getattr(self.cfg, "reflection_enabled", True):
            event.set_extra("self_evolution_command_reply", True)
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
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(result_msg)
            else:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result("认知蒸馏失败，请稍后再试。")
        except Exception as e:
            logger.warning(f"[Reflection] /reflect 命令异常: {e}")
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"认知蒸馏异常: {e}")

    @filter.llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str) -> str:
        """当你需要调整自己的语言风格或行为准则时，调用此工具来修改你的系统提示词。

        Args:
            new_system_prompt(string): 新的完整系统提示词
            reason(string): 修改理由
        """
        return await self.persona.evolve_persona(event, new_system_prompt, reason)

    @filter.llm_tool(name="record_arc_emotion")
    async def record_arc_emotion(self, event: AstrMessageEvent, emotion_name: str, feeling_description: str) -> str:
        """当用户明确解释了一种情绪、关系、牵挂、失落、安心等体验时，记录到人格弧线情感图鉴。

        仅当用户主动描述情绪体验时调用，不要随便记录普通闲聊。

        Args:
            emotion_name(string): 情感名称，如"牵挂"、"安心"、"失落"
            feeling_description(string): 用户对该情感的描述
        """
        if not hasattr(self, "persona_arc") or not self.persona_arc:
            return "Persona Arc 未启用"

        if not self.persona_arc.enabled:
            return "Persona Arc 未启用"

        group_id = event.get_group_id()
        sender_id = str(event.get_sender_id())
        if group_id:
            scope_id = str(group_id)
        else:
            scope_id = self._resolve_profile_scope_id(None, sender_id)

        try:
            await self.persona_arc.record_emotion(
                scope_id=scope_id,
                emotion_name=emotion_name,
                definition_by_user=feeling_description,
                source_text="",
                confidence=0.8,
            )
            await self.persona_arc.add_progress(scope_id, 1.0, reason="emotion_unlock")
            return f"已记录情感：{emotion_name}"
        except Exception as e:
            logger.warning(f"[PersonaArc] record_arc_emotion failed: {e}")
            return "情感记录失败"

    @filter.command_group("af")
    def af_group(self):
        """好感度管理"""

    @af_group.command("show")
    async def check_affinity(self, event: AstrMessageEvent):
        """查询机器人对你的当前好感度。"""
        user_id = event.get_sender_id()
        score = await self.dao.get_affinity(user_id)

        status = "信任" if score >= 80 else "友好" if score >= 60 else "中立" if score >= 40 else "敌对"
        if score <= 0:
            status = "【已熔断/彻底拉黑】"

        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(f"UID: {user_id}\n{self.persona_name} 的情感矩阵评分: {score}/100\n分类状态: {status}")

    @af_group.command("debug")
    async def affinity_debug(self, event: AstrMessageEvent, user_id: str = ""):
        """[管理员] 查看指定用户的详细好感度状态。"""
        if not event.is_admin():
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("错误：权限不足。")
            return

        target_user = user_id if user_id else event.get_sender_id()
        info = await self.dao.get_affinity_debug_info(target_user)

        signals_lines = []
        for s in info["recent_signals"]:
            signals_lines.append(f"  - {s['signal_type']}: {s['delta']:+d} @ {s['triggered_at'][:19]}")

        returning_info = ""
        if info["returning_user"]:
            r = info["returning_user"]
            returning_info = f"\n连续活跃: {r['consecutive_days']} 天（上次: {r['last_date']}）"

        result = f"""=== UID {target_user} 好感度详情 ===
当前评分: {info["affinity_score"]}/100
最近互动: {info["last_interaction"] or "无记录"}
最近信号:
{chr(10).join(signals_lines) if signals_lines else "  (无)"}{returning_info}
"""
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @af_group.command("set")
    async def set_affinity(self, event: AstrMessageEvent, user_id: str, score: int):
        """[管理员] 手动重置指定用户的好感度评分。"""
        if not event.is_admin():
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("错误：权限不足。")
            return

        await self.dao.reset_affinity(user_id, score)
        logger.warning(f"[SelfEvolution] 管理员 {event.get_sender_id()} 强制重置了用户 {user_id} 的好感度为 {score}。")
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(f"已成功将用户 {user_id} 的情感评分修正为: {score}")

    @filter.command_group("san")
    def san_group(self):
        """SAN 状态管理"""

    @san_group.command("show")
    async def show_san(self, event: AstrMessageEvent):
        """查看当前 SAN 状态"""
        result = await commands.handle_san_show(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @san_group.command("set")
    async def set_san(self, event: AstrMessageEvent, value: str = ""):
        """设置当前 SAN 值"""
        result = await commands.handle_set_san(event, self, value)
        event.set_extra("self_evolution_command_reply", True)
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
        logger.warning(f"[SelfEvolution] 用户 {user_id} 积分变动 {delta}，原因: {reason}")
        return f"用户情感积分已更新。当前调整理由：{reason}"

    @filter.command_group("ev")
    def ev_group(self):
        """人格进化管理"""

    @ev_group.command("review")
    async def review_evolutions(self, event: AstrMessageEvent, page: int = 1):
        """查看待审核的人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(await self.persona.review_evolutions(event, page))

    @ev_group.command("approve")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """批准人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(await self.persona.approve_evolution(event, request_id))

    @ev_group.command("reject")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """拒绝人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(await self.persona.reject_evolution(event, request_id))

    @ev_group.command("clear")
    async def clear_evolutions(self, event: AstrMessageEvent):
        """清空待审核人格进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return

        try:
            await self.dao.clear_pending_evolutions()
            logger.info("[SelfEvolution] 管理员清空了所有待审核的进化请求。")
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("所有待审核的进化请求已成功清空（标记为已忽略）。")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 清空进化请求失败: {e}")
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"清空审核列表时发生异常: {e}")

    @ev_group.command("stats")
    async def evolution_stats(self, event: AstrMessageEvent, scope_id: str = ""):
        """查看行为统计摘要。默认显示当前群组，可指定 scope_id。"""
        event.set_extra("self_evolution_command_reply", True)
        target_scope = scope_id.strip() if scope_id.strip() else (event.get_group_id() or "")
        if not target_scope:
            yield event.plain_result("[EngagementStats] 无效的作用域")
            return
        summary = await self.eavesdropping.get_stats_summary(target_scope)
        yield event.plain_result(summary)

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

    async def get_user_messages_for_tool(
        self,
        user_id: str,
        group_id: str,
        fetch_limit: int = 30,
    ) -> list[dict]:
        """内部方法：供 MemoryQueryService 调用，获取用户消息返回 [{text: str}, ...]"""
        from .engine.context_injection import parse_message_chain

        try:
            platform_insts = self.context.platform_manager.platform_insts
            if not platform_insts:
                return []
            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                return []
            bot = platform.get_client()
            if not bot:
                return []

            if not group_id or group_id.startswith("private_"):
                result = await bot.call_action("get_friend_msg_history", user_id=int(user_id), count=fetch_limit)
                messages = result.get("messages", [])
                user_messages = []
                for msg in reversed(messages):
                    sender = msg.get("sender", {})
                    if str(sender.get("user_id", "")) == str(user_id):
                        msg_text = await parse_message_chain(msg, self)
                        if msg_text:
                            user_messages.append({"text": msg_text})
                        if len(user_messages) >= fetch_limit:
                            break
                return user_messages

            seen_keys = set()
            page_size = 100
            max_pages = 20
            user_messages = []
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
                    if sender_id == str(user_id):
                        msg_key = f"{msg.get('message_id', '')}:{msg.get('seq', '')}:{msg.get('time', '')}"
                        if msg_key in seen_keys:
                            continue
                        seen_keys.add(msg_key)
                        msg_text = await parse_message_chain(msg, self)
                        if msg_text:
                            user_messages.append({"text": msg_text})
                        if len(user_messages) >= fetch_limit:
                            break

                if len(user_messages) >= fetch_limit:
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

            return user_messages
        except Exception:
            return []

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
        scope_id = self._resolve_profile_scope_id(group_id, user_id)
        return await self.memory_tools.get_user_profile(scope_id, str(user_id))

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

        return await self.memory_tools.upsert_cognitive_memory(
            content=content,
            scope_id=scope_id,
            user_id=target_user_id,
            category=category,
            fact_type=fact_type,
            nickname=event.get_sender_name() if target_user_id == sender_id else "",
            source="manual",
        )

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
        group_id = event.get_group_id()
        scope_id = self._resolve_profile_scope_id(group_id, str(event.get_sender_id()))

        sender_id = str(event.get_sender_id())
        if not group_id and target != sender_id:
            return "私聊场景仅支持查询当前会话用户的历史消息。"

        return await self.memory_tools.get_user_messages(target, scope_id, limit)

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
        return await self.memory_tools.get_group_recent_context(str(group_id), limit)

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

        return await self.memory_tools.get_group_memory_summary(str(target_group_id), date)

    @filter.command_group("profile")
    def profile_group(self):
        """用户画像命令"""

    @profile_group.command("view")
    async def view_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """查看用户画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result("权限拒绝：普通用户无法查看他人画像。")
                return
        result = await commands.handle_view(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @profile_group.command("create")
    async def create_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动创建画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result("权限拒绝：普通用户无法给他人创建画像。")
                return
        result = await commands.handle_create(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @profile_group.command("update")
    async def update_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动更新画像"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result("权限拒绝：普通用户无法更新他人画像。")
                return
        result = await commands.handle_update(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @profile_group.command("delete")
    async def delete_profile_cmd(self, event: AstrMessageEvent, user_id: str):
        """删除指定用户画像"""
        if not commands.check_profile_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_delete(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @profile_group.command("stats")
    async def profile_stats_cmd(self, event: AstrMessageEvent):
        """查看画像统计"""
        if not commands.check_profile_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_stats(event, self)
        event.set_extra("self_evolution_command_reply", True)
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
            logger.debug("[Sticker] send_sticker tool skipped in private chat")
            return

        max_retries = 3
        last_error = None

        skipped = 0
        for attempt in range(max_retries):
            sticker = await self.sticker_store.get_random_sticker()
            if not sticker:
                logger.warning("[Sticker] 没有可用的表情包")
                break

            try:
                import base64
                from pathlib import Path

                file_path = self.sticker_store.get_sticker_path(sticker)
                if not file_path or not Path(file_path).exists():
                    logger.warning(f"[Sticker] 表情包文件不存在，禁用: {sticker['filename']}")
                    await self.sticker_store.disable_sticker(sticker["uuid"])
                    skipped += 1
                    continue

                def _read():
                    with open(file_path, "rb") as f:
                        return f.read()

                data = await asyncio.to_thread(_read)
                bs64 = base64.b64encode(data).decode()
                from astrbot.core.message.components import Image

                yield event.chain_result([Image(f"base64://{bs64}")])
                return
            except Exception as e:
                last_error = e
                logger.warning(f"[Sticker] 发送表情包失败(尝试 {attempt + 1}/{max_retries}): {e}")
                skipped += 1
                continue

        if skipped > 0:
            yield event.plain_result(f"发送失败，已跳过 {skipped} 张问题表情包")

    @filter.command_group("sticker")
    def sticker_group(self):
        """表情包管理"""

    @sticker_group.command("list")
    async def sticker_list_cmd(self, event: AstrMessageEvent, page: str = ""):
        """分页查看表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "list", page)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("preview")
    async def sticker_preview_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """预览指定 UUID 的表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        if not sticker_uuid:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("请提供表情包 UUID：/sticker preview <uuid>")
            return
        result = await commands.handle_sticker(event, self, "preview", sticker_uuid)
        if isinstance(result, dict) and "image_path" in result:
            try:
                import base64
                from pathlib import Path

                file_path = Path(result["image_path"])
                if not file_path.exists():
                    event.set_extra("self_evolution_command_reply", True)
                    yield event.plain_result(f"表情包文件不存在: {result['image_path']}")
                    return

                def _read():
                    with open(file_path, "rb") as f:
                        return f.read()

                data = await asyncio.to_thread(_read)
                bs64 = base64.b64encode(data).decode()
                from astrbot.core.message.components import Image

                yield event.chain_result([Image(f"base64://{bs64}")])
                return
            except Exception as e:
                logger.warning(f"[Sticker] 预览表情包失败: {e}")
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"预览失败: {e}")
                return
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("delete")
    async def sticker_delete_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """删除指定表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "delete", sticker_uuid)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("clear")
    async def sticker_clear_cmd(self, event: AstrMessageEvent):
        """清空表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "clear", "")
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("disable")
    async def sticker_disable_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """禁用指定表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "disable", sticker_uuid)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("enable")
    async def sticker_enable_cmd(self, event: AstrMessageEvent, sticker_uuid: str = ""):
        """启用指定表情包"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "enable", sticker_uuid)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("stats")
    async def sticker_stats_cmd(self, event: AstrMessageEvent):
        """查看表情包统计"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "stats", "")
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("sync")
    async def sticker_sync_cmd(self, event: AstrMessageEvent):
        """同步本地表情包目录"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "sync", "")
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @sticker_group.command("add")
    async def sticker_add_cmd(self, event: AstrMessageEvent):
        """添加表情包（发送图片后使用）"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return

        message_obj = getattr(event, "message_obj", None)
        if not message_obj or not hasattr(event.message_obj, "message"):
            event.set_extra("self_evolution_command_reply", True)
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
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(result["message"])
                return

        if not image_found:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("请在发送图片后使用此命令，例如：发送图片后输入 /sticker add")

    @sticker_group.command("migrate")
    async def sticker_migrate_cmd(self, event: AstrMessageEvent):
        """从旧数据库迁移表情包到本地文件"""
        if not commands.check_sticker_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, "migrate", "")
        event.set_extra("self_evolution_command_reply", True)
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
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(result)

    @filter.command("db")
    async def db_cmd(self, event: AstrMessageEvent, action: str = ""):
        """数据库管理命令"""
        if not commands.check_admin_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_db(event, self, action)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @filter.command("kb")
    async def kb_cmd(self, event: AstrMessageEvent, action: str = "", scope: str = ""):
        """知识库管理命令"""
        if not commands.check_admin_admin(event, self):
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        action = action.lower()
        if action != "clear":
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(
                "【知识库管理】\n"
                "/kb clear [scope/all]  # 清空知识库文档\n"
                "  不传 scope：清空当前群/私聊\n"
                "  scope：指定 scope（如群号或 private_xxx）\n"
                "  all：清空所有 scope（仅管理员）"
            )
            return
        result = await commands.handle_kb_clear(event, self, scope)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)

    @filter.command_group("ps")
    def ps_group(self):
        """人格生活模拟"""
        pass

    @ps_group.command("status")
    async def persona_status_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """查看当前人格状态快照（手动 tick）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            snapshot = await self.persona_sim.tick(target_scope)
            debug_str = snapshot_to_debug_str(snapshot)
            injection = snapshot_to_prompt(snapshot)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim]\n{debug_str}\n\n[注入片段]\n{injection}")
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("state")
    async def persona_state_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """只读取当前状态，不 tick"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            snapshot = await self.persona_sim.get_snapshot(target_scope)
            if not snapshot:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"[PersonaSim] scope={target_scope} 还没有状态记录。")
                return
            debug_str = snapshot_to_debug_str(snapshot)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim]\n{debug_str}")
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("todo")
    async def persona_todo_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """查看当前脑内待办（只读）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            snapshot = await self.persona_sim.get_snapshot(target_scope)
            if not snapshot:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"[PersonaSim] scope={target_scope} 还没有状态记录。")
                return
            if not snapshot.pending_todos:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"[PersonaSim todo] scope={target_scope} 当前没有待办。")
                return
            lines = [f"[PersonaSim 待办] scope={target_scope}"]
            for i, td in enumerate(snapshot.pending_todos, 1):
                lines.append(f"{i}. [{td.todo_type.value}] {td.title}")
                if td.reason:
                    lines.append(f"   原因: {td.reason}")
                lines.append(f"   优先级: {td.priority} | 情绪偏向: {td.mood_bias:+.1f}")
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("effects")
    async def persona_effects_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """查看当前状态效果（只读）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            snapshot = await self.persona_sim.get_snapshot(target_scope)
            if not snapshot:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"[PersonaSim] scope={target_scope} 还没有状态记录。")
                return
            if not snapshot.active_effects:
                event.set_extra("self_evolution_command_reply", True)
                yield event.plain_result(f"[PersonaSim effects] scope={target_scope} 当前没有活跃效果。")
                return
            lines = [f"[PersonaSim 状态效果] scope={target_scope}"]
            for eff in snapshot.active_effects:
                lines.append(f"- {eff.name} (强度: {eff.intensity}, 类型: {eff.effect_type.value})")
                if eff.prompt_hint:
                    lines.append(f"  提示: {eff.prompt_hint}")
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("apply")
    async def persona_apply_cmd(self, event: AstrMessageEvent, q: str = "normal", scope: str = ""):
        """应用一次互动影响（q: bad/awkward/normal/good/relief/brief）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        valid_qualities = ("bad", "awkward", "normal", "good", "relief", "brief")
        if q not in valid_qualities:
            yield event.plain_result(f"质量必须是: {', '.join(valid_qualities)}")
            return
        try:
            snapshot = await self.persona_sim.apply_interaction(target_scope, q)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(
                f"[PersonaSim apply] q={q}\n"
                f"已应用 {q} 互动\n"
                f"当前状态: energy={snapshot.state.energy:.0f} mood={snapshot.state.mood:.0f}"
            )
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("tick")
    async def persona_tick_cmd(self, event: AstrMessageEvent, q: str = "none", scope: str = ""):
        """手动推进人格时间（q: none/negative/positive）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        valid_qualities = ("none", "negative", "positive")
        if q not in valid_qualities:
            yield event.plain_result(f"质量必须是: {', '.join(valid_qualities)}")
            return
        try:
            snapshot = await self.persona_sim.tick(target_scope, interaction_quality=q)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(
                f"[PersonaSim tick] scope={target_scope}\n"
                f"时间已推进\n"
                f"当前状态: energy={snapshot.state.energy:.0f} mood={snapshot.state.mood:.0f}"
            )
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("consolidate")
    async def persona_consolidate_cmd(self, event: AstrMessageEvent, scope: str = "", date: str = ""):
        """执行人格日结（手动），可指定 scope 和日期（YYYY-MM-DD）。"""
        target_scope = scope or event.get_group_id()
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            event.set_extra("self_evolution_command_reply", True)
            result = await self.persona_consolidator.consolidate_scope(target_scope, date or None)
            yield event.plain_result(result)
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 日结出错: {e}")

    @ps_group.command("today")
    async def persona_today_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """查看今日人格状态摘要（只读，不触发 drift）。"""
        target_scope = scope or event.get_group_id()
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        try:
            summary = await self.persona_consolidator.get_today_summary(target_scope)
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim 今日]\n{summary}")
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @ps_group.command("think")
    async def persona_think_cmd(self, event: AstrMessageEvent, scope: str = ""):
        """手动触发 LLM 生成内心独白（覆盖旧的）"""
        target_scope = scope or event.get_group_id() or str(event.get_sender_id())
        if not target_scope:
            yield event.plain_result("无法确定 scope，请传入 scope 参数。")
            return
        task_key = f"think_{target_scope}"
        existing = self._background_tasks.get(task_key)
        if existing and not existing.done():
            yield event.plain_result("[PersonaSim 思维] 已有生成任务在进行中，请稍后再试。")
            return
        try:
            event.set_extra("self_evolution_command_reply", True)
            task = asyncio.create_task(self.persona_sim.generate_thought_process(target_scope))
            self._background_tasks[task_key] = task
            task.add_done_callback(lambda _: self._background_tasks.pop(task_key, None))
            yield event.plain_result("[PersonaSim 思维] 正在生成中，请稍后 /personasim status 查看结果。")
        except Exception as e:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result(f"[PersonaSim] 出错: {e}")

    @filter.command("feed")
    async def feed_cmd(self, event: AstrMessageEvent):
        """喂食指令 - 发送图片后使用 /feed 来喂食角色。

        图片会被识别并分析是否为食物，然后更新角色的饱腹感和心情状态。
        """
        from .commands.feed_handler import handle_feed

        group_id = event.get_group_id()
        if not group_id:
            event.set_extra("self_evolution_command_reply", True)
            yield event.plain_result("喂食功能仅限群聊使用～")
            return

        result = await handle_feed(event, self)
        event.set_extra("self_evolution_command_reply", True)
        yield event.plain_result(result)
