# ~/ledgermind/test_graph_compile.py
from backend.app.engines.graph import build_graph
g = build_graph()
print("Graph compiled:", g is not None)
print(g.get_graph().draw_mermaid())