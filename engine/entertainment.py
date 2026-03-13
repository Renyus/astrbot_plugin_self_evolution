"""
娱乐功能模块 - 包含表情包学习和今日老婆等娱乐指令
"""

import asyncio
import base64
import hashlib
import random
import time
from astrbot.api import logger


class EntertainmentEngine:
    """娱乐功能引擎"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._last_tag_time = 0
        self._last_send_time = {}

    @property
    def dao(self):
        return self.plugin.dao

    @property
    def cfg(self):
        return self.plugin.cfg

    # ========== 今日老婆 ==========

    async def today_waifu(self, event) -> list:
        """今日老婆功能 - 随机抽取一名群友"""
        group_id = event.get_group_id()
        if not group_id:
            return ["此指令仅限群聊使用"]

        logger.info(f"[Entertainment] 今日老婆指令，群 {group_id}")

        try:
            group = await event.get_group(group_id)
            if not group or not group.members:
                return ["获取群成员失败"]

            members = group.members
            if not members:
                return ["群里没有成员"]

            selected = random.choice(members)
            user_id = selected.user_id
            nickname = selected.nickname or user_id

            logger.info(f"[Entertainment] 今日老婆抽取结果: {nickname} ({user_id})")

            avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

            return [f"今日老婆是：{nickname}！", avatar_url]

        except Exception as e:
            logger.warning(f"[Entertainment] 今日老婆功能异常: {e}")
            return [f"功能异常: {e}"]

    # ========== 表情包学习 ==========

    async def learn_sticker_from_event(self, event) -> bool:
        """从消息事件中学习表情包（检测指定人的图片并保存）"""
        if not self.cfg.sticker_learning_enabled:
            return False

        target_qq = self.cfg.sticker_target_qq
        if not target_qq:
            return False

        target_qq_list = [qq.strip() for qq in target_qq.split(",") if qq.strip()]
        if not target_qq_list:
            return False

        group_id = event.get_group_id()
        user_id = str(event.get_sender_id())

        if user_id not in target_qq_list:
            return False

        try:
            from astrbot.core.message.components import Image

            message_obj = getattr(event, "message_obj", None)
            if not message_obj or not hasattr(message_obj, "message"):
                return False

            for comp in message_obj.message:
                if isinstance(comp, Image):
                    try:
                        base64_data = await comp.convert_to_base64()
                    except Exception as e:
                        logger.warning(f"[Sticker] 获取图片Base64失败: {e}")
                        continue

                    sticker_hash = hashlib.md5(base64_data.encode()).hexdigest()

                    daily_count = await self.dao.get_today_sticker_count(group_id)
                    if daily_count >= self.cfg.sticker_daily_limit:
                        logger.info(
                            f"[Sticker] 今日已达上限 {self.cfg.sticker_daily_limit}"
                        )
                        return False

                    total_count = await self.dao.get_sticker_count(group_id)
                    if total_count >= self.cfg.sticker_total_limit:
                        await self.dao.delete_oldest_sticker()
                        logger.info(f"[Sticker] 已达总上限，删除最旧的")

                    success = await self.dao.add_sticker(group_id, user_id, base64_data)
                    if success:
                        logger.info(
                            f"[Sticker] 成功学习表情包: user={user_id}, group={group_id}"
                        )
                        return True
                    else:
                        logger.debug(f"[Sticker] 表情包已存在: hash={sticker_hash}")

        except Exception as e:
            logger.warning(f"[Sticker] 学习表情包异常: {e}")

        return False

    async def tag_stickers(self) -> bool:
        """给未打标签的表情包打标签（有冷却时间）"""
        if not self.cfg.sticker_learning_enabled:
            return False

        now = time.time()
        cooldown_seconds = self.cfg.sticker_tag_cooldown * 60

        if now - self._last_tag_time < cooldown_seconds:
            logger.debug(
                f"[Sticker] 打标签冷却中，剩余 {int(cooldown_seconds - (now - self._last_tag_time))} 秒"
            )
            return False

        untagged = await self.dao.get_untagged_stickers(1)
        if not untagged:
            logger.debug(f"[Sticker] 没有未打标签的表情包")
            return False

        sticker = untagged[0]
        logger.info(f"[Sticker] 准备给表情包打标签: id={sticker['id']}")

        try:
            from astrbot.core.message.components import Image

            img_data = base64.b64decode(sticker["base64_data"])
            img_bytes = img_data

            provider = self.plugin.context.get_using_provider()
            if not provider:
                logger.warning(f"[Sticker] 获取 provider 失败")
                return False

            prompt = f"""请用一句话描述这张图片的内容，然后提取3-5个关键词标签（用|分隔）。

