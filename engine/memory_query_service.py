from typing import Optional

from astrbot.api import logger

from .memory_types import MemoryQueryIntent, MemoryQueryRequest, MemoryQueryResult


class MemoryQueryService:
    def __init__(self, plugin):
        self.plugin = plugin

    def _debug(self, msg: str):
        if getattr(self.plugin, "cfg", None) and getattr(self.plugin.cfg, "memory_debug_enabled", False):
            logger.debug(msg)

    async def query(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """统一查询入口，根据 intent 分派到具体 retrieval 策略"""
        intent_name = getattr(request.intent, "value", str(request.intent))
        self._debug(
            f"[MemoryQuery] intent={intent_name} scope={request.scope_id} user={request.user_id} limit={request.limit}"
        )
        if request.intent == MemoryQueryIntent.RECENT_CONTEXT:
            return await self._query_recent_context(request)
        elif request.intent == MemoryQueryIntent.DAILY_SUMMARY:
            return await self._query_daily_summary(request)
        elif request.intent == MemoryQueryIntent.SESSION_EVENT:
            return await self._query_session_event(request)
        elif request.intent == MemoryQueryIntent.USER_PROFILE:
            return await self._query_user_profile(request)
        elif request.intent == MemoryQueryIntent.USER_MESSAGE_HISTORY:
            return await self._query_user_message_history(request)
        elif request.intent == MemoryQueryIntent.FALLBACK_KB:
            return await self._query_fallback_kb(request)
        else:
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="unknown",
                hit_count=0,
            )

    async def _query_recent_context(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """查询最近群上下文 - 走 NapCat 历史消息，不查 KB"""
        try:
            group_id = request.scope_id
            if not group_id or group_id.startswith("private_"):
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="recent_context",
                    hit_count=0,
                )

            from .context_injection import get_group_history

            history_text = await get_group_history(
                self.plugin,
                group_id,
                count=request.limit,
            )
            hit = 1 if history_text else 0
            self._debug(f"[MemoryQuery] intent=recent_context scope={group_id} hit={hit}")
            return MemoryQueryResult(
                intent=request.intent,
                text=history_text,
                source="recent_context",
                hit_count=hit,
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] RECENT_CONTEXT failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="recent_context",
                hit_count=0,
            )

    async def _query_daily_summary(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """查询某天总结 - 走 SessionMemoryStore.get_summary_by_date"""
        try:
            scope_id = request.scope_id
            date = request.date or "yesterday"

            store = getattr(self.plugin, "session_memory_store", None)
            if not store:
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="daily_summary",
                    hit_count=0,
                )

            summary = await store.get_summary_by_date(scope_id, date)
            hit = 1 if summary else 0
            self._debug(f"[MemoryQuery] intent=daily_summary scope={scope_id} date={date} hit={hit}")
            return MemoryQueryResult(
                intent=request.intent,
                text=summary,
                source="daily_summary",
                hit_count=hit,
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] DAILY_SUMMARY failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="daily_summary",
                hit_count=0,
            )

    async def _query_session_event(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """查询事件 - 走 SessionMemoryStore.retrieve_events"""
        try:
            scope_id = request.scope_id
            query = request.query
            limit = request.limit

            store = getattr(self.plugin, "session_memory_store", None)
            if not store:
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="session_event",
                    hit_count=0,
                )

            events = await store.retrieve_events(scope_id, query, limit)
            if not events:
                self._debug(f"[MemoryQuery] intent=session_event scope={scope_id} query='{query}' hit=0")
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="session_event",
                    hit_count=0,
                )

            lines = [f"- {e}" for e in events]
            text = "\n".join(lines)
            self._debug(f"[MemoryQuery] intent=session_event scope={scope_id} query='{query}' hit={len(events)}")
            return MemoryQueryResult(
                intent=request.intent,
                text=text,
                source="session_event",
                hit_count=len(events),
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] SESSION_EVENT failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="session_event",
                hit_count=0,
            )

    async def _query_user_profile(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """查询用户画像 - 走 ProfileSummaryService.get_structured_summary"""
        try:
            scope_id = request.scope_id
            user_id = request.user_id

            svc = getattr(self.plugin, "profile_summary_service", None)
            if not svc:
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="user_profile",
                    hit_count=0,
                )

            summary = await svc.get_structured_summary(scope_id, user_id, max_items=8)
            hit = 1 if summary else 0
            self._debug(f"[MemoryQuery] intent=user_profile scope={scope_id} user={user_id} hit={hit}")
            return MemoryQueryResult(
                intent=request.intent,
                text=summary,
                source="user_profile",
                hit_count=hit,
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] USER_PROFILE failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="user_profile",
                hit_count=0,
            )

    async def _query_user_message_history(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """查询用户历史消息 - 走 NapCat 单用户消息抓取"""
        try:
            user_id = request.user_id
            scope_id = request.scope_id
            limit = request.limit

            if not hasattr(self.plugin, "get_user_messages_for_tool"):
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="user_message_history",
                    hit_count=0,
                )

            messages = await self.plugin.get_user_messages_for_tool(
                user_id=user_id,
                group_id=scope_id,
                fetch_limit=limit,
            )
            if not messages:
                self._debug(f"[MemoryQuery] intent=user_message_history scope={scope_id} user={user_id} hit=0")
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="user_message_history",
                    hit_count=0,
                )

            lines = [f"- {m.get('text', '')}" for m in messages if m.get("text")]
            text = "\n".join(lines)
            self._debug(
                f"[MemoryQuery] intent=user_message_history scope={scope_id} user={user_id} hit={len(messages)}"
            )
            return MemoryQueryResult(
                intent=request.intent,
                text=text,
                source="user_message_history",
                hit_count=len(messages),
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] USER_MESSAGE_HISTORY failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="user_message_history",
                hit_count=0,
            )

    async def _query_fallback_kb(self, request: MemoryQueryRequest) -> MemoryQueryResult:
        """兜底 KB 检索 - 走通用 smart_retrieve"""
        try:
            if not getattr(self.plugin, "cfg", None) or not self.plugin.cfg.memory_query_fallback_enabled:
                self._debug(f"[MemoryQuery] intent=fallback_kb scope={request.scope_id} skipped=disabled")
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="fallback_kb",
                    hit_count=0,
                )

            scope_id = request.scope_id
            query = request.query
            max_results = request.limit

            store = getattr(self.plugin, "session_memory_store", None)
            if not store:
                return MemoryQueryResult(
                    intent=request.intent,
                    text="",
                    source="fallback_kb",
                    hit_count=0,
                )

            text = await store.retrieve_summary(scope_id, query, max_results)
            hit = 1 if text else 0
            self._debug(f"[MemoryQuery] intent=fallback_kb scope={scope_id} query='{query}' hit={hit}")
            return MemoryQueryResult(
                intent=request.intent,
                text=text,
                source="fallback_kb",
                hit_count=hit,
            )
        except Exception as e:
            logger.warning(f"[MemoryQuery] FALLBACK_KB failed: {e}")
            return MemoryQueryResult(
                intent=request.intent,
                text="",
                source="fallback_kb",
                hit_count=0,
            )
