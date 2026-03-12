"""
期货/宏观领域提示词
====================
节点类型：供给·需求·库存·头寸·冲击·缺口 (6)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "futures"

NODE_TYPE_DESCRIPTIONS = {
    "供给": "商品/资产的生产、产出或供应端信息。例：OPEC+ 减产 200 万桶/日、美国页岩油产量创新高、铜矿罢工导致供应中断。",
    "需求": "商品/资产的消费、需求端信息。例：中国基建投资拉动铜需求、全球航空复苏推升燃油需求、新能源汽车渗透率提升拉动锂需求。",
    "库存": "商品/资产的库存状态和变化。例：LME 铜库存降至 15 年低点、美国商业原油库存超预期增加、黄金 ETF 持仓持续流出。",
    "头寸": "市场参与者的持仓和资金流向。例：CFTC 投机性净多头达历史高位、对冲基金大幅减持美债、散户资金流入黄金 ETF。",
    "冲击": "突发事件或外部冲击对市场的影响。例：地缘冲突推升油价、美联储意外加息、关税战升级冲击供应链。",
    "缺口": "供需失衡或定价偏差。例：铜市场缺口预计达 50 万吨、期货升水扩大暗示现货紧缺、利差倒挂信号衰退。",
}

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS)
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    return _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
