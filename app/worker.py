import asyncio
import json
import random
import logging
from app.db import connect, close
from app.config import settings
from app.logic import mock_summary, mock_tags, now
from app.queueing import enqueue

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cohort-worker")

async def process_one(db, redis, document_id: str, digest: str, stage: str):
    from bson import ObjectId
    from app.main import release_active
    if not ObjectId.is_valid(document_id):
        return
    oid = ObjectId(document_id)
    doc = await db.documents.find_one({"_id": oid, "current_content_hash": digest})
    if not doc:
        log.info("Discarding stale job %s", document_id)
        return
    if stage == "processing":
        claimed = await db.documents.update_one(
            {"_id": oid, "current_content_hash": digest, "status": {"$in": ["queued", "failed"]}},
            {"$set": {"status": "processing", "failed_stage": None, "error": None, "updated_at": now()}}
        )
        if not claimed.modified_count:
            return
        await asyncio.sleep(random.uniform(10, 20))
        if random.random() < 0.10:
            await db.documents.update_one(
                {"_id": oid, "current_content_hash": digest, "status": "processing"},
                {"$set": {"status": "failed", "failed_stage": "processing",
                          "error": "Simulated processing failure", "updated_at": now()}}
            )
            await release_active(redis, doc["user_id"], document_id)
            return
        summary = mock_summary(doc["content"])
        # Guard the write with the content hash, so a PATCH invalidates in-flight work.
        result = await db.documents.update_one(
            {"_id": oid, "current_content_hash": digest, "status": "processing"},
            {"$set": {"summary": summary, "summary_content_hash": digest,
                      "status": "enriching", "updated_at": now()}}
        )
        if result.modified_count:
            await enqueue(redis, document_id, digest, "enriching")
    elif stage == "enriching":
        if doc.get("summary_content_hash") != digest or not doc.get("summary"):
            return
        claimed = await db.documents.update_one(
            {"_id": oid, "current_content_hash": digest, "status": "enriching"},
            {"$set": {"updated_at": now()}}
        )
        if not claimed.matched_count:
            return
        await asyncio.sleep(random.uniform(5, 15))
        if random.random() < 0.10:
            await db.documents.update_one(
                {"_id": oid, "current_content_hash": digest, "status": "enriching"},
                {"$set": {"status": "failed", "failed_stage": "enriching",
                          "error": "Simulated enrichment failure", "updated_at": now()}}
            )
            await release_active(redis, doc["user_id"], document_id)
            return
        tags = mock_tags(doc["summary"])
        result = await db.documents.update_one(
            {"_id": oid, "current_content_hash": digest, "status": "enriching",
             "summary_content_hash": digest},
            {"$set": {"tags": tags, "tags_content_hash": digest, "status": "completed",
                      "failed_stage": None, "error": None, "updated_at": now()}}
        )
        if result.modified_count:
            await redis.set(f"content-cache:{digest}", json.dumps({"summary": doc["summary"], "tags": tags}),
                            ex=settings.cache_ttl_seconds)
            await release_active(redis, doc["user_id"], document_id)

async def main():
    db, redis = await connect()
    log.info("Worker started")
    try:
        while True:
            item = await redis.blpop(settings.queue_name, timeout=5)
            if not item:
                continue
            _, raw = item
            try:
                document_id, digest, stage = raw.split("|", 2)
                await process_one(db, redis, document_id, digest, stage)
            except Exception:
                log.exception("Job failed unexpectedly")
    finally:
        await close()

if __name__ == "__main__":
    asyncio.run(main())
