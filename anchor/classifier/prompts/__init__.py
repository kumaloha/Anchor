from anchor.classifier.prompts.base import BasePrompt
from anchor.classifier.prompts.v1_identify import PromptV1Identify
from anchor.classifier.prompts.v2_cot import PromptV2CoT
from anchor.classifier.prompts.v3_adversarial import PromptV3Adversarial
from anchor.classifier.prompts.v2_identify import PromptV2Identify
from anchor.classifier.prompts.v3_unified import PromptV3Unified

# 可用版本注册表（key = version string）
PROMPT_REGISTRY: dict[str, BasePrompt] = {
    "v1_identify": PromptV1Identify(),
    "v2_cot": PromptV2CoT(),
    "v3_adversarial": PromptV3Adversarial(),
    "v2_identify": PromptV2Identify(),
    "v3_unified": PromptV3Unified(),
}

DEFAULT_PROMPT_VERSION = "v3_unified"

__all__ = [
    "BasePrompt",
    "PromptV1Identify",
    "PromptV2CoT",
    "PromptV3Adversarial",
    "PromptV2Identify",
    "PromptV3Unified",
    "PROMPT_REGISTRY",
    "DEFAULT_PROMPT_VERSION",
]
