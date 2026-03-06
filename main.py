from astrbot.api.all import *
from astrbot.api.provider import ProviderRequest
import json
import logging

@register("astrbot_plugin_self_evolution", "自我进化 (Self-Evolution)", "让大模型具备自我迭代、记忆沉淀和人格进化能力的插件。", "1.0.0")
class SelfEvolutionPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.review_mode = self.config.get("review_mode", True)
        self.memory_kb_name = self.config.get("memory_kb_name", "self_evolution_memory")

    @llm_tool(name="evolve_persona")
    async def evolve_persona(self, event: AstrMessageEvent, new_system_prompt: str, reason: str):
        """
        当你认为需要调整自己的语言风格、行为准则或遵循用户的改进建议时，调用此工具来修改你的系统提示词（Persona）。
        :param new_system_prompt: 新的完整系统提示词（System Prompt）。
        :param reason: 为什么要进行这次进化（理由）。
        """
        try:
            # 获取当前的 Persona ID
            # 注意：这里假设用户正在使用某种人格，如果没有，可能需要处理默认情况
            curr_persona_id = event.persona_id
            if not curr_persona_id or curr_persona_id == "default":
                # 如果是默认人格，尝试获取当前活跃的人格名
                # 简单处理：如果没写进 DB，可能无法直接 update
                # 但 AstrBot 通常会有选中的 persona
                pass
            
            if self.review_mode:
                logger.info(f"[SelfEvolution] 收到进化请求。原因: {reason}")
                logger.info(f"[SelfEvolution] 拟修改 Prompt 为: {new_system_prompt[:50]}...")
                return f"进化请求已提交，等待管理员审核。进化理由：{reason}"
            
            # 执行更新
            await self.context.persona_manager.update_persona(
                persona_id=curr_persona_id,
                system_prompt=new_system_prompt
            )
            
            logger.info(f"[SelfEvolution] 人格进化成功！Persona: {curr_persona_id}, 原因: {reason}")
            return f"进化成功！我已经更新了我的核心预设。进化理由：{reason}"
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 进化失败: {str(e)}")
            return f"进化过程中出现错误: {str(e)}"

    @llm_tool(name="commit_to_memory")
    async def commit_to_memory(self, event: AstrMessageEvent, fact: str):
        """
        当你发现了一些关于用户的重要的、需要永久记住的事实时，调用此工具将该事实存入你的长期记忆库。
        :param fact: 需要记住的具体事实或信息。
        """
        try:
            kb_manager = self.context.kb_manager
            kb_helper = await kb_manager.get_kb_by_name(self.memory_kb_name)
            
            if not kb_helper:
                # 尝试自动创建一个简单的知识库（如果权限允许）
                # 这里可能需要更多的参数如 embedding_provider_id
                # 暂时报错提醒用户手动创建
                return f"未找到名为 {self.memory_kb_name} 的记忆知识库，请先在后台手动创建它。"

            # 存入记忆
            await kb_helper.upload_document(
                file_name=f"memory_{json.dumps(event.message_obj.timestamp)}.txt",
                file_content=None,
                file_type="txt",
                pre_chunked_text=[fact]
            )
            
            logger.info(f"[SelfEvolution] 成功存入一条长期记忆: {fact[:30]}...")
            return "事实已成功存入长期记忆库，我以后会记得这件事的。"
            
        except Exception as e:
            logger.error(f"[SelfEvolution] 存入记忆失败: {str(e)}")
            return f"存入记忆时出错: {str(e)}"
