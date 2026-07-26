"""Движок и фабрика сессий."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

POOL_SIZE = 5
MAX_OVERFLOW = 5

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
