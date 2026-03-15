import asyncio
import logging
import random
import time
from collections import defaultdict
from pathlib import Path

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

    def _get_profile_path(self, group_id: str, user_id: str) -> Path:
        return self.profile_dir / f"user_{group_id}_{user_id}.md"

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
        """读取用户画像（Markdown 文本），无则返回空"""
        profile_key = f"{group_id}_{user_id}"
        # 定期清理过期缓存
        self._cleanup_expired_cache()

        # 先从缓存读取
        if profile_key in self._profile_cache:
            self._cache_access_time[profile_key] = time.time()
            logger.debug(f"[Profile] 从缓存加载画像: {profile_key}")
            return self._profile_cache[profile_key]

        path = self._get_profile_path(group_id, user_id)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                # 存入缓存
                self._profile_cache[profile_key] = content
                self._cache_access_time[profile_key] = time.time()
                logger.info(f"[Profile] 从磁盘加载画像: {profile_key} ({len(content)} 字符)")
                return content
            except OSError as e:
                logger.warning(f"[Profile] 读取画像失败 {profile_key}: {e}")
        logger.debug(f"[Profile] 用户无画像: {profile_key}")
        return ""

    async def save_profile(self, group_id: str, user_id: str, content: str):
        """保存用户画像（Markdown 文本）"""
        profile_key = f"{group_id}_{user_id}"
        # 定期清理过期缓存
        self._cleanup_expired_cache()

        path = self._get_profile_path(group_id, user_id)
        path.write_text(content, encoding="utf-8")
        # 更新缓存
        self._profile_cache[profile_key] = content
        self._cache_access_time[profile_key] = time.time()
        logger.info(f"[Profile] 已保存用户画像: {profile_key} ({len(content)} 字符)")

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

        logger.info(
            f"[Profile] 画像摘要(分层): {user_id}, core={len(core_lines)}, edge={len(kept_edge)}/{len(edge_lines)}"
        )
        return result

    async def cleanup_expired_profiles(self, days: int = 90):
        """清理过期画像 - 根据文件修改时间删除长时间未更新的画像"""
        try:
            cutoff_time = time.time() - (days * 86400)
            deleted_count = 0

            for profile_path in self.profile_dir.glob("user_*.md"):
                try:
                    mtime = profile_path.stat().st_mtime
                    if mtime < cutoff_time:
                        profile_path.unlink()
                        deleted_count += 1
                        logger.info(f"[Profile] 已删除过期画像: {profile_path.name}")
                except Exception as e:
                    logger.warning(f"[Profile] 删除画像失败 {profile_path.name}: {e}")

            if deleted_count > 0:
                logger.info(f"[Profile] 清理完成，共删除 {deleted_count} 个过期画像")
            return deleted_count
        except Exception as e:
            logger.warning(f"[Profile] 清理过期画像失败: {e}")
            return 0

    async def view_profile(self, group_id: str, user_id: str) -> str:
        """查看用户画像"""
        profile_key = f"{group_id}_{user_id}"
        logger.info(f"[Profile] 查看用户画像: {profile_key}")
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
            logger.info(f"[Profile] 已删除用户画像: {profile_key}")
            return f"已删除用户 {user_id} 的画像。"
        return f"用户 {user_id} 不存在画像记录。"

    async def list_profiles(self) -> dict:
        """列出所有画像统计"""
        logger.info("[Profile] 列出所有画像统计")
        files = list(self.profile_dir.glob("user_*.md"))
        return {
            "total_users": len(files),
            "total_tags": 0,
            "total_traits": 0,
        }

    async def build_profile(self, user_id: str, group_id: str, mode: str = "update") -> str:
        """
        从 NapCat 获取用户在群里的消息，构建/更新画像

        Args:
            user_id: 用户ID
            group_id: 群ID
            mode: "create" 覆盖创建, "update" 增量更新
        """

        logger.info(f"[Profile] 构建画像: 用户={user_id}, 群={group_id}, 模式={mode}")

        # 冷却时间检查
        cooldown_key = f"{group_id}_{user_id}"
        last_build = self._profile_build_cooldown.get(cooldown_key, 0)
        cooldown_seconds = self.plugin.cfg.profile_cooldown_minutes * 60
        if time.time() - last_build < cooldown_seconds:
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

            # 获取用户昵称
            try:
                member_info = await bot.call_action(
                    "get_group_member_info", group_id=int(group_id), user_id=int(user_id)
                )
                nickname = member_info.get("card") or member_info.get("nickname", "未知")
            except Exception:
                nickname = "未知"

            msg_count = self.plugin.cfg.profile_msg_count
            result = await bot.call_action("get_group_msg_history", group_id=int(group_id), count=msg_count)

            messages = result.get("messages", [])
            if not messages:
                return f"群 {group_id} 无消息记录"

            user_messages = []
            for msg in messages:
                if str(msg.get("user_id")) == str(user_id):
                    sender = msg.get("sender", {})
                    nickname = sender.get("nickname", "未知")
                    content = msg.get("message", "")
                    if content:
                        user_messages.append(f"{nickname}: {content}")

            if not user_messages:
                return f"用户 {user_id} 在群 {group_id} 中无消息记录"

            logger.info(f"[Profile] 获取到 {len(user_messages)} 条用户消息")

            existing_note = ""
            if mode == "update":
                existing_note = await self.load_profile(group_id, user_id)
                existing_note = existing_note[:500] if existing_note else "(暂无)"

            prompt = (
                f"你是记忆助手。请根据对话分析用户特征。\n"
                f"目标用户：{nickname} (QQ: {user_id})\n"
                f"{'旧笔记：' + existing_note + '\n' if mode == 'update' else ''}"
                f"用户消息：\n" + "\n".join(user_messages) + "\n"
                "请根据以上消息输出一段详细用户画像描述。使用Markdown格式输出，不少于500字。"
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

            await self.save_profile(group_id, user_id, new_note)
            # 更新冷却时间
            self._profile_build_cooldown[cooldown_key] = time.time()
            logger.info(f"[Profile] 已保存用户画像: {user_id}")
            return f"画像已{'创建' if mode == 'create' else '更新'}"

        except Exception as e:
            logger.warning(f"[Profile] 构建画像失败: {e}")
            return f"构建画像失败: {e}"
