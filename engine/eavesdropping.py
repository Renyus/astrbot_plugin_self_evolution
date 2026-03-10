from astrbot.api import logger
import re
import time
import asyncio
import zlib
from collections import defaultdict
from astrbot.api.all import AstrMessageEvent


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self.global_window = defaultdict(list)
        self.window_size = 5
        self.leaky_bucket = defaultdict(dict)  # {"value": float, "last_time": float}
        self.inner_monologue_cache = defaultdict(str)
        self.boredom_cache = defaultdict(lambda: {"count": 0, "last_message_time": 0.0})

        # 漏斗机制 - 用户活跃判定
        self.active_users = defaultdict(
            dict
        )  # {group_id: {user_id: {"last_active": timestamp, "window_end": timestamp}}}
        self.active_window_seconds = 30  # 30秒活跃窗口

        # 唤醒词列表
        self.wake_names = ["黑塔", "belta", "Bot", "机器人", "小塔"]

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

        self._boredom_responses = [
            "这种毫无信息量的话题不要占用我的进程，我很忙。",
            "你们的对话让我感到困倦。有正事再说。",
            "我已经无聊到开始数像素点了。有价值的讨论再 @ 我。",
            "抱歉，我的算力是用来解决真正的问题的，不是来陪你们闲聊的。",
        ]

    def _extract_monologue(self, text: str) -> str:
        match = re.search(r"<inner_monologue>(.*?)</inner_monologue>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _store_monologue(self, session_id: str, monologue: str):
        self.inner_monologue_cache[session_id] = monologue

    def _get_stored_monologue(self, session_id: str) -> str:
        return self.inner_monologue_cache.get(session_id, "")

    def _clear_stored_monologue(self, session_id: str):
        if session_id in self.inner_monologue_cache:
            del self.inner_monologue_cache[session_id]

    def _calculate_entropy(self, text: str) -> float:
        if not text or len(text) < 10:
            return 1.0
        try:
            compressed = zlib.compress(text.encode("utf-8"))
            ratio = len(compressed) / len(text)
            return min(ratio, 1.0)
        except:
            return 0.5

    # ==================== 漏斗机制：用户活跃判定 ====================

    def _check_funnel_level1(self, event: AstrMessageEvent) -> bool:
        """第一级：框架级强特征（100%确定是互动）"""
        msg = event.message_obj

        # 1.1 @ 提及检测
        if event.is_at_or_wake_command:
            return True

        # 1.2 命令前缀检测
        msg_text = event.message_str or ""
        config = self.plugin.context.get_config()
        prefixes = config.get("wake_prefix", ["/"])
        prov_prefix = config.get("provider_settings", {}).get("wake_prefix", "/")
        prefixes = list(prefixes) + [prov_prefix]
        if any(msg_text.startswith(p) for p in prefixes if p):
            return True

        # 1.3 引用回复检测（检查回复的是否是Bot的消息）
        if hasattr(msg, "reply") and msg.reply:
            reply_msg = msg.reply
            if hasattr(reply_msg, "sender") and reply_msg.sender:
                bot_id = str(self.plugin.context.bot_info.get("user_id", ""))
                if str(reply_msg.sender) == bot_id:
                    return True

        return False

    def _check_funnel_level2(self, event: AstrMessageEvent) -> bool:
        """第二级：唤醒词与正则匹配（软特征）"""
        msg_text = (event.message_str or "").lower()

        # 2.1 名称唤醒
        for name in self.wake_names:
            if name.lower() in msg_text:
                return True

        # 2.2 强AI意图句式
        for pattern in self.ai_intent_patterns:
            if re.search(pattern, msg_text):
                return True

        return False

    def _check_funnel_level3(self, group_id: str, user_id: str) -> bool:
        """第三级：上下文时间窗（Session机制）"""
        if group_id not in self.active_users:
            return False
        if user_id not in self.active_users[group_id]:
            return False

        window_end = self.active_users[group_id][user_id].get("window_end", 0)
        return time.time() < window_end

    def _mark_user_active(self, group_id: str, user_id: str):
        """标记用户为活跃状态，开启30秒窗口"""
        now = time.time()
        self.active_users[group_id][user_id] = {
            "last_active": now,
            "window_end": now + self.active_window_seconds,
        }

    def is_user_active(self, group_id: str, user_id: str) -> bool:
        """检查用户是否处于活跃状态"""
        return self._check_funnel_level3(group_id, user_id)

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
            "enabled": getattr(self.plugin, "boredom_enabled", True),
            "threshold": getattr(self.plugin, "boredom_threshold", 0.6),
            "consecutive_count": getattr(self.plugin, "boredom_consecutive_count", 5),
            "sarcastic_reply": getattr(self.plugin, "boredom_sarcastic_reply", True),
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
        if entropy > params["threshold"]:
            boredom["count"] += 1
        else:
            boredom["count"] = max(0, boredom["count"] - 1)
        is_bored = boredom["count"] >= params["consecutive_count"]
        logger.debug(
            f"[Boredom] Group {group_id}: entropy={entropy:.2f}, count={boredom['count']}, is_bored={is_bored}"
        )
        return is_bored

    def _get_boredom_reply(self) -> str:
        import random

        return random.choice(self._boredom_responses)

    def _get_leaky_params(self):
        return {
            "enabled": getattr(self.plugin, "leaky_integrator_enabled", True),
            "decay": getattr(self.plugin, "leaky_decay_factor", 0.9),
            "threshold": getattr(self.plugin, "leaky_trigger_threshold", 4.0),
            "interest_boost": getattr(self.plugin, "interest_boost", 2.0),
            "daily_boost": getattr(self.plugin, "daily_chat_boost", 0.2),
        }

    def _calculate_boost(self, msg_text: str) -> float:
        params = self._get_leaky_params()

        critical_keywords = getattr(self.plugin, "critical_keywords", "")
        if critical_keywords:
            try:
                pattern = re.compile(f"({critical_keywords})", re.IGNORECASE)
                if pattern.search(msg_text):
                    return params["interest_boost"]
            except:
                pass

        return params["daily_boost"]

    async def handle_message(self, event: AstrMessageEvent):
        msg_text = event.message_str
        session_id = str(event.session_id)
        user_id = str(event.get_sender_id())
        sender_name = event.get_sender_name() or "Unknown"
        is_at = event.is_at_or_wake_command

        group_id = event.get_group_id()
        if not group_id:
            return

        group_id = str(group_id)

        # 漏斗机制：检测用户是否活跃
        level1_triggered = self._check_funnel_level1(event)
        level2_triggered = self._check_funnel_level2(event)

        if level1_triggered or level2_triggered:
            self._mark_user_active(group_id, user_id)
            logger.debug(
                f"[漏斗] 用户 {user_id} 在群 {group_id} 被标记为活跃 (L1={level1_triggered}, L2={level2_triggered})"
            )

        # 清理过期的活跃用户（每100条消息清理一次）
        if hasattr(self, "_msg_counter"):
            self._msg_counter += 1
        else:
            self._msg_counter = 1
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
            self.global_window[group_id].append(f"{sender_name}: {msg_text}")
            if len(self.global_window[group_id]) > self.window_size:
                self.global_window[group_id].pop(0)

        entropy = self._calculate_entropy(msg_text)
        boredom_params = self._get_boredom_params()
        if boredom_params["enabled"] and self._update_boredom(group_id, entropy):
            if is_at and boredom_params["sarcastic_reply"]:
                boredom_reply = self._get_boredom_reply()
                logger.info(f"[Boredom] Group {group_id} 触发傲慢回复: {boredom_reply}")
                yield event.plain_result(boredom_reply)
                return
            elif not is_at:
                logger.debug(f"[Boredom] Group {group_id} 无聊中，跳过插嘴")
                return

        critical_pattern = re.compile(
            f"({self.plugin.critical_keywords})", re.IGNORECASE
        )
        if critical_pattern.search(msg_text):
            logger.info(f"[CognitionCore] 预扫描命中关键词，强制立即触发评估。")
            async for result in self._evaluate_interjection(
                event, session_id, force_immediate=True
            ):
                yield result
            return

        # 前置低算力拦截：快速过滤明显无需介入的情况
        if not is_at and len(msg_text) < 6:
            logger.debug(f"[CognitionCore] 消息过短，跳过评估: {msg_text[:10]}")
            return

        # 高信息熵直接跳过（全是重复字符/表情）
        if entropy > 0.95 and not is_at:
            logger.debug(f"[CognitionCore] 信息熵过高，跳过: {msg_text[:10]}")
            return

        if is_at:
            async for result in self._evaluate_interjection(event, session_id):
                yield result
            return

        params = self._get_leaky_params()

        if params["enabled"]:
            boost = self._calculate_boost(msg_text)

            import time
            import math

            current_time = time.time()
            bucket_data = self.leaky_bucket.get(session_id)
            if not isinstance(bucket_data, dict):
                bucket_data = {"value": 0.0, "last_time": current_time}

            last_time = bucket_data.get("last_time", current_time)
            delta_t = current_time - last_time

            decay_factor = params.get("decay_exp", 0.5)
            exp_decay = math.exp(-decay_factor * delta_t / 60)

            old_value = float(bucket_data.get("value", 0))
            new_value = old_value * exp_decay + boost

            self.leaky_bucket[session_id] = {
                "value": new_value,
                "last_time": current_time,
            }

            current_z = new_value

            if current_z >= params["threshold"]:
                logger.info(
                    f"[CognitionCore] 泄漏积分器触发! Z={current_z:.2f} >= {params['threshold']}"
                )
                async for result in self._evaluate_interjection(event, session_id):
                    yield result
                self.leaky_bucket[session_id] = {
                    "value": 0.0,
                    "last_time": current_time,
                }
        else:
            if session_id not in self.plugin.active_buffers:
                self.plugin.active_buffers[session_id] = []
                self.plugin._session_speakers = getattr(
                    self.plugin, "_session_speakers", {}
                )
                if session_id not in self.plugin._session_speakers:
                    self.plugin._session_speakers[session_id] = {}

            speaker_map = self.plugin._session_speakers[session_id]
            if user_id not in speaker_map:
                speaker_map[user_id] = len(speaker_map) + 1
            speaker_num = speaker_map[user_id]

            self.plugin.active_buffers[session_id].append(
                f"[群成员{speaker_num}]{sender_name}({user_id}): {msg_text}"
            )

            if (
                len(self.plugin.active_buffers[session_id])
                > self.plugin.max_buffer_size
            ):
                self.plugin.active_buffers[session_id].pop(0)

            if (
                len(self.plugin.active_buffers[session_id])
                >= self.plugin.buffer_threshold
                and session_id not in self.plugin.processing_sessions
            ):
                async for result in self._evaluate_interjection(event, session_id):
                    yield result

    async def _evaluate_interjection(
        self, event: AstrMessageEvent, session_id: str, force_immediate: bool = False
    ):
        """插嘴评估层：简化逻辑，只做关键词预过滤"""
        if session_id in self.plugin.processing_sessions:
            return

        self.plugin.processing_sessions.add(session_id)
        try:
            buffer = self.plugin.active_buffers.get(session_id, [])

            snap_len = 0
            sender_name = event.get_sender_name() or "Unknown"
            sender_id = str(event.get_sender_id())
            speaker_map = getattr(self.plugin, "_session_speakers", {}).get(
                session_id, {}
            )
            if sender_id not in speaker_map:
                speaker_map[sender_id] = len(speaker_map) + 1
            speaker_num = speaker_map[sender_id]

            if force_immediate:
                chat_history = f"[群成员{speaker_num}]{sender_name}({sender_id}): {event.message_str}"
            else:
                snap_len = len(buffer)
                chat_history = "\n".join(buffer[:snap_len])

            inner_monologue_enabled = getattr(
                self.plugin, "inner_monologue_enabled", True
            )

            monologue_instruction = ""
            if inner_monologue_enabled:
                monologue_instruction = (
                    "\n\n【潜意识任务】（即使判定为 IGNORE 也必须执行）：\n"
                    "请输出一个 20 字以内的简短内心独白，描述你对这个对话片段的真实腹诽或吐槽。\n"
                    "格式：<inner_monologue>你的内心独白</inner_monologue>\n"
                    "示例：<inner_monologue>这帮人又在聊毫无营养的八卦</inner_monologue>"
                )

            decision_prompt = (
                f"你现在是 {self.plugin.persona_name}（{self.plugin.persona_title}），特点是：{self.plugin.persona_style}。\n"
                f'【当前社交阈值】：你的"发言意愿"设定为 {self.plugin.interjection_desire}/10。数值越低你越冷漠。\n'
                "【后台监控任务】：评估以下实时对话片段，决定是否需要以你的身份进行[即时干预]。\n\n"
                f"--- 监控片段 ---\n{chat_history}\n----------------\n\n"
                "【严格执行指令】：\n"
                "1. **静默判定 [IGNORE]**：如果满足以下任一条件，必须仅回复 [IGNORE]：\n"
                f"   - 话题的重要性、趣味性或技术价值评分低于你的发言意愿阈值 ({self.plugin.interjection_desire}/10)。\n"
                "   - 对话内容为简单的表情、无意义的语气词、或低信息量的日常寒暄。\n"
                "2. **干预判定 [COMMENT]**：唯有满足以下任一条件，方可输出你的简练评论：\n"
                "   - 话题触及你的核心关键词。\n"
                "   - 对方在发表明显的逻辑谬误或常识性错误。\n"
                f"3. **表达风格**：回复必须极度简略（通常不超过 20 字），语气要冷淡且专业，像真正的 {self.plugin.persona_name} 一样。\n"
                '【禁止事项】：绝对禁止发表类似"对话缺乏信息密度"、"建议继续检测"等关于后台评估过程本身的任何评论。'
                + monologue_instruction
            )

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
                contexts=[],
                system_prompt=getattr(
                    self.plugin,
                    "prompt_eavesdrop_system",
                    "你处于后台冷启动决策模式。如果不值得开口，请务必回复 IGNORE。",
                ),
            )

            reply_text = res.completion_text.strip()

            meta_indicators = [
                "监测",
                "监控",
                "信息密度",
                "忽略协议",
                "评估结果",
                "当前对话",
                "冗余",
                "标注",
                "发现值得",
                "数据片段",
            ]
            is_meta = (
                any(indicator in reply_text for indicator in meta_indicators)
                and len(reply_text) > 10
            )

            should_respond = False
            monologue_text = ""
            if reply_text:
                reply_stripped = reply_text.strip().upper()
                if (
                    "[IGNORE]" in reply_text.upper()
                    or "IGNORE" in reply_stripped
                    or reply_stripped == "IGNORE"
                ):
                    reason = "判定为噪音/无价值"
                    monologue_text = self._extract_monologue(reply_text)
                elif is_meta:
                    reason = "触发元评论拦截"
                else:
                    should_respond = True
                    reason = "评估通过"
                    monologue_text = self._extract_monologue(reply_text)
            else:
                reason = "内容为空"

            if should_respond:
                inner_monologue = self._get_stored_monologue(session_id)
                if inner_monologue:
                    full_response = f"{inner_monologue} {reply_text}"
                    logger.info(
                        f"[CognitionCore] 插嘴评估通过！注入内心独白: {inner_monologue}"
                    )
                    yield event.plain_result(full_response)
                else:
                    logger.info(f"[CognitionCore] 插嘴评估通过！响应: {reply_text}")
                    yield event.plain_result(reply_text)
                self._clear_stored_monologue(session_id)
            else:
                if monologue_text:
                    self._store_monologue(session_id, monologue_text)
                    logger.info(f"[CognitionCore] 已存储内心独白: {monologue_text}")
                logger.info(f"[CognitionCore] 插嘴评估未通过：{reason}。")

            if not force_immediate:
                self.plugin.active_buffers[session_id] = self.plugin.active_buffers[
                    session_id
                ][snap_len:]
        except Exception as e:
            if "安全检查" in str(e) or "Safety" in str(e):
                logger.warning(f"[CognitionCore] 插嘴评估被服务商安全策略拦截。")
            else:
                logger.warning(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            try:
                self.plugin.processing_sessions.discard(str(session_id))
            except Exception as e:
                logger.warning(f"[CognitionCore] 清理 processing_sessions 失败: {e}")
