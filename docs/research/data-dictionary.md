# Data and feature conventions

| Raw family | Columns used | Units / time convention |
|---|---|---|
| USD-M 5m klines | open/high/low/close, volume, number_of_trades, taker_buy_volume | USDT per BTC; base volume BTC; count; taker-buy base BTC |
| Mark/index 5m klines | close | USDT per BTC |
| Metrics | sum_open_interest | BTC open interest, not USDT notional |
| Metrics | count_long_short_ratio | Global account long/short ratio; dimensionless |
| Metrics | count_toptrader_long_short_ratio | Top trader account long/short ratio; dimensionless |
| Metrics | sum_toptrader_long_short_ratio | Top trader position long/short ratio; dimensionless |
| Funding archive | calc_time, last_funding_rate | Actual UTC settlement time in milliseconds; fractional rate per settlement |
| Alternative.me | timestamp, value | UTC Unix seconds; Fear & Greed 0–100 |

All numeric archive columns are parsed explicitly. Event timestamps are UTC. Five-minute bars require the expected close timestamp (open + 299,999 ms) and native five-minute grid; availability is the next boundary. Historic raw timestamps in unexpected units fail parsing/grid coverage. CSV headerless klines and the documented header format are supported.

`event_time`, `available_at`, `received_at`, and `source` remain distinct in Parquet. Historical publication delays are assumptions stated in the audit. Data availability joins are backward only, with tolerances: mark/index 10 minutes, positioning 15 minutes, settled funding 9 hours, sentiment 2 days. Missing native bars/metrics still fail coverage even when an as-of join could temporarily reuse an older observation.

The primary feature frame is indexed by completed candle cutoff. External values join at or before that cutoff. Five-minute technicals include EMA/SMA distances/slopes/crossovers, MACD/ADX; RSI/stochastic/ROC/CCI/Williams; prior UTC day/week levels, confirmed swings, pivots, channels and break/retest flags; relative volume/OBV changes/MFI/accumulation-distribution/VWAP; ATR/realized volatility/Bollinger/Keltner compression; and body/wick/range/pattern structure.

Fifteen-minute, one-hour and four-hour contexts include RSI, EMA 21/50 distance, ATR fraction, MACD and ADX. Resampling retains only complete counts (3/12/48 bars), labels at close, and forwards only completed values. Prior day/week bins also label at the closing boundary. UTC VWAP assigns a bar closing at midnight to the session it completed.

A five-bar swing candidate becomes visible two bars after its potential turning point. No centered-window feature is emitted at the candidate timestamp. ATR-normalized distances use past/current completed ranges. Rolling prior channels exclude the current bar.

Derivatives add OI/changes/price interaction, global/top ratios, prior settled funding/change, mark-index premium, taker imbalance and cumulative rolling signed taker-volume **proxy**. Sentiment adds value, 24h change, extreme states and persistence. Time/weekend/regime and cross-timeframe alignment complete the catalogue.

Zero denominators, warm-up and unavailable external inputs remain NaN. No backward fill, current-value historical substitution, fabricated positioning, tick-level CVD claim, liquidation map, order-book reconstruction or news data is used. Training eligibility requires every full-catalogue feature and forward label; group ablations use that identical index.

Inspect downloaded archive headers/units against provider definitions during the first executable audit. This dictionary states the intended contract; zero archives have been verified in this environment.


Funding interval validation allows one second of timestamp jitter around the eight-hour maximum; settlement timestamps themselves are never rounded. Missing native observations and invalid numeric observations are reported separately. An invalid latest external observation makes its prediction cutoff unavailable. Offline re-audit verifies cached raw archive checksums and unchanged Parquet hashes.
