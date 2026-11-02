"""The indicator library: what each indicator is, how to read it, and the zones that turn a number into words.

Each entry:
  id, name, group, scope ('timeframe' = computed on 15m/1h/4h/1d bars; 'market' = one live reading),
  settings, what (plain definition), how (how to read it), unit (display), scale (zone-bar range),
  zones: [upper bound or None, label, tone, sentence] in ascending order, study (chart study ids or None).

Tones: up / down (directional), hot / cold (stretched up / down), neutral, quiet / busy / warn (activity).
"""
from __future__ import annotations

GROUPS = ('Trend', 'Momentum', 'Volatility', 'Volume & flow', 'Levels', 'Positioning')
TIMEFRAMES = ('15m', '1h', '4h', '1d')
UP, DOWN = ('up', 'hot'), ('down', 'cold')


def _z(*rows):
    return [list(r) for r in rows]


def _stretch(lo_lo, lo, hi, hi_hi, names, sentences):
    """Five-zone oscillator: stretched down, weak, neutral, strong, stretched up."""
    return _z((lo_lo, names[0], 'cold', sentences[0]), (lo, names[1], 'down', sentences[1]),
              (hi, names[2], 'neutral', sentences[2]), (hi_hi, names[3], 'up', sentences[3]),
              (None, names[4], 'hot', sentences[4]))


def _four(lo, mid, hi, names, sentences):
    """Four-zone oscillator: stretched down, lower half, upper half, stretched up."""
    return _z((lo, names[0], 'cold', sentences[0]), (mid, names[1], 'down', sentences[1]),
              (hi, names[2], 'up', sentences[2]), (None, names[3], 'hot', sentences[3]))


_DIST = ['Far below', 'Below', 'At it', 'Above', 'Far above']


def _distance(average):
    return _stretch(-1.0, -0.25, 0.25, 1.0, _DIST, [
        f'Price is well under the {average}: sellers are clearly in charge on this chart.',
        f'Price sits under the {average}: a mild downward lean.',
        f'Price is hugging the {average}: no lean either way.',
        f'Price sits over the {average}: a mild upward lean.',
        f'Price is well over the {average}: buyers are clearly in charge, and price is stretched.'])


