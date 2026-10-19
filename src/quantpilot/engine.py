"""One event reducer for backtests and forward paper. No exchange order endpoints."""
from __future__ import annotations
import copy
import math
import pandas as pd
from pydantic import BaseModel, Field, ConfigDict
from .config import RiskConfig
from .data import utc

EVENT_PRIORITY = {"funding":0, "bar":1, "quote":2, "signal":3}

class Forecast(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    probability: float = Field(ge=0, le=1)
    up_magnitude: float = Field(gt=0)
    down_magnitude: float = Field(gt=0)
    atr: float = Field(gt=0)
    observed_at: str
    model_version: str
    complete: bool = True
    funding_rate: float = 0
    funding_observable: bool = False
    threshold: float = Field(.60, ge=.5, lt=1)

def initial(config=None):
    c = config or RiskConfig()
    return dict(cash=c.starting_balance, equity=c.starting_balance, mark=None, position=None,
                pending=None, day=None, day_start=c.starting_balance, halted=False, paused=False,
                last_event=None, last_worker=None, last_control_at=None, config=c.model_dump(), fills=[], funding=[],
                decisions=[], curve=[], realized_pnl=0.0, fees=0.0, funding_total=0.0)

def equity(s, mark=None):
    p = s["position"]
    mark = mark or s["mark"]
    return s["cash"] + (p["side"]*p["quantity"]*(mark-p["entry"]) if p and mark else 0)

def gate(s, forecast, price, now, config):
    reasons = []
    try:
        f = Forecast.model_validate(forecast)
        age = (utc(now)-utc(f.observed_at)).total_seconds()
    except Exception:
        return dict(action="NO_TRADE", reasons=["INVALID_FORECAST"], expected_edge=None)
    if not f.complete or not f.funding_observable: reasons.append("MISSING_CRITICAL_INPUT")
    if age < 0 or age > config.max_age_seconds: reasons.append("STALE_DATA")
    if s["paused"]: reasons.append("PAUSED")
    if s["halted"] or equity(s, price) <= s["day_start"]*(1-config.daily_loss): reasons.append("DAILY_LOSS_LIMIT")
    if s["position"]: reasons.append("POSITION_OPEN")
    side = 1 if f.probability >= .5 else -1
    confidence = max(f.probability, 1-f.probability)
    if confidence < max(config.threshold, f.threshold): reasons.append("WEAK_FORECAST")
    expected = side*(f.probability*f.up_magnitude-(1-f.probability)*f.down_magnitude)
    costs = 2*(config.fee_bps+config.slippage_bps)/10000
    # Conservatively budget latest observable settlement rate even if settlement is not imminent.
    funding_cost = max(0, side*f.funding_rate)
    if expected <= costs+funding_cost+config.buffer_bps/10000: reasons.append("INSUFFICIENT_EDGE")
    return dict(action="NO_TRADE" if reasons else ("LONG" if side == 1 else "SHORT"),
                reasons=reasons, expected_edge=expected, cost_budget=costs+funding_cost,
                probability=f.probability, model_version=f.model_version)

def close_position(s, price, time, reason, c, replayed=False):
    p = s["position"]
    if not p: return
    fill = price*(1-p["side"]*c.slippage_bps/10000)
    fee = p["quantity"]*fill*c.fee_bps/10000
    raw_pnl = p["side"]*p["quantity"]*(fill-p["entry"])
    pnl = raw_pnl
    floor = -p["collateral"]-p.get("funding", 0)
    if reason == "MARGIN_APPROXIMATION" or pnl < floor:
        # Simplified isolated bankruptcy cap: free collateral cannot be borrowed to cover a gap.
        reason = "MARGIN_APPROXIMATION"
        pnl = max(pnl, floor)
    s["cash"] += pnl-fee
    s["realized_pnl"] += pnl
    s["fees"] += fee
    s["fills"].append(dict(time=str(time), action="EXIT", side=p["side"], quantity=p["quantity"],
                           price=fill, fee=fee, pnl=pnl, raw_pnl=raw_pnl, margin_adjustment=pnl-raw_pnl,
                           reason=reason, replayed=replayed,
                           entry_fee=p["entry_fee"], funding=p.get("funding", 0),
                           model_version=p.get("model_version"), config_version=c.identity))
    s["position"] = None

def reduce_event(state, event, config=None, inplace=False):
    """Pure reducer. Caller guarantees unique monotonically ordered event keys."""
    s = state if inplace else copy.deepcopy(state)
    c = config or RiskConfig.model_validate(s["config"])
    now, kind = utc(event["time"]), event["kind"]
    replayed = bool(event.get("replayed", False))
    if kind == "control":
        # Owner controls must not move the chronological market reconciliation cursor.
        previous = s.get("last_control_at")
        if previous and now < utc(previous):
            raise ValueError("out-of-order owner control")
        s["paused"] = bool(event["paused"])
        s["last_control_at"] = now.isoformat()
        if s["paused"]:
            s["pending"] = None
        return s
    day = now.strftime("%Y-%m-%d")
    if s["day"] != day:
        s.update(day=day, day_start=equity(s), halted=False)
    if kind == "funding":
        if not math.isfinite(float(event["rate"])) or not math.isfinite(float(event["mark"])) or float(event["mark"]) <= 0:
            raise ValueError("invalid funding observation")
        p = s["position"]
        if p:
            amount = -p["side"]*p["quantity"]*float(event["mark"])*float(event["rate"])
            s["cash"] += amount
            s["funding_total"] += amount
            p["funding"] = p.get("funding", 0)+amount
            s["funding"].append(dict(time=now.isoformat(), amount=amount, rate=event["rate"], replayed=replayed))
    elif kind in ("bar", "quote"):
        price = float(event["price"])
        if not math.isfinite(price) or price <= 0: raise ValueError("invalid price")
        s["mark"] = float(event.get("mark", price))
        if not math.isfinite(s["mark"]) or s["mark"] <= 0: raise ValueError("invalid mark")
        if kind == "bar" and (not all(math.isfinite(float(event[k])) and float(event[k]) > 0 for k in ("open","low","high")) or event["low"] > event["high"]):
            raise ValueError("invalid OHLC")
        p = s["position"]
        if p:
            # Margin is fully collateralized entry notional. Approximate isolated liquidation.
            margin_equity = p["collateral"]+p["side"]*p["quantity"]*(s["mark"]-p["entry"])+p.get("funding", 0)
            if margin_equity <= p["quantity"]*s["mark"]*c.maintenance_margin:
                close_position(s, s["mark"], now, "MARGIN_APPROXIMATION", c, replayed)
            elif kind == "bar":
                hit = (p["side"] == 1 and float(event["low"]) <= p["stop"]) or (p["side"] == -1 and float(event["high"]) >= p["stop"])
                if hit:
                    # Gap-aware adverse fill; same-bar unknown paths conservatively hit stop.
                    stop_fill = min(p["stop"], event["open"]) if p["side"] == 1 else max(p["stop"], event["open"])
                    close_position(s, stop_fill, now, "STOP", c, replayed)
            if s["position"] and now >= utc(p["entry_time"])+pd.Timedelta(minutes=c.max_hold_minutes):
                close_position(s, price, now, "TIME_EXIT", c, replayed)
        if kind == "quote" and s["pending"]:
            pending = s["pending"]
            if now >= utc(pending["time"])+pd.Timedelta(seconds=c.execution_delay_seconds):
                s["pending"] = None
                if replayed:
                    s["decisions"].append(dict(time=now.isoformat(), action="NO_TRADE", reasons=["REPLAY_ENTRY_FORBIDDEN"], replayed=True))
                else:
                    decision = gate(s, pending["forecast"], price, now, c)
                    decision.update(time=now.isoformat(), replayed=False, config_version=c.identity)
                    s["decisions"].append(decision)
                    if decision["action"] != "NO_TRADE":
                        f = pending["forecast"]
                        side = 1 if decision["action"] == "LONG" else -1
                        fill = price*(1+side*c.slippage_bps/10000)
                        stop_distance = c.stop_atr*f["atr"]
                        eq = equity(s, price)
                        unit_risk = stop_distance + fill*2*(c.fee_bps+c.slippage_bps)/10000
                        qty = min(eq*c.risk_budget/unit_risk, eq*c.max_exposure/fill)
                        fee = qty*fill*c.fee_bps/10000
                        if qty <= 0 or qty*fill > eq*c.max_exposure+1e-8:
                            decision.update(action="NO_TRADE", reasons=["EXPOSURE_LIMIT"])
                        else:
                            s["cash"] -= fee
                            s["fees"] += fee
                            s["position"] = dict(side=side, quantity=qty, entry=fill, stop=fill-side*stop_distance,
                                                 collateral=qty*fill, entry_time=now.isoformat(), entry_fee=fee, funding=0, model_version=f["model_version"], config_version=c.identity)
                            s["fills"].append(dict(time=now.isoformat(), action="ENTRY", side=side,
                                                   quantity=qty, price=fill, fee=fee, pnl=0, replayed=False, model_version=f["model_version"], config_version=c.identity))
    elif kind == "signal":
        f = event["forecast"]
        decision = gate(s, f, s["mark"] or event.get("price", 0), now, c)
        decision.update(time=now.isoformat(), config_version=c.identity, replayed=replayed)
        s["decisions"].append(decision)
        if decision["action"] != "NO_TRADE" and not replayed:
            s["pending"] = dict(time=now.isoformat(), forecast=f)
    elif kind == "checkpoint":
        s["last_worker"] = now.isoformat()
    else:
        raise ValueError(f"unknown event kind: {kind}")
    s["equity"] = equity(s)
    if s["equity"] <= s["day_start"]*(1-c.daily_loss):
        s["halted"] = True
        s["pending"] = None
    if kind == "bar":
        s["curve"].append(dict(time=now.isoformat(), equity=s["equity"], exposure=(
            s["position"]["quantity"]*s["mark"]/max(s["equity"], 1e-9) if s["position"] else 0)))
    s["last_event"] = now.isoformat()
    return s

def market_events(candles, funding=None, replayed=False):
    events = []
    if candles.empty:
        return events
    range_start = utc(candles.event_time.min())
    range_end = utc(candles.available_at.max())
    last_mark = None
    for row in candles.itertuples():
        start, end = utc(row.event_time), utc(row.available_at)
        mark = getattr(row, "mark_price", row.close)
        events.extend([
            dict(id=f"quote:{start.isoformat()}", time=start.isoformat(), kind="quote", price=row.open,
                 mark=last_mark if last_mark is not None else row.open, replayed=replayed),
            dict(id=f"bar:{end.isoformat()}", time=end.isoformat(), kind="bar", price=row.close, mark=mark,
                 open=row.open, low=row.low, high=row.high, replayed=replayed)])
        last_mark = mark
    if funding is not None:
        # Funding mark must be the last observable mark at the settlement timestamp.
        for row in funding.itertuples():
            # Never charge settlements from outside this evaluation/reconciliation window.
            if not range_start <= utc(row.event_time) <= range_end:
                continue
            prior = candles[candles.available_at <= row.event_time]
            if prior.empty: continue
            mark = prior.iloc[-1].get("mark_price", prior.iloc[-1].close)
            events.append(dict(id=f"funding:{row.event_time.isoformat()}", time=row.event_time.isoformat(),
                               kind="funding", rate=row.funding_rate, mark=float(mark), replayed=replayed))
    return sorted(events, key=lambda e: (utc(e["time"]), EVENT_PRIORITY[e["kind"]]))

def backtest(candles, forecasts, funding=None, config=None):
    c = config or RiskConfig()
    events = market_events(candles, funding)
    for t, f in forecasts.items():
        events.append(dict(id=f"signal:{t}", time=str(t), kind="signal", forecast=f))
    events.sort(key=lambda e: (utc(e["time"]), EVENT_PRIORITY[e["kind"]]))
    s = initial(c)
    full_curve, full_fills = [], []
    for event in events:
        before = len(s["fills"])
        s = reduce_event(s, event, c, inplace=True)
        if len(s["fills"]) > before: full_fills.extend(s["fills"][before:])
        if event["kind"] == "bar": full_curve.append(s["curve"][-1])
    if s["position"]:
        before = len(s["fills"])
        close_position(s, s["mark"], s["last_event"], "BACKTEST_END", c)
        full_fills.extend(s["fills"][before:])
        s["equity"] = equity(s)
        if full_curve: full_curve[-1]["equity"] = s["equity"]
    s["curve"], s["fills"] = full_curve, full_fills
    return s

def performance(s, starting=10000):
    curve = pd.Series([r["equity"] for r in s["curve"]], dtype=float)
    if len(curve):
        peak = curve.cummax().clip(lower=starting)
        drawdown = float((curve/peak-1).min())
    else: drawdown = 0
    exits = [f["pnl"]-f["fee"]-f.get("entry_fee", 0)+f.get("funding", 0) for f in s["fills"] if f["action"] == "EXIT"]
    gains, losses = sum(max(x, 0) for x in exits), -sum(min(x, 0) for x in exits)
    return dict(net_return=s["equity"]/starting-1, max_drawdown=drawdown,
                profit_factor=gains/losses if losses else None,
                exposure=float(np_mean([r["exposure"] for r in s["curve"]])),
                turnover=sum(f["quantity"]*f["price"] for f in s["fills"])/starting,
                trades=len(exits), fees=s["fees"], funding=s["funding_total"])

def np_mean(values):
    return sum(values)/len(values) if values else 0

