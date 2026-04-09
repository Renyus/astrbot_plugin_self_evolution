from ..types import PersonaArcProfile

PROFILES: dict[str, PersonaArcProfile] = {}


def register_profile(profile: PersonaArcProfile) -> None:
    PROFILES[profile.arc_id] = profile


def get_profile(arc_id: str) -> PersonaArcProfile | None:
    return PROFILES.get(arc_id)


from . import amphoreus_demurge
