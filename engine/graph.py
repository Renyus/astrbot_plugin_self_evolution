import json
import logging
import asyncio
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, List, Set
from datetime import datetime

logger = logging.getLogger("astrbot")


class GraphRAG:
    """
    关系图谱 RAG 模块
    使用 NetworkX 风格的数据结构存储用户关系，用于增强 RAG 检索
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.graph_dir = plugin.data_dir / "graph"
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        self._graph_data: Dict[str, dict] = {}
        self._locks = defaultdict(asyncio.Lock)
        self._load_graph()

    @property
    def graph_enabled(self):
        return getattr(self.plugin, "graph_enabled", True)

    @property
    def graph_path(self):
        return self.graph_dir / "user_relations.json"

    def _load_graph(self):
        """从磁盘加载关系图谱"""
        if self.graph_path.exists():
            try:
                data = json.loads(self.graph_path.read_text(encoding="utf-8"))
                self._graph_data = data.get("nodes", {}) or {}
                logger.info(
                    f"[GraphRAG] 已加载 {len(self._graph_data)} 个节点的关系图谱"
                )
            except Exception as e:
                logger.warning(f"[GraphRAG] 加载关系图谱失败: {e}")
                self._graph_data = {}

    def _save_graph(self):
        """持久化关系图谱到磁盘"""
        try:
            data = {"nodes": self._graph_data, "updated_at": datetime.now().isoformat()}
            self.graph_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[GraphRAG] 保存关系图谱失败: {e}")

    def _get_node(self, user_id: str) -> dict:
        """获取或创建用户节点"""
        if user_id not in self._graph_data:
            self._graph_data[user_id] = {
                "user_id": user_id,
                "groups": [],
                "interactions": defaultdict(int),
                "last_seen": None,
                "traits": [],
                "created_at": datetime.now().isoformat(),
            }
        return self._graph_data[user_id]

    async def record_interaction(
        self, user_id: str, group_id: str, other_user_id: str = None
    ):
        """记录用户互动"""
        if not self.graph_enabled:
            return

        try:
            async with self._locks[user_id]:
                node = self._get_node(user_id)

                if group_id and group_id not in node["groups"]:
                    node["groups"].append(group_id)

                if other_user_id:
                    node["interactions"][other_user_id] = (
                        node["interactions"].get(other_user_id, 0) + 1
                    )

                node["last_seen"] = datetime.now().isoformat()

                self._graph_data[user_id] = node

            if other_user_id:
                async with self._locks[other_user_id]:
                    other_node = self._get_node(other_user_id)
                    if group_id and group_id not in other_node["groups"]:
                        other_node["groups"].append(group_id)
                    other_node["interactions"][user_id] = (
                        other_node["interactions"].get(user_id, 0) + 1
                    )
                    other_node["last_seen"] = datetime.now().isoformat()
                    self._graph_data[other_user_id] = other_node

            self._save_graph()
            logger.debug(f"[GraphRAG] 已记录用户 {user_id} 在群 {group_id} 的互动")

        except Exception as e:
            logger.warning(f"[GraphRAG] 记录互动失败: {e}")

    async def get_user_groups(self, user_id: str) -> List[str]:
        """获取用户所在的所有群"""
        node = self._graph_data.get(user_id, {})
        return node.get("groups", [])

    async def get_frequent_interactors(
        self, user_id: str, limit: int = 5
    ) -> List[tuple]:
        """获取与用户互动最频繁的用户列表"""
        node = self._graph_data.get(user_id, {})
        interactions = node.get("interactions", {})
        sorted_interactions = sorted(
            interactions.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_interactions[:limit]

    async def add_trait(self, user_id: str, trait: str):
        """为用户添加特征标签"""
        async with self._locks[user_id]:
            node = self._get_node(user_id)
            if trait not in node.get("traits", []):
                node.setdefault("traits", []).append(trait)
                self._graph_data[user_id] = node
                self._save_graph()

    async def get_user_info(self, user_id: str) -> str:
        """获取用户的关系图谱信息"""
        node = self._graph_data.get(user_id, {})
        if not node:
            return f"用户 {user_id} 暂无关系图谱记录。"

        groups = node.get("groups", [])
        traits = node.get("traits", [])
        last_seen = node.get("last_seen", "未知")
        interactions = node.get("interactions", {})

        top_interactions = sorted(
            interactions.items(), key=lambda x: x[1], reverse=True
        )[:5]

        result = [f"用户 {user_id} 的关系图谱："]
        result.append(f"- 所在群数: {len(groups)}")
        result.append(f"- 特征标签: {', '.join(traits) if traits else '暂无'}")
        result.append(f"- 最后活跃: {last_seen}")
        if top_interactions:
            result.append(
                f"- 频繁互动用户: {', '.join([f'{u}({c}次)' for u, c in top_interactions])}"
            )

        return "\n".join(result)

    async def find_common_groups(self, user_id_a: str, user_id_b: str) -> List[str]:
        """查找两个用户的共同群聊"""
        node_a = self._graph_data.get(user_id_a, {}).get("groups", [])
        node_b = self._graph_data.get(user_id_b, {}).get("groups", [])
        return list(set(node_a) & set(node_b))

    async def enhance_recall(self, user_id: str, query: str) -> str:
        """基于关系图谱增强记忆检索"""
        if not self.graph_enabled:
            return ""

        groups = await self.get_user_groups(user_id)
        frequent_users = await self.get_frequent_interactors(user_id)

        if not groups and not frequent_users:
            return ""

        enhancement = ["\n【关系图谱增强信息】"]
        if groups:
            enhancement.append(f"- 该用户活跃于 {len(groups)} 个群聊")
        if frequent_users:
            top_user = frequent_users[0][0]
            count = frequent_users[0][1]
            enhancement.append(f"- 与该用户互动最多的是 {top_user} ({count} 次)")

        enhancement.append("（此信息来自关系图谱，仅供参考）")
        return "\n".join(enhancement)

    async def get_group_members(self, group_id: str) -> List[str]:
        """获取群聊的所有已知成员"""
        members = []
        for user_id, node in self._graph_data.items():
            if group_id in node.get("groups", []):
                members.append(user_id)
        return members

    async def get_group_stats(self, group_id: str) -> dict:
        """获取群聊统计信息"""
        members = await self.get_group_members(group_id)
        if not members:
            return {"member_count": 0, "total_interactions": 0}

        total_interactions = 0
        for user_id in members:
            node = self._graph_data.get(user_id, {})
            interactions = node.get("interactions", {})
            total_interactions += sum(interactions.values())

        return {
            "member_count": len(members),
            "total_interactions": total_interactions,
            "members": members[:10],
        }

    async def cleanup_stale_nodes(self, days: int = 90):
        """清理长时间不活跃的节点"""
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)
        stale_users = []

        for user_id, node in self._graph_data.items():
            last_seen = node.get("last_seen")
            if last_seen:
                try:
                    last_time = datetime.fromisoformat(last_seen)
                    if last_time < cutoff:
                        stale_users.append(user_id)
                except Exception:
                    continue

        for user_id in stale_users:
            del self._graph_data[user_id]

        if stale_users:
            self._save_graph()
            logger.info(f"[GraphRAG] 已清理 {len(stale_users)} 个过期节点")

        return len(stale_users)
