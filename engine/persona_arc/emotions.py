import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....dao import SelfEvolutionDAO


class PersonaArcEmotionService:
    def __init__(self, dao: "SelfEvolutionDAO"):
        self.dao = dao

    async def record_emotion(
        self,
        scope_id: str,
        arc_id: str,
        emotion_name: str,
        definition_by_user: str,
        source_text: str = "",
        confidence: float = 0.8,
    ) -> None:
        """Record or update an emotion. Deduplication by scope_id + arc_id + emotion_name."""
        now = time.time()
        await self.dao.upsert_persona_arc_emotion(
            scope_id=scope_id,
            arc_id=arc_id,
            emotion_name=emotion_name,
            definition_by_user=definition_by_user,
            source_text=source_text,
            confidence=confidence,
            unlocked_at=now,
            updated_at=now,
        )

    async def list_emotions(self, scope_id: str, arc_id: str, limit: int = 20) -> list[dict]:
        """List emotions for a scope+arc, ordered by updated_at descending."""
        return await self.dao.get_persona_arc_emotions(scope_id, arc_id, limit=limit)

    async def build_emotion_prompt(self, scope_id: str, arc_id: str, limit: int = 6) -> str:
        """Build a compact emotion prompt from the most recent unlocked emotions."""
        emotions = await self.list_emotions(scope_id, arc_id, limit=limit)
        if not emotions:
            return ""

        lines = []
        for em in emotions:
            name = em.get("emotion_name", "")
            definition = em.get("definition_by_user", "")
            if name and definition:
                lines.append(f"- {name}：{definition}")

        if not lines:
            return ""

        return "\n".join(lines)
