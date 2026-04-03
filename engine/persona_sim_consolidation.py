"""
Persona Sim Consolidation - 低频固化层

每天睡眠时段（或手动触发）跑一次。

职责：
- 读取当天所有 persona_events
- 分析情绪轨迹 / effect 出现频率 / 互动质量
- 生成「人格经历日结」写入 persona_events (type=consolidated)
- 可选：轻微调整次日开始状态（persona drift）
- 可选：写入长期人格记忆（session_memory_store 的 private scope）

不做的：
- 不清空当天事件（保留原始记录）
- 不做复杂人格漂移（一期只做轻微 mood nudge）
"""

import logging
import time
from datetime import datetime, timedelta

from .persona_sim_types import EventType, PersonaEvent

logger = logging.getLogger("astrbot")

DRIFT_MAX = 5.0


class PersonaSimConsolidator:
    def __init__(self, plugin):
        self.plugin = plugin
        self._dao = getattr(plugin, "dao", None)
        self._memory_store = getattr(plugin, "memory_store", None)

    async def consolidate_scope(self, scope_id: str, force_date: str | None = None) -> str:
        """对指定 scope 执行日结。返回总结文字。"""
        if not self._dao:
            return "DAO 不可用"

        now = time.time()
        date_str = force_date or datetime.now().strftime("%Y-%m-%d")

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return f"日期格式错误: {date_str}，应为 YYYY-MM-DD"

        day_start = datetime.combine(target_date, datetime.min.time()).timestamp()
        day_end = day_start + 86400

        event_rows = await self._dao.get_all_persona_events_since(scope_id, day_start)
        event_rows = [e for e in event_rows if float(e.get("timestamp", 0)) < day_end]
        if not event_rows:
            logger.info(f"[Consolidation] scope={scope_id} date={date_str} 无事件，跳过")
            return f"scope={scope_id} date={date_str} 无事件记录，跳过"

        events = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
            )
            for e in event_rows
        ]

        state_rows = await self._dao.get_persona_state(scope_id)
        current_state = None
        mood_before = 50.0
        energy_before = 50.0
        if state_rows:
            mood_before = float(state_rows["mood"])
            energy_before = float(state_rows["energy"])
            current_state = {
                "energy": energy_before,
                "mood": mood_before,
                "social_need": float(state_rows["social_need"]),
                "satiety": float(state_rows["satiety"]),
            }

        summary_parts = self._analyze_day(events, current_state, date_str)
        summary_text = self._format_summary(summary_parts, date_str)

        summary_event = PersonaEvent(
            event_type=EventType.CONSOLIDATED,
            summary=summary_text,
            causes=[f"events_count={len(events)}"],
            effects_applied=[],
            timestamp=now,
        )
        await self._dao.add_persona_event(scope_id, summary_event)

        drift = self._calc_drift(summary_parts)
        mood_after = mood_before
        energy_after = energy_before
        if drift != 0 and current_state:
            new_mood = current_state["mood"] + drift
            new_mood = max(10.0, min(90.0, new_mood))
            logger.info(
                f"[Consolidation] scope={scope_id} mood drift: {current_state['mood']:.1f} -> {new_mood:.1f} (drift={drift:+.1f})"
            )
            current_state["mood"] = new_mood
            mood_after = new_mood
            from .persona_sim_types import PersonaState

            new_state = PersonaState(
                energy=current_state["energy"],
                mood=current_state["mood"],
                social_need=current_state["social_need"],
                satiety=current_state["satiety"],
                last_tick_at=now,
                last_interaction_at=current_state.get("last_interaction_at", now),
            )
            await self._dao.upsert_persona_state(scope_id, new_state)

        await self._dao.upsert_persona_episode(
            scope_id=scope_id,
            episode_date=date_str,
            summary=summary_text,
            drift_applied=drift,
            event_count=len(events),
            interaction_count=summary_parts.get("interaction_count", 0),
            dominant_effect=summary_parts.get("dominant_effect", ""),
            mood_before=mood_before,
            mood_after=mood_after,
            energy_before=energy_before,
            energy_after=energy_after,
        )

        logger.info(f"[Consolidation] scope={scope_id} date={date_str} 完成: {summary_text[:80]}")
        return f"人格日结完成 [{date_str}]\n{summary_text}"

    async def get_today_summary(self, scope_id: str) -> str:
        """只读今天的总结，不触发 drift。"""
        if not self._dao:
            return "DAO 不可用"
        date_str = datetime.now().strftime("%Y-%m-%d")
        day_start = datetime.combine(datetime.strptime(date_str, "%Y-%m-%d").date(), datetime.min.time()).timestamp()
        event_rows = await self._dao.get_all_persona_events_since(scope_id, day_start)
        if not event_rows:
            return f"[{date_str}] 暂无记录"
        state_rows = await self._dao.get_persona_state(scope_id)
        current_state = None
        if state_rows:
            current_state = {
                "energy": float(state_rows["energy"]),
                "mood": float(state_rows["mood"]),
                "social_need": float(state_rows["social_need"]),
                "satiety": float(state_rows["satiety"]),
            }
        events = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
            )
            for e in event_rows
        ]
        summary_parts = self._analyze_day(events, current_state, date_str)
        return self._format_summary(summary_parts, date_str)

    def _analyze_day(
        self,
        events: list[PersonaEvent],
        state: dict | None,
        date_str: str,
    ) -> dict:
        """分析一天的情绪轨迹，而非统计事件数量。

        关注点：
        - 今天主导的情绪颜色是什么
        - 互动质量序列（有没有"落空"感）
        - 是否有过缓和/恢复
        - 中间有没有明显起伏
        """
        effect_counter: dict[str, int] = {}
        interaction_events = []

        for ev in events:
            if ev.event_type == EventType.EFFECT_TRIGGER:
                for eff in ev.effects_applied:
                    effect_counter[eff] = effect_counter.get(eff, 0) + 1
            elif ev.event_type == EventType.INTERACTION:
                interaction_events.append(ev)

        dominant_effect = max(effect_counter, key=effect_counter.get) if effect_counter else None
        dominant_count = effect_counter.get(dominant_effect, 0) if dominant_effect else 0

        mood = state.get("mood", 50) if state else 50
        energy = state.get("energy", 50) if state else 50

        mood_desc = ""
        if mood >= 70:
            mood_desc = "愉悦"
        elif mood >= 50:
            mood_desc = "平稳"
        elif mood >= 30:
            mood_desc = "低落"
        else:
            mood_desc = "低迷"

        energy_desc = ""
        if energy >= 70:
            energy_desc = "充沛"
        elif energy >= 50:
            energy_desc = "正常"
        elif energy >= 30:
            energy_desc = "疲惫"
        else:
            energy_desc = "疲倦"

        bad_interactions = sum(1 for e in interaction_events if "bad" in e.causes)
        good_interactions = sum(1 for e in interaction_events if "good" in e.causes)
        awkward_interactions = sum(1 for e in interaction_events if "awkward" in e.causes)
        missed = sum(1 for e in interaction_events if getattr(e, "interaction_outcome", "") == "missed")
        connected = sum(1 for e in interaction_events if getattr(e, "interaction_outcome", "") == "connected")
        active_ixes = sum(1 for e in interaction_events if getattr(e, "interaction_mode", "") == "active")

        if not interaction_events:
            trajectory = "独处"
            emotional_arc = "今天大部分是独处状态"
        elif bad_interactions == 0 and good_interactions == 0:
            trajectory = "平淡"
            emotional_arc = "今天就那么过来了，没什么特别的"
        elif missed > 0 and connected > 0:
            trajectory = "有落差"
            emotional_arc = "今天有些被接住，有些落空，有点颠簸"
        elif missed > good_interactions:
            trajectory = "有失落"
            emotional_arc = "今天有点不太顺，想说的话有些没能说出去"
        elif good_interactions > bad_interactions * 2 and connected > 0:
            trajectory = "向上"
            emotional_arc = "今天整体是往上的，有人把话接住了"
        elif bad_interactions > good_interactions:
            trajectory = "向下"
            emotional_arc = "今天不太舒心，有种被冷落的感觉"
        else:
            trajectory = "平稳"
            emotional_arc = "今天情绪没有太大起伏"

        recovery = bad_interactions > 0 and good_interactions >= bad_interactions

        if recovery and trajectory == "向上":
            arc_suffix = "，后来缓过来了"
        elif recovery:
            arc_suffix = "，但后面慢慢好点了"
        else:
            arc_suffix = ""

        if arc_suffix and emotional_arc.endswith("有点颠簸"):
            emotional_arc = f"今天有些被接住，有些落空{arc_suffix}"
        elif arc_suffix:
            emotional_arc = emotional_arc + arc_suffix

        shift_hint = ""
        if recovery and good_interactions >= 2:
            shift_hint = "经历了低谷后被接住，人会稍微沉稳一点"
        elif trajectory == "有落差" and missed > 0 and active_ixes > 0:
            shift_hint = "主动出击但落空了一次，会有点小心翼翼"
        elif trajectory == "向上" and good_interactions >= 3:
            shift_hint = "今天顺畅，明天会更愿意开口"
        elif trajectory == "向下" and bad_interactions >= 2:
            shift_hint = "今天有点背，明天可能会更谨慎"
        elif trajectory == "独处" and dominant_effect in ("lonely", "low_mood"):
            shift_hint = "独处久了会想要靠近人"

        return {
            "date": date_str,
            "interaction_count": len(interaction_events),
            "bad_interactions": bad_interactions,
            "good_interactions": good_interactions,
            "awkward_interactions": awkward_interactions,
            "missed": missed,
            "connected": connected,
            "dominant_effect": dominant_effect,
            "dominant_count": dominant_count,
            "mood_desc": mood_desc,
            "energy_desc": energy_desc,
            "trajectory": trajectory,
            "emotional_arc": emotional_arc,
            "recovery": recovery,
            "shift_hint": shift_hint,
        }

    def _format_summary(self, parts: dict, date_str: str) -> str:
        lines = [
            f"【人格轨迹 {date_str}】",
        ]

        emotional_arc = parts.get("emotional_arc", "")
        if emotional_arc:
            lines.append(emotional_arc)

        if parts["dominant_effect"]:
            effect_names = {
                "low_energy": "提不起劲",
                "low_mood": "情绪低落",
                "lonely": "有点孤单",
                "hungry": "饿了",
                "irritated": "烦躁",
                "wronged": "委屈",
                "tired": "困倦",
                "curious": "好奇",
                "relieved": "轻松",
                "satisfied": "满足",
                "sleepy": "困了",
                "thriving": "神清气爽",
            }
            eff_name = effect_names.get(parts["dominant_effect"], parts["dominant_effect"])
            lines.append(f"主要感受：{eff_name}")

        if parts["mood_desc"]:
            lines.append(f"今日心情：{parts['mood_desc']}")
        if parts["energy_desc"]:
            lines.append(f"精力状态：{parts['energy_desc']}")

        shift_hint = parts.get("shift_hint", "")
        if shift_hint:
            lines.append(f"明日倾向：{shift_hint}")

        return "\n".join(lines)

    def _calc_drift(self, parts: dict) -> float:
        mood_map = {
            "低迷": -2.0,
            "低落": -1.5,
            "平稳": 0.0,
            "愉悦": 1.5,
            "疲倦": -2.0,
            "疲惫": -1.0,
            "正常": 0.0,
            "充沛": 1.5,
        }
        base = mood_map.get(parts.get("mood_desc", ""), 0.0)

        trajectory = parts.get("trajectory", "平稳")
        trajectory_drift = {
            "向上": 1.0,
            "有落差": -0.5,
            "有失落": -1.0,
            "向下": -1.5,
            "独处": -0.5,
            "平淡": 0.0,
            "平稳": 0.0,
        }
        base += trajectory_drift.get(trajectory, 0.0)

        good = parts.get("good_interactions", 0)
        bad = parts.get("bad_interactions", 0)
        missed = parts.get("missed", 0)
        connected = parts.get("connected", 0)

        if good > bad * 2:
            base += 1.0
        elif bad > good * 2:
            base -= 1.0

        if missed > 0 and bad > 0:
            base -= 0.5

        if connected > missed and good >= 2:
            base += 0.5

        recovery = parts.get("recovery", False)
        if recovery:
            base += 0.5

        if abs(base) < 0.5:
            return 0.0
        return max(-DRIFT_MAX, min(DRIFT_MAX, base))

    async def _write_long_term_memory(self, scope_id: str, summary: str, date_str: str):
        """写入长期人格记忆（private scope）。"""
        if not self._memory_store:
            return
        try:
            private_scope = f"private_persona_{scope_id}"
            memory_json = f'{{"type":"persona_daily_summary","date":"{date_str}","summary":"{summary.replace(chr(34), chr(34) + chr(34))}"}}'
            await self._memory_store.save_session_event(
                private_scope,
                {
                    "content": f"[人格日结 {date_str}] {summary}",
                    "source": "persona_sim_consolidation",
                    "date": date_str,
                },
            )
            logger.debug(f"[Consolidation] 长期记忆已写入 private_scope={private_scope}")
        except Exception as e:
            logger.warning(f"[Consolidation] 写入长期记忆失败: {e}")
