import asyncio
import psycopg2
from fastapi import FastAPI
import redis.asyncio as aioredis
import httpx
from app.api.metrics import router as metrics_router
from app.auth.router import router as auth_router
from app.api.query import router as query_router
from app.core.config import settings
from app.api.documents import router as documents_router


app = FastAPI(title="LedgerMind API", version="0.1.0")

# Include Routers
app.include_router(auth_router)
app.include_router(query_router)
app.include_router(metrics_router)
app.include_router(documents_router)

def check_postgres_sync():
    """Sync function to check DB, run in a separate thread so it doesn't block FastAPI."""
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute("SELECT 1")
    cur.close()
    conn.close()


@app.get("/health")
async def health_check():
    services = {}

    # PostgreSQL (Using psycopg2)
    try:
        await asyncio.to_thread(check_postgres_sync)
        services["postgres"] = "ok"
    except Exception as e:
        services["postgres"] = f"error: {e}"

    # Redis
    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        services["redis"] = "ok"
    except Exception as e:
        services["redis"] = f"error: {e}"

    # Qdrant
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.qdrant_url}/", timeout=5.0)
            services["qdrant"] = (
                "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
            )
    except Exception as e:
        services["qdrant"] = f"error: {e}"

    all_ok = all(v == "ok" for v in services.values())

    return {
        "status": "healthy" if all_ok else "degraded",
        "services": services,
    }