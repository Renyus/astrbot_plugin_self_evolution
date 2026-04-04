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

    if include_groups and not include_private:
        active_scopes = getattr(plugin, "eavesdropping", None) and plugin.eavesdropping.get_active_scopes() or []
        scopes = [g for g in active_scopes if not str(g).startswith("private_")]
        if scopes:
            logger.debug(f"[Scheduler][{task_name}] 使用 get_active_scopes 群列表: {scopes}")
            return scopes, ""

    if include_private and include_groups:
        scopes = getattr(plugin, "eavesdropping", None) and plugin.eavesdropping.get_active_scopes() or []
        if scopes:
            logger.debug(f"[Scheduler][{task_name}] 使用 get_active_scopes (含私聊): {scopes}")
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
    if not getattr(getattr(plugin, "cfg", None), "reflection_enabled", True):
        logger.info("[Scheduler] DailyReflection 跳过: reflection_enabled=False")
        return ScheduledTaskResult(
            task_name="DailyReflection", scope_id=None, success=True, skipped=True, reason="reflection_enabled=False"
        )
    return await _run_task(
        "DailyReflection",
        _reflection_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
    )


async def scheduled_affinity_recovery(plugin) -> ScheduledTaskResult:
    """每日好感度恢复任务 - 独立于反思模块运行"""
    if not getattr(getattr(plugin, "cfg", None), "affinity_recovery_enabled", True):
        logger.info("[Scheduler] AffinityRecovery 跳过: affinity_recovery_enabled=False")
        return ScheduledTaskResult(
            task_name="AffinityRecovery",
            scope_id=None,
            success=True,
            skipped=True,
            reason="affinity_recovery_enabled=False",
        )
    return await _run_task(
        "AffinityRecovery",
        _affinity_recovery_impl,
        plugin,
        swallow_errors=True,
    )


async def _affinity_recovery_impl(plugin):
    await plugin.dao.recover_all_affinity(recovery_amount=2)
    logger.debug("[Scheduler] 已执行每日好感度恢复：所有负面评分用户好感度已小幅回升。")


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
    if not getattr(getattr(plugin, "cfg", None), "memory_enabled", True):
        logger.info("[Scheduler] MemorySummary 跳过: memory_enabled=False")
        return ScheduledTaskResult(
            task_name="MemorySummary", scope_id=None, success=True, skipped=True, reason="memory_enabled=False"
        )
    return await _run_task(
        "MemorySummary",
        _memory_summary_impl,
        plugin,
        swallow_errors=True,
    )


async def _memory_summary_impl(plugin):
    summarizer = getattr(plugin, "session_memory_summarizer", None)
    if summarizer:
        await summarizer.daily_summary()


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
        await plugin.eavesdropping.check_engagement(group_id)


async def scheduled_profile_cleanup(plugin) -> ScheduledTaskResult:
    """清理过期用户画像"""
    return await _run_task(
        "ProfileCleanup",
        _profile_cleanup_impl,
        plugin,
        swallow_errors=True,
    )


async def _profile_cleanup_impl(plugin):
    manager = getattr(plugin, "profile", None)
    if manager:
        await manager.cleanup_expired_profiles()


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
                profile_manager = getattr(plugin, "profile", None)
                if profile_manager and hasattr(profile_manager, "analyze_and_build_profiles"):
                    await profile_manager.analyze_and_build_profiles(str(group_id), umo=group_umo)
            except Exception as e:
                logger.warning(f"[Scheduler] ProfileBuild 群 {group_id} 失败: {e}")

        if i + batch_size < len(scopes):
            logger.debug(f"[Scheduler] ProfileBuild 批次完成，等待 {batch_interval} 分钟...")
            await asyncio.sleep(batch_interval * 60)


async def scheduled_persona_consolidation(plugin) -> ScheduledTaskResult:
    """人格日结定时任务 - 每天凌晨对所有活跃 scope 执行日结"""
    consolidator = getattr(plugin, "persona_consolidator", None)
    if not consolidator:
        logger.info("[Scheduler] PersonaConsolidation 跳过: persona_consolidator 不可用")
        return ScheduledTaskResult(
            task_name="PersonaConsolidation",
            scope_id=None,
            success=True,
            skipped=True,
            reason="persona_consolidator unavailable",
        )

    scopes, skip_reason = await _resolve_target_scopes(
        plugin, "PersonaConsolidation", include_private=True, include_groups=True
    )
    if not scopes:
        logger.debug(f"[Scheduler] PersonaConsolidation 跳过: {skip_reason}")
        return ScheduledTaskResult(
            task_name="PersonaConsolidation",
            scope_id=None,
            success=True,
            skipped=True,
            reason=skip_reason,
        )

    return await _run_task(
        "PersonaConsolidation",
        _persona_consolidation_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
        scope_count=len(scopes),
    )


