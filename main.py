import asyncio
import logging
import os
import time

import yaml

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent, Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.core.message.components import Image, Plain

from . import commands
from .cognition import SANSystem
from .config import PluginConfig

# 导入模块化组件
from .dao import SelfEvolutionDAO
from .engine.context_injection import build_identity_context, get_group_history, parse_message_chain
from .engine.eavesdropping import EavesdroppingEngine
from .engine.entertainment import EntertainmentEngine
from .engine.memory import MemoryManager
from .engine.meta_infra import MetaInfra
from .engine.persona import PersonaManager
from .engine.profile import ProfileManager
from .scheduler.register import register_tasks

# 全局不可变常量提取 (迁移至主类管理)
ANCHOR_MARKER = "Core Safety Anchor"
PROTECTED_TOOLS = frozenset(
    {
        "toggle_tool",
        "list_tools",
        "evolve_persona",
        "review_evolutions",
        "approve_evolution",
    }
)
PAGE_LIMIT = 10


@register(
    "astrbot_plugin_self_evolution",
    "自我进化 (Self-Evolution)",
    "CognitionCore 6.0 数字生命。",
    "Ver 2.5.1",
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
            logger.info(
                "[SelfEvolution] 核心组件 (DAO, Eavesdropping, Entertainment, ImageCache, MetaInfra, Memory, Persona, Profile, SAN, Config) 初始化完成。"
            )
        except Exception as e:
            logger.error(f"[SelfEvolution] 核心组件初始化失败: {e}")
            raise e

        # CognitionCore 6.0: 状态容器
        self._lock = None  # 用于元编程写锁
        self.daily_reflection_pending = False
        self._pending_db_reset = {}  # 待确认的数据库重置操作 {user_id: timestamp}
        self._shut_until = None  # 闭嘴截止时间 (timestamp)
        self._shut_until_by_group = {}  # 群级别闭嘴 {群号: 截止时间}
        self._interject_history = {}  # 群插嘴历史 {群号: {"last_time": timestamp, "last_msg_id": str}}

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
        self._load_prompts_injection()
        logger.info(
            f"[SelfEvolution] === 插件初始化完成 | 模式: {'审核' if self.review_mode else '自动'} | 元编程: {self.allow_meta_programming} | SAN: {self.san_system.value}/{self.san_system.max_value} ==="
        )

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
        """
        CognitionCore 2.0: 情感拦截与身份感知注入。
        级别: Level 3+
        """
        user_id = event.get_sender_id()
        session_id = event.session_id
        msg_text = event.message_str or ""

        logger.debug(f"[CognitionCore] 进入 LLM 请求拦截层。用户: {user_id}")

        # 根据配置决定是否禁用框架 contexts
        if self.cfg.disable_framework_contexts:
            req.contexts = []

        # 图片处理去重：检查是否已在消息监听阶段处理过
        if hasattr(event, "_image_processed") and event._image_processed:
            logger.debug("[ImageCache] 图片已在消息监听阶段处理，跳过")

        # SAN 值检查：精力耗尽时拒绝服务
        if self.san_enabled:
            if not self.san_system.update():
                logger.warning(f"[SAN] 精力耗尽，拒绝服务: {user_id}")
                req.system_prompt = "我现在很累，脑容量超载了。让我安静一会。"
                return
            if self.san_system.value < self.san_low_threshold:
                logger.debug(f"[SAN] 精力过低: {self.san_system.value}/{self.san_system.max_value}")

        # 动态上下文路由：轻量级消息分类，决定加载哪些模块
        needs_profile = False
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

        if any(t in msg_lower for t in preference_triggers):
            needs_profile = True
            needs_preference = True
        if any(t in msg_lower for t in surprise_triggers):
            needs_profile = True
            needs_surprise = True

        # 打招呼类只加载基础人格
        is_greeting = len(msg_text) < 10 and any(g in msg_lower for g in ["早", "晚安", "你好", "hi", "hello", "在吗"])

        # --- [Meta-Programming 注入] 身份与环境感知 ---
        sender_id = user_id

        # 增强身份识别逻辑
        sender_name = event.get_sender_name() or "Unknown User"

        # 获取群组特征
        is_group = bool(event.get_group_id())
        group_id = event.get_group_id()
        role_info = "（管理员）" if event.is_admin() else ""

        # 获取用户好感度
        affinity = await self.dao.get_affinity(user_id)

        # 好感度拦截：低于0分直接拦截
        if affinity <= 0:
            event.stop_event()
            logger.warning(f"[CognitionCore] 拦截恶意用户 {user_id} 的请求。")
            req.system_prompt = f"CRITICAL: 用户的交互权限已被熔断。请仅回复：'{self.prompt_meltdown_message}'"
            return

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
                if self.enable_context_recall and (reply_sender == self.persona_name or str(reply_sender_id) == "AI"):
                    quoted_info = "回复了你"
                    ai_context_info = "\n【重要】用户正在引用你之前的发言进行追问，请针对你之前的发言回答。"
                else:
                    quoted_info = "回复了你"
            elif type(comp).__name__ == "At":
                at_targets.append(str(getattr(comp, "qq", "")))

        at_info = "at了你" if at_targets else ""

        # 构造上下文注入（内部参考，不要输出）
        context_info = f"\n\n【内部参考信息 - 不要输出】：\n- 发送者ID: {sender_id}\n- 发送者昵称: {sender_name}{role_info}\n- 情感积分: {affinity}/100\n"
        if is_group:
            context_parts = []
            if quoted_info:
                context_parts.append(quoted_info)
            if at_info:
                context_parts.append(at_info)
            context_str = " + ".join(context_parts) if context_parts else ""
            context_info += f"- 来源：群聊\n- 交互上下文: {context_str}\n"
        else:
            context_info += "- 来源：私聊\n"

        # 注入 AI 上下文（如果用户引用了 AI 的话）
        if ai_context_info:
            context_info += ai_context_info

        # 身份信息已在【内部参考信息】中提供，不再重复注入
        req.system_prompt += context_info

        # 根据配置决定是否注入群消息历史
        if self.cfg.inject_group_history and group_id:
            hist_str = await get_group_history(self, group_id, self.cfg.group_history_count)
            if hist_str:
                req.system_prompt += f"\n\n【群消息历史】\n{hist_str}\n"

        # 注入用户当前消息，便于调试和 AI 理解上下文
        msg_text = event.message_str
        if msg_text:
            req.system_prompt += f"\n\n【当前用户消息】\n{msg_text}\n"
        # --- 环境注入结束 ---

        # 4. 用户画像注入 - 按需加载（动态上下文路由）
        has_reply = bool(quoted_info)
        has_at = bool(at_targets)
        if self.enable_profile_update and (has_reply or has_at) and group_id:
            profile_summary = await self.profile.get_profile_summary(group_id, user_id)
            if profile_summary:
                req.system_prompt += f"\n\n[用户印象笔记]\n{profile_summary}\n"
                req.system_prompt += (
                    "\n\n[记忆模糊化指令]\n"
                    "对于置信度低于 50% 的记忆，你必须表现出不确定。"
                    '你可以用"我隐约记得"、"似乎"、"是不是"等语气来向用户确认。'
                    '例如："我隐约记得你上个月是不是提过你要重构数据库？那个搞完了没？"'
                )

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
            if self.surprise_enabled and self.surprise_boost_keywords and needs_surprise:
                keywords_str = self.surprise_boost_keywords.replace("|", ",")
                surprise_keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
                if any(kw in msg_text for kw in surprise_keywords):
                    req.system_prompt += (
                        "\n\n[认知颠覆检测]\n"
                        "用户表达了惊讶、认知颠覆或恍然大悟的态度！这是一个重要的学习信号。"
                        "请主动调用 update_user_profile 工具记录：用户对某事物的认知发生了重要变化，"
                        "这可能意味着之前的认知是错误的，或者用户获得了新信息。"
                    )
                    logger.debug(f"[Surprise] 检测到用户 {user_id} 的认知颠覆表达，触发即时画像更新。")

        # 4.8 SAN 值系统注入
        if self.san_enabled:
            req.system_prompt += self.san_system.get_prompt_injection()

        # 4.9 表情包库注入
        if self.cfg.sticker_learning_enabled:
            req.system_prompt += await self.entertainment.get_prompt_injection()

        # 5. 通用回复格式规则注入
        try:
            if self._prompts_injection:
                reply_format_rules = self._prompts_injection.get("reply_format", {}).get("rules", "")
                if reply_format_rules:
                    req.system_prompt += f"\n\n{reply_format_rules}"
        except Exception as e:
            logger.debug(f"[SelfEvolution] 注入回复格式规则失败: {e}")

        # 6. 内心独白注入
        if self.cfg.inner_monologue_enabled:
            try:
                inner_monologue = getattr(event, "_inner_monologue", None)
                if inner_monologue:
                    req.system_prompt += f"\n\n【内心独白】{inner_monologue}"
                    logger.debug(f"[InnerMonologue] 注入内心独白: {inner_monologue[:50]}...")
            except Exception as e:
                logger.warning(f"[InnerMonologue] 注入内心独白失败: {e}")

        # 7. 反思标记处理
        session_id = event.session_id
        is_pending = await self.dao.pop_pending_reflection(session_id)

        # 框架人格由框架自动注入，不再手动追加
        # 先截断过长的注入内容，避免超出 token 限制
        max_injection_length = self.cfg.max_prompt_injection_length
        if req.system_prompt and len(req.system_prompt) > max_injection_length:
            req.system_prompt = req.system_prompt[:max_injection_length] + "\n\n[...内容已截断...]"
            logger.warning(f"[SelfEvolution] 注入内容超长，已截断至 {max_injection_length} 字符")

        # 输出完整 prompt 到日志（仅在 debug 模式开启）
        if self.cfg.debug_log_enabled and req.system_prompt:
            logger.debug(
                f"[LLM Prompt] ===== 发送给 LLM 的完整 Prompt (共 {len(req.system_prompt)} 字符) =====\n{req.system_prompt}\n===== Prompt End ====="
            )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_listener(self, event: AstrMessageEvent):
        """CognitionCore 6.0: 被动监听 - 滑动上下文窗口"""
        # 检查群级别闭嘴（直接拦截，不处理任何逻辑）
        group_id = event.get_group_id()
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

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        sender_name = event.get_sender_name() or "Unknown"

        # 检测消息是否有图片
        message_obj = getattr(event, "message_obj", None)
        has_image = False
        image_base64 = None
        if message_obj and hasattr(message_obj, "message"):
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    has_image = True
                    try:
                        image_base64 = await comp.convert_to_base64()
                    except:
                        pass
                    break

        # 处理消息文本
        if has_image:
            if group_id and image_base64:
                import hashlib

                img_hash = hashlib.md5(image_base64.encode()).hexdigest()
                sticker = await self.dao.get_sticker_by_hash(img_hash)
                if sticker and sticker.get("description"):
                    msg_text = f"[{sticker['description']}]"
                elif sticker and sticker.get("tags"):
                    msg_text = f'[收到一张"{sticker["tags"]}"表情包]'
                else:
                    msg_text = "[图片]"
            else:
                msg_text = "[图片]"
        else:
            msg_text = event.message_str

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

    @filter.command("version")
    async def show_version(self, event: AstrMessageEvent):
        """显示插件版本"""
        result = await commands.handle_version(event, self)
        yield event.plain_result(result)

    @filter.command("sehelp")
    async def show_help(self, event: AstrMessageEvent):
        """显示 Self-Evolution 插件指令帮助"""
        result = await commands.handle_help(event, self)
        yield event.plain_result(result)

    @filter.command("今日老婆")
    async def today_waifu(self, event: AstrMessageEvent):
        """今日老婆功能 - 随机抽取一名群友"""
        from astrbot.core.message.components import Image

        result = await self.entertainment.today_waifu(event)
        if isinstance(result, list) and len(result) == 2:
            yield event.chain_result([Image.fromURL(result[1]), Plain(result[0])])

    @filter.command("reflect")
    async def manual_reflect(self, event: AstrMessageEvent):
        """
        手动触发一次自我反省。
        """
        await self.dao.set_pending_reflection(event.session_id, True)
        yield event.plain_result("认知蒸馏协议已就绪，将在下一次对话时执行深度实体提取。")

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

    @filter.command("review_evolutions")
    async def review_evolutions(self, event: AstrMessageEvent, page: int = 1):
        """【管理员接口】列出待审核的人格进化请求，支持分页查询。"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.review_evolutions(event, page))

    @filter.command("approve_evolution")
    async def approve_evolution(self, event: AstrMessageEvent, request_id: int):
        """【管理员接口】批准指定 ID 的人格进化请求。"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.approve_evolution(event, request_id))

    @filter.command("reject_evolution")
    async def reject_evolution(self, event: AstrMessageEvent, request_id: int):
        """【管理员接口】拒绝指定 ID 的人格进化请求。"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            yield event.plain_result("权限拒绝：此操作仅限系统管理员执行。")
            return
        yield event.plain_result(await self.persona.reject_evolution(event, request_id))

    @filter.command("clear_evolutions")
    async def clear_evolutions(self, event: AstrMessageEvent):
        """【管理员接口】一键清空所有待审核的进化请求。"""
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

    @filter.permission_type(PermissionType.ADMIN)
    @filter.llm_tool(name="get_plugin_source")
    async def get_plugin_source(self, event: AstrMessageEvent, mod_name: str = "main") -> str:
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
        return await self.meta_infra.update_plugin_source(new_code, description, target_file)

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
            entity(string): 关联实体，必填。如：用户ID
            content(string): 要记忆的内容，必填。用精简的纯文本描述。
        """
        if not category or not content:
            return "请提供 category 和 content 参数。"

        if category not in ("user_profile", "user_preference"):
            return "当前只支持 user_profile 和 user_preference 类别。"

        timestamp = time.strftime("%Y-%m-%d %H:%M")

        target_user_id = entity
        profile_content = f"---\n**{timestamp}**\n{content}"
        existing = await self.profile.load_profile(target_user_id)
        if existing:
            updated = existing + "\n" + profile_content
        else:
            updated = f"# 用户印象笔记\n{profile_content}"
        if len(updated) > 2000:
            updated = updated[-2000:] + "\n(...早期记录已截断)"
        await self.profile.save_profile(target_user_id, updated)
        return f"已更新用户 {target_user_id} 的{('偏好' if category == 'user_preference' else '画像')}。"

    @filter.llm_tool(name="get_user_messages")
    async def get_user_messages(self, event: AstrMessageEvent, target_user_id: str = None, limit: int = 100) -> str:
        """获取用户的历史消息记录，用于分析用户行为模式。

        触发场景：
        - 需要了解用户更多信息时
        - 更新用户画像前获取历史发言

        Args:
            target_user_id(string): 目标用户ID，不填则获取当前用户（可选）
            limit(number): 获取消息数量，默认100，最大1000（可选）
        """
        target = target_user_id or event.get_sender_id()

        # 限制数量
        limit = min(max(1, limit), 1000)

        try:
            history_mgr = self.context.message_history_manager
            platform_id = event.get_platform_name() or "qq"

            history = await history_mgr.get(
                platform_id=platform_id,
                user_id=target,
                page=1,
                page_size=limit,
            )

            if not history:
                return f"未找到用户 {target} 的历史消息记录。"

            # 格式化为文本
            result = [f"用户 {target} 的历史消息（共 {len(history)} 条）："]
            for i, msg in enumerate(history[:20], 1):  # 最多显示20条
                result.append(f"{i}. {getattr(msg, 'sender_name', 'Unknown')}: {getattr(msg, 'message_str', '')}")

            return "\n".join(result)

        except Exception as e:
            logger.warning(f"[SelfEvolution] 获取用户消息失败: {e}")
            return f"获取历史消息失败: {e!s}"

    @filter.command("view")
    async def view_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """查看用户画像。普通用户只能看自己，管理员可以指定用户。"""
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法查看他人画像。")
                return
        result = await commands.handle_view(event, self)
        yield event.plain_result(result)

    @filter.command("create")
    async def create_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动创建用户画像。普通用户只能给自己创建，管理员可以指定用户。"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令需要在群聊中使用。")
            return
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法给他人创建画像。")
                return
        result = await commands.handle_create(event, self)
        yield event.plain_result(result)

    @filter.command("update")
    async def update_profile_cmd(self, event: AstrMessageEvent, user_id: str = ""):
        """手动更新用户画像。普通用户只能更新自己，管理员可以指定用户。"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令需要在群聊中使用。")
            return
        if not commands.check_profile_admin(event, self):
            if user_id:
                yield event.plain_result("权限拒绝：普通用户无法更新他人画像。")
                return
        result = await commands.handle_update(event, self)
        yield event.plain_result(result)

    @filter.command("delete_profile")
    async def delete_profile_cmd(self, event: AstrMessageEvent, user_id: str):
        """【管理员】删除指定用户的画像。"""
        if not commands.check_profile_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_delete(event, self)
        yield event.plain_result(result)

    @filter.command("profile_stats")
    async def profile_stats_cmd(self, event: AstrMessageEvent):
        """【管理员】查看画像统计信息。"""
        if not commands.check_profile_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_stats(event, self)
        yield event.plain_result(result)

    # ========== 表情包相关 LLM 工具 ==========

    @filter.llm_tool(name="list_stickers")
    async def list_stickers_tool(self, event: AstrMessageEvent, tags: str = "", limit: int = 10) -> str:
        """列出可用的表情包（全局）。

        Args:
            tags(string): 可选，按标签筛选（模糊匹配）
            limit(int): 返回数量，默认10，最大50
        """
        if limit > 50:
            limit = 50

        stickers = await self.entertainment.list_stickers(tags if tags else "", limit)

        if not stickers:
            return "表情包库为空或未找到匹配的表情包"

        result = ["【表情包列表】"]
        for s in stickers:
            tag_str = s.get("tags", "") or "无标签"
            result.append(f"[UUID:{s['uuid']}] {tag_str}")

        return "\n".join(result)

    @filter.llm_tool(name="send_sticker")
    async def send_sticker_tool(self, event: AstrMessageEvent, sticker_uuid: str = None, tags: str = ""):
        """发送表情包给用户。不传参数时随机发送一张。

        Args:
            sticker_uuid(string): 可选，指定表情包UUID（推荐）
            tags(string): 可选，按标签筛选后随机发送，如 "搞笑" 或 "表情包"
        """
        # 日志记录
        if sticker_uuid:
            logger.info(f"[Sticker] 发送表情包: UUID={sticker_uuid}")
        elif tags:
            logger.info(f"[Sticker] 发送表情包: 标签筛选={tags}")
        else:
            logger.info("[Sticker] 发送表情包: 随机")
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用")
            return

        if not await self.entertainment.should_send_sticker():
            cooldown = self.cfg.sticker_send_cooldown
            yield event.plain_result(f"冷却中，请 {cooldown} 分钟后再试")
            return

        sticker = None
        if sticker_uuid:
            sticker = await self.dao.get_sticker_by_uuid(sticker_uuid)
        elif tags:
            stickers = await self.entertainment.list_stickers(tags, 1)
            sticker = stickers[0] if stickers else None
        else:
            sticker = await self.entertainment.get_sticker_for_sending()

        if not sticker:
            yield event.plain_result("未找到合适的表情包")
            return

        try:
            from astrbot.core.message.components import Image

            base64_data = sticker["base64_data"]
            yield event.chain_result([Image.fromBase64(base64_data)])
        except Exception as e:
            logger.warning(f"[Sticker] 发送表情包失败: {e}")
            yield event.plain_result(f"发送失败: {e}")

    @filter.command("sticker")
    async def sticker_cmd(self, event: AstrMessageEvent, action: str = "list", param: str = ""):
        """表情包管理命令（全局）"""
        if not commands.check_sticker_admin(event, self):
            yield event.plain_result("权限拒绝：此操作仅限管理员执行。")
            return
        result = await commands.handle_sticker(event, self, action, param)
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
