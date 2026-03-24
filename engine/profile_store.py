import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger

logger = logging.getLogger("astrbot")

PROFILE_DIR = "data/self_evolution/profiles"


class ProfileStore:
    def __init__(self, plugin):
        self.plugin = plugin
        self._ensure_profile_dir()

    def _ensure_profile_dir(self):
        profile_path = Path(PROFILE_DIR)
        profile_path.mkdir(parents=True, exist_ok=True)

    def _get_profile_path(self, scope_id: str, user_id: str) -> Path:
        safe_user_id = re.sub(r'[\\/:*?"<>|]', "_", str(user_id))
        safe_scope_id = re.sub(r'[\\/:*?"<>|]', "_", str(scope_id))
        filename = f"{safe_scope_id}__{safe_user_id}.txt"
        return Path(PROFILE_DIR) / filename

    async def load_profile(self, scope_id: str, user_id: str) -> Optional[str]:
        """加载用户画像文件内容"""
        try:
            profile_path = self._get_profile_path(scope_id, user_id)
            if not profile_path.exists():
                return None
            return profile_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Profile] load_profile failed: {e}")
            return None

    async def save_profile(
        self,
        scope_id: str,
        user_id: str,
        content: str,
        nickname: str = "",
    ) -> bool:
        """保存用户画像"""
        try:
            profile_path = self._get_profile_path(scope_id, user_id)
            profile_path.write_text(content, encoding="utf-8")
            logger.debug(f"[Profile] 画像已保存: {profile_path}")
            return True
        except Exception as e:
            logger.warning(f"[Profile] save_profile failed: {e}")
            return False

    async def upsert_fact(
        self,
        scope_id: str,
        user_id: str,
        fact: str,
        fact_type: str,
        content: str,
    ) -> bool:
        """向画像中插入或更新事实"""
        try:
            existing = await self.load_profile(scope_id, user_id) or ""

            section_map = {
                "identity": "## identity",
                "preferences": "## preferences",
                "traits": "## traits",
                "recent_updates": "## recent_updates",
                "long_term_notes": "## long_term_notes",
            }
            section_header = section_map.get(fact_type, "## identity")

            new_entry = f"- {fact}\n"

            if section_header in existing:
                pattern = rf"({re.escape(section_header)}.*?)(\n## |\Z)"
                match = re.search(pattern, existing, re.DOTALL)
                if match:
                    section_content = match.group(1)
                    if fact in section_content:
                        return True
                    updated_section = section_content.rstrip() + "\n" + new_entry
                    existing = existing[: match.start()] + updated_section + match.group(2)
                else:
                    existing = existing.rstrip() + "\n" + new_entry
            else:
                if existing.rstrip().endswith("##"):
                    existing = existing.rstrip() + f" {fact_type}\n{new_entry}"
                else:
                    existing = existing.rstrip() + f"\n{section_header}\n{new_entry}"

            return await self.save_profile(scope_id, user_id, existing)
        except Exception as e:
            logger.warning(f"[Profile] upsert_fact failed: {e}")
            return False

    async def delete_profile(self, scope_id: str, user_id: str) -> bool:
        """删除用户画像"""
        try:
            profile_path = self._get_profile_path(scope_id, user_id)
            if profile_path.exists():
                profile_path.unlink()
                return True
            return False
        except Exception as e:
            logger.warning(f"[Profile] delete_profile failed: {e}")
            return False

    async def list_profiles(self) -> list[dict]:
        """列出所有画像"""
        try:
            profile_dir = Path(PROFILE_DIR)
            if not profile_dir.exists():
                return []
            profiles = []
            for f in profile_dir.glob("*.txt"):
                name = f.stem
                if "__" in name:
                    scope_id, user_id = name.split("__", 1)
                    profiles.append(
                        {
                            "scope_id": scope_id,
                            "user_id": user_id,
                            "path": str(f),
                            "modified": f.stat().st_mtime,
                        }
                    )
            return profiles
        except Exception as e:
            logger.warning(f"[Profile] list_profiles failed: {e}")
            return []
