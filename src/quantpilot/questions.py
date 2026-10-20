"""Canonical market questions and a rule parser for common phrasings (UTC)."""
from __future__ import annotations

from datetime import timedelta
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['direction', 'touch', 'range', 'big_move', 'explain']
    horizon_hours: int = Field(ge=1, le=168)
    level: float | None = Field(default=None, gt=0)
    side: Literal['above', 'below', 'up', 'down', 'either'] | None = None
    threshold_pct: float | None = Field(default=None, gt=0, le=50)
    coverage: float = Field(default=.8, gt=.5, lt=.99)
    text: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def _complete(self):
        if self.type == 'touch' and self.level is None:
            raise ValueError('touch questions need a price level')
        if self.type == 'direction':
            self.side = 'down' if self.side in ('down', 'below') else 'up'   # probability is P(side)
        if self.type == 'touch' and self.side in ('up', 'down'):
            self.side = 'above' if self.side == 'up' else 'below'
        if self.type == 'big_move':
            self.threshold_pct = self.threshold_pct or 5.0
            self.side = self.side if self.side in ('up', 'down', 'either') else 'either'
        return self


def _hours_until(now, moment):
    return max(1, math.ceil((moment-now).total_seconds()/3600))


def resolve_horizon(text, now):
    t = text.lower()
    found = re.search(r'(\d+)\s*(h|hr|hrs|hour|hours)\b', t)
    if found:
        hours = int(found.group(1))
        return hours if 1 <= hours <= 168 else None
    found = re.search(r'(\d+)\s*(d|day|days)\b', t)
    if found:
        hours = int(found.group(1))*24
        return hours if hours <= 168 else None
    if 'next hour' in t:
        return 1
    if 'tomorrow' in t:
        return 24
    if re.search(r'\btoday\b|end of (the )?day|tonight', t):
        midnight = (now+timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return _hours_until(now, midnight)
    if re.search(r'\bweek\b', t):
        return 168
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf'\b{name}\b', t):
            days = (index-now.weekday()) % 7 or 7
            end = (now+timedelta(days=days)).replace(hour=23, minute=59, second=0, microsecond=0)
            return min(168, _hours_until(now, end))
    return None


def extract_level(text):
    t = text.lower().replace(',', '')
    for found in re.finditer(r'\$?\s*(\d+(?:\.\d+)?)\s*(k\b)?', t):
        value = float(found.group(1))*(1000 if found.group(2) else 1)
        if not found.group(2) and '$' not in found.group(0) and value.is_integer() and 2000 <= value <= 2100:
            continue  # a year such as 2027, not a price
        tail = t[found.end():found.end()+6]
        if re.match(r'\s*(h|hr|hour|d\b|day|%)', tail):
            continue  # a duration or percentage, not a price
        if value >= 1000:
            return value
    return None


def rule_parse(text, now, price):
    t = text.lower()
    horizon = resolve_horizon(t, now)
    if re.search(r'\bwhy\b|what.*(happening|going on|moving)', t):
        return Question(type='explain', horizon_hours=horizon or 24, text=text)
    pct = re.search(r'(\d+(?:\.\d+)?)\s*%', t)
    if pct and re.search(r'drop|fall|crash|dump|decline', t):
        return Question(type='big_move', horizon_hours=horizon or 24, threshold_pct=float(pct.group(1)),
                        side='down', text=text)
    if pct and re.search(r'rally|pump|rise|jump|surge', t):
        return Question(type='big_move', horizon_hours=horizon or 24, threshold_pct=float(pct.group(1)),
                        side='up', text=text)
    level = extract_level(t)
    if level and re.search(r'touch|hit|reach|break|cross|above|below|get to|fall|drop|go', t):
        side = 'below' if level < price else 'above'
        return Question(type='touch', horizon_hours=horizon or 24, level=level, side=side, text=text)
    if re.search(r'\brange\b|where will|how (far|much)|between', t):
        return Question(type='range', horizon_hours=horizon or 24, text=text)
    if re.search(r'\bup\b|\bdown\b|higher|lower|rise|fall|bull|bear|direction|pump|dump', t):
        bearish = re.search(r'\bdown\b|lower|fall|drop|bear|dump|declin|crash', t)
        bullish = re.search(r'\bup\b|higher|rise|bull|pump|rall', t)
        side = 'down' if bearish and not bullish else 'up'
        return Question(type='direction', horizon_hours=horizon or 24, side=side, text=text)
    return None
