import hashlib
import re
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc)

def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def mock_summary(content: str) -> str:
    words = content.split()
    if len(words) <= 35:
        return " ".join(words)
    return " ".join(words[:35]) + "..."

def mock_tags(summary: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_-]{3,}", summary.lower())
    stop = {"the", "and", "for", "that", "with", "this", "from", "are", "was", "you"}
    unique = []
    for word in words:
        if word not in stop and word not in unique:
            unique.append(word)
        if len(unique) == 5:
            break
    return unique
