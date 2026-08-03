import re, sys
from app.metrics.registry import metric_anchor_phrases
P = metric_anchor_phrases()
for q in sys.argv[1:]:
    ql = q.lower()
    sub = sorted(p for p in P if p in ql)
    word = sorted(p for p in P if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", ql))
    print(f"\nQ: {q}")
    print(f"  substring anchors (what Stage 0c sees): {sub or 'none'}")
    print(f"  word-boundary anchors (real intent):    {word or 'none'}")
