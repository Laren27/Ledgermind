"""Read-only: duplicate ETERNAL rows on the relational side?"""
import os, sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()
TENANT = "a0000000-0000-0000-0000-000000000001"

DOCS = """
SELECT doc_id, fiscal_year, quarter, financial_type, filing_date,
       sha256_checksum, is_latest, ingestion_state
FROM documents WHERE company='ETERNAL'
ORDER BY filing_date, doc_id;
"""

FIN_DUPES = """
SELECT company, fiscal_year, quarter, financial_type, metric,
       COUNT(*) AS n, COUNT(DISTINCT value) AS distinct_values,
       COUNT(DISTINCT doc_id) AS distinct_docs
FROM financials WHERE company='ETERNAL' AND is_latest=true
GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1
ORDER BY n DESC, metric LIMIT 40;
"""

FIN_TOTAL = "SELECT COUNT(*) FROM financials WHERE company='ETERNAL';"


def run(label, dsn):
    print(f"\n{'='*60}\n{label}\n{dsn.split('@')[-1][:60]}\n{'='*60}")
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  CONNECT FAILED: {str(e).splitlines()[0]}")
        return
    with conn, conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = '{TENANT}';")

        cur.execute(DOCS)
        rows = cur.fetchall()
        print(f"\ndocuments rows: {len(rows)}")
        for r in rows:
            print(f"  {str(r[0])[:8]} fy={r[1]} q={r[2]} type={r[3]} "
                  f"filed={r[4]} sha={str(r[5])[:10]} latest={r[6]} state={r[7]}")

        cur.execute(FIN_TOTAL)
        print(f"\nfinancials rows (all): {cur.fetchone()[0]}")

        cur.execute(FIN_DUPES)
        d = cur.fetchall()
        if not d:
            print("financials: NO duplicate metric rows")
        else:
            print(f"financials: {len(d)} duplicated metric keys")
            for r in d:
                print(f"  {r[1]} {r[2]} {r[3]} {r[4]:<32} n={r[5]} "
                      f"distinct_values={r[6]} distinct_docs={r[7]}")
    conn.close()


run("LOCAL DOCKER", "postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind")
sup = os.getenv("DATABASE_URL")
if sup:
    run("SUPABASE (DATABASE_URL)", sup)
else:
    print("\nDATABASE_URL unset — skipped Supabase")
