from anchor.extract.prompts.base import BasePrompt
from anchor.extract.prompts.v1_identify import PromptV1Identify
from anchor.extract.prompts.v2_cot import PromptV2CoT
from anchor.extract.prompts.v3_adversarial import PromptV3Adversarial
from anchor.extract.prompts.v2_identify import PromptV2Identify
from anchor.extract.prompts.v3_unified import PromptV3Unified
from anchor.extract.prompts.v4_sixentity import PromptV4SixEntity
from anchor.extract.prompts import v5_step1_claims
from anchor.extract.prompts import v5_step2_merge
from anchor.extract.prompts import v5_step3_classify
from anchor.extract.prompts import v5_step4_implicit
from anchor.extract.prompts import v6_step1_anchors
from anchor.extract.prompts import v6_step2_supporting
from anchor.extract.prompts import v6_step3_abstract
from anchor.extract.prompts import v6_step4_merge
from anchor.extract.prompts import v6_step5_relationships

PROMPT_REGISTRY: dict[str, BasePrompt] = {
    "v1_identify": PromptV1Identify(),
    "v2_cot": PromptV2CoT(),
    "v3_adversarial": PromptV3Adversarial(),
    "v2_identify": PromptV2Identify(),
    "v3_unified": PromptV3Unified(),
    "v4_sixentity": PromptV4SixEntity(),
}

# v5/v6 多步流水线使用独立步骤提示词模块，不注册到 PROMPT_REGISTRY
# 直接通过 extractor.py 内部 import 调用
DEFAULT_PROMPT_VERSION = "v6"

__all__ = [
    "BasePrompt",
    "PromptV1Identify",
    "PromptV2CoT",
    "PromptV3Adversarial",
    "PromptV2Identify",
    "PromptV3Unified",
    "PromptV4SixEntity",
    "v5_step1_claims",
    "v5_step2_merge",
    "v5_step3_classify",
    "v5_step4_implicit",
    "v6_step1_anchors",
    "v6_step2_supporting",
    "v6_step3_abstract",
    "v6_step4_merge",
    "v6_step5_relationships",
    "PROMPT_REGISTRY",
    "DEFAULT_PROMPT_VERSION",
]
