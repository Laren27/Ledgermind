import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import require_role
from app.db.session import db_transaction

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["metrics"])

class SummaryStats(BaseModel):
    total_queries: int
    cache_hit_rate_pct: float
    avg_latency_ms: float
    p95_latency_ms: float
    refusal_rate_pct: float

class PathCount(BaseModel):
    path: str
    count: int

class DayCount(BaseModel):
    date: str
    count: int

class TierCount(BaseModel):
    tier: str
    count: int

class PathLatency(BaseModel):
    path: str
    avg_ms: float

class MetricsResponse(BaseModel):
    summary: SummaryStats
    path_distribution: list[PathCount]
    volume_by_day: list[DayCount]
    confidence_distribution: list[TierCount]
    avg_latency_by_path: list[PathLatency]

_SQL_SUMMARY = """
    SELECT
        COUNT(*)                                                        AS total_queries,
        ROUND(AVG(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS cache_hit_rate_pct,
        ROUND(AVG(latency_ms)::numeric, 0)                              AS avg_latency_ms,
        ROUND((PERCENTILE_CONT(0.95) WITHIN GROUP
              (ORDER BY latency_ms))::numeric, 0)                       AS p95_latency_ms,
        ROUND(AVG(CASE WHEN confidence_score < 0.5
                       THEN 1.0 ELSE 0.0 END) * 100, 1)                AS refusal_rate_pct
    FROM audit_log
    WHERE tenant_id = current_setting('app.tenant_id')::uuid
"""

_SQL_PATH_DIST = """
    SELECT COALESCE(query_path, 'unknown') AS path, COUNT(*) AS count
    FROM audit_log
    WHERE tenant_id = current_setting('app.tenant_id')::uuid
    GROUP BY query_path ORDER BY count DESC
"""

_SQL_VOLUME_BY_DAY = """
    SELECT DATE(created_at AT TIME ZONE 'UTC')::text AS date, COUNT(*) AS count
    FROM audit_log
    WHERE tenant_id = current_setting('app.tenant_id')::uuid
    GROUP BY DATE(created_at AT TIME ZONE 'UTC') ORDER BY date ASC
"""

_SQL_CONFIDENCE_DIST = """
    SELECT
        CASE
            WHEN confidence_score >= 0.8 THEN 'high'
            WHEN confidence_score >= 0.5 THEN 'medium'
            ELSE 'low'
        END AS tier,
        COUNT(*) AS count
    FROM audit_log
    WHERE tenant_id = current_setting('app.tenant_id')::uuid
    GROUP BY 1 ORDER BY count DESC
"""

_SQL_LATENCY_BY_PATH = """
    SELECT COALESCE(query_path, 'unknown') AS path,
           ROUND(AVG(latency_ms)::numeric, 0) AS avg_ms
    FROM audit_log
    WHERE tenant_id = current_setting('app.tenant_id')::uuid
      AND latency_ms > 0
    GROUP BY query_path ORDER BY avg_ms DESC
"""

@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(user: dict = Depends(require_role("admin"))):
    tenant_id = user["tenant_id"]
    logger.info("Metrics requested | tenant_id=%s user_id=%s", tenant_id, user["user_id"])

    with db_transaction(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_SUMMARY)
            row = cur.fetchone()
            summary = SummaryStats(
                total_queries=int(row[0] or 0),
                cache_hit_rate_pct=float(row[1] or 0.0),
                avg_latency_ms=float(row[2] or 0.0),
                p95_latency_ms=float(row[3] or 0.0),
                refusal_rate_pct=float(row[4] or 0.0),
            )

            cur.execute(_SQL_PATH_DIST)
            path_distribution = [PathCount(path=r[0], count=int(r[1])) for r in cur.fetchall()]

            cur.execute(_SQL_VOLUME_BY_DAY)
            volume_by_day = [DayCount(date=r[0], count=int(r[1])) for r in cur.fetchall()]

            cur.execute(_SQL_CONFIDENCE_DIST)
            confidence_distribution = [TierCount(tier=r[0], count=int(r[1])) for r in cur.fetchall()]

            cur.execute(_SQL_LATENCY_BY_PATH)
            avg_latency_by_path = [PathLatency(path=r[0], avg_ms=float(r[1])) for r in cur.fetchall()]

    return MetricsResponse(
        summary=summary,
        path_distribution=path_distribution,
        volume_by_day=volume_by_day,
        confidence_distribution=confidence_distribution,
        avg_latency_by_path=avg_latency_by_path,
    )
