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

import asyncio
import dataclasses
import hashlib
import os
import uuid

from astrbot.api import logger

from .media_extractor import MediaKind, MediaOrigin, MediaTarget

try:
    from astrbot.core.utils.io import download_image_by_url
except ImportError:
    download_image_by_url = None


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


async def _download_and_hash(target: MediaTarget) -> tuple[str, str]:
    """下载图片/视频封面并计算内容hash。

    Returns (content_hash, local_path)。
    content_hash 用于做缓存key，不管URL怎么变，相同内容产生相同hash。
    """
    url_to_download = None

    for c in target.resource_candidates:
        if c.url and (c.url.startswith("http://") or c.url.startswith("https://")):
            url_to_download = c.url
            break

    if not url_to_download:
        for c in target.resource_candidates:
            if c.cover and (c.cover.startswith("http://") or c.cover.startswith("https://")):
                url_to_download = c.cover
                break

    local_path = None
    if url_to_download and download_image_by_url:
        try:
            local_path = await asyncio.wait_for(
                download_image_by_url(url_to_download),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[CaptionService] 下载超时: {url_to_download[:50]}")
        except Exception as e:
            logger.warning(f"[CaptionService] 下载失败: {e} {url_to_download[:50]}")

    if not local_path:
        for c in target.resource_candidates:
            f = c.file or ""
            if f.startswith("file:///"):
                local_path = f[8:]
                break
            elif f.startswith("/") and len(f) < 256:
                if os.path.exists(f):
                    local_path = f
                    break

    if not local_path:
        for c in target.resource_candidates:
            if c.url:
                return f"url_hash:{hashlib.md5(c.url.encode()).hexdigest()}", ""

    if not local_path:
        return "", ""

    try:
        with open(local_path, "rb") as f:
            content_hash = hashlib.md5(f.read()).hexdigest()
        return content_hash, local_path
    except Exception as e:
        logger.warning(f"[CaptionService] 计算文件hash失败: {e} {local_path}")
        return "", local_path


async def get_caption_for_target(
    target: MediaTarget,
    plugin_context,
    dao=None,
    prompt_override: str | None = None,
) -> CaptionResult:
    """对单个 MediaTarget 调用图片理解 provider，返回 CaptionResult。

    Caption service 只负责取描述，不管审核。
    text 字段是"中立描述"，即使包含 NSFW 暗示也不在此层处理。

    缓存策略：使用图片内容hash作为缓存key，URL变化不影响缓存命中。

    Args:
        prompt_override: 如果提供，使用此 prompt 而非配置文件中的 image_caption_prompt。
                        用于需要特定格式输出的场景（如 feed 食物分类）。
    """
    result = CaptionResult(
        kind=target.kind,
        origin=target.origin,
    )

    if not target.can_process_now:
        result.reason = target.reason or "missing_resource"
        return result

    cache_key, local_path = await _download_and_hash(target)
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

    if not local_path:
        result.reason = "no_processable_url"
        return result

    cfg = getattr(plugin_context, "_cfg", None)
    if cfg is None:
        try:
            cfg = plugin_context.get_config().get("provider_settings", {})
        except Exception:
            cfg = {}

    prov_id = cfg.get("default_image_caption_provider_id", "")
    caption_prompt = prompt_override or cfg.get(
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
    result.resource_key = local_path

    try:
        model_name = getattr(provider, "model", "") or getattr(provider, "model_name", "") or ""
        result.model_name = model_name
    except Exception:
        pass

    try:
        resp = await provider.text_chat(
            prompt=caption_prompt,
            session_id=uuid.uuid4().hex,
            image_urls=[local_path],
            persist=False,
        )
        result.text = resp.completion_text or ""
        result.success = bool(result.text)
        if not result.success:
            result.reason = "provider_returned_empty"
        if result.success and dao and cache_key:
            ttl = 0
            try:
                await dao.set_caption_cache(cache_key, result.text, result.provider_id, result.model_name, ttl)
            except Exception:
                pass
    except Exception as e:
        result.reason = f"provider_error:{e}"
        result.success = False

    return result
