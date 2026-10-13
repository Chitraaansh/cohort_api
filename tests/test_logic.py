from app.logic import content_hash, mock_summary, mock_tags

def test_content_hash_is_deterministic():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")

def test_summary_and_tags():
    summary = mock_summary("Python microservices process telecom documents with queues and workers")
    assert "Python" in summary
    assert "microservices" in mock_tags(summary)
