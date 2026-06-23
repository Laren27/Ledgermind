from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://ledger:ledger_dev_pass@postgres:5432/ledgermind"
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    environment: str = "development"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()