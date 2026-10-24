"""Read-only release smoke check against local data, FastAPI, and official MCP stdio."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from quantpilot.api.app import app
from quantpilot.config import settings

ROUTES = {
    "get_market_snapshot":"candles", "get_indicators":"indicators",
    "get_market_positioning":"positioning", "get_sentiment":"sentiment",
    "check_data_coverage":"coverage", "get_prediction":"predictions",
    "explain_decision":"decisions", "compare_backtests":"backtests",
    "get_paper_portfolio":"portfolio",
}


def missing_release_tools(listed):
    """Release tools absent from the server; tools added after the release are allowed."""
    return set(ROUTES)-set(listed)


def main():
    expected, timings = {}, {}
    with TestClient(app) as client:
        health=client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["data"]["model_released"]
        for tool,route in ROUTES.items():
            started=time.perf_counter()
            response=client.get("/api/v1/"+route)
            assert response.status_code == 200, route
            result=response.json()
            assert "provenance" in result and result["model_version"], route
            expected[tool]=result["data"]
            timings[route]=round(time.perf_counter()-started,4)
        # Warm reads must use the current generation, not recompute the full catalogue.
        started=time.perf_counter()
        assert client.get("/api/v1/indicators").json()["data"] == expected["get_indicators"]
        timings["indicators_warm"]=round(time.perf_counter()-started,4)
    async def verify_mcp():
        env={**os.environ,"PYTHONPATH":str(Path("src").resolve()),
             "QUANTPILOT_DATA_DIR":str(settings.data_dir.resolve()),
             "QUANTPILOT_ARTIFACT_DIR":str(settings.artifact_dir.resolve()),
             "QUANTPILOT_DATABASE_URL":settings.database_url,
             "QUANTPILOT_MARKET_DATABASE_URL":settings.market_database_url or ''}
        args=["-m","quantpilot.api.mcp_server"]
        if os.environ.get("QUANTPILOT_SMOKE_DEBUG"):
            args=["-c","import faulthandler; faulthandler.dump_traceback_later(20, repeat=True); from quantpilot.mcp_server import main; main()"]
        params=StdioServerParameters(command=sys.executable,args=args,env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                listed=await session.list_tools()
                assert not missing_release_tools(tool.name for tool in listed.tools)
                for tool in (t for t in listed.tools if t.name in ROUTES):
                    print("Checking MCP "+tool.name,flush=True)
                    result=await asyncio.wait_for(session.call_tool(tool.name,{}),timeout=90)
                    assert not result.isError, tool.name
                    assert result.structuredContent["data"] == expected[tool.name], tool.name
    asyncio.run(verify_mcp())
    coverage=expected["check_data_coverage"]
    portfolio=expected["get_paper_portfolio"]
    output=dict(status="passed",model_version=health.json()["model_version"],
                readonly_api_routes=10,mcp_tools_matched=len(ROUTES),
                coverage_policy=coverage.get("coverage_policy"),quarantine=coverage.get("quarantine"),
                prediction_action=expected["get_prediction"]["action"],
                prediction_reasons=expected["get_prediction"]["reasons"],
                equity=portfolio["equity"],fills=len(portfolio["fills"]),
                latency_seconds=timings)
    Path(".verification").mkdir(exist_ok=True)
    Path(".verification/release-smoke.json").write_text(json.dumps(output,indent=2),encoding="utf-8")
    print(json.dumps(output,indent=2))


if __name__ == "__main__": main()
