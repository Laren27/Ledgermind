"""
Unit tests — app.ingestion.entity_resolver

Covers resolve_company, resolve_ticker and resolve_metric. All three take a
string and return a string or a dataclass; none touches the corpus, the DB or
the network.

Several assertions here record DEFECTS rather than intended behaviour. Each
names its audit finding. See conftest.py for why they are written that way.
"""
import pytest

from app.ingestion.entity_resolver import (
    COMPANY_REGISTRY,
    resolve_company,
    resolve_metric,
    resolve_ticker,
)


# ---------------------------------------------------------------------------
# resolve_company — exact alias matching
# ---------------------------------------------------------------------------

class TestResolveCompanyExact:
    def test_exact_alias_resolves_to_profile(self):
        profile = resolve_company("eternal limited")
        assert profile is not None
        assert profile.primary == "ETERNAL"
        assert profile.ticker == "ETERNAL"

    def test_lookup_is_case_insensitive_and_stripped(self):
        """key = raw_name.lower().strip() at entity_resolver.py:313."""
        for variant in ("PAYTM", "paytm", "  Paytm  ", "PayTM"):
            profile = resolve_company(variant)
            assert profile is not None, variant
            assert profile.primary == "PAYTM", variant

    def test_former_name_resolves_to_current_entity(self):
        """Zomato renamed to Eternal; both are aliases of one profile."""
        assert resolve_company("zomato").primary == "ETERNAL"
        assert resolve_company("zomato limited").primary == "ETERNAL"

    def test_unknown_company_returns_none(self):
        assert resolve_company("HDFC Bank") is None
        assert resolve_company("Reliance Industries") is None

    def test_registry_is_a_closed_hardcoded_set(self):
        """
        documents F1 — the ingestible company set is a literal list in code.

        pipeline.py:96-101 refuses to ingest anything resolve_company returns
        None for, so onboarding company N+1 is a code change plus a redeploy.
        This test pins the size so that growing the list is a deliberate,
        reviewed act rather than a drive-by edit.
        """
        assert len(COMPANY_REGISTRY) == 7
        assert {p.primary for p in COMPANY_REGISTRY} == {
            "ETERNAL", "PAYTM", "NYKAA", "POLICYBAZAAR",
            "DELHIVERY", "SWIGGY", "TITAN",
        }


# ---------------------------------------------------------------------------
# resolve_company — no substring fallback (F1 FIXED)
# ---------------------------------------------------------------------------

class TestResolveCompanyNoSubstringFallback:
    """
    F1 FIXED 2026-08-11 — resolution is exact-alias-only.

    A substring-containment loop previously returned the first alias that was
    contained in the input, so any company whose legal name contained an
    incumbent's alias resolved to the incumbent. Ingestion then overwrote
    `company` with profile.primary, filing one issuer's financials under
    another's key in the same tenant with nothing in the audit trail recording
    the substitution. The match was also dict-insertion-ordered, so which
    incumbent won was non-deterministic.

    These names are the exact collisions measured live before the fix. They
    now return None, which pipeline.py:96-101 turns into a refusal to ingest
    -- the correct outcome for a company the registry does not know.

    These assertions are CORRECT AS BEHAVIOUR. If any starts returning a
    profile again, the fallback has been reintroduced.
    """

    def test_titan_biotech_no_longer_misfiles_as_titan_company(self):
        """
        Titan Biotech Limited (BSE 524717) is a real listed company, distinct
        from Titan Company Limited. Before the fix it resolved to TITAN.
        """
        assert resolve_company("Titan Biotech Limited") is None

    @pytest.mark.parametrize(
        "distinct_company,formerly_absorbed_into",
        [
            ("TITANIUM INDUSTRIES LIMITED", "TITAN"),
            ("ETERNAL MATERIALS PVT LTD", "ETERNAL"),
            ("ONE97 REALTY", "PAYTM"),
            ("Bundl Foods", "SWIGGY"),
        ],
    )
    def test_former_substring_collisions_now_return_none(
        self, distinct_company, formerly_absorbed_into
    ):
        assert resolve_company(distinct_company) is None

    def test_legitimate_multiword_aliases_still_resolve(self):
        """
        The fix must not break exact multi-word aliases -- these are indexed
        forms, not substring matches, and every one of the 91 golden questions
        names its company in a form indexed exactly like these.
        """
        assert resolve_company("Titan Company Ltd").primary == "TITAN"
        assert resolve_company("one97 communications limited").primary == "PAYTM"
        assert resolve_company("Zomato Limited").primary == "ETERNAL"

    def test_names_sharing_no_alias_substring_still_return_none(self):
        assert resolve_company("Infosys Technologies") is None


# ---------------------------------------------------------------------------
# resolve_ticker
# ---------------------------------------------------------------------------

class TestResolveTicker:
    def test_known_company_returns_registry_ticker(self):
        assert resolve_ticker("Zomato") == "ETERNAL"
        assert resolve_ticker("eternal limited") == "ETERNAL"
        assert resolve_ticker("  paytm  ") == "PAYTM"

    def test_unknown_company_returns_uppercased_input(self):
        """
        entity_resolver.py:321 falls back to raw_name.upper().strip() rather
        than raising or returning None. The caller receives a plausible-looking
        ticker for a company the registry has never heard of.
        """
        assert resolve_ticker("HDFC Bank") == "HDFC BANK"
        assert resolve_ticker("  some new issuer ") == "SOME NEW ISSUER"

    def test_former_substring_collision_now_passes_through(self):
        """
        F1 FIXED — the misfile no longer reaches the value used as the DB key.

        Note what did NOT change: resolve_ticker still never returns None, it
        uppercases the input. So an unknown company yields a plausible-looking
        ticker rather than a signal. That is the surface F2 works against --
        router.py:107 gates on `resolved in _KNOWN_TICKERS`, and a miss there
        silently sets company=None, which retriever.py:174 reads as "no filter".
        """
        assert resolve_ticker("Titan Biotech Limited") == "TITAN BIOTECH LIMITED"


# ---------------------------------------------------------------------------
# resolve_metric
# ---------------------------------------------------------------------------

class TestResolveMetricCanonical:
    def test_exact_alias_resolves_to_canonical_name(self):
        assert resolve_metric("Revenue from operations") == "revenue"
        assert resolve_metric("Profit before tax") == "profit_before_tax"

    def test_ocr_mangled_alias_in_registry_resolves(self):
        """
        'ill total incomc 1+11' is a registry alias -- an OCR mangling of
        'III. Total Income (I+II)' captured deliberately. Normalisation maps
        this raw label onto it.
        """
        assert resolve_metric("III Total Incomc 1+11") == "total_income"

    def test_slash_split_matches_across_collapsed_separator(self):
        """
        normalize_metric_label collapses 'products / services' to
        'products/services'; _WORD_SPLIT_RE splits on slashes so the alias
        'sale of products' still matches. Regression noted in-code as having
        broken TITAN revenue extraction on first attempt.
        """
        assert resolve_metric("Sale of products/services") == "revenue"

    def test_three_word_alias_wins_over_tied_two_word_aliases(self):
        """
        PAYTM's 'Deferred tax expense/ (credit)' once tied between
        deferred_tax and tax_expense at two words each, resolving by
        declaration order to the wrong one. A 3-word alias was added so it
        wins outright.
        """
        assert resolve_metric("Deferred tax expense/ (credit)") == "deferred_tax"


class TestResolveMetricFallThrough:
    def test_empty_input_returns_unmapped_sentinel(self):
        assert resolve_metric("") == "unmapped_metric"
        assert resolve_metric("   ") == "unmapped_metric"

    def test_bare_total_refuses_to_resolve(self):
        """
        entity_resolver.py:190-196 returns 'total' rather than guessing. A bare
        Total names no metric without its section -- the same label means
        revenue, segment assets and segment liabilities on one TITAN page.
        """
        assert resolve_metric("Total") == "total"
        assert resolve_metric("total") == "total"

    def test_total_with_trailing_punctuation_also_refuses(self):
        """
        The guard tests the NORMALISED form, so 'Total:' reaches it even though
        _should_skip_row's exact lowercase test does not catch it. This is the
        second defence described at entity_resolver.py:180-186.
        """
        assert resolve_metric("Total:") == "total"

    def test_unknown_label_is_slugified_not_rejected(self):
        """
        documents F6 — the fall-through stores an unrecognised label as a
        slug rather than refusing it. 174 such names exist in `financials`,
        covering 686 of 1437 rows, and none is reachable by a DSL query
        because dsl_compiler validates against the registry.
        """
        assert resolve_metric("Wibble Wobble Frobnicator") == "wibble_wobble_frobnicator"

    def test_slugification_replaces_spaces_only(self):
        """Confirms the fall-through shape: normalised text, spaces to underscores."""
        result = resolve_metric("Some Entirely Novel Line Item")
        assert result == "some_entirely_novel_line_item"
        assert " " not in result
