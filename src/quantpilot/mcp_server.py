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
async def get_shadow_forecasts() -> dict[str, Any]:
    """Research forecasts (1h/4h/24h direction, ranges, drop risk, volatility) with their forward record; never trades."""
    return await asyncio.to_thread(services.shadow_forecasts)

@mcp.tool(structured_output=True)
async def get_macro_calendar() -> dict[str, Any]:
    """Upcoming CPI, jobs and FOMC releases plus recent first-release values; no consensus surprise."""
    return await asyncio.to_thread(services.macro)

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

@mcp.tool(structured_output=True)
async def ask_outlook(type: Literal['direction', 'touch', 'range', 'big_move', 'explain'],
                      horizon_hours: Annotated[int, Field(ge=1, le=168)], level: float | None = None,
                      side: Literal['above', 'below', 'up', 'down', 'either'] | None = None,
                      threshold_pct: float | None = None, coverage: float = .8) -> dict[str, Any]:
    """Validated outlook for a BTCUSDT question: probability or range, confidence label, evidence, track record."""
    question = {k: v for k, v in dict(type=type, horizon_hours=horizon_hours, level=level, side=side,
                                      threshold_pct=threshold_pct, coverage=coverage).items() if v is not None}
    return services.envelope(await asyncio.to_thread(services.outlook_question, question))


@mcp.tool(structured_output=True)
async def record_ai_view(answer_id: str, probability: Annotated[float, Field(ge=0, le=1)],
                         reasons: list[str]) -> dict[str, Any]:
    """Record your own probability for an outlook answer so it is scored against the quant number."""
    return services.envelope(await asyncio.to_thread(services.record_view, answer_id, probability, reasons))


@mcp.tool(structured_output=True)
async def get_answer_scoreboard() -> dict[str, Any]:
    """Scored outlook answers: quant vs AI vs base-rate accuracy per question type and horizon."""
    return await asyncio.to_thread(services.answers_view)

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

