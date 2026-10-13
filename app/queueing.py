from app.config import settings

async def enqueue(redis, document_id: str, content_hash: str, stage: str = "processing"):
    # A job carries the exact content hash it was created for. Workers discard stale jobs.
    await redis.rpush(settings.queue_name, f"{document_id}|{content_hash}|{stage}")
