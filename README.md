# Cohort Insights API

Backend take-home implementation using Python 3.11+, FastAPI, MongoDB, Redis, and Docker Compose. Summary/tag generation is deterministic mock logic; no LLM is used.

## Run

```bash
docker compose up --build
```

API docs: http://localhost:8000/docs  
Health: http://localhost:8000/health

Submit:
```bash
curl -X POST http://localhost:8000/documents \
  -H 'content-type: application/json' \
  -d '{"user_id":"u-1","title":"Example","content":"Python services process documents using queues and workers","client_doc_ref":"partner-123"}'
```

Poll (replace ID):
```bash
curl 'http://localhost:8000/documents/DOCUMENT_ID?user_id=u-1'
```

Update:
```bash
curl -X PATCH http://localhost:8000/documents/DOCUMENT_ID \
  -H 'content-type: application/json' \
  -d '{"content":"Updated content that must be processed again"}'
```

List:
```bash
curl 'http://localhost:8000/users/u-1/documents?page=1&page_size=20&status=completed'
```

External reference:
```bash
curl 'http://localhost:8000/documents/by-ref/partner-123?user_id=u-1'
```

## Design decisions

- **MongoDB** stores source documents, status, stage outputs, timestamps, hashes, and failure metadata.
- **Redis list** is the work queue. The worker is a separate process; API requests do not wait for processing.
- **Redis active set** tracks active document IDs per user. A Lua script atomically checks the three-job cap and adds a reservation.
- **Redis content cache** is keyed by SHA-256 of content, not document ID. Cached results are reused for identical content.
- **Crosswalk policy:** `client_doc_ref` is globally unique. Reusing a reference returns HTTP 409, even if the submitted content differs. A partner must use a new reference for a new logical document. The unique sparse MongoDB index enforces this.
- **Ownership:** read and list operations scope queries to `user_id`; single-document reads return 404 for non-owners.
- **Status:** `failed_stage` is a constrained value (`processing` or `enriching`), and `error` holds a human-readable failure detail.

## Schema & Staleness Design

Each document stores `current_content_hash`, `summary_content_hash`, and `tags_content_hash`. On PATCH, the content and `current_content_hash` change atomically while both derived outputs and their hashes are cleared in the same MongoDB update. Each worker job carries the hash of the content version it was created for. Before writing a result, the worker filters by both document ID and `current_content_hash`; enrichment additionally requires `summary_content_hash` to match the job hash. Thus a worker for an old version cannot publish into a newer version. GET independently suppresses any derived field whose stored hash differs from `current_content_hash`. The reader therefore never receives a derived field represented as current unless its version hash matches the current content hash. Summary and tags are published separately during processing, but the API may show a current summary while enrichment is running; tags remain absent until their matching version is available. A PATCH racing a worker invalidates the old job's guarded writes.

## Concurrency and failure behavior

The queue message includes document ID, content hash, and stage. Workers discard messages for a non-current hash. Each stage simulates a 10% failure chance. An enrichment failure preserves the summary and records `failed_stage="enriching"`; it does not rerun stage one automatically. A processing failure records `failed_stage="processing"`. A production deployment should add an explicit retry endpoint or delayed retry policy, visibility/lease semantics, dead-lettering, and queue durability guarantees.

## Indexes

- `{user_id: 1, created_at: -1}` for newest-first user listings.
- `{user_id: 1, status: 1, created_at: -1}` for filtered listings.
- `{client_doc_ref: 1}` unique and sparse for indexed external-reference lookup.
- `{content_hash: 1}` for content-hash lookup/diagnostics.

## At 100×

At 500K documents for one user, the compound user/time index still avoids scanning unrelated users, but that user's index range becomes large and deep `skip/limit` pagination becomes increasingly expensive because MongoDB must walk past skipped entries. I would switch to cursor pagination using the last row's `(created_at, _id)` pair, with a stable sort and a compound index supporting that order. Large per-user histories may also justify archival or time-based retention policies.

For horizontal scaling, I would avoid using only `user_id` as the shard key because a very large tenant can become a hot shard and a monotonically growing tenant's writes are concentrated. A hashed `user_id` distributes tenants but cannot split a single tenant's documents well. Depending on access patterns, a compound key involving a hashed tenant identifier and a high-cardinality document identifier, or a bucketed tenant/time strategy, can distribute one large tenant while preserving acceptable query targeting. The trade-off is that queries by user may become scatter-gather if the key is not aligned with the dominant query pattern; this should be validated against actual workload and MongoDB's shard-key constraints.

At 100× submit QPS, one Redis set per user remains a simple membership model, but hot tenants can concentrate operations on one Redis key and a process crash can leave stale reservations. I would use atomic Lua scripts, reservation TTLs or leases, reconciliation against MongoDB, and metrics for active-set drift. If Redis Cluster is used, all keys touched by a Lua script must share a hash slot. The content cache should remain separate from active-job tracking, with TTLs and a documented fail-open/fail-closed policy.

Finally, skip/limit should be replaced with keyset pagination for deep pages. I would also examine whether the exact total count is necessary; count queries can be expensive for very large histories. Return a `next_cursor` and fetch `page_size + 1` documents to determine whether another page exists.

## Tests

Run local tests with:
```bash
python -m pip install -r requirements.txt
pytest -q
```

The included tests cover deterministic processing helpers and request validation. Add integration tests with MongoDB and Redis for ownership, 429 enforcement, PATCH/worker races, cache behavior, and crosswalk uniqueness before treating this as production-ready.