async def _persona_consolidation_impl(plugin):
    consolidator = getattr(plugin, "persona_consolidator", None)
    if not consolidator:
        return

    scopes, _ = await _resolve_target_scopes(plugin, "PersonaConsolidation", include_private=True, include_groups=True)
    scopes = _dedupe_scopes(scopes)

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    success_count = 0
    for scope_id in scopes:
        try:
            await consolidator.consolidate_scope(scope_id, yesterday)
            success_count += 1
        except Exception as e:
            logger.warning(f"[Scheduler] PersonaConsolidation scope={scope_id} 失败: {e}")

    logger.info(f"[Scheduler] PersonaConsolidation 完成: {success_count}/{len(scopes)} 个 scope")


async def scheduled_persona_thought(plugin) -> ScheduledTaskResult:
    """人格思维生成定时任务 - 每12小时为所有活跃 scope 生成内心独白"""
    persona_sim = getattr(plugin, "persona_sim", None)
    if not persona_sim:
        logger.info("[Scheduler] PersonaThought 跳过: persona_sim 不可用")
        return ScheduledTaskResult(
            task_name="PersonaThought",
            scope_id=None,
            success=True,
            skipped=True,
            reason="persona_sim unavailable",
        )

    scopes, skip_reason = await _resolve_target_scopes(
        plugin, "PersonaThought", include_private=True, include_groups=True
    )
    if not scopes:
        logger.debug(f"[Scheduler] PersonaThought 跳过: {skip_reason}")
        return ScheduledTaskResult(
            task_name="PersonaThought",
            scope_id=None,
            success=True,
            skipped=True,
            reason=skip_reason,
        )

    return await _run_task(
        "PersonaThought",
        _persona_thought_impl,
        plugin,
        swallow_errors=True,
        log_scope_count=True,
        scope_count=len(scopes),
    )


async def _persona_thought_impl(plugin):
    persona_sim = getattr(plugin, "persona_sim", None)
    if not persona_sim:
        return

    scopes, _ = await _resolve_target_scopes(plugin, "PersonaThought", include_private=True, include_groups=True)
    scopes = _dedupe_scopes(scopes)

    success_count = 0
    for scope_id in scopes:
        try:
            await persona_sim.generate_thought_process(scope_id)
            success_count += 1
        except Exception as e:
            logger.warning(f"[Scheduler] PersonaThought scope={scope_id} 失败: {e}")

    logger.info(f"[Scheduler] PersonaThought 完成: {success_count}/{len(scopes)} 个 scope")


async def scheduled_github_check(plugin):
    """检查 GitHub 仓库更新，有新 commit 则发群通知。"""
    notify_group_ids = plugin.cfg.update_notify_group_id
    if not notify_group_ids:
        logger.debug("[Scheduler] GitHub 检查跳过：未配置通知群")
        return

    repo = plugin.cfg.update_notify_repo

    import urllib.request
    import json

    url = f"https://api.github.com/repos/{repo}/commits?per_page=3"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AstrBot-SelfEvolution"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            commits = json.loads(resp.read())
    except Exception as e:
        logger.warning(f"[Scheduler] GitHub API 请求失败: {e}")
        return

    if not commits:
        return

    latest_sha = commits[0]["sha"]
    cache_key = "self_evolution_github_last_sha"

    from astrbot.core import sp

    last_sha = await sp.get_async(scope="plugin", scope_id="global", key=cache_key, default=None)
    if last_sha is None:
        await sp.put_async(scope="plugin", scope_id="global", key=cache_key, value=latest_sha)
        logger.info(f"[Scheduler] GitHub 检查首次记录: {latest_sha[:7]}")
        return
    if last_sha == latest_sha:
        logger.debug(f"[Scheduler] GitHub 无新 commit: {latest_sha[:7]}")
        return

    await sp.put_async(scope="plugin", scope_id="global", key=cache_key, value=latest_sha)

    commit_lines = []
    for commit in commits[:3]:
        msg = commit["commit"]["message"].split("\n")[0]
        author = commit["commit"]["author"]["name"]
        date = commit["commit"]["author"]["date"][:10]
        commit_lines.append(f"- {msg} ({author}, {date})")

    notify_text = f"【插件更新通知】\n仓库有新提交：\n" + "\n".join(commit_lines)

    try:
        platform_insts = plugin.context.platform_manager.platform_insts
        if platform_insts:
            bot = platform_insts[0].get_client()
            if bot:
                for group_id in notify_group_ids:
                    try:
                        await bot.send_group_msg(
                            group_id=int(group_id), message=[{"type": "text", "data": {"text": notify_text}}]
                        )
                        logger.info(f"[Scheduler] GitHub 更新已通知群 {group_id}")
                    except Exception as ge:
                        logger.warning(f"[Scheduler] 通知群 {group_id} 失败: {ge}")
    except Exception as e:
        logger.warning(f"[Scheduler] GitHub 更新通知发送失败: {e}")
