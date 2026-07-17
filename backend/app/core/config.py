from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://ledger:ledger_dev_pass@postgres:5432/ledgermind"
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    environment: str = "development"
    JWT_SECRET: str
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()