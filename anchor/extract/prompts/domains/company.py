"""
公司领域提示词
==============
节点类型：表现·归因·指引·风险·叙事 (5)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "company"

NODE_TYPE_DESCRIPTIONS = {
    "表现": "公司的业绩和经营数据。例：营收同比增长 15%、净利润 50 亿美元、毛利率 42%、数据中心业务收入翻倍。包括财务指标和运营数据。",
    "归因": "业绩表现的原因分析。例：AI 需求爆发推动数据中心收入、汇率波动影响海外业务、成本优化提升利润率。",
    "指引": "管理层的前瞻性指导和预期。例：下季度营收预计 300-320 亿、2026 年资本开支计划增长 30%、预计年底前完成收购。",
    "风险": "公司面临的风险和挑战。例：客户集中度过高、库存积压、竞争加剧、监管风险、供应链依赖。",
    "叙事": "围绕公司的核心投资叙事和逻辑。例：AI 基础设施不可替代的卖铲人、从硬件到平台的转型故事、被低估的隐形冠军。",
}

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS)
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    return _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
