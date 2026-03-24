from typing import cast

from redis.asyncio import Redis

from app.core.config import Settings


def build_redis_client(settings: Settings) -> Redis | None:
    if not settings.redis_url:
        return None

    return cast(
        Redis,
        Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True),
    )


async def dispose_redis_client(redis_client: Redis | None) -> None:
    if redis_client is None:
        return

    await redis_client.aclose()