INDICATORS = [
    # ---- Trend ---------------------------------------------------------------------------------------------
    dict(id='ema_stack', name='EMA stack (9/21/50/200)', group='Trend', scope='timeframe', unit='state', scale=[-1, 1],
         settings='Exponential moving averages of 9, 21, 50 and 200 closes',
         what='Four moving averages from fast to slow. When they line up in order, the trend is clean.',
         how='Up = 9 above 21 above 50, and price above the 200. Down = the mirror. Anything else is mixed.',
         zones=_z((-0.5, 'Stacked down', 'down', 'Fast, medium and slow averages are lined up downward: a clean downtrend.'),
                  (0.5, 'Mixed', 'neutral', 'The averages are tangled: no clean trend on this chart.'),
                  (None, 'Stacked up', 'up', 'Fast, medium and slow averages are lined up upward: a clean uptrend.')),
         study=['EMA 9', 'EMA 21', 'EMA 50', 'EMA 200']),
    dict(id='ema_21_distance', name='Price vs EMA 21', group='Trend', scope='timeframe', unit='ATR', scale=[-2, 2],
         settings='Distance from the 21-bar EMA, in ATRs', what='How far price is from its short-term average.',
         how='Measured in ATRs (typical bar sizes), so it means the same in calm and wild markets.',
         zones=_distance('21-bar average'), study=['EMA 21']),
    dict(id='sma_50_distance', name='Price vs SMA 50', group='Trend', scope='timeframe', unit='ATR', scale=[-3, 3],
         settings='Distance from the 50-bar simple average, in ATRs', what='How far price is from its medium-term average.',
         how='Above it = medium-term uptrend; below = downtrend. Far away means stretched.',
         zones=_distance('50-bar average'), study=['SMA 50']),
    dict(id='ema_200_distance', name='Price vs EMA 200', group='Trend', scope='timeframe', unit='ATR', scale=[-4, 4],
         settings='Distance from the 200-bar EMA, in ATRs', what='Where price sits against its long-term average.',
         how='The classic dividing line: above it is a bull phase on this chart, below it a bear phase.',
         zones=_distance('200-bar average'), study=['EMA 200']),
    dict(id='adx', name='ADX 14 (trend strength)', group='Trend', scope='timeframe', unit='', scale=[0, 60],
         settings='Average Directional Index, 14 bars (Wilder)', what='How strong the trend is, whichever way it points.',
         how='Below 20 = no real trend (sideways). 25 and above = trending. Above 40 = very strong trend.',
         zones=_z((20, 'No trend', 'quiet', 'Trend strength is low: the market is moving sideways on this chart.'),
                  (25, 'Forming', 'neutral', 'A trend may be forming but is not established yet.'),
                  (40, 'Trending', 'busy', 'A real trend is in place on this chart.'),
                  (None, 'Very strong', 'warn', 'A very strong trend: these often run further than expected, then exhaust.')),
         study=['adx']),
    # ---- Momentum ------------------------------------------------------------------------------------------
    dict(id='rsi_14', name='RSI 14', group='Momentum', scope='timeframe', unit='', scale=[0, 100],
         settings='Relative Strength Index, 14 bars (Wilder)', what='Compares recent up-moves with down-moves, on a 0–100 scale.',
         how='Below 30 = oversold, above 70 = overbought, 45–55 = balanced. In strong trends it can stay extreme.',
         zones=_stretch(30, 45, 55, 70, ['Oversold', 'Weak', 'Neutral', 'Strong', 'Overbought'], [
             'Sellers have pushed hard: price is oversold and often due a pause or bounce.',
             'Down-moves have been beating up-moves recently.',
             'Up- and down-moves are balanced.',
             'Up-moves have been beating down-moves recently.',
             'Buyers have pushed hard: price is overbought and often due a pause or pullback.']),
         study=['rsi_14']),
    dict(id='macd_histogram', name='MACD histogram', group='Momentum', scope='timeframe', unit='ATR', scale=[-1, 1],
         settings='MACD (12, 26) minus its 9-bar signal, in ATRs', what='Whether momentum is speeding up or slowing down.',
         how='Above zero = upward momentum growing; below zero = downward momentum growing.',
         zones=_z((-0.1, 'Falling', 'down', 'Downward momentum is building.'),
                  (0.1, 'Flat', 'neutral', 'Momentum is flat.'),
                  (None, 'Rising', 'up', 'Upward momentum is building.')),
         study=['MACD line', 'MACD signal', 'MACD histogram']),
    dict(id='stochastic', name='Stochastic %K (14)', group='Momentum', scope='timeframe', unit='', scale=[0, 100],
         settings='Close within the last 14 bars’ high-low range', what='Where the close sits inside the recent range.',
         how='Near 0 = closing at the lows (oversold); near 100 = closing at the highs (overbought).',
         zones=_four(20, 50, 80, ['Oversold', 'Lower half', 'Upper half', 'Overbought'], [
             'Closing near the bottom of the recent range.', 'Closing in the lower half of the recent range.',
             'Closing in the upper half of the recent range.', 'Closing near the top of the recent range.']),
         study=['stochastic']),
    dict(id='stoch_rsi', name='Stochastic RSI', group='Momentum', scope='timeframe', unit='', scale=[0, 1],
         settings='RSI 14 within its own 14-bar range (0–1)', what='A faster, twitchier version of RSI.',
         how='Below 0.2 = oversold, above 0.8 = overbought. It flips often, so it is a timing hint, not a trend signal.',
         zones=_four(0.2, 0.5, 0.8, ['Oversold', 'Lower half', 'Upper half', 'Overbought'], [
             'RSI is at the bottom of its recent range.', 'RSI is in the lower half of its recent range.',
             'RSI is in the upper half of its recent range.', 'RSI is at the top of its recent range.']),
         study=['stoch_rsi']),
    dict(id='williams_r', name='Williams %R (14)', group='Momentum', scope='timeframe', unit='', scale=[-100, 0],
         settings='Close vs the 14-bar high, from 0 to −100', what='The stochastic turned upside down.',
         how='Below −80 = oversold; above −20 = overbought.',
         zones=_four(-80, -50, -20, ['Oversold', 'Lower half', 'Upper half', 'Overbought'], [
             'Closing near the recent lows.', 'Closing in the lower half of the recent range.',
             'Closing in the upper half of the recent range.', 'Closing near the recent highs.']),
         study=['williams_r']),
    dict(id='cci', name='CCI 20', group='Momentum', scope='timeframe', unit='', scale=[-250, 250],
         settings='Commodity Channel Index, 20 bars', what='How far price is from its average, against its usual spread.',
         how='Above +100 = unusually strong; below −100 = unusually weak.',
         zones=_four(-100, 0, 100, ['Very weak', 'Below average', 'Above average', 'Very strong'], [
             'Price is unusually far below its average.', 'Price is below its average.',
             'Price is above its average.', 'Price is unusually far above its average.']),
         study=['cci']),
    # ---- Volatility ----------------------------------------------------------------------------------------
    dict(id='atr_percentile', name='ATR 14 (volatility)', group='Volatility', scope='timeframe', unit='pct', scale=[0, 100],
         settings='Average True Range, 14 bars, ranked against about the last 30 days',
         what='The typical size of one bar. The ranking shows if today is calm or wild compared with recent weeks.',
         how='Low ranking = quiet (often before a big move). High ranking = wild (wider swings both ways).',
         zones=_z((20, 'Quiet', 'quiet', 'Bars are unusually small: a quiet, compressed market.'),
                  (80, 'Normal', 'neutral', 'Volatility is normal for recent weeks.'),
                  (95, 'High', 'busy', 'Bars are unusually large: expect wider swings.'),
                  (None, 'Extreme', 'warn', 'Volatility is extreme: moves can be violent in both directions.')),
         study=['atr']),
    dict(id='bollinger_position', name='Bollinger position (20, 2)', group='Volatility', scope='timeframe', unit='', scale=[-1.5, 1.5],
         settings='Close vs the 20-bar average, in units of 2 standard deviations',
         what='Where price sits inside the Bollinger Bands.',
         how='−1 = on the lower band, +1 = on the upper band. Outside the bands = a stretched move.',
         zones=_stretch(-1, -0.5, 0.5, 1, ['Below lower band', 'Near lower band', 'Middle', 'Near upper band', 'Above upper band'], [
             'Price has pushed below the lower band: a stretched drop.', 'Price is near the lower band.',
             'Price is in the middle of the bands.', 'Price is near the upper band.',
             'Price has pushed above the upper band: a stretched rise.']),
         study=['Bollinger upper', 'Bollinger middle', 'Bollinger lower']),
    dict(id='squeeze', name='Squeeze (Bollinger inside Keltner)', group='Volatility', scope='timeframe', unit='state', scale=[0, 1],
         settings='Bollinger (20, 2 SD) inside Keltner (20 EMA ± 1.5 ATR)',
         what='A squeeze means volatility has compressed unusually tightly.',
         how='Squeezes often end with a sharp move. The squeeze does not tell you which way.',
         zones=_z((0.5, 'No squeeze', 'neutral', 'Volatility is not compressed.'),
                  (None, 'Squeeze on', 'quiet', 'Volatility is tightly compressed: a bigger move often follows, direction unknown.')),
         study=['Bollinger upper', 'Bollinger lower', 'Keltner upper', 'Keltner lower']),
    dict(id='keltner_position', name='Keltner position', group='Volatility', scope='timeframe', unit='', scale=[-1.5, 1.5],
         settings='Close vs the 20-bar EMA, in units of 2 ATRs', what='Where price sits inside the Keltner Channel.',
         how='±1 = on the channel edge. Outside = a strong, stretched move.',
         zones=_stretch(-1, -0.5, 0.5, 1, ['Below channel', 'Lower part', 'Middle', 'Upper part', 'Above channel'], [
             'Price is below the Keltner Channel: a strong drop.', 'Price is in the lower part of the channel.',
             'Price is in the middle of the channel.', 'Price is in the upper part of the channel.',
             'Price is above the Keltner Channel: a strong rise.']),
         study=['Keltner upper', 'Keltner lower']),
    # ---- Volume & flow -------------------------------------------------------------------------------------
    dict(id='relative_volume', name='Relative volume', group='Volume & flow', scope='timeframe', unit='x', scale=[0, 3],
         settings='Last bar’s volume vs the average of the last 20 bars', what='Is the market busier or quieter than usual?',
         how='Moves on high volume are more convincing; moves on low volume are easier to reverse.',
         zones=_z((0.7, 'Quiet', 'quiet', 'Trading is quieter than usual.'),
                  (1.3, 'Normal', 'neutral', 'Trading activity is normal.'),
                  (2.0, 'Busy', 'busy', 'Trading is busier than usual.'),
                  (None, 'Surge', 'warn', 'Volume is surging: something is happening.')),
         study=['relative_volume']),
    dict(id='taker_share', name='Aggressive buyers’ share', group='Volume & flow', scope='timeframe', unit='%', scale=[0.3, 0.7],
         settings='Share of the last bar’s volume from traders buying at market', what='Who is more eager: buyers or sellers?',
         how='Above 55% = buyers are pushing; below 45% = sellers are pushing.',
         zones=_z((0.45, 'Sellers pushing', 'down', 'Aggressive sellers outweighed aggressive buyers.'),
                  (0.55, 'Balanced', 'neutral', 'Aggressive buyers and sellers were balanced.'),
                  (None, 'Buyers pushing', 'up', 'Aggressive buyers outweighed aggressive sellers.')),
         study=['Taker buy imbalance']),
    dict(id='mfi', name='Money Flow Index 14', group='Volume & flow', scope='timeframe', unit='', scale=[0, 100],
         settings='Volume-weighted RSI, 14 bars', what='Like RSI, but bigger-volume bars count more.',
         how='Below 20 = money flowing out hard (oversold); above 80 = flowing in hard (overbought).',
         zones=_four(20, 50, 80, ['Outflow extreme', 'Outflow', 'Inflow', 'Inflow extreme'], [
             'Money has been flowing out hard.', 'More money flowing out than in.',
             'More money flowing in than out.', 'Money has been flowing in hard.']),
         study=['mfi']),
    dict(id='obv_change', name='On-balance volume (12 bars)', group='Volume & flow', scope='timeframe', unit='', scale=[-1, 1],
         settings='Volume on up-bars minus volume on down-bars, last 12 bars, as a share of volume',
         what='Is volume arriving on rising bars or on falling bars?',
         how='Positive = volume backs the rises; negative = volume backs the drops.',
         zones=_z((-0.2, 'Volume on drops', 'down', 'Most recent volume came on falling bars.'),
                  (0.2, 'Mixed', 'neutral', 'Volume is split between rising and falling bars.'),
                  (None, 'Volume on rises', 'up', 'Most recent volume came on rising bars.')),
         study=['OBV']),
    # ---- Levels (one live reading) -------------------------------------------------------------------------
    dict(id='range_pos_4h', name='Position in the 4h likely range', group='Levels', scope='market', unit='', scale=[0, 1],
         settings='Price inside the 4-hour 80% range (EWMA volatility, from the 4h block’s open)',
         what='Where price sits inside the range BTC is expected to stay in 8 times out of 10 this 4-hour block.',
         how='0 = bottom edge, 1 = top edge. This range forecast is the one forecasting skill that tested well.',
         zones=_stretch(0.1, 0.35, 0.65, 0.9, ['At the bottom', 'Lower part', 'Middle', 'Upper part', 'At the top'], [
             'Price is at the bottom edge of its likely 4-hour range.', 'Price is in the lower part of its likely range.',
             'Price is in the middle of its likely range.', 'Price is in the upper part of its likely range.',
             'Price is at the top edge of its likely 4-hour range.']),
         study=None),
    dict(id='vwap_z', name='Price vs VWAP', group='Levels', scope='market', unit='SD', scale=[-3, 3],
         settings='Distance from today’s volume-weighted average price, in standard deviations',
         what='VWAP is the average price everyone paid today, weighted by volume.',
         how='Above VWAP = today’s buyers are in profit. ±2 SD = stretched far from the day’s fair price.',
         zones=_stretch(-2, -0.5, 0.5, 2, ['Far below', 'Below', 'At VWAP', 'Above', 'Far above'], [
             'Price is stretched far below today’s average price.', 'Price is below today’s average price.',
             'Price is at today’s average price.', 'Price is above today’s average price.',
             'Price is stretched far above today’s average price.']),
         study=['Session VWAP']),
    # ---- Positioning (one live reading) --------------------------------------------------------------------
    dict(id='funding_annual', name='Funding rate (per year)', group='Positioning', scope='market', unit='%yr', scale=[-0.2, 0.4],
         settings='Last 8-hour funding × 3 × 365', what='What traders holding futures pay each other every 8 hours.',
         how='Positive = buyers pay sellers (more demand to be long). Very high = crowded longs; negative = crowded shorts.',
         zones=_z((0.0, 'Shorts paying', 'cold', 'Sellers are paying buyers: the crowd leans short.'),
                  (0.12, 'Normal', 'neutral', 'Funding is normal.'),
                  (0.3, 'Elevated', 'up', 'Buyers are paying more than usual: the crowd leans long.'),
                  (None, 'Crowded longs', 'hot', 'Buyers are paying a lot: longs are crowded and vulnerable to a flush.')),
         study=None),
    dict(id='funding_z', name='Funding vs normal', group='Positioning', scope='market', unit='SD', scale=[-3, 3],
         settings='Latest funding against the previous 90 settlements (30 days)', what='Is funding unusual right now?',
         how='Beyond ±2 = an extreme that the rulebook’s crowding setups watch for.',
         zones=_stretch(-2.0, -1.0, 1.0, 2.2, ['Extreme short', 'Leaning short', 'Normal', 'Leaning long', 'Extreme long'], [
             'Funding is extremely negative: shorts are crowded.', 'Funding is lower than usual.', 'Funding is normal.',
             'Funding is higher than usual.', 'Funding is extremely positive: longs are crowded.']),
         study=None),
    dict(id='oi_change_24h', name='Open interest, 24h change', group='Positioning', scope='market', unit='%', scale=[-0.1, 0.1],
         settings='Change in total open futures positions over 24 hours', what='Are traders opening new positions or closing them?',
         how='Rising OI with a move = new money behind it. Falling OI = positions closing (often after a flush).',
         zones=_z((-0.05, 'Falling fast', 'quiet', 'Positions are being closed fast.'),
                  (-0.01, 'Falling', 'quiet', 'Positions are being closed.'),
                  (0.01, 'Flat', 'neutral', 'Open positions are steady.'),
                  (0.05, 'Rising', 'busy', 'New positions are being opened.'),
                  (None, 'Rising fast', 'warn', 'Positions are building fast: leverage is rising.')),
         study=None),
    dict(id='global_ratio', name='Long/short accounts ratio', group='Positioning', scope='market', unit='x', scale=[0.5, 3.5],
         settings='Binance accounts holding longs ÷ accounts holding shorts', what='How the crowd of traders is positioned.',
         how='Above 1 = more accounts long. Very high readings are often read contrarian: the crowd is usually late.',
         zones=_z((1.0, 'More short', 'cold', 'More accounts are short than long.'),
                  (1.8, 'Balanced', 'neutral', 'Account positioning is fairly balanced.'),
                  (2.5, 'Long-heavy', 'up', 'Clearly more accounts are long.'),
                  (None, 'Very long-heavy', 'hot', 'The crowd is heavily long.')),
         study=None),
    dict(id='fear_greed', name='Fear & Greed index', group='Positioning', scope='market', unit='', scale=[0, 100],
         settings='Alternative.me daily index (0–100)', what='A daily mood gauge for the crypto market.',
         how='Below 25 = extreme fear; above 75 = extreme greed. Extremes are often read contrarian.',
         zones=_stretch(25, 45, 55, 75, ['Extreme fear', 'Fear', 'Neutral', 'Greed', 'Extreme greed'], [
             'The market mood is extreme fear.', 'The market mood is fearful.', 'The market mood is neutral.',
             'The market mood is greedy.', 'The market mood is extreme greed.']),
         study=None),
    dict(id='premium', name='Futures premium', group='Positioning', scope='market', unit='%', scale=[-0.002, 0.002],
         settings='Futures mark price vs the spot index', what='Are futures trading above or below spot?',
         how='A premium = eager futures buyers; a discount = eager futures sellers.',
         zones=_z((-0.0005, 'Discount', 'down', 'Futures trade below spot: sellers are eager.'),
                  (0.0005, 'In line', 'neutral', 'Futures trade in line with spot.'),
                  (None, 'Premium', 'up', 'Futures trade above spot: buyers are eager.')),
         study=None),
    dict(id='liq_z_15m', name='Liquidations (15 min)', group='Positioning', scope='market', unit='SD', scale=[-1, 6],
         settings='Forced closes in the last 15 minutes vs the last day (recorded live since 20 Oct 2026)',
         what='How many traders were forced out by the exchange just now.',
         how='Bursts mark capitulation moments: sometimes the end of a move, sometimes fuel for more.',
         zones=_z((2, 'Normal', 'neutral', 'Liquidations are normal.'),
                  (4, 'Elevated', 'busy', 'More traders than usual are being forced out.'),
                  (None, 'Burst', 'warn', 'A liquidation burst is happening.')),
         study=None),
]

BY_ID = {i['id']: i for i in INDICATORS}


def zone(indicator, value):
    """(index, label, tone, sentence) of the zone `value` falls in, or None when there is no value."""
    if value is None:
        return None
    for i, (upper, label, tone, sentence) in enumerate(indicator['zones']):
        if upper is None or value < upper:
            return i, label, tone, sentence
    return None


def public():
    """The catalogue as served to the dashboard."""
    return dict(groups=list(GROUPS), timeframes=list(TIMEFRAMES), indicators=INDICATORS)
