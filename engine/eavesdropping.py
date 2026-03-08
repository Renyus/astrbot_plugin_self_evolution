from astrbot.api import logger
import re
from astrbot.api.all import AstrMessageEvent

class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_message(self, event: AstrMessageEvent):
        """CognitionCore 4.5: 意图预扫描 (Intent Pre-scan) 拦截器"""
        msg_text = event.message_str
        is_at = event.is_at_or_wake_command
        
        if is_at:
            return
            
        session_id = event.session_id
        user_id = event.get_sender_id()
        score = await self.plugin.dao.get_affinity(user_id)
        
        if score <= 0: return 

        # --- 意图预扫描层 (Intent Pre-scan) ---
        # 从配置中动态编译关键词正则
        critical_pattern = re.compile(f"({self.plugin.critical_keywords})", re.IGNORECASE)
        if critical_pattern.search(msg_text):
            logger.info(f"[CognitionCore] 预扫描命中词库: '{self.plugin.critical_keywords}'，强制立即触发评估。")
            async for result in self._evaluate_interjection(event, session_id, force_immediate=True):
                yield result
            return # 命中后直接处理，不再重复进入普通缓冲逻辑

        # --- 缓冲池逻辑 ---
        if session_id not in self.plugin.active_buffers:
            self.plugin.active_buffers[session_id] = []
        
        sender_name = event.get_sender_name() or "Unknown"
        self.plugin.active_buffers[session_id].append(f"{sender_name}({user_id}): {msg_text}")
        
        if len(self.plugin.active_buffers[session_id]) > self.plugin.max_buffer_size:
            self.plugin.active_buffers[session_id].pop(0)
            
        if len(self.plugin.active_buffers[session_id]) >= self.plugin.buffer_threshold and session_id not in self.plugin.processing_sessions:
            async for result in self._evaluate_interjection(event, session_id):
                yield result

    async def _evaluate_interjection(self, event: AstrMessageEvent, session_id: str, force_immediate: bool = False):
        """插嘴评估层：增加强制立即评估逻辑，并保留安全风控加固"""
        if session_id in self.plugin.processing_sessions:
            return
            
        self.plugin.processing_sessions.add(session_id)
        try:
            buffer = self.plugin.active_buffers.get(session_id, [])
            
            # 如果是强制立即评估，优先针对当前单条消息
            if force_immediate:
                chat_history = f"{event.get_sender_name()}({event.get_sender_id()}): {event.message_str}"
            else:
                snap_len = len(buffer)
                chat_history = "\n".join(buffer[:snap_len])
            
            # 使用动态的人设配置构建决策指令 (CognitionCore 5.0)
            decision_prompt = (
                f"你现在是 {self.plugin.persona_name}（{self.plugin.persona_title}），特点是：{self.plugin.persona_style}。\n"
                "【后台监控任务】：评估以下实时对话片段，决定是否需要以你的身份进行[即时干预]。\n\n"
                f"--- 监控片段 ---\n{chat_history}\n----------------\n\n"
                "【严格执行指令】：\n"
                "1. **静默判定 [IGNORE]**：如果满足以下任一条件，必须仅回复 [IGNORE]：\n"
                "   - 对话内容为简单的表情、无意义的语气词、或低信息量的日常寒暄（如：在吗、哈哈、吃饭了吗）。\n"
                "   - 用户之间在进行与你无关的死循环讨论或纯粹的情绪发泄。\n"
                "   - 缺乏深度、逻辑或值得你这种身份（智商超群）介入的技术点/趣点。\n"
                "2. **干预判定 [COMMENT]**：唯有满足以下任一条件，方可输出你的简练评论：\n"
                "   - 话题触及你的核心关键词（如：模拟宇宙、技术原理、空间站管理、或特定的研究话题）。\n"
                "   - 对方在发表明显的逻辑谬误或常识性错误，让你感到不屑并想纠正。\n"
                "   - 对话中出现了让你觉得真正“有趣”或具备“研究价值”的信息流。\n"
                f"3. **表达风格**：回复必须极度简略（通常不超过 20 字），语气要冷淡且专业，像真正的 {self.plugin.persona_name} 一样。\n"
                "【禁止事项】：**绝对禁止**发表类似“对话缺乏信息密度”、“建议继续检测”、“监控显示...”等关于后台评估过程本身的任何评论。你的回复是发给群聊中用户的，而不是给系统的报告。如果你认为话题无聊，必须直接回复 [IGNORE]。"
            )
            
            llm_provider = self.plugin.context.get_using_provider(event.unified_msg_origin)
            if not llm_provider: return
            
            logger.info(f"[CognitionCore] 正在请求 LLM 决策自省... Prompt长度: {len(decision_prompt)}")
            res = await llm_provider.text_chat(
                prompt=decision_prompt,
                contexts=[], # 不带长期记忆以减少消耗
                system_prompt=(
                    f"你处于后台冷启动决策模式。你的人设是 {self.plugin.persona_name}。"
                    "你对浪费算力的废话极度反感。如果不值得开口，请务必回复 [IGNORE]。"
                )
            )
            
            reply_text = res.completion_text.strip()
            
            # 增加元评论硬过滤防线：防止 LLM 以“监测报告”形式回复
            meta_indicators = ["监测", "监控", "信息密度", "忽略协议", "评估结果", "当前对话"]
            is_meta = any(indicator in reply_text for indicator in meta_indicators) and len(reply_text) > 15
            
            if reply_text and "[IGNORE]" not in reply_text and not is_meta:
                logger.info(f"[CognitionCore] 插嘴评估通过！响应: {reply_text}")
                yield event.plain_result(reply_text)
            else:
                reason = "判定为噪音/无价值" if "[IGNORE]" in reply_text else "触发元评论拦截" if is_meta else "内容为空"
                logger.info(f"[CognitionCore] 插嘴评估未通过：{reason}。")
                
            # 非强制模式下才清空缓冲切片
            if not force_immediate:
                self.plugin.active_buffers[session_id] = self.plugin.active_buffers[session_id][snap_len:]
        except Exception as e:
            if "安全检查" in str(e) or "Safety" in str(e):
                logger.warning(f"[CognitionCore] 插嘴评估被服务商安全策略拦截 (可能是消息包含敏感词)。已自动忽略。")
            else:
                logger.error(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            self.plugin.processing_sessions.discard(session_id)
