"""SEC 10-K 分段器 — 按 Item/Note 切分，为每个提取主题只提供相关段落

用法:
    from sec_10k_splitter import split_10k, get_sections_for_topic

    sections = split_10k(text)
    # sections = {"item_1": "...", "item_1a": "...", "item_7": "...", "note_1": "...", ...}

    relevant_text = get_sections_for_topic("supply_chain", sections)
    # 只返回供应链相关的段落拼接
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Item 标题的正则：匹配 "Item 1." / "ITEM 1A." / "Item 7." 等（带标题文字的才是正文开始）
ITEM_RE = re.compile(
    r"^(?:ITEM|Item)\s+(1[ABC]?|[2-9][ABC]?|1[0-6])\.\s+\S",
    re.MULTILINE,
)

# Note 标题的正则：匹配 "Note 1 - xxx" / "NOTE 1 - xxx"
NOTE_RE = re.compile(
    r"^(?:NOTE|Note)\s+(\d+)\s*[-–—]\s*\S",
    re.MULTILINE,
)


@dataclass
class Section:
    key: str          # "item_1", "item_1a", "note_11" 等
    title: str        # 原始标题行
    start: int        # 字符起始位置
    end: int          # 字符结束位置
    line_start: int   # 行号起始


def split_10k(text: str) -> dict[str, str]:
    """将 10-K 全文按 Item/Note 切分为字典"""
    markers: list[tuple[int, str, str]] = []  # (char_pos, key, title_line)

    for m in ITEM_RE.finditer(text):
        item_num = m.group(1).lower()
        key = f"item_{item_num}"
        # 取整行作为标题
        line_end = text.find("\n", m.start())
        title = text[m.start():line_end] if line_end != -1 else text[m.start():]
        markers.append((m.start(), key, title.strip()))

    for m in NOTE_RE.finditer(text):
        note_num = m.group(1)
        key = f"note_{note_num}"
        line_end = text.find("\n", m.start())
        title = text[m.start():line_end] if line_end != -1 else text[m.start():]
        markers.append((m.start(), key, title.strip()))

    # 按位置排序
    markers.sort(key=lambda x: x[0])

    # 去重：同一个 key 可能出现两次（目录 + 正文），只保留后一个（正文）
    seen: dict[str, int] = {}
    for i, (pos, key, title) in enumerate(markers):
        seen[key] = i
    markers = [markers[i] for i in sorted(seen.values())]

    # 切分
    sections: dict[str, str] = {}
    for i, (pos, key, title) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
        content = text[pos:end].strip()
        sections[key] = content

    return sections


# ============================================================
# 主题 → 所需段落的映射
# ============================================================
TOPIC_SECTIONS = {
    "operations": [
        "item_1",       # Business（含 CEO letter 概述）
        "item_7",       # MD&A
    ],
    "supply_chain": [
        "item_1",       # Business（供应商/客户描述）
        "item_1a",      # Risk Factors（供应链风险）
        "item_7",       # MD&A（客户集中度讨论）
        "note_12",      # Commitments and Contingencies（采购义务）
        "note_13",      # 有些年份 commitments 在这里
    ],
    "revenue_model": [
        "item_1",       # Business（产品/服务描述）
        "item_7",       # MD&A（收入分析、ASP讨论）
        "note_1",       # Revenue Recognition 政策
        "note_9",       # Balance Sheet Components（递延收入，FY2025）
        "note_10",      # Balance Sheet Components（递延收入，FY2021-FY2024）
    ],
    "financials": [
        "note_1",       # Accounting Policies（含收入确认）
        "note_3",       # SBC（部分年份）
        "note_4",       # SBC（部分年份）
        "note_11",      # Debt
        "note_12",      # Commitments
        "note_13",      # Income Taxes（部分年份）
        "note_14",      # Income Taxes / Shareholders' Equity
        "note_15",      # Shareholders' Equity（部分年份）
        "item_5",       # Market for Common Equity（回购等）
    ],
    "risk_kpi": [
        "item_1",       # Business（员工数等 KPI）
        "item_1a",      # Risk Factors
        "item_3",       # Legal Proceedings
        "note_12",      # Commitments and Contingencies（诉讼）
        "note_13",      # 部分年份诉讼在这里
    ],
    "geographic": [
        "note_16",      # Segment Information（含地理收入）
        "note_17",      # Segment Information（部分年份）
        "item_7",       # MD&A（地域讨论）
    ],
}


def get_sections_for_topic(topic: str, sections: dict[str, str]) -> str:
    """拼接指定主题所需的段落，返回合并文本"""
    keys = TOPIC_SECTIONS.get(topic, [])
    parts = []
    for key in keys:
        if key in sections:
            parts.append(sections[key])
    return "\n\n".join(parts)


def report_savings(text: str, sections: dict[str, str]):
    """打印分段统计和各主题的 token 节省比例"""
    total = len(text)
    print(f"全文: {total:,} chars")
    print(f"分段数: {len(sections)}")
    print()

    for key in sorted(sections, key=lambda k: (k.split("_")[0], int(re.sub(r"[a-z]", "", k.split("_")[1]) or "0"))):
        size = len(sections[key])
        pct = size / total * 100
        # 取第一行作为标题
        title = sections[key].split("\n")[0][:80]
        print(f"  {key:12s} {size:>8,} chars ({pct:4.1f}%)  {title}")

    print()
    for topic, keys in TOPIC_SECTIONS.items():
        topic_text = get_sections_for_topic(topic, sections)
        topic_size = len(topic_text)
        saving = (1 - topic_size / total) * 100
        print(f"  {topic:15s} {topic_size:>8,} chars ({topic_size/total*100:4.1f}% of full) — 节省 {saving:.0f}%")


if __name__ == "__main__":
    from pathlib import Path

    for fy in ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]:
        text = Path(f"/tmp/nvidia_10k_{fy}.txt").read_text()
        print(f"\n{'='*60}")
        print(f" {fy.upper()}")
        print(f"{'='*60}")
        sections = split_10k(text)
        report_savings(text, sections)
