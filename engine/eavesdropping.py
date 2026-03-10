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
        self.leaky_bucket = defaultdict(float)
        self.inner_monologue_cache = defaultdict(str)

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

        if is_at:
            async for result in self._evaluate_interjection(event, session_id):
                yield result
            return

        params = self._get_leaky_params()

        if params["enabled"]:
            boost = self._calculate_boost(msg_text)
            self.leaky_bucket[session_id] = (
                self.leaky_bucket[session_id] * params["decay"] + boost
            )

            current_z = self.leaky_bucket[session_id]

            if current_z >= params["threshold"]:
                logger.info(
                    f"[CognitionCore] 泄漏积分器触发! Z={current_z:.2f} >= {params['threshold']}"
                )
                async for result in self._evaluate_interjection(event, session_id):
                    yield result
                self.leaky_bucket[session_id] = 0
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
                if "[IGNORE]" in reply_text.upper() or reply_stripped == "IGNORE":
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
            self.plugin.processing_sessions.discard(session_id)
