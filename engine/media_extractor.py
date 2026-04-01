"""
Phase 1: 消息媒体目标抽取

只做监听与抽取，不调用模型、不审核、不写库。

职责：
- 遍历 event.get_messages()
- 解析主消息 / 回复 / 转发里的媒体组件
- 输出统一的 media targets 结构

输出结构 MediaTarget：
- kind: image / video / gif / unknown_media
- origin: message / reply / forward
- group_id / user_id / message_id
- resource_candidates: [{url, file, cover, file_id, file_unique, raw_component_type}]
- can_process_now: bool
- reason: str
"""

import enum
from dataclasses import dataclass, field
from typing import Optional

from astrbot.api import logger


class MediaKind(enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    GIF = "gif"
    UNKNOWN_MEDIA = "unknown_media"


class MediaOrigin(enum.Enum):
    MESSAGE = "message"
    REPLY = "reply"
    FORWARD = "forward"


@dataclass
class ResourceCandidate:
    url: Optional[str] = None
    file: Optional[str] = None
    cover: Optional[str] = None
    file_id: Optional[str] = None
    file_unique: Optional[str] = None
    raw_component_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "file": self.file,
            "cover": self.cover,
            "file_id": self.file_id,
            "file_unique": self.file_unique,
            "raw_component_type": self.raw_component_type,
        }


@dataclass
class MediaTarget:
    kind: MediaKind
    origin: MediaOrigin
    group_id: str
    user_id: str
    message_id: str
    resource_candidates: list[ResourceCandidate] = field(default_factory=list)
    can_process_now: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "origin": self.origin.value,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "message_id": self.message_id,
            "resource_candidates": [r.to_dict() for r in self.resource_candidates],
            "can_process_now": self.can_process_now,
            "reason": self.reason,
        }


def _determine_kind(comp) -> MediaKind:
    """根据组件类型判断媒体种类。"""
    comp_type = getattr(comp, "type", None)
    if comp_type is not None:
        t = str(comp_type).lower()
        if t == "image":
            return MediaKind.IMAGE
        if t == "video":
            return MediaKind.VIDEO
        if t == "gif":
            return MediaKind.GIF

    cls_name = type(comp).__name__.lower()
    if "image" in cls_name:
        return MediaKind.IMAGE
    if "video" in cls_name:
        return MediaKind.VIDEO
    if "gif" in cls_name:
        return MediaKind.GIF

    return MediaKind.UNKNOWN_MEDIA


def _is_video_file(file_val: Optional[str]) -> bool:
    if not file_val:
        return False
    ext = file_val.lower().rsplit(".", 1)[-1] if "." in file_val else ""
    return ext in ("mp4", "mov", "avi", "mkv", "webm", "wmv", "flv", "m4v")


def _is_reliable_file_id(file_val: Optional[str]) -> bool:
    """判断 file 字段是否为可靠的文件标识符（而不是裸露的文件路径或临时URL）。"""
    if not file_val:
        return False
    if file_val.startswith("http://") or file_val.startswith("https://"):
        return False
    if file_val.startswith("file:///:"):
        return False
    if file_val.startswith("base64://"):
        return False
    if len(file_val) < 32:
        return False
    return True


def _build_resource_candidate(comp) -> ResourceCandidate:
    """从任意媒体组件提取 resource_candidate。"""
    r = ResourceCandidate()
    r.raw_component_type = type(comp).__name__

    if hasattr(comp, "url") and comp.url:
        r.url = comp.url
    if hasattr(comp, "file") and comp.file:
        r.file = comp.file
    if hasattr(comp, "cover") and comp.cover:
        r.cover = comp.cover
    if hasattr(comp, "id") and comp.id is not None:
        r.file_id = str(comp.id)
    if hasattr(comp, "file_unique") and comp.file_unique:
        r.file_unique = comp.file_unique

    return r


def _compute_can_process(candidates: list[ResourceCandidate]) -> tuple[bool, str]:
    """判断当前候选资源是否已足够处理。

    收紧规则：
    - url（http/https）：可靠，provider 可直接访问
    - cover（http/https）：可靠
    - file_id / file_unique：永久标识符，可靠
    - file（http/https/file:///base64://）：需要检查
    - file（裸字符串且<32）：不可靠，拒绝
    """
    has_url = False
    has_cover = False
    has_file_id = False
    has_reliable_file = False

    for c in candidates:
        if c.url and c.url.startswith("http"):
            has_url = True
        if c.cover and c.cover.startswith("http"):
            has_cover = True
        if c.file_id or c.file_unique:
            has_file_id = True
        if c.file and (
            c.file.startswith("http")
            or c.file.startswith("file:///")
            or c.file.startswith("base64://")
            or _is_reliable_file_id(c.file)
        ):
            has_reliable_file = True

    if has_url or has_cover or has_file_id or has_reliable_file:
        return True, ""

    if not candidates:
        return False, "missing_resource"

    has_video = any(_is_video_file(c.file) for c in candidates if c.file)
    if has_video:
        has_cover_url = any(c.cover for c in candidates if c.cover and c.cover.startswith("http"))
        has_file_url = any(c.file and c.file.startswith("http") for c in candidates if c.file)
        if not (has_cover_url or has_file_url):
            return False, "video_without_cover"

    return False, "missing_url"


def _make_target_from_component(
    comp,
    origin: MediaOrigin,
    group_id: str,
    user_id: str,
    message_id: str,
) -> Optional[MediaTarget]:
    """从单个媒体组件构造 MediaTarget。"""
    kind = _determine_kind(comp)
    if kind == MediaKind.UNKNOWN_MEDIA:
        return None

    r = _build_resource_candidate(comp)
    candidates = [r]
    can_now, reason = _compute_can_process(candidates)

    return MediaTarget(
        kind=kind,
        origin=origin,
        group_id=group_id,
        user_id=user_id,
        message_id=message_id,
        resource_candidates=candidates,
        can_process_now=can_now,
        reason=reason,
    )


