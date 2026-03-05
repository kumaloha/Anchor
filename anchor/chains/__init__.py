"""
Anchor v4 — 三条独立链路
========================
Chain 1 — 逻辑提炼：URL → 六实体提取 + DAG 分析
Chain 2 — 作者分析：author_id → 档案 + 立场分析
Chain 3 — 验证：raw_post_id → 事实/假设/隐含条件/结论/预测验证
"""

from anchor.chains.chain1_extractor import run_chain1
from anchor.chains.chain2_author import run_chain2
from anchor.chains.chain3_verifier import run_chain3

__all__ = ["run_chain1", "run_chain2", "run_chain3"]
