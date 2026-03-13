"""
图像缓存处理模块 - 负责图片哈希计算、描述缓存、标签生成
"""

import hashlib
import aiohttp
import logging

logger = logging.getLogger("astrbot")


class ImageCacheEngine:
    """图像缓存引擎 - 处理图片哈希、描述缓存、标签提取"""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def dao(self):
        return self.plugin.dao

    @property
    def session_manager(self):
        return self.plugin.session_manager

    @property
    def context(self):
        return self.plugin.context

    async def get_image_hash(self, image_path: str) -> str | None:
        """计算图片的 MD5 hash"""
        try:
            if image_path.startswith("http://") or image_path.startswith("https://"):
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        image_path, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status != 200:
                            return None
                        content = await resp.read()
            elif image_path.startswith("file://"):
                path = image_path[7:]
                with open(path, "rb") as f:
                    content = f.read()
            elif image_path.startswith("base64://"):
                import base64

                content = base64.b64decode(image_path[9:])
            else:
                with open(image_path, "rb") as f:
                    content = f.read()
            return hashlib.md5(content).hexdigest()
        except Exception as e:
            logger.warning(f"[ImageCache] 计算图片 hash 失败: {e}")
            return None

    async def process_image_captions(self, event) -> list:
        """处理消息中的图片，获取描述和标签（优先使用缓存）"""
        from astrbot.core.message.components import Image

        image_summaries = []
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        buffer_key = str(group_id) if group_id else f"private_{user_id}"

        try:
            message_obj = getattr(event, "message_obj", None)
            logger.debug(f"[ImageCache] message_obj: {message_obj}")

            if not message_obj:
                logger.debug("[ImageCache] message_obj 为空")
                return image_summaries

            if not hasattr(message_obj, "message"):
                logger.debug("[ImageCache] message_obj 没有 message 属性")
                return image_summaries

            found_image = False
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    found_image = True
                    logger.debug(f"[ImageCache] 找到 Image 组件: {comp}")

                    if hasattr(comp, "url") and comp.url:
                        image_url = comp.url
                    elif hasattr(comp, "path") and comp.path:
                        image_url = comp.path
                    else:
                        try:
                            image_url = await comp.convert_to_file_path()
                        except Exception as e:
                            logger.warning(
                                f"[ImageCache] convert_to_file_path 失败: {e}"
                            )
                            image_url = comp.file if comp.file else ""

                    if not image_url:
                        logger.debug("[ImageCache] image_url 为空")
                        continue

                    img_hash = await self.get_image_hash(image_url)
                    logger.debug(f"[ImageCache] img_hash: {img_hash}")

                    if not img_hash:
                        logger.debug("[ImageCache] img_hash 计算失败")
                        continue

                    cached_summary = await self.dao.get_image_summary(img_hash)
                    logger.debug(f"[ImageCache] cached_summary: {cached_summary}")

                    if cached_summary:
                        image_summaries.append(cached_summary)
                        logger.info(
                            f"[ImageCache] 使用缓存: {img_hash[:8]}... -> {cached_summary}"
                        )
                    else:
                        logger.debug("[ImageCache] 无缓存，等待拦截器处理")

            if not found_image:
                logger.debug("[ImageCache] 没有找到 Image 组件")

            if image_summaries:
                session_buffer = self.session_manager.session_buffers.get(
                    buffer_key, {}
                )
                session_buffer["image_summaries"] = image_summaries
                self.session_manager.session_buffers[buffer_key] = session_buffer
        except Exception as e:
            logger.warning(f"[ImageCache] 处理图片描述失败: {e}")
        return image_summaries

    async def generate_summary(self, caption: str) -> str | None:
        """从完整描述中提取简述和关键词标签"""
        try:
            config = self.context.get_config()

            provider_id = (
                config.get("default_image_caption_provider_id")
                or config.get("provider_settings", {}).get(
                    "default_image_caption_provider_id"
                )
                or config.get("provider_settings", {}).get("default_provider_id")
                or ""
            )

            logger.debug(f"[ImageCache] generate_summary provider_id: {provider_id}")

            if not provider_id:
                logger.warning("[ImageCache] provider_id 未配置")
                return None

            provider = self.context.get_provider_by_id(provider_id)

            logger.debug(f"[ImageCache] provider: {provider}")

            if not provider:
                logger.warning(f"[ImageCache] provider 获取失败: {provider_id}")
                return None

            prompt = f"""根据以下图片描述，请完成两个任务：
1. 用一句话简述图片内容（100字以内）
2. 提取最多10个关键词标签（用 | 分隔）

图片描述：{caption}

输出格式：
简述：<一句话描述>
标签：<tag1 | tag2 | tag3>"""

            logger.debug("[ImageCache] 调用 LLM 生成标签...")

            llm_response = await provider.text_chat(prompt=prompt)

            logger.debug(f"[ImageCache] LLM 响应: {llm_response}")

            summary = llm_response.completion_text.strip()
            logger.debug(f"[ImageCache] summary 原始值: {summary}")

            if summary:
                lines = summary.strip().split("\n")
                description = ""
                tags = []

                for line in lines:
                    line = line.strip()
                    if line.startswith("简述：") or line.startswith("描述："):
                        description = line.split("：", 1)[1].strip()
                    elif line.startswith("标签："):
                        tag_part = line.split("：", 1)[1].strip()
                        tags = [t.strip() for t in tag_part.split("|") if t.strip()]

                if not description:
                    description = caption[:100]

                result_parts = [description] + tags[:10] if tags else [description]
                final_result = f"[{' | '.join(result_parts)}]"
                logger.debug(f"[ImageCache] summary 处理后: {final_result}")
                return final_result
            return None
        except Exception as e:
            logger.warning(f"[ImageCache] 生成标签失败: {e}")
            return None

    async def handle_tool_result(self, event, tool, tool_args, tool_result) -> bool:
        """拦截工具调用结果，用于图片描述缓存"""
        from mcp.types import CallToolResult, TextContent
        from astrbot.core.message.components import Image

        if tool.name != "understand_image" or not tool_result:
            return False

        try:
            if tool_result.content and isinstance(tool_result.content, list):
                first_item = tool_result.content[0]
                if isinstance(first_item, TextContent):
                    caption = first_item.text.strip()
                elif hasattr(first_item, "text"):
                    caption = first_item.text.strip()
                else:
                    return False
            else:
                return False

            if not caption:
                return False

            message_obj = getattr(event, "message_obj", None)
            if not message_obj or not hasattr(message_obj, "message"):
                return False

            img_hash = None
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    if hasattr(comp, "url") and comp.url:
                        img_hash = await self.get_image_hash(comp.url)
                    elif hasattr(comp, "path") and comp.path:
                        img_hash = await self.get_image_hash(comp.path)
                    if not img_hash:
                        try:
                            local_path = await comp.convert_to_file_path()
                            img_hash = await self.get_image_hash(local_path)
                        except:
                            pass
                    break

            if not img_hash:
                return False

            existing = await self.dao.get_image_summary(img_hash)
            if existing:
                return False

            summary = await self.generate_summary(caption)
            summary = summary if summary else f"[{caption}]"
            await self.dao.add_image_cache(img_hash, caption, summary)

            group_id = event.get_group_id()
            user_id = event.get_sender_id()
            buffer_key = str(group_id) if group_id else f"private_{user_id}"
            session_buffer = self.session_manager.session_buffers.get(buffer_key, {})
            session_buffer["image_summaries"] = [summary]
            self.session_manager.session_buffers[buffer_key] = session_buffer

            logger.info(f"[ImageCache] 拦截并缓存: {img_hash[:8]}... -> {summary}")
            return True
        except Exception as e:
            logger.warning(f"[ImageCache] 拦截工具结果失败: {e}")
            return False

    async def list_caches(self, limit: int = 20, offset: int = 0) -> list:
        """分页查看图片缓存"""
        return await self.dao.list_image_caches(limit, offset)

    async def cleanup_old_caches(self, days: int = 30) -> int:
        """清理 N 天前的图片缓存"""
        return await self.dao.cleanup_image_cache(days)

    async def flush_all_caches(self) -> int:
        """删除全部图片缓存"""
        return await self.dao.flush_image_cache()

    async def delete_cache(self, image_hash: str) -> str:
        """删除指定的图片描述缓存"""
        success = await self.dao.delete_image_cache(image_hash)
        if success:
            return f"已删除图片缓存: {image_hash}"
        return f"未找到图片缓存: {image_hash}"
