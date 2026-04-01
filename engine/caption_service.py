"""
Phase 2: 图片理解服务（Caption Service）

职责：
- 输入 ResolvedMedia（MediaTarget），产出 CaptionResult
- 调用图片理解 provider，支持 caption cache 复用
- 只做"取描述"，不审核、不处罚

输出 CaptionResult：
- text: str  # 中立描述，不是审核结论
- provider_id: str
- model_name: str
- resource_key: str  # 具体用了哪个 url/file/cover
- kind: MediaKind
- origin: MediaOrigin
- success: bool
- reason: str  # 失败原因
- cache_hit: bool
"""

import dataclasses
import uuid

from astrbot.api import logger

from .media_extractor import MediaKind, MediaOrigin, MediaTarget


@dataclasses.dataclass
class CaptionResult:
    text: str = ""
    provider_id: str = ""
    model_name: str = ""
    resource_key: str = ""
    kind: MediaKind = MediaKind.UNKNOWN_MEDIA
    origin: MediaOrigin = MediaOrigin.MESSAGE
    success: bool = False
    reason: str = ""
    cache_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "resource_key": self.resource_key[:40] if self.resource_key else "",
            "kind": self.kind.value,
            "origin": self.origin.value,
            "success": self.success,
            "reason": self.reason,
            "cache_hit": self.cache_hit,
        }


def _build_cache_key(target: MediaTarget) -> str:
    """Build cache key from MediaTarget.

    Priority: file_id > file_unique > url > file
    Only permanent identifiers get TTL=0 (permanent cache).
    """
    for c in target.resource_candidates:
        if c.file_id:
            return f"fid:{c.file_id}"
        if c.file_unique:
            return f"fuid:{c.file_unique}"
    for c in target.resource_candidates:
        if c.url:
            return f"url:{c.url}"
    for c in target.resource_candidates:
        if c.file and (
            c.file.startswith("http")
            or c.file.startswith("file:///")
            or c.file.startswith("base64://")
            or len(c.file) >= 32
        ):
            return f"file:{c.file[:128]}"
    return ""


def _pick_best_url(target: MediaTarget) -> tuple[str, str]:
    """从 MediaTarget 的 resource_candidates 里挑最优的 URL。

    优先级：url > cover > file（如果是 http）
    返回 (url, resource_key)
    """
    for c in target.resource_candidates:
        if c.url and (c.url.startswith("http://") or c.url.startswith("https://")):
            return c.url, "url"
        if c.cover and (c.cover.startswith("http://") or c.cover.startswith("https://")):
            return c.cover, "cover"
        if c.file and (c.file.startswith("http://") or c.file.startswith("https://")):
            return c.file, "file"

    for c in target.resource_candidates:
        if c.file and not any(
            ext in c.file.lower() for ext in ("mp4", "mov", "avi", "mkv", "wmv", "webm", "flv", "m4v")
        ):
            if len(c.file) < 256:
                return c.file, "file"

    return "", ""


async def get_caption_for_target(
    target: MediaTarget,
    plugin_context,
    dao=None,
) -> CaptionResult:
    """对单个 MediaTarget 调用图片理解 provider，返回 CaptionResult。

    Caption service 只负责取描述，不管审核。
    text 字段是"中立描述"，即使包含 NSFW 暗示也不在此层处理。
    """
    result = CaptionResult(
        kind=target.kind,
        origin=target.origin,
    )

    if not target.can_process_now:
        result.reason = target.reason or "missing_resource"
        return result

    url, resource_key = _pick_best_url(target)
    if not url:
        result.reason = "no_processable_url"
        return result

    cache_key = _build_cache_key(target)
    cached = None
    if dao and cache_key:
        try:
            cached = await dao.get_caption_cache(cache_key)
        except Exception:
            pass
        if cached:
            cap_text, prov_id, model_nm = cached
            result.text = cap_text
            result.provider_id = prov_id
            result.model_name = model_nm
            result.success = True
            result.reason = "cache_hit"
            result.cache_hit = True
            return result

    cfg = getattr(plugin_context, "_cfg", None)
    if cfg is None:
        try:
            cfg = plugin_context.get_config().get("provider_settings", {})
        except Exception:
            cfg = {}

    prov_id = cfg.get("default_image_caption_provider_id", "")
    caption_prompt = cfg.get(
        "image_caption_prompt",
        "Please describe the image using Chinese.",
    )

    if not prov_id:
        result.reason = "no_caption_provider_configured"
        return result

    provider = None
    try:
        provider = plugin_context.get_provider_by_id(prov_id)
    except Exception as e:
        logger.debug(f"[CaptionService] get_provider_by_id failed: {e}")

    if not provider:
        result.reason = f"provider_not_found:{prov_id}"
        return result

    result.provider_id = prov_id
    result.resource_key = resource_key

    try:
        model_name = getattr(provider, "model", "") or getattr(provider, "model_name", "") or ""
        result.model_name = model_name
    except Exception:
        pass

    try:
        resp = await provider.text_chat(
            prompt=caption_prompt,
            session_id=uuid.uuid4().hex,
            image_urls=[url],
            persist=False,
        )
        result.text = resp.completion_text or ""
        result.success = bool(result.text)
        if not result.success:
            result.reason = "provider_returned_empty"
        if result.success and dao and cache_key:
            ttl = 0 if (":" in cache_key and cache_key.startswith(("fid:", "fuid:"))) else 86400
            try:
                await dao.set_caption_cache(cache_key, result.text, result.provider_id, result.model_name, ttl)
            except Exception:
                pass
    except Exception as e:
        result.reason = f"provider_error:{e}"
        result.success = False

    return result
