"""
娱乐功能模块 - 包含今日老婆等娱乐指令
"""

import random
from astrbot.api import logger


class EntertainmentEngine:
    """娱乐功能引擎"""

    def __init__(self, plugin):
        self.plugin = plugin

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

            # 返回结果列表 [文字消息, 图片消息]
            return [f"你今日的群友老婆是：{nickname}！", avatar_url]

        except Exception as e:
            logger.warning(f"[Entertainment] 今日老婆功能异常: {e}")
            return [f"功能异常: {e}"]
