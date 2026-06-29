# ~/ledgermind/test_semantic.py
import os
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/ledgermind/.env"))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from backend.app.engines.state import make_initial_state
from backend.app.engines.semantic_engine import semantic_engine_node
import uuid

TENANT = 'a0000000-0000-0000-0000-000000000001'
USER   = str(uuid.uuid4())
REQ    = str(uuid.uuid4())

# --- TEST 1: Qualitative risk query ---
state = make_initial_state(
    query='What regulatory risks does Eternal disclose?',
    tenant_id=TENANT, user_id=USER, request_id=REQ,
)
state['company']        = 'ETERNAL'
state['fiscal_year']    = 'FY26'
state['quarter']        = None
state['financial_type'] = 'consolidated'
state['resolved_query'] = 'ETERNAL FY26 consolidated What regulatory risks does Eternal disclose?'
state['path']           = 'semantic'

result = semantic_engine_node(state)

print('=== Semantic Engine: Qualitative query ===')
print(f'chunks retrieved : {len(result["retrieved_chunks"])}')
print(f'confidence_score : {result["confidence_score"]}')
print(f'confidence_tier  : {result["confidence_tier"]}')
print(f'crag_triggered   : {result["crag_triggered"]}')
print(f'error            : {result.get("error")}')
print(f'\nCitations:')
for i, c in enumerate(result['citations']):
    print(f'  [{i+1}] page={c["page_number"]} score={c["reranker_score"]:.4f}')
    print(f'       preview: {c["text_preview"][:80]}')