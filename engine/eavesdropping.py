import logging
from astrbot.api.all import AstrMessageEvent

logger = logging.getLogger("astrbot")

class EavesdroppingEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_message(self, event: AstrMessageEvent):
        """CognitionCore 3.0: 全环境被动监听与插嘴决策"""
        # 排除黑名单、艾特机器人的消息（艾特已由常规流程处理）
        if event.is_at_or_wake_command: return
        
        session_id = event.session_id
        user_id = event.get_sender_id()
        score = await self.plugin.dao.get_affinity(user_id)
        
        if score <= 0: return # 忽略黑名单用户的闲聊
        
        # 维护缓冲池
        if session_id not in self.plugin.active_buffers:
            self.plugin.active_buffers[session_id] = []
        
        msg_text = event.message_str
        sender_name = event.get_sender_name() or "Unknown"
        self.plugin.active_buffers[session_id].append(f"{sender_name}({user_id}): {msg_text}")
        
        # 防止溢出
        if len(self.plugin.active_buffers[session_id]) > self.plugin.max_buffer_size:
            self.plugin.active_buffers[session_id].pop(0)
            
        # 触发评估决策
        if len(self.plugin.active_buffers[session_id]) >= self.plugin.buffer_threshold and session_id not in self.plugin.processing_sessions:
            # 必须用 async for 迭代异步生成器，以便将其 Yield 传递给请求流水线
            async for result in self._evaluate_interjection(event, session_id):
                yield result

    async def _evaluate_interjection(self, event: AstrMessageEvent, session_id: str):
        """插嘴评估层：决定是否发言"""
        self.plugin.processing_sessions.add(session_id)
        try:
            buffer = self.plugin.active_buffers[session_id]
            # 获取当前快照的快照长度
            snap_len = len(buffer)
            chat_history = "\n".join(buffer[:snap_len])
            
            # 构建决策指令
            decision_prompt = (
                f"你现在是黑塔的人偶辅助AI。以下是当前群聊的实时闲聊片段：\n\n{chat_history}\n\n"
                "【决策指令】：这些人类正在聊什么？是否有嘲讽价值、技术价值或插嘴必要？\n"
                "1. 如果话题无聊、与你无关或不值得回应，请务必仅回复：[IGNORE]\n"
                "2. 如果决定插嘴（嘲讽、指正或附和），请直接输出插嘴的内容。语气保持冷淡、高效且略带嘲讽。禁止在回复中使用括号说明动作。"
            )
            
            # 调用底层 LLM 接口
            
            # 由于此处是后台静默请求，需要构造一个不触发前台回复的请求
            llm_provider = self.plugin.context.get_using_provider(event.unified_msg_origin)
            if not llm_provider: return
            
            res = await llm_provider.text_chat(
                prompt=decision_prompt,
                contexts=[], # 不带长期记忆以减少消耗
                system_prompt="你现在在进行后台思考，必须严格遵守指令。"
            )
            
            reply_text = res.completion_text.strip()
            
            # 只有当模型输出不是 IGNORE 时才真实下发到聊天框
            if reply_text and "[IGNORE]" not in reply_text:
                logger.info(f"[CognitionCore] 主动插嘴触发！响应: {reply_text}")
                yield event.plain_result(reply_text)
            else:
                logger.debug(f"[CognitionCore] 插嘴评估完毕：判断为无聊水群，选择潜水。")
                
            # 发言或评估后清空缓冲，【仅清空已处理的消息切片】，保留处理期间新产生的消息
            self.plugin.active_buffers[session_id] = self.plugin.active_buffers[session_id][snap_len:]
        except Exception as e:
            logger.error(f"[CognitionCore] 插嘴评估过程发生异常: {e}")
        finally:
            self.plugin.processing_sessions.remove(session_id)
