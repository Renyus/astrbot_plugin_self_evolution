"""
娱乐功能模块 - 包含表情包学习和今日老婆等娱乐指令
"""

import hashlib
import mimetypes
import random
import time
from pathlib import Path

import aiofiles
import aiohttp

from astrbot.api import logger


class EntertainmentEngine:
    """娱乐功能引擎"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._last_send_time = {}
        self._image_freq_cache: dict[str, dict[str, int]] = {}
        self._banquet_timestamps: dict[str, list[float]] = {}

    @property
    def dao(self):
        return self.plugin.dao

    @property
    def sticker_store(self):
        return self.plugin.sticker_store

    @property
    def cfg(self):
        return self.plugin.cfg

    @property
    def stickers_dir(self) -> Path:
        return self.plugin.stickers_dir

    # ========== 今日老婆 ==========

    async def today_waifu(self, event) -> list:
        """今日老婆功能 - 随机抽取一名群友"""
        if not getattr(self.cfg, "entertainment_enabled", True):
            return ["娱乐模块当前已关闭"]
        group_id = event.get_group_id()
        if not group_id:
            return ["此指令仅限群聊使用"]

        logger.debug(f"[Entertainment] 今日老婆指令，群 {group_id}")

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

            logger.debug(f"[Entertainment] 今日老婆抽取结果: {nickname} ({user_id})")

            avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

            return [f"你今日的群友老婆是：{nickname}！", avatar_url]

        except Exception as e:
            logger.warning(f"[Entertainment] 今日老婆功能异常: {e}")
            return [f"功能异常: {e}"]

    # ========== 表情包学习 ==========

    async def learn_sticker_from_event(self, event) -> bool:
        """从消息事件中学习表情包（检测指定人的图片并保存）"""
        if not getattr(self.cfg, "entertainment_enabled", True):
            return False
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

            raw_msg = getattr(event.message_obj, "raw_message", None)
            image_sub_types: dict[str, int] = {}
            image_sources: dict[str, str] = {}
            if raw_msg and hasattr(raw_msg, "get"):
                raw_msg_list = raw_msg.get("message")
                if raw_msg_list:
                    for seg in raw_msg_list:
                        if isinstance(seg, dict):
                            seg_type = seg.get("type")
                            if seg_type == "image":
                                seg_data = seg.get("data", {})
                                if isinstance(seg_data, dict):
                                    img_file = seg_data.get("file", "")
                                    img_sub_type = seg_data.get("sub_type", 0)
                                    img_url = seg_data.get("url", "")
                                    if img_file:
                                        image_sub_types[img_file] = img_sub_type
                                        image_sources[img_file] = img_url

            for comp in message_obj.message:
                if not isinstance(comp, Image):
                    continue

                comp_file = getattr(comp, "file", "") or ""
                sub_type = image_sub_types.get(comp_file, 0)
                img_url = image_sources.get(comp_file, "")

                if not comp_file:
                    logger.debug(f"[Sticker] 未找到图片file ID，跳过")
                    continue

                if not img_url:
                    logger.debug(f"[Sticker] 未找到图片URL，跳过: file={comp_file}")
                    continue

                sticker_hash = hashlib.md5(comp_file.encode()).hexdigest()

                if sub_type == 0:
                    freq_threshold = self.cfg.sticker_freq_threshold
                    if freq_threshold > 0:
                        user_cache = self._image_freq_cache.setdefault(user_id, {})
                        current_count = user_cache.get(sticker_hash, 0) + 1
                        user_cache[sticker_hash] = current_count

                        if current_count < freq_threshold:
                            logger.debug(
                                f"[Sticker] sub_type=0 频率不足跳过: user={user_id}, group={group_id}, "
                                f"count={current_count}/{freq_threshold}, hash={sticker_hash[:8]}"
                            )
                            continue

                        logger.debug(
                            f"[Sticker] sub_type=0 频率达标学习: user={user_id}, group={group_id}, "
                            f"count={current_count}/{freq_threshold}"
                        )
                    else:
                        logger.debug(f"[Sticker] sub_type=0 普通图片，跳过: user={user_id}, group={group_id}")
                        continue

                learned = await self._save_sticker(group_id, user_id, img_url, sticker_hash)
                if learned:
                    self._image_freq_cache.setdefault(user_id, {}).pop(sticker_hash, None)
                    return True

        except Exception as e:
            logger.warning(f"[Sticker] 学习表情包异常: {e}")

        return False

    async def _save_sticker(
        self,
        group_id: str,
        user_id: str,
        url: str,
        sticker_hash: str,
    ) -> bool:
        """保存表情包到 StickerStore"""
        total_count = (await self.sticker_store.get_stats())["total"]
        if total_count >= self.cfg.sticker_total_limit:
            await self.sticker_store.delete_oldest_sticker()
            logger.debug("[Sticker] 已达总上限，删除最旧的")

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Sticker] 下载图片失败: HTTP {resp.status}")
                        return False
                    image_content = await resp.read()

            mime_type = resp.headers.get("Content-Type", "image/jpeg")

            sticker = await self.sticker_store.add_sticker(
                group_id=group_id,
                user_id=user_id,
                content=image_content,
                mime_type=mime_type,
                source_url=url,
            )

            if sticker:
                logger.debug(f"[Sticker] 成功学习表情包: user={user_id}, group={group_id}, hash={sticker_hash[:8]}")
                return True
            else:
                logger.debug(f"[Sticker] 表情包已存在: hash={sticker_hash[:8]}")
                return False

        except Exception as e:
            logger.warning(f"[Sticker] 保存表情包失败: {e}")
            return False

    async def add_sticker_from_image(self, event, image_data: dict) -> dict:
        """
        从上传的图片添加表情包到本地目录

        Args:
            event: 消息事件
            image_data: 包含 file, url, sub_type 等字段的图片数据

        Returns:
            dict: {"success": bool, "message": str}
        """
        img_url = image_data.get("url", "")

        if not img_url:
            return {"success": False, "message": "无法获取图片URL"}

        group_id = event.get_group_id() or "private"
        user_id = str(event.get_sender_id())

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return {"success": False, "message": f"下载图片失败: HTTP {resp.status}"}
                    image_content = await resp.read()

            mime_type = resp.headers.get("Content-Type", "image/jpeg")

            sticker = await self.sticker_store.add_sticker(
                group_id=group_id,
                user_id=user_id,
                content=image_content,
                mime_type=mime_type,
                source_url=img_url,
            )

            if sticker:
                return {"success": True, "message": f"已添加表情包: {sticker['uuid']}", "uuid": sticker["uuid"]}
            else:
                return {"success": False, "message": "该图片已存在于表情包库中"}

        except Exception as e:
            logger.warning(f"[Sticker] 添加表情包失败: {e}")
            return {"success": False, "message": f"添加失败: {e}"}

    async def should_send_sticker(self) -> bool:
        """判断当前是否应该发表情包（全局）"""
        if not self.cfg.sticker_learning_enabled:
            return False

        cooldown_seconds = self.cfg.sticker_send_cooldown * 60
        last_time = self._last_send_time.get("global", 0)
        if time.time() - last_time < cooldown_seconds:
            logger.debug(f"[Sticker] 发表情包冷却中，剩余 {int(cooldown_seconds - (time.time() - last_time))} 秒")
            return False

        sticker = await self.sticker_store.get_random_sticker()
        return sticker is not None

    async def get_sticker_for_sending(self) -> dict | None:
        """获取要发送的表情包（全局）"""
        sticker = await self.sticker_store.get_random_sticker()
        if sticker:
            self._last_send_time["global"] = time.time()
        return sticker

    async def list_stickers(self, limit: int = 10) -> list:
        """列出表情包（全局）"""
        if not getattr(self.cfg, "entertainment_enabled", True):
            return []
        stickers, _ = await self.sticker_store.list_stickers(limit)
        return stickers

    async def get_sticker_stats(self) -> dict:
        """获取表情包统计（全局）"""
        return await self.sticker_store.get_stats()

    async def get_prompt_injection(self) -> str:
        """获取表情包相关的 prompt 注入"""
        if not getattr(self.cfg, "entertainment_enabled", True):
            return ""
        if not self.cfg.sticker_learning_enabled:
            return ""

        stats = await self.get_sticker_stats()
        if stats["total"] == 0:
            return ""

        injection = (
            f"\n\n【表情包库】你有一个表情包库，目前有 {stats['total']} 张表情包（今日新增 {stats['today']} 张）。"
        )
        injection += "\n当群聊氛围适合时，可以使用 send_sticker 工具发送表情包来活跃气氛。"
        injection += "\n使用 list_stickers 工具可以查看可用的表情包。"

        return injection

    # ========== 群菜单自然语言触发 ==========

    @property
    def eat_keywords(self) -> list[str]:
        val = getattr(self.cfg, "meal_eat_keywords", None)
        if val is None:
            return ["吃啥", "吃什么", "今天吃啥", "今天吃什么", "吃点啥"]
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [k.strip() for k in val.split("|") if k.strip()]
        return ["吃啥", "吃什么", "今天吃啥", "今天吃什么", "吃点啥"]

    @property
    def banquet_keywords(self) -> list[str]:
        val = getattr(self.cfg, "meal_banquet_keywords", None)
        if val is None:
            return ["摆酒席", "开席", "整一桌", "来一桌", "上菜"]
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [k.strip() for k in val.split("|") if k.strip()]
        return ["摆酒席", "开席", "整一桌", "来一桌", "上菜"]

    async def handle_meal_nl_trigger(self, event, msg_text: str) -> bool:
        """
        处理群菜单自然语言触发
        Returns: True if triggered, False otherwise
        """
        if not getattr(self.cfg, "entertainment_enabled", True):
            return False

        group_id = event.get_group_id()
        if not group_id:
            return False

        if event.is_at_or_wake_command:
            return False

        for pattern in self.eat_keywords:
            if pattern in msg_text:
                meals = await self.plugin.meal_store.get_random_meals(group_id, count=1)
                if meals:
                    reply_text = f"吃{meals[0]}怎么样？"
                else:
                    reply_text = "菜单空空如也，请先用 /addmeal <菜名> 添加菜品再问我吃啥～"
                await self._send_to_group(group_id, reply_text)
                return True

        for pattern in self.banquet_keywords:
            if pattern in msg_text:
                cooldown_result = await self._check_banquet_cooldown(group_id)
                if cooldown_result:
                    await self._send_to_group(group_id, cooldown_result)
                    return True
                meals = await self.plugin.meal_store.get_random_meals(group_id, count=10)
                if meals:
                    lines = [f"第{i + 1}道菜：{meal}" for i, meal in enumerate(meals)]
                    reply_text = "\n".join(lines)
                else:
                    reply_text = "菜单空空如也，请先用 /addmeal <菜名> 添加菜品再来摆酒席～"
                await self._send_to_group(group_id, reply_text)
                return True

        return False

    async def _check_banquet_cooldown(self, group_id: str) -> str | None:
        """
        检查摆酒席是否在冷却中。
        Returns: 冷却提示文本 if rate-limited, None if allowed.
        """
        import time

        now = time.time()
        window = getattr(self.cfg, "meal_banquet_cooldown_minutes", 5) * 60
        limit = getattr(self.cfg, "meal_banquet_count", 5)

        timestamps = self._banquet_timestamps.setdefault(group_id, [])
        cutoff = now - window
        timestamps[:] = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= limit:
            remaining = int(timestamps[0] + window - now)
            return f"冷却中，请 {remaining} 秒后再试～"

        timestamps.append(now)
        return None

    async def _send_to_group(self, group_id: str, text: str):
        """发送消息到群，参照 engagement_executor 实现"""
        try:
            platform = self.plugin.context.platform_manager.platform_insts[0]
            bot = platform.bot
            await bot.send_group_msg(group_id=int(group_id), message=[{"type": "text", "data": {"text": text}}])
        except Exception:
            pass
