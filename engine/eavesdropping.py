from astrbot.api import logger
import re
import time
import asyncio
from collections import defaultdict
from astrbot.api.all import AstrMessageEvent


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self.global_window = defaultdict(list)
        self.window_size = 5
        self.leaky_bucket = defaultdict(dict)  # {"value": float, "last_time": float}
        self.boredom_cache = defaultdict(lambda: {"count": 0, "last_message_time": 0.0})

        # 中间消息拦截缓存：{session_id: {"messages": [], "last_update": timestamp}}
        self.intercepted_messages = defaultdict(
            lambda: {"messages": [], "last_update": 0.0}
        )
        self.intercepted_message_ttl = 30  # 缓存30秒后丢弃

        # 漏斗机制 - 用户活跃判定
        self.active_users = defaultdict(
            dict
        )  # {group_id: {user_id: {"last_active": timestamp, "window_end": timestamp}}}
        self.active_window_seconds = 30  # 30秒活跃窗口

        # 唤醒词列表
        self.wake_names = ["黑塔", "belta", "Bot", "机器人", "小塔"]

        # 中间消息模式 - 这些消息会在工具调用期间被拦截
        self.intermediate_message_patterns = [
            r"^让我",
            r"^让我先",
            r"^让我查查",
            r"^让我看看",
            r"^让我再",
            r"^我来帮你",
            r"^让我获取",
            r"^让我整理",
            r"^我先",
            r"^我先查",
            r"^我先看看",
            r"^我先了解一下",
            r"^让我先了解一下",
            r"^让我先查一下",
            r"^让我先看看",
            r"^让我来分析",
            r"^让我来",
        ]
        # 预编译中间消息正则
        self._intermediate_patterns_compiled = [
            re.compile(p) for p in self.intermediate_message_patterns
        ]

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
        self._ai_intent_patterns_compiled = [
            re.compile(p) for p in self.ai_intent_patterns
        ]

    def is_intermediate_message(self, text: str) -> bool:
        """检查消息是否是中间消息（工具调用期间的过渡性消息），应该被拦截"""
        if not text:
            return False
        text = text.strip()
        for pattern in self._intermediate_patterns_compiled:
            if pattern.match(text):
                logger.debug(f"[IntermediateFilter] 拦截中间消息: {text[:50]}")
                return True
        return False

    def cache_intercepted_message(self, session_id: str, message: str):
        """缓存被拦截的中间消息"""
        session_id = str(session_id)
        now = time.time()
        self.intercepted_messages[session_id]["messages"].append(message)
        self.intercepted_messages[session_id]["last_update"] = now
        logger.debug(
            f"[IntermediateFilter] 缓存中间消息，当前缓存 {len(self.intercepted_messages[session_id]['messages'])} 条"
        )

    def cleanup_expired_intercepted_messages(self):
        """清理过期的被拦截消息"""
        now = time.time()
        expired_sessions = []
        for session_id, data in self.intercepted_messages.items():
            if now - data["last_update"] > self.intercepted_message_ttl:
                if data["messages"]:
                    logger.debug(
                        f"[IntermediateFilter] 丢弃过期缓存消息 {len(data['messages'])} 条"
                    )
                expired_sessions.append(session_id)
        for session_id in expired_sessions:
            del self.intercepted_messages[session_id]

    def _calculate_entropy(self, text: str) -> float:
        """基于香农熵计算文本信息量"""
        if not text or len(text) < 2:
            return 1.0

        import math

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

    def _check_funnel_trigger(self, event: AstrMessageEvent) -> bool:
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
            if hasattr(reply_msg, "sender") and reply_msg.sender:
                bot_id = str(self.plugin.context.bot_info.get("user_id", ""))
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
            expired_users = [
                uid for uid, data in users.items() if now > data.get("window_end", 0)
            ]
            for uid in expired_users:
                del users[uid]
            if not users:
                expired_groups.append(group_id)
        for gid in expired_groups:
            del self.active_users[gid]

    def _get_boredom_params(self):
        return {
            "enabled": self.plugin.cfg.boredom_enabled,
            "threshold": self.plugin.cfg.boredom_threshold,
            "consecutive_count": self.plugin.cfg.boredom_consecutive_count,
            "sarcastic_reply": self.plugin.cfg.boredom_sarcastic_reply,
        }

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
        msg_text = event.message_str or ""
        session_id = str(event.session_id)
        user_id = str(event.get_sender_id())
        sender_name = event.get_sender_name() or "Unknown"
        is_at = event.is_at_or_wake_command

        group_id = event.get_group_id()

        is_private = not group_id
        if is_private:
            buffer_key = f"private_{user_id}"
            label = f"私聊 {user_id}"
        else:
            buffer_key = str(group_id)
            label = f"群 {group_id}"

        if not group_id:
            group_id = user_id
            is_private = True

        group_id = str(group_id)

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
            logger.info(f"[漏斗] 检测到图片，尝试获取标签...")
            try:
                image_summaries = await self.plugin.image_cache.process_image_captions(
                    event
                )
                if image_summaries:
                    image_boost = self.plugin.cfg.interest_boost
                    logger.info(f"[漏斗] 检测到图片，增加欲望值: +{image_boost}")
            except Exception as e:
                logger.warning(f"[漏斗] 图片处理失败: {e}")
        # =========================================

        logger.info(
            f"[CognitionCore] 收到待评估消息，{label}: {msg_text[:30] if msg_text else '(无文字)'}"
        )

        # 漏斗机制：检测用户是否活跃
        funnel_triggered = self._check_funnel_trigger(event)

        if funnel_triggered:
            self._mark_user_active(group_id, user_id)
            logger.debug(f"[漏斗] 用户 {user_id} 在群 {group_id} 被标记为活跃")

        # 清理过期的活跃用户（每100条消息清理一次）
        self._msg_counter = getattr(self, "_msg_counter", 0) + 1
        if self._msg_counter % 100 == 0:
            self.cleanup_expired_active_users()
            self.cleanup_expired_intercepted_messages()

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
            self.global_window[group_id].append(f"{sender_name}: {msg_text}")
            if len(self.global_window[group_id]) > self.window_size:
                self.global_window[group_id].pop(0)

        entropy = self._calculate_entropy(msg_text)
        boredom_params = self._get_boredom_params()
        is_bored = False
        if boredom_params["enabled"]:
            is_bored = self._update_boredom(group_id, entropy)

        # 将无聊状态传递给评估函数
        self._current_boredom_state = is_bored

        import time

        params = self._get_leaky_params()

        if not params["enabled"]:
            logger.info(f"[CognitionCore] 漏斗积分器未启用，跳过 ({label})")
            return

        # 计算 boost 值（统一入口，根据触发条件不同）
        critical_pattern = re.compile(
            f"({self.plugin.cfg.critical_keywords})", re.IGNORECASE
        )
        funnel_triggered = self._check_funnel_trigger(event)

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

        if len(msg_text) < 6 and not is_at and not has_image:
            logger.info(
                f"[CognitionCore] 消息过短跳过: {msg_text[:10] if msg_text else '(空)'} ({label})"
            )
            return
        elif entropy > 0.95:
            if not is_at:
                logger.info(
                    f"[CognitionCore] 信息熵过高跳过: {msg_text[:10] if msg_text else '(空)'} ({label})"
                )
                return

        # 统一欲望累积流程
        import math

        current_time = time.time()
        bucket_data = self._get_or_init_bucket_data(session_id, current_time)

        # 如果正在观察期间遇到感兴趣话题，重置计数器
        if bucket_data.get("triggered", False) and trigger_reason:
            bucket_data["consecutive_replies"] = 0
            logger.info(
                f"[CognitionCore] 观察期间遇到 {trigger_reason}，重置观察计数器 ({label})"
            )

        last_time = bucket_data.get("last_time", current_time)
        delta_t = current_time - last_time

        is_cooling_down = bucket_data.get("is_cooling_down", False)
        cooling_end_time = bucket_data.get("cooling_end_time", 0)

        if is_cooling_down and current_time >= cooling_end_time:
            is_cooling_down = False
            logger.info(f"[CognitionCore] 冷却结束，欲望恢复累积 ({label})")

        old_value = float(bucket_data.get("value", 2.0))

        if is_cooling_down:
            new_value = old_value + (delta_t * 0.01)
            logger.info(
                f"[CognitionCore] 冷却恢复中 Z={new_value:.2f}/{params['threshold']} ({label})"
            )
        else:
            decay_factor = params.get("decay", 0.9)
            exp_decay = math.exp(-decay_factor * delta_t / 60)
            new_value = old_value * exp_decay + boost

            logger.info(
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
            logger.info(
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
            logger.info(
                f"[CognitionCore] 欲望已触发，观察中 {consecutive_replies}/{cooldown_messages} ({label})"
            )
        else:
            if session_id not in self.plugin.session_manager.processing_sessions:
                session_buffer = self.plugin.session_manager.session_buffers.get(
                    buffer_key, {}
                )
                msg_count = len(session_buffer.get("messages", []))
                dynamic_threshold = session_buffer.get(
                    "threshold", self.plugin.cfg.eavesdrop_message_threshold
                )

                if msg_count >= dynamic_threshold:
                    logger.info(
                        f"[CognitionCore] 消息数阈值触发 {msg_count}/{dynamic_threshold} ({label})"
                    )
                    count = session_buffer.get("eavesdrop_count", 0) + 1
                    session_buffer["eavesdrop_count"] = count

                    if count >= 1:
                        async for result in self._evaluate_interjection(
                            event, session_id
                        ):
                            yield result

    async def _evaluate_interjection(
        self, event: AstrMessageEvent, session_id: str, force_immediate: bool = False
    ):
        """插嘴评估层：使用 session_buffers 作为上下文"""
        if session_id in self.plugin.session_manager.processing_sessions:
            return

        self.plugin.session_manager.processing_sessions.add(session_id)
        try:
            group_id = event.get_group_id()
            user_id = str(event.get_sender_id())
            if group_id:
                lookup_key = str(group_id)
            else:
                lookup_key = f"private_{user_id}"
            session_buffer = self.plugin.session_manager.session_buffers.get(lookup_key)
            if not session_buffer:
                session_buffer = {"messages": [], "token_count": 0}

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
                personality = (
                    await self.plugin.context.persona_manager.get_default_persona_v3(
                        event.unified_msg_origin
                    )
                )
                if personality:
                    persona_name = personality.get("name", "AI")
                    persona_prompt = personality.get("prompt", "")
            except Exception:
                pass

            # 获取对话上下文
            contexts = []
            try:
                history_mgr = self.plugin.context.message_history_manager
                if history_mgr and hasattr(history_mgr, "get"):
                    hist = await history_mgr.get(event.get_group_id(), limit=10)
                    if hist:
                        contexts = hist
            except Exception:
                pass

            # 构建判断prompt
            prompt_parts = []
            if persona_prompt:
                prompt_parts.append(persona_prompt)
            prompt_parts.append(f"\n对话：\n{chat_history}\n")
            prompt_parts.append("有趣吗？有趣[+3] / 无聊[-1]\n")
            prompt_parts.append(
                "数值由你自己决定。只返回判定结果，不要生成任何回复内容。"
            )
            decision_prompt = "".join(prompt_parts)

            llm_provider = self.plugin.context.get_using_provider(
                event.unified_msg_origin
            )
            if not llm_provider:
                return

            logger.info(
                f"[CognitionCore] 正在请求 LLM 决策自省... Prompt长度: {len(decision_prompt)}"
            )
            res = await llm_provider.text_chat(
                prompt=decision_prompt,
                contexts=contexts,
            )

            reply_text = res.completion_text.strip()

            if not reply_text:
                logger.warning("[CognitionCore] LLM 返回空响应，已离线...")
                return

            logger.info(f"[CognitionCore] LLM 决策原始响应:\n{reply_text}")

            # 解析有趣/无聊判定并调整阈值和SAN
            session_buffer = self.plugin.session_manager.session_buffers.get(
                lookup_key, {}
            )
            threshold_min = self.plugin.cfg.eavesdrop_threshold_min
            threshold_max = self.plugin.cfg.eavesdrop_threshold_max

            # 简化解析：支持多种格式
            interesting_match = re.search(
                r"(?:有趣)?\s*\[([+-]?\d+)\]", reply_text, re.IGNORECASE
            )
            boring_match = re.search(r"(无聊)\s*\[(-?\d+)\]", reply_text, re.IGNORECASE)
            ignore_match = re.search(
                r"(忽略|IGNORE|跳过|不感兴趣)", reply_text, re.IGNORECASE
            )

            if interesting_match:
                value = int(interesting_match.group(2))
                current_threshold = session_buffer.get("threshold", 20)
                new_threshold = max(threshold_min, current_threshold - value)
                session_buffer["threshold"] = new_threshold
                if session_id in self.leaky_bucket:
                    self.leaky_bucket[session_id]["value"] += value
                logger.info(
                    f"[CognitionCore] 有趣判定！欲望+{value}，阈值降至 {new_threshold}"
                )
                # 有趣时，生成正式回复
                formal_reply = await self._generate_formal_reply(
                    event, session_id, chat_history, persona_name
                )
                if formal_reply:
                    logger.info(
                        f"[CognitionCore] 有趣判定生成正式回复: {formal_reply[:30]}"
                    )
                    yield event.plain_result(formal_reply)

                    # AI 回复了，增加连续回复计数器
                    bucket_data = self.leaky_bucket.get(session_id, {})
                    if bucket_data.get("triggered", False):
                        consecutive_replies = (
                            bucket_data.get("consecutive_replies", 0) + 1
                        )
                        cooldown_messages = getattr(
                            self.plugin, "desire_cooldown_messages", 5
                        )

                        # 检查当前消息是否包含兴趣关键词
                        msg_for_check = event.message_str or ""
                        critical_pattern_check = re.compile(
                            f"({self.plugin.critical_keywords})", re.IGNORECASE
                        )
                        if critical_pattern_check.search(msg_for_check):
                            # 本条消息包含兴趣关键词，重置计数器
                            bucket_data["consecutive_replies"] = 0
                            logger.info(
                                f"[CognitionCore] 本条消息包含兴趣关键词，重置观察计数器，继续回复"
                            )
                        else:
                            bucket_data["consecutive_replies"] = consecutive_replies
                            logger.info(
                                f"[CognitionCore] AI 回复第 {consecutive_replies}/{cooldown_messages} 条"
                            )

                        if consecutive_replies >= cooldown_messages:
                            # 达到冷却消息数，进入贤者时间
                            import time

                            current_time = time.time()
                            new_urge = bucket_data.get("value", 2.0) * 0.1
                            bucket_data["value"] = new_urge
                            bucket_data["is_cooling_down"] = True
                            bucket_data["cooling_end_time"] = current_time + 60
                            bucket_data["triggered"] = False
                            bucket_data["consecutive_replies"] = 0
                            self.leaky_bucket[session_id] = bucket_data
                            logger.info(
                                f"[CognitionCore] 连续回复 {consecutive_replies} 条，进入贤者时间，欲望降至 {new_urge:.2f}"
                            )
                        else:
                            self.leaky_bucket[session_id] = bucket_data
                return
            elif boring_match:
                value = int(boring_match.group(2))
                current_threshold = session_buffer.get("threshold", 20)
                new_threshold = min(threshold_max, current_threshold + value)
                session_buffer["threshold"] = new_threshold
                await self._decrease_san(event, value)
                logger.info(
                    f"[CognitionCore] 无聊判定！SAN-{value}，阈值升至 {new_threshold}"
                )
                logger.info(f"[CognitionCore] 无聊判定，不回应。")
                return
            elif ignore_match:
                logger.info(f"[CognitionCore] 判定为忽略，不回应。")
                return
            else:
                # 无法解析，但有回复内容，视为有趣直接发送
                if reply_text and len(reply_text.strip()) > 0:
                    logger.info(f"[CognitionCore] LLM 直接回复，视为有趣")
                    yield event.plain_result(reply_text)

                    # 增加连续回复计数器
                    bucket_data = self.leaky_bucket.get(session_id, {})
                    if bucket_data.get("triggered", False):
                        consecutive_replies = (
                            bucket_data.get("consecutive_replies", 0) + 1
                        )
                        cooldown_messages = getattr(
                            self.plugin, "desire_cooldown_messages", 5
                        )

                        msg_for_check = event.message_str or ""
                        critical_pattern_check = re.compile(
                            f"({self.plugin.critical_keywords})", re.IGNORECASE
                        )
                        if critical_pattern_check.search(msg_for_check):
                            bucket_data["consecutive_replies"] = 0
                            logger.info(
                                f"[CognitionCore] 本条消息包含兴趣关键词，重置观察计数器，继续回复"
                            )
                        else:
                            bucket_data["consecutive_replies"] = consecutive_replies
                            logger.info(
                                f"[CognitionCore] AI 回复第 {consecutive_replies}/{cooldown_messages} 条"
                            )

                        if consecutive_replies >= cooldown_messages:
                            import time

                            current_time = time.time()
                            new_urge = bucket_data.get("value", 2.0) * 0.1
                            bucket_data["value"] = new_urge
                            bucket_data["is_cooling_down"] = True
                            bucket_data["cooling_end_time"] = current_time + 60
                            bucket_data["triggered"] = False
                            bucket_data["consecutive_replies"] = 0
                            self.leaky_bucket[session_id] = bucket_data
                            logger.info(
                                f"[CognitionCore] 连续回复 {consecutive_replies} 条，进入贤者时间，欲望降至 {new_urge:.2f}"
                            )
                        else:
                            self.leaky_bucket[session_id] = bucket_data
                    return
                else:
                    logger.warning(f"[CognitionCore] 无法解析 LLM 响应，已拦截")
                    return
        except Exception as e:
            logger.warning(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            try:
                self.plugin.session_manager.reset_eavesdrop_count(str(session_id))
                self.plugin.session_manager.processing_sessions.discard(str(session_id))
            except Exception as e:
                logger.warning(f"[CognitionCore] 清理 processing_sessions 失败: {e}")

    async def _decrease_san(self, event: AstrMessageEvent, value: int):
        """降低SAN精力值"""
        try:
            san = getattr(self.plugin, "san", None)
            if san and hasattr(san, "consume"):
                await san.consume(value)
                logger.info(f"[CognitionCore] 无聊判定，降低SAN: -{value}")
        except Exception as e:
            logger.warning(f"[CognitionCore] 降低SAN失败: {e}")

    async def _generate_formal_reply(
        self,
        event: AstrMessageEvent,
        session_id: str,
        chat_history: str,
        persona_name: str,
    ) -> str:
        """有趣时生成正式回复（带人设+上下文）"""
        try:
            # 获取完整人格
            persona_prompt = ""
            try:
                personality = (
                    await self.plugin.context.persona_manager.get_default_persona_v3(
                        event.unified_msg_origin
                    )
                )
                if personality:
                    persona_name = personality.get("name", persona_name)
                    persona_prompt = personality.get("prompt", "")
            except Exception:
                pass

            # 获取对话上下文
            contexts = []
            try:
                history_mgr = self.plugin.context.message_history_manager
                if history_mgr and hasattr(history_mgr, "get"):
                    hist = await history_mgr.get(event.get_group_id(), limit=10)
                    if hist:
                        contexts = hist
            except Exception:
                pass

            # 构建正式回复的prompt
            prompt_parts = []
            if persona_prompt:
                prompt_parts.append(persona_prompt)
            prompt_parts.append(f"\n对话：\n{chat_history}\n")
            prompt_parts.append(
                "你觉得这个对话很有趣，决定参与。现在该你参与互动了。"
                "请严格遵照自己的人格回复。"
            )
            formal_prompt = "".join(prompt_parts)

            llm_provider = self.plugin.context.get_using_provider(
                event.unified_msg_origin
            )
            if not llm_provider:
                return ""

            logger.info(f"[CognitionCore] 正在请求正式回复...")
            res = await llm_provider.text_chat(
                prompt=formal_prompt,
                contexts=contexts,
            )

            reply = res.completion_text.strip()
            return reply

        except Exception as e:
            logger.warning(f"[CognitionCore] 生成正式回复失败: {e}")
            return ""
