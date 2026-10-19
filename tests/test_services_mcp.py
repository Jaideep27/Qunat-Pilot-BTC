import asyncio
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from quantpilot.api import app
from quantpilot import services

def test_api_envelopes_read_only_and_bounds(account):
    with TestClient(app) as client:
        r=client.get("/api/v1/predictions")
        assert r.status_code == 200
        assert r.json()["data"]["action"] == "NO_TRADE"
        assert r.json()["data"] == services.prediction()["data"]
        assert client.get("/api/v1/candles?limit=2001").status_code == 422
        assert client.get("/api/v1/decisions?index=-1").status_code == 422
        assert client.post("/api/v1/portfolio").status_code == 405
        assert client.get("/api/v1/portfolio").json()["data"]["equity"] == 10000

def test_official_sdk_stdio_discovery_calls_and_invalid_inputs(account):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from quantpilot.config import settings
    async def run():
        env={**os.environ,"PYTHONPATH":str(Path("src").resolve()),"QUANTPILOT_DATA_DIR":str(settings.data_dir),
             "QUANTPILOT_DATABASE_URL":settings.database_url,"QUANTPILOT_ARTIFACT_DIR":str(settings.artifact_dir),
             "QUANTPILOT_MARKET_DATABASE_URL":settings.market_database_url or ''}
        params=StdioServerParameters(command=sys.executable,args=["-m","quantpilot.mcp_server"],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                discovered=await session.list_tools()
                assert {t.name for t in discovered.tools} == {"get_market_snapshot","get_indicators",
                    "get_market_positioning","get_sentiment","check_data_coverage","get_prediction",
                    "explain_decision","compare_backtests","get_paper_portfolio","get_news_events"}
                expected = {
                    "get_market_snapshot": services.candles,
                    "get_indicators": services.indicators,
                    "get_market_positioning": lambda: services.family("positioning"),
                    "get_sentiment": lambda: services.family("sentiment"),
                    "check_data_coverage": services.coverage,
                    "get_prediction": services.prediction,
                    "explain_decision": services.explain_decision,
                    "compare_backtests": services.research,
                    "get_paper_portfolio": services.paper,
                    "get_news_events": services.news,
                }
                for tool in discovered.tools:
                    assert tool.outputSchema["type"] == "object"
                    result = await session.call_tool(tool.name, {})
                    assert not result.isError, tool.name
                    assert result.structuredContent is not None, tool.name
                    assert result.structuredContent["data"] == expected[tool.name]()["data"]
                    assert "provenance" in result.structuredContent
                bad=await session.call_tool("get_market_snapshot",{"limit":2001})
                assert bad.isError
                invalid=await session.call_tool("compare_backtests",{"groups":["invented"]})
                assert invalid.isError
    asyncio.run(run())


def test_shared_feature_cache_invalidates_on_market_file_change(account,monkeypatch):
    import pandas as pd
    from quantpilot.config import settings
    path=settings.data_dir/"candles.parquet"
    path.write_bytes(b"first generation")
    calls=[]
    monkeypatch.setattr(services,"load_frames",lambda root:{"candles":pd.DataFrame({"close":[1.]})})
    def build(*args):
        calls.append(True)
        return ({"generation":len(calls)},None,None)
    monkeypatch.setattr(services,"build_features",build)
    first=services.feature_data()
    assert services.feature_data() is first
    assert len(calls)==1
    path.write_bytes(b"second file generation")
    second=services.feature_data()
    assert len(calls)==2 and second[0]["generation"]==2
