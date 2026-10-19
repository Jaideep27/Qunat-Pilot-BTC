"""Official MCP SDK v1, local stdio, read-only tools."""
import asyncio
from typing import Annotated, Any, Literal
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from . import services
from .store import database, initialize

mcp = FastMCP("QuantPilot BTC", instructions="Research and simulated portfolio only. Check freshness and coverage before interpreting results.")

@mcp.tool(structured_output=True)
async def get_market_snapshot(limit: Annotated[int, Field(ge=1, le=2000)] = 288) -> dict[str, Any]:
    """Completed BTCUSDT USD-M candles with timestamps and freshness."""
    return await asyncio.to_thread(services.candles, limit)

@mcp.tool(structured_output=True)
async def get_indicators() -> dict[str, Any]:
    """Causal technical catalogue, derivatives interactions and completed timeframe context."""
    return await asyncio.to_thread(services.indicators)

@mcp.tool(structured_output=True)
async def get_market_positioning() -> dict[str, Any]:
    """Open interest and global/top account and top position ratios."""
    return await asyncio.to_thread(services.family, "positioning")

@mcp.tool(structured_output=True)
async def get_sentiment() -> dict[str, Any]:
    """Alternative.me Fear & Greed with conservative historical publication lag."""
    return await asyncio.to_thread(services.family, "sentiment")

@mcp.tool(structured_output=True)
async def get_news_events(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> dict[str, Any]:
    """Source-linked news excerpts, completed reasoning and coverage limitations; research only."""
    return await asyncio.to_thread(services.news, limit)

@mcp.tool(structured_output=True)
async def check_data_coverage() -> dict[str, Any]:
    """Historical coverage, missing intervals and model release eligibility."""
    return await asyncio.to_thread(services.coverage)

@mcp.tool(structured_output=True)
async def get_prediction() -> dict[str, Any]:
    """Calibrated next-hour probability, associations, and risk/edge gates."""
    return await asyncio.to_thread(services.prediction)

@mcp.tool(structured_output=True)
async def explain_decision(index: Annotated[int, Field(ge=0, le=1999)] = 0) -> dict[str, Any]:
    """Inspect a persisted decision, newest first."""
    return await asyncio.to_thread(services.explain_decision, index)

@mcp.tool(structured_output=True)
async def compare_backtests(groups: Annotated[list[Literal["price","technical","derivatives","full"]], Field(min_length=1, max_length=4)] = ["price","technical","derivatives","full"]) -> dict[str, Any]:
    """Feature-group metrics evaluated on identical eligible timestamps."""
    return await asyncio.to_thread(services.research, groups)

@mcp.tool(structured_output=True)
async def get_paper_portfolio() -> dict[str, Any]:
    """Virtual balances, exposure, fees, funding, decisions and worker checkpoint."""
    return await asyncio.to_thread(services.paper)

def main():
    initialize(database())
    # Initialize native ML libraries before starting the stdio event loop.
    # Model files are trusted owner-generated local artifacts only.
    model_path = services.settings.artifact_dir/"released.joblib"
    if model_path.exists():
        bundle = services.joblib.load(model_path)
        if bundle["kind"] == "xgboost":
            import shap  # noqa: F401
        del bundle
    mcp.run(transport="stdio")

if __name__ == "__main__": main()

