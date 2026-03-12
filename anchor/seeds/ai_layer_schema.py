"""
AI 产业链 LayerSchema 种子数据 — thin wrapper
==============================================
实际实现已移至 axion.seeds.ai_layer_schema。

用法：
    python -m anchor.seeds.ai_layer_schema
    python -m axion.seeds.ai_layer_schema   (推荐)
"""

from axion.seeds.ai_layer_schema import seed, INDUSTRY_CHAIN, AI_LAYER_METRICS  # noqa: F401

if __name__ == "__main__":
    import asyncio
    asyncio.run(seed())
