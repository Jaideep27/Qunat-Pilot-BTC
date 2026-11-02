from contextlib import asynccontextmanager
from typing import Annotated, Literal
from fastapi import FastAPI, Query, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware
from quantpilot.api import services
from quantpilot.config import settings
from quantpilot.storage.database import database, initialize

@asynccontextmanager
async def lifespan(app):
    initialize(database())
    if settings.market_database_url:initialize(database(settings.market_database_url))
    yield

app = FastAPI(title="QuantPilot BTC", version="0.1.0", lifespan=lifespan,
              description="QuantPilot BTC analysis: market data, indicators, strategies and research. Nothing trades.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"], allow_headers=["*"])

@app.get("/api/v1/health")
def health(): return services.health()

@app.get('/api/v1/operations')
def operational_status():
    try:return services.operations()
    except SQLAlchemyError:raise HTTPException(status_code=503,detail='Operational database unavailable') from None

@app.get('/api/v1/ready')
def readiness():
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    try:result=services.operations()
    except SQLAlchemyError:raise HTTPException(status_code=503,detail='Operational database unavailable') from None
    return JSONResponse(jsonable_encoder(result),status_code=200 if result['data']['ready'] else 503)

@app.get("/api/v1/candles")
def candles(limit: Annotated[int, Query(ge=1, le=2000)] = 288): return services.candles(limit)

@app.get("/api/v1/positioning")
def positioning(): return services.family("positioning")

@app.get("/api/v1/sentiment")
def sentiment(): return services.family("sentiment")

@app.get('/api/v1/news')
def news(limit: Annotated[int, Query(ge=1, le=100)] = 20, compact: bool = False):
    return services.news(limit, compact)

@app.get('/api/v1/forecasts')
def forecasts(): return services.shadow_forecasts()
@app.get('/api/v1/strategy-research')
def strategy_research(): return services.strategy_research()

@app.get('/api/v1/archive')
def archive(): return services.archive()

@app.get('/api/v1/macro')
def macro(): return services.macro()

@app.get("/api/v1/coverage")
def coverage(): return services.coverage()

@app.get("/api/v1/backtests")
def backtests(detail: bool = False): return services.research(detail=detail)

@app.get('/api/v1/market/storage')
def market_storage():
    from quantpilot.storage.market import summary
    return services.envelope(summary(database(settings.market_database_url)),source='Persistent market database')

@app.get('/api/v1/market/candles')
def chart_candles(market: Literal['BTCUSDT']='BTCUSDT',
                  timeframe: Literal['5m','15m','30m','1h','4h','1d','1w']='5m',
                  limit: Annotated[int,Query(ge=1,le=5000)]=1000,
                  forming: bool=False,
                  before: Annotated[int|None,Query(ge=0,le=4102444800000)]=None):
    import pandas as pd
    from quantpilot.storage.market import candles
    rows=candles(database(settings.market_database_url),market,timeframe,limit,
                 pd.to_datetime(before,unit='ms',utc=True) if before is not None else None,include_forming=forming)
    return services.envelope(rows,rows[-1]['available_at'] if rows else None,'Persistent exchange history')

@app.get('/api/v1/market/studies')
def chart_studies(market: Literal['BTCUSDT']='BTCUSDT',
                  timeframe: Literal['5m','15m','30m','1h','4h','1d','1w']='5m',
                  limit: Annotated[int,Query(ge=50,le=3000)]=1000,
                  selected: Annotated[str,Query(max_length=1000)]='EMA 21,rsi_14',
                  before: Annotated[int|None,Query(ge=0,le=4102444800000)]=None):
    import pandas as pd
    from quantpilot.storage.market import candles
    from quantpilot.features.charting import studies
    rows=candles(database(settings.market_database_url),market,timeframe,min(5000,limit+300),
                 pd.to_datetime(before,unit='ms',utc=True) if before is not None else None)
    result=studies(rows,selected.split(',')[:12])
    return services.envelope(result,rows[-1]['available_at'] if rows else None,'Causal chart study catalogue')


LOOPBACK = {'127.0.0.1', '::1', 'localhost', 'testclient'}


def local_only(request: Request):
    if request.client is None or request.client.host not in LOOPBACK:
        raise HTTPException(status_code=403, detail='Write endpoints accept local requests only')


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    ai: bool = True


