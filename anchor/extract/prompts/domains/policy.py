from __future__ import annotations

"""
政策领域提示词（含地缘）
========================
节点类型：主旨·目标·战略·战术·资源·考核·约束·反馈·外溢 (9)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "policy"

NODE_TYPE_DESCRIPTIONS = {
    "主旨": (
        "政策的核心议题/工作板块。一份综合性政策文件通常包含多个主旨。"
        "例：扩大内需、科技创新、绿色发展、民生保障、对外开放。"
        "每个主旨是一棵独立的子树，下挂自己的目标、战略、战术、资源、约束、考核。"
    ),
    "目标": (
        "该主旨下可量化或可观测的具体目标。必须能回溯到某个主旨。"
        "每个独立的量化指标应单独成为一个目标节点，不要把多个指标合并为一个笼统的'多项目标'。"
        "例：GDP增速5% 是一个目标节点，城镇新增就业1200万 是另一个目标节点。"
        "claim 中必须保留原始数字和单位。"
    ),
    "战略": "该主旨下实现目标的顶层路径设计。例：双循环发展格局、科技自立自强。每个主旨通常有1个战略。",
    "战术": (
        "该战略下具体的执行手段和措施。每个战略通常对应多条战术。"
        "例：减税降费、专项债扩容、以旧换新、育儿补贴。"
        "战术要足够具体，能回答「怎么干」。"
    ),
    "资源": "为该主旨/战术配套的资金、人力、组织资源。例：赤字率4%、特别国债1.3万亿、设立跨部委协调小组。",
    "考核": "该主旨的执行追踪与评估机制。例：纳入政绩考核、政策一致性评估、季度通报。",
    "约束": "该主旨实施的红线与限制条件。例：严禁变相举债、环保一票否决、碳排降幅指标。",
    "反馈": "该主旨相关的已有执行效果或调整信号。例：保交房完成、设备投资+11.8%。",
    "外溢": "该主旨对其他领域/国家/市场的连带影响。例：中美磋商成果、免签扩大、一带一路。",
}

# 政策领域额外提取指导（追加到 system prompt）
_POLICY_EXTRA_GUIDANCE = """

【政策领域特殊规则 — 多主旨树状结构】
政策文件通常包含多个并列的工作板块（主旨），每个主旨下应分别提取：
  主旨 → 目标 + 战略 → 多条战术 + 资源 + 约束 + 考核

示例（政府工作报告）：
  主旨A「扩大内需」
    → 目标: GDP增长5%
    → 战略: 做强国内大循环
    → 战术: 以旧换新、设备更新、楼市托底、稳股市
    → 资源: 赤字率4%、专项债4.4万亿
    → 约束: 严禁变相举债
    → 考核: 政策一致性评估

  主旨B「科技创新」
    → 目标: 数字经济核心产业占比10.5%+
    → 战略: 新质生产力
    → 战术: AI+行动、583项国标、数据要素释放
    → 资源: 研发经费年均增7%+
    → 考核: ...

  主旨C「民生保障」
    → 战术: 学前免费教育、育儿补贴、提高养老金
    → ...

不要把所有战略合并成一个节点、所有战术合并成一个节点。
每个节点的 summary 应带上所属主旨的标识，如「[内需]以旧换新」「[科创]AI+行动」。

注意：
· 综合性政策文件的主旨不仅限于经济领域。国防军事、外交、台湾/统一、国家安全、法治、
  反腐败等如果文件中有独立章节/段落论述，必须各自成为独立主旨树。
· 提取前先通读全文，列出所有章节标题，确保每个章节至少对应一棵主旨树。
· 特别注意：报告末尾常有以"我们要"开头的独立段落，涵盖国防军队、港澳、台湾统一、
  外交等板块，虽然没有编号但同样是独立主旨，必须提取。
