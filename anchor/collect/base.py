from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawPostData:
    """采集器返回的原始帖子数据（未入库）"""

    source: str                          # 平台名称，对应 CollectorSource 枚举值
    external_id: str                     # 平台原始帖子 ID
    content: str                         # 正文
    author_name: str
    author_id: str | None
    url: str
    posted_at: datetime
    metadata: dict = field(default_factory=dict)   # 点赞、转发、评论数等
    # 媒体列表：[{"type": "photo"|"video"|"gif", "url": "https://..."}]
    media_items: list[dict] = field(default_factory=list)


class BaseCollector(ABC):
    """所有采集器的抽象基类。

    子类只需实现 `collect` 异步生成器，yield RawPostData 对象。
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """对应 CollectorSource 的字符串值"""
        ...

    @abstractmethod
    async def collect(self, **kwargs) -> list[RawPostData]:
        """执行一次采集，返回本次抓取到的帖子列表。

        kwargs 由子类自定义，例如 keywords、user_ids 等。
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source={self.source_name}>"