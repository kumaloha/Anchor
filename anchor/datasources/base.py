"""
anchor/datasources/base.py
==========================
所有数据源适配器共享的基础数据结构。
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class DataResult:
    """数据源查询结果，供 Phase B LLM 使用。"""
    content: str                    # 格式化文本，直接传给 LLM 分析
    data_period: str | None         # 数据实际覆盖的时间段描述
    source_url: str | None          # 数据来源 URL（用于存档）
    source_type: str = "unknown"    # 来源标识（fred/bls/world_bank/imf/…）
    ok: bool = True                 # False 表示查询失败（content 含错误信息）
    extra: dict = field(default_factory=dict)  # 原始结构化数据（可选）
