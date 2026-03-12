"""
专家分析领域提示词
==================
节点类型：事实·判断·预测·建议 (4)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "expert"

NODE_TYPE_DESCRIPTIONS = {
    "事实": (
        "可独立核查的客观陈述——有明确的时间、数据、事件或来源。"
        "包括：① 直接引用的数据/事件（美国 2 月非农 -9.2 万）；"
        "② 市场已发生的价格变动（布伦特原油上周涨 36%）；"
        "③ 作者用来支撑判断的关键论据（韩国 LNG 储备不足两周、AI 投资占美国总投资六成）。"
        "注意：「市场正在为 X 定价」「资金正在流向 Y」是已观察到的市场行为，属于事实而非预测。"
        "关键要求：不要遗漏作者论证链中的重要支撑事实，即使它只出现一句话。"
    ),
    "判断": (
        "作者对已发生事件或当前形势的分析性结论——回顾型，基于事实的推理。"
        "包括归因推理（「A 导致 B」）、形势判断（「经济正在放缓」）、因果分析。"
        "即使 A 和 B 都是事实，「A 导致 B」本身是判断；A、B 应各自作为事实节点支撑它。"
    ),
    "预测": (
        "作者对未来走势的预判——面向未来、可追踪验证。"
        "必须含有时态标志：将、未来、会、预计、可能发展成、有望等。"
        "注意区分：「油价上周涨 36%」是事实；「油价将突破 100 美元」是预测。"
        "「市场正在为油价破百定价」是事实（描述当前市场行为）；「油价将破百」才是预测。"
    ),
    "建议": (
        "作者建议的具体行动方案——买/卖/持有/增配/减仓/关注等。"
        "例：增持黄金对冲风险、减少美股敞口。"
        "如果文章没有给出任何行动建议，不要硬凑。"
    ),
}

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS)
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    return _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
