"""All features are causal at the completed five-minute bar cutoff."""
import numpy as np
import pandas as pd
from quantpilot.data.archives import join_external

def div(a, b):
    return a / b.replace(0, np.nan)

def wilder(x, n=14):
    return x.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def technical(d):
    o, h, l, c, v = (d[k] for k in ("open", "high", "low", "close", "volume"))
    f = pd.DataFrame(index=d.index)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    f["atr"] = atr
    f["return_1"] = c.pct_change()
    for n in (3, 12, 48, 288):
        f[f"return_{n}"] = c.pct_change(n)
    for n in (9, 21, 50, 200):
        ema = c.ewm(span=n, adjust=False, min_periods=n).mean()
        f[f"ema_{n}_distance"] = div(c-ema, atr)
        f[f"ema_{n}_slope"] = div(ema.diff(3), atr)
    for n in (20, 50, 200):
        f[f"sma_{n}_distance"] = div(c-c.rolling(n).mean(), atr)
    f["ema_cross"] = np.sign(f.ema_9_distance - f.ema_21_distance)
    macd = c.ewm(span=12, adjust=False).mean()-c.ewm(span=26, adjust=False).mean()
    f["macd"] = div(macd, atr)
    f["macd_histogram"] = div(macd-macd.ewm(span=9, adjust=False).mean(), atr)
    up, down = h.diff(), -l.diff()
    plus = wilder(up.where((up > down) & (up > 0), 0))
    minus = wilder(down.where((down > up) & (down > 0), 0))
    f["adx"] = 100*wilder(div((plus-minus).abs(), plus+minus))
    delta = c.diff()
    for n in (7, 14, 21):
        f[f"rsi_{n}"] = 100-100/(1+div(wilder(delta.clip(lower=0), n), wilder(-delta.clip(upper=0), n)))
    rsi = f.rsi_14
    f["stoch_rsi"] = div(rsi-rsi.rolling(14).min(), rsi.rolling(14).max()-rsi.rolling(14).min())
    high, low = h.rolling(14).max(), l.rolling(14).min()
    f["stochastic"] = 100*div(c-low, high-low)
    f["williams_r"] = -100*div(high-c, high-low)
    f["roc"] = c.pct_change(12)
    typical = (h+l+c)/3
    mean = typical.rolling(20).mean()
    mad = typical.rolling(20).apply(lambda a: np.mean(np.abs(a-a.mean())), raw=True)
    f["cci"] = div(typical-mean, .015*mad)
    f["relative_volume"] = div(v, v.rolling(20).mean())
    f["volume_change"] = v.pct_change()
    f["volume_z"] = div(v-v.rolling(48).mean(), v.rolling(48).std())
    signed = np.sign(c.diff()).fillna(0)*v
    f["obv_change"] = div(signed.rolling(12).sum(), v.rolling(12).sum())
    money = typical*v
    f["mfi"] = 100-100/(1+div(money.where(typical.diff() > 0, 0).rolling(14).sum(),
                                  money.where(typical.diff() <= 0, 0).rolling(14).sum()))
    f["accumulation_distribution"] = div((div(2*c-h-l, h-l)*v).rolling(12).sum(), v.rolling(12).sum())
    session = (d.index-pd.Timedelta(nanoseconds=1)).floor("D")
    vwap = div((typical*v).groupby(session).cumsum(), v.groupby(session).cumsum())
    f["vwap_distance"] = div(c-vwap, atr)
    f["atr_fraction"] = div(atr, c)
    f["realized_volatility"] = np.log(c).diff().rolling(48).std()*np.sqrt(288)
    std = c.rolling(20).std()
    f["bollinger_position"] = div(c-mean, 2*std)
    f["bollinger_bandwidth"] = div(4*std, mean)
    f["keltner_position"] = div(c-c.ewm(span=20, adjust=False).mean(), 2*atr)
    f["compression"] = div(4*std, 4*atr)
    span = h-l
    f["body"] = div((c-o).abs(), span)
    f["upper_wick"] = div(h-pd.concat([o, c], axis=1).max(axis=1), span)
    f["lower_wick"] = div(pd.concat([o, c], axis=1).min(axis=1)-l, span)
    f["close_location"] = div(c-l, span)
    f["range_expansion"] = div(span, span.rolling(20).mean())
    f["inside_bar"] = ((h < h.shift()) & (l > l.shift())).astype(float)
    f["engulfing"] = (((c > o) & (c.shift() < o.shift()) & (c > o.shift()) & (o < c.shift())).astype(float)
                      - ((c < o) & (c.shift() > o.shift()) & (c < o.shift()) & (o > c.shift())).astype(float))
    for n in (20, 288):
        hi, lo = h.shift().rolling(n).max(), l.shift().rolling(n).min()
        f[f"channel_high_{n}"] = div(hi-c, atr)
        f[f"channel_low_{n}"] = div(c-lo, atr)
        f[f"breakout_{n}"] = (c > hi).astype(float)-(c < lo).astype(float)
        f[f"retest_{n}"] = ((l <= hi) & (c > hi) & (c.shift() > hi.shift())).astype(float)
    # A centered 5-bar swing is emitted two bars AFTER its candidate.
    swing_h = h.shift(2).where(h.shift(2) == h.rolling(5).max()).ffill()
    swing_l = l.shift(2).where(l.shift(2) == l.rolling(5).min()).ffill()
    f["swing_high_distance"] = div(swing_h-c, atr)
    f["swing_low_distance"] = div(c-swing_l, atr)
    for freq, label in (("1D", "day"), ("7D", "week")):
        # Fixed UTC Monday boundaries expose each period only after its closing bar.
        # Weekly calendar offsets can label all of Monday at its midnight start.
        levels = d.resample(freq, origin=pd.Timestamp("1970-01-05", tz=d.index.tz), closed="right", label="right").agg({"high":"max", "low":"min", "close":"last"})
        levels = levels.reindex(d.index, method="ffill")
        for col in ("high", "low"):
            f[f"prior_{label}_{col}"] = div(levels[col]-c, atr)
        if label == "day":
            pivot = levels.mean(axis=1)
            f["pivot_distance"] = div(c-pivot, atr)
            f["pivot_r1"] = div(2*pivot-levels.low-c, atr)
            f["pivot_s1"] = div(c-(2*pivot-levels.high), atr)
    return f.replace([np.inf, -np.inf], np.nan)

