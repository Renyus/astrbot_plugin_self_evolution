"""
娱乐功能模块 - 包含今日老婆等娱乐指令
"""

import random
from astrbot.api import logger
from astrbot.api.event.filter import command
from astrbot.api.all import AstrMessageEvent


class EntertainmentEngine:
    """娱乐功能引擎"""

    def __init__(self, plugin):
        self.plugin = plugin

    @command("今日老婆")
    async def today_waifu(self, event: AstrMessageEvent):
        """今日老婆功能 - 随机抽取一名群友"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅限群聊使用")
            return

        logger.info(f"[Entertainment] 今日老婆指令，群 {group_id}")

        try:
            group = await event.get_group(group_id)
            if not group or not group.members:
                yield event.plain_result("获取群成员失败")
                return

            members = group.members
            if not members:
                yield event.plain_result("群里没有成员")
                return

            selected = random.choice(members)
            user_id = selected.user_id
            nickname = selected.nickname or user_id

            logger.info(f"[Entertainment] 今日老婆抽取结果: {nickname} ({user_id})")

            avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

            yield event.plain_result(f"今日老婆是：{nickname}！")
            yield event.image_result(avatar_url)

        except Exception as e:
            logger.warning(f"[Entertainment] 今日老婆功能异常: {e}")
            yield event.plain_result(f"功能异常: {e}")
