"""C1 Funding Collector (A10): hold spot BTC and an equal perpetual short while funding is positive.

The two legs cancel price moves; the account earns the funding that leveraged longs pay shorts every 8 hours.
Source: the spot-perpetual basis ('cash and carry') literature, e.g. He, Manela, Ross & von Wachter, 'Fundamentals of
Perpetual Futures'. Our own data: about +5% gross in 2025, +2-3% a year in 2026, more in bull years.
"""
CODE, NAME = 'C1', 'Funding Collector'
OPEN_WINDOW, CLOSE_WINDOW = 21, 9          # settlements: 7 days and 3 days
RULES = [
    'Does not bet on direction: holds BTC and an equal short future at the same time, so price moves cancel out.',
    'Earns the fee that leveraged buyers pay sellers every 8 hours.',
    'Starts when that fee averaged above zero over the last 7 days; stops when it averages below zero over 3 days.',
    'Uses 60% of the account per side (40% kept as margin). Rebalances after a big move. Reviewed every 30 days.',
]


def decide(rates, is_open):
    """'open', 'close' or None, from the funding rates settled so far (oldest first)."""
    rates = [float(r) for r in rates]
    if not is_open and len(rates) >= OPEN_WINDOW and sum(rates[-OPEN_WINDOW:])/OPEN_WINDOW > 0:
        return 'open'
    if is_open and len(rates) >= CLOSE_WINDOW and sum(rates[-CLOSE_WINDOW:])/CLOSE_WINDOW < 0:
        return 'close'
    return None


def active(rates, is_open):
    if len(rates) < OPEN_WINDOW:
        return False, 'Off: waiting for 7 days of funding history.'
    week = sum(rates[-OPEN_WINDOW:])/OPEN_WINDOW
    yearly = week*3*365
    if is_open:
        return True, f'On: holding the hedge; funding is running at about {yearly:+.1%} a year.'
    if week > 0:
        return True, f'On: funding is positive (about {yearly:+.1%} a year); it opens at the next settlement.'
    return False, f'Off: funding is negative (about {yearly:+.1%} a year), so holding the hedge would cost money.'
