from abc import ABC, abstractmethod
from typing import List

from app.models.blogger import Blogger
from app.models.raw_content import RawContent


class BaseCollector(ABC):
    """Abstract base class for all content collectors."""

    @abstractmethod
    async def fetch(self, blogger: Blogger) -> List[RawContent]:
        """
        Fetch new content from the platform for a given blogger.

        Args:
            blogger: The Blogger ORM object with platform-specific URL/id info.

        Returns:
            A list of unsaved RawContent objects ready to be persisted.
        """
        ...

    @abstractmethod
    def supports_platform(self, platform: str) -> bool:
        """Return True if this collector handles the given platform string."""
        ...
