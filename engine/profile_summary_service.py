import logging
import re

from astrbot.api import logger

logger = logging.getLogger("astrbot")


class ProfileSummaryService:
    def __init__(self, plugin, profile_manager=None):
        self.plugin = plugin
        self._profile_manager = profile_manager

    def _get_profile_manager(self):
        if self._profile_manager is None:
            self._profile_manager = getattr(self.plugin, "profile", None)
        return self._profile_manager

    async def get_profile_summary(self, group_id: str, user_id: str) -> str:
        """获取用户画像摘要"""
        try:
            manager = self._get_profile_manager()
            if not manager:
                return ""
            profile = await manager.load_profile(group_id, user_id)
            if not profile:
                return ""
            return profile
        except Exception as e:
            logger.warning(f"[ProfileSummary] get_profile_summary failed: {e}")
            return ""

    async def get_structured_summary(
        self,
        scope_id: str,
        user_id: str,
        max_items: int = 8,
    ) -> str:
        """获取结构化画像摘要用于注入

        返回格式：
        ## identity
        ...
        ## preferences
        ...
        """
        try:
            manager = self._get_profile_manager()
            if not manager:
                return ""
            profile = await manager.load_profile(scope_id, user_id)
            if not profile:
                return ""

            parsed = self._parse_structured_content(profile, max_items)
            if not parsed:
                return ""

            lines = []
            for section in ["identity", "preferences", "traits", "recent_updates", "long_term_notes"]:
                content = parsed.get(section, "")
                if content:
                    lines.append(f"## {section}")
                    for item in content:
                        lines.append(item)

            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"[ProfileSummary] get_structured_summary failed: {e}")
            return ""

    def _parse_structured_content(self, content: str, max_items: int = 8) -> dict:
        """解析结构化画像内容"""
        result = {}
        sections = ["identity", "preferences", "traits", "recent_updates", "long_term_notes"]

        for section in sections:
            pattern = rf"##\s*{section}\s*\n(.*?)(?=\n## |\Z)"
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if not match:
                result[section] = []
                continue

            section_text = match.group(1).strip()
            items = []
            for line in section_text.split("\n"):
                line = line.strip()
                if line.startswith("-"):
                    items.append(line)
                elif line.startswith("*"):
                    items.append("-" + line[1:].strip())
                elif line and not line.startswith("#"):
                    items.append(f"- {line}")

            result[section] = items[:max_items]

        return result
