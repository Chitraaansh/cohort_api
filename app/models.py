from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

Status = Literal["queued", "processing", "enriching", "completed", "failed"]

class DocumentCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2_000_000)
    client_doc_ref: str | None = Field(default=None, min_length=1, max_length=256)

class DocumentUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)

class DocumentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    document_id: str
    user_id: str
    title: str
    content: str
    status: Status
    current_content_hash: str
    summary: str | None = None
    tags: list[str] | None = None
    summary_content_hash: str | None = None
    tags_content_hash: str | None = None
    failed_stage: Literal["processing", "enriching"] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    client_doc_ref: str | None = None
