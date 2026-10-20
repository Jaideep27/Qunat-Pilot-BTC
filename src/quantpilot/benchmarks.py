"""Passive, fully collateralized long comparator using the shared accounting reducer."""
from .config import RiskConfig
from .data import utc
from .engine import initial, equity, market_events, reduce_event, close_position, performance


def buy_and_hold(candles, funding=None, config=None):
    if candles.empty:
        raise ValueError("empty benchmark window")
    candles = candles.sort_values("available_at")
    costs = config or RiskConfig()
    # This comparator holds until the window ends; active-policy time/stop exits do not apply.
    span_minutes = int((utc(candles.available_at.iloc[-1])-utc(candles.available_at.iloc[0])).total_seconds()/60)+1
    c = costs.model_copy(update={"max_hold_minutes": max(costs.max_hold_minutes, span_minutes)})
    s = initial(c)
    first = candles.iloc[0]
    entered = utc(first.available_at)
    entry = float(first.close)*(1+c.slippage_bps/10000)
    quantity = c.starting_balance/(entry*(1+c.fee_bps/10000))
    fee = quantity*entry*c.fee_bps/10000
    s.update(cash=c.starting_balance-fee, fees=fee, mark=float(first.get("mark_price", first.close)),
             day=entered.strftime("%Y-%m-%d"), last_event=entered.isoformat())
    s["position"] = dict(side=1, quantity=quantity, entry=entry, stop=0.0, collateral=quantity*entry,
                         entry_time=entered.isoformat(), entry_fee=fee, funding=0.0,
                         model_version="passive-benchmark", config_version=c.identity)
    s["fills"].append(dict(time=entered.isoformat(), action="ENTRY", side=1, quantity=quantity,
                            price=entry, fee=fee, pnl=0.0, replayed=False))
    s["equity"] = equity(s)
    s["curve"].append(dict(time=entered.isoformat(), equity=s["equity"], exposure=quantity*s["mark"]/s["equity"]))
    for event in market_events(candles, funding):
        # A purchase immediately after the first close is not held for that close's settlement.
        if utc(event["time"]) > entered:
            s = reduce_event(s, event, c, inplace=True)
    if s["position"]:
        last = candles.iloc[-1]
        close_position(s, float(last.close), utc(last.available_at), "BENCHMARK_END", c)
        s["equity"] = equity(s)
        s["curve"][-1]["equity"] = s["equity"]
    curve = s["curve"][::12]
    if s["curve"] and (not curve or curve[-1] != s["curve"][-1]):
        curve.append(s["curve"][-1])
    return {**performance(s, c.starting_balance), "curve":curve, "fills":s["fills"],
            "assumptions":{"allocation":"100% initial equity including entry fee", "holding":"until window end",
                           "funding":"actual settlement timestamps after entry", "fee_bps":c.fee_bps,
                           "slippage_bps":c.slippage_bps}}

