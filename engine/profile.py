import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .context_injection import parse_message_chain


@dataclass
class StructuredProfile:
    """结构化画像数据容器 - parse -> mutate -> serialize"""

    identity: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    recent_updates: list[dict] = field(default_factory=list)
    long_term_notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.identity or self.preferences or self.traits or self.recent_updates or self.long_term_notes)


logger = logging.getLogger("astrbot")
PRIVATE_SCOPE_PREFIX = "private_"


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
        return self.profile_dir / f"{group_id}_{user_id}.yaml"

    def _get_legacy_profile_pattern(self, group_id: str, user_id: str) -> str:
        return f"{group_id}_{user_id}_*.yaml"

    def _get_profile_candidates(self, group_id: str, user_id: str) -> list[Path]:
        canonical_path = self._get_profile_path(group_id, user_id)
        candidates = []

        if canonical_path.exists():
            candidates.append(canonical_path)

        legacy_files = sorted(
            self.profile_dir.glob(self._get_legacy_profile_pattern(group_id, user_id)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for legacy_path in legacy_files:
            if legacy_path not in candidates:
                candidates.append(legacy_path)

        return candidates

    @staticmethod
    def _is_private_scope(scope_id: str) -> bool:
        return str(scope_id).startswith(PRIVATE_SCOPE_PREFIX)

    @staticmethod
    def _get_private_scope_user_id(scope_id: str) -> str:
        scope_id = str(scope_id or "")
        if not scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            return ""
        return scope_id[len(PRIVATE_SCOPE_PREFIX) :]

    @staticmethod
    def _extract_sender_id(msg: dict) -> str:
        sender = msg.get("sender", {}) or {}
        sender_id = sender.get("user_id")
        if sender_id is None:
            sender_id = msg.get("user_id", "")
        return str(sender_id or "")

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

        profile_paths = self._get_profile_candidates(group_id, user_id)
        if profile_paths:
            try:
                content = self._load_profile_from_file(profile_paths[0])
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
            _, body = self._parse_profile_document_text(content)
            return body
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

    @staticmethod
    def _unquote_yaml_scalar(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    def _parse_profile_document_text(self, content: str) -> tuple[dict, str]:
        """解析画像文档，返回元数据和正文内容。"""
        cleaned = self._clean_yaml_content(content or "")
        if not cleaned:
            return {}, ""

        lines = cleaned.splitlines()
        block_index = next((i for i, line in enumerate(lines) if line.startswith("content: |")), None)
        if block_index is not None:
            metadata = {}
            for raw_line in lines[:block_index]:
                if ":" not in raw_line:
                    continue
                key, value = raw_line.split(":", 1)
                metadata[key.strip()] = self._unquote_yaml_scalar(value.strip())

            body_lines = []
            for raw_line in lines[block_index + 1 :]:
                if raw_line.startswith("  "):
                    body_lines.append(raw_line[2:])
                elif raw_line == "":
                    body_lines.append("")
                else:
                    body_lines.append(raw_line)
            return metadata, "\n".join(body_lines).rstrip()

        try:
            data = yaml.safe_load(cleaned)
        except Exception:
            return {}, cleaned

        if isinstance(data, dict) and "content" in data:
            metadata = {k: v for k, v in data.items() if k != "content"}
            return metadata, str(data.get("content") or "")

        return {}, cleaned

    def _load_profile_document(self, path: Path) -> tuple[dict, str]:
        content = path.read_text(encoding="utf-8")
        return self._parse_profile_document_text(content)

    def _serialize_profile_document(self, document: dict) -> str:
        body = str(document.get("content", "") or "").rstrip()
        lines = [
            f'user_id: "{str(document.get("user_id", "") or "")}"',
            f'scope_id: "{str(document.get("scope_id", "") or "")}"',
            f'nickname: "{str(document.get("nickname", "") or "")}"',
            f'updated_at: "{str(document.get("updated_at", "") or "")}"',
            "content: |-",
        ]

        if body:
            lines.extend(f"  {line}" for line in body.splitlines())
        else:
            lines.append("  ")

        return "\n".join(lines).rstrip() + "\n"

    def _build_profile_document(
        self,
        group_id: str,
        user_id: str,
        content: str,
        nickname: str = "",
        existing_metadata: dict | None = None,
    ) -> dict:
        metadata = dict(existing_metadata or {})
        resolved_scope_id = str(group_id or metadata.get("scope_id") or metadata.get("group_id") or "")
        resolved_nickname = str(nickname or metadata.get("nickname") or "未知")
        return {
            "user_id": str(user_id),
            "scope_id": resolved_scope_id,
            "nickname": resolved_nickname,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": str(content or "").rstrip(),
        }

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

    async def get_structured_summary(
        self,
        group_id: str,
        user_id: str,
        max_items: int = 10,
    ) -> str:
        """
        获取结构化画像摘要（注入用，控制在 5-10 条高价值信息）

        格式：
        [identity]
        - ...
        [preferences]
        - ...
        [traits]
        - ...
        [recent_updates]
        - ...
        """
        profile_key = f"{group_id}_{user_id}"
        content = await self.load_profile(group_id, user_id)
        if not content:
            return ""

        data = self._parse_structured_content(content)
        identity = data.get("identity", [])
        preferences = data.get("preferences", [])
        traits = data.get("traits", [])
        recent_updates = data.get("recent_updates", [])
        long_term_notes = data.get("long_term_notes", [])

        if self.dropout_enabled:
            identity = [x for x in identity if self._is_core_info(x)] if identity else identity

        total_items = len(identity) + len(preferences) + len(traits) + len(recent_updates) + len(long_term_notes)
        if total_items <= max_items:
            result_parts = []
            if identity:
                result_parts.append("[identity]")
                result_parts.extend(f"- {x}" for x in identity)
            if preferences:
                result_parts.append("[preferences]")
                result_parts.extend(f"- {x}" for x in preferences)
            if traits:
                result_parts.append("[traits]")
                result_parts.extend(f"- {x}" for x in traits)
            if recent_updates:
                result_parts.append("[recent_updates]")
                for u in recent_updates[-3:]:
                    result_parts.append(f"- [{u.get('timestamp', '')}] {u.get('content', '')}")
            if long_term_notes:
                result_parts.append("[long_term_notes]")
                result_parts.extend(f"- {x}" for x in long_term_notes[-3:])
            return "\n".join(result_parts)

        result_parts = []
        slots_per_section = max(1, max_items // 5)

        if identity:
            result_parts.append("[identity]")
            result_parts.extend(f"- {x}" for x in identity[:slots_per_section])

        identity_taken = len(identity[:slots_per_section]) if identity else 0
        remaining = max_items - identity_taken

        if preferences and remaining > 0:
            take = min(len(preferences), max(1, remaining // 3))
            result_parts.append("[preferences]")
            result_parts.extend(f"- {x}" for x in preferences[:take])
            remaining -= take

        if traits and remaining > 0:
            take = min(len(traits), max(1, remaining // 3))
            result_parts.append("[traits]")
            result_parts.extend(f"- {x}" for x in traits[:take])
            remaining -= take

        if recent_updates and remaining > 0:
            result_parts.append("[recent_updates]")
            to_take = min(remaining, len(recent_updates))
            for u in recent_updates[-to_take:]:
                result_parts.append(f"- [{u.get('timestamp', '')}] {u.get('content', '')}")
            remaining -= to_take

        if long_term_notes and remaining > 0:
            result_parts.append("[long_term_notes]")
            result_parts.extend(f"- {x}" for x in long_term_notes[-remaining:])

        logger.debug(f"[Profile] 结构化摘要: {user_id}, items={len(result_parts) // 2 if result_parts else 0}")
        return "\n".join(result_parts)

    async def save_profile(self, group_id: str, user_id: str, content: str, nickname: str = ""):
        """保存用户画像（YAML 格式，统一写入稳定文件名）"""
        profile_key = f"{group_id}_{user_id}"
        self._cleanup_expired_cache()

        path = self._get_profile_path(group_id, user_id, nickname)

        # 清理 Markdown 代码块标记，防止 LLM 返回 ```yaml 格式
        content = self._clean_yaml_content(content)
        path.write_text(content, encoding="utf-8")

        for legacy_path in self.profile_dir.glob(self._get_legacy_profile_pattern(group_id, user_id)):
            if legacy_path != path:
                try:
                    legacy_path.unlink()
                except OSError as e:
                    logger.warning(f"[Profile] 清理旧画像文件失败 {legacy_path.name}: {e}")

        _, cached_body = self._parse_profile_document_text(content)
        self._profile_cache[profile_key] = cached_body
        self._cache_access_time[profile_key] = time.time()
        logger.debug(f"[Profile] 已保存用户画像: {path.name} ({len(content)} 字符)")

    async def save_profile_document(
        self,
        group_id: str,
        user_id: str,
        content: str,
        nickname: str = "",
        existing_metadata: dict | None = None,
    ):
        """以结构化 YAML 文档格式保存画像。"""
        document = self._build_profile_document(
            group_id, user_id, content, nickname, existing_metadata=existing_metadata
        )
        await self.save_profile(group_id, user_id, self._serialize_profile_document(document), nickname=nickname)

    async def append_profile_content(self, group_id: str, user_id: str, addition: str, nickname: str = ""):
        """向画像正文追加内容，并保留结构化 YAML 元数据。"""
        addition = str(addition or "").strip()
        if not addition:
            return

        existing_metadata = {}
        existing_content = ""
        profile_candidates = self._get_profile_candidates(group_id, user_id)
        if profile_candidates:
            try:
                existing_metadata, existing_content = self._load_profile_document(profile_candidates[0])
            except OSError as e:
                logger.warning(f"[Profile] 读取画像文档失败 {group_id}_{user_id}: {e}")

        segments = []
        if existing_content.strip():
            segments.append(existing_content.rstrip())
        else:
            segments.append("# 用户印象笔记")
        segments.append(addition)

        merged_content = "\n".join(segment for segment in segments if segment).strip()
        if len(merged_content) > 2000:
            merged_content = merged_content[-2000:]

        await self.save_profile_document(
            group_id,
            user_id,
            merged_content,
            nickname=nickname or str(existing_metadata.get("nickname") or ""),
            existing_metadata=existing_metadata,
        )

    async def upsert_fact(
        self,
        scope_id: str,
        user_id: str,
        fact_type: str,
        content: str,
        source: str = "manual",
        replace_similar: bool = True,
        nickname: str = "",
    ) -> bool:
        """
        统一写入接口 - 分类写入 + 去重/覆盖策略

        Args:
            scope_id: 会话范围ID（群号或 private_xxx）
            user_id: 用户ID
            fact_type: 事实类型 - "identity" | "preference" | "trait" | "recent_update" | "long_term_note"
            content: 事实内容
            source: 来源 - "manual" | "reflection" | "auto"
            replace_similar: 是否覆盖相似条目（默认 True）
            nickname: 用户昵称

        Returns:
            是否写入成功
        """
        import re

        content = str(content or "").strip()
        if not content:
            return False

        profile_key = f"{scope_id}_{user_id}"

        existing_metadata = {}
        existing_data = {}
        profile_candidates = self._get_profile_candidates(scope_id, user_id)
        if profile_candidates:
            try:
                existing_metadata, existing_content = self._load_profile_document(profile_candidates[0])
                existing_data = self._parse_structured_content(existing_content)
            except Exception as e:
                logger.warning(f"[Profile] 读取画像文档失败 {profile_key}: {e}")

        identity = existing_data.get("identity", [])
        preferences = existing_data.get("preferences", [])
        traits = existing_data.get("traits", [])
        recent_updates = existing_data.get("recent_updates", [])
        long_term_notes = existing_data.get("long_term_notes", [])

        if not isinstance(identity, list):
            identity = []
        if not isinstance(preferences, list):
            preferences = []
        if not isinstance(traits, list):
            traits = []
        if not isinstance(recent_updates, list):
            recent_updates = []
        if not isinstance(long_term_notes, list):
            long_term_notes = []

        timestamp = datetime.now().strftime("%Y-%m-%d")

        if fact_type == "identity":
            modified = self._upsert_identity(identity, content, replace_similar)
        elif fact_type == "preference":
            modified = self._upsert_preference(preferences, content, replace_similar)
        elif fact_type == "trait":
            modified = self._upsert_trait(traits, content, replace_similar)
        elif fact_type == "recent_update":
            modified = self._upsert_recent_update(recent_updates, content, timestamp, replace_similar, long_term_notes)
        elif fact_type == "long_term_note":
            modified = self._upsert_long_term_note(long_term_notes, content, replace_similar)
        else:
            logger.warning(f"[Profile] 未知 fact_type: {fact_type}")
            return False

        if not modified:
            return False

        new_content = self._build_content_lines(identity, preferences, traits, recent_updates, long_term_notes)
        await self.save_profile_document(
            scope_id,
            user_id,
            new_content,
            nickname=nickname or str(existing_metadata.get("nickname", "")),
            existing_metadata=existing_metadata,
        )

        logger.debug(f"[Profile] upsert_fact 成功: type={fact_type}, content={content[:50]}...")
        return True

    def _normalize_for_dedup(self, text: str) -> str:
        """标准化文本用于去重比较"""
        import re

        return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()

    def _find_similar(self, text: str, target_list: list[str], threshold: float = 0.7) -> int | None:
        """查找相似条目，返回索引或 None"""
        import re

        norm_text = self._normalize_for_dedup(text)
        if not norm_text:
            return None

        for i, existing in enumerate(target_list):
            norm_existing = self._normalize_for_dedup(existing)
            if not norm_existing:
                continue
            if norm_text == norm_existing:
                return i
            if len(norm_text) > 2 and len(norm_existing) > 2:
                if norm_text in norm_existing or norm_existing in norm_text:
                    return i
        return None

    def _upsert_identity(self, identity: list[str], content: str, replace_similar: bool) -> bool:
        """identity 类：覆盖同类旧值（提取主体后覆盖）"""
        import re

        idx = self._find_similar(content, identity)
        if idx is not None:
            if replace_similar:
                identity[idx] = content
                logger.debug(f"[Profile] identity 覆盖: {content[:50]}")
                return True
            return False

        identity.append(content)
        return True

    def _upsert_preference(self, preferences: list[str], content: str, replace_similar: bool) -> bool:
        """preference 类：允许覆盖冲突项"""
        import re

        negations = ["不", "没", "讨厌", "恨", "反对", "拒绝", "放弃", "不再", "以前"]
        is_negative = any(content.startswith(neg) or neg in content[:6] for neg in negations)

        for i, pref in enumerate(preferences):
            pref_is_negative = any(pref.startswith(neg) or neg in pref[:6] for neg in negations)

            if is_negative == pref_is_negative:
                norm_pref = self._normalize_for_dedup(pref)
                norm_content = self._normalize_for_dedup(content)
                if norm_pref == norm_content or norm_pref in norm_content or norm_content in norm_pref:
                    if replace_similar:
                        preferences[i] = content
                        logger.debug(f"[Profile] preference 覆盖（同类）: {content[:50]}")
                        return True
                    return False

            if is_negative and not pref_is_negative:
                norm_pref = self._normalize_for_dedup(pref)
                norm_content = self._normalize_for_dedup(content)
                common = set(norm_pref) & set(norm_content)
                if len(common) >= max(2, min(len(norm_pref), len(norm_content)) * 0.5):
                    if replace_similar:
                        preferences[i] = content
                        logger.debug(f"[Profile] preference 覆盖冲突: {pref} -> {content}")
                        return True
                    return False

        preferences.append(content)
        return True

    def _upsert_trait(self, traits: list[str], content: str, replace_similar: bool) -> bool:
        """trait 类：保守追加，去重"""
        idx = self._find_similar(content, traits)
        if idx is not None:
            if replace_similar:
                traits[idx] = content
                logger.debug(f"[Profile] trait 覆盖: {content[:50]}")
                return True
            return False

        traits.append(content)
        return True

    def _upsert_recent_update(
        self,
        recent_updates: list[dict],
        content: str,
        timestamp: str,
        replace_similar: bool,
        long_term_notes: list[str],
        max_items: int = 10,
    ) -> bool:
        """
        recent_update 类：优先保留最近 N 条，溢出归档到 long_term_notes。
        高价值关键词内容直接晋升 long_term_notes，不经 recent_updates。
        """
        if self._should_promote_to_long_term_note(content):
            logger.debug(f"[Profile] 高价值关键词检测，直接晋升 long_term_note: {content[:30]}...")
            return self._upsert_long_term_note(long_term_notes, content, replace_similar)

        for i, update in enumerate(recent_updates):
            norm_existing = self._normalize_for_dedup(update.get("content", ""))
            norm_new = self._normalize_for_dedup(content)
            if (
                norm_existing
                and norm_new
                and (norm_existing == norm_new or norm_existing in norm_new or norm_new in norm_existing)
            ):
                if replace_similar:
                    recent_updates[i] = {"timestamp": timestamp, "content": content}
                    logger.debug(f"[Profile] recent_update 覆盖: {content[:50]}")
                    return True
                return False

        recent_updates.append({"timestamp": timestamp, "content": content})

        if len(recent_updates) > max_items:
            oldest = recent_updates.pop(0)
            long_term_notes.append(oldest["content"])
            logger.debug(f"[Profile] recent_updates 溢出归档: {oldest['content'][:30]}...")

        return True

    def _upsert_long_term_note(self, long_term_notes: list[str], content: str, replace_similar: bool) -> bool:
        """long_term_note 类：只保留高价值不冲突的结论"""
        idx = self._find_similar(content, long_term_notes)
        if idx is not None:
            if replace_similar:
                long_term_notes[idx] = content
                logger.debug(f"[Profile] long_term_note 覆盖: {content[:50]}")
                return True
            return False

        long_term_notes.append(content)
        return True

    def _build_content_lines(
        self,
        identity: list[str],
        preferences: list[str],
        traits: list[str],
        recent_updates: list[dict],
        long_term_notes: list[str],
    ) -> str:
        """构建内容行"""
        lines = []
        if identity:
            lines.append("## identity")
            for item in identity:
                lines.append(f"- {item}")
        if preferences:
            lines.append("## preferences")
            for item in preferences:
                lines.append(f"- {item}")
        if traits:
            lines.append("## traits")
            for item in traits:
                lines.append(f"- {item}")
        if recent_updates:
            lines.append("## recent_updates")
            for update in recent_updates:
                ts = update.get("timestamp", "")
                lines.append(f"- [{ts}] {update.get('content', '')}")
        if long_term_notes:
            lines.append("## long_term_notes")
            for note in long_term_notes:
                lines.append(f"- {note}")
        return "\n".join(lines)

    def classify_fact(self, fact: str, explicit_type: str | None = None) -> str:
        """
        自动分类事实类型。

        优先级：
        1. explicit_type - 调用方显式指定的类型（最高优先级）
        2. heuristic - 关键词启发式分类
        3. 默认 recent_update
        """
        VALID_TYPES = {"identity", "preference", "trait", "recent_update", "long_term_note"}
        if explicit_type and explicit_type in VALID_TYPES:
            return explicit_type

        return self._heuristic_classify(fact)

    def _heuristic_classify(self, fact: str) -> str:
        """基于关键词的启发式分类（第二优先级）"""
        fact_lower = fact.lower()

        identity_keywords = [
            "是",
            "职业",
            "工作",
            "身份",
            "角色",
            "年龄",
            "岁",
            "学生",
            "老师",
            "程序员",
            "工程师",
            "医生",
            "养",
            "有",
            "住在",
            "来自",
            "城市",
            "公司",
            "学校",
            "年级",
            "专业",
            "学历",
        ]
        if any(kw in fact_lower for kw in identity_keywords):
            return "identity"

        preference_keywords = [
            "喜欢",
            "爱",
            "讨厌",
            "偏好",
            "想",
            "要",
            "想学",
            "决定",
            "不爱",
            "不喜",
            "恨",
            "支持",
            "反对",
        ]
        if any(kw in fact_lower for kw in preference_keywords):
            return "preference"

        trait_keywords = [
            "说话",
            "性格",
            "风格",
            "直接",
            "简洁",
            "话多",
            "话少",
            "活跃",
            "安静",
            "幽默",
            "内向",
            "外向",
            "理性",
            "感性",
        ]
        if any(kw in fact_lower for kw in trait_keywords):
            return "trait"

        return "recent_update"

    LONG_TERM_KEYWORDS = [
        "每周",
        "每月",
        "每天",
        "一直",
        "永远",
        "习惯",
        "固定",
        "长期",
        "规律",
        "必定",
        "每逢",
    ]

    def _should_promote_to_long_term_note(self, content: str) -> bool:
        """判断内容是否应直接写入 long_term_note（高价值关键词）"""
        content_lower = content.lower()
        return any(kw in content_lower for kw in self.LONG_TERM_KEYWORDS)

    def _parse_structured_content(self, content: str) -> dict:
        """解析结构化画像内容"""
        import re

        result = {
            "identity": [],
            "preferences": [],
            "traits": [],
            "recent_updates": [],
            "long_term_notes": [],
        }

        if not content:
            return result

        current_section = None
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            section_match = re.match(r"^##\s*(\w+)", line)
            if section_match:
                current_section = section_match.group(1)
                continue

            if current_section == "identity":
                item = re.sub(r"^-\s*", "", line)
                if item:
                    result["identity"].append(item)
            elif current_section == "preferences":
                item = re.sub(r"^-\s*", "", line)
                if item:
                    result["preferences"].append(item)
            elif current_section == "traits":
                item = re.sub(r"^-\s*", "", line)
                if item:
                    result["traits"].append(item)
            elif current_section == "recent_updates":
                item_match = re.match(r"- \[([\d-]+)\]\s*(.+)", line)
                if item_match:
                    result["recent_updates"].append(
                        {
                            "timestamp": item_match.group(1),
                            "content": item_match.group(2),
                        }
                    )
                else:
                    item = re.sub(r"^-\s*", "", line)
                    if item:
                        result["recent_updates"].append(
                            {
                                "timestamp": "",
                                "content": item,
                            }
                        )
            elif current_section == "long_term_notes":
                item = re.sub(r"^-\s*", "", line)
                if item:
                    result["long_term_notes"].append(item)

        return result

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
        profile_key = f"{group_id}_{user_id}"
        deleted_count = 0

        # 删除所有匹配的文件：无昵称版本和有昵称版本
        for pattern in [f"{profile_key}.yaml", f"{profile_key}_*.yaml"]:
            for path in self.profile_dir.glob(pattern):
                try:
                    path.unlink()
                    deleted_count += 1
                    logger.debug(f"[Profile] 已删除画像文件: {path.name}")
                except Exception as e:
                    logger.warning(f"[Profile] 删除画像失败 {path.name}: {e}")

        # 清理缓存
        self._profile_cache.pop(profile_key, None)
        self._cache_access_time.pop(profile_key, None)

        if deleted_count > 0:
            return f"已删除用户 {user_id} 的画像（{deleted_count}个文件）。"
        return f"用户 {user_id} 不存在画像记录。"

    async def list_profiles(self) -> dict:
        """列出所有画像统计"""
        logger.debug("[Profile] 列出所有画像统计")
        files = list(self.profile_dir.glob("*.yaml"))
        return {
            "total_users": len(files),
        }

    async def build_profile(
        self,
        user_id: str,
        group_id: str,
        mode: str = "update",
        force: bool = False,
        umo: str | None = None,
    ) -> str:
        """
        从 NapCat 获取用户在群里的消息，构建/更新画像

        Args:
            user_id: 用户ID
            group_id: 群ID
            mode: "create" 覆盖创建, "update" 增量更新
            force: 是否强制更新（忽略每日限制）
        """

        scope_id = str(group_id)
        is_private_scope = self._is_private_scope(scope_id)
        private_user_id = self._get_private_scope_user_id(scope_id)

        logger.debug(f"[Profile] 构建画像: 用户={user_id}, 范围={scope_id}, 模式={mode}, 强制={force}")

        if is_private_scope and private_user_id and str(user_id) != private_user_id:
            return "私聊画像仅支持当前会话用户。"

        daily_key = f"{scope_id}_{user_id}"

        # 每日更新限制检查
        if not force:
            today = datetime.now().strftime("%Y-%m-%d")
            last_update_date = self._profile_daily_updated.get(daily_key)
            if last_update_date == today:
                logger.debug(f"[Profile] 用户 {user_id} 今日已更新，跳过")
                return "今日已更新"

        # 冷却时间检查
        cooldown_key = f"{scope_id}_{user_id}"
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
                if is_private_scope:
                    member_info = await bot.call_action("get_stranger_info", user_id=int(user_id), no_cache=False)
                    member_nickname = (
                        member_info.get("remark") or member_info.get("nick") or member_info.get("nickname", "未知")
                    )
                else:
                    member_info = await bot.call_action(
                        "get_group_member_info", group_id=int(scope_id), user_id=int(user_id)
                    )
                    member_nickname = member_info.get("card") or member_info.get("nickname", "未知")
            except Exception:
                member_nickname = "未知"

            msg_count = self.plugin.cfg.profile_msg_count
            if is_private_scope:
                friend_user_id = private_user_id or str(user_id)
                result = await bot.call_action("get_friend_msg_history", user_id=int(friend_user_id), count=msg_count)
            else:
                result = await bot.call_action("get_group_msg_history", group_id=int(scope_id), count=msg_count)

            messages = result.get("messages", [])
            if not messages:
                if is_private_scope:
                    return f"私聊 {private_user_id or user_id} 无消息记录"
                return f"群 {scope_id} 无消息记录"

            user_messages = []
            nickname = member_nickname
            for msg in messages:
                if self._extract_sender_id(msg) == str(user_id):
                    msg_text = await parse_message_chain(msg, self.plugin)
                    if msg_text:
                        user_messages.append(msg_text)
                        sender = msg.get("sender", {})
                        nickname = sender.get("card") or sender.get("nickname", nickname)

            if not user_messages:
                location = "私聊" if is_private_scope else f"群 {scope_id}"
                return f"用户 {user_id} 在{location}中无消息记录"

            logger.debug(f"[Profile] 获取到 {len(user_messages)} 条用户消息")

            existing_note = ""
            if mode == "update":
                existing_note = await self.load_profile(scope_id, user_id)
                existing_note = existing_note[:500] if existing_note else "(暂无)"

            prompt = (
                f"你是记忆助手。请根据对话分析用户特征。\n"
                f"目标用户：{nickname} (QQ: {user_id})\n"
                f"会话范围：{scope_id}\n"
                f"{'旧笔记：' + existing_note + chr(10) if mode == 'update' else ''}"
                f"{'私聊消息' if is_private_scope else '群聊消息'}：" + "\n" + "\n".join(user_messages) + "\n"
                "请以 YAML 格式输出用户画像，包含以下字段：\n"
                "- user_id: 用户QQ号\n"
                "- scope_id: 会话范围ID（群号或 private_xxx）\n"
                "- nickname: 用户昵称\n"
                "- updated_at: 更新时间（格式：YYYY-MM-DD HH:MM:SS）\n"
                "- content: 用户画像描述（使用Markdown格式，不少于500字）\n"
            )

            llm_provider = self.plugin.context.get_using_provider(umo=umo)
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

            await self.save_profile(scope_id, user_id, new_note, nickname)
            # 更新冷却时间
            self._profile_build_cooldown[cooldown_key] = time.time()
            # 更新每日记录
            self._profile_daily_updated[daily_key] = datetime.now().strftime("%Y-%m-%d")
            logger.debug(f"[Profile] 已保存用户画像: {user_id}")
            return f"画像已{'创建' if mode == 'create' else '更新'}"

        except Exception as e:
            logger.warning(f"[Profile] 构建画像失败: {e}")
            return f"构建画像失败: {e}"

    async def analyze_and_build_profiles(self, group_id: str, messages: list = None, umo: str | None = None) -> str:
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

            parsed_messages = await asyncio.gather(*[parse_message_chain(msg, self.plugin) for msg in messages])

            # 统计用户消息数量
            user_msg_counts = defaultdict(int)
            user_nicknames = {}
            user_contents = defaultdict(list)
            bot_id = str(self.plugin._get_bot_id() or "") if hasattr(self.plugin, "_get_bot_id") else ""

            for msg, parsed_text in zip(messages, parsed_messages, strict=False):
                sender = msg.get("sender", {})
                user_id = self._extract_sender_id(msg)
                if not user_id or user_id == "0" or (bot_id and user_id == bot_id):
                    continue
                nickname = sender.get("card") or sender.get("nickname", "未知")
                if parsed_text:
                    user_msg_counts[user_id] += 1
                    if user_id not in user_nicknames:
                        user_nicknames[user_id] = nickname
                    user_contents[user_id].append(parsed_text)

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

            llm_provider = self.plugin.context.get_using_provider(umo=umo)
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
                    f"{'旧笔记：' + existing_note + chr(10) if existing_note != '(暂无)' else ''}"
                    f"用户消息：\n" + "\n".join(user_messages) + "\n"
                    f"{interested_tag}\n"
                    "请以 YAML 格式输出用户画像，包含以下字段：\n"
                    "- user_id: 用户QQ号\n"
                    "- scope_id: 会话范围ID（群号）\n"
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
