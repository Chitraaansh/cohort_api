# Lightweight contract test for local development. Full integration tests require MongoDB and Redis.
from app.models import DocumentCreate, DocumentUpdate
from pydantic import ValidationError
import pytest

def test_create_validation():
    with pytest.raises(ValidationError):
        DocumentCreate(user_id="", title="Title", content="Content")

def test_update_validation():
    with pytest.raises(ValidationError):
        DocumentUpdate(content="")
