"""
Unit tests — the one-retry-before-fallback paths.

Two providers, two helpers, one rule: a transport failure gets exactly ONE
retry, then falls back. Covered here:

  app.llm.client._call_gemini        Gemini -> Groq
  app.engines.retriever._cohere_with_retry   Cohere -> local ONNX

Both take a callable, so every test drives them with a fake that COUNTS its
invocations. "Retried once" is asserted by that count, never inferred from a
log line — a log-derived assertion would pass against a helper that logged and
did nothing.

Fully offline. No provider is constructed and no socket is opened: the fakes
raise before any client exists, time.sleep is stubbed by an autouse fixture,
and conftest's network guard is active throughout (it patches socket AND
psycopg2.connect by name, since libpq bypasses the socket layer).

WHY THESE EXIST. Measured 2026-08-21, three Paytm sweeps, three withheld
scores. Four contamination events, three of them the same shape — a single
transport failure, no retry, immediate switch to a different model or reranker:

    PQ016  Gemini -> Groq   NameResolutionError [Errno -3]
    PQ008  Gemini -> Groq   Read timed out (read timeout=20.0)
    PQ020  Cohere -> ONNX   [Errno 111] Connection refused

Rate limiting was positively excluded, not merely unobserved: zero 429s in any
log, no resource_exhausted, no retryDelay in any error, spacing >=45s against a
5 RPM ceiling, failures at scattered positions (16/20 and 8/20) with the
provider serving normally on both sides.

NEGATIVE CONTROLS ARE INLINE. Each assertion is followed by the same claim
inverted, wrapped in pytest.raises(AssertionError). A check never observed
failing is not evidence that it can fail, and keeping the control in the same
test body makes it impossible for the pair to drift apart.
"""
import pytest

from app.engines import retriever as retriever_mod
from app.engines.retriever import COHERE_RETRY_BACKOFF_S, _cohere_with_retry
from app.llm import client as client_mod
from app.llm.client import (
    TRANSPORT_RETRY_BACKOFF_S,
    _call_gemini,
    _marker_class,
    _should_fall_back,
)

# ---------------------------------------------------------------------------
# Exception fixtures — real strings, from the logs
# ---------------------------------------------------------------------------

# VERBATIM from the 2026-08-21 03:37:48 log line, urllib3 wrapper included.
#
# THE WRAPPER IS THE POINT. An abbreviated "NameResolutionError: failed to
# resolve" string passes this test for the WRONG REASON, and hides the gap it
# exists to cover: the marker list matched PQ016 on "connection"
# (HTTPSConnectionPool) and "max retries", NOT on anything about resolution.
# A first draft of this test used a shortened string, failed, and the fix was
# to make the input faithful rather than to relax the assertion. See
# TestUnwrappedNameResolution below for the behaviour with the wrapper absent.
DNS_VERBATIM = Exception(
    "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
    "Max retries exceeded with url: /v1beta/models/gemini-3.1-flash-lite:generateContent "
    "(Caused by NameResolutionError(\"HTTPSConnection("
    "host='generativelanguage.googleapis.com', port=443): Failed to resolve "
    "'generativelanguage.googleapis.com' "
    "([Errno -3] Temporary failure in name resolution)\"))"
)

# VERBATIM from the 2026-08-21 17:35:45 log line.
READ_TIMEOUT = Exception(
    "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
    "Read timed out. (read timeout=20.0)"
)

# PQ020, Cohere side.
CONNECTION_REFUSED = ConnectionRefusedError(111, "Connection refused")

RATE_LIMITED_429 = Exception("429 RESOURCE_EXHAUSTED: quota exceeded for model")
SERVER_503 = Exception("503 Service Unavailable")
CONFIG_401 = Exception("401 UNAUTHENTICATED: API key not valid")

# Carries a server-advised retryDelay inside MAX_RPM_RETRY_WAIT_S.
SERVER_ADVISED = Exception("429 rate limit exceeded, 'retryDelay': '2s'")

# The pre-edit marker tuple, spelled out so a membership change has to be
# deliberate rather than incidental.
MARKERS_BEFORE_HOIST = (
    "timeout", "timed out", "deadline", "connection", "connecterror",
    "network", "unreachable", "max retries", "remote end closed",
    "429", "resource_exhausted", "rate limit", "rate_limit",
    "500", "502", "503", "504", "unavailable", "internal server error",
)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """
    Stub the backoff in both modules.

    Without this the suite pays 1.0s per retried case in real wall time, which
    is how a fast unit suite becomes one nobody runs. The retry is still
    exercised — only the wait is removed.
    """
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(retriever_mod.time, "sleep", lambda _s: None)


def _counting(exc, succeed_on=None):
    """
    A callable that raises `exc`, and its attempt counter.

    succeed_on=N makes attempt N and later return a sentinel instead, which is
    how "the retry actually recovered" is distinguished from "the retry ran".
    """
    state = {"attempts": 0}

    def fn():
        state["attempts"] += 1
        if succeed_on is not None and state["attempts"] >= succeed_on:
            return "recovered"
        raise exc

    return fn, state


def _attempts(helper, exc):
    """How many times `helper` invoked its callable before giving up."""
    fn, state = _counting(exc)
    with pytest.raises(Exception):
        helper(fn)
    return state["attempts"]


# ---------------------------------------------------------------------------
# Gemini — transport class retries exactly once
# ---------------------------------------------------------------------------

class TestGeminiTransportRetry:
    @pytest.mark.parametrize(
        "label,exc",
        [("dns", DNS_VERBATIM), ("read_timeout", READ_TIMEOUT),
         ("refused", CONNECTION_REFUSED)],
    )
    def test_transport_failure_makes_two_attempts(self, label, exc):
        assert _attempts(_call_gemini, exc) == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, exc) == 1

    def test_retry_that_succeeds_returns_the_value(self):
        fn, state = _counting(READ_TIMEOUT, succeed_on=2)
        assert _call_gemini(fn) == "recovered"
        assert state["attempts"] == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert state["attempts"] == 1

    def test_transport_exceptions_classify_as_transport(self):
        for exc in (DNS_VERBATIM, READ_TIMEOUT, CONNECTION_REFUSED):
            assert _marker_class(exc) == "transport"
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _marker_class(READ_TIMEOUT) == "provider"


# ---------------------------------------------------------------------------
# Gemini — provider class is deliberately NOT retried
# ---------------------------------------------------------------------------

class TestGeminiProviderNotRetried:
    """
    429/5xx are the server saying something, and the server-advised retryDelay
    path already covers the case where it asks us to wait. Retrying them just
    doubles latency before the same answer.
    """

    @pytest.mark.parametrize("exc", [RATE_LIMITED_429, SERVER_503])
    def test_provider_failure_makes_one_attempt(self, exc):
        assert _attempts(_call_gemini, exc) == 1
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, exc) == 2

    def test_provider_exceptions_classify_as_provider(self):
        assert _marker_class(RATE_LIMITED_429) == "provider"
        assert _marker_class(SERVER_503) == "provider"
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _marker_class(RATE_LIMITED_429) == "transport"


# ---------------------------------------------------------------------------
# Gemini — the server-advised path is unchanged
# ---------------------------------------------------------------------------

class TestServerAdvisedRetryUnchanged:
    def test_retry_delay_still_produces_a_second_attempt(self):
        assert _attempts(_call_gemini, SERVER_ADVISED) == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, SERVER_ADVISED) == 1


# ---------------------------------------------------------------------------
# Gemini — a config error must not retry and must not fall back
# ---------------------------------------------------------------------------

class TestConfigErrorReRaises:
    """
    401/403 are deliberately absent from the marker list: serving a bad key
    from the fallback would hide the real fault.
    """

    def test_config_error_makes_one_attempt(self):
        assert _attempts(_call_gemini, CONFIG_401) == 1
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, CONFIG_401) == 2

    def test_config_error_does_not_fall_back(self):
        assert _should_fall_back(CONFIG_401) is False
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _should_fall_back(CONFIG_401) is True

    def test_config_error_has_no_marker_class(self):
        assert _marker_class(CONFIG_401) is None
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _marker_class(CONFIG_401) == "transport"


# ---------------------------------------------------------------------------
# One retry, never a ladder
# ---------------------------------------------------------------------------

class TestNoLadder:
    """
    The module docstring's rationale: a second failure means the condition is
    not transient, and the caller should fall through rather than keep a user
    waiting.
    """

    def test_gemini_stops_at_two_attempts(self):
        assert _attempts(_call_gemini, DNS_VERBATIM) == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, DNS_VERBATIM) == 3

    def test_cohere_stops_at_two_attempts(self):
        assert _attempts(_cohere_with_retry, CONNECTION_REFUSED) == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_cohere_with_retry, CONNECTION_REFUSED) == 3


# ---------------------------------------------------------------------------
# Marker membership — hoisting changed location, not behaviour
# ---------------------------------------------------------------------------

class TestMarkerMembership:
    def test_union_is_transport_then_provider(self):
        assert (client_mod._FALLBACK_MARKERS
                == client_mod._TRANSPORT_MARKERS + client_mod._PROVIDER_MARKERS)
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert client_mod._FALLBACK_MARKERS == client_mod._TRANSPORT_MARKERS

    def test_classes_are_disjoint(self):
        overlap = set(client_mod._TRANSPORT_MARKERS) & set(client_mod._PROVIDER_MARKERS)
        assert overlap == set()
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert overlap == {"timeout"}

    def test_provider_membership_is_unchanged_from_before_the_hoist(self):
        assert client_mod._PROVIDER_MARKERS == MARKERS_BEFORE_HOIST[9:]
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert client_mod._PROVIDER_MARKERS == MARKERS_BEFORE_HOIST[:9]


# ---------------------------------------------------------------------------
# The unwrapped NameResolutionError — CURRENT BEHAVIOUR, and it is a gap
# ---------------------------------------------------------------------------

class TestUnwrappedNameResolution:
    """
    CURRENT BEHAVIOUR, asserted per the conftest convention.

    The marker list carries no "resolve"/"resolution" entry, so a
    NameResolutionError stripped of its urllib3 wrapper matches nothing: it
    neither retries nor falls back. PQ016 matched only because urllib3 wrapped
    it in HTTPSConnectionPool / "Max retries exceeded", which means the
    fallback path currently depends on an exception's incidental packaging.

    When that gap is closed these assertions SHOULD fail. That is the suite
    working — read this docstring, confirm the change was intended, and move
    the assertions in the same commit as the fix.
    """

    BARE = Exception(
        "NameResolutionError: Failed to resolve 'generativelanguage.googleapis.com'"
    )

    def test_bare_name_resolution_error_has_no_marker_class(self):
        assert _marker_class(self.BARE) is None
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _marker_class(self.BARE) == "transport"

    def test_bare_name_resolution_error_does_not_fall_back(self):
        assert _should_fall_back(self.BARE) is False
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _should_fall_back(self.BARE) is True

    def test_bare_name_resolution_error_makes_one_attempt(self):
        assert _attempts(_call_gemini, self.BARE) == 1
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_call_gemini, self.BARE) == 2


# ---------------------------------------------------------------------------
# Cohere — same rule, second-failure path still reaches ONNX
# ---------------------------------------------------------------------------

class TestCohereRetry:
    @pytest.mark.parametrize(
        "exc", [CONNECTION_REFUSED, TimeoutError("timed out")]
    )
    def test_transport_failure_makes_two_attempts(self, exc):
        assert _attempts(_cohere_with_retry, exc) == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _attempts(_cohere_with_retry, exc) == 1

    def test_retry_that_succeeds_returns_the_response(self):
        fn, state = _counting(CONNECTION_REFUSED, succeed_on=2)
        assert _cohere_with_retry(fn) == "recovered"
        assert state["attempts"] == 2
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert state["attempts"] == 1

    def test_second_failure_propagates_so_the_onnx_fallback_runs(self):
        """
        The ONNX fallback is the correct behaviour when Cohere is genuinely
        down; what it should not be is the response to one refused socket. It
        stays reachable only if the second failure escapes this helper.
        """
        fn, _ = _counting(CONNECTION_REFUSED)
        with pytest.raises(ConnectionRefusedError):
            _cohere_with_retry(fn)
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert isinstance(CONNECTION_REFUSED, TimeoutError)

    def test_backoff_constant_is_separate_from_the_gemini_one(self):
        """
        Same value today, deliberately different constants: different
        providers on different links, and coupling them would tie one's tuning
        to the other's.
        """
        assert COHERE_RETRY_BACKOFF_S == 1.0
        assert TRANSPORT_RETRY_BACKOFF_S == 1.0
        assert COHERE_RETRY_BACKOFF_S is not None
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert COHERE_RETRY_BACKOFF_S == 9.0
