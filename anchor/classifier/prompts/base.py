"""
Prompt 基类
===========
所有 Prompt 版本都继承此类。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BasePrompt(ABC):
    """观点提取 Prompt 基类

    子类实现 system_prompt 和 build_user_message 两个方法。
    Extractor 通过 prompt.version 记录使用的版本。
    """

    @property
    @abstractmethod
    def version(self) -> str:
        """Prompt 版本标识，如 'v1_identify'"""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Claude 的 system 消息"""
        ...

    @abstractmethod
    def build_user_message(self, content: str, platform: str, author: str) -> str:
        """构建 user 消息

        Args:
            content:  Step 2 补全后的完整文本（enriched_content）
            platform: 来源平台，如 "twitter"、"weibo"
            author:   作者名称
        """
        ...
