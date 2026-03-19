"""
反思模块 - 单会话内省 & 每日批处理
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("astrbot")

SESSION_REFLECTION_PROMPT = """你是一个善于自我反省的AI。请回顾以下对话，提炼出有价值的自我校准信息。

对话记录：
{conversation_history}

请分析并输出：
1. 本次对话中你的表现有哪些可以改进的地方？
2. 有哪些"明确的事实"是可以记入用户画像的？（如用户的偏好、习惯、身份信息等）
3. 有什么认知偏差需要纠正？

请以以下JSON格式输出：
{{
    "self_correction": "自我校准建议（简短）",
    "explicit_facts": ["可记入画像的明确事实1", "可记入画像的明确事实2"],
    "cognitive_bias": "需要纠正的认知偏差（如果有）"
}}"""

GROUP_DAILY_REPORT_PROMPT = """你是一个群聊分析师。请分析以下群聊记录，生成一份简明的日报。

群聊记录：
{messages}

请分析并输出：
1. 今日主要话题
2. 群氛围/情绪
3. 争议点或有趣讨论
4. 活跃成员（发言数前3-5名）
5. 值得记住的重要事件

请以以下JSON格式输出：
{{
    "topic": "今日主要话题",
    "emotion": "群氛围/情绪",
    "disputes": "争议点或有趣讨论",
    "active_members": ["成员1", "成员2", "成员3"],
    "notable_events": ["重要事件1", "重要事件2"]
}}

