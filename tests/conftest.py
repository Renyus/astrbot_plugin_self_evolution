import pytest

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def event_loop_policy():
    """设置事件循环策略"""
    import asyncio

    return asyncio.get_event_loop_policy()
