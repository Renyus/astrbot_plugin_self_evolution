"""Unified command display data for text help."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


def _str_width(s: str) -> int:
    """计算字符串在等宽终端的视觉宽度（中文=2，英文=1）。"""
    return sum(2 if re.match(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", c) else 1 for c in s)


def _ljust(s: str, width: int) -> str:
    """左对齐，视觉宽度补空格。"""
    return s + " " * (width - _str_width(s))


CommandGroup = Literal[
    "base",
    "social",
    "meal",
    "profile",
    "sticker",
    "evolution",
    "database",
    "persona",
]


@dataclass
class HelpCommand:
    group: CommandGroup
    command: str
    desc: str
    admin_only: bool = False


HELP_CATALOG_VERSION = 4

GROUP_ORDER: list[CommandGroup] = [
    "base",
    "social",
    "meal",
    "profile",
    "sticker",
    "evolution",
    "database",
    "persona",
]

GROUP_NAMES: dict[CommandGroup, str] = {
    "base": "基础",
    "social": "互动",
    "meal": "群菜单",
    "profile": "画像",
    "sticker": "表情包",
    "evolution": "进化",
    "database": "数据库",
    "persona": "Persona",
}


_FULL_CATALOG: list[HelpCommand] = [
    HelpCommand("base", "/se help", "查看指令帮助"),
    HelpCommand("base", "/se version", "查看插件版本"),
    HelpCommand("base", "/reflect", "手动触发一次反思"),
    HelpCommand("base", "/今日老婆", "查看今日老婆"),
    HelpCommand("social", "/af show", "查看当前好感度"),
    HelpCommand("social", "/af debug <用户>", "查看详细好感度（@或ID）", admin_only=True),
    HelpCommand("social", "/af set <用户> <分数>", "强制设置好感度（@或ID）", admin_only=True),
    HelpCommand("social", "/san show", "查看当前 SAN 状态"),
    HelpCommand("social", "/san set [值]", "查看或设置 SAN", admin_only=True),
    HelpCommand("social", "/shut [分钟]", "让 AI 暂时闭嘴", admin_only=True),
    HelpCommand("meal", "/addmeal <菜名>", "添加群菜单菜品"),
    HelpCommand("meal", "/delmeal <菜名>", "删除指定菜品"),
    HelpCommand("meal", "/meal ban <用户>", "禁止某人加菜（@或ID）", admin_only=True),
    HelpCommand("meal", "/meal unban <用户>", "解除加菜限制（@或ID）", admin_only=True),
    HelpCommand("profile", "/profile view [用户ID]", "查看用户画像"),
    HelpCommand("profile", "/profile create [用户ID]", "创建画像"),
    HelpCommand("profile", "/profile update [用户ID]", "更新画像"),
    HelpCommand("profile", "/profile delete <用户ID>", "删除画像", admin_only=True),
    HelpCommand("profile", "/profile stats", "查看画像统计", admin_only=True),
    HelpCommand("sticker", "/sticker list [页码]", "查看表情包列表", admin_only=True),
    HelpCommand("sticker", "/sticker preview <UUID>", "预览指定表情包", admin_only=True),
    HelpCommand("sticker", "/sticker delete <UUID>", "删除指定表情包", admin_only=True),
    HelpCommand("sticker", "/sticker disable <UUID>", "禁用指定表情包", admin_only=True),
    HelpCommand("sticker", "/sticker enable <UUID>", "启用指定表情包", admin_only=True),
    HelpCommand("sticker", "/sticker clear", "清空全部表情包", admin_only=True),
    HelpCommand("sticker", "/sticker stats", "查看表情包统计", admin_only=True),
    HelpCommand("sticker", "/sticker sync", "同步本地表情包文件", admin_only=True),
    HelpCommand("sticker", "/sticker add", "把刚发送的图片加入表情包", admin_only=True),
    HelpCommand("sticker", "/sticker migrate", "迁移旧表情包数据", admin_only=True),
    HelpCommand("evolution", "/ev review [页码]", "查看待审核进化", admin_only=True),
    HelpCommand("evolution", "/ev approve <ID>", "批准指定进化", admin_only=True),
    HelpCommand("evolution", "/ev reject <ID>", "拒绝指定进化", admin_only=True),
    HelpCommand("evolution", "/ev clear", "清空待审核队列", admin_only=True),
    HelpCommand("evolution", "/ev stats [群ID]", "查看进化统计", admin_only=True),
    HelpCommand("database", "/db show", "查看数据库统计", admin_only=True),
    HelpCommand("database", "/db reset", "清空插件数据", admin_only=True),
    HelpCommand("database", "/db rebuild", "删除并重建数据库", admin_only=True),
    HelpCommand("database", "/db confirm", "确认执行危险操作", admin_only=True),
    HelpCommand("persona", "/ps state [群]", "只读当前人格状态", admin_only=True),
    HelpCommand("persona", "/ps status [群]", "推进后查看人格快照", admin_only=True),
    HelpCommand("persona", "/ps tick [群] [quality]", "手动推进人格时间（none/negative/positive）", admin_only=True),
    HelpCommand("persona", "/ps todo [群]", "查看当前脑内待办", admin_only=True),
    HelpCommand("persona", "/ps effects [群]", "查看当前状态效果", admin_only=True),
    HelpCommand(
        "persona", "/ps apply [q] [群]", "应用一次互动影响（q: bad/awkward/normal/good/relief/brief）", admin_only=True
    ),
    HelpCommand("persona", "/ps today [群]", "查看今日人格摘要", admin_only=True),
    HelpCommand("persona", "/ps consolidate [群] [日期]", "执行人格日结（格式: YYYY-MM-DD）", admin_only=True),
    HelpCommand("persona", "/ps think [群]", "手动触发 LLM 生成内心独白", admin_only=True),
]


def get_user_commands() -> list[HelpCommand]:
    return [cmd for cmd in _FULL_CATALOG if not cmd.admin_only]


def get_admin_commands() -> list[HelpCommand]:
    return _FULL_CATALOG


def get_commands_by_group(include_admin: bool = True) -> dict[CommandGroup, list[HelpCommand]]:
    source = _FULL_CATALOG if include_admin else get_user_commands()
    groups: dict[CommandGroup, list[HelpCommand]] = {group: [] for group in GROUP_ORDER}
    for cmd in source:
        groups[cmd.group].append(cmd)
    return groups


def format_text_help(is_admin: bool = False) -> str:
    groups = get_commands_by_group(include_admin=is_admin)
    lines = ["【Self-Evolution 指令帮助】", ""]

    for group in GROUP_ORDER:
        cmds = groups.get(group, [])
        if not cmds:
            continue
        max_cmd_width = max(_str_width(cmd.command) for cmd in cmds)
        lines.append(f"【{GROUP_NAMES[group]}】")
        for cmd in cmds:
            padded = _ljust(cmd.command, max_cmd_width)
            lines.append(f"{padded}  -  {cmd.desc}")
        lines.append("")

    lines.append("发送 /se help 查看帮助")
    return "\n".join(lines).strip()
