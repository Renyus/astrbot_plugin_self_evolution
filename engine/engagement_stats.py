from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScopeStats:
    active_text_count: int = 0
    active_emoji_count: int = 0
    passive_text_count: int = 0
    passive_emoji_count: int = 0
    guard_blocked_count: int = 0
    degraded_to_emoji_count: int = 0
    anchor_type_counts: dict = field(default_factory=lambda: defaultdict(int))
    skip_reason_counts: dict = field(default_factory=lambda: defaultdict(int))
    degrade_reason_counts: dict = field(default_factory=lambda: defaultdict(int))
    blocked_reason_counts: dict = field(default_factory=lambda: defaultdict(int))

    def to_dict(self) -> dict:
        return {
            "active_text_count": self.active_text_count,
            "active_emoji_count": self.active_emoji_count,
            "passive_text_count": self.passive_text_count,
            "passive_emoji_count": self.passive_emoji_count,
            "guard_blocked_count": self.guard_blocked_count,
            "degraded_to_emoji_count": self.degraded_to_emoji_count,
            "anchor_type_counts": dict(self.anchor_type_counts),
            "skip_reason_counts": dict(self.skip_reason_counts),
            "degrade_reason_counts": dict(self.degrade_reason_counts),
            "blocked_reason_counts": dict(self.blocked_reason_counts),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScopeStats":
        stats = cls()
        stats.active_text_count = data.get("active_text_count", 0)
        stats.active_emoji_count = data.get("active_emoji_count", 0)
        stats.passive_text_count = data.get("passive_text_count", 0)
        stats.passive_emoji_count = data.get("passive_emoji_count", 0)
        stats.guard_blocked_count = data.get("guard_blocked_count", 0)
        stats.degraded_to_emoji_count = data.get("degraded_to_emoji_count", 0)
        stats.anchor_type_counts = defaultdict(int, data.get("anchor_type_counts", {}))
        stats.skip_reason_counts = defaultdict(int, data.get("skip_reason_counts", {}))
        stats.degrade_reason_counts = defaultdict(int, data.get("degrade_reason_counts", {}))
        stats.blocked_reason_counts = defaultdict(int, data.get("blocked_reason_counts", {}))
        return stats


class EngagementStats:
    """轻量行为观测层。

    记录主动/被动文本、表情包、被拦截、降级的次数，
    以及锚点类型分布和跳过原因分布。
    数据存在内存中，按 scope 隔离，可选落 DB。
    """

    def __init__(self):
        self._scope_stats: dict[str, ScopeStats] = defaultdict(ScopeStats)
        self._loaded: set[str] = set()

    def to_dict(self, scope_id: str) -> dict:
        stats = self._scope_stats.get(scope_id)
        if not stats:
            return {}
        return stats.to_dict()

    def from_dict(self, scope_id: str, data: dict):
        if not data:
            return
        self._scope_stats[scope_id] = ScopeStats.from_dict(data)
        self._loaded.add(scope_id)

    def is_loaded(self, scope_id: str) -> bool:
        return scope_id in self._loaded

    def record_active_text(self, scope_id: str, anchor_type: str = ""):
        stats = self._scope_stats[scope_id]
        stats.active_text_count += 1
        if anchor_type:
            stats.anchor_type_counts[anchor_type] += 1

    def record_active_emoji(self, scope_id: str):
        self._scope_stats[scope_id].active_emoji_count += 1

    def record_passive_text(self, scope_id: str):
        self._scope_stats[scope_id].passive_text_count += 1

    def record_passive_emoji(self, scope_id: str):
        self._scope_stats[scope_id].passive_emoji_count += 1

    def record_guard_blocked(self, scope_id: str, reason: str = ""):
        stats = self._scope_stats[scope_id]
        stats.guard_blocked_count += 1
        if reason:
            stats.blocked_reason_counts[reason] += 1

    def record_degraded(self, scope_id: str, reason: str = ""):
        stats = self._scope_stats[scope_id]
        stats.degraded_to_emoji_count += 1
        if reason:
            stats.degrade_reason_counts[reason] += 1

    def record_skip(self, scope_id: str, reason: str = ""):
        if reason:
            self._scope_stats[scope_id].skip_reason_counts[reason] += 1

    def get_stats(self, scope_id: str) -> ScopeStats:
        return self._scope_stats.get(scope_id, ScopeStats())

    def get_summary(self, scope_id: str) -> str:
        stats = self.get_stats(scope_id)
        has_data = (
            stats.active_text_count > 0
            or stats.active_emoji_count > 0
            or stats.passive_text_count > 0
            or stats.passive_emoji_count > 0
            or stats.guard_blocked_count > 0
            or stats.degraded_to_emoji_count > 0
            or stats.skip_reason_counts
            or stats.blocked_reason_counts
        )
        if not has_data:
            return f"[EngagementStats scope={scope_id}] 无记录"

        total_active = stats.active_text_count + stats.active_emoji_count
        total_passive = stats.passive_text_count + stats.passive_emoji_count

        lines = [
            f"[EngagementStats scope={scope_id}]",
            f"  主动: 文本={stats.active_text_count} 表情={stats.active_emoji_count}",
            f"  被动: 文本={stats.passive_text_count} 表情={stats.passive_emoji_count}",
            f"  拦截: {stats.guard_blocked_count} | 降级表情: {stats.degraded_to_emoji_count}",
        ]

        if stats.anchor_type_counts:
            anchor_str = ", ".join(f"{k}={v}" for k, v in sorted(stats.anchor_type_counts.items()))
            lines.append(f"  锚点分布: {anchor_str}")

        if stats.blocked_reason_counts:
            blocked_str = ", ".join(
                f"{k}={v}" for k, v in sorted(stats.blocked_reason_counts.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  审查拦截原因(top3): {blocked_str}")

        if stats.skip_reason_counts:
            skip_str = ", ".join(
                f"{k}={v}" for k, v in sorted(stats.skip_reason_counts.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  跳过原因(top3): {skip_str}")

        return "\n".join(lines)

    def clear_scope(self, scope_id: str):
        if scope_id in self._scope_stats:
            del self._scope_stats[scope_id]
