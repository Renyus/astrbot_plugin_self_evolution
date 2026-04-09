import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....dao import SelfEvolutionDAO


class PersonaArcRuminationService:
    def __init__(self, dao: "SelfEvolutionDAO"):
        self.dao = dao

    async def create_rumination(
        self,
        scope_id: str,
        arc_id: str,
        text: str,
        source_summary: str = "",
    ) -> None:
        """Create a new rumination entry."""
        now = time.time()
        await self.dao.add_persona_arc_rumination(
            scope_id=scope_id,
            arc_id=arc_id,
            text=text,
            source_summary=source_summary,
            created_at=now,
            injected=False,
        )

    async def get_uninjected_ruminations(self, scope_id: str, arc_id: str, limit: int = 1) -> list[dict]:
        """Get ruminations that haven't been injected yet."""
        return await self.dao.get_uninjected_persona_arc_ruminations(scope_id, arc_id, limit=limit)

    async def mark_injected(self, ids: list[int]) -> None:
        """Mark ruminations as injected."""
        if not ids:
            return
        await self.dao.mark_persona_arc_ruminations_injected(ids)

    async def list_ruminations(self, scope_id: str, arc_id: str, limit: int = 10) -> list[dict]:
        """List recent ruminations for a scope+arc."""
        return await self.dao.get_persona_arc_ruminations(scope_id, arc_id, limit=limit)

    async def build_rumination_prompt(self, scope_id: str, arc_id: str) -> str:
        """Build a rumination prompt from uninjected ruminations."""
        ruminations = await self.get_uninjected_ruminations(scope_id, arc_id, limit=3)
        if not ruminations:
            return ""

        lines = []
        for rum in ruminations:
            text = rum.get("text", "")
            if text:
                lines.append(f"- {text}")

        if not lines:
            return ""

        return "\n".join(lines)
