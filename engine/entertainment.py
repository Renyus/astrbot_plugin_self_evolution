"""
娱乐功能模块 - 包含表情包学习和今日老婆等娱乐指令
"""

import hashlib
import random
import time

from astrbot.api import logger


class EntertainmentEngine:
    """娱乐功能引擎"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._last_send_time = {}
        self._image_freq_cache: dict[str, dict[str, int]] = {}

    @property
    def dao(self):
        return self.plugin.dao

    @property
    def cfg(self):
        return self.plugin.cfg

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

            avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=256"

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
        """保存表情包到数据库"""
        daily_count = await self.dao.get_today_sticker_count()
        if daily_count >= self.cfg.sticker_daily_limit:
            logger.debug(f"[Sticker] 今日已达上限 {self.cfg.sticker_daily_limit}")
            return False

        total_count = await self.dao.get_sticker_count()
        if total_count >= self.cfg.sticker_total_limit:
            await self.dao.delete_oldest_sticker()
            logger.debug("[Sticker] 已达总上限，删除最旧的")

        sticker_uuid = await self.dao.add_sticker(group_id, user_id, url, sticker_hash)
        if sticker_uuid:
            logger.debug(f"[Sticker] 成功学习表情包: user={user_id}, group={group_id}, hash={sticker_hash[:8]}")
            return True
        else:
            logger.debug(f"[Sticker] 表情包已存在: hash={sticker_hash[:8]}")
            return False

    async def should_send_sticker(self) -> bool:
        """判断当前是否应该发表情包（全局）"""
        if not self.cfg.sticker_learning_enabled:
            return False

        cooldown_seconds = self.cfg.sticker_send_cooldown * 60
        last_time = self._last_send_time.get("global", 0)
        if time.time() - last_time < cooldown_seconds:
            logger.debug(f"[Sticker] 发表情包冷却中，剩余 {int(cooldown_seconds - (time.time() - last_time))} 秒")
            return False

        sticker = await self.dao.get_random_sticker()
        return sticker is not None

    async def get_sticker_for_sending(self) -> dict | None:
        """获取要发送的表情包（全局）"""
        sticker = await self.dao.get_random_sticker()
        if sticker:
            self._last_send_time["global"] = time.time()
        return sticker

    async def list_stickers(self, limit: int = 10) -> list:
        """列出表情包（全局）"""
        if not getattr(self.cfg, "entertainment_enabled", True):
            return []
        return await self.dao.get_stickers(limit)

    async def get_sticker_stats(self) -> dict:
        """获取表情包统计（全局）"""
        return await self.dao.get_sticker_stats()

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
