from contextlib import asynccontextmanager
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query, Request, status
from pymongo.errors import DuplicateKeyError
from app.db import connect, close
from app.models import DocumentCreate, DocumentUpdate
from app.logic import content_hash, now
from app.queueing import enqueue

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db, app.state.redis = await connect()
    yield
    await close()

app = FastAPI(title="Cohort Insights API", version="1.0.0", lifespan=lifespan)

def public_doc(doc):
    if not doc:
        return None
    result = dict(doc)
    result["document_id"] = str(result.pop("_id"))
    result.pop("_internal", None)
    return result

async def reserve_active(redis, user_id: str, document_id: str):
    # Set membership makes reservation/release idempotent; Lua keeps the limit atomic.
    key = f"active:{user_id}"
    script = """
    local n = redis.call('SCARD', KEYS[1])
    if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then return 1 end
    if n >= tonumber(ARGV[2]) then return 0 end
    redis.call('SADD', KEYS[1], ARGV[1])
    return 1
    """
    return bool(await redis.eval(script, 1, key, document_id, 3))

async def release_active(redis, user_id: str, document_id: str):
    await redis.srem(f"active:{user_id}", document_id)

@app.get("/health")
async def health(request: Request):
    try:
        await request.app.state.db.command("ping")
        await request.app.state.redis.ping()
        return {"status": "ok", "mongodb": "ok", "redis": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependency health check failed") from exc

@app.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_document(payload: DocumentCreate, request: Request):
    db, redis = request.app.state.db, request.app.state.redis
    digest = content_hash(payload.content)
    # Content cache is global by hash, because derived output depends only on content.
    cached = await redis.get(f"content-cache:{digest}")
    if cached:
        import json
        cached_result = json.loads(cached)
    else:
        cached_result = None

    doc_id = str(ObjectId())
    if not await reserve_active(redis, payload.user_id, doc_id) and not cached_result:
        raise HTTPException(status_code=429, detail="At most 3 active documents are allowed per user")

    timestamp = now()
    document = {
        "_id": ObjectId(doc_id), "user_id": payload.user_id, "title": payload.title,
        "content": payload.content, "content_hash": digest, "current_content_hash": digest,
        "summary": cached_result.get("summary") if cached_result else None,
        "tags": cached_result.get("tags") if cached_result else None,
        "summary_content_hash": digest if cached_result else None,
        "tags_content_hash": digest if cached_result else None,
        "status": "completed" if cached_result else "queued",
        "failed_stage": None, "error": None, "client_doc_ref": payload.client_doc_ref,
        "created_at": timestamp, "updated_at": timestamp,
    }
    try:
        await db.documents.insert_one(document)
    except DuplicateKeyError as exc:
        await release_active(redis, payload.user_id, doc_id)
        raise HTTPException(status_code=409, detail="client_doc_ref already exists") from exc
    if not cached_result:
        await enqueue(redis, doc_id, digest, "processing")
    return {"document_id": doc_id, "status": document["status"]}

@app.patch("/documents/{document_id}")
async def update_document(document_id: str, payload: DocumentUpdate, request: Request):
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    db, redis = request.app.state.db, request.app.state.redis
    doc = await db.documents.find_one({"_id": ObjectId(document_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    digest = content_hash(payload.content)
    # Increment generation and clear both derived fields in one atomic write.
    updated = await db.documents.find_one_and_update(
        {"_id": doc["_id"], "current_content_hash": doc["current_content_hash"]},
        {"$set": {"content": payload.content, "content_hash": digest,
                  "current_content_hash": digest, "summary": None, "tags": None,
                  "summary_content_hash": None, "tags_content_hash": None,
                  "status": "queued", "failed_stage": None, "error": None, "updated_at": now()}},
        return_document=True,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Concurrent update detected; retry")
    cached = await redis.get(f"content-cache:{digest}")
    if cached:
        import json
        result = json.loads(cached)
        await db.documents.update_one(
            {"_id": doc["_id"], "current_content_hash": digest},
            {"$set": {"summary": result["summary"], "tags": result["tags"],
                      "summary_content_hash": digest, "tags_content_hash": digest,
                      "status": "completed", "updated_at": now()}}
        )
        await release_active(redis, doc["user_id"], document_id)
    else:
        # Existing active membership remains; completed documents reserve a slot only while active.
        await reserve_active(redis, doc["user_id"], document_id)
        await enqueue(redis, document_id, digest, "processing")
    return {"document_id": document_id, "status": "completed" if cached else "queued"}

@app.get("/documents/{document_id}")
async def get_document(document_id: str, user_id: str, request: Request):
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    doc = await request.app.state.db.documents.find_one({"_id": ObjectId(document_id), "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Never expose a derived field unless its recorded hash matches current content.
    if doc.get("summary_content_hash") != doc.get("current_content_hash"):
        doc["summary"] = None
    if doc.get("tags_content_hash") != doc.get("current_content_hash"):
        doc["tags"] = None
    return public_doc(doc)

@app.get("/users/{user_id}/documents")
async def list_documents(user_id: str, request: Request, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100), status_filter: str | None = Query(None, alias="status")):
    query = {"user_id": user_id}
    allowed = {"queued", "processing", "enriching", "completed", "failed"}
    if status_filter:
        if status_filter not in allowed:
            raise HTTPException(status_code=422, detail="Invalid status filter")
        query["status"] = status_filter
    cursor = request.app.state.db.documents.find(query).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size)
    items = [public_doc(doc) async for doc in cursor]
    total = await request.app.state.db.documents.count_documents(query)
    return {"items": items, "page": page, "page_size": page_size, "total": total}

@app.get("/documents/by-ref/{client_doc_ref}")
async def get_by_ref(client_doc_ref: str, request: Request, user_id: str):
    doc = await request.app.state.db.documents.find_one({"client_doc_ref": client_doc_ref, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return public_doc(doc)
