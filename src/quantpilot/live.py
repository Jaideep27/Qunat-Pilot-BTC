"""Public REST ingestion with pagination. No authenticated trading API calls."""
import json
from pathlib import Path
import httpx
import pandas as pd
from .data import KLINES, VALUES, load_frames, utc

API = "https://fapi.binance.com"

def _get(client, path, params=None):
    r = client.get(API+path, params=params)
    r.raise_for_status()
    return r.json()

def _bars(client, path, since, until, family):
    result, cursor = [], int(since.timestamp()*1000)
    end = int(until.timestamp()*1000)
    while cursor < end:
        rows = _get(client, path, dict(symbol="BTCUSDT", interval="5m", startTime=cursor, endTime=end, limit=1000))
        if not rows: break
        result.extend(rows)
        next_cursor = int(rows[-1][0])+300000
        if next_cursor <= cursor: raise ValueError("provider pagination did not advance")
        cursor = next_cursor
    if not result: return pd.DataFrame()
    d = pd.DataFrame(result, columns=KLINES)
    d["event_time"] = pd.to_datetime(d.open_time.astype("int64"), unit="ms", utc=True)
    d["available_at"] = d.event_time+pd.Timedelta(minutes=5)
    d = d[d.available_at <= until]
    if family != "candles": d = d.rename(columns={"close":f"{family}_price"})
    d = d[["event_time","available_at",*VALUES[family]]].copy()
    for c in VALUES[family]: d[c] = d[c].astype(float)
    d["source"] = API+path
    d["received_at"] = pd.Timestamp.now(tz="UTC")
    return d

def ingest(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    frames = load_frames(root)
    now = pd.Timestamp.now(tz="UTC")
    errors = []
    with httpx.Client(timeout=20) as client:
        for family, path in [("candles","/fapi/v1/klines"), ("mark","/fapi/v1/markPriceKlines"), ("index","/fapi/v1/indexPriceKlines")]:
            old = frames.get(family)
            since = old.event_time.max()-pd.Timedelta(minutes=5) if old is not None and len(old) else now-pd.Timedelta(days=40)
            # Index endpoint uses pair rather than symbol; Binance accepts pair parameter only.
            if family == "index":
                # A wrapper keeps the paginated contract while changing only the symbol argument.
                class IndexClient:
                    def get(self, url, params):
                        params["pair"] = params.pop("symbol")
                        return client.get(url, params=params)
                incoming = _bars(IndexClient(), path, since, now, family)
            else: incoming = _bars(client, path, since, now, family)
            frames[family] = _merge(old, incoming)
        try:
            # Require a common timestamp across all four positioning series.
            metrics = []
            for path, key, column in [
                ("/futures/data/openInterestHist","sumOpenInterest","open_interest"),
                ("/futures/data/globalLongShortAccountRatio","longShortRatio","global_ratio"),
                ("/futures/data/topLongShortAccountRatio","longShortRatio","top_account_ratio"),
                ("/futures/data/topLongShortPositionRatio","longShortRatio","top_position_ratio")]:
                rows = _get(client, path, dict(symbol="BTCUSDT", period="5m", limit=500))
                d = pd.DataFrame(rows)
                d["event_time"] = pd.to_datetime(d.timestamp.astype("int64"), unit="ms", utc=True)
                metrics.append(d[["event_time",key]].rename(columns={key:column}).set_index("event_time"))
            d = pd.concat(metrics, axis=1).reset_index().dropna()
            for c in VALUES["positioning"]: d[c] = d[c].astype(float)
            d["available_at"] = d.event_time+pd.Timedelta(minutes=5)
            d["received_at"], d["source"] = pd.Timestamp.now(tz="UTC"), API+"/futures/data/*"
            frames["positioning"] = _merge(frames.get("positioning"), d)
        except Exception as exc:
            errors.append("positioning: "+str(exc))
        old = frames.get("funding")
        since = old.event_time.max() if old is not None and len(old) else now-pd.Timedelta(days=40)
        rows = _get(client, "/fapi/v1/fundingRate", dict(symbol="BTCUSDT", startTime=int(since.timestamp()*1000), limit=1000))
        d = pd.DataFrame(rows)
        d["event_time"] = pd.to_datetime(d.fundingTime.astype("int64"), unit="ms", utc=True)
        d["available_at"] = d.event_time+pd.Timedelta(seconds=1)
        d["funding_rate"] = d.fundingRate.astype(float)
        d["received_at"], d["source"] = pd.Timestamp.now(tz="UTC"), API+"/fapi/v1/fundingRate"
        frames["funding"] = _merge(old, d[["event_time","available_at","funding_rate","received_at","source"]])
        try:
            r = client.get("https://api.alternative.me/fng/?limit=60&format=json")
            r.raise_for_status()
            d = pd.DataFrame(r.json()["data"])
            d["event_time"] = pd.to_datetime(d.timestamp.astype("int64"), unit="s", utc=True)
            d["available_at"] = d.event_time+pd.Timedelta(days=1)
            d["fear_greed"] = d.value.astype(float)
            d["received_at"], d["source"] = pd.Timestamp.now(tz="UTC"), str(r.url)
            frames["sentiment"] = _merge(frames.get("sentiment"), d[["event_time","available_at","fear_greed","received_at","source"]])
        except Exception as exc:
            errors.append("sentiment: "+str(exc))
    # Each file atomically replaced; worker reads only after ingest finishes under its lock.
    # PostgreSQL is the durable store; rolling Parquet remains a bounded ML cache.
    if settings_market_url():
        from .market_store import save_frame
        from .store import database
        engine=database(settings_market_url())
        for name, frame in frames.items(): save_frame(engine,name,frame)
    for name, d in frames.items():
        d = d[d.event_time >= now-pd.Timedelta(days=45)]
        temp = root / f"{name}.partial"
        d.to_parquet(temp, index=False)
        temp.replace(root / f"{name}.parquet")
    (root / "ingestion-status.json").write_text(json.dumps({"errors":errors,"updated_at":now.isoformat()}), encoding="utf-8")
    return frames


def settings_market_url():
    from .config import settings
    return settings.market_database_url or (settings.database_url if settings.database_url.startswith('postgresql') else None)

def _merge(old, new):
    return pd.concat([old, new] if old is not None else [new]).drop_duplicates("event_time", keep="last").sort_values("event_time").reset_index(drop=True)

def live_quote():
    with httpx.Client(timeout=10) as c:
        r = _get(c, "/fapi/v1/premiumIndex", dict(symbol="BTCUSDT"))
        ticker = _get(c, "/fapi/v2/ticker/price", dict(symbol="BTCUSDT"))
        return dict(time=utc(pd.to_datetime(ticker["time"], unit="ms", utc=True)).isoformat(), price=float(ticker["price"]),
                    mark=float(r["markPrice"]), source=API+"/fapi/v1/premiumIndex")

