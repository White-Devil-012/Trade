"""Symbol and scheme search across Indian equities, indices and mutual funds.

Three sources, in order of reliability:

1. A built-in index of NIFTY 50 constituents and the major Indian indices. It is
   small, offline, and always works -- which matters because Yahoo's search
   endpoint is unofficial and has broken before.
2. Yahoo's search endpoint, for everything outside that list.
3. AMFI's scheme list for mutual funds, matched locally.

Results are ranked so an exact ticker beats a name that merely contains the
query, and NSE listings outrank the same company's other listings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable

YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"

# NSE listings for the NIFTY 50 plus the indices people actually look up.
# Enough that the common case never depends on an unofficial endpoint.
BUILTIN_INDIA: list[tuple[str, str, str]] = [
    ("^NSEI", "NIFTY 50", "index"), ("^NSEBANK", "NIFTY Bank", "index"),
    ("^BSESN", "BSE SENSEX", "index"), ("^CNXIT", "NIFTY IT", "index"),
    ("RELIANCE.NS", "Reliance Industries", "equity"),
    ("TCS.NS", "Tata Consultancy Services", "equity"),
    ("HDFCBANK.NS", "HDFC Bank", "equity"), ("ICICIBANK.NS", "ICICI Bank", "equity"),
    ("INFY.NS", "Infosys", "equity"), ("BHARTIARTL.NS", "Bharti Airtel", "equity"),
    ("SBIN.NS", "State Bank of India", "equity"), ("LICI.NS", "Life Insurance Corporation", "equity"),
    ("ITC.NS", "ITC", "equity"), ("HINDUNILVR.NS", "Hindustan Unilever", "equity"),
    ("LT.NS", "Larsen & Toubro", "equity"), ("KOTAKBANK.NS", "Kotak Mahindra Bank", "equity"),
    ("AXISBANK.NS", "Axis Bank", "equity"), ("MARUTI.NS", "Maruti Suzuki India", "equity"),
    ("SUNPHARMA.NS", "Sun Pharmaceutical Industries", "equity"),
    ("TITAN.NS", "Titan Company", "equity"), ("ULTRACEMCO.NS", "UltraTech Cement", "equity"),
    ("BAJFINANCE.NS", "Bajaj Finance", "equity"), ("NESTLEIND.NS", "Nestle India", "equity"),
    ("WIPRO.NS", "Wipro", "equity"), ("ONGC.NS", "Oil & Natural Gas Corporation", "equity"),
    ("NTPC.NS", "NTPC", "equity"), ("POWERGRID.NS", "Power Grid Corporation of India", "equity"),
    ("TATAMOTORS.NS", "Tata Motors", "equity"), ("TATASTEEL.NS", "Tata Steel", "equity"),
    ("JSWSTEEL.NS", "JSW Steel", "equity"), ("ADANIENT.NS", "Adani Enterprises", "equity"),
    ("ADANIPORTS.NS", "Adani Ports and SEZ", "equity"), ("COALINDIA.NS", "Coal India", "equity"),
    ("HCLTECH.NS", "HCL Technologies", "equity"), ("TECHM.NS", "Tech Mahindra", "equity"),
    ("ASIANPAINT.NS", "Asian Paints", "equity"), ("BAJAJFINSV.NS", "Bajaj Finserv", "equity"),
    ("GRASIM.NS", "Grasim Industries", "equity"), ("HINDALCO.NS", "Hindalco Industries", "equity"),
    ("CIPLA.NS", "Cipla", "equity"), ("DRREDDY.NS", "Dr. Reddy's Laboratories", "equity"),
    ("DIVISLAB.NS", "Divi's Laboratories", "equity"), ("EICHERMOT.NS", "Eicher Motors", "equity"),
    ("HEROMOTOCO.NS", "Hero MotoCorp", "equity"), ("BAJAJ-AUTO.NS", "Bajaj Auto", "equity"),
    ("BRITANNIA.NS", "Britannia Industries", "equity"), ("APOLLOHOSP.NS", "Apollo Hospitals", "equity"),
    ("INDUSINDBK.NS", "IndusInd Bank", "equity"), ("TATACONSUM.NS", "Tata Consumer Products", "equity"),
    ("SBILIFE.NS", "SBI Life Insurance", "equity"), ("HDFCLIFE.NS", "HDFC Life Insurance", "equity"),
    ("SHRIRAMFIN.NS", "Shriram Finance", "equity"), ("BPCL.NS", "Bharat Petroleum", "equity"),
]


# Display names for the builtin list, so a ticker can always be shown as a name.
_NAMES = {symbol: name for symbol, name, _ in BUILTIN_INDIA}

# Demo mode's mutual funds. The names are deliberately fictional: demo prices
# are random, and a random NAV under a real fund's name reads as a real NAV.
DEMO_FUNDS: list[dict[str, str]] = [
    {"code": "900001", "name": "Demo Nifty 50 Index Fund - Growth"},
    {"code": "900002", "name": "Demo Flexi Cap Fund - Growth"},
    {"code": "900003", "name": "Demo Small Cap Fund - Growth"},
    {"code": "900004", "name": "Demo Mid Cap Fund - Growth"},
    {"code": "900005", "name": "Demo ELSS Tax Saver Fund - Growth"},
    {"code": "900006", "name": "Demo Liquid Fund - Growth"},
    {"code": "900007", "name": "Demo Large Cap Fund - Growth"},
    {"code": "900008", "name": "Demo Balanced Advantage Fund - Growth"},
]


def lookup_name(symbol: str) -> str | None:
    """Company or index name for a known symbol, else None."""
    key = str(symbol).strip().upper()
    if key in _NAMES:
        return _NAMES[key]
    if key.startswith("MF:"):
        code = key.removeprefix("MF:")
        for fund in DEMO_FUNDS:
            if fund["code"] == code:
                return fund["name"]
    return None


@dataclass(frozen=True)
class SearchResult:
    symbol: str
    name: str
    kind: str        # equity | fund | index | etf
    exchange: str
    provider: str    # yahoo | amfi
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def score_match(query: str, symbol: str, name: str) -> float:
    """Rank a candidate against the query. Higher is better; 0 means no match.

    Ordering intent: an exact ticker wins outright, then a name that starts with
    the query, then one that contains every query word.
    """
    q = query.strip().lower()
    if not q:
        return 0.0

    sym_l = symbol.lower()
    sym_root = sym_l.split(".")[0].lstrip("^")
    name_l = name.lower()

    if q == sym_l or q == sym_root:
        return 100.0
    if name_l == q:
        return 95.0
    if sym_root.startswith(q):
        return 80.0 + len(q) / len(sym_root) * 5
    if name_l.startswith(q):
        return 70.0 + len(q) / len(name_l) * 5

    q_tokens, name_tokens = _tokens(q), _tokens(name)
    if not q_tokens:
        return 0.0

    # Every query word must appear somewhere, as a whole word or a word prefix.
    matched = sum(
        1 for qt in q_tokens if any(nt == qt or nt.startswith(qt) for nt in name_tokens)
    )
    if matched == len(q_tokens):
        # Shorter names are the better match for the same set of words.
        return 40.0 + 20.0 * (len(q_tokens) / max(len(name_tokens), 1))
    if matched:
        return 10.0 * (matched / len(q_tokens))
    if q in name_l:
        return 15.0
    return 0.0


def search_builtin(query: str, limit: int = 10) -> list[SearchResult]:
    """Match against the offline Indian index. Never touches the network."""
    hits = []
    for rank, (symbol, name, kind) in enumerate(BUILTIN_INDIA):
        s = score_match(query, symbol, name)
        if s > 0:
            exchange = "NSE" if symbol.endswith(".NS") else "BSE" if symbol.endswith(".BO") else "INDEX"
            # Ties break by position in BUILTIN_INDIA, which is ordered by how
            # often people look each one up -- so "nifty" leads with NIFTY 50.
            hits.append((rank, SearchResult(symbol, name, kind, exchange, "yahoo", s)))
    hits.sort(key=lambda pair: (-pair[1].score, pair[0]))
    return [r for _, r in hits[:limit]]


def parse_yahoo_search(payload: dict[str, Any], query: str, india_only: bool = True) -> list[SearchResult]:
    """Map Yahoo's search response into SearchResults.

    Separated from the fetch so it can be tested against a recorded response.
    """
    out: list[SearchResult] = []
    for quote in payload.get("quotes", []):
        symbol = quote.get("symbol")
        if not symbol:
            continue
        if india_only and not (symbol.endswith((".NS", ".BO")) or symbol.startswith("^NSE")
                               or symbol in {"^BSESN", "^CNXIT"}):
            continue

        name = quote.get("longname") or quote.get("shortname") or symbol
        quote_type = (quote.get("quoteType") or "").upper()
        kind = {"EQUITY": "equity", "ETF": "etf", "INDEX": "index",
                "MUTUALFUND": "fund"}.get(quote_type, "equity")
        exchange = quote.get("exchange") or ""
        exchange = {"NSI": "NSE", "BSE": "BSE", "BOM": "BSE"}.get(exchange, exchange)

        s = score_match(query, symbol, name)
        # An NSE listing is the one a retail user in India usually wants.
        if symbol.endswith(".NS"):
            s += 3.0
        out.append(SearchResult(symbol, name, kind, exchange, "yahoo", s))
    return out


def search_yahoo(query: str, limit: int = 10, india_only: bool = True) -> list[SearchResult]:
    try:
        import requests

        response = requests.get(
            YAHOO_SEARCH,
            params={"q": query, "quotesCount": 25, "newsCount": 0},
            timeout=15,
            headers={"User-Agent": "trendlab/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        # Yahoo's search endpoint is unofficial; a failure here must not take
        # the whole search down, because the builtin index still has answers.
        return []
    hits = parse_yahoo_search(payload, query, india_only)
    hits.sort(key=lambda r: -r.score)
    return [h for h in hits if h.score > 0][:limit]


def search_funds(query: str, schemes: Iterable[dict[str, str]], limit: int = 10) -> list[SearchResult]:
    """Match an AMFI scheme list locally. ``schemes`` is [{code, name}, ...]."""
    hits = []
    for scheme in schemes:
        code, name = scheme.get("code", ""), scheme.get("name", "")
        s = score_match(query, code, name)
        if s > 0:
            hits.append(SearchResult(f"MF:{code}", name, "fund", "AMFI", "amfi", s))
    hits.sort(key=lambda r: (-r.score, len(r.name)))
    return hits[:limit]


def search(query: str, limit: int = 15, include_funds: bool = True,
           india_only: bool = True, cache_dir: str = ".cache/trendlab",
           include_equities: bool = True, demo: bool = False) -> list[SearchResult]:
    """Search everything, best matches first.

    The builtin index answers first and always; network sources only add to it.
    ``demo`` stays fully offline and swaps real fund schemes for DEMO_FUNDS.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    results: list[SearchResult] = []
    if include_equities:
        results.extend(search_builtin(query, limit))
    seen = {r.symbol for r in results}

    if demo:
        if include_funds:
            for hit in search_funds(query, DEMO_FUNDS, limit=8):
                if hit.symbol not in seen:
                    seen.add(hit.symbol)
                    results.append(hit)
        results.sort(key=lambda r: -r.score)
        return results[:limit]

    if include_equities:
        for hit in search_yahoo(query, limit, india_only):
            if hit.symbol not in seen:
                seen.add(hit.symbol)
                results.append(hit)

    if include_funds:
        try:
            from .data.amfi import AmfiProvider

            fund_limit = limit if not include_equities else 8
            for hit in search_funds(query, AmfiProvider(cache_dir).schemes(), limit=fund_limit):
                if hit.symbol not in seen:
                    seen.add(hit.symbol)
                    results.append(hit)
        except Exception:
            pass  # AMFI unreachable; equities still searchable

    results.sort(key=lambda r: -r.score)
    return results[:limit]
