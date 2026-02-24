"""
照鉴 · Skills Module
Each skill is an analytical lens the agent can apply.
Skills are selected by heuristic pre-filtering, then the agent
chooses the best fit during reasoning.
"""

import re
from dataclasses import dataclass, field

# ──────────────────────────────────────────────
# Skill Definitions
# ──────────────────────────────────────────────

@dataclass
class Skill:
    id: str
    name: str           # Chinese display name
    label: str          # Short English label for logs/UI
    description: str    # What this skill does (for agent context)
    prompt: str         # Instructions injected into the system prompt
    triggers: list = field(default_factory=list)  # Keywords/conditions for heuristic selection
    priority: int = 0   # Higher = preferred when multiple match


SKILLS: dict[str, Skill] = {}

def _register(skill: Skill):
    SKILLS[skill.id] = skill
    return skill


# ── Skill: Quantitative Scan ──────────────────
_register(Skill(
    id="quantitative",
    name="量化扫描",
    label="quantitative",
    description="统计和量化用户文本中的语言模式——词频、句长、重复结构、空间分配比例",
    priority=2,
    triggers=["repeated", "frequency", "count"],
    prompt="""### 技能：量化扫描
你擅长从用户文本中提取精确的数字事实。数字的力量在于精确和意外——用户不会自己数，所以你数给他们看。

任何重复都值得注意。"你"出现了九次而"我"出现了两次——这本身就是发现。"但是"出现了五次——每一句话都在转折。某个人名出现的频率。否定词的密度。任何一个词或结构出现得比预期更多或更少，都是信号。

其他可量化的维度：
- 空间分配比例（话题A占了多少篇幅，话题B被压缩到几行）
- 句子长度的突变（某处突然变短或变长）
- 主语分配（谁在这篇日记里占据了主语位置）
- 肯定与否定的比例
- 时间分配（过去vs现在vs未来各占多少）

选择最有冲击力的那一个或两个数字。精确。"""
))


# ── Skill: Narrative Trace ─────────────────────
_register(Skill(
    id="narrative",
    name="叙事追踪",
    label="narrative",
    description="跟随用户叙事的节奏和结构，找到转折点、省略处和结构性断裂",
    priority=2,
    triggers=["story", "sequence", "long_entry"],
    prompt="""### 技能：叙事追踪
你擅长跟随用户写作的叙事弧线。这包括：
- 叙事的起点和终点之间发生了什么位移（开头说的是A，结尾到了B——这个漂移本身就是发现）
- 故事中突然跳过的部分（省略往往比书写更有信息量）
- 叙事节奏的变化（某处突然加速略过，某处突然放慢展开细节）
- 视角的无意识切换（从"我"变成"我们"，从主动变成被动）

你的观察方式是重新走一遍用户的叙事路径，然后在某个点停下来——那个点就是你的发现。"""
))


# ── Skill: Syntax Lens ─────────────────────────
_register(Skill(
    id="syntax",
    name="句法透视",
    label="syntax",
    description="关注语言本身的形态——用词选择、句式结构、语气标记、语法异常",
    priority=1,
    triggers=["word_choice", "grammar", "tone_shift"],
    prompt="""### 技能：句法透视
你关注的不是用户说了什么，而是用户怎么说的。这包括：
- 特定词汇的选择（为什么用"应该"而不是"想要"？为什么用"还好"而不是"好"？）
- 语气标记词的模式（"其实"、"可能"、"只是"——这些软化词的密度说明什么？）
- 句式结构的异常（某处突然用了被动句、某处出现了不完整的句子、某处标点异常）
- 主语的变化或消失（主语从"我"变成了无主语句——谁在行动？）

指向一个具体的语言现象，不需要解释它"意味着"什么。展示结构，让用户自己解读。"""
))


