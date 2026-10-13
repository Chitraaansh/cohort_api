from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis
from app.config import settings

mongo_client: AsyncIOMotorClient | None = None
redis_client: Redis | None = None

async def connect():
    global mongo_client, redis_client
    mongo_client = AsyncIOMotorClient(settings.mongodb_uri)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await mongo_client.admin.command("ping")
    await redis_client.ping()
    db = mongo_client[settings.mongodb_database]
    await db.documents.create_index([("user_id", 1), ("created_at", -1)])
    await db.documents.create_index([("user_id", 1), ("status", 1), ("created_at", -1)])
    await db.documents.create_index("client_doc_ref", unique=True, sparse=True)
    await db.documents.create_index("content_hash")
    return db, redis_client

async def close():
    global mongo_client, redis_client
    if mongo_client:
        mongo_client.close()
        mongo_client = None
    if redis_client:
        await redis_client.aclose()
        redis_client = None