def _resolve_bot_client(event) -> Optional[callable]:
    """从 event 解析出可用的 call_action 函数。"""
    try:
        from astrbot.core.utils.quoted_message.onebot_client import OneBotClient

        client = OneBotClient(event)
        if client._call_action is not None:
            return client
    except Exception as e:
        logger.debug(f"[MediaExtractor] OneBotClient 初始化失败: {e}")
    return None


def _make_target_from_raw_dict(
    d: dict,
    origin: MediaOrigin,
    group_id: str,
    user_id: str,
    message_id: str,
) -> Optional[MediaTarget]:
    """从 get_msg / get_forward 返回的原始字典构造 MediaTarget。

    处理 OneBot 消息段格式：
    - {type: "image", data: {file: "...", url: "...", id: "...", file_unique: "..."}}
    - {type: "video", data: {file: "...", url: "...", cover: "..."}}
    """
    seg_type = d.get("type", "")
    data = d.get("data") or {}

    if seg_type == "image":
        kind = MediaKind.IMAGE
    elif seg_type == "video":
        kind = MediaKind.VIDEO
    elif seg_type == "gif":
        kind = MediaKind.GIF
    else:
        kind = MediaKind.UNKNOWN_MEDIA

    if kind == MediaKind.UNKNOWN_MEDIA:
        return None

    r = ResourceCandidate()
    r.raw_component_type = seg_type
    r.url = data.get("url")
    r.file = data.get("file")
    r.cover = data.get("cover")
    file_id_val = data.get("id") or data.get("file_id")
    if file_id_val is not None:
        r.file_id = str(file_id_val)
    r.file_unique = data.get("file_unique")

    candidates = [r]
    can_now, reason = _compute_can_process(candidates)

    return MediaTarget(
        kind=kind,
        origin=origin,
        group_id=group_id,
        user_id=user_id,
        message_id=message_id,
        resource_candidates=candidates,
        can_process_now=can_now,
        reason=reason,
    )


async def extract_media_targets(event) -> list[MediaTarget]:
    """从消息事件中抽取所有媒体目标。

    覆盖三类来源：
    - 主消息体（图片 / 视频）
    - 回复原消息（图片 / 视频）
    - 转发节点（图片 / 视频）
    """
    targets: list[MediaTarget] = []

    group_id = event.get_group_id() or ""
    user_id = str(event.get_sender_id() or "")
    message_id = ""
    try:
        raw_msg = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if raw_msg and isinstance(raw_msg, dict):
            message_id = str(raw_msg.get("message_id", ""))
        if not message_id:
            message_id = str(getattr(event, "message_id", ""))
        if not message_id:
            message_id = str(event.get_id() if hasattr(event, "get_id") else "")
    except Exception:
        pass
    message_id = message_id or ""

    msg_chain = event.get_messages()

    for comp in msg_chain:
        target = _make_target_from_component(comp, MediaOrigin.MESSAGE, group_id, user_id, message_id)
        if target:
            targets.append(target)

    ob_client = _resolve_bot_client(event)

    for comp in msg_chain:
        if getattr(comp, "type", None) == "Reply":
            chain = getattr(comp, "chain", None)
            if chain:
                for inner in chain:
                    inner_target = _make_target_from_component(inner, MediaOrigin.REPLY, group_id, user_id, message_id)
                    if inner_target:
                        targets.append(inner_target)

            reply_id = str(getattr(comp, "id", "") or "")
            if reply_id and ob_client and not any(t.origin == MediaOrigin.REPLY for t in targets):
                try:
                    payload = await ob_client.get_msg(reply_id)
                    if payload and "data" in payload:
                        msgs = payload["data"].get("message", [])
                    elif isinstance(payload, dict):
                        msgs = payload.get("message", [])
                    else:
                        msgs = []
                    for inner in msgs:
                        inner_target = _make_target_from_raw_dict(
                            inner, MediaOrigin.REPLY, group_id, user_id, message_id
                        )
                        if inner_target:
                            targets.append(inner_target)
                except Exception as e:
                    logger.debug(f"[MediaExtractor] 获取回复消息失败: {e}")

    for comp in msg_chain:
        if getattr(comp, "type", None) == "Forward":
            forward_id = str(getattr(comp, "id", "") or "")
            if not forward_id:
                continue

            extracted_from_local = False
            nested = getattr(comp, "data", None)
            if nested:
                nodes = getattr(nested, "nodes", []) or []
                if nodes:
                    for node in nodes:
                        for inner in getattr(node, "message", []) or []:
                            inner_target = _make_target_from_component(
                                inner, MediaOrigin.FORWARD, group_id, user_id, message_id
                            )
                            if inner_target:
                                targets.append(inner_target)
                                extracted_from_local = True

            if ob_client and not extracted_from_local:
                try:
                    fwd_payload = await ob_client.get_forward_msg(forward_id)
                    if fwd_payload:
                        nodes = fwd_payload.get("messages", [])
                        for node in nodes:
                            for inner in node.get("message", []) or []:
                                inner_target = _make_target_from_raw_dict(
                                    inner, MediaOrigin.FORWARD, group_id, user_id, message_id
                                )
                                if inner_target:
                                    targets.append(inner_target)
                except Exception as e:
                    logger.debug(f"[MediaExtractor] 获取转发消息失败: {e}")

    return targets
