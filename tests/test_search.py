"""Symbol search: ranking, parsing and graceful degradation."""

import pytest
from fastapi.testclient import TestClient

from trendlab.search import (
    SearchResult, parse_yahoo_search, score_match, search_builtin, search_funds,
)
from trendlab.server.app import app

SCHEMES = [
    {"code": "118834", "name": "HDFC Top 100 Fund - Growth"},
    {"code": "120503", "name": "HDFC Mid-Cap Opportunities Fund - Direct Plan - Growth"},
    {"code": "119551", "name": "Aditya Birla Sun Life Frontline Equity Fund - Growth"},
]


# ---------- ranking ----------
def test_exact_ticker_beats_name_match():
    exact = score_match("tcs", "TCS.NS", "Tata Consultancy Services")
    partial = score_match("tcs", "TATAMOTORS.NS", "Tata Motors")
    assert exact > partial
    assert exact == 100.0


def test_ticker_match_ignores_exchange_suffix():
    assert score_match("reliance", "RELIANCE.NS", "Reliance Industries") == 100.0


def test_index_caret_is_ignored_for_matching():
    assert score_match("nsei", "^NSEI", "NIFTY 50") == 100.0


def test_prefix_beats_contains():
    prefix = score_match("hdfc", "HDFCBANK.NS", "HDFC Bank")
    contains = score_match("bank", "HDFCBANK.NS", "HDFC Bank")
    assert prefix > contains


def test_all_query_words_must_appear():
    assert score_match("hdfc bank", "HDFCBANK.NS", "HDFC Bank") > 0
    assert score_match("hdfc airline", "HDFCBANK.NS", "HDFC Bank") < 20


def test_empty_query_scores_zero():
    assert score_match("", "TCS.NS", "Tata Consultancy") == 0.0


# ---------- builtin index ----------
def test_builtin_finds_common_indian_names():
    for query, expected in [("reliance", "RELIANCE.NS"), ("infosys", "INFY.NS"),
                            ("nifty", "^NSEI"), ("tata consultancy", "TCS.NS")]:
        hits = search_builtin(query, 5)
        assert hits, f"no builtin match for {query!r}"
        assert hits[0].symbol == expected, f"{query!r} gave {hits[0].symbol}"


def test_builtin_needs_no_network():
    """The offline index is what makes search usable when Yahoo is unreachable."""
    assert len(search_builtin("bank", 10)) >= 3


def test_builtin_ties_break_by_popularity_order():
    # "NIFTY 50" and "NIFTY IT" score identically on a "nifty" prefix.
    assert search_builtin("nifty", 3)[0].symbol == "^NSEI"


# ---------- funds ----------
def test_fund_search_matches_name_and_code():
    by_name = search_funds("hdfc", SCHEMES)
    assert by_name and all(h.symbol.startswith("MF:") for h in by_name)
    assert search_funds("118834", SCHEMES)[0].symbol == "MF:118834"


def test_fund_results_are_tagged_as_funds():
    assert all(h.kind == "fund" for h in search_funds("hdfc", SCHEMES))


def test_shorter_scheme_name_ranks_first_on_equal_words():
    hits = search_funds("hdfc", SCHEMES)
    assert hits[0].name == "HDFC Top 100 Fund - Growth"


# ---------- Yahoo parsing ----------
YAHOO_PAYLOAD = {
    "quotes": [
        {"symbol": "RELIANCE.NS", "longname": "Reliance Industries Limited",
         "quoteType": "EQUITY", "exchange": "NSI"},
        {"symbol": "RELIANCE.BO", "longname": "Reliance Industries Limited",
         "quoteType": "EQUITY", "exchange": "BSE"},
        {"symbol": "RS", "longname": "Reliance Steel", "quoteType": "EQUITY", "exchange": "NYQ"},
        {"noSymbol": True},
    ]
}


def test_yahoo_parsing_filters_to_india():
    hits = parse_yahoo_search(YAHOO_PAYLOAD, "reliance", india_only=True)
    assert {h.symbol for h in hits} == {"RELIANCE.NS", "RELIANCE.BO"}


def test_yahoo_parsing_can_include_global():
    hits = parse_yahoo_search(YAHOO_PAYLOAD, "reliance", india_only=False)
    assert "RS" in {h.symbol for h in hits}


def test_nse_listing_outranks_bse_for_same_company():
    hits = parse_yahoo_search(YAHOO_PAYLOAD, "reliance")
    ranked = sorted(hits, key=lambda h: -h.score)
    assert ranked[0].symbol == "RELIANCE.NS"


def test_yahoo_exchange_codes_are_humanised():
    hits = {h.symbol: h.exchange for h in parse_yahoo_search(YAHOO_PAYLOAD, "reliance")}
    assert hits["RELIANCE.NS"] == "NSE"
    assert hits["RELIANCE.BO"] == "BSE"


def test_yahoo_parsing_skips_entries_without_a_symbol():
    assert all(h.symbol for h in parse_yahoo_search(YAHOO_PAYLOAD, "reliance", india_only=False))


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_search_endpoint_returns_results(client):
    body = client.get("/api/search", params={"q": "reliance", "funds": False}).json()
    assert body["results"][0]["symbol"] == "RELIANCE.NS"


def test_search_endpoint_rejects_a_single_character(client):
    assert client.get("/api/search", params={"q": "r"}).status_code == 422


def test_search_endpoint_caps_limit(client):
    assert client.get("/api/search", params={"q": "bank", "limit": 500}).status_code == 422


def test_search_result_shape_is_stable():
    hit = search_builtin("reliance", 1)[0]
    assert set(hit.to_dict()) == {"symbol", "name", "kind", "exchange", "provider", "score"}
    assert isinstance(hit, SearchResult)
