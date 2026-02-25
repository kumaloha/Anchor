from abc import ABC, abstractmethod
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.opinion import Opinion


class BaseTracker(ABC):
    """Abstract base for all opinion trackers."""

    @abstractmethod
    async def track(self, opinion: Opinion, session: AsyncSession) -> None:
        """
        Perform tracking/verification for the given opinion.
        Should update the relevant detail record and create VerificationRecord(s).
        """
        ...

    @abstractmethod
    def supports_type(self, opinion_type: str) -> bool:
        """Return True if this tracker handles the given opinion type string."""
        ...
