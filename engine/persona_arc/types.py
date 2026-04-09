from dataclasses import dataclass, field


@dataclass
class PersonaArcStage:
    stage: int
    name: str
    threshold: float
    prompt: str
    forbidden: tuple[str, ...] = ()


@dataclass
class PersonaArcProfile:
    arc_id: str
    display_name: str
    stages: tuple[PersonaArcStage, ...]
    lore_guard: str = ""

    def get_stage(self, stage_num: int) -> PersonaArcStage:
        for s in self.stages:
            if s.stage == stage_num:
                return s
        return self.stages[0]


@dataclass
class PersonaArcState:
    scope_id: str
    arc_id: str = ""
    arc_stage: int = 0
    arc_progress: float = 0.0
    updated_at: float = field(default_factory=lambda: __import__("time").time())
