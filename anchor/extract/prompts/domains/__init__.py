"""
领域提示词注册表
================
每个领域一个模块，统一接口：
  - NODE_TYPE_DESCRIPTIONS: dict[str, str]
  - SYSTEM_CALL1: str
  - SYSTEM_CALL2: str
  - build_user_message_call1(content, platform, author, today)
  - build_user_message_call2(content, nodes_json)
"""

from anchor.extract.prompts.domains import (
    policy,
    industry,
    technology,
    futures,
    company,
    expert,
)

DOMAIN_PROMPTS = {
    "policy": policy,
    "industry": industry,
    "technology": technology,
    "futures": futures,
    "company": company,
    "expert": expert,
}

__all__ = ["DOMAIN_PROMPTS"]
