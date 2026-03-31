from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import time

from .speech_types import InteractionKind


@dataclass
class WindowedScopeStats:
    active_text_count: int = 0
    active_emoji_count: int = 0
    active_reaction_count: int = 0
    passive_text_count: int = 0
    passive_emoji_count: int = 0
    passive_reaction_count: int = 0
    guard_blocked_count: int = 0
    degraded_to_emoji_count: int = 0
    anchor_type_counts: dict = field(default_factory=lambda: defaultdict(int))
    skip_reason_counts: dict = field(default_factory=lambda: defaultdict(int))
    degrade_reason_counts: dict = field(default_factory=lambda: defaultdict(int))
    blocked_reason_counts: dict = field(default_factory=lambda: defaultdict(int))
    _window_start: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict:
        return {
            "active_text_count": self.active_text_count,
            "active_emoji_count": self.active_emoji_count,
            "active_reaction_count": self.active_reaction_count,
            "passive_text_count": self.passive_text_count,
            "passive_emoji_count": self.passive_emoji_count,
            "passive_reaction_count": self.passive_reaction_count,
            "guard_blocked_count": self.guard_blocked_count,
            "degraded_to_emoji_count": self.degraded_to_emoji_count,
            "anchor_type_counts": dict(self.anchor_type_counts),
            "skip_reason_counts": dict(self.skip_reason_counts),
            "degrade_reason_counts": dict(self.degrade_reason_counts),
            "blocked_reason_counts": dict(self.blocked_reason_counts),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WindowedScopeStats":
        stats = cls()
        stats.active_text_count = data.get("active_text_count", 0)
        stats.active_emoji_count = data.get("active_emoji_count", 0)
        stats.active_reaction_count = data.get("active_reaction_count", 0)
        stats.passive_text_count = data.get("passive_text_count", 0)
        stats.passive_emoji_count = data.get("passive_emoji_count", 0)
        stats.passive_reaction_count = data.get("passive_reaction_count", 0)
        stats.guard_blocked_count = data.get("guard_blocked_count", 0)
        stats.degraded_to_emoji_count = data.get("degraded_to_emoji_count", 0)
        stats.anchor_type_counts = defaultdict(int, data.get("anchor_type_counts", {}))
        stats.skip_reason_counts = defaultdict(int, data.get("skip_reason_counts", {}))
        stats.degrade_reason_counts = defaultdict(int, data.get("degrade_reason_counts", {}))
        stats.blocked_reason_counts = defaultdict(int, data.get("blocked_reason_counts", {}))
        return stats

    def is_window_expired(self, window_seconds: float = 86400.0) -> bool:
        return (time.time() - self._window_start) > window_seconds


@dataclass
class ScopeStats:
    active_text_count: int = 0
    active_emoji_count: int = 0
    active_reaction_count: int = 0
    passive_text_count: int = 0
    passive_emoji_count: int = 0
    passive_reaction_count: int = 0
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
            "active_reaction_count": self.active_reaction_count,
            "passive_text_count": self.passive_text_count,
            "passive_emoji_count": self.passive_emoji_count,
            "passive_reaction_count": self.passive_reaction_count,
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
        stats.active_reaction_count = data.get("active_reaction_count", 0)
        stats.passive_text_count = data.get("passive_text_count", 0)
        stats.passive_emoji_count = data.get("passive_emoji_count", 0)
        stats.passive_reaction_count = data.get("passive_reaction_count", 0)
        stats.guard_blocked_count = data.get("guard_blocked_count", 0)
        stats.degraded_to_emoji_count = data.get("degraded_to_emoji_count", 0)
        stats.anchor_type_counts = defaultdict(int, data.get("anchor_type_counts", {}))
        stats.skip_reason_counts = defaultdict(int, data.get("skip_reason_counts", {}))
        stats.degrade_reason_counts = defaultdict(int, data.get("degrade_reason_counts", {}))
        stats.blocked_reason_counts = defaultdict(int, data.get("blocked_reason_counts", {}))
        return stats


class EngagementStats:
    """轻量行为观测层。

    记录主动/被动 文本/表情/Reaction 的次数，
    以及锚点类型分布、跳过原因、审查拦截原因、降级原因。
    支持 rolling_24h 和 lifetime 两层窗口，全部持久化到 DB。
    """

    _WINDOW_SECONDS = 86400.0

    def __init__(self):
        self._lifetime: dict[str, ScopeStats] = defaultdict(ScopeStats)
        self._windowed: dict[str, WindowedScopeStats] = defaultdict(WindowedScopeStats)
        self._loaded: set[str] = set()

    def record(self, scope_id: str, kind: InteractionKind, reason: str = "", anchor_type: str = ""):
        """统一记录入口，自动分流到 lifetime + rolling_24h。"""
        lt = self._lifetime[scope_id]
        wd = self._windowed[scope_id]

        if wd.is_window_expired(self._WINDOW_SECONDS):
            wd = self._windowed[scope_id] = WindowedScopeStats()

        if kind == InteractionKind.ACTIVE_TEXT:
            lt.active_text_count += 1
            wd.active_text_count += 1
            if anchor_type:
                lt.anchor_type_counts[anchor_type] += 1
                wd.anchor_type_counts[anchor_type] += 1
        elif kind == InteractionKind.ACTIVE_EMOJI:
            lt.active_emoji_count += 1
            wd.active_emoji_count += 1
        elif kind == InteractionKind.ACTIVE_REACTION:
            lt.active_reaction_count += 1
            wd.active_reaction_count += 1
        elif kind == InteractionKind.PASSIVE_TEXT:
            lt.passive_text_count += 1
            wd.passive_text_count += 1
        elif kind == InteractionKind.PASSIVE_EMOJI:
            lt.passive_emoji_count += 1
            wd.passive_emoji_count += 1
        elif kind == InteractionKind.PASSIVE_REACTION:
            lt.passive_reaction_count += 1
            wd.passive_reaction_count += 1
        elif kind == InteractionKind.SKIP:
            if reason:
                lt.skip_reason_counts[reason] += 1
                wd.skip_reason_counts[reason] += 1
        elif kind == InteractionKind.GUARD_BLOCKED:
            lt.guard_blocked_count += 1
            wd.guard_blocked_count += 1
            if reason:
                lt.blocked_reason_counts[reason] += 1
                wd.blocked_reason_counts[reason] += 1
        elif kind == InteractionKind.DEGRADED:
            lt.degraded_to_emoji_count += 1
            wd.degraded_to_emoji_count += 1
            if reason:
                lt.degrade_reason_counts[reason] += 1
                wd.degrade_reason_counts[reason] += 1

    record_active_text = lambda self, sid, anchor_type="": self.record(sid, InteractionKind.ACTIVE_TEXT, anchor_type=anchor_type)
    record_active_emoji = lambda self, sid: self.record(sid, InteractionKind.ACTIVE_EMOJI)
    record_active_reaction = lambda self, sid: self.record(sid, InteractionKind.ACTIVE_REACTION)
    record_passive_text = lambda self, sid: self.record(sid, InteractionKind.PASSIVE_TEXT)
    record_passive_emoji = lambda self, sid: self.record(sid, InteractionKind.PASSIVE_EMOJI)
    record_passive_reaction = lambda self, sid: self.record(sid, InteractionKind.PASSIVE_REACTION)
    record_guard_blocked = lambda self, sid, reason="": self.record(sid, InteractionKind.GUARD_BLOCKED, reason)
    record_degraded = lambda self, sid, reason="": self.record(sid, InteractionKind.DEGRADED, reason)
    record_skip = lambda self, sid, reason="": self.record(sid, InteractionKind.SKIP, reason)

    def to_dict(self, scope_id: str) -> dict:
        lt = self._lifetime.get(scope_id)
        if not lt:
            return {}
        return lt.to_dict()

    def to_windowed_dict(self, scope_id: str) -> dict:
        wd = self._windowed.get(scope_id)
        if not wd:
            return {}
        return wd.to_dict()

    def from_dict(self, scope_id: str, data: dict):
        if not data:
            return
        self._lifetime[scope_id] = ScopeStats.from_dict(data)
        self._loaded.add(scope_id)

    def from_windowed_dict(self, scope_id: str, data: dict):
        if not data:
            return
        self._windowed[scope_id] = WindowedScopeStats.from_dict(data)

    def is_loaded(self, scope_id: str) -> bool:
        return scope_id in self._loaded

    def get_lifetime(self, scope_id: str) -> ScopeStats:
        return self._lifetime.get(scope_id, ScopeStats())

    def get_windowed(self, scope_id: str) -> WindowedScopeStats:
        wd = self._windowed.get(scope_id)
        if wd and wd.is_window_expired(self._WINDOW_SECONDS):
            wd = self._windowed[scope_id] = WindowedScopeStats()
        return wd if wd else WindowedScopeStats()

    def get_summary(self, scope_id: str) -> str:
        lt = self.get_lifetime(scope_id)
        wd = self.get_windowed(scope_id)
        has_data = (
            lt.active_text_count > 0
            or lt.active_emoji_count > 0
            or lt.active_reaction_count > 0
            or lt.passive_text_count > 0
            or lt.passive_emoji_count > 0
            or lt.passive_reaction_count > 0
            or lt.guard_blocked_count > 0
            or lt.degraded_to_emoji_count > 0
            or lt.skip_reason_counts
            or lt.blocked_reason_counts
        )
        if not has_data:
            return f"[EngagementStats scope={scope_id}] 无记录"

        def fmt_bucket(label, lt_val, wd_val, extra=""):
            return f"  {label}: 累计={lt_val} | 24h={wd_val}{extra}"

        lines = [
            f"[EngagementStats scope={scope_id}]",
            f"  主动: 文本={lt.active_text_count}/{wd.active_text_count} "
            f"表情={lt.active_emoji_count}/{wd.active_emoji_count} "
            f"reaction={lt.active_reaction_count}/{wd.active_reaction_count}",
            f"  被动: 文本={lt.passive_text_count}/{wd.passive_text_count} "
            f"表情={lt.passive_emoji_count}/{wd.passive_emoji_count} "
            f"reaction={lt.passive_reaction_count}/{wd.passive_reaction_count}",
            f"  拦截: {lt.guard_blocked_count}/{wd.guard_blocked_count} | "
            f"降级表情: {lt.degraded_to_emoji_count}/{wd.degraded_to_emoji_count}",
        ]

        if lt.anchor_type_counts:
            anchor_str = ", ".join(f"{k}={v}" for k, v in sorted(lt.anchor_type_counts.items()))
            lines.append(f"  锚点分布: {anchor_str}")

        if lt.blocked_reason_counts:
            blocked_str = ", ".join(
                f"{k}={v}" for k, v in sorted(lt.blocked_reason_counts.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  审查拦截原因(top3): {blocked_str}")

        if lt.skip_reason_counts:
            skip_str = ", ".join(f"{k}={v}" for k, v in sorted(lt.skip_reason_counts.items(), key=lambda x: -x[1])[:3])
            lines.append(f"  跳过原因(top3): {skip_str}")

        if lt.degrade_reason_counts:
            deg_str = ", ".join(
                f"{k}={v}" for k, v in sorted(lt.degrade_reason_counts.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  降级原因(top3): {deg_str}")

        return "\n".join(lines)

    def clear_scope(self, scope_id: str):
        if scope_id in self._lifetime:
            del self._lifetime[scope_id]
        if scope_id in self._windowed:
            del self._windowed[scope_id]
