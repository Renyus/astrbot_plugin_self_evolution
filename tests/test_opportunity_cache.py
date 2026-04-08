"""测试 opportunity_cache 模块"""

import time
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.opportunity_cache import (
    OpportunityCache,
    PendingOpportunity,
    OpportunityScore,
    ActiveMotive,
    MotiveType,
)


class TestOpportunityCache:
    def setup_method(self):
        self.cache = OpportunityCache()

    def _make_score(self, total=0.3, blocked=False):
        s = OpportunityScore(total=total)
        if blocked:
            s.negative_override = -1.0
        return s

    def _make_motive(self, motive=MotiveType.NONE, strength=0.5):
        return ActiveMotive(motive=motive, strength=strength, source="test")

    @pytest.mark.asyncio
    async def test_warm_and_consume_basic(self):
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.4),
            anchor_text="hello",
            anchor_type="passive_message",
            motive=self._make_motive(),
            message_ids=["mid1"],
            trigger_reason="test",
        )
        pending = await self.cache.consume("group_1")
        assert len(pending) == 1
        assert pending[0].score.total == 0.4
        assert pending[0].anchor_text == "hello"
        assert not pending[0].is_expired()

    @pytest.mark.asyncio
    async def test_consume_empty_when_no_opportunities(self):
        pending = await self.cache.consume("nonexistent_group")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_expired_opportunity_not_returned(self):
        now = time.time()
        opp = PendingOpportunity(
            scope_id="group_1",
            score=self._make_score(0.3),
            anchor_text="old",
            anchor_type="test",
            motive=self._make_motive(),
            created_at=now - 200,
            expires_at=now - 10,
            message_ids=[],
            trigger_reason="expired test",
        )
        self.cache._data["group_1"] = [opp]
        pending = await self.cache.consume("group_1")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_warm_blocked_score_ignored(self):
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.5, blocked=True),
            anchor_text="blocked",
            anchor_type="test",
            motive=self._make_motive(),
            message_ids=[],
            trigger_reason="should be blocked",
        )
        pending = await self.cache.consume("group_1")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_max_per_scope_enforced(self):
        for i in range(5):
            await self.cache.warm(
                scope_id="group_1",
                score=self._make_score(0.1 * i),
                anchor_text=f"msg_{i}",
                anchor_type="test",
                motive=self._make_motive(),
                message_ids=[],
                trigger_reason=f"test_{i}",
            )
        pending = await self.cache.consume("group_1")
        assert len(pending) == 3
        scores = [p.score.total for p in pending]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_peek_does_not_remove(self):
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.4),
            anchor_text="peekable",
            anchor_type="test",
            motive=self._make_motive(),
            message_ids=[],
            trigger_reason="peek test",
        )
        peek1 = await self.cache.peek("group_1")
        peek2 = await self.cache.peek("group_1")
        assert len(peek1) == 1
        assert len(peek2) == 1
        consume = await self.cache.consume("group_1")
        assert len(consume) == 1
        assert len(await self.cache.peek("group_1")) == 0

    @pytest.mark.asyncio
    async def test_has_any(self):
        assert not await self.cache.has_any("group_1")
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.4),
            anchor_text="test",
            anchor_type="test",
            motive=self._make_motive(),
            message_ids=[],
            trigger_reason="test",
        )
        assert await self.cache.has_any("group_1")

    @pytest.mark.asyncio
    async def test_remove_by_anchor(self):
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.4),
            anchor_text="hello",
            anchor_type="passive_message",
            motive=self._make_motive(),
            message_ids=["mid1"],
            trigger_reason="test",
        )
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.5),
            anchor_text="world",
            anchor_type="passive_message",
            motive=self._make_motive(),
            message_ids=["mid2"],
            trigger_reason="test",
        )
        await self.cache.remove_by_anchor("group_1", "passive_message", "hello")
        remaining = await self.cache.peek("group_1")
        assert len(remaining) == 1
        assert remaining[0].anchor_text == "world"

    @pytest.mark.asyncio
    async def test_remove_one(self):
        await self.cache.warm(
            scope_id="group_1",
            score=self._make_score(0.4),
            anchor_text="hello",
            anchor_type="test",
            motive=self._make_motive(),
            message_ids=[],
            trigger_reason="test",
        )
        peek_result = await self.cache.peek("group_1")
        assert len(peek_result) == 1
        opp = peek_result[0]
        await self.cache.remove_one("group_1", opp)
        assert len(await self.cache.peek("group_1")) == 0


class TestOpportunityScore:
    def test_level_from_score_blocked(self):
        s = OpportunityScore(total=0.8)
        s.negative_override = -1.0
        assert s.is_blocked
        assert s.level_from_score() == "ignore"

    def test_level_from_score_ignore(self):
        s = OpportunityScore(total=0.10)
        assert not s.is_blocked
        assert s.level_from_score() == "ignore"

    def test_level_from_score_react(self):
        s = OpportunityScore(total=0.20)
        assert not s.is_blocked
        assert s.level_from_score() == "react"

    def test_level_from_score_boundary_react_full(self):
        s = OpportunityScore(total=0.34)
        assert s.level_from_score() == "text_lite"

    def test_level_from_score_full(self):
        s = OpportunityScore(total=0.50)
        assert not s.is_blocked
        assert s.level_from_score() == "full"

    def test_level_from_score_boundary_react_ignore(self):
        s = OpportunityScore(total=0.14)
        assert s.level_from_score() == "ignore"
        s2 = OpportunityScore(total=0.15)
        assert s2.level_from_score() == "react"

    def test_level_from_score_boundary_react_full(self):
        s = OpportunityScore(total=0.34)
        assert s.level_from_score() == "text_lite"
        s2 = OpportunityScore(total=0.35)
        assert s2.level_from_score() == "full"


class TestPendingOpportunity:
    def test_is_expired(self):
        now = time.time()
        opp = PendingOpportunity(
            scope_id="g1",
            score=OpportunityScore(total=0.3),
            anchor_text="test",
            anchor_type="test",
            motive=ActiveMotive(motive=MotiveType.NONE, strength=0.0, source=""),
            created_at=now - 100,
            expires_at=now + 50,
        )
        assert not opp.is_expired()
        assert not opp.is_expired(now + 40)
        assert opp.is_expired(now + 60)

    def test_is_high_score(self):
        now = time.time()
        opp_low = PendingOpportunity(
            scope_id="g1",
            score=OpportunityScore(total=0.30),
            anchor_text="test",
            anchor_type="test",
            motive=ActiveMotive(motive=MotiveType.NONE, strength=0.0, source=""),
            created_at=now,
            expires_at=now + 100,
        )
        assert not opp_low.is_high_score()

        opp_high = PendingOpportunity(
            scope_id="g1",
            score=OpportunityScore(total=0.50),
            anchor_text="test",
            anchor_type="test",
            motive=ActiveMotive(motive=MotiveType.NONE, strength=0.0, source=""),
            created_at=now,
            expires_at=now + 100,
        )
        assert opp_high.is_high_score()
