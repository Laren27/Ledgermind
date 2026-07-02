"""
DB transaction helper.

USAGE SCOPE (Phase 5): this is currently used ONLY by auth/service.py's
login lookup. quant_engine.py (and presumably contradiction.py /
audit_writer.py, following the same pattern) open their own psycopg2
connections per call and run `SET LOCAL app.tenant_id` themselves using
tenant_id read from QueryState -- they do not need a connection injected
from the HTTP layer. As long as state["tenant_id"] is sourced from the
verified JWT (see api/query.py), those per-call connections are already
RLS-correct.

CRITICAL: uses SET LOCAL, not SET. SET LOCAL is scoped to the current
transaction and clears automatically on COMMIT/ROLLBACK. A bare SET on a
pooled/reused connection can leak one tenant's setting into the next
request. Same class of bug as the superuser-bypasses-RLS issue fixed in
Phase 4 -- do not "simplify" this to a plain SET.
"""
import psycopg2
from contextlib import contextmanager
from app.core.config import settings


def _get_raw_connection():
    return psycopg2.connect(settings.database_url)


@contextmanager
def db_transaction(tenant_id: str | None):
    """
    Opens a transaction, sets app.tenant_id for RLS (if provided), yields a
    connection, commits on success / rolls back on exception.

    tenant_id=None is reserved for the auth bootstrap case (login lookup
    only) -- see migration 006's auth_bootstrap_lookup policy. Every other
    caller must pass a real tenant_id.
    """
    conn = _get_raw_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if tenant_id is not None:
                    cur.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
                yield conn
        # `with conn:` commits on clean exit, rolls back on exception
    finally:
        conn.close()