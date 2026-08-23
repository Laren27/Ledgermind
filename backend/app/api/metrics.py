"""
app/api/metrics.py

Admin dashboard aggregates over `audit_log`. Read-only; no node, no engine,
nothing on the query path reaches this module.

WHY IT READS audit_log AND NOT A PURPOSE-BUILT TABLE.
audit_writer_node already writes one row per request, for EVERY outcome
including blocks and refusals (see its module docstring). A second store
would be a second copy of the same facts, which is the failure class this
project has paid for three times -- three metric registries, two formula
copies, a writer and its dry-run. The aggregates below are therefore
projections of the lineage record, not a parallel record.

TENANT SCOPING IS BELT AND BRACES HERE, DELIBERATELY.
Every statement carries an explicit `WHERE tenant_id = current_setting(...)`
even though db_transaction() sets `app.tenant_id` and RLS applies regardless.
Harmless duplication, and it makes each SQL string readable in isolation.

WHAT THESE NUMBERS CAN AND CANNOT BE TRUSTED FOR -- read before quoting one:

  cache_hit_rate_pct  STRUCTURALLY 0.0. See the warning on _SQL_SUMMARY.
  total_queries       counts a client retry as a separate question; nothing
                      in audit_log marks a row as a retry (lib/api.ts).
  avg_latency_ms      includes blocked rows, which cost single-digit ms and
                      pull the mean down. p95 is the more useful figure.
  refusal_rate_pct    a PROXY: confidence_score < 0.5. It never reads
                      `error`, so it counts every Prompt Shield block (score
                      0.0 by design) and any merely-mediocre answer. And the
                      0.5 is a THIRD copy of a measured constant that also
                      lives as COHERE_HIGH in semantic_engine.py and again in
                      _SQL_CONFIDENCE_DIST below -- nothing keeps the three in
                      step, and only CLAUDE.md's freeze on that constant is
                      currently preventing a silent divergence. [inferred: no
                      document records this duplication as a known risk.]

Admin-only via require_role("admin"), the same tier as llm_provider and
latency_ms in response_shaping.py.
"""

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
        -- WARNING: structurally always 0.0. The semantic cache described in
        -- blueprint §15 was never built (no cache module exists; Redis is only
        -- the Celery broker + health check). QueryState.cache_hit is set False
        -- in make_initial_state and never written again, so this AVG has no
        -- producer. Do NOT surface this in a dashboard as a measurement until
        -- a cache actually writes the column. See docs/IMPLEMENTATION_DELTAS.md §B.
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
