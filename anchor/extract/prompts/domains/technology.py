"""
技术领域提示词
==============
节点类型：问题·方案·效果性能·局限场景·玩家 (5)
"""

from anchor.extract.prompts.domains._base import (
    build_system_call1,
    build_system_call2,
    build_user_message_call1 as _build_call1,
    build_user_message_call2 as _build_call2,
)

DOMAIN = "technology"

NODE_TYPE_DESCRIPTIONS = {
    "问题": "论文/文章要解决的核心问题或挑战。例：大语言模型的幻觉问题、推理延迟过高、训练数据不足。",
    "方案": "提出的技术方法或解决路径。例：Mixture of Experts 架构、RLHF 对齐训练、知识蒸馏、新型注意力机制。",
    "效果性能": "方案的效果和性能指标。例：在 MMLU 上达到 90.2%、推理速度提升 3 倍、FLOPs 降低 40%。",
    "局限场景": "方案的局限性、适用条件或失败场景。例：仅适用于英语、需要大量标注数据、在长上下文场景下性能下降。",
    "玩家": "技术领域的关键参与者和机构。例：OpenAI、DeepSeek、Google DeepMind。包括研究团队、公司、开源社区。",
}

SYSTEM_CALL1 = build_system_call1(DOMAIN, NODE_TYPE_DESCRIPTIONS)
SYSTEM_CALL2 = build_system_call2(DOMAIN)

_NODE_TYPE_NAMES = list(NODE_TYPE_DESCRIPTIONS.keys())


def build_user_message_call1(content: str, platform: str, author: str, today: str) -> str:
    return _build_call1(content, platform, author, today, DOMAIN, _NODE_TYPE_NAMES)


def build_user_message_call2(content: str, nodes_json: str) -> str:
    return _build_call2(content, nodes_json)
