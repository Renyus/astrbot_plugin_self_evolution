"""
Scheduler Tasks - 定时任务回调实现
薄编排层：只负责选目标 scope、调具体模块、统一日志、统一异常处理、统一跳过原因。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Coroutine, Any

logger = logging.getLogger("astrbot")


@dataclass
class ScheduledTaskResult:
    task_name: str
    scope_id: str | None
    success: bool
    skipped: bool
    reason: str = ""
    elapsed_ms: float = 0


def _get_previous_day_window(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    current_dt = (now or datetime.now()).astimezone()
    end_dt = current_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=1)
    return start_dt, end_dt, start_dt.strftime("%Y-%m-%d")


def _dedupe_scopes(scopes):
    deduped = []
    for scope_id in scopes:
        normalized_scope_id = str(scope_id or "").strip()
        if normalized_scope_id and normalized_scope_id not in deduped:
            deduped.append(normalized_scope_id)
    return deduped


async def _fetch_known_private_scopes(plugin) -> list[str]:
    dao = getattr(plugin, "dao", None)
    if dao and hasattr(dao, "list_known_scopes"):
        return await dao.list_known_scopes(scope_type="private")
    return []


async def _fetch_groups_from_platform(plugin) -> list[str]:
    try:
        platform_insts = plugin.context.platform_manager.platform_insts
        if not platform_insts:
            return []

        platform = platform_insts[0]
        if not hasattr(platform, "get_client"):
            return []

        bot = platform.get_client()
        if not bot:
            return []

        result = await bot.call_action("get_group_list")
        if isinstance(result, list):
            groups_data = result
        elif isinstance(result, dict):
            groups_data = result.get("data", [])
        else:
            groups_data = []
        groups = [str(g.get("group_id", "")) for g in groups_data if g.get("group_id")]
        if groups:
            logger.debug(f"[Scheduler] 获取到平台群列表: {groups}")
        return groups
    except Exception as e:
        logger.debug(f"[Scheduler] 获取平台群列表失败: {e}")
        return []


async def _resolve_target_scopes(
    plugin,
    task_name: str,
    *,
    include_private: bool = True,
    include_groups: bool = True,
) -> tuple[list[str], str]:
    """
    统一 scope 发现逻辑，优先级：白名单 -> active_users -> 平台列表。

    Returns:
        (scopes, skip_reason)
    """
    whitelist = plugin.cfg.target_scopes
    if whitelist:
        all_scopes = [str(g) for g in whitelist]
        filtered = []
        for s in all_scopes:
            is_private = str(s).startswith("private_")
            if not include_private and is_private:
                continue
            if not include_groups and not is_private:
                continue
            filtered.append(s)
        scopes = filtered
        logger.debug(f"[Scheduler][{task_name}] 使用白名单（过滤后）: {scopes}")
        return scopes, ""

    active_users = getattr(getattr(plugin, "eavesdropping", None), "active_users", None) or {}

    if include_groups and not include_private:
        scopes = [g for g in active_users if not str(g).startswith("private_")]
        if scopes:
            logger.debug(f"[Scheduler][{task_name}] 使用 active_users 群列表: {scopes}")
            return scopes, ""

    if include_private and include_groups:
        scopes = list(active_users)
        if scopes:
            logger.debug(f"[Scheduler][{task_name}] 使用 active_users (含私聊): {scopes}")
            return scopes, ""

    if include_groups:
        scopes = await _fetch_groups_from_platform(plugin)
        if scopes:
            logger.debug(f"[Scheduler][{task_name}] 使用平台群列表: {scopes}")
            return scopes, ""

    return [], f"[Scheduler][{task_name}] 无目标 scope"


async def _run_task(
    name: str,
    coro_func: Callable[..., Coroutine],
    plugin,
    *,
    swallow_errors: bool = True,
    log_scope_count: bool = False,
    scope_count: int | None = None,
) -> ScheduledTaskResult:
    """
    统一任务运行包装器。

    - 起止日志
    - 耗时统计
    - 异常捕获（可配置）
    - 失败不中断其他任务
    """
    t0 = time.monotonic()
    scope_label = f", scope={scope_count}" if (log_scope_count and scope_count is not None) else ""
    logger.info(f"[Scheduler] 任务开始: {name}{scope_label}")

    try:
        await coro_func(plugin)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[Scheduler] 任务完成: {name} ({elapsed_ms:.1f}ms){scope_label}")
        return ScheduledTaskResult(task_name=name, scope_id=None, success=True, skipped=False, elapsed_ms=elapsed_ms)
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        if swallow_errors:
            logger.warning(f"[Scheduler] 任务异常（已捕获）: {name}, {e}{scope_label}", exc_info=True)
            return ScheduledTaskResult(
                task_name=name, scope_id=None, success=False, skipped=False, reason=str(e), elapsed_ms=elapsed_ms
            )
        else:
            logger.error(f"[Scheduler] 任务失败: {name}, {e}{scope_label}", exc_info=True)
            raise


async def scheduled_reflection(plugin) -> ScheduledTaskResult:
    """每日批处理任务 - 会话摘要生成 + 活跃用户画像更新"""
    return await _run_task(
        "DailyReflection",
        _reflection_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
    )


async def scheduled_affinity_recovery(plugin) -> ScheduledTaskResult:
    """每日好感度恢复任务 - 独立于批处理运行"""
    return await _run_task(
        "AffinityRecovery",
        _affinity_recovery_impl,
        plugin,
        swallow_errors=True,
    )


async def _affinity_recovery_impl(plugin):
    await plugin.dao.recover_all_affinity(recovery_amount=2)
    logger.debug('[Scheduler] 已执行每日"大赦天下"：所有负面评分用户好感度已小幅回升。')


async def _reflection_impl(plugin):
    scopes, skip_reason = await _resolve_target_scopes(plugin, "DailyReflection")
    if not scopes:
        logger.debug(f"[Scheduler] DailyReflection 跳过: {skip_reason}")
        return

    await plugin.dao.init_db()
    scopes = _dedupe_scopes(scopes)

    private_scopes = await _fetch_known_private_scopes(plugin)
    all_scopes = _dedupe_scopes(scopes + private_scopes)

    result = await plugin.daily_batch.run_daily_batch(all_scopes)
    logger.info(
        f"[Scheduler] 每日批处理完成: 会话{result['groups_processed']}个, 用户{result['users_processed']}个, 报告{result['reports_saved']}份"
    )


async def scheduled_san_analyze(plugin) -> ScheduledTaskResult:
    """SAN 分析定时任务 - 分析群状态动态调整 SAN 值，支持热更新"""
    return await _run_task(
        "SANAnalyze",
        _san_analyze_impl,
        plugin,
        swallow_errors=True,
    )


async def _san_analyze_impl(plugin):
    san_interval = plugin.cfg.san_analyze_interval
    new_cron = f"*/{san_interval} * * * *"
    try:
        cron_mgr = plugin.context.cron_manager
        jobs = await cron_mgr.list_jobs()
        for job in jobs:
            if job.name == "SelfEvolution_SANAnalyze":
                if job.cron_expression != new_cron:
                    await cron_mgr.update_job(job.job_id, cron_expression=new_cron)
                    logger.debug(f"[Scheduler] SAN cron 热更新: {job.cron_expression} -> {new_cron}")
                break
    except Exception as e:
        logger.warning(f"[Scheduler] SAN cron 热更新检查失败: {e}")

    await plugin.san_system.analyze_all_groups()


async def scheduled_memory_summary(plugin) -> ScheduledTaskResult:
    """每日会话总结任务"""
    return await _run_task(
        "MemorySummary",
        _memory_summary_impl,
        plugin,
        swallow_errors=True,
    )


async def _memory_summary_impl(plugin):
    await plugin.memory.daily_summary()


async def scheduled_interject(plugin) -> ScheduledTaskResult:
    """主动插嘴定时任务 - 获取群消息，LLM判断是否需要插嘴"""
    scopes, skip_reason = await _resolve_target_scopes(plugin, "Interject", include_private=False, include_groups=True)
    if not scopes:
        logger.debug(f"[Scheduler] Interject 跳过: {skip_reason}")
        return ScheduledTaskResult(task_name="Interject", scope_id=None, success=True, skipped=True, reason=skip_reason)

    return await _run_task(
        "Interject",
        _interject_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
        scope_count=len(scopes),
    )


async def _interject_impl(plugin):
    scopes, _ = await _resolve_target_scopes(plugin, "Interject", include_private=False, include_groups=True)
    for group_id in scopes:
        await plugin.eavesdropping.interject_check_group(group_id)


async def scheduled_profile_cleanup(plugin) -> ScheduledTaskResult:
    """清理过期用户画像"""
    return await _run_task(
        "ProfileCleanup",
        _profile_cleanup_impl,
        plugin,
        swallow_errors=True,
    )


async def _profile_cleanup_impl(plugin):
    await plugin.profile.cleanup_expired_profiles()


async def scheduled_profile_build(plugin) -> ScheduledTaskResult:
    """定时批量构建用户画像"""
    if not plugin.cfg.auto_profile_enabled:
        logger.info("[Scheduler] ProfileBuild 跳过: auto_profile_enabled=False")
        return ScheduledTaskResult(
            task_name="ProfileBuild", scope_id=None, success=True, skipped=True, reason="auto_profile_enabled=False"
        )

    return await _run_task(
        "ProfileBuild",
        _profile_build_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
    )


async def _profile_build_impl(plugin):
    scopes, skip_reason = await _resolve_target_scopes(
        plugin, "ProfileBuild", include_private=False, include_groups=True
    )
    if not scopes:
        logger.debug(f"[Scheduler] ProfileBuild 跳过: {skip_reason}")
        return

    batch_size = plugin.cfg.auto_profile_batch_size
    batch_interval = plugin.cfg.auto_profile_batch_interval
    logger.debug(f"[Scheduler] ProfileBuild: {len(scopes)} 个群，批次大小 {batch_size}，间隔 {batch_interval} 分钟")

    for i in range(0, len(scopes), batch_size):
        batch = scopes[i : i + batch_size]
        logger.debug(f"[Scheduler] ProfileBuild 批次 {i // batch_size + 1}，群: {batch}")

        for group_id in batch:
            try:
                group_umo = plugin.get_group_umo(group_id) if hasattr(plugin, "get_group_umo") else None
                await plugin.profile.analyze_and_build_profiles(str(group_id), umo=group_umo)
            except Exception as e:
                logger.warning(f"[Scheduler] ProfileBuild 群 {group_id} 失败: {e}")

        if i + batch_size < len(scopes):
            logger.debug(f"[Scheduler] ProfileBuild 批次完成，等待 {batch_interval} 分钟...")
            await asyncio.sleep(batch_interval * 60)
