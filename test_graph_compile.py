# ~/ledgermind/test_graph_quant_only.py
import os, time
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/ledgermind/.env"))
import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from backend.app.engines.graph import get_graph
from backend.app.engines.state import make_initial_state
import uuid

TENANT = 'a0000000-0000-0000-0000-000000000001'
state = make_initial_state(
    "What was Eternal's consolidated revenue for FY26?",
    TENANT, str(uuid.uuid4()), str(uuid.uuid4()),
)
result = get_graph().invoke(state)
print(f"path: {result.get('path')}")
print(f"response_text: {result.get('response_text')}")