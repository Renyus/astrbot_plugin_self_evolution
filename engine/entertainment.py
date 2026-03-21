"""
娱乐功能模块 - 包含表情包学习和今日老婆等娱乐指令
"""

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

            raw_msg = getattr(event, "raw_message", None)
            image_sub_types = {}
            if raw_msg and hasattr(raw_msg, "get"):
                for seg in raw_msg.get("message", []):
                    if seg.get("type") == "image":
                        img_data = seg.get("data", {})
                        img_sub_type = img_data.get("sub_type", 0)
                        image_sub_types[len(image_sub_types)] = img_sub_type

            img_index = 0
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    sub_type = image_sub_types.get(img_index, 0)
                    img_index += 1
                    if sub_type == 0:
                        logger.debug(f"[Sticker] sub_type=0 普通图片，跳过: user={user_id}, group={group_id}")
                        continue

                    try:
                        base64_data = await comp.convert_to_base64()
                    except Exception as e:
                        logger.warning(f"[Sticker] 获取图片Base64失败: {e}")
                        continue

                    sticker_hash = hashlib.md5(base64_data.encode()).hexdigest()

                    daily_count = await self.dao.get_today_sticker_count()
                    if daily_count >= self.cfg.sticker_daily_limit:
                        logger.debug(f"[Sticker] 今日已达上限 {self.cfg.sticker_daily_limit}")
                        return False

                    total_count = await self.dao.get_sticker_count()
                    if total_count >= self.cfg.sticker_total_limit:
                        await self.dao.delete_oldest_sticker()
                        logger.debug("[Sticker] 已达总上限，删除最旧的")

                    sticker_uuid = await self.dao.add_sticker(group_id, user_id, base64_data, "", sticker_hash)
                    if sticker_uuid:
                        logger.debug(f"[Sticker] 成功学习表情包: user={user_id}, group={group_id}, sub_type={sub_type}")
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
            logger.debug(f"[Sticker] 打标签冷却中，剩余 {int(cooldown_seconds - (now - self._last_tag_time))} 秒")
            return False

        untagged = await self.dao.get_untagged_stickers(1)
        if not untagged:
            logger.debug("[Sticker] 没有未打标签的表情包")
            return False

        sticker = untagged[0]
        logger.debug(f"[Sticker] 准备给表情包打标签: uuid={sticker['uuid']}")

        temp_file_path = None
        try:
            import os
            import uuid

            img_data = base64.b64decode(sticker["base64_data"])

            # 获取临时目录并保存文件
            try:
                from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

                temp_dir = get_astrbot_temp_path()
            except ImportError:
                temp_dir = os.path.join(os.path.expanduser("~"), ".astrbot", "temp")

            os.makedirs(temp_dir, exist_ok=True)
            temp_file_path = os.path.join(temp_dir, f"sticker_tag_{uuid.uuid4()}.jpg")

            with open(temp_file_path, "wb") as f:
                f.write(img_data)

            logger.debug(f"[Sticker] 已保存临时文件: {temp_file_path}")

            # 调用 MCP 工具 understand_image
            tool_manager = self.plugin.context.get_llm_tool_manager()
            if not tool_manager:
                logger.warning("[Sticker] 获取 tool_manager 失败")
                return False

            mcp_runtime = tool_manager.mcp_server_runtime_view
            if not mcp_runtime:
                logger.warning("[Sticker] 没有可用的 MCP 服务")
                return False

            logger.debug(f"[Sticker] 找到 {len(mcp_runtime)} 个 MCP 服务器")

            # 遍历所有 MCP 服务器，找到 understand_image 工具并调用
            for server_name, runtime in mcp_runtime.items():
                logger.debug(f"[Sticker] 检查 MCP 服务器: {server_name}, runtime: {runtime}")
                if runtime and runtime.client:
                    mcp_client = runtime.client
                    logger.debug(f"[Sticker] 准备调用 MCP 客户端: {server_name}")
                    try:
                        from datetime import timedelta

                        tool_result = await mcp_client.call_tool_with_reconnect(
                            "understand_image",
                            {
                                "prompt": "请用一句话描述这张图片的内容，然后提取3-5个关键词标签（用|分隔）。输出格式：描述：<一句话描述> 标签：<tag1|tag2|tag3>",
                                "image_source": temp_file_path,
                            },
                            timedelta(seconds=60),
                        )

                        logger.debug(f"[Sticker] MCP 返回结果: {tool_result}")

                        if tool_result and tool_result.content:
                            # 提取文本内容
                            response_text = ""
                            for content in tool_result.content:
                                if hasattr(content, "text"):
                                    response_text += content.text
                                elif isinstance(content, str):
                                    response_text += content

                            logger.debug(f"[Sticker] MCP 工具响应: {response_text[:100]}")

                            # 解析描述和标签
                            description = ""
                            tags = ""
                            if "描述：" in response_text:
                                desc_part = response_text.split("描述：")[1].strip()
                                if "标签：" in desc_part:
                                    description = desc_part.split("标签：")[0].strip()
                                elif "标签:" in desc_part:
                                    description = desc_part.split("标签:")[0].strip()
                                else:
                                    description = desc_part.split("\n")[0].strip()
                            elif "描述:" in response_text:
                                desc_part = response_text.split("描述:")[1].strip()
                                if "标签：" in desc_part:
                                    description = desc_part.split("标签：")[0].strip()
                                elif "标签:" in desc_part:
                                    description = desc_part.split("标签:")[0].strip()
                                else:
                                    description = desc_part.split("\n")[0].strip()

                            if "标签：" in response_text:
                                tags = response_text.split("标签：")[1].strip()
                            elif "标签:" in response_text:
                                tags = response_text.split("标签:")[1].strip()

                            if not tags:
                                tags = response_text.split("\n")[0][:50]

                            await self.dao.update_sticker_tags_by_uuid(sticker["uuid"], tags, description)
                            self._last_tag_time = time.time()
                            logger.debug(
                                f"[Sticker] 打标签成功: uuid={sticker['uuid']}, tags={tags}, description={description[:30]}"
                            )
                            return True
                    except Exception as e:
                        logger.warning(f"[Sticker] MCP 客户端调用失败: {e}")
                        continue

            logger.warning("[Sticker] 所有 MCP 客户端调用失败")
            return False

        except Exception as e:
            logger.warning(f"[Sticker] 打标签异常: {e}")
            return False
        finally:
            # 清理临时文件
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.debug(f"[Sticker] 已删除临时文件: {temp_file_path}")
                except Exception:
                    pass

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

    async def list_stickers(self, tags: str = None, limit: int = 10) -> list:
        """列出表情包（全局）"""
        return await self.dao.get_stickers_by_tags(tags, limit)

    async def get_sticker_stats(self) -> dict:
        """获取表情包统计（全局）"""
        return await self.dao.get_sticker_stats()

    async def get_prompt_injection(self) -> str:
        """获取表情包相关的 prompt 注入"""
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
