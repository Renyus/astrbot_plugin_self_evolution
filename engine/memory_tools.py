from typing import Optional

from astrbot.api import logger

from .memory_query_service import MemoryQueryService
from .memory_types import MemoryQueryIntent, MemoryQueryRequest


class MemoryTools:
    def __init__(self, plugin):
        self.plugin = plugin
        self.query_service = MemoryQueryService(plugin)

    async def get_group_memory_summary(self, scope_id: str, date: str = "yesterday") -> str:
        """获取群在某天的会话总结

        对应意图：DAILY_SUMMARY
        """
        result = await self.query_service.query(
            MemoryQueryRequest(
                scope_id=scope_id,
                user_id="",
                query="",
                intent=MemoryQueryIntent.DAILY_SUMMARY,
                limit=3,
                date=date,
            )
        )
        return result.text

    async def get_group_recent_context(self, scope_id: str, limit: int = 30) -> str:
        """获取群最近上下文

        对应意图：RECENT_CONTEXT
        """
        result = await self.query_service.query(
            MemoryQueryRequest(
                scope_id=scope_id,
                user_id="",
                query="",
                intent=MemoryQueryIntent.RECENT_CONTEXT,
                limit=limit,
            )
        )
        return result.text

    async def get_user_profile(self, scope_id: str, user_id: str) -> str:
        """获取用户画像

        对应意图：USER_PROFILE
        """
        result = await self.query_service.query(
            MemoryQueryRequest(
                scope_id=scope_id,
                user_id=user_id,
                query="",
                intent=MemoryQueryIntent.USER_PROFILE,
                limit=8,
            )
        )
        return result.text

    async def get_user_messages(self, user_id: str, scope_id: str, limit: int = 30) -> str:
        """获取用户历史消息

        对应意图：USER_MESSAGE_HISTORY
        """
        result = await self.query_service.query(
            MemoryQueryRequest(
                scope_id=scope_id,
                user_id=user_id,
                query="",
                intent=MemoryQueryIntent.USER_MESSAGE_HISTORY,
                limit=limit,
            )
        )
        return result.text

    async def upsert_cognitive_memory(
        self,
        content: str,
        scope_id: str,
        user_id: str,
        category: str,
        fact_type: Optional[str] = None,
        nickname: str = "",
        source: str = "manual",
    ) -> str:
        """统一认知记忆写入

        代理到 memory_router.write()
        """
        if not hasattr(self.plugin, "memory_router"):
            return "记忆路由不可用"

        result = await self.plugin.memory_router.write(
            content=content,
            scope_id=scope_id,
            user_id=user_id,
            category=category,
            fact_type=fact_type,
            nickname=nickname,
            source=source,
        )
        return result