class OutlookBody(BaseModel):
    type: str
    horizon_hours: int
    level: float | None = None
    side: str | None = None
    threshold_pct: float | None = None
    coverage: float = .8
    thread_id: str | None = None
    run_id: str | None = None


class ViewBody(BaseModel):
    probability: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=5)
    run_id: str | None = None


@app.post('/api/v1/ask')
def ask_route(body: AskBody, request: Request):
    local_only(request)
    try:
        result = services.ask(body.question, ai=body.ai)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return services.envelope(result, result.get('data_time'), 'Outlook engine + Claude explanation', max_age=7200)


@app.post('/api/v1/outlook')
def outlook_route(body: OutlookBody, request: Request):
    local_only(request)
    fields = body.model_dump(exclude={'thread_id', 'run_id'}, exclude_none=True)
    try:
        result = services.outlook_question(fields, source='chat' if body.run_id else 'mcp',
                                           thread_id=body.thread_id, run_id=body.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return services.envelope(result, result.get('data_time'), 'Outlook engine', max_age=7200)


@app.post('/api/v1/answers/{answer_id}/ai-view')
def ai_view_route(answer_id: str, body: ViewBody, request: Request):
    local_only(request)
    return services.envelope(services.record_view(answer_id, body.probability, body.reasons, body.run_id))


@app.get('/api/v1/answers')
def answers_route(limit: Annotated[int, Query(ge=1, le=500)] = 50):
    return services.answers_view(limit)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=32)


@app.post('/api/v1/chat')
def chat_route(body: ChatBody, request: Request):
    local_only(request)
    from quantpilot.ai.chat import Busy
    try:
        result = services.chat_send(body.message, body.thread_id)
    except Busy:
        raise HTTPException(status_code=409, detail='A chat answer is already in progress') from None
    except ValueError as exc:
        status = 429 if 'limit' in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None
    return services.envelope(result, source='In-app chat (Claude with QuantPilot tools)')


@app.get('/api/v1/chat/threads')
def chat_threads_route():
    return services.chat_threads()


@app.get('/api/v1/chat/threads/{thread_id}')
def chat_thread_route(thread_id: str):
    result = services.chat_thread(thread_id)
    if result['data'] is None:
        raise HTTPException(status_code=404, detail='Unknown chat thread')
    return result


# ---- analysis app (A15) ---------------------------------------------------------------------------------------------

@app.get('/api/v1/analysis/snapshot')
def analysis_snapshot_route():
    return services.analysis_snapshot()


@app.get('/api/v1/analysis/levels')
def analysis_levels_route():
    return services.analysis_levels()


@app.get('/api/v1/analysis/summary')
def analysis_summary_route():
    return services.analysis_summary()


@app.get('/api/v1/analysis/catalogue')
def analysis_catalogue_route():
    return services.analysis_catalogue()


@app.get('/api/v1/analysis/briefs')
def analysis_briefs_route(limit: Annotated[int, Query(ge=1, le=20)] = 5):
    return services.analysis_briefs(limit)


AnalysisKind = Literal['indicator', 'strategy', 'news', 'chart']
Timeframe = Literal['15m', '1h', '4h', '1d']


@app.get('/api/v1/analysis/explanations')
def analysis_explanations_route(kind: AnalysisKind, ref: Annotated[str, Query(min_length=1, max_length=80)],
                                timeframe: Timeframe | None = None):
    return services.analysis_explanation(kind, ref, timeframe)


class ExplainBody(BaseModel):
    kind: AnalysisKind
    ref: str = Field(min_length=1, max_length=80)
    timeframe: Timeframe | None = None
    studies: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=12)


@app.post('/api/v1/analysis/explain')
def analysis_explain_route(body: ExplainBody, request: Request):
    # Uses the owner's Claude subscription: this computer only, never the public gateway.
    local_only(request)
    from quantpilot.analysis.ai import RateLimited
    try:
        return services.analysis_explain(body.kind, body.ref, body.timeframe, body.studies)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except Exception as exc:
        if type(exc).__name__ == 'UsageLimitReached':
            raise HTTPException(status_code=429, detail='Claude usage limit reached; try again later.') from None
        raise HTTPException(status_code=502, detail='Claude could not write an explanation right now.') from None


@app.post('/api/v1/analysis/brief')
def analysis_brief_route(request: Request):
    local_only(request)
    try:
        return services.analysis_brief_now()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
