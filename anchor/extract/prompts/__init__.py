from anchor.extract.prompts.base import BasePrompt
from anchor.extract.prompts.v1_identify import PromptV1Identify
from anchor.extract.prompts.v2_cot import PromptV2CoT
from anchor.extract.prompts.v3_adversarial import PromptV3Adversarial
from anchor.extract.prompts.v2_identify import PromptV2Identify
from anchor.extract.prompts.v3_unified import PromptV3Unified
from anchor.extract.prompts.v4_sixentity import PromptV4SixEntity

PROMPT_REGISTRY: dict[str, BasePrompt] = {
    "v1_identify": PromptV1Identify(),
    "v2_cot": PromptV2CoT(),
    "v3_adversarial": PromptV3Adversarial(),
    "v2_identify": PromptV2Identify(),
    "v3_unified": PromptV3Unified(),
    "v4_sixentity": PromptV4SixEntity(),
}

DEFAULT_PROMPT_VERSION = "v4_sixentity"

__all__ = [
    "BasePrompt",
    "PromptV1Identify",
    "PromptV2CoT",
    "PromptV3Adversarial",
    "PromptV2Identify",
    "PromptV3Unified",
    "PromptV4SixEntity",
    "PROMPT_REGISTRY",
    "DEFAULT_PROMPT_VERSION",
]