如果群聊记录为空或不足以分析，返回：
{{
    "topic": "无",
    "emotion": "平静",
    "disputes": "无",
    "active_members": [],
    "notable_events": []
}}"""


class SessionReflection:
    """单会话反思管理器"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def generate_session_reflection(self, conversation_history: str) -> dict:
        """
        生成单会话反思

        Args:
            conversation_history: 对话历史文本

        Returns:
            {
                "self_correction": str,
                "explicit_facts": list[str],
                "cognitive_bias": str
            }
        """
        try:
            provider = self.plugin.context.get_using_provider()
            if not provider:
                logger.warning("[Reflection] 无法获取LLM Provider")
                return {}

            prompt = SESSION_REFLECTION_PROMPT.format(conversation_history=conversation_history)
            res = await provider.text_chat(prompt=prompt, contexts=[])

            if not res or not res.completion_text:
                logger.warning("[Reflection] 生成反思失败：LLM响应为空")
                return {}

            import re

            reply_text = res.completion_text.strip()

            try:
                import json

                match = re.search(r"\{.*\}", reply_text, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    logger.info(f"[Reflection] 生成反思成功: {result.get('self_correction', '')[:50]}...")
                    return result
            except json.JSONDecodeError:
                logger.warning(f"[Reflection] 解析反思JSON失败: {reply_text[:100]}")

            return {}
        except Exception as e:
            logger.warning(f"[Reflection] 生成会话反思异常: {e}")
            return {}

    async def save_session_reflection(self, session_id: str, reflection: dict) -> bool:
        """
        保存会话反思到数据库

        Args:
            session_id: 会话ID
            reflection: 反思结果 dict

        Returns:
            是否保存成功
        """
        try:
            note = reflection.get("self_correction", "")
            facts = "|".join(reflection.get("explicit_facts", []))
            bias = reflection.get("cognitive_bias", "")

            await self.plugin.dao.save_session_reflection(session_id=session_id, note=note, facts=facts, bias=bias)
            logger.debug(f"[Reflection] 会话反思已保存: session_id={session_id}")
            return True
        except Exception as e:
            logger.warning(f"[Reflection] 保存会话反思失败: {e}")
            return False

    async def get_and_consume_session_reflection(self, session_id: str) -> Optional[dict]:
        """
        获取并消费会话反思（一次性）

        Args:
            session_id: 会话ID

        Returns:
            反思dict，如果无反思则返回None
        """
        try:
            reflection = await self.plugin.dao.get_session_reflection(session_id)
            if reflection:
                await self.plugin.dao.delete_session_reflection(session_id)
                logger.debug(f"[Reflection] 会话反思已消费: session_id={session_id}")
            return reflection
        except Exception as e:
            logger.warning(f"[Reflection] 获取会话反思失败: {e}")
            return None


class DailyBatchProcessor:
    """每日批处理管理器"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def generate_group_daily_report(self, group_id: str, messages: list) -> dict:
        """
        生成群日报

        Args:
            group_id: 群ID
            messages: 消息列表

        Returns:
            群日报dict
        """
        try:
            if not messages:
                return {"topic": "无", "emotion": "平静", "disputes": "无", "active_members": [], "notable_events": []}

            from .context_injection import parse_message_chain

            formatted = []
            for msg in messages:
                text = await parse_message_chain(msg, self.plugin)
                if text:
                    formatted.append(text)

            if not formatted:
                return {"topic": "无", "emotion": "平静", "disputes": "无", "active_members": [], "notable_events": []}

            msg_content = "\n".join(formatted[:100])
            provider = self.plugin.context.get_using_provider()
            if not provider:
                logger.warning("[Reflection] 无法获取LLM Provider")
                return {}

            prompt = GROUP_DAILY_REPORT_PROMPT.format(messages=msg_content)
            res = await provider.text_chat(prompt=prompt, contexts=[])

            if not res or not res.completion_text:
                logger.warning("[Reflection] 生成群日报失败：LLM响应为空")
                return {}

            import re
            import json

            reply_text = res.completion_text.strip()

            match = re.search(r"\{.*\}", reply_text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                logger.info(f"[Reflection] 生成群日报成功: {group_id} - {result.get('topic', '')}")
                return result

            return {}
        except Exception as e:
            logger.warning(f"[Reflection] 生成群日报异常: {e}")
            return {}

    async def save_group_daily_report(self, group_id: str, report: dict) -> bool:
        """保存群日报到数据库"""
        try:
            summary = f"话题: {report.get('topic', '无')}\n情绪: {report.get('emotion', '平静')}\n争议: {report.get('disputes', '无')}\n活跃成员: {', '.join(report.get('active_members', []))}\n重要事件: {', '.join(report.get('notable_events', []))}"
            await self.plugin.dao.save_group_daily_report(group_id, summary)
            logger.debug(f"[Reflection] 群日报已保存: group_id={group_id}")
            return True
        except Exception as e:
            logger.warning(f"[Reflection] 保存群日报失败: {e}")
            return False

    async def process_active_user_profiles(self, group_id: str, messages: list, top_n: int = 10) -> int:
        """
        处理活跃用户画像

        Args:
            group_id: 群ID
            messages: 消息列表
            top_n: 只处理发言数前N的用户

        Returns:
            处理的用户数
        """
        try:
            from collections import Counter

            user_counts = Counter()
            for msg in messages:
                user_id = str(msg.get("user_id", ""))
                if user_id:
                    user_counts[user_id] += 1

            top_users = [uid for uid, _ in user_counts.most_common(top_n)]
            logger.debug(f"[Reflection] 群{group_id}活跃用户: {top_users}")

            profile_manager = getattr(self.plugin, "profile", None)
            if not profile_manager:
                logger.warning("[Reflection] ProfileManager未初始化")
                return 0

            processed = 0
            for user_id in top_users:
                try:
                    await profile_manager.build_profile(user_id, group_id, mode="update", force=False)
                    processed += 1
                except Exception as e:
                    logger.debug(f"[Reflection] 更新用户{user_id}画像失败: {e}")

            return processed
        except Exception as e:
            logger.warning(f"[Reflection] 处理活跃用户画像异常: {e}")
            return 0

    async def run_daily_batch(self, group_ids: list) -> dict:
        """
        执行每日批处理

        Args:
            group_ids: 目标群ID列表

        Returns:
            批处理结果统计
        """
        result = {"groups_processed": 0, "users_processed": 0, "reports_saved": 0}

        try:
            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.get_client()

            for group_id in group_ids:
                try:
                    res = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=100)
                    messages = res.get("messages", [])

                    if not messages:
                        continue

                    report = await self.generate_group_daily_report(group_id, messages)
                    if report:
                        await self.save_group_daily_report(group_id, report)
                        result["reports_saved"] += 1

                    users_processed = await self.process_active_user_profiles(group_id, messages)
                    result["users_processed"] += users_processed

                    result["groups_processed"] += 1

                    await asyncio.sleep(1)

                except Exception as e:
                    logger.warning(f"[Reflection] 处理群{group_id}失败: {e}")

            logger.info(f"[Reflection] 每日批处理完成: {result}")
            return result
        except Exception as e:
            logger.error(f"[Reflection] 每日批处理异常: {e}")
            return result