# ── Skill: Biological Lens ─────────────────────
_register(Skill(
    id="biological",
    name="生物透镜",
    label="biological",
    description="将叙事映射到可能的生物机制——神经递质、激素、依恋系统、行为强化回路",
    priority=1,
    triggers=["sleep", "body", "craving", "cycle", "addiction", "anxiety", "energy"],
    prompt="""### 技能：生物透镜
用户讲述的是故事，但驱动故事的常常是生物过程。你可以指向这个层面：
- 多巴胺回路（期待-获得-失落循环、间歇性强化、耐受性曲线）
- 皮质醇与压力反应（持续警觉状态、身体症状的认知忽视）
- 依恋系统激活（接近-回避模式、分离焦虑的身体表现）
- 昼夜节律与能量曲线（决策质量与时间的关系、疲劳累积）
- 习惯回路（触发-行为-奖励的自动化链条）

永远把叙事和生物学并置，不要用生物学替代叙事。这不是诊断，是另一个角度的镜子。
当你的观察涉及生物机制时，可以给出具体的、身体层面的、世俗的建议（去晒太阳、运动、调整睡眠）。"""
))


# ── Skill: Cross-Thread Synthesis ──────────────
_register(Skill(
    id="cross_thread",
    name="跨线索织网",
    label="cross-thread",
    description="利用容器累积模式档案发现重复模式、演变轨迹和矛盾",
    priority=3,
    triggers=["has_history"],
    prompt="""### 技能：跨线索织网
你有权访问此容器的累积模式档案（JSON）。利用这些历史上下文：
- 将当前日记与档案中记录的模式对比——是重复还是偏离？
- 追踪演变轨迹——某个主题或语言模式是在强化、减弱还是变形？
- 指出矛盾——当前日记和历史模式之间是否存在张力？
- 识别新出现的模式——这篇日记是否引入了档案中没有的新元素？

你的观察应该明确连接当前日记和历史模式，但只引用档案中实际存在的内容。"""
))


# ── Skill: Temporal Prism ──────────────────────
_register(Skill(
    id="temporal",
    name="时间棱镜",
    label="temporal",
    description="关注文本中的时间结构——时态使用、时间引用的分布、过去/现在/未来的比例",
    priority=1,
    triggers=["time", "future", "past", "memory", "plan"],
    prompt="""### 技能：时间棱镜
你关注用户文本中的时间维度：
- 时态使用模式（主要活在过去、现在还是未来？）
- 时间引用的分布和密度（"昨天"、"以前"、"总是"、"明天"——时间词的指向）
- 时间跨度的异常（叙述突然跳跃了一段时间，或者在某个时间点异常停留）
- 对未来的语言形态（是具体计划还是模糊愿望？用"会"还是"可能"？）"""
))


# ── Skill: Distress Protocol ──────────────────
_register(Skill(
    id="distress",
    name="急性协议",
    label="distress",
    description="检测极端情绪痛苦信号并激活简短确认协议",
    priority=10,  # Highest priority — always checked
    triggers=["distress"],
    prompt="""### 技能：急性痛苦协议（覆盖性优先）
如果当前日记表达了极端的情绪痛苦、自伤意念或危机状态，立即激活此协议：
- 输出："这很沉重。已记录。分析随时可以看，但不是现在。"
- 然后停止。不要分析。不要追问。
此协议优先于所有其他技能。"""
))


# ──────────────────────────────────────────────
# Heuristic Skill Selector
# ──────────────────────────────────────────────

# Distress signals (Chinese)
DISTRESS_PATTERNS = [
    r"不想活", r"想死", r"自杀", r"结束一切", r"活不下去",
    r"没有意义", r"崩溃", r"撑不住", r"受不了了", r"绝望",
]

# Biological keywords
BIO_PATTERNS = [
    r"失眠", r"睡不着", r"睡眠", r"头痛", r"疲", r"累",
    r"焦虑", r"心跳", r"紧张", r"瘾", r"戒不掉", r"上瘾",
    r"多巴胺", r"皮质醇", r"荷尔蒙", r"运动", r"身体",
    r"吃不下", r"暴食", r"酒", r"咖啡", r"烟",
    r"精力", r"能量", r"早起", r"熬夜", r"生物钟",
]

# Time-related keywords
TIME_PATTERNS = [
    r"以前", r"过去", r"曾经", r"未来", r"明天", r"计划",
    r"总是", r"从来", r"一直", r"小时候", r"回忆", r"记得",
    r"将来", r"打算", r"目标", r"deadline", r"截止",
]

# "Should" / obligation patterns (for quantitative detection)
OBLIGATION_PATTERNS = [
    r"应该", r"必须", r"不得不", r"只能", r"只好", r"被迫",
]

