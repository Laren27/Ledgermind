"""
Delete ETERNAL FY24 financials rows whose metric name is a mangled
artifact of the split-initial bug (fixed in entity_resolver 2026-08-01).

Dry-run by default. Matches by PATTERN, then prints every candidate with
whether a corrected twin exists at the same value — a mangled row with no
twin is NOT deleted, since that would lose data rather than de-duplicate.
"""
import argparse
import os
import re
import sys
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

load_dotenv()
TENANT = "a0000000-0000-0000-0000-000000000001"
DSN = "postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind"

# A single leading letter followed by underscore, OR a name that is a
# corrected name minus its first letter (with optional trailing _<letter>).
MANGLED = re.compile(r"^(?:[a-z]_[a-z]|[a-z]*_i$)")


def unmangle(m: str) -> str | None:
    """Best-guess corrected form, used ONLY to find a twin — never to write."""
    if re.match(r"^[a-z]_", m):
        return m[0] + m[2:]
    if m.endswith("_i"):
        return "i" + m[:-2]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()

    conn = psycopg2.connect(DSN)
    with conn, conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = '{TENANT}';")
        cur.execute("""
            SELECT financial_type, metric, value, COUNT(*)
            FROM financials
            WHERE company='ETERNAL' AND fiscal_year='FY24'
            GROUP BY 1,2,3 ORDER BY 1,2;
        """)
        rows = cur.fetchall()

        by_ft = defaultdict(dict)
        for ft, metric, val, n in rows:
            by_ft[ft][metric] = (float(val), n)

        print(f"ETERNAL FY24 distinct metric rows: {len(rows)}\n")

        to_delete = []
        for ft, metrics in by_ft.items():
            for metric, (val, n) in sorted(metrics.items()):
                twin = None
                for cand, (cval, _) in metrics.items():
                    if cand == metric or cval != val:
                        continue
                    # metric is a mangled form of cand if dropping cand's
                    # first letter yields metric, optionally with that
                    # letter re-appended as a trailing "_<letter>".
                    head, tail = cand[0], cand[1:]
                    if metric in (tail, f"{tail}_{head}", f"{head}_{tail}"):
                        twin = cand
                        break
                if twin is None and not MANGLED.match(metric):
                    continue
                has_twin = twin is not None
                mark = "DELETE" if has_twin else "KEEP (no twin)"
                print(f"  {ft:<13} {metric:<58} = {val:>12} rows={n}  {mark}")
                if has_twin:
                    to_delete.append((ft, metric))

        print(f"\ncandidates to delete: {len(to_delete)}")
        if not a.confirm:
            print("DRY RUN — re-run with --confirm")
            return

        cur.execute("SELECT COUNT(*) FROM financials WHERE company='ETERNAL' AND fiscal_year='FY24';")
        before = cur.fetchone()[0]
        for ft, metric in to_delete:
            cur.execute("""
                DELETE FROM financials
                WHERE company='ETERNAL' AND fiscal_year='FY24'
                  AND financial_type=%s AND metric=%s;
            """, (ft, metric))
        cur.execute("SELECT COUNT(*) FROM financials WHERE company='ETERNAL' AND fiscal_year='FY24';")
        after = cur.fetchone()[0]
        print(f"\nbefore={before} after={after} deleted={before - after}")
    conn.close()


if __name__ == "__main__":
    main()
