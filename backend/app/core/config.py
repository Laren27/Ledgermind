from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://ledger:ledger_dev_pass@postgres:5432/ledgermind"
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    # gemini_api_key / groq_api_key removed 2026-08-03: nothing read either
    # attribute. Every LLM consumer reads the environment directly via
    # os.getenv (app/llm/client.py), so these were a second, silently unused
    # declaration of the same configuration. The env vars themselves are
    # untouched and still required -- extra="ignore" below means their
    # continued presence in .env is a no-op rather than an error.
    environment: str = "development"
    JWT_SECRET: str
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()