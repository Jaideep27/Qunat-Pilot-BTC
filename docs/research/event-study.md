# BTC 5% move event study

Hourly BTCUSDT perpetual data, 2021-10-19 to 2026-10-19 (43,825 complete hours). Drop episodes: **143**, rally episodes: **175**, matched ordinary controls: 954.

> Descriptive research. Findings are associations, not causes, and not trading signals.

## After a 5% drop: continuation or recovery?

| Horizon | Median after drops | Median, controls | Share still falling | Mean difference (95% CI) |
|---|---|---|---|---|
| 1h | +0.14% | -0.02% | 42% | +0.10% (-0.12% to +0.30%) |
| 4h | +0.08% | -0.01% | 47% | +0.22% (-0.08% to +0.51%) |
| 24h | +0.44% | -0.32% | 43% | +0.79% (+0.05% to +1.49%) |

Conditional 24h outcome after drops (median, n):

- funding negative: yes +0.48% (n=25), no +0.37% (n=118)
- open interest falling: yes +0.56% (n=53), no +0.22% (n=46)
- volume above 2x weekly: yes +1.31% (n=20), no +0.37% (n=123)
- macro release within 24h before: yes +0.13% (n=19), no +0.46% (n=124)

## State at the trigger: drops vs. ordinary hours (medians)

| Measure | 24h before drop | At drop trigger | Controls |
|---|---|---|---|
| ret_24h | 0.002186 | -0.05455 | -0.0006297 |
| rsi_14 | 51.78 | 26.18 | 49.56 |
| ema_50_distance | 0.05166 | -3.653 | -0.1583 |
| adx | 26.86 | 35.69 | 21.38 |
| atr_fraction | 0.009342 | 0.01221 | 0.007243 |
| realized_vol_24h | 0.02842 | 0.03688 | 0.02079 |
| dist_7d_low | 0.05697 | 0.009233 | 0.05127 |
| dist_7d_high | -0.05468 | -0.1026 | -0.04695 |
| volume_ratio_24h | 1.07 | 1.386 | 0.7586 |
| taker_buy_share_24h | 0.4972 | 0.4819 | 0.4981 |
| oi_change_24h | 0.007357 | -0.005407 | -0.001281 |
| funding_rate | 7.813e-05 | 9.173e-05 | 7.516e-05 |
| global_ratio | 1.811 | 2.115 | 1.851 |
| global_ratio_change_24h | 0.01601 | 0.3727 | 0.0003027 |
| top_position_ratio | 1.331 | 1.338 | 1.337 |
| top_position_ratio_change_24h | -0.006996 | 0.02301 | -0.008871 |
| premium | -0.0003509 | -0.0003033 | -0.0003784 |
| fear_greed | 30 | 30 | 31 |
| hours_to_next_cpi | 168 | 168 | 168 |
| hours_to_next_jobs | 168 | 168 | 168 |
| hours_to_next_fomc | 168 | 168 | 168 |

## Before the move: how often a 5% drop follows within 24h, by state

Base rate and quintile rates over all hours (touching -5% at any point in the next 24h).

- **ret_24h** base 7.4% · lowest→highest quintile: 12.5% (146d), 6.4% (159d), 5.0% (147d), 4.5% (131d), 8.7% (146d)
- **rsi_14** base 7.4% · lowest→highest quintile: 11.1% (151d), 7.1% (168d), 6.5% (165d), 5.6% (164d), 6.8% (146d)
- **realized_vol_24h** base 7.4% · lowest→highest quintile: 3.2% (45d), 3.8% (72d), 6.9% (108d), 8.1% (141d), 15.1% (168d)
- **dist_7d_low** base 7.4% · lowest→highest quintile: 9.4% (110d), 6.1% (112d), 6.7% (118d), 6.4% (118d), 8.6% (120d)
- **volume_ratio_24h** base 7.4% · lowest→highest quintile: 4.3% (66d), 7.6% (114d), 6.3% (142d), 7.7% (143d), 11.2% (127d)
- **taker_buy_share_24h** base 7.4% · lowest→highest quintile: 9.2% (117d), 8.6% (149d), 6.6% (159d), 6.8% (140d), 6.0% (100d)
- **oi_change_24h** base 6.5% · lowest→highest quintile: 5.0% (78d), 4.5% (105d), 5.8% (122d), 7.6% (125d), 9.6% (120d)
- **funding_rate** base 7.4% · lowest→highest quintile: 6.9% (95d), 4.5% (85d), 6.0% (112d), 8.9% (163d), 14.5% (52d)
- **global_ratio** base 6.5% · lowest→highest quintile: 3.6% (45d), 4.6% (56d), 4.4% (64d), 7.2% (85d), 12.7% (99d)
- **top_position_ratio** base 6.5% · lowest→highest quintile: 5.0% (54d), 7.4% (78d), 5.4% (60d), 7.4% (61d), 7.4% (58d)
- **premium** base 7.4% · lowest→highest quintile: 6.2% (165d), 5.4% (170d), 5.6% (173d), 8.6% (206d), 11.3% (164d)
- **fear_greed** base 7.4% · lowest→highest quintile: 12.0% (114d), 7.5% (68d), 5.1% (43d), 4.4% (53d), 8.0% (72d)

## Recent drop episodes

| Trigger (UTC) | 24h move | Next 24h | Macro within -48h/+24h |
|---|---|---|---|
| 2025-12-01T04:00 | -5.01% | +0.71% | — |
| 2026-01-29T16:00 | -5.08% | -2.41% | FOMC rate decision (-21h) |
| 2026-01-31T19:00 | -7.03% | -0.00% | — |
| 2026-02-03T18:00 | -5.14% | -3.12% | — |
| 2026-02-05T02:00 | -5.24% | -9.29% | — |
| 2026-02-06T02:00 | -9.29% | +7.26% | — |
| 2026-02-23T02:00 | -5.50% | -0.40% | — |
| 2026-02-24T13:00 | -5.00% | +4.93% | — |
| 2026-02-28T07:00 | -6.57% | +5.88% | — |
| 2026-03-06T15:00 | -5.80% | -0.57% | Employment Situation (-2h) |
| 2026-03-19T08:00 | -5.00% | +0.95% | FOMC rate decision (-14h) |
| 2026-06-02T16:00 | -5.38% | -1.82% | — |
| 2026-06-04T01:00 | -5.36% | +0.13% | — |
| 2026-06-05T14:00 | -5.46% | +0.13% | Employment Situation (-2h) |
| 2026-06-18T16:00 | -5.14% | +1.37% | FOMC rate decision (-22h) |

## Limitations

- Associations only: a nearby release or headline does not establish cause.
- Hours overlap: precursor rates share future windows; distinct_days counts separate dates.
- Episodes are few and cluster in volatile periods; intervals resample episodes as independent.
- Positioning history starts October 2021 and invalid provider rows are excluded.
- News is collected forward only, so historical episodes have no news timeline.
- Macro values are first releases; no consensus forecast, so surprises are not measured.
