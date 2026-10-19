from contextlib import asynccontextmanager
from typing import Annotated, Literal
from fastapi import FastAPI, Query, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware
from . import services
from .config import settings
from .store import database, initialize

@asynccontextmanager
async def lifespan(app):
    initialize(database())
    if settings.market_database_url:initialize(database(settings.market_database_url))
    yield

app = FastAPI(title="QuantPilot BTC", version="0.1.0", lifespan=lifespan,
              description="Read-only BTC perpetual research and paper ledger. No real-money execution.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET"], allow_headers=["*"])

@app.get("/api/v1/health")
def health(): return services.health()

@app.get('/api/v1/operations')
def operational_status(limit: Annotated[int,Query(ge=1,le=100)]=20):
    try:return services.operations(limit)
    except SQLAlchemyError:raise HTTPException(status_code=503,detail='Operational database unavailable') from None

@app.get('/api/v1/ready')
def readiness():
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    try:result=services.operations(1)
    except SQLAlchemyError:raise HTTPException(status_code=503,detail='Operational database unavailable') from None
    return JSONResponse(jsonable_encoder(result),status_code=200 if result['data']['ready'] else 503)

@app.get("/api/v1/candles")
def candles(limit: Annotated[int, Query(ge=1, le=2000)] = 288): return services.candles(limit)

@app.get("/api/v1/indicators")
def indicators(): return services.indicators()

@app.get("/api/v1/positioning")
def positioning(): return services.family("positioning")

@app.get("/api/v1/sentiment")
def sentiment(): return services.family("sentiment")

@app.get('/api/v1/news')
def news(limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return services.news(limit)

@app.get('/api/v1/macro')
def macro(): return services.macro()

@app.get("/api/v1/coverage")
def coverage(): return services.coverage()

@app.get("/api/v1/predictions")
def predictions(): return services.prediction()

@app.get("/api/v1/backtests")
def backtests(): return services.research()

@app.get("/api/v1/portfolio")
def portfolio(): return services.paper()

@app.get("/api/v1/decisions")
def decisions(index: Annotated[int, Query(ge=0, le=1999)] = 0): return services.explain_decision(index)

@app.get('/api/v1/market/storage')
def market_storage():
    from .market_store import summary
    return services.envelope(summary(database(settings.market_database_url)),source='Persistent market database')

@app.get('/api/v1/market/candles')
def chart_candles(market: Literal['BTCUSDT']='BTCUSDT',
                  timeframe: Literal['5m','15m','30m','1h','4h','1d','1w']='5m',
                  limit: Annotated[int,Query(ge=1,le=5000)]=1000,
                  forming: bool=False,
                  before: Annotated[int|None,Query(ge=0,le=4102444800000)]=None):
    import pandas as pd
    from .market_store import candles
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
    from .market_store import candles
    from .charting import studies
    rows=candles(database(settings.market_database_url),market,timeframe,min(5000,limit+300),
                 pd.to_datetime(before,unit='ms',utc=True) if before is not None else None)
    result=studies(rows,selected.split(',')[:12])
    return services.envelope(result,rows[-1]['available_at'] if rows else None,'Causal chart study catalogue')

