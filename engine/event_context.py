from __future__ import annotations


def extract_interaction_context(message_components, *, persona_name: str, bot_id: str) -> dict:
    """Extract reply/@ interaction hints from a message component list."""
    quoted_info = ""
    ai_context_info = ""
    at_targets = []

    for comp in message_components or []:
        comp_type = type(comp).__name__
        if comp_type == "Reply":
            reply_sender = getattr(comp, "sender_nickname", "")
            reply_sender_id = str(getattr(comp, "sender_id", ""))
            is_ai_reply = reply_sender == persona_name or reply_sender_id == str(bot_id) or reply_sender_id == "AI"
            if is_ai_reply:
                quoted_info = "回复了你"
        elif comp_type == "At":
            at_targets.append(str(getattr(comp, "qq", "")))

    at_info = ""
    if at_targets and ("all" in at_targets or str(bot_id) in at_targets):
        at_info = "at了你"

    return {
        "quoted_info": quoted_info,
        "ai_context_info": ai_context_info,
        "at_targets": at_targets,
        "at_info": at_info,
    }
