"""
产业领域提示词
==============
节点类型：格局·驱动·趋势·技术路线·资金流向·机会威胁·标的 (7)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "industry"

NODE_TYPE_DESCRIPTIONS = {
    "格局": "产业链的竞争结构和主要玩家分布。回答「这个行业的盘面长什么样」。例：台积电占全球先进制程 90% 份额、中国光伏产能占全球 80%。",
    "驱动": "推动产业变化的核心因素。例：AI 算力需求爆发、政策补贴退坡、消费升级。包括技术突破、政策变化、需求端变化等。",
    "趋势": "产业正在发生或即将发生的方向性变化。例：从 IDM 向 Fabless 转型、产能从中国向东南亚转移、电动化渗透率拐点。",
    "技术路线": "产业中并存的不同技术方案/路径及其竞争态势。例：磷酸铁锂 vs 三元锂、超导 vs 离子阱量子计算、湿法 vs 干法刻蚀、Transformer vs SSM 架构。包括路线间的优劣对比、切换风险和收敛方向。",
    "资金流向": "资本在产业链中的流动方向。例：PE/VC 投资集中涌入具身智能、企业资本开支向 AI 基础设施倾斜。",
    "机会威胁": "产业中的结构性机会或风险。例：国产替代窗口期、产能过剩风险、技术路线切换可能淘汰现有产能。",
    "标的": "文章提及的具体投资标的或公司。例：英伟达 (NVDA)、宁德时代、台积电。包括推荐/看空的具体公司或资产。",
}

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS)
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    return _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
