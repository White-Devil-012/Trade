"""FastAPI backend for the TrendLab dashboard."""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..analysis import analyze, compare
from ..search import search as symbol_search
from ..config import AppConfig
from ..data.base import DataError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="TrendLab",
    description="Technical analysis, market-regime detection and probabilistic forecasting.",
    version="0.1.0",
)


def _credentials_ok(header: str, user: str, password: str) -> bool:
    """Check an HTTP Basic header in constant time."""
    if not header.startswith("Basic "):
        return False
    try:
        given_user, _, given_pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    # compare_digest on bytes: a str compare would raise on non-ASCII input.
    return secrets.compare_digest(given_user.encode(), user.encode()) & secrets.compare_digest(
        given_pw.encode(), password.encode()
    )


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """Password-protect the whole app when TRENDLAB_PASSWORD is set.

    Off by default so local use stays frictionless. Worth turning on for any
    public deployment: every forecast costs seconds of CPU and a share of the
    Yahoo rate limit, and an open URL lets anyone spend both.

    The health check stays open so the hosting platform can still probe it.
    """
    password = os.environ.get("TRENDLAB_PASSWORD")
    if not password or request.url.path == "/api/health":
        return await call_next(request)

    user = os.environ.get("TRENDLAB_USER", "trendlab")
    if _credentials_ok(request.headers.get("authorization", ""), user, password):
        return await call_next(request)
    return Response(
        status_code=401,
        content="Authentication required.",
        headers={"WWW-Authenticate": 'Basic realm="TrendLab", charset="UTF-8"'},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/analyze")
def api_analyze(
    symbol: str = Query(..., min_length=1, max_length=24),
    period: str = Query("5y", pattern=r"^(1y|2y|3y|5y|10y|max)$"),
    provider: str = Query("auto", pattern=r"^(auto|yahoo|amfi|synthetic)$"),
    horizon: int = Query(5, ge=1, le=60),
    forecast: bool = Query(True),
) -> dict[str, Any]:
    """Full report for one symbol."""
    from dataclasses import replace

    cfg = AppConfig()
    cfg = replace(cfg, model=replace(cfg.model, horizon_days=horizon))
    try:
        return analyze(symbol.strip(), period, provider, cfg, include_forecast=forecast)
    except DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConnectionError, TimeoutError, OSError) as exc:
        # A blocked or unreachable data feed is an upstream problem, not a bug
        # here -- say so, and name the offline fallback.
        raise HTTPException(
            status_code=502,
            detail=(
                f"could not reach the market data provider: {str(exc).rstrip('.')}. Check your "
                f"network (a proxy or firewall may block finance.yahoo.com), or switch Source "
                f"to 'Demo data' to explore the app offline."
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"analysis failed: {exc}") from exc


@app.get("/api/screen")
def api_screen(
    symbols: str = Query(..., description="comma-separated symbols"),
    period: str = Query("2y", pattern=r"^(1y|2y|3y|5y|10y|max)$"),
    provider: str = Query("auto", pattern=r"^(auto|yahoo|amfi|synthetic)$"),
) -> dict[str, Any]:
    """Quick multi-symbol screen. Capped so one request cannot stall the server."""
    tickers = [s.strip() for s in symbols.split(",") if s.strip()][:12]
    if not tickers:
        raise HTTPException(status_code=400, detail="no symbols supplied")
    return {"rows": compare(tickers, period, provider, AppConfig())}


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(15, ge=1, le=40),
    funds: bool = Query(True),
) -> dict[str, Any]:
    """Find a symbol or mutual fund scheme by name or ticker.

    Always returns something for a common Indian name even when the upstream
    search endpoints are unreachable -- the builtin index is matched locally.
    """
    try:
        hits = symbol_search(q.strip(), limit=limit, include_funds=funds)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"search failed: {exc}") from exc
    return {"query": q.strip(), "results": [h.to_dict() for h in hits]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
