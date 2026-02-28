from anchor.classifier.prompts.base import BasePrompt
from anchor.classifier.prompts.v1_identify import PromptV1Identify
from anchor.classifier.prompts.v2_cot import PromptV2CoT
from anchor.classifier.prompts.v3_adversarial import PromptV3Adversarial

# 可用版本注册表（key = version string）
PROMPT_REGISTRY: dict[str, BasePrompt] = {
    "v1_identify": PromptV1Identify(),
    "v2_cot": PromptV2CoT(),
    "v3_adversarial": PromptV3Adversarial(),
}

DEFAULT_PROMPT_VERSION = "v1_identify"

__all__ = [
    "BasePrompt",
    "PromptV1Identify",
    "PromptV2CoT",
    "PromptV3Adversarial",
    "PROMPT_REGISTRY",
    "DEFAULT_PROMPT_VERSION",
]