def build_features(candles, frames=None):
    joined = join_external(candles, frames or {})
    d = joined.set_index("cutoff").sort_index()
    # Reject irregular/duplicate bars: rolling windows must mean elapsed time.
    if d.index.duplicated().any() or (d.index.to_series().diff().dropna() != pd.Timedelta(minutes=5)).any():
        raise ValueError("candles must be unique, contiguous 5-minute bars; quarantine gaps before features")
    f = technical(d)
    for interval, count in (("15min", 3), ("1h", 12), ("4h", 48)):
        bars = d.resample(interval, closed="right", label="right").agg(
            {"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"})
        sizes = d.close.resample(interval, closed="right", label="right").count()
        bars = bars[sizes == count]
        context = technical(bars)[["rsi_14", "ema_21_distance", "ema_50_distance", "atr_fraction", "macd", "adx"]]
        context = context.add_prefix(interval+"_")
        f = f.join(context.reindex(f.index, method="ffill"))
    f["timeframe_alignment"] = np.sign(f["15min_ema_21_distance"])+np.sign(f["1h_ema_21_distance"])+np.sign(f["4h_ema_21_distance"])
    for col in ("open_interest", "global_ratio", "top_account_ratio", "top_position_ratio"):
        f[col] = d[col]
    f["oi_change"] = d.open_interest.pct_change(12, fill_method=None)
    f["price_oi"] = f.return_12*f.oi_change
    positioning_columns = ["open_interest", "global_ratio", "top_account_ratio", "top_position_ratio"]
    observed = pd.Series(np.isfinite(d[positioning_columns].to_numpy(dtype=float)).all(axis=1), index=d.index)
    complete_history = observed.rolling(13, min_periods=13).sum().eq(13)
    f.loc[~complete_history, positioning_columns+["oi_change", "price_oi"]] = np.nan
    f["price_volume"] = f.return_12*f.relative_volume
    f["funding_rate"] = d.funding_rate
    f["funding_change"] = d.funding_rate.diff(12)
    f["mark_index_premium"] = div(d.mark_price-d.index_price, d.index_price)
    f["taker_imbalance"] = div(2*d.taker_buy_volume-d.volume, d.volume)
    # Rolling cumulative signed taker-volume proxy, not tick-level CVD.
    f["signed_taker_volume_proxy"] = div((2*d.taker_buy_volume-d.volume).rolling(288).sum(), d.volume.rolling(288).sum())
    f["fear_greed"] = d.fear_greed
    f["sentiment_change"] = d.fear_greed.diff(288)
    f["extreme_fear"] = (d.fear_greed < 25).astype(float).where(d.fear_greed.notna())
    f["extreme_greed"] = (d.fear_greed > 75).astype(float).where(d.fear_greed.notna())
    state = (d.fear_greed < 25) | (d.fear_greed > 75)
    f["extreme_persistence"] = state.groupby((state != state.shift()).cumsum()).cumcount().where(state, 0).where(d.fear_greed.notna())
    hour = f.index.hour + f.index.minute/60
    f["hour_sin"], f["hour_cos"] = np.sin(2*np.pi*hour/24), np.cos(2*np.pi*hour/24)
    f["weekend"] = (f.index.dayofweek >= 5).astype(float)
    f["regime"] = (f.adx > 25).astype(float).where(f.adx.notna())
    # Labels are kept separate and never included in feature selection.
    forward = d.close.shift(-12)/d.close-1
    labels = pd.DataFrame({"forward_return":forward, "target":(forward > 0).astype(float).where(forward.notna())})
    return f.replace([np.inf, -np.inf], np.nan), labels, d

def feature_groups(columns):
    sentiment = [c for c in columns if c.startswith(("fear_", "sentiment_", "extreme_"))]
    derivatives = [c for c in columns if c in {"open_interest", "global_ratio", "top_account_ratio", "top_position_ratio",
                    "oi_change", "price_oi", "funding_rate", "funding_change", "mark_index_premium"}]
    tech = [c for c in columns if c not in sentiment+derivatives]
    return {"price": [c for c in columns if c.startswith("return_")],
            "technical": tech, "derivatives":tech+derivatives, "full":list(columns)}

