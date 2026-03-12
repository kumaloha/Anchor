"""
Anchor — 三步流水线
====================
1. 通用判断 (General Assessment)  — 作者背景 + 2D分类 + 摘要 + 利益冲突
2. 内容提取 (Content Extraction)  — 路由 → 实体提取 + DAG 分析
3. 事实验证 (Fact Verification)   — 事实/假设/结论/预测验证
"""

from anchor.chains.general_assessment import run_assessment, assess_post
from anchor.chains.content_extraction import run_extraction
from anchor.chains.fact_verification import run_verification

__all__ = [
    "run_assessment", "assess_post",
    "run_extraction",
    "run_verification",
]
