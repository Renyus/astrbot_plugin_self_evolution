import logging
from typing import List, Tuple

logger = logging.getLogger("astrbot")


class GraphRAG:
    """
    关系图谱 RAG 模块
    使用 SQLite 数据库存储用户关系，用于增强 RAG 检索
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.dao = plugin.dao

    @property
    def graph_enabled(self):
        return getattr(self.plugin, "graph_enabled", True)

    async def record_interaction(
        self, user_id: str, group_id: str, other_user_id: str = None
    ):
        """记录用户互动"""
        if not self.graph_enabled:
            return

        try:
            if group_id:
                await self.dao.record_interaction(str(user_id), None, str(group_id))
            if other_user_id and group_id:
                await self.dao.record_interaction(
                    str(user_id), str(other_user_id), str(group_id)
                )
            logger.debug(f"[GraphRAG] 已记录用户 {user_id} 在群 {group_id} 的互动")
        except Exception as e:
            logger.warning(f"[GraphRAG] 记录互动失败: {e}")

    async def get_user_groups(self, user_id: str) -> List[str]:
        """获取用户所在的所有群"""
        try:
            return await self.dao.get_user_groups(str(user_id))
        except Exception as e:
            logger.warning(f"[GraphRAG] 获取用户群组失败: {e}")
            return []

    async def get_frequent_interactors(
        self, user_id: str, limit: int = 5
    ) -> List[Tuple[str, int]]:
        """获取与用户互动最频繁的用户列表"""
        try:
            return await self.dao.get_frequent_interactors(str(user_id), limit)
        except Exception as e:
            logger.warning(f"[GraphRAG] 获取频繁互动用户失败: {e}")
            return []

    async def add_trait(self, user_id: str, trait: str):
        """为用户添加特征标签（保留接口，暂未实现）"""
        logger.debug(f"[GraphRAG] 添加特征标签，用户 {user_id}: {trait}")
        pass

    async def get_user_info(self, user_id: str) -> str:
        """获取用户的关系图谱信息"""
        logger.debug(f"[GraphRAG] 获取用户信息: {user_id}")
        try:
            groups = await self.get_user_groups(user_id)
            frequent = await self.get_frequent_interactors(user_id)
            if not groups and not frequent:
                return f"用户 {user_id} 暂无关系图谱记录。"

            result = [f"用户 {user_id} 的关系图谱："]
            result.append(f"- 所在群数: {len(groups)}")
            if frequent:
                result.append(
                    f"- 频繁互动用户: {', '.join([f'{u}({c}次)' for u, c in frequent])}"
                )
            return "\n".join(result)
        except Exception as e:
            logger.warning(f"[GraphRAG] 获取用户信息失败: {e}")
            return f"获取用户 {user_id} 关系图谱失败"

    async def find_common_groups(self, user_id_a: str, user_id_b: str) -> List[str]:
        """查找两个用户的共同群聊"""
        logger.debug(f"[GraphRAG] 查找共同群聊: {user_id_a} <-> {user_id_b}")
        groups_a = set(await self.get_user_groups(user_id_a))
        groups_b = set(await self.get_user_groups(user_id_b))
        result = list(groups_a & groups_b)
        logger.debug(f"[GraphRAG] 找到 {len(result)} 个共同群聊")
        return result

    async def enhance_recall(self, user_id: str, query: str) -> str:
        """基于关系图谱增强记忆检索"""
        if not self.graph_enabled:
            return ""

        logger.debug(f"[GraphRAG] 增强检索，用户 {user_id}: {query[:30]}")
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
        result = "\n".join(enhancement)
        logger.debug(f"[GraphRAG] 检索增强完成，返回 {len(result)} 字符")
        return result

    async def get_group_members(self, group_id: str) -> List[str]:
        """获取群聊的所有已知成员"""
        logger.debug(f"[GraphRAG] 获取群成员: {group_id}")
        return []

    async def get_group_stats(self, group_id: str) -> dict:
        """获取群聊统计信息"""
        logger.debug(f"[GraphRAG] 获取群统计: {group_id}")
        return {"member_count": 0, "total_interactions": 0, "members": []}

    async def cleanup_stale_nodes(self, days: int = 90):
        """清理长时间不活跃的节点（SQLite 自动管理）"""
        pass
