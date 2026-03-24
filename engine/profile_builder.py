import asyncio
import logging
from datetime import datetime

from astrbot.api import logger

from .profile_store import ProfileStore

BUILD_PROFILE_PROMPT = """你是记忆助手。请根据对话分析用户特征。

目标用户：{nickname} (QQ: {user_id})
会话范围：{scope_id}
{existing_note}
用户消息：
{messages}

请以Markdown格式输出用户画像，包含以下章节（每个章节不少于3个要点）：
## identity - 用户身份背景（职业、年龄、地区、教育等）
## preferences - 用户兴趣爱好（喜欢什么、讨厌什么）
## traits - 性格特征和行为习惯
## recent_updates - 最近发生的事件或变化
## long_term_notes - 长期稳定的特质或重要经历
"""


class ProfileBuilder:
    def __init__(self, plugin):
        self.plugin = plugin
        self.store = ProfileStore(plugin)

    async def build_profile(
        self,
        scope_id: str,
        user_id: str,
        messages: list,
        is_private_scope: bool = False,
        mode: str = "create",
        existing_note: str = "",
    ) -> str:
        """构建用户画像"""
        try:
            nickname = messages[0].get("sender_nickname", "Unknown") if messages else "Unknown"

            prompt = BUILD_PROFILE_PROMPT.format(
                nickname=nickname,
                user_id=user_id,
                scope_id=scope_id,
                existing_note=f"旧笔记：{existing_note}\n" if mode == "update" and existing_note else "",
                messages="\n".join(
                    [f"- {m.get('sender_nickname', '未知')}: {self._extract_text(m)}" for m in messages[-50:]]
                ),
            )

            umo = None
            try:
                if self.plugin.context.platform_manager.platform_insts:
                    platform = self.plugin.context.platform_manager.platform_insts[0]
                    bot = platform.bot
                    login_info = await bot.call_action("get_login_info")
                    umo = str(login_info.get("user_id", ""))
            except Exception:
                pass

            llm_provider = self.plugin.context.get_using_provider(umo=umo) if umo else None
            if not llm_provider:
                return "无可用模型提供者"

            res = await llm_provider.text_chat(prompt=prompt, contexts=[])
            content = res.completion_text.strip() if hasattr(res, "completion_text") else str(res).strip()

            if not content:
                return "生成画像失败"

            await self.store.save_profile(scope_id, user_id, content, nickname)
            return content

        except Exception as e:
            logger.warning(f"[ProfileBuilder] build_profile failed: {e}")
            return f"生成画像失败: {e}"

    async def analyze_and_build_profiles(
        self,
        group_id: str,
        messages: list = None,
        umo: str | None = None,
    ) -> str:
        """自动分析并构建多个用户画像"""
        try:
            if not messages:
                return "无消息可供分析"

            user_messages = {}
            for msg in messages:
                sender_id = str(msg.get("sender", {}).get("user_id", ""))
                if sender_id and sender_id != str(getattr(self.plugin, "_bot_id", "")):
                    if sender_id not in user_messages:
                        user_messages[sender_id] = []
                    user_messages[sender_id].append(msg)

            if not user_messages:
                return "无有效用户消息"

            results = []
            for user_id, msgs in user_messages.items():
                if len(msgs) < 3:
                    continue

                nickname = msgs[0].get("sender_nickname", "未知")
                scope_id = group_id

                profile_content = await self.build_profile(
                    scope_id=scope_id,
                    user_id=user_id,
                    messages=msgs,
                    is_private_scope=False,
                    mode="create",
                )
                results.append(f"用户 {user_id}({nickname}): {profile_content[:100]}...")

            return "\n".join(results) if results else "无画像生成"

        except Exception as e:
            logger.warning(f"[ProfileBuilder] analyze_and_build_profiles failed: {e}")
            return f"批量构建失败: {e}"

    def _extract_text(self, msg: dict) -> str:
        """从消息中提取文本"""
        try:
            text_parts = []
            for seg in msg.get("message", []):
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text_parts.append(seg.get("data", {}).get("text", ""))
            return "".join(text_parts).strip()
        except Exception:
            return msg.get("text", "")
