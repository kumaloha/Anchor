"""
Anchor test fixtures.
"""
from __future__ import annotations

import os

import pytest

# 测试使用内存数据库
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")


@pytest.fixture(autouse=True)
async def _init_db():
    """每个测试前创建所有表。"""
    from anchor.database.session import create_tables

    await create_tables()
