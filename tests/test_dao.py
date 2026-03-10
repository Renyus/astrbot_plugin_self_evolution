import pytest
import pytest_asyncio
import sys
import os
import asyncio
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest_asyncio.fixture
async def test_db():
    """创建临时测试数据库"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest_asyncio.fixture
async def dao(test_db):
    """创建DAO实例"""
    from dao import SelfEvolutionDAO

    dao = SelfEvolutionDAO(test_db)
    await dao.init_db()
    yield dao
    await dao.close()


class TestDAO:
    """DAO数据库访问层测试"""

    async def test_init_db(self, test_db):
        """测试数据库初始化"""
        from dao import SelfEvolutionDAO

        dao = SelfEvolutionDAO(test_db)
        await dao.init_db()

        assert os.path.exists(test_db)

    async def test_save_and_get_memory(self, dao):
        """测试记忆存取"""
        await dao.save_memory(
            user_id="12345",
            content="测试记忆内容",
            memory_type="test",
            tags=["测试", "单元"],
        )

        memories = await dao.get_memories("12345", limit=10)

        assert len(memories) > 0
        assert any("测试记忆内容" in m["content"] for m in memories)

    async def test_get_memories_by_type(self, dao):
        """测试按类型获取记忆"""
        await dao.save_memory(
            user_id="12345", content="用户偏好", memory_type="preference"
        )

        memories = await dao.get_memories("12345", memory_type="preference")

        assert len(memories) > 0

    async def test_update_affinity(self, dao):
        """测试好感度更新"""
        await dao.update_affinity("12345", 50)

        affinity = await dao.get_affinity("12345")

        assert affinity == 50

    async def test_update_affinity_increment(self, dao):
        """测试好感度增量"""
        await dao.update_affinity("12345", 50)
        await dao.update_affinity("12345", 10)

        affinity = await dao.get_affinity("12345")

        assert affinity == 60

    async def test_save_and_get_profile(self, dao):
        """测试用户画像存取"""
        profile_data = {
            "name": "测试用户",
            "interests": ["编程", "AI"],
            "traits": ["理性", "外向"],
        }

        await dao.save_user_profile("12345", profile_data)

        profile = await dao.get_user_profile("12345")

        assert profile is not None
        assert profile["name"] == "测试用户"

    async def test_record_interaction(self, dao):
        """测试交互记录"""
        await dao.record_interaction(
            user_id="12345", group_id="67890", message="你好", is_positive=True
        )

        interactions = await dao.get_user_interactions("12345", limit=10)

        assert len(interactions) > 0

    async def test_record_negative_interaction(self, dao):
        """测试负面交互记录"""
        await dao.record_interaction(
            user_id="12345", group_id="67890", message="骂人内容", is_positive=False
        )

        interactions = await dao.get_user_interactions("12345", limit=10)

        assert len(interactions) > 0

    async def test_get_frequent_interactors(self, dao):
        """测试获取频繁互动者"""
        await dao.record_interaction("11111", "67890", "msg1", True)
        await dao.record_interaction("11111", "67890", "msg2", True)
        await dao.record_interaction("11111", "67890", "msg3", True)
        await dao.record_interaction("22222", "67890", "msg1", True)

        frequent = await dao.get_frequent_interactors("67890", limit=5)

        assert len(frequent) > 0
        user_ids = [u[0] for u in frequent]
        assert "11111" in user_ids

    async def test_get_user_groups(self, dao):
        """测试获取用户所在群组"""
        await dao.record_interaction("12345", "111111", "msg1", True)
        await dao.record_interaction("12345", "222222", "msg2", True)
        await dao.record_interaction("12345", "333333", "msg3", True)

        groups = await dao.get_user_groups("12345")

        assert len(groups) >= 3

    async def test_delete_memory(self, dao):
        """测试删除记忆"""
        await dao.save_memory("12345", "要删除的记忆", "test")

        memories = await dao.get_memories("12345")
        if memories:
            mem_id = memories[0]["id"]
            await dao.delete_memory(mem_id)

        memories_after = await dao.get_memories("12345")

        if memories_after:
            assert not any("要删除的记忆" in m["content"] for m in memories_after)

    async def test_clear_user_memory(self, dao):
        """测试清除用户记忆"""
        await dao.save_memory("99999", "记忆1", "test")
        await dao.save_memory("99999", "记忆2", "test")

        await dao.clear_user_memory("99999")

        memories = await dao.get_memories("99999")

        assert len(memories) == 0
