import logging
from typing import cast

from redis.asyncio import Redis

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_redis_client(settings: Settings) -> Redis | None:
    if not settings.redis_url:
        logger.debug("Redis client is disabled because redis_url is empty")
        return None

    logger.info("Creating Redis client from configured URL")
    return cast(
        Redis,
        Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True),
    )


async def dispose_redis_client(redis_client: Redis | None) -> None:
    if redis_client is None:
        logger.debug("Redis client disposal skipped because client is not configured")
        return

    logger.debug("Closing Redis client")
    await redis_client.aclose()
