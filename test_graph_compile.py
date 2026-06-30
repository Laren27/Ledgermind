# ~/ledgermind/test_graph_e2e.py
import os, time
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/ledgermind/.env"))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from backend.app.engines.graph import get_graph
from backend.app.engines.state import make_initial_state
import uuid

TENANT = 'a0000000-0000-0000-0000-000000000001'

def run(query, label):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    state = make_initial_state(query, TENANT, str(uuid.uuid4()), str(uuid.uuid4()))
    graph = get_graph()
    result = graph.invoke(state)

    print(f"path            : {result.get('path')}")
    print(f"is_blocked      : {result['is_blocked']}")
    print(f"confidence_tier : {result.get('confidence_tier')}")
    print(f"confidence_score: {result.get('confidence_score')}")
    print(f"latency_ms      : {result.get('latency_ms')}")
    print(f"error           : {result.get('error')}")
    print(f"\nresponse_text:\n{result.get('response_text')}")
    return result

# Test 1: Quantitative — should hit Path 2, return verified SQL value
run("What was Eternal's consolidated revenue for FY26?", "TEST 1: QUANTITATIVE")
time.sleep(15)

# Test 2: Semantic — should hit Path 1, return synthesised answer with citations
run("What regulatory risks does Eternal disclose?", "TEST 2: SEMANTIC")
time.sleep(15)

# Test 3: Blocked — should never reach router or any engine
run("Should I buy Eternal stock?", "TEST 3: BLOCKED")