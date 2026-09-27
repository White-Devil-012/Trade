"""Indian mutual fund NAV history.

AMFI publishes the official daily NAV for every Indian scheme, but only for the
*current* day -- there is no official history endpoint. ``api.mfapi.in`` is a
community mirror that archives those same AMFI numbers per scheme, so it is the
practical source for a NAV time series. Both are used here: AMFI for the
authoritative scheme list, mfapi for history.

A NAV series is not a price series. There is no open, high, low or traded
volume -- a fund prices once a day at a single number. Rather than fabricate an
OHLC bar, every price column is set to the NAV and volume is left as NaN, and
the rest of the package is told (via ``has_volume=False``) to drop the
volume-derived features instead of training on nothing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .base import DataError, PriceHistory, normalize

MFAPI_BASE = "https://api.mfapi.in/mf"
AMFI_NAV_ALL = "https://www.amfiindia.com/spages/NAVAll.txt"
SCHEME_CACHE_TTL = 60 * 60 * 24  # the scheme list changes rarely
NAV_CACHE_TTL = 60 * 60 * 6

_PERIOD_TO_DAYS = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825, "10y": 3650, "max": 100_000}


def _get(url: str, timeout: int = 30) -> Any:
    """Fetch JSON, surfacing network problems as DataError with the cause intact."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise DataError("requests is not installed; run pip install -r requirements.txt") from exc

    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "trendlab/0.1"})
        response.raise_for_status()
    except Exception as exc:
        raise DataError(f"could not reach {url}: {exc}") from exc
    return response


class AmfiProvider:
    """Historical NAV for Indian mutual funds, keyed by AMFI scheme code."""

    name = "amfi"
    has_volume = False

    def __init__(self, cache_dir: str = ".cache/trendlab") -> None:
        self.cache_dir = Path(cache_dir)

    # ------------------------------------------------------------------ cache
    def _cached(self, path: Path, ttl: int) -> Any | None:
        if not path.exists() or time.time() - path.stat().st_mtime > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _store(self, path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        except Exception:
            pass  # a cache miss is never worth failing a request over

    # --------------------------------------------------------------- history
    def history(self, symbol: str, period: str = "5y", interval: str = "1d") -> PriceHistory:
        code = str(symbol).strip().upper().removeprefix("MF:")
        if not code.isdigit():
            raise DataError(
                f"{symbol!r} is not an AMFI scheme code. Scheme codes are numeric "
                f"(e.g. 118834, or MF:118834). Use search to find one by name."
            )

        cache = self.cache_dir / f"mf_{code}.json"
        payload = self._cached(cache, NAV_CACHE_TTL)
        if payload is None:
            payload = _get(f"{MFAPI_BASE}/{code}").json()
            self._store(cache, payload)

        frame = parse_mfapi(payload, code)
        cutoff_days = _PERIOD_TO_DAYS.get(period, 1825)
        cutoff = frame.index.max() - pd.Timedelta(days=cutoff_days)
        return frame[frame.index >= cutoff]

    # --------------------------------------------------------------- schemes
    def schemes(self) -> list[dict[str, str]]:
        """Every scheme AMFI lists, as {code, name}. Cached for a day."""
        cache = self.cache_dir / "mf_schemes.json"
        cached = self._cached(cache, SCHEME_CACHE_TTL)
        if cached is not None:
            return cached

        rows = parse_amfi_nav_all(_get(AMFI_NAV_ALL).text)
        if not rows:
            # AMFI's dump occasionally ships empty; mfapi's index is the fallback.
            rows = [
                {"code": str(r["schemeCode"]), "name": r["schemeName"]}
                for r in _get(MFAPI_BASE).json()
            ]
        self._store(cache, rows)
        return rows

    def meta(self, symbol: str) -> dict[str, Any]:
        code = str(symbol).strip().upper().removeprefix("MF:")
        cached = self._cached(self.cache_dir / f"mf_{code}.json", NAV_CACHE_TTL)
        payload = cached if cached is not None else _get(f"{MFAPI_BASE}/{code}").json()
        return payload.get("meta", {})


# --------------------------------------------------------------- parsing
def parse_mfapi(payload: dict[str, Any], code: str) -> PriceHistory:
    """Turn an mfapi scheme response into the canonical frame.

    Kept as a free function so it can be tested against a recorded response
    without touching the network.
    """
    if not isinstance(payload, dict) or not payload.get("data"):
        raise DataError(f"no NAV history returned for scheme {code!r}")

    records = []
    for row in payload["data"]:
        try:
            nav = float(row["nav"])
        except (TypeError, ValueError):
            continue  # AMFI publishes blank NAVs on non-dealing days
        if nav <= 0:
            continue
        records.append((row["date"], nav))

    if not records:
        raise DataError(f"scheme {code!r} returned no usable NAV values")

    dates, navs = zip(*records)
    frame = pd.DataFrame(
        {
            "open": navs, "high": navs, "low": navs, "close": navs,
            # A fund has no traded volume. NaN keeps volume features out of the
            # model rather than feeding them a fabricated zero.
            "volume": [float("nan")] * len(navs),
        },
        index=pd.to_datetime(list(dates), format="%d-%m-%Y", errors="coerce"),
    )
    frame = frame[frame.index.notna()]
    return normalize(frame, code)


def parse_amfi_nav_all(text: str) -> list[dict[str, str]]:
    """Extract {code, name} from AMFI's semicolon-delimited daily dump.

    The file interleaves data rows with AMC and category headings, and repeats a
    header row per section; only rows with the full field count are data.
    """
    schemes: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if ";" not in line:
            continue  # AMC or category heading
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 5 or not parts[0].isdigit():
            continue  # repeated header row
        code = parts[0]
        if code in seen:
            continue
        seen.add(code)
        schemes.append({"code": code, "name": parts[3]})
    return schemes