输出格式：
描述：<一句话描述>
标签：<tag1|tag2|tag3>"""

            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

            # 检测图片类型
            def detect_image_type(data):
                if len(data) < 4:
                    return "jpeg"
                # 检查常见图片格式的头字节
                if data[:2] == b"\xff\xd8":
                    return "jpeg"
                elif data[:8] == b"\x89PNG\r\n\x1a\n":
                    return "png"
                elif data[:4] == b"GIF87a" or data[:4] == b"GIF89a":
                    return "gif"
                elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                    return "webp"
                elif data[:2] == b"BM":
                    return "bmp"
                else:
                    return "jpeg"  # 默认

            mime = f"image/{detect_image_type(img_bytes)}"

            resp = await provider.text_chat(
                prompt=prompt,
                image_urls=[f"data:{mime};base64,{img_base64}"],
            )

            if not resp or not resp.completion_text:
                logger.warning(f"[Sticker] LLM 响应为空")
                return False

            response_text = resp.completion_text.strip()
            logger.info(f"[Sticker] LLM 响应: {response_text}")

            tags = ""
            if "标签：" in response_text:
                tags = response_text.split("标签：")[1].strip()
            elif "标签:" in response_text:
                tags = response_text.split("标签:")[1].strip()

            if not tags:
                tags = response_text.split("\n")[0][:50]

            await self.dao.update_sticker_tags(sticker["id"], tags)
            self._last_tag_time = time.time()
            logger.info(f"[Sticker] 打标签成功: id={sticker['id']}, tags={tags}")
            return True

        except Exception as e:
            logger.warning(f"[Sticker] 打标签异常: {e}")
            return False

    async def should_send_sticker(self, group_id: str) -> bool:
        """判断当前是否应该发表情包"""
        if not self.cfg.sticker_learning_enabled:
            return False

        cooldown_seconds = self.cfg.sticker_send_cooldown * 60
        last_time = self._last_send_time.get(group_id, 0)
        if time.time() - last_time < cooldown_seconds:
            logger.debug(
                f"[Sticker] 发表情包冷却中，剩余 {int(cooldown_seconds - (time.time() - last_time))} 秒"
            )
            return False

        sticker = await self.dao.get_random_sticker(group_id)
        return sticker is not None

    async def get_sticker_for_sending(self, group_id: str) -> dict | None:
        """获取要发送的表情包"""
        sticker = await self.dao.get_random_sticker(group_id)
        if sticker:
            self._last_send_time[group_id] = time.time()
        return sticker

    async def list_stickers(
        self, group_id: str, tags: str = None, limit: int = 10
    ) -> list:
        """列出表情包"""
        return await self.dao.get_stickers_by_tags(tags, group_id, limit)

    async def get_sticker_stats(self, group_id: str = None) -> dict:
        """获取表情包统计"""
        return await self.dao.get_sticker_stats(group_id)

    async def get_prompt_injection(self, group_id: str) -> str:
        """获取表情包相关的 prompt 注入"""
        if not self.cfg.sticker_learning_enabled:
            return ""

        stats = await self.get_sticker_stats(group_id)
        if stats["total"] == 0:
            return ""

        injection = f"\n\n【表情包库】你有一个表情包库，目前有 {stats['total']} 张表情包（今日新增 {stats['today']} 张）。"
        injection += (
            "\n当群聊氛围适合时，可以使用 send_sticker 工具发送表情包来活跃气氛。"
        )
        injection += "\n使用 list_stickers 工具可以查看可用的表情包。"

        return injection