· 一份政府工作报告通常包含8-12个主旨，对应35-50个节点。如果你的主旨少于7个，说明遗漏了重要板块。
"""

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS) + _POLICY_EXTRA_GUIDANCE
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def _extract_sections(content: str) -> list[str]:
    """从政策文件中提取章节标题，帮助 LLM 覆盖全文。"""
    import re
    patterns = [
        re.compile(r'^[（(]\s*[一二三四五六七八九十]+\s*[）)]\s*(.+)', re.MULTILINE),
        re.compile(r'^\s*[一二三四五六七八九十]+\s*[、，]\s*(.+)', re.MULTILINE),
        re.compile(r'^\s*第[一二三四五六七八九十]+[章节部分]\s*(.+)', re.MULTILINE),
        # 英文：Department of ..., National ..., Corps of ...
        re.compile(r'^\*?\*?(Department of [A-Z][\w\s]+?)(?:\s*\(|\*?\*?\s*$)', re.MULTILINE),
        re.compile(r'^\*?\*?((?:National |Small |Environmental |Corps of )[A-Z][\w\s]+?)(?:\s*\(|\*?\*?\s*$)', re.MULTILINE),
        re.compile(r'^\*?\*?(Small Agency Eliminations)\*?\*?', re.MULTILINE),
    ]
    sections = []
    for p in patterns:
        for m in p.finditer(content):
            title = m.group(1).split('.')[0].split('。')[0].strip()[:40]
            if title and title not in sections:
                sections.append(title)
    return sections


# ── 章节边界识别 + 按主旨切割 ──────────────────────────────────────────

import re as _re

_SECTION_PATTERNS = [
    _re.compile(r'^[（(]\s*[一二三四五六七八九十]+\s*[）)]', _re.MULTILINE),
    _re.compile(r'^\s*[一二三四五六七八九十]+\s*[、，]', _re.MULTILINE),
    _re.compile(r'^\s*第[一二三四五六七八九十]+[章节部分]', _re.MULTILINE),
    # 英文：Department of ..., Agency ..., Corps of ...
    _re.compile(r'^\*?\*?Department of \w+', _re.MULTILINE),
    _re.compile(r'^\*?\*?(?:National |Small |Environmental |Corps of )', _re.MULTILINE),
]

# 大章节标题（一、二、三...）用于分段 — 同时匹配英文部门/机构标题
_MAJOR_SECTION = _re.compile(
    r'^\s*[一二三四五六七八九十]{1,3}\s*[、，]'
    r'|^\*?\*?Department of \w+'
    r'|^\*?\*?(?:National Aeronautics|National Science|Small Business|Environmental Protection|Corps of Engineers)'
    r'|^#{1,3}\s+(?:Department of|National |Small |Environmental |Corps of )',
    _re.MULTILINE
)

_CHUNK_TARGET = 8000  # 每段目标字数

# 小节标题 (一)(二)... 或英文 Increases / Cuts, Reductions
_SUB_SECTION = _re.compile(
    r'^[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]'
    r'|^\*?_?\*?(?:Increases|Cuts, Reductions|Other )\*?_?'
    r'|^(?:Major Discretionary Funding|Table \d+\.)',
    _re.MULTILINE
)


def _find_all_boundaries(content: str) -> list[tuple[int, int]]:
    """找到所有可用的切割边界。返回 (position, level) 列表。
    level=1: 大章节（一、二、...）
    level=2: 小节（（一）（二）...）
    """
    boundaries = []
    for m in _MAJOR_SECTION.finditer(content):
        boundaries.append((m.start(), 1))
    for m in _SUB_SECTION.finditer(content):
        boundaries.append((m.start(), 2))
    boundaries.sort(key=lambda x: x[0])
    # 去重（同位置取更高级别）
    deduped = []
    seen = set()
    for pos, level in boundaries:
        if pos not in seen:
            deduped.append((pos, level))
            seen.add(pos)
    return deduped


def chunk_content(content: str) -> list[str] | None:
    """按章节边界切割长文档。

    返回 None 表示不需要切割（内容够短）。
    优先在大章节边界切割；大章节过长时在小节边界切割。
    """
    if len(content) <= _CHUNK_TARGET:
        return None

    boundaries = _find_all_boundaries(content)
    if len(boundaries) < 2:
        return None

    # 构建段落列表: [(start, end), ...]
    segments = []
    for i, (start, _level) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(content)
        segments.append((start, end))

    # 前言部分
    preamble = content[:boundaries[0][0]].strip()

    # 贪心合并
    chunks = []
    current = (preamble + "\n\n") if preamble else ""

    for start, end in segments:
        segment_text = content[start:end]
        if len(current) + len(segment_text) > _CHUNK_TARGET and current.strip():
            chunks.append(current.strip())
            current = ""
        current += segment_text

    if current.strip():
        chunks.append(current.strip())

    if len(chunks) <= 1:
        return None

    return chunks


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    base = _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)
    sections = _extract_sections(content)
    if sections:
        section_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))
        base += f"\n\n## 检测到的章节（每个章节必须至少对应一棵主旨树）\n{section_list}"
    return base


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
