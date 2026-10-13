from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://mongo:27017"
    mongodb_database: str = "cohort_insights"
    redis_url: str = "redis://redis:6379/0"
    active_limit: int = 3
    queue_name: str = "cohort:jobs"
    cache_ttl_seconds: int = 86400
    worker_poll_seconds: float = 1.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
