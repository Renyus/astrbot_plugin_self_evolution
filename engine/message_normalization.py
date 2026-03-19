from __future__ import annotations

import hashlib


async def normalize_event_message_text(event, dao) -> tuple[str, bool]:
    """Normalize an incoming event into stable plain text for downstream cognition logic."""
    message_obj = getattr(event, "message_obj", None)
    message_chain = getattr(message_obj, "message", None)

    has_image = False
    image_base64 = None

    if message_chain:
        for comp in message_chain:
            if hasattr(comp, "convert_to_base64"):
                has_image = True
                try:
                    image_base64 = await comp.convert_to_base64()
                except Exception:
                    image_base64 = None
                break

    if not has_image:
        return event.message_str or "", False

    group_id = event.get_group_id() if hasattr(event, "get_group_id") else None
    if group_id and image_base64 and dao:
        img_hash = hashlib.md5(image_base64.encode()).hexdigest()
        sticker = await dao.get_sticker_by_hash(img_hash)
        if sticker and sticker.get("description"):
            return f"[{sticker['description']}]", True
        if sticker and sticker.get("tags"):
            return f'[收到一张"{sticker["tags"]}"表情包]', True

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
