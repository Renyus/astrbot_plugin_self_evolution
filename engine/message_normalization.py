from __future__ import annotations


async def normalize_event_message_text(event, dao) -> tuple[str, bool]:
    """Normalize an incoming event into stable plain text for downstream cognition logic."""
    message_obj = getattr(event, "message_obj", None)
    message_chain = getattr(message_obj, "message", None)

    has_image = False

    if message_chain:
        try:
            from astrbot.core.message.components import Image

            for comp in message_chain:
                if isinstance(comp, Image):
                    has_image = True
                    break
        except (ImportError, ModuleNotFoundError):
            for comp in message_chain:
                if hasattr(comp, "url"):
                    has_image = True
                    break

    if not has_image:
        return event.message_str or "", False

    return "[图片]", True


async def ensure_event_message_text(event, dao) -> str:
    """Return normalized event text and cache it back onto the event when possible."""
    cached = None
    if hasattr(event, "get_extra"):
        cached = event.get_extra("self_evolution_message_text", None)

    if cached is not None:
        return cached or event.message_str or ""

    msg_text, has_image = await normalize_event_message_text(event, dao)

    if hasattr(event, "set_extra"):
        event.set_extra("self_evolution_message_text", msg_text or "")
    setattr(event, "_image_processed", has_image)

    return msg_text or ""
