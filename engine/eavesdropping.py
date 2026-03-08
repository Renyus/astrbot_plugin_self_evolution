import logging
import re
from astrbot.api.all import AstrMessageEvent

logger = logging.getLogger("astrbot")

class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_message(self, event: AstrMessageEvent):
        """CognitionCore 4.5: 意图预扫描 (Intent Pre-scan) 拦截器"""
        if event.is_at_or_wake_command: return
        
        msg_text = event.message_str
        session_id = event.session_id
        user_id = event.get_sender_id()
        score = await self.plugin.dao.get_affinity(user_id)
        
        if score <= 0: return 

        # --- 意图预扫描层 (Intent Pre-scan) ---
        # 从配置中动态编译关键词正则
        critical_pattern = re.compile(f"({self.plugin.critical_keywords})", re.IGNORECASE)
        if critical_pattern.search(msg_text):
            logger.debug(f"[CognitionCore] 预扫描命中：消息包含本体属性关键词，立即触发评估。")
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
        self.plugin.processing_sessions.add(session_id)
        try:
            buffer = self.plugin.active_buffers.get(session_id, [])
            
            # 如果是强制立即评估，优先针对当前单条消息
            if force_immediate:
                chat_history = f"{event.get_sender_name()}({event.get_sender_id()}): {event.message_str}"
            else:
                snap_len = len(buffer)
                chat_history = "\n".join(buffer[:snap_len])
            
            # 保留 v3.1.6 的安全加固提示词
            decision_prompt = (
                f"你现在是黑塔（人偶负责人）。以下是实时群聊监控片段：\n\n{chat_history}\n\n"
                "【执行指令】：作为高维观察者，由于当前属于监听模式，请评估是否有必要介入？\n"
                "1. 如果属于无意义的闲聊、重复信息或与你无关，请务必回复：[IGNORE]\n"
                "2. 如果发现具备深度讨论潜力的技术话题、错误信息或值得互动的有趣节点，请直接输出你的简练评论。语气保持理性、犀利且专业。禁止使用括号说明动作描述。"
            )
            
            llm_provider = self.plugin.context.get_using_provider(event.unified_msg_origin)
            if not llm_provider: return
            
            res = await llm_provider.text_chat(
                prompt=decision_prompt,
                contexts=[], # 不带长期记忆以减少消耗
                system_prompt="你现在在进行后台数据自省与决策评估。请严格遵守 [IGNORE] 输出协议。"
            )
            
            reply_text = res.completion_text.strip()
            
            if reply_text and "[IGNORE]" not in reply_text:
                logger.info(f"[CognitionCore] 主动插嘴触发！响应: {reply_text}")
                yield event.plain_result(reply_text)
            else:
                logger.debug(f"[CognitionCore] 插嘴评估完毕：判断为无需介入。")
                
            # 非强制模式下才清空缓冲切片
            if not force_immediate:
                self.plugin.active_buffers[session_id] = self.plugin.active_buffers[session_id][snap_len:]
        except Exception as e:
            if "安全检查" in str(e) or "Safety" in str(e):
                logger.warning(f"[CognitionCore] 插嘴评估被服务商安全策略拦截 (可能是消息包含敏感词)。已自动忽略。")
            else:
                logger.error(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            self.plugin.processing_sessions.remove(session_id)
