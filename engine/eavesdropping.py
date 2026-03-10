from astrbot.api import logger
import re
import time
import asyncio
from astrbot.api.all import AstrMessageEvent


class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_message(self, event: AstrMessageEvent):
        """CognitionCore 4.5: 意图预扫描 (Intent Pre-scan) 拦截器"""
        msg_text = event.message_str
        session_id = event.session_id
        user_id = event.get_sender_id()
        sender_name = event.get_sender_name() or "Unknown"
        is_at = event.is_at_or_wake_command

        # 获取命令前缀配置，检查是否为命令消息
        config = self.plugin.context.get_config()
        bot_wake_prefixes = config.get("wake_prefix", ["/"])
        prov_wake_prefix = config.get("provider_settings", {}).get("wake_prefix", "/")

        # 合并所有命令前缀
        all_prefixes = set(bot_wake_prefixes)
        if prov_wake_prefix:
            all_prefixes.add(prov_wake_prefix)

        # 检查是否为命令消息，如果是则跳过处理
        if any(msg_text.startswith(prefix) for prefix in all_prefixes):
            return

        score = await self.plugin.dao.get_affinity(user_id)

        if score <= 0:
            return

        # --- 触发条件 A：兴趣关键词命中 ---
        critical_pattern = re.compile(
            f"({self.plugin.critical_keywords})", re.IGNORECASE
        )
        if critical_pattern.search(msg_text):
            logger.info(
                f"[CognitionCore] 预扫描命中词库: '{self.plugin.critical_keywords}'，强制立即触发评估。"
            )
            async for result in self._evaluate_interjection(
                event, session_id, force_immediate=True
            ):
                yield result
            return

        # --- 触发条件 B：用户 @ 机器人 ---
        if is_at:
            async for result in self._evaluate_interjection(event, session_id):
                yield result
            return

        # --- 原有缓冲池逻辑 ---
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

        if len(self.plugin.active_buffers[session_id]) > self.plugin.max_buffer_size:
            self.plugin.active_buffers[session_id].pop(0)

        if (
            len(self.plugin.active_buffers[session_id]) >= self.plugin.buffer_threshold
            and session_id not in self.plugin.processing_sessions
        ):
            async for result in self._evaluate_interjection(event, session_id):
                yield result

    def _is_relevant_reply(self, msg_text: str, messages: list) -> bool:
        """判断消息是否与当前话题相关"""
        # 简单关键词判断：是否包含问号、或者与之前消息有词汇重叠
        if "？" in msg_text or "?" in msg_text:
            return True

        # 检查是否回复了机器人（通过关键词检测）
        robot_name = getattr(self.plugin, "persona_name", "黑塔")
        if robot_name in msg_text:
            return True

        # 检查是否与最近消息有词汇重叠（用词集而非字符集）
        if messages:
            import re

            # 分词：提取连续的中文词或英文单词
            def extract_words(text):
                chinese = re.findall(r"[\u4e00-\u9fff]+", text)
                english = re.findall(r"[a-zA-Z]+", text)
                chinese_words = [w for w in chinese if len(w) >= 2]  # 至少2个汉字
                english_words = [
                    w.lower() for w in english if len(w) >= 3
                ]  # 至少3个字母
                return set(chinese_words + english_words)

            recent_words = extract_words(messages[-1].get("content", ""))
            current_words = extract_words(msg_text)
            overlap = recent_words & current_words
            if len(overlap) >= 2:  # 至少2个词重叠才算相关
                return True

        return False

    async def _evaluate_interjection(
        self, event: AstrMessageEvent, session_id: str, force_immediate: bool = False
    ):
        """插嘴评估层：增加强制立即评估逻辑，并保留安全风控加固"""
        if session_id in self.plugin.processing_sessions:
            return

        self.plugin.processing_sessions.add(session_id)
        try:
            buffer = self.plugin.active_buffers.get(session_id, [])

            # 如果是强制立即评估，优先针对当前单条消息
            snap_len = 0
            sender_name = event.get_sender_name() or "Unknown"
            sender_id = event.get_sender_id()
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

            # 使用动态的人设配置构建决策指令 (CognitionCore 5.5)
            decision_prompt = (
                f"你现在是 {self.plugin.persona_name}（{self.plugin.persona_title}），特点是：{self.plugin.persona_style}。\n"
                f'【当前社交阈值】：你的"发言意愿"设定为 {self.plugin.interjection_desire}/10。数值越低你越冷漠，只有越重要的话题才值得你开口。\n'
                "【后台监控任务】：评估以下实时对话片段，决定是否需要以你的身份进行[即时干预]。\n\n"
                f"--- 监控片段 ---\n{chat_history}\n----------------\n\n"
                "【严格执行指令】：\n"
                "1. **静默判定 [IGNORE]**：如果满足以下任一条件，必须仅回复 [IGNORE]：\n"
                f"   - 话题的重要性、趣味性或技术价值评分低于你的发言意愿阈值 ({self.plugin.interjection_desire}/10)。\n"
                "   - 对话内容为简单的表情、无意义的语气词、或低信息量的日常寒暄（如：在吗、哈哈、吃饭了吗）。\n"
                "   - 用户之间在进行与你无关的死循环讨论或纯粹的情绪发泄。\n"
                "2. **干预判定 [COMMENT]**：唯有满足以下任一条件，方可输出你的简练评论：\n"
                "   - 话题触及你的核心关键词（如：模拟宇宙、技术原理、空间站管理、或特定的研究话题）。\n"
                "   - 对方在发表明显的逻辑谬误或常识性错误，让你感到不屑并想纠正。\n"
                '   - 对话中出现了让你觉得真正"有趣"或具备"研究价值"的信息流。\n'
                f"3. **表达风格**：回复必须极度简略（通常不超过 20 字），语气要冷淡且专业，像真正的 {self.plugin.persona_name} 一样。\n"
                '【禁止事项】：**绝对禁止**发表类似"对话缺乏信息密度"、"建议继续检测"、"监控显示..."等关于后台评估过程本身的任何评论。你的回复是发给群聊中用户的，而不是给系统的报告。如果你认为话题无聊，必须直接回复 [IGNORE]。'
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
                contexts=[],  # 不带长期记忆以减少消耗
                system_prompt=(
                    f"你处于后台冷启动决策模式。你的人设是 {self.plugin.persona_name}。"
                    "你对浪费算力的废话极度反感。如果不值得开口，请务必回复 [IGNORE]。"
                ),
            )

            reply_text = res.completion_text.strip()

            # 增加元评论硬过滤防线：防止 LLM 以"监测报告"形式回复
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

            if reply_text and "[IGNORE]" not in reply_text and not is_meta:
                logger.info(f"[CognitionCore] 插嘴评估通过！响应: {reply_text}")
                yield event.plain_result(reply_text)
            else:
                reason = (
                    "判定为噪音/无价值"
                    if "[IGNORE]" in reply_text
                    else "触发元评论拦截"
                    if is_meta
                    else "内容为空"
                )
                logger.info(f"[CognitionCore] 插嘴评估未通过：{reason}。")

            # 非强制模式下才清空缓冲切片
            if not force_immediate:
                self.plugin.active_buffers[session_id] = self.plugin.active_buffers[
                    session_id
                ][snap_len:]
        except Exception as e:
            if "安全检查" in str(e) or "Safety" in str(e):
                logger.warning(
                    f"[CognitionCore] 插嘴评估被服务商安全策略拦截 (可能是消息包含敏感词)。已自动忽略。"
                )
            else:
                logger.error(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            self.plugin.processing_sessions.discard(session_id)
