import asyncio
import logging
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger("astrbot")


class ProfileManager:
    """用户画像管理器 - Markdown 文本格式存储，支持分层失活"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.profile_dir = plugin.data_dir / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.locks = defaultdict(asyncio.Lock)
        # 画像内存缓存 {user_id: content}
        self._profile_cache = {}
        self._cache_ttl = 300  # 缓存5分钟
        self._cache_access_time = {}  # 记录缓存访问时间
        self._last_cache_cleanup = 0
        # 画像构建冷却时间 {group_id_user_id: timestamp}
        self._profile_build_cooldown = {}
        # 每日更新记录 {group_id_user_id: "YYYY-MM-DD"}
        self._profile_daily_updated = {}

    @property
    def precision_mode(self):
        return self.plugin.cfg.profile_precision_mode

    @property
    def dropout_enabled(self):
        return self.plugin.cfg.dropout_enabled

    @property
    def dropout_edge_rate(self):
        return self.plugin.cfg.dropout_edge_rate

    @property
    def core_info_keywords(self):
        keywords = self.plugin.cfg.core_info_keywords
        return [k.strip() for k in keywords.split(",")]

    def _get_profile_path(self, group_id: str, user_id: str, nickname: str = "") -> Path:
        if nickname:
            import re

            safe_nickname = re.sub(r'[<>:"/\\|?*]', "", nickname)[:20]
            return self.profile_dir / f"{group_id}_{user_id}_{safe_nickname}.yaml"
        return self.profile_dir / f"{group_id}_{user_id}.yaml"

    def _is_core_info(self, line: str) -> bool:
        """判断是否为核心信息（永不丢失）"""
        line_lower = line.lower()
        for keyword in self.core_info_keywords:
            if keyword.lower() in line_lower:
                return True
        return False

    def _cleanup_expired_cache(self):
        """清理过期的缓存"""
        now = time.time()
        if now - self._last_cache_cleanup < 300:  # 每5分钟最多清理一次
            return
        self._last_cache_cleanup = now

        expired_users = []
        for user_id, access_time in self._cache_access_time.items():
            if now - access_time > self._cache_ttl:
                expired_users.append(user_id)

        for user_id in expired_users:
            self._profile_cache.pop(user_id, None)
            self._cache_access_time.pop(user_id, None)

        if expired_users:
            logger.debug(f"[Profile] 已清理 {len(expired_users)} 个过期缓存")

    async def load_profile(self, group_id: str, user_id: str) -> str:
        """读取用户画像（YAML 格式），无则返回空"""
        profile_key = f"{group_id}_{user_id}"
        self._cleanup_expired_cache()

        if profile_key in self._profile_cache:
            self._cache_access_time[profile_key] = time.time()
            logger.debug(f"[Profile] 从缓存加载画像: {profile_key}")
            return self._profile_cache[profile_key]

        path = self._get_profile_path(group_id, user_id)
        if path.exists():
            try:
                content = self._load_profile_from_file(path)
                if content:
                    self._profile_cache[profile_key] = content
                    self._cache_access_time[profile_key] = time.time()
                    logger.debug(f"[Profile] 从磁盘加载画像: {profile_key} ({len(content)} 字符)")
                return content
            except OSError as e:
                logger.warning(f"[Profile] 读取画像失败 {profile_key}: {e}")

        pattern = f"{group_id}_{user_id}_*.yaml"
        matching_files = list(self.profile_dir.glob(pattern))
        if matching_files:
            try:
                content = self._load_profile_from_file(matching_files[0])
                if content:
                    self._profile_cache[profile_key] = content
                    self._cache_access_time[profile_key] = time.time()
                    logger.debug(f"[Profile] 从磁盘加载画像: {profile_key} ({len(content)} 字符)")
                return content
            except OSError as e:
                logger.warning(f"[Profile] 读取画像失败 {profile_key}: {e}")

        logger.debug(f"[Profile] 用户无画像: {profile_key}")
        return ""

    def _load_profile_from_file(self, path: Path) -> str:
        """从 yaml 文件加载画像内容"""
        try:
            content = path.read_text(encoding="utf-8").strip()
            # 清理 Markdown 代码块标记
            content = self._clean_yaml_content(content)
            data = yaml.safe_load(content)
            if data and isinstance(data, dict):
                return data.get("content", "")
            return content
        except Exception as e:
            logger.warning(f"[Profile] 解析画像文件失败 {path}: {e}")
            return ""

    def _clean_yaml_content(self, content: str) -> str:
        """清理 YAML 内容中的 Markdown 代码块标记"""
        import re

        # 移除 ```yaml 或 ``` 开头的代码块
        content = re.sub(r"^```yaml\s*\n?", "", content, flags=re.MULTILINE)
        content = re.sub(r"^```\s*\n?", "", content, flags=re.MULTILINE)
        # 移除结尾的 ```
        content = re.sub(r"\n?```$", "", content)
        return content.strip()

    async def get_profile_summary(self, group_id: str, user_id: str) -> str:
        """获取画像摘要（用于注入 LLM）- 支持分层失活"""
        profile_key = f"{group_id}_{user_id}"
        logger.debug(f"[Profile] 获取画像摘要: {profile_key}")
        content = await self.load_profile(group_id, user_id)
        if not content:
            logger.debug(f"[Profile] 用户无画像，返回空: {user_id}")
            return ""

        lines = content.split("\n")

        if not self.dropout_enabled:
            preview = "\n".join(lines[:10])
            if len(content) > 500:
                preview += "\n..."
            logger.debug(f"[Profile] 画像摘要(不分层): {user_id} ({len(preview)} 字符)")
            return preview

        core_lines = []
        edge_lines = []

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if self._is_core_info(line):
                core_lines.append(line)
            else:
                edge_lines.append(line)

        kept_edge = []
        for line in edge_lines:
            if random.random() > self.dropout_edge_rate:
                kept_edge.append(line)

        all_kept = core_lines + kept_edge
        result = "\n".join(all_kept[:10])

        if len(all_kept) > 10:
            result += f"\n... (共 {len(all_kept)} 条，已随机保留)"

        logger.debug(
            f"[Profile] 画像摘要(分层): {user_id}, core={len(core_lines)}, edge={len(kept_edge)}/{len(edge_lines)}"
        )
        return result

    async def save_profile(self, group_id: str, user_id: str, content: str, nickname: str = ""):
        """保存用户画像（YAML 格式，直接保存 LLM 返回的 YAML）"""
        profile_key = f"{group_id}_{user_id}"
        self._cleanup_expired_cache()

        path = self._get_profile_path(group_id, user_id, nickname)

        # 清理 Markdown 代码块标记，防止 LLM 返回 ```yaml 格式
        content = self._clean_yaml_content(content)
        path.write_text(content, encoding="utf-8")

        self._profile_cache[profile_key] = content
        self._cache_access_time[profile_key] = time.time()
        logger.debug(f"[Profile] 已保存用户画像: {path.name} ({len(content)} 字符)")

    async def cleanup_expired_profiles(self, days: int = 90):
        """清理过期画像 - 根据文件修改时间删除长时间未更新的画像"""
        try:
            cutoff_time = time.time() - (days * 86400)
            deleted_count = 0

            for profile_path in self.profile_dir.glob("*.yaml"):
                try:
                    if profile_path.stat().st_mtime < cutoff_time:
                        profile_path.unlink()
                        deleted_count += 1
                        logger.debug(f"[Profile] 已删除过期画像: {profile_path.name}")
                except Exception as e:
                    logger.warning(f"[Profile] 删除画像失败 {profile_path.name}: {e}")

            logger.debug(f"[Profile] 清理完成，共删除 {deleted_count} 个过期画像")
            return deleted_count
        except Exception as e:
            logger.warning(f"[Profile] 清理过期画像失败: {e}")
            return 0

    async def view_profile(self, group_id: str, user_id: str) -> str:
        """查看用户画像"""
        profile_key = f"{group_id}_{user_id}"
        logger.debug(f"[Profile] 查看用户画像: {profile_key}")
        content = await self.load_profile(group_id, user_id)
        if not content:
            return f"用户 {user_id} 暂无画像记录。"
        return f"用户ID: {user_id}\n\n{content}"

    async def delete_profile(self, group_id: str, user_id: str) -> str:
        """删除用户画像"""
        path = self._get_profile_path(group_id, user_id)
        profile_key = f"{group_id}_{user_id}"
        if path.exists():
            path.unlink()
            # 清理缓存
            self._profile_cache.pop(profile_key, None)
            self._cache_access_time.pop(profile_key, None)
            logger.debug(f"[Profile] 已删除用户画像: {profile_key}")
            return f"已删除用户 {user_id} 的画像。"
        return f"用户 {user_id} 不存在画像记录。"

    async def list_profiles(self) -> dict:
        """列出所有画像统计"""
        logger.debug("[Profile] 列出所有画像统计")
        files = list(self.profile_dir.glob("*.yaml"))
        return {
            "total_users": len(files),
        }

    async def build_profile(self, user_id: str, group_id: str, mode: str = "update", force: bool = False) -> str:
        """
        从 NapCat 获取用户在群里的消息，构建/更新画像

        Args:
            user_id: 用户ID
            group_id: 群ID
            mode: "create" 覆盖创建, "update" 增量更新
            force: 是否强制更新（忽略每日限制）
        """

        logger.debug(f"[Profile] 构建画像: 用户={user_id}, 群={group_id}, 模式={mode}, 强制={force}")

        daily_key = f"{group_id}_{user_id}"

        # 每日更新限制检查
        if not force:
            today = datetime.now().strftime("%Y-%m-%d")
            last_update_date = self._profile_daily_updated.get(daily_key)
            if last_update_date == today:
                logger.debug(f"[Profile] 用户 {user_id} 今日已更新，跳过")
                return "今日已更新"

        # 冷却时间检查
        cooldown_key = f"{group_id}_{user_id}"
        last_build = self._profile_build_cooldown.get(cooldown_key, 0)
        cooldown_seconds = self.plugin.cfg.profile_cooldown_minutes * 60
        if time.time() - last_build < cooldown_seconds and not force:
            remaining = int(cooldown_seconds - (time.time() - last_build))
            minutes = remaining // 60
            seconds = remaining % 60
            return f"画像操作冷却中，请 {minutes} 分 {seconds} 秒后再试"

        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
            if not platform_insts:
                return "无法获取平台实例"

            platform = platform_insts[0]
            if not hasattr(platform, "get_client"):
                return "平台不支持获取 bot"

            bot = platform.get_client()
            if not bot:
                return "无法获取 bot 实例"

            # 获取用户昵称（用于文件名）
            try:
                member_info = await bot.call_action(
                    "get_group_member_info", group_id=int(group_id), user_id=int(user_id)
                )
                member_nickname = member_info.get("card") or member_info.get("nickname", "未知")
            except Exception:
                member_nickname = "未知"

            msg_count = self.plugin.cfg.profile_msg_count
            result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=msg_count)

            messages = result.get("messages", [])
            if not messages:
                return f"群 {group_id} 无消息记录"

            from .context_injection import parse_message_chain

            user_messages = []
            nickname = member_nickname
            for msg in messages:
                if str(msg.get("user_id")) == str(user_id):
                    msg_text = await parse_message_chain(msg, self.plugin)
                    if msg_text:
                        user_messages.append(msg_text)
                        sender = msg.get("sender", {})
                        nickname = sender.get("nickname", "未知")

            if not user_messages:
                return f"用户 {user_id} 在群 {group_id} 中无消息记录"

            logger.debug(f"[Profile] 获取到 {len(user_messages)} 条用户消息")

            existing_note = ""
            if mode == "update":
                existing_note = await self.load_profile(group_id, user_id)
                existing_note = existing_note[:500] if existing_note else "(暂无)"

            prompt = (
                f"你是记忆助手。请根据对话分析用户特征。\n"
                f"目标用户：{nickname} (QQ: {user_id})\n"
                f"{'旧笔记：' + existing_note + '\n' if mode == 'update' else ''}"
                f"用户消息：\n" + "\n".join(user_messages) + "\n"
                "请以 YAML 格式输出用户画像，包含以下字段：\n"
                "- user_id: 用户QQ号\n"
                "- group_id: 群号\n"
                "- nickname: 用户昵称\n"
                "- updated_at: 更新时间（格式：YYYY-MM-DD HH:MM:SS）\n"
                "- content: 用户画像描述（使用Markdown格式，不少于500字）\n"
            )

            llm_provider = self.plugin.context.get_using_provider("qq")
            if not llm_provider:
                return "无法获取 LLM Provider"

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个专业的用户画像分析师。根据用户消息分析其身份背景、性格特征、兴趣爱好、沟通方式，工作学习习惯等，每个结论必须标注置信度。",
            )

            new_note = res.completion_text.strip() if res.completion_text else ""

            if not new_note:
                return "生成画像失败，请重试"

            await self.save_profile(group_id, user_id, new_note, nickname)
            # 更新冷却时间
            self._profile_build_cooldown[cooldown_key] = time.time()
            # 更新每日记录
            self._profile_daily_updated[daily_key] = datetime.now().strftime("%Y-%m-%d")
            logger.debug(f"[Profile] 已保存用户画像: {user_id}")
            return f"画像已{'创建' if mode == 'create' else '更新'}"

        except Exception as e:
            logger.warning(f"[Profile] 构建画像失败: {e}")
            return f"构建画像失败: {e}"

    async def analyze_and_build_profiles(self, group_id: str, messages: list = None) -> str:
        """
        自动分析群消息，找出活跃/感兴趣的用户，并自动构建画像

        Args:
            group_id: 群ID
            messages: 可选的群消息列表，如果为None则自动获取

        Returns:
            处理结果描述
        """
        import json

        logger.debug(f"[Profile] 自动分析并构建画像: 群={group_id}")

        try:
            # 获取群消息
            if messages is None:
                platform_insts = self.plugin.context.platform_manager.platform_insts
                if not platform_insts:
                    return "无法获取平台实例"

                platform = platform_insts[0]
                if not hasattr(platform, "get_client"):
                    return "平台不支持获取 bot"

                bot = platform.get_client()
                if not bot:
                    return "无法获取 bot 实例"

                msg_count = self.plugin.cfg.profile_msg_count
                result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=msg_count)
                messages = result.get("messages", [])

            if not messages:
                return "群消息为空"

            # 统计用户消息数量
            user_msg_counts = defaultdict(int)
            user_nicknames = {}
            user_contents = defaultdict(list)

            for msg in messages:
                sender = msg.get("sender", {})
                user_id = str(sender.get("user_id", ""))
                if not user_id or user_id == "0":
                    continue
                nickname = sender.get("nickname", "未知")
                content = msg.get("message", "")
                if content:
                    user_msg_counts[user_id] += 1
                    if user_id not in user_nicknames:
                        user_nicknames[user_id] = nickname
                    user_contents[user_id].append(f"{nickname}: {content}")

            if not user_msg_counts:
                return "无法分析用户消息"

            # 按消息数量排序，取前5名活跃用户
            sorted_users = sorted(user_msg_counts.items(), key=lambda x: x[1], reverse=True)
            active_users = sorted_users[:5]

            # 让 LLM 判断哪些用户值得构建画像
            # 格式化消息给 LLM
            top_users_summary = []
            for user_id, count in active_users:
                nickname = user_nicknames.get(user_id, "未知")
                # 取该用户最近5条消息
                user_msgs = user_contents[user_id][-5:]
                top_users_summary.append(
                    f"用户: {nickname} (QQ: {user_id}), 消息数: {count}, 最近消息: {'; '.join(user_msgs)}"
                )

            prompt = (
                f"你是用户画像分析师。请分析以下群聊用户，判断哪些用户值得构建画像。\n\n"
                + "\n".join(top_users_summary)
                + "\n\n"
                "请以JSON数组格式输出，格式如下：\n"
                '[{"user_id": "用户QQ号", "nickname": "用户昵称", "reason": "为什么值得构建画像", "interested": true/false}]\n\n'
                "规则：\n"
                "1. interested=true 表示该用户是AI感兴趣的用户（如活跃、有趣、经常发言、有独特观点等）\n"
                "2. interested=false 表示普通用户，可以构建画像但不紧急\n"
                "3. 只返回JSON数组，不要其他内容"
            )

            llm_provider = self.plugin.context.get_using_provider("qq")
            if not llm_provider:
                return "无法获取 LLM Provider"

            res = await llm_provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个专业的用户画像分析师，只输出JSON数组。",
            )

            result_text = res.completion_text.strip() if res.completion_text else ""

            # 解析 JSON
            try:
                # 尝试提取 JSON 部分
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0]
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0]

                target_users = json.loads(result_text)
            except json.JSONDecodeError:
                logger.warning(f"[Profile] 解析用户列表失败: {result_text}")
                # 如果解析失败，取前3名活跃用户
                target_users = [
                    {"user_id": uid, "nickname": user_nicknames.get(uid, "未知"), "interested": True}
                    for uid, _ in active_users[:3]
                ]

            # 为每个目标用户构建画像
            built_count = 0
            today = datetime.now().strftime("%Y-%m-%d")
            for user_info in target_users:
                user_id = user_info.get("user_id")
                nickname = user_info.get("nickname", "未知")
                interested = user_info.get("interested", False)
                reason = user_info.get("reason", "")

                if not user_id:
                    continue

                # 每日更新检查
                daily_key = f"{group_id}_{user_id}"
                last_update_date = self._profile_daily_updated.get(daily_key)
                if last_update_date == today:
                    logger.debug(f"[Profile] 用户 {user_id} 今日已更新，跳过")
                    continue

                # 检查冷却时间
                cooldown_key = f"{group_id}_{user_id}"
                last_build = self._profile_build_cooldown.get(cooldown_key, 0)
                cooldown_seconds = self.plugin.cfg.profile_cooldown_minutes * 60
                if time.time() - last_build < cooldown_seconds:
                    logger.debug(f"[Profile] 用户 {user_id} 冷却中，跳过")
                    continue

                # 获取该用户的最近消息
                user_messages = user_contents.get(user_id, [])
                if not user_messages:
                    continue

                # 构建画像
                existing_note = await self.load_profile(group_id, user_id)
                existing_note = existing_note[:500] if existing_note else "(暂无)"

                # 添加感兴趣标记
                interested_tag = "\n\n> ⭐ 该用户被AI标记为'感兴趣'" if interested else ""

                profile_prompt = (
                    f"你是记忆助手。请根据对话分析用户特征。\n"
                    f"目标用户：{nickname} (QQ: {user_id})\n"
                    f"构建原因：{reason}\n"
                    f"{'旧笔记：' + existing_note + '\n' if existing_note != '(暂无)' else ''}"
                    f"用户消息：\n" + "\n".join(user_messages) + "\n"
                    f"{interested_tag}\n"
                    "请以 YAML 格式输出用户画像，包含以下字段：\n"
                    "- user_id: 用户QQ号\n"
                    "- group_id: 群号\n"
                    "- nickname: 用户昵称\n"
                    "- updated_at: 更新时间（格式：YYYY-MM-DD HH:MM:SS）\n"
                    "- content: 用户画像描述（使用Markdown格式，不少于300字）\n"
                )

                res = await llm_provider.text_chat(
                    prompt=profile_prompt,
                    contexts=[],
                    system_prompt="你是一个专业的用户画像分析师。根据用户消息分析其身份背景、性格特征、兴趣爱好、沟通方式等。",
                )

                new_note = res.completion_text.strip() if res.completion_text else ""
                if new_note:
                    await self.save_profile(group_id, user_id, new_note, nickname)
                    self._profile_build_cooldown[cooldown_key] = time.time()
                    self._profile_daily_updated[daily_key] = today
                    built_count += 1
                    logger.debug(f"[Profile] 自动构建画像完成: 用户={user_id}, 感兴趣={interested}")

            return f"自动分析完成，为 {built_count} 位用户构建了画像"

        except Exception as e:
            logger.warning(f"[Profile] 自动分析并构建画像失败: {e}")
            return f"自动分析失败: {e}"
