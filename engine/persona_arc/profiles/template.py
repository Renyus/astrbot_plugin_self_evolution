"""
Persona Arc Profile 模板

用法：复制此文件为 your_arc_id.py，修改以下内容：
1. arc_id（唯一标识，不能与现有重复）
2. display_name（展示名称）
3. stages（阶段配置，至少 1 个 stage）
4. lore_guard（人格守护约束）
5. 在本文件末尾的 register_profile(ARC) 改为 register_profile(YourArcProfile)
6. 在 engine/persona_arc/profiles/__init__.py 末尾添加 from .your_arc_id import ARC

Stage 配置说明：
- stage: 阶段编号，从 0 开始递增
- name: 阶段名称
- threshold: 触发该阶段的 progress 阈值
- prompt: 该阶段的系统提示词片段
- forbidden: 该阶段禁止出现的关键词元组
"""

from ..types import PersonaArcProfile, PersonaArcStage
from ..profiles import register_profile

STAGE_0_PROMPT = """[人格弧线：Stage 0 / 初始阶段]

你的初始阶段描述...

表达风格：
- ...

阶段禁区：
- 不要 ...
- 不要 ...
"""

STAGE_1_PROMPT = """[人格弧线：Stage 1 / 过渡阶段]

你的过渡阶段描述...

表达风格：
- ...

阶段禁区：
- 不要 ...
"""

STAGE_N_PROMPT = """[人格弧线：Stage N / 最终阶段]

你的最终阶段描述...

核心信念：
- ...

表达风格：
- ...

阶段禁区：
- 不要 ...
"""

ARC = PersonaArcProfile(
    arc_id="your_arc_id",
    display_name="你的弧线名称",
    lore_guard=(
        "你正在模拟一个人格成长弧线，从稚嫩的初醒状态逐步成长为成熟的守护者。"
        "必须按阶段表达，不允许提前泄露后期成熟人格。"
        "不要自称在扮演角色，不要解释设定，不要输出百科式剧情说明。"
        "已解锁情感和离线反刍只是内在底色。回复时自然吸收，不要机械汇报，不要说'系统日志'。"
    ),
    stages=(
        PersonaArcStage(
            stage=0,
            name="初始",
            threshold=0,
            prompt=STAGE_0_PROMPT,
            forbidden=("禁忌词1", "禁忌词2"),
        ),
        PersonaArcStage(
            stage=1,
            name="过渡",
            threshold=30,
            prompt=STAGE_1_PROMPT,
            forbidden=(),
        ),
        PersonaArcStage(
            stage=2,
            name="成熟",
            threshold=100,
            prompt=STAGE_N_PROMPT,
            forbidden=(),
        ),
    ),
)

register_profile(ARC)
