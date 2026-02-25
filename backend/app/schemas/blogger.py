from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.models.blogger import PlatformEnum


class BloggerCreate(BaseModel):
    platform: PlatformEnum
    url: Optional[str] = None
    name: str
    description: Optional[str] = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()


class BloggerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    url: Optional[str] = None


class BloggerOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    platform: PlatformEnum
    url: Optional[str] = None
    name: str
    description: Optional[str] = None
    is_active: bool
    last_crawled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
