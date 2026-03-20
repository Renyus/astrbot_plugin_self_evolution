import asyncio
import json
import math
import re
import time
from collections import defaultdict, deque

from astrbot.api import logger
from astrbot.api.all import AstrMessageEvent

from .context_injection import build_identity_context, get_group_history, parse_message_chain


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self.global_window = defaultdict(lambda: deque(maxlen=5))
        self.window_size = 5
        self.leaky_bucket = defaultdict(dict)  # {"value": float, "last_time": float}
        self.boredom_cache = defaultdict(lambda: {"count": 0, "last_message_time": 0.0})

        # 线程安全锁
        self._bucket_lock = asyncio.Lock()
        self._boredom_lock = asyncio.Lock()
        self._active_users_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._interject_lock = asyncio.Lock()

        # 漏斗机制 - 用户活跃判定
        self.active_users = defaultdict(
            dict
        )  # {group_id: {user_id: {"last_active": timestamp, "window_end": timestamp}}}
        self.active_window_seconds = 30  # 30秒活跃窗口

        # 唤醒词列表
        self.wake_names = ["黑塔", "belta", "Bot", "机器人", "小塔"]

        # 简化的会话状态管理（原session_manager核心功能）
        self.session_buffers = {}  # {buffer_key: {"messages": [], "eavesdrop_count": 0, "threshold": int}}
        self.processing_sessions = set()
        self._session_lock = asyncio.Lock()

        # 插嘴功能状态
        self._interject_history = {}  # 群插嘴历史 {群号: {"last_time": timestamp, "last_msg_id": str}}

        # 强AI意图句式
        self.ai_intent_patterns = [
            r"^帮我",
            r"^帮我画",
            r"^翻译",
            r"^总结",
            r"^写一段",
            r"^解释",
            r"^计算",
            r"^查询",
            r"^生成",
        ]
        # 预编译AI意图正则
        self._ai_intent_patterns_compiled = [re.compile(p) for p in self.ai_intent_patterns]

    @staticmethod
    def _latest_message_mentions_bot(latest_msg: dict, bot_id: str) -> bool:
        """Check whether the latest message explicitly mentions the bot or @all."""
        for seg in latest_msg.get("message", []):
            if isinstance(seg, dict) and seg.get("type") == "at":
                at_qq = str(seg.get("data", {}).get("qq", ""))
                if at_qq == bot_id or at_qq == "all":
                    return True
        return False

    def _calculate_entropy(self, text: str) -> float:
        """基于香农熵计算文本信息量"""
        if not text or len(text) < 2:
            return 1.0

        # 统计字符频率
        freq = {}
        for char in text:
            freq[char] = freq.get(char, 0) + 1

        # 计算香农熵
        entropy = 0
        length = len(text)
        for count in freq.values():
            p = count / length
            entropy -= p * math.log2(p)

        # 归一化（最大熵为 log2(字符集大小)）
        max_entropy = math.log2(len(freq)) if len(freq) > 1 else 1
        if max_entropy > 0:
            normalized = entropy / max_entropy
            return min(normalized, 1.0)

        return 0.0

    # ==================== 漏斗机制：用户活跃判定 ====================

    def _get_or_init_bucket_data(self, session_id: str, current_time: float) -> dict:
        bucket_data = self.leaky_bucket.get(session_id)
        if not isinstance(bucket_data, dict):
            bucket_data = {
                "value": 2.0,
                "last_time": current_time,
                "is_cooling_down": False,
                "cooling_end_time": 0,
                "triggered": False,
                "consecutive_replies": 0,
            }
        return bucket_data

    async def _check_funnel_trigger(self, event: AstrMessageEvent) -> bool:
        """漏斗触发检测：@/命令/引用/唤醒词/意图正则"""
        msg = event.message_obj
        msg_text = event.message_str or ""

        # @ 提及或命令前缀
        if event.is_at_or_wake_command:
            return True

        # 命令前缀
        config = self.plugin.context.get_config()
        prefixes = config.get("wake_prefix", ["/"])
        prov_prefix = config.get("provider_settings", {}).get("wake_prefix", "/")
        prefixes = list(prefixes) + [prov_prefix]
        if any(msg_text.startswith(p) for p in prefixes if p):
            return True

        # 引用回复检测
        if hasattr(msg, "reply") and msg.reply:
            reply_msg = msg.reply
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if platform_insts:
                platform = platform_insts[0]
                bot = platform.bot
                try:
                    login_info = await bot.call_action("get_login_info")
                    bot_id = str(login_info.get("user_id", ""))
                except Exception:
                    bot_id = str(getattr(platform, "client_self_id", ""))
                if str(reply_msg.sender) == bot_id:
                    return True

        # 唤醒词检测
        msg_lower = msg_text.lower()
        for name in self.wake_names:
            if name.lower() in msg_lower:
                return True

        # AI意图句式
        for pattern in self._ai_intent_patterns_compiled:
            if pattern.search(msg_lower):
                return True

        return False

    def _is_user_in_active_window(self, group_id: str, user_id: str) -> bool:
        """检查用户是否在活跃时间窗内"""
        group_id = str(group_id)
        user_id = str(user_id)

        if group_id not in self.active_users:
            return False
        if user_id not in self.active_users.get(group_id, {}):
            return False

        window_end = self.active_users[group_id].get(user_id, {}).get("window_end", 0)
        return time.time() < window_end

    async def _mark_user_active_async(self, group_id: str, user_id: str):
        """标记用户为活跃状态，开启30秒窗口（异步版本，带锁）"""
        group_id = str(group_id)
        user_id = str(user_id)
        now = time.time()
        async with self._active_users_lock:
            self.active_users[group_id][user_id] = {
                "last_active": now,
                "window_end": now + self.active_window_seconds,
            }

    def _mark_user_active(self, group_id: str, user_id: str):
        """标记用户为活跃状态，开启30秒窗口"""
        # 确保类型一致
        group_id = str(group_id)
        user_id = str(user_id)

        now = time.time()
        self.active_users[group_id][user_id] = {
            "last_active": now,
            "window_end": now + self.active_window_seconds,
        }

    def is_user_active(self, group_id: str, user_id: str) -> bool:
        """检查用户是否处于活跃状态"""
        return self._is_user_in_active_window(str(group_id), str(user_id))

    def cleanup_expired_active_users(self):
        """清理过期的活跃用户记录"""
        now = time.time()
        expired_groups = []
        for group_id, users in self.active_users.items():
            expired_users = [uid for uid, data in users.items() if now > data.get("window_end", 0)]
            for uid in expired_users:
                del users[uid]
            if not users:
                expired_groups.append(group_id)
        for gid in expired_groups:
            del self.active_users[gid]

    def _get_boredom_params(self):
        return {
            "enabled": self.plugin.cfg.boredom_enabled,
            "threshold": 0.3,  # 硬编码：信息熵阈值
            "consecutive_count": self.plugin.cfg.boredom_consecutive_count,
        }

    async def _update_boredom_async(self, group_id: str, entropy: float):
        """更新无聊状态（异步版本，带锁）"""
        params = self._get_boredom_params()
        if not params["enabled"]:
            return False
        async with self._boredom_lock:
            boredom = self.boredom_cache[group_id]
            current_time = time.time()
            if current_time - boredom["last_message_time"] > 120:
                boredom["count"] = 0
            boredom["last_message_time"] = current_time
            if entropy < params["threshold"]:
                boredom["count"] += 1
            else:
                boredom["count"] = max(0, boredom["count"] - 1)
            is_bored = boredom["count"] >= params["consecutive_count"]
        logger.debug(
            f"[Boredom] Group {group_id}: entropy={entropy:.2f}, count={boredom['count']}, is_bored={is_bored}"
        )
        return is_bored

    def _update_boredom(self, group_id: str, entropy: float):
        params = self._get_boredom_params()
        if not params["enabled"]:
            return False
        boredom = self.boredom_cache[group_id]
        current_time = time.time()
        if current_time - boredom["last_message_time"] > 120:
            boredom["count"] = 0
        boredom["last_message_time"] = current_time
        # 熵值低（信息量小）→ 累积无聊；熵值高（信息量大）→ 消除无聊
        if entropy < params["threshold"]:
            boredom["count"] += 1
        else:
            boredom["count"] = max(0, boredom["count"] - 1)
        is_bored = boredom["count"] >= params["consecutive_count"]
        logger.debug(
            f"[Boredom] Group {group_id}: entropy={entropy:.2f}, count={boredom['count']}, is_bored={is_bored}"
        )
        return is_bored

    def _get_leaky_params(self):
        return {
            "enabled": self.plugin.cfg.leaky_integrator_enabled,
            "decay": self.plugin.cfg.leaky_decay_factor,
            "threshold": self.plugin.cfg.leaky_trigger_threshold,
            "interest_boost": self.plugin.cfg.interest_boost,
            "daily_boost": self.plugin.cfg.daily_chat_boost,
        }

    def _calculate_boost(self, msg_text: str) -> float:
        params = self._get_leaky_params()

        critical_keywords = self.plugin.cfg.critical_keywords
        if critical_keywords:
            try:
                pattern = re.compile(f"({critical_keywords})", re.IGNORECASE)
                if pattern.search(msg_text):
                    return params["interest_boost"]
            except Exception:
                pass

        return params["daily_boost"]

    async def handle_message(self, event: AstrMessageEvent):
        msg_text = event.get_extra("self_evolution_message_text", event.message_str or "")
        session_id = str(event.session_id)
        user_id = str(event.get_sender_id())
        sender_name = event.get_sender_name() or "Unknown"
        is_at = event.is_at_or_wake_command

        group_id = event.get_group_id()

        is_private = not group_id
        if is_private:
            buffer_key = f"private_{user_id}"
            label = f"私聊 {user_id}"
            group_id_for_active = f"private_{user_id}"  # 用于active_users的key
        else:
            buffer_key = str(group_id)
            label = f"群 {group_id}"
            group_id_for_active = str(group_id)

        # === 图片检测：作为发言意愿加分项 ===
        image_boost = 0.0
        has_image = False
        try:
            if event.message_obj and hasattr(event.message_obj, "message"):
                for comp in event.message_obj.message:
                    comp_type = type(comp).__name__
                    if comp_type == "Image":
                        has_image = True
                        break
        except Exception:
            pass

        if has_image:
            image_boost = 0.1
            logger.debug(f"[漏斗] 检测到图片，欲望 +{image_boost}")

        logger.debug(f"[CognitionCore] 收到待评估消息，{label}: {msg_text[:30] if msg_text else '(无文字)'}")

        # 漏斗机制：检测用户是否活跃
        funnel_triggered = await self._check_funnel_trigger(event)

        if funnel_triggered:
            await self._mark_user_active_async(group_id_for_active, user_id)
            logger.debug(f"[漏斗] 用户 {user_id} 在群 {group_id_for_active} 被标记为活跃")

        # 清理过期的活跃用户（每100条消息清理一次）
        self._msg_counter = getattr(self, "_msg_counter", 0) + 1
        if self._msg_counter % 100 == 0:
            self.cleanup_expired_active_users()

        config = self.plugin.context.get_config()
        bot_wake_prefixes = config.get("wake_prefix", ["/"])
        prov_wake_prefix = config.get("provider_settings", {}).get("wake_prefix", "/")
        all_prefixes = set(bot_wake_prefixes)
        if prov_wake_prefix:
            all_prefixes.add(prov_wake_prefix)

        if any(msg_text.startswith(prefix) for prefix in all_prefixes):
            return

        score = await self.plugin.dao.get_affinity(user_id)
        if score <= 0:
            return

        if group_id:
            async with self._session_lock:
                self.global_window[group_id].append(f"{sender_name}: {msg_text}")

        entropy = self._calculate_entropy(msg_text)
        boredom_params = self._get_boredom_params()
        is_bored = False
        if boredom_params["enabled"]:
            is_bored = await self._update_boredom_async(group_id, entropy)

        params = self._get_leaky_params()

        if not params["enabled"]:
            logger.debug(f"[CognitionCore] 漏斗积分器未启用，跳过 ({label})")
            return

        # 计算 boost 值（统一入口，根据触发条件不同）
        critical_pattern = re.compile(f"({self.plugin.cfg.critical_keywords})", re.IGNORECASE)
        funnel_triggered = await self._check_funnel_trigger(event)

        trigger_reason = ""
        boost = params["daily_boost"]  # 默认普通消息 boost

        if critical_pattern.search(msg_text):
            boost = params["interest_boost"]
            trigger_reason = "关键词"
        elif is_at:
            boost = params["interest_boost"]
            trigger_reason = "@机器人"
        elif funnel_triggered:
            boost = params["interest_boost"] * 0.8  # 漏斗触发稍低
            trigger_reason = "意图"
        elif has_image and image_boost > 0:
            boost = image_boost
            trigger_reason = "图片"

        # 图片加分：在其他条件基础上额外加分
        if has_image and image_boost > 0 and trigger_reason != "图片":
            boost += image_boost
            trigger_reason += "+图片"

        # 信息质量多维度检查
        char_diversity = len(set(msg_text)) / len(msg_text) if msg_text else 0

        # 1. 熵值过低 = 重复字符（如"哈哈哈"）
        if entropy < 0.3:
            if not is_at:
                logger.debug(f"[CognitionCore] 信息熵过低跳过: {msg_text[:10] if msg_text else '(空)'} ({label})")
                return

        # 2. 字符多样性过低 = 大量重复字符
        if char_diversity < 0.15 and len(msg_text) > 10:
            if not is_at:
                logger.debug(f"[CognitionCore] 字符多样性过低跳过: {msg_text[:10] if msg_text else '(空)'} ({label})")
                return

        # 3. 熵值过高 + 字符多样性异常 = 可能是乱码
        if entropy > 0.95 and char_diversity > 0.9 and len(msg_text) > 50:
            if not is_at:
                logger.debug(f"[CognitionCore] 疑似乱码跳过: {msg_text[:10] if msg_text else '(空)'} ({label})")
                return

        # 统一欲望累积流程
        current_time = time.time()
        async with self._bucket_lock:
            bucket_data = self._get_or_init_bucket_data(session_id, current_time)

            # 如果正在观察期间遇到感兴趣话题，重置计数器
            if bucket_data.get("triggered", False) and trigger_reason:
                bucket_data["consecutive_replies"] = 0
                logger.debug(f"[CognitionCore] 观察期间遇到 {trigger_reason}，重置观察计数器 ({label})")

            last_time = bucket_data.get("last_time", current_time)
            delta_t = current_time - last_time

            is_cooling_down = bucket_data.get("is_cooling_down", False)
            cooling_end_time = bucket_data.get("cooling_end_time", 0)

            if is_cooling_down and current_time >= cooling_end_time:
                is_cooling_down = False
                logger.debug(f"[CognitionCore] 冷却结束，欲望恢复累积 ({label})")

            old_value = float(bucket_data.get("value", 2.0))

            if is_cooling_down:
                decay_factor = 0.3
                exp_decay = math.exp(-decay_factor * delta_t / 60)
                new_value = old_value * exp_decay
                logger.debug(f"[CognitionCore] 贤者时间冷却中 Z={new_value:.2f}/{params['threshold']} ({label})")
            else:
                decay_factor = params.get("decay", 0.9)
                exp_decay = math.exp(-decay_factor * delta_t / 60)
                new_value = old_value * exp_decay + boost
                logger.debug(
                    f"[CognitionCore] 欲望累积 [{trigger_reason}] Z={new_value:.2f}/{params['threshold']} boost={boost:.1f} ({label})"
                )

            self.leaky_bucket[session_id] = {
                "value": new_value,
                "last_time": current_time,
                "is_cooling_down": is_cooling_down,
                "cooling_end_time": cooling_end_time,
                "triggered": bucket_data.get("triggered", False),
                "consecutive_replies": bucket_data.get("consecutive_replies", 0),
            }

            current_z = new_value
            triggered = bucket_data.get("triggered", False)
            consecutive_replies = bucket_data.get("consecutive_replies", 0)
        cooldown_messages = self.plugin.cfg.desire_cooldown_messages

        if current_z >= params["threshold"] and not triggered:
            logger.debug(
                f"[CognitionCore] 欲望触发! Z={current_z:.2f} >= {params['threshold']}，将观察 {cooldown_messages} 条消息后进入贤者时间"
            )
            bucket_data["triggered"] = True
            bucket_data["triggered_time"] = current_time
            bucket_data["value"] = current_z
            self.leaky_bucket[session_id] = bucket_data
            async for result in self._evaluate_interjection(event, session_id):
                yield result
            return
        elif triggered:
            logger.debug(f"[CognitionCore] 欲望已触发，观察中 {consecutive_replies}/{cooldown_messages} ({label})")
        else:
            if session_id not in self.processing_sessions:
                session_buffer = self.session_buffers.get(buffer_key)
                if not session_buffer:
                    session_buffer = {
                        "messages": [],
                        "eavesdrop_count": 0,
                        "threshold": self.plugin.cfg.eavesdrop_message_threshold,
                    }
                    self.session_buffers[buffer_key] = session_buffer
                msg_count = len(session_buffer.get("messages", []))
                dynamic_threshold = session_buffer.get("threshold", self.plugin.cfg.eavesdrop_message_threshold)

                if msg_count >= dynamic_threshold:
                    logger.debug(f"[CognitionCore] 消息数阈值触发 {msg_count}/{dynamic_threshold} ({label})")
                    count = session_buffer.get("eavesdrop_count", 0) + 1
                    session_buffer["eavesdrop_count"] = count

                    if count >= 1:
                        async for result in self._evaluate_interjection(event, session_id):
                            yield result

    async def _evaluate_interjection(self, event: AstrMessageEvent, session_id: str, force_immediate: bool = False):
        """插嘴评估层：使用 session_buffers 作为上下文"""
        if session_id in self.processing_sessions:
            return

        self.processing_sessions.add(session_id)
        try:
            group_id = event.get_group_id()
            user_id = str(event.get_sender_id())
            if group_id:
                lookup_key = str(group_id)
            else:
                lookup_key = f"private_{user_id}"
            session_buffer = self.session_buffers.get(lookup_key)
            if not session_buffer:
                session_buffer = {"messages": [], "token_count": 0}
                self.session_buffers[lookup_key] = session_buffer

            buffer = session_buffer.get("messages", [])
            snap_len = len(buffer)

            if force_immediate:
                sender_name = event.get_sender_name() or "Unknown"
                sender_id = str(event.get_sender_id())
                chat_history = f"{sender_name}({sender_id}): {event.message_str}"
            else:
                chat_history = "\n".join(buffer)

            # 获取框架人格信息
            persona_name = "AI"
            persona_prompt = ""
            try:
                personality = await self.plugin.context.persona_manager.get_default_persona_v3(
                    umo=event.unified_msg_origin
                )
                if personality:
                    persona_name = personality.get("name", "AI")
                    persona_prompt = personality.get("prompt", "")
            except Exception:
                pass

            # 获取对话上下文
            contexts = []
            if self.plugin.cfg.inject_group_history:
                hist_str = await get_group_history(self.plugin, str(group_id), self.plugin.cfg.group_history_count)
                if hist_str:
                    chat_history = hist_str
            # 构建判断prompt
            prompt_parts = []
            if persona_prompt:
                prompt_parts.append(persona_prompt)
            prompt_parts.append(f"\n对话：\n{chat_history}\n")

            # 添加 SAN 值
            if self.plugin.san_enabled:
                san_prompt = self.plugin.san_system.get_prompt_injection()
                if san_prompt:
                    prompt_parts.append(san_prompt)

            # 添加表情包库
            if self.plugin.cfg.sticker_learning_enabled:
                sticker_prompt = await self.plugin.entertainment.get_prompt_injection()
                if sticker_prompt:
                    prompt_parts.append(sticker_prompt)

            prompt_parts.append("有趣吗？有趣[+3] / 无聊[-1]\n")
            prompt_parts.append("数值由你自己决定。只返回判定结果，不要生成任何回复内容。")
            decision_prompt = "".join(prompt_parts)

            llm_provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
            if not llm_provider:
                return

            # 根据配置决定是否禁用框架 contexts
            if self.plugin.cfg.disable_framework_contexts:
                contexts = []

            logger.info(f"[CognitionCore] 触发插嘴判断... Prompt长度: {len(decision_prompt)}")
            logger.debug(f"[CognitionCore] 插嘴判断 Prompt: {decision_prompt[:200]}...")
            res = await llm_provider.text_chat(
                prompt=decision_prompt,
                contexts=contexts,
            )

            reply_text = res.completion_text.strip()

            if not reply_text:
                logger.warning("[CognitionCore] LLM 返回空响应，已离线...")
                return

            logger.debug(f"[CognitionCore] LLM 决策原始响应:\n{reply_text}")

            # 解析有趣/无聊判定并调整阈值和SAN
            session_buffer = self.session_buffers.get(lookup_key, {})
            threshold_min = self.plugin.cfg.eavesdrop_threshold_min
            threshold_max = self.plugin.cfg.eavesdrop_threshold_max

            # 简化解析：支持多种格式
            # 只匹配明确标注"有趣"的格式，避免匹配"无聊[-1]"中的[-1]
            interesting_match = re.search(r"(有趣)\s*\[([+-]?\d+)\]", reply_text, re.IGNORECASE)
            boring_match = re.search(r"(无聊)\s*\[(-?\d+)\]", reply_text, re.IGNORECASE)
            ignore_match = re.search(r"(忽略|IGNORE|跳过|不感兴趣)", reply_text, re.IGNORECASE)

            if interesting_match:
                value = int(interesting_match.group(2))
                current_threshold = session_buffer.get("threshold", 20)
                new_threshold = max(threshold_min, current_threshold - value)
                session_buffer["threshold"] = new_threshold
                if session_id in self.leaky_bucket:
                    self.leaky_bucket[session_id]["value"] += value
                logger.info(f"[CognitionCore] 有趣判定！欲望+{value}，阈值降至 {new_threshold}")
                # 有趣时，生成正式回复
                formal_reply = await self._generate_formal_reply(event, session_id, chat_history, persona_name)
                if formal_reply:
                    logger.info(f"[CognitionCore] 有趣判定生成正式回复: {formal_reply[:30]}")
                    yield event.plain_result(formal_reply)

                    # AI 回复了，增加连续回复计数器
                    bucket_data = self.leaky_bucket.get(session_id, {})
                    if bucket_data.get("triggered", False):
                        consecutive_replies = bucket_data.get("consecutive_replies", 0) + 1
                        cooldown_messages = getattr(self.plugin, "desire_cooldown_messages", 5)

                        # 检查当前消息是否包含兴趣关键词
                        msg_for_check = event.message_str or ""
                        critical_pattern_check = re.compile(f"({self.plugin.critical_keywords})", re.IGNORECASE)
                        if critical_pattern_check.search(msg_for_check):
                            bucket_data["consecutive_replies"] = 0
                            logger.debug("[CognitionCore] 本条消息包含兴趣关键词，重置观察计数器，继续回复")
                        else:
                            bucket_data["consecutive_replies"] = consecutive_replies
                            logger.debug(f"[CognitionCore] AI 回复第 {consecutive_replies}/{cooldown_messages} 条")

                    if consecutive_replies >= cooldown_messages:
                        bucket_data = self.leaky_bucket.get(session_id, {})
                        current_time = time.time()
                        new_urge = bucket_data.get("value", 2.0) * 0.1
                        bucket_data["value"] = new_urge
                        bucket_data["is_cooling_down"] = True
                        bucket_data["cooling_end_time"] = current_time + 60
                        bucket_data["triggered"] = False
                        bucket_data["consecutive_replies"] = 0
                        session_buffer["consecutive_replies"] = 0
                        self.leaky_bucket[session_id] = bucket_data
                        logger.debug(
                            f"[CognitionCore] 连续回复 {consecutive_replies} 条，进入贤者时间，欲望降至 {new_urge:.2f}"
                        )
                    else:
                        bucket_data = self.leaky_bucket.get(session_id, {})
                        self.leaky_bucket[session_id] = bucket_data
                    return
            elif boring_match:
                value = int(boring_match.group(2))
                current_threshold = session_buffer.get("threshold", 20)
                new_threshold = min(threshold_max, current_threshold + value)
                session_buffer["threshold"] = new_threshold
                await self._decrease_san(event, value)
                logger.info(f"[CognitionCore] 无聊判定！SAN-{value}，阈值升至 {new_threshold}")
                logger.info("[CognitionCore] 无聊判定，不回应。")
                # 尝试生成内心独白
                if self.plugin.cfg.inner_monologue_enabled:
                    await self._generate_inner_monologue(event, session_id, "无聊")
                return
            elif ignore_match:
                logger.info("[CognitionCore] 判定为忽略，不回应。")
                # 尝试生成内心独白
                if self.plugin.cfg.inner_monologue_enabled:
                    await self._generate_inner_monologue(event, session_id, "忽略")
                return
            else:
                # 无法解析时，检查是否包含正/负数（无方括号也可）
                number_match = re.search(r"([+-]?\d+)", reply_text)
                if number_match:
                    value = int(number_match.group(1))
                    if value < 0:
                        logger.info("[CognitionCore] 判定为负数（无聊），不回应。")
                        return
                    elif value > 0:
                        logger.info("[CognitionCore] 判定为正数（有趣）")
                        yield event.plain_result(reply_text)

                        # AI 回复了，始终检查贤者时间（不依赖 triggered 状态）
                        consecutive_replies = session_buffer.get("consecutive_replies", 0) + 1
                        session_buffer["consecutive_replies"] = consecutive_replies
                        cooldown_messages = getattr(self.plugin, "desire_cooldown_messages", 5)

                        msg_for_check = event.message_str or ""
                        critical_pattern_check = re.compile(f"({self.plugin.critical_keywords})", re.IGNORECASE)
                        if critical_pattern_check.search(msg_for_check):
                            session_buffer["consecutive_replies"] = 0
                            consecutive_replies = 0
                            logger.debug("[CognitionCore] 本条消息包含兴趣关键词，重置观察计数器，继续回复")

                        logger.debug(f"[CognitionCore] AI 回复第 {consecutive_replies}/{cooldown_messages} 条")

                        bucket_data = self.leaky_bucket.get(session_id, {})
                        if consecutive_replies >= cooldown_messages:
                            current_time = time.time()
                            cooldown_seconds = getattr(self.plugin.cfg, "desire_cooldown_seconds", 60)
                            new_urge = bucket_data.get("value", 2.0) * 0.1
                            bucket_data["value"] = new_urge
                            bucket_data["is_cooling_down"] = True
                            bucket_data["cooling_end_time"] = current_time + cooldown_seconds
                            bucket_data["triggered"] = False
                            bucket_data["consecutive_replies"] = 0
                            session_buffer["consecutive_replies"] = 0
                            self.leaky_bucket[session_id] = bucket_data
                            logger.debug(
                                f"[CognitionCore] 连续回复 {consecutive_replies} 条，进入贤者时间 {cooldown_seconds}秒，欲望降至 {new_urge:.2f}"
                            )
                        else:
                            bucket_data["consecutive_replies"] = consecutive_replies
                            self.leaky_bucket[session_id] = bucket_data
                    else:  # value == 0
                        logger.info("[CognitionCore] 判定为0，静默")
                        return
                else:
                    logger.info("[CognitionCore] 无法解析 LLM 判定，发送原始回复")
                    yield event.plain_result(reply_text)
        except Exception as e:
            logger.warning(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            try:
                self.reset_eavesdrop_count(str(session_id))
                self.processing_sessions.discard(str(session_id))
            except Exception as e:
                logger.warning(f"[CognitionCore] 清理 processing_sessions 失败: {e}")

    async def _decrease_san(self, event: AstrMessageEvent, value: int):
        """降低SAN精力值"""
        try:
            san = getattr(self.plugin, "san_system", None)
            if san and hasattr(san, "consume"):
                await san.consume(value)
                logger.debug(f"[CognitionCore] 无聊判定，降低SAN: -{value}")
        except Exception as e:
            logger.warning(f"[CognitionCore] 降低SAN失败: {e}")

    async def _generate_inner_monologue(self, event: AstrMessageEvent, session_id: str, reason: str):
        """生成内心独白并缓存"""
        try:
            user_id = event.get_sender_id()
            group_id = event.get_group_id()
            buffer_key = str(group_id) if group_id else f"private_{user_id}"

            # 检查是否已有缓存的内心独白
            session_buffer = self.session_buffers.get(buffer_key, {})
            if session_buffer.get("inner_monologue"):
                logger.debug("[CognitionCore] 已有缓存的内心独白，跳过生成")
                return

            provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                logger.warning("[CognitionCore] 获取 provider 失败，无法生成内心独白")
                return

            prompt = f"""你正在群聊/私聊中听到一段对话，但你选择不直接回复。
原因：{reason}
请用简短的几句话表达你内心的想法或反应。
请直接输出，不要有任何格式前缀。
输出格式：<inner_monologue>你的内心独白</inner_monologue>"""

            res = await provider.text_chat(prompt=prompt, contexts=[])

            if not res or not res.completion_text:
                logger.warning("[CognitionCore] 生成内心独白失败：LLM 响应为空")
                return

            response_text = res.completion_text.strip()

            # 解析内心独白
            match = re.search(r"<inner_monologue>(.*?)</inner_monologue>", response_text, re.DOTALL)
            if match:
                monologue = match.group(1).strip()
            else:
                monologue = response_text

            if monologue:
                # 存入 session_buffer
                if buffer_key not in self.session_buffers:
                    self.session_buffers[buffer_key] = {}
                self.session_buffers[buffer_key]["inner_monologue"] = monologue
                # 设置到 event 对象上，让 main.py 能够读取
                setattr(event, "_inner_monologue", monologue)

                logger.debug(f"[CognitionCore] 内心独白已缓存(内存): {monologue[:50]}...")
            else:
                logger.warning("[CognitionCore] 无法解析内心独白内容")

        except Exception as e:
            logger.warning(f"[CognitionCore] 生成内心独白异常: {e}")

    async def _generate_formal_reply(
        self,
        event: AstrMessageEvent,
        session_id: str,
        chat_history: str,
        persona_name: str,
    ) -> str:
        """有趣时生成正式回复（带人设+上下文）"""
        try:
            # 获取用户信息
            user_id = str(event.get_sender_id())
            user_name = event.get_sender_name() or "Unknown User"
            group_id = event.get_group_id()
            role_info = "（管理员）" if event.is_admin() else ""

            # 获取好感度
            affinity = 50
            try:
                affinity = await self.plugin.dao.get_affinity(user_id)
            except Exception:
                pass

            # 获取完整人格
            persona_prompt = ""
            try:
                personality = await self.plugin.context.persona_manager.get_default_persona_v3(
                    umo=event.unified_msg_origin
                )
                if personality:
                    persona_name = personality.get("name", persona_name)
                    persona_prompt = personality.get("prompt", "")
            except Exception:
                pass

            # 获取对话上下文
            contexts = []
            if self.plugin.cfg.inject_group_history:
                chat_history_str = await get_group_history(
                    self.plugin, str(group_id), self.plugin.cfg.group_history_count
                )
                if chat_history_str:
                    chat_history = chat_history_str

            # 构建正式回复的prompt
            prompt_parts = []
            if persona_prompt:
                prompt_parts.append(persona_prompt)

            # 添加身份上下文（核心修复！）
            identity_context = build_identity_context(
                user_id=user_id,
                user_name=user_name,
                affinity=affinity,
                role_info=role_info,
                is_group=bool(group_id),
            )
            prompt_parts.append(identity_context)

            # 补充 SAN 值系统
            if self.plugin.san_enabled:
                try:
                    san_prompt = self.plugin.san_system.get_prompt_injection()
                    if san_prompt:
                        prompt_parts.append(san_prompt)
                except Exception:
                    pass

            # 补充表情包库
            if self.plugin.cfg.sticker_learning_enabled:
                try:
                    sticker_prompt = await self.plugin.entertainment.get_prompt_injection()
                    if sticker_prompt:
                        prompt_parts.append(sticker_prompt)
                except Exception:
                    pass

            prompt_parts.append(f"\n对话：\n{chat_history}\n")
            prompt_parts.append("你觉得这个对话很有趣，决定参与。现在该你参与互动了。")
            formal_prompt = "".join(prompt_parts)

            llm_provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
            if not llm_provider:
                return ""

            logger.info("[CognitionCore] 正在请求正式回复...")
            # 不传框架的历史contexts，避免历史干扰导致身份混淆
            res = await llm_provider.text_chat(
                prompt=formal_prompt,
                contexts=[],
            )

            reply = res.completion_text.strip()
            reply = re.sub(r"\n{3,}", "\n\n", reply)
            return reply

        except Exception as e:
            logger.warning(f"[CognitionCore] 生成正式回复失败: {e}")
            return ""

    def reset_eavesdrop_count(self, group_id: str):
        """重置互动意愿触发计数器"""
        if group_id in self.session_buffers:
            self.session_buffers[group_id]["eavesdrop_count"] = 0

    # ==================== 插嘴功能 ====================

    async def _get_interject_prompt(self, umo: str | None = None) -> str:
        """获取插嘴判断的 system prompt"""
        persona_prompt = ""
        try:
            personality = await self.plugin.context.persona_manager.get_default_persona_v3(umo=umo)
            if personality:
                persona_prompt = personality.get("prompt", "")
        except Exception as e:
            logger.debug(f"[Interject] 获取主人格设定失败: {e}")

        if persona_prompt:
            base_prompt = f"你是 {self.plugin.persona_name}。\n\n{persona_prompt}\n\n"
        else:
            base_prompt = f"你是 {self.plugin.persona_name}。\n\n"

        try:
            if self.plugin._prompts_injection:
                extra_rules = self.plugin._prompts_injection.get("interject", {}).get("judge_prompt", "")
                if extra_rules:
                    extra_rules = extra_rules.replace("{persona_name}", self.plugin.persona_name)
                    return base_prompt + extra_rules
        except Exception as e:
            logger.warning(f"[Interject] 获取插嘴提示词失败: {e}")

        default_prompt = f"你是 {self.plugin.persona_name}。根据群聊消息判断是否应该主动插嘴，只输出JSON。"
        return base_prompt + default_prompt

    def _local_interject_filter(self, formatted_messages: list) -> dict:
        """本地轻量级前置过滤（第三层漏斗）

        在调用 LLM 之前，先用本地规则快速判断这批消息是否值得分析。

        Returns:
            {
                "should_continue": bool,  # 是否继续调用 LLM
                "reason": str,  # 原因
                "keywords_found": list,  # 匹配到的关键词
            }
        """
        if not formatted_messages:
            return {"should_continue": False, "reason": "无消息", "keywords_found": []}

        # 疑问词模式
        question_patterns = [
            r"怎么",
            r"为什么",
            r"怎么办",
            r"如何",
            r"是不是",
            r"能不能",
            r"会不会",
            r"有没有",
            r"求问",
            r"求解",
            r"求助",
            r"问一下",
            r"问一下",
            r"求告知",
        ]

        # 情绪/话题词模式
        emotion_patterns = [
            r"好无聊",
            r"绝了",
            r"笑死",
            r"哈哈哈",
            r"太离谱",
            r"争议",
            r"讨论",
            r"吵架",
            r"分歧",
            r"话题",
            r"好烦",
            r"郁闷",
            r"纠结",
            r"求助",
            r"帮忙",
        ]

        # 复读机/无意义模式（应该过滤掉）
        spam_patterns = [
            r"^1+$",
            r"^11+$",
            r"^111+$",
            r"^1111+$",  # 纯数字
            r"^哈+$",
            r"^哈哈+$",
            r"^哈哈哈+$",  # 纯哈
            r"^[\U0001F600-\U0001F64F]+$",  # 纯emoji
        ]

        import re

        keywords_found = []
        is_question = False
        is_emotion = False
        is_spam = True  # 假设是垃圾消息，除非找到有意义的关键词

        for msg in formatted_messages:
            msg_lower = msg.lower()

            # 检查疑问词
            for pattern in question_patterns:
                if re.search(pattern, msg_lower):
                    is_question = True
                    keywords_found.append(f"疑问词:{pattern}")
                    is_spam = False

            # 检查情绪词
            for pattern in emotion_patterns:
                if re.search(pattern, msg_lower):
                    is_emotion = True
                    keywords_found.append(f"情绪词:{pattern}")
                    is_spam = False

            # 检查是否是垃圾消息
            for pattern in spam_patterns:
                if re.search(pattern, msg):
                    # 如果整条消息匹配垃圾模式，认为是垃圾
                    if len(msg.strip()) < 20:
                        continue

        if is_spam and not keywords_found:
            # 随机松绑：即使没有命中关键词，也有概率放行
            import random

            bypass_rate = self.plugin.cfg.interject_random_bypass_rate
            if random.random() < bypass_rate:
                logger.debug(f"[Interject] 群: 随机松绑命中，概率={bypass_rate}")
                return {
                    "should_continue": True,
                    "reason": f"本地过滤：随机松绑(概率{bypass_rate})",
                    "keywords_found": keywords_found,
                }
            return {
                "should_continue": False,
                "reason": "本地过滤：消息无明显话题关键词，可能是复读或无意义内容",
                "keywords_found": keywords_found,
            }

        if is_question or is_emotion:
            return {
                "should_continue": True,
                "reason": f"本地过滤通过：发现疑问词={is_question}, 情绪词={is_emotion}",
                "keywords_found": keywords_found,
            }

        return {
            "should_continue": True,
            "reason": "本地过滤：无可疑特征，但继续让LLM判断",
            "keywords_found": keywords_found,
        }

    def _clean_message(self, message: str) -> str:
        """清洗消息中的括号、星号动作和空行"""
        message = re.sub(r"[（(][^）)]*[）)]", "", message)
        message = re.sub(r"\*[^*]+\*", "", message)
        message = re.sub(r"\n\s*\n", "\n", message)
        return message.strip()

    async def interject_check_group(self, group_id: str):
        """检查单个群是否需要插嘴 - 四层漏斗模型

        第一层：基础状态拦截（本地极速判断）
        第二层：动态冷却+留白检测
        第三层：本地轻量级前置过滤
        第四层：LLM深度分析
        """
        layer = 0
        try:
            # ========== 第一层：基础状态拦截 ==========
            layer = 1
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                logger.debug(f"[Interject] 群 {group_id}: [L1] 无平台实例")
                return

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                logger.debug(f"[Interject] 群 {group_id}: [L1] 平台无 get_client")
                return

            bot = platform.get_client()
            if not bot:
                logger.debug(f"[Interject] 群 {group_id}: [L1] 无法获取 bot 实例")
                return

            # 检查白名单
            whitelist = getattr(self.plugin.cfg, "target_group_scopes", [])
            if whitelist and group_id not in [str(g) for g in whitelist]:
                logger.debug(f"[Interject] 群 {group_id}: [L1] 不在白名单，跳过")
                return

            # 检查群是否被禁言
            if group_id in self.plugin._shut_until_by_group:
                if time.time() < self.plugin._shut_until_by_group[group_id]:
                    remaining = int(self.plugin._shut_until_by_group[group_id] - time.time())
                    logger.debug(f"[Interject] 群 {group_id}: [L1] 群被禁言，剩余 {remaining} 秒")
                    return
                else:
                    del self.plugin._shut_until_by_group[group_id]

            # 获取消息
            msg_count = self.plugin.cfg.group_history_count
            result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=msg_count)
            messages = result.get("messages", [])
            if not messages:
                logger.debug(f"[Interject] 群 {group_id}: [L1] 无历史消息")
                return

            # 消息列表是倒序的，messages[0]最新，messages[-1]最旧
            # 但我们需要最新的消息来判断时间
            latest_msg = messages[0]  # 最新
            earliest_msg = messages[-1]  # 最旧
            latest_msg_time = latest_msg.get("time", 0)
            latest_msg_seq = latest_msg.get("message_seq")

            logger.debug(
                f"[Interject] 群 {group_id}: [L1] 获取到{len(messages)}条消息，最新时间={latest_msg_time}, seq={latest_msg_seq}"
            )

            # 获取 bot_id
            try:
                login_info = await bot.call_action("get_login_info")
                bot_id = str(login_info.get("user_id", ""))
            except Exception:
                bot_id = str(getattr(platform, "client_self_id", ""))

            # 第一层：检查最新消息是否是 AI 自己发的
            latest_sender = latest_msg.get("sender", {})
            latest_sender_id = str(latest_sender.get("user_id", ""))
            if latest_sender_id == bot_id:
                logger.debug(f"[Interject] 群 {group_id}: [L1] 最新消息是AI自己发的，跳过")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            # ========== 第二层：动态冷却+留白检测 ==========
            layer = 2
            cooldown_seconds = self.plugin.cfg.interject_cooldown * 60
            silence_timeout = self.plugin.cfg.interject_silence_timeout

            # 检查是否在冷却期内（严格模式：只要在冷却期就跳过）
            if group_id in self._interject_history:
                last_time = self._interject_history[group_id].get("last_time", 0)
                elapsed = time.time() - last_time
                if elapsed < cooldown_seconds:
                    remaining = cooldown_seconds - elapsed
                    logger.debug(f"[Interject] 群 {group_id}: [L2] 冷却期内，剩余 {remaining:.0f} 秒，跳过")
                    self._update_interject_cursor(group_id, latest_msg_seq)
                    return
                logger.debug(f"[Interject] 群 {group_id}: [L2] 冷却期已过，已过 {elapsed:.0f} 秒")

            # 留白检测：距离上一条消息是否已经过去了足够长的时间
            current_time = time.time()
            time_since_last_msg = current_time - latest_msg_time
            if time_since_last_msg < silence_timeout:
                logger.debug(
                    f"[Interject] 群 {group_id}: [L2] 留白检测未通过，距离上一条消息仅 {time_since_last_msg:.0f} 秒 < {silence_timeout} 秒，跳过"
                )
                self._update_interject_cursor(group_id, latest_msg_seq)
                return
            logger.debug(f"[Interject] 群 {group_id}: [L2] 留白检测通过，距离上一条消息 {time_since_last_msg:.0f} 秒")

            # ========== 计算新增消息数量 ==========
            layer = 3
            last_msg_seq = None
            if group_id in self._interject_history:
                last_msg_seq = self._interject_history[group_id].get("last_msg_seq")

            total_msgs = len(messages)
            new_msg_count = total_msgs

            if last_msg_seq is not None:
                for i in range(len(messages)):
                    msg_seq = messages[i].get("message_seq")
                    if msg_seq is not None and msg_seq <= last_msg_seq:
                        new_msg_count = i
                        break

            min_msg_count = self.plugin.cfg.interject_min_msg_count
            if new_msg_count < min_msg_count:
                logger.debug(f"[Interject] 群 {group_id}: [L3] 新增消息不足 {new_msg_count} < {min_msg_count}，跳过")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            logger.debug(f"[Interject] 群 {group_id}: [L3] 新增消息 {new_msg_count} >= {min_msg_count}，继续")

            # ========== 格式化消息 ==========
            formatted = await asyncio.gather(*[parse_message_chain(msg, self.plugin) for msg in messages])
            formatted = [f for f in formatted if f]
            if not formatted:
                logger.debug(f"[Interject] 群 {group_id}: [L3] 消息格式化为空")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            # ========== 第三层：本地轻量级前置过滤 ==========
            layer = 4
            if self.plugin.cfg.interject_local_filter_enabled:
                filter_result = self._local_interject_filter(formatted)
                if not filter_result["should_continue"]:
                    logger.debug(f"[Interject] 群 {group_id}: [L3] 本地过滤拦截: {filter_result['reason']}")
                    self._update_interject_cursor(group_id, latest_msg_seq)
                    return
                logger.debug(f"[Interject] 群 {group_id}: [L3] 本地过滤通过: {filter_result['reason']}")

            # ========== @检测：可配置是否要求最新消息必须 @ 机器人 ==========
            if self.plugin.cfg.interject_require_at:
                if not self._latest_message_mentions_bot(latest_msg, bot_id):
                    logger.debug(f"[Interject] 群 {group_id}: [L3.5] 最新消息未@机器人，跳过")
                    self._update_interject_cursor(group_id, latest_msg_seq)
                    return
            else:
                logger.debug(f"[Interject] 群 {group_id}: [L3.5] 已关闭 @ 门槛，继续进行主动插嘴分析")

            # ========== 第四层：LLM深度分析 ==========
            layer = 5
            group_umo = self.plugin.get_group_umo(group_id) if hasattr(self.plugin, "get_group_umo") else None
            llm_provider = self.plugin.context.get_using_provider(umo=group_umo)
            if not llm_provider:
                logger.debug(f"[Interject] 群 {group_id}: [L4] 无 LLM provider")
                return

            analyze_count = self.plugin.cfg.interject_analyze_count
            prompt = f"""分析以下群聊消息，判断是否应该主动插嘴。

当前机器人ID：{bot_id}

群聊消息：
{chr(10).join(formatted[:analyze_count])}

请以JSON格式输出判断结果：
{{
    "analysis": "简要分析当前群聊氛围和话题",
    "urgency_score": 0-100的紧迫度评分,
    "should_interject": true/false,
    "reason": "判断理由",
    "suggested_response": "如果应该插嘴，给出建议的回复内容"
}}

注意：
1. urgency_score 超过 {self.plugin.cfg.interject_urgency_threshold} 时才应该插嘴
2. 只有当群里有有趣的讨论、有争议的话题、或者有人提问但没人回答时才应该插嘴"""

            logger.debug(f"[Interject] 群 {group_id}: [L4] 正在请求LLM判断...")
            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt=await self._get_interject_prompt(umo=group_umo),
            )

            if not res.completion_text:
                logger.debug(f"[Interject] 群 {group_id}: [L4] LLM 无返回")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            # 解析 JSON
            match = re.search(r"\{.*\}", res.completion_text, re.DOTALL)
            if not match:
                logger.debug(f"[Interject] 群 {group_id}: [L4] LLM 返回无法解析 JSON")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                logger.debug(f"[Interject] 群 {group_id}: [L4] JSON 解析失败")
                self._update_interject_cursor(group_id, latest_msg_seq)
                return

            urgency_score = result.get("urgency_score", 0)
            should_interject = result.get("should_interject", False)
            reason = result.get("reason", "")
            suggested_response = result.get("suggested_response", "")

            threshold = self.plugin.cfg.interject_urgency_threshold

            logger.info(
                f"[Interject] 群 {group_id}: [L4] LLM返回: urgency={urgency_score}, should={should_interject}, threshold={threshold}"
            )

            # 只有 urgency_score 超过阈值时才插嘴
            if urgency_score >= threshold and should_interject:
                if suggested_response:
                    # 检查是否是影子模式（dry run）
                    if self.plugin.cfg.interject_dry_run:
                        logger.info(
                            f"[Interject] 群 {group_id}: [DRY-RUN] 满足插嘴条件，建议发送: {suggested_response[:50]}..."
                        )
                        self._update_interject_cursor(group_id, latest_msg_seq)
                        return

                    logger.debug(f"[Interject] 群 {group_id}: [L4] 满足插嘴条件，执行插嘴")
                    await self._do_interject(group_id, suggested_response, messages)
                    # 插嘴后更新 cursor 并进入冷却
                    self._interject_history[group_id] = {"last_time": time.time(), "last_msg_seq": latest_msg_seq}
                    return

            # 不插嘴，只更新 cursor
            logger.debug(f"[Interject] 群 {group_id}: [L4] 不满足插嘴条件，拒绝插嘴")
            self._update_interject_cursor(group_id, latest_msg_seq)

        except Exception as e:
            logger.warning(f"[Interject] 群 {group_id} 检查失败 [L{layer}]: {e}", exc_info=True)

    def _update_interject_cursor(self, group_id: str, latest_msg_seq):
        """更新插嘴游标（每次判定后调用）"""
        self._interject_history[group_id] = {
            "last_time": self._interject_history.get(group_id, {}).get("last_time", time.time()),
            "last_msg_seq": latest_msg_seq,
        }

    async def _do_interject(self, group_id: str, message: str, messages: list = None):
        """执行插嘴"""
        logger.info(f"[Interject] 群 {group_id} 准备插嘴，消息: {message[:50]}...")
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                logger.debug(f"[Interject] 群 {group_id}: 无平台实例")
                return

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                logger.debug(f"[Interject] 群 {group_id}: 平台无 get_client")
                return

            bot = platform.get_client()
            if not bot:
                logger.debug(f"[Interject] 群 {group_id}: 无法获取 bot 实例")
                return

            message = self._clean_message(message)
            if not message:
                logger.debug(f"[Interject] 群 {group_id}: 消息清洗后为空")
                return

            logger.debug(f"[Interject] 群 {group_id}: 清洗后的消息: {message[:50]}...")
            result = await bot.call_action("send_group_msg", group_id=int(group_id), message=message)

            msg_seq = None
            if result and isinstance(result, dict):
                msg_seq = result.get("message_id")  # message_id 在 QQ 中通常等于 message_seq

            self._interject_history[group_id] = {
                "last_time": time.time(),
                "last_msg_seq": msg_seq,
            }
            logger.info(f"[Interject] 群 {group_id} 插嘴成功! message_id={msg_seq}, 消息: {message[:50]}...")

        except Exception as e:
            logger.warning(f"[Interject] 群 {group_id} 插嘴失败: {e}")
