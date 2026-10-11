"""FastAPI backend for the TrendLab dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..analysis import analyze, compare
from ..market import market_overview, stock_snapshot
from ..search import search as symbol_search
from ..config import AppConfig
from ..data.base import DataError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="TrendLab",
    description="Technical analysis, market-regime detection and probabilistic forecasting.",
    version="0.1.0",
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


def _upstream_error(exc: Exception) -> HTTPException:
    """Turn a data-feed failure into a message a person can act on."""
    if isinstance(exc, DataError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(
        status_code=502,
        detail=(
            f"could not reach the market data provider: {str(exc).rstrip('.')}. Check your "
            f"network, or turn on Demo data to explore the app offline."
        ),
    )


@app.get("/api/market")
def api_market(
    provider: str = Query("auto", pattern=r"^(auto|yahoo|synthetic)$"),
) -> dict[str, Any]:
    """Indices and popular stocks for the home screen."""
    try:
        return market_overview(provider)
    except (DataError, ConnectionError, TimeoutError, OSError) as exc:
        raise _upstream_error(exc) from exc


@app.get("/api/stock")
def api_stock(
    symbol: str = Query(..., min_length=1, max_length=24),
    provider: str = Query("auto", pattern=r"^(auto|yahoo|amfi|synthetic)$"),
) -> dict[str, Any]:
    """Price, chart history, returns and market mood for one stock or fund.

    Fast on purpose. The forecast comes from /api/analyze, which takes seconds.
    """
    try:
        return stock_snapshot(symbol.strip(), provider)
    except (DataError, ConnectionError, TimeoutError, OSError) as exc:
        raise _upstream_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not load {symbol}: {exc}") from exc


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(15, ge=1, le=40),
    funds: bool = Query(True),
    kind: str = Query("all", pattern=r"^(all|fund|equity)$"),
    provider: str = Query("auto", pattern=r"^(auto|yahoo|amfi|synthetic)$"),
) -> dict[str, Any]:
    """Find a symbol or mutual fund scheme by name or ticker.

    Always returns something for a common Indian name even when the upstream
    search endpoints are unreachable -- the builtin index is matched locally.
    """
    try:
        hits = symbol_search(
            q.strip(), limit=limit,
            include_funds=funds and kind != "equity",
            include_equities=kind != "fund",
            demo=provider == "synthetic",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"search failed: {exc}") from exc
    return {"query": q.strip(), "results": [h.to_dict() for h in hits]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
