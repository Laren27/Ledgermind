"""
Shared fixtures and hard guards for the pure-function unit suite.

SCOPE OF THIS SUITE — read before adding a file.

Every test under backend/tests/ is a unit test over a PURE function: one that
takes plain Python values and returns plain Python values. No test in this
directory may touch the network, Postgres, Qdrant, Gemini, Groq, Cohere, or a
corpus PDF. That is not a style preference, it is what makes the suite runnable
in CI, on a quota-exhausted day, and against a database that is mid-migration.

A function that needs any of those is OUT OF SCOPE here. It belongs in
regression_check.py (integration, needs the corpus) or in a targeted script
under scripts/ (the pattern test_synthesis_floor.py follows, monkeypatching the
provider boundary rather than reaching through it).

ASSERTIONS RECORD OBSERVED BEHAVIOUR, NOT DESIRED BEHAVIOUR.

Several functions covered here have known defects, catalogued in
docs/audit/repo_audit_20260811.md. Where a defect exists the test asserts what
the code CURRENTLY DOES and names the finding in its docstring. That is
deliberate: a test suite whose purpose is to detect change must first describe
the present accurately. A test asserting the fixed behaviour of an unfixed
function is a failing test, and a suite that is red on arrival gets ignored.

When one of those defects is fixed, the corresponding test SHOULD fail. That is
the suite working. Read the docstring, confirm the change was intended, update
the assertion in the same commit as the fix.

No test here is marked xfail. xfail(strict=False) would let a genuine
regression pass silently, and every defect covered below is currently stable
and reproducible rather than flaky.
"""
import os
import socket
import sys

import pytest

# The container binds ./backend to /app, so both /app and this file's parent
# are the same directory. Adding it explicitly means the suite also runs from
# a host checkout with the venv interpreter, where PYTHONPATH is not preset.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------

class NetworkAccessAttempted(RuntimeError):
    """Raised when a test tries to open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """
    Fail loudly on outbound connection attempts during a test body.

    The suite's zero-network property is a claim that has to be enforced rather
    than asserted in a docstring -- a lazily-constructed client is exactly the
    kind of thing that starts making calls one refactor later, and the failure
    mode without this guard is a test suite that quietly depends on Qdrant
    Cloud being up.

    EXACTLY WHAT THIS COVERS, AND WHAT IT DOES NOT.

    Patching socket catches every client that opens its connection through
    Python: httpx, qdrant_client, cohere, groq, google-genai, urllib. Verified
    by probe -- socket.create_connection and socket.socket.connect both raise.

    It does NOT catch a C extension that calls the OS directly. psycopg2 is the
    one such client in this project (CLAUDE.md §7: raw psycopg2, not
    SQLAlchemy); it connects via libpq and never touches Python's socket
    module. Measured: with the socket patch alone, psycopg2.connect to the
    live database SUCCEEDED. That is why psycopg2.connect is patched by name
    below rather than trusted to the socket layer.

    The general lesson, if another C-extension client is added: the socket
    patch is a broad net with a known hole, not a proof. A new client needs
    its own line here, and a probe confirming the line works.

    Applies to test bodies only. Module-level imports run at collection time,
    before fixtures, which is intentional: importing app.engines.retriever
    pulls fastembed and qdrant_client, and both are import-clean (measured:
    2.77s, no connection). Client construction in that module is lazy and
    guarded by module-level globals, so nothing connects until a function that
    is out of scope here is called.
    """
    def _blocked(*args, **kwargs):
        raise NetworkAccessAttempted(
            "A test attempted a network connection. Every test under "
            "backend/tests/ must be pure: no DB, no Qdrant, no LLM, no Cohere. "
            "If the function under test needs a connection, it does not belong "
            "in this suite -- see the conftest docstring."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    # C-extension clients that bypass the socket module -- see docstring.
    import psycopg2
    monkeypatch.setattr(psycopg2, "connect", _blocked)


# ---------------------------------------------------------------------------
# eval_runner import
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def eval_runner():
    """
    scripts/eval_runner.py, imported as a module.

    It calls parser.parse_args() at module scope with --model AND --api-base
    marked required, so a bare import raises SystemExit(2). Both are supplied
    below; adding a third required flag to the runner breaks every test using
    this fixture, and no type checker sees an argv list built by hand.
    sys.argv is substituted for the
    duration of the import, matching what scripts/test_eval_matcher.py already
    does -- this fixture is the same manoeuvre, not a second approach to it.

    Import is otherwise side-effect free: argparse, a path computation, and an
    --out sanity check that only fires when --out resolves inside
    golden_dataset/. Nothing opens a socket or reads the corpus.

    The module is NOT importable by name (scripts/ has no __init__.py on the
    import path used here), hence spec_from_file_location.
    """
    import importlib.util

    scripts_dir = os.path.join(_BACKEND_ROOT, "scripts")
    module_path = os.path.join(scripts_dir, "eval_runner.py")

    saved_argv = sys.argv
    sys.argv = ["eval_runner.py", "--model", "unused-by-these-tests",
                "--api-base", "http://unused-by-these-tests"]
    try:
        spec = importlib.util.spec_from_file_location(
            "eval_runner_under_unit_test", module_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved_argv

    return module


# ---------------------------------------------------------------------------
# Block builder for document_classifier
# ---------------------------------------------------------------------------

@pytest.fixture
def make_block():
    """
    Build a PageBlock from plain values.

    PageBlock is a plain dataclass -- page_number, content, block_type -- so a
    document layout can be expressed as a literal without parsing a PDF. This
    is what makes detect_sections testable here at all; the rest of the
    ingestion path takes parser output and does not have that property.
    """
    from app.ingestion.models import BlockType, PageBlock

    def _make(page_number: int, content: str, block_type: str = BlockType.TABLE):
        return PageBlock(
            page_number=page_number, content=content, block_type=block_type
        )

    return _make