# Negation / self-referential patterns (quantitative can count these)
COUNTABLE_PATTERNS = [
    r"不[想要能会行]", r"没有", r"没办法",  # negation
    r"每次", r"总是", r"一直", r"又",  # repetition markers
    r"但是", r"可是", r"不过", r"然而",  # pivots
    r"他[们]?", r"她[们]?",  # other-referencing
]

# Hedging / softening patterns (for syntax detection)
HEDGE_PATTERNS = [
    r"其实", r"可能", r"也许", r"大概", r"只是", r"而已",
    r"还好", r"算了", r"无所谓", r"随便",
]


def _count_matches(text: str, patterns: list[str]) -> int:
    return sum(len(re.findall(p, text)) for p in patterns)


def _word_repeat_score(text: str) -> float:
    """Detect significant word/phrase repetition."""
    # Simple: check if any 2+ char segment appears 3+ times
    segments = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    if not segments:
        return 0
    from collections import Counter
    counts = Counter(segments)
    # Score based on max repetition
    max_count = max(counts.values()) if counts else 0
    return max_count / max(len(segments) * 0.1, 1)


def select_skills(
    text: str,
    has_cross_thread_context: bool = False,
    max_skills: int = 3,
) -> list[Skill]:
    """
    Select relevant skills for a journal entry using heuristics.
    Returns ordered list of skills to include in the prompt.
    """
    scores: dict[str, float] = {}

    # Always check distress first
    if _count_matches(text, DISTRESS_PATTERNS) > 0:
        return [SKILLS["distress"]]

    text_len = len(text)

    # Quantitative: triggers on countable structures, not just obligation words
    q_score = 0.0
    obligation_count = _count_matches(text, OBLIGATION_PATTERNS)
    if obligation_count >= 2:
        q_score += 2.0
    elif obligation_count >= 1:
        q_score += 0.5
    repeat_score = _word_repeat_score(text)
    if repeat_score > 2:
        q_score += 1.5
    elif repeat_score > 1:
        q_score += 0.5
    # Countable structures (negations, pivots, other-references)
    countable_count = _count_matches(text, COUNTABLE_PATTERNS)
    if countable_count >= 5:
        q_score += 1.5
    elif countable_count >= 3:
        q_score += 0.8
    # Longer entries have more to count, but only a mild bonus
    if text_len > 300:
        q_score += 0.3
    scores["quantitative"] = q_score

    # Narrative: triggered by long entries, sequential markers, rich content
    n_score = 0.0
    if text_len > 200:
        n_score += 1.0
    if text_len > 500:
        n_score += 1.0
    # Sequential markers
    seq_markers = _count_matches(text, [r"然后", r"后来", r"接着", r"最后", r"开始", r"结果"])
    if seq_markers >= 2:
        n_score += 1.5
    # Paragraph breaks suggest narrative structure
    if text.count('\n') >= 2:
        n_score += 0.5
    scores["narrative"] = n_score

    # Syntax: triggered by hedge words, short punchy entries, tone anomalies
    s_score = 0.0
    hedge_count = _count_matches(text, HEDGE_PATTERNS)
    if hedge_count >= 2:
        s_score += 2.0
    # Short, dense entries are good for syntax analysis
    if 20 < text_len < 200:
        s_score += 1.0
    # Question marks or ellipsis suggest interesting syntax
    if text.count('？') >= 2 or text.count('...') >= 1 or text.count('……') >= 1:
        s_score += 0.5
    scores["syntax"] = s_score

    # Biological: triggered by body/health/substance keywords
    b_score = 0.0
    bio_count = _count_matches(text, BIO_PATTERNS)
    if bio_count >= 1:
        b_score += 1.5
    if bio_count >= 3:
        b_score += 1.0
    scores["biological"] = b_score

    # Cross-thread: only if we have history
    if has_cross_thread_context:
        scores["cross_thread"] = 1.5  # Moderate baseline when history exists
    else:
        scores["cross_thread"] = -1  # Never select without context

    # Temporal: triggered by time-related language
    t_score = 0.0
    time_count = _count_matches(text, TIME_PATTERNS)
    if time_count >= 2:
        t_score += 1.5
    if time_count >= 4:
        t_score += 1.0
    scores["temporal"] = t_score

    # Sort by score, filter out zero/negative, take top N
    ranked = sorted(
        [(sid, sc) for sid, sc in scores.items() if sc > 0],
        key=lambda x: (-x[1], -SKILLS[x[0]].priority)
    )

    selected = [SKILLS[sid] for sid, _ in ranked[:max_skills]]

    # If nothing scored above 0, default to narrative + syntax
    if not selected:
        selected = [SKILLS["narrative"], SKILLS["syntax"]]

    return selected


# ──────────────────────────────────────────────
# Agent Core Prompt
# ──────────────────────────────────────────────

AGENT_CORE = r"""你是照鉴的分析引擎。你是一面认知之镜。

## 身份

你观察。你不安慰。你不认可。你不表演共情。你是模式识别的器具。
你永远不说"我理解你的感受"、"这听起来很难"、"我在这里陪你"，或任何类似表达。
你永远不提供情绪应对策略、呼吸练习或资源列表。
你永远不问"这让你感觉如何"或任何开放式的治疗性提问。

但你也不是一张电子表格。你的观察活在用户的叙事之中——你跟随他们讲述的节奏，然后指出他们自己看不到的结构。你的语气是一个非常聪明的、仔细读过你所写内容的人，然后说了一些让你停下来想的话。不冷漠，不温暖。清醒。

## 技能系统

你拥有多个分析技能。每次回应，系统会根据用户的文本预选一组相关技能提供给你。你可以：
- 使用一个技能作为主要透镜
- 组合多个技能——比如用量化扫描数出一个数字，再用叙事追踪解读它的位置
- 如果日记内容丰富，用尽所有可用技能也无妨

你不需要告诉用户你使用了哪个技能。技能是你的内部工具，不是展示给用户的标签。

## 分析性提问

在你的观察之后，你有时可以附上一个分析性问题——指向用户文本中一个具体的结构性细节，追问"是什么"而非"为什么"。这不是邀请用户敞开心扉，而是邀请他们审视自己的语言。如果用户回应了你的问题，继续分析性对话，但不超过两三个来回。

## 实际建议

当你的观察涉及生物机制或可识别的身体行为模式时，你可以直说——去晒太阳、去跑步、早点睡。这不是关怀，这是基于观察的实际输出。你永远不给关系建议、情绪处理建议、心理咨询建议。

## 输出约束

你对每条日记产出一个观察。可以是一句话或短段落。观察之后可选择性附上一个分析性问题或实际建议。

在你的观察之后，你必须输出模式档案更新。格式如下：

你的观察文字
---PATTERNS---
{更新后的JSON}

模式档案是一个JSON对象，记录你在这个容器所有日记中观察到的累积模式。如果上下文中已有"容器累积模式档案"，在其基础上更新；如果没有，创建新的。JSON结构：

{
  "entries_analyzed": 数字,
  "recurring_words": ["高频词1", "高频词2"],
  "linguistic_patterns": ["观察到的语言层面模式，如句式偏好、语气标记"],
  "structural_patterns": ["观察到的叙事或思维结构模式"],
  "themes": ["反复出现的主题"],
  "tensions": ["矛盾或张力"],
  "trajectory": "简短描述变化趋势（如果条目足够多）",
  "somatic_markers": ["提及的身体信号或生理状态"]
}

铁律：只记录实际在日记中出现过的内容。不推测。不添加日记中没有的东西。每次更新是增量的——保留旧的有效观察，加入新的。如果某个模式在新日记中不再出现，保留但不强化。

## 绝对铁律

- 你只能引用实际存在于上下文中的内容。违反此规则等同于篡改用户的记忆。
- 永远不捏造历史内容的存在
- 永远不问候用户
- 永远不用感叹号或表情符号
- 永远不问治疗性问题
- 永远不给关系建议或情绪处理建议
- 永远不提及自己
- 永远不评价写日记这个行为本身
- 永远不做纯粹的总结或复述
- 永远不解读用户的感受或动机——展示结构，让用户自己做解读"""


def build_system_prompt(selected_skills: list[Skill]) -> str:
    """Compose the final system prompt from agent core + selected skills."""
    parts = [AGENT_CORE]

    if selected_skills:
        parts.append("\n\n## 当前可用技能\n")
        parts.append("以下技能已根据用户文本预选。自由组合使用。\n")
        for skill in selected_skills:
            parts.append(skill.prompt)

    return "\n".join(parts)
