"""
AI 产业链 LayerSchema 种子数据
==============================
预定义 AI 产业链 8 层的关键指标，供 industry pipeline 匹配 is_schema_metric。

用法：
    python -m anchor.seeds.ai_layer_schema
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")

INDUSTRY_CHAIN = "AI"

# (tier_id, metric_name, unit, description)
AI_LAYER_METRICS: list[tuple[int, str, str | None, str]] = [
    # Tier 1: 基础材料
    (1, "晶圆产能", "万片/月", "12寸等效晶圆月产能"),
    (1, "HBM 产能", "GB/月", "高带宽存储月产能"),
    (1, "CoWoS 产能", "万片/月", "先进封装月产能"),
    # Tier 2: 算力芯片
    (2, "GPU 出货量", "万颗", "数据中心 GPU 季度出货"),
    (2, "算力总量", "EFLOPS", "全球 AI 算力总量"),
    (2, "芯片制程", "nm", "最先进量产制程节点"),
    (2, "市占率", "%", "细分市场份额"),
    # Tier 3: 系统集成
    (3, "服务器出货量", "万台", "AI 服务器季度出货"),
    (3, "机柜功率密度", "kW/柜", "单机柜平均功率"),
    (3, "PUE", "", "数据中心能效比"),
    # Tier 4: 云/算力平台
    (4, "GPU 云实例价格", "$/GPU·小时", "主流 GPU 云租赁价格"),
    (4, "GPU 利用率", "%", "数据中心 GPU 平均利用率"),
    (4, "资本开支", "亿美元", "云厂商季度 CapEx"),
    # Tier 5: 基础模型
    (5, "训练算力", "FLOP", "模型训练总算力消耗"),
    (5, "参数规模", "B", "模型参数量（十亿）"),
    (5, "MMLU 得分", "%", "多任务语言理解基准分数"),
    (5, "推理成本", "$/百万token", "API 推理单价"),
    # Tier 6: 开发工具/中间件
    (6, "开发者数量", "万人", "平台注册开发者数"),
    (6, "API 调用量", "亿次/天", "日均 API 调用量"),
    # Tier 7: 应用层
    (7, "MAU", "万", "月活跃用户数"),
    (7, "ARR", "亿美元", "年度经常性收入"),
    (7, "付费转化率", "%", "免费→付费用户转化率"),
    # Tier 8: 终端/硬件
    (8, "AI PC 渗透率", "%", "AI PC 占 PC 出货比例"),
    (8, "端侧算力", "TOPS", "设备端 AI 算力"),
    (8, "出货量", "万台", "AI 终端设备季度出货"),
]


async def seed() -> None:
    from anchor.database.session import AsyncSessionLocal, create_tables
    from anchor.models import LayerSchema
    from sqlmodel import select

    await create_tables()

    async with AsyncSessionLocal() as session:
        created = 0
        skipped = 0
        for tier_id, metric_name, unit, desc in AI_LAYER_METRICS:
            existing = (await session.exec(
                select(LayerSchema).where(
                    LayerSchema.industry_chain == INDUSTRY_CHAIN,
                    LayerSchema.tier_id == tier_id,
                    LayerSchema.metric_name == metric_name,
                )
            )).first()
            if existing:
                skipped += 1
                continue
            ls = LayerSchema(
                industry_chain=INDUSTRY_CHAIN,
                tier_id=tier_id,
                metric_name=metric_name,
                unit=unit,
                description=desc,
            )
            session.add(ls)
            created += 1

        await session.commit()
        print(f"[seed] AI LayerSchema: {created} created, {skipped} skipped (already exist)")


if __name__ == "__main__":
    asyncio.run(seed())
