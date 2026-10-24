"""Official macro release calendar with first-release values.

Schedules: FRED's release calendar for BLS releases (times shown in US Central) and the
Federal Reserve's FOMC calendar (scheduled decisions at 2:00 pm Eastern). Values: ALFRED
vintages dated on the release day, i.e. what was first published, never later revisions.
No timestamped consensus forecast is available, so a "surprise" is never computed.
"""
from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta, timezone
from html import unescape
import re
import time
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import JSON, Column, String, select
from sqlalchemy.orm import Session

from quantpilot.config import settings
from quantpilot.storage.database import Base, database, serialized

CENTRAL, EASTERN = ZoneInfo('America/Chicago'), ZoneInfo('America/New_York')
FRED_CALENDAR = 'https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}'
ALFRED_VINTAGE = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={series}&vintage_date={day}'
FOMC_CALENDAR = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
SOURCES = {
    'cpi': dict(rid=10, label='Consumer Price Index', series=('CPIAUCSL', 'CPILFESL')),
    'jobs': dict(rid=50, label='Employment Situation', series=('PAYEMS', 'UNRATE')),
}
KINDS = ('cpi', 'jobs', 'fomc')
# A far-off date may still be rescheduled (e.g. the 2025 shutdown), so lead times are capped.
LEAD_CAP_HOURS, SINCE_CAP_HOURS = 168.0, 720.0
WINDOW = (timedelta(hours=-2), timedelta(hours=4))
MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct',
                                       'nov', 'dec'), 1)}


class MacroEvent(Base):
    __tablename__ = 'macro_events'
    id = Column(String(40), primary_key=True)
    kind = Column(String(16), nullable=False, index=True)
    scheduled_at = Column(String(40), nullable=False, index=True)
    payload = Column(JSON, nullable=False)


def text(fragment):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def parse_fred_calendar(html):
    """Release instants (UTC) from one FRED calendar page; FRED lists times in US Central."""
    body = html[html.find('<tbody>'):html.find('</tbody>')]
    result, day = [], None
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S):
        value = text(row)
        found = re.search(r'[A-Z][a-z]+day ([A-Z][a-z]+ \d{1,2}, \d{4})', value)
        if found:
            day = datetime.strptime(found.group(1), '%B %d, %Y').date()
            continue
        moment = re.match(r'(\d{1,2}:\d{2} [ap]m)', value)
        if day and moment:
            local = datetime.combine(day, datetime.strptime(moment.group(1), '%I:%M %p').time(), CENTRAL)
            result.append(local.astimezone(timezone.utc))
            day = None
    return result


def parse_fomc_calendar(html):
    """Scheduled rate decisions: the meeting's last day at 2:00 pm Eastern. Notation votes are skipped."""
    meetings = []
    panels = re.split(r'(\d{4}) FOMC Meetings', html)
    for year, panel in zip(panels[1::2], panels[2::2]):
        # Rows alternate between "row fomc-meeting" and "fomc-meeting--shaded row fomc-meeting".
        blocks = re.split(r'class="[^"]*\bfomc-meeting"', panel)[1:]
        for block in blocks:
            month = re.search(r'fomc-meeting__month[^>]*>(.*?)</div>', block, re.S)
            dates = re.search(r'fomc-meeting__date[^>]*>(.*?)</div>', block, re.S)
            if not month or not dates:
                continue
            month, dates = text(month.group(1)), text(dates.group(1))
            if 'notation' in dates.lower():
                continue
            days = [int(d) for d in re.findall(r'\d+', dates)]
            last_month = MONTHS[month.split('/')[-1].strip()[:3].lower()]
            day = datetime(int(year), last_month, days[-1]).date()
            decision = datetime.combine(day, clock_time(14, 0), EASTERN).astimezone(timezone.utc)
            meetings.append(dict(date=day.isoformat(), decision_at=decision,
                                 unscheduled='unscheduled' in dates.lower(),
                                 statement_published=bool(re.search(r'monetary\d{8}a\.htm', block))))
    return meetings


def calendar_year(html):
    """Year a FRED calendar page actually shows; FRED answers unsupported years with another year."""
    found = re.search(r'name="vs" value="(\d{4})-', html)
    return int(found.group(1)) if found else None


def first_release(csv_text):
    """Latest observation in a release-day vintage, with the prior month from the same vintage."""
    rows = [line.split(',') for line in csv_text.strip().splitlines()[1:] if ',' in line]
    if not rows or rows[-1][1].strip() in ('', '.'):
        raise ValueError('latest observation unavailable in vintage')
    period, value = rows[-1][0], float(rows[-1][1])
    previous = float(rows[-2][1]) if len(rows) > 1 and rows[-2][1].strip() not in ('', '.') else None
    return dict(period=period, value=value, previous=previous,
                change=None if previous is None else value-previous,
                change_pct=None if previous is None else (value/previous-1)*100)


def macro_features(index, events):
    """Schedule-time features (dates are published in advance) plus values known only after release."""
    out = pd.DataFrame(index=index)
    stamps = index.asi8
    for kind in KINDS:
        times = pd.DatetimeIndex(sorted(pd.Timestamp(e['scheduled_at']) for e in events if e['kind'] == kind))
        hours_to = np.full(len(index), np.nan)
        hours_since = np.full(len(index), np.nan)
        inside = np.zeros(len(index))
        if len(times):
            moments = times.tz_convert('UTC').asi8
            nxt = np.searchsorted(moments, stamps, side='left')
            has_next = nxt < len(moments)
            hours_to[has_next] = (moments[nxt[has_next]]-stamps[has_next])/3.6e12
            prev = np.searchsorted(moments, stamps, side='right')-1
            has_prev = prev >= 0
            hours_since[has_prev] = (stamps[has_prev]-moments[prev[has_prev]])/3.6e12
            before, after = (abs(w.total_seconds())/3600 for w in WINDOW)
            inside = (((hours_to <= before) & has_next) | ((hours_since <= after) & has_prev)).astype(float)
        out[f'hours_to_next_{kind}'] = np.minimum(hours_to, LEAD_CAP_HOURS)
        out[f'hours_since_{kind}'] = np.minimum(hours_since, SINCE_CAP_HOURS)
        out[f'{kind}_window'] = inside
    for column, kind, series, field in (('last_cpi_mom', 'cpi', 'CPIAUCSL', 'change_pct'),
                                        ('last_core_cpi_mom', 'cpi', 'CPILFESL', 'change_pct'),
                                        ('last_payrolls_change', 'jobs', 'PAYEMS', 'change'),
                                        ('last_unemployment_rate', 'jobs', 'UNRATE', 'value')):
        known = sorted((pd.Timestamp(e['scheduled_at']), (e.get('values') or {}).get(series, {}).get(field))
                       for e in events if e['kind'] == kind)
        known = [(t, v) for t, v in known if v is not None]
        if not known:
            out[column] = np.nan
            continue
        right = pd.DataFrame(known, columns=['available_at', column])
        right['available_at'] = pd.to_datetime(right.available_at, utc=True)
        left = pd.DataFrame({'cutoff': index})
        out[column] = pd.merge_asof(left, right, left_on='cutoff', right_on='available_at')[column].to_numpy()
    return out


def events(engine=None):
    with Session(engine or database()) as session:
        rows = session.scalars(select(MacroEvent).order_by(MacroEvent.scheduled_at)).all()
        return [dict(id=r.id, **r.payload) for r in rows]


def _get(client, url, pause):
    if pause:
        time.sleep(pause)  # Keep requests sparse; these are public research services.
    response = client.get(url)
    response.raise_for_status()
    return response.text


def _last_period(client, series, day, pause):
    rows = [line.split(',') for line in _get(client, ALFRED_VINTAGE.format(series=series, day=day), pause)
            .strip().splitlines()[1:] if ',' in line]
    return rows[-1][0] if rows else ''


def discover_release(client, series, month, pause=1.5):
    """Release date of `month`: the first ALFRED vintage containing it, searched in the next two months."""
    target = pd.Timestamp(month).strftime('%Y-%m-%d')
    for offset in (1, 2):
        lo = pd.Timestamp(month)+pd.DateOffset(months=offset)
        hi = lo+pd.offsets.MonthEnd(0)
        if _last_period(client, series, hi.date().isoformat(), pause) < target:
            continue
        while lo < hi:
            mid = lo+pd.Timedelta(days=(hi-lo).days//2)
            if _last_period(client, series, mid.date().isoformat(), pause) >= target:
                hi = mid
            else:
                lo = mid+pd.Timedelta(days=1)
        return lo.date().isoformat()
    return None


def _payload(kind, label, at, day, source, time_basis, now, stored, **extra):
    return dict(kind=kind, label=label, scheduled_at=at.isoformat(), release_date=day, source=source,
                time_basis=time_basis, unscheduled=extra.pop('unscheduled', False),
                status='released' if at <= now else 'scheduled',
                values=stored.get('values', {}), value_sources=stored.get('value_sources', {}),
                surprise=None, surprise_status='unavailable_no_timestamped_consensus', **extra)


def backfill_history(engine, client, kind, months, now=None, pause=1.5):
    """Releases before the FRED calendar's coverage, dated by ALFRED vintages; BLS publishes at 8:30 Eastern."""
    now = now or datetime.now(timezone.utc)
    source, added = SOURCES[kind], 0
    for month in months:
        day = discover_release(client, source['series'][0], month, pause)
        if day is None:
            continue
        at = datetime.combine(datetime.fromisoformat(day).date(), clock_time(8, 30), EASTERN).astimezone(timezone.utc)
        with serialized(engine) as session:
            key = f'{kind}:{day}'
            if session.get(MacroEvent, key):
                continue
            payload = _payload(kind, source['label'], at, day, ALFRED_VINTAGE.format(series=source['series'][0], day=day),
                               'BLS standard 8:30 am Eastern; date = first ALFRED vintage containing the month',
                               now, {}, reference_month=pd.Timestamp(month).date().isoformat())
            session.add(MacroEvent(id=key, kind=kind, scheduled_at=payload['scheduled_at'], payload=payload))
            added += 1
    return added


def collect(engine=None, client=None, years=None, now=None, pause=1.5, backfill=True):
    """Refresh schedules, then download first-release values for released events that lack them."""
    engine = engine or database()
    now = now or datetime.now(timezone.utc)
    years = years or range(now.year-settings.market_history_years, now.year+2)
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False,
                                    headers={'User-Agent': 'QuantPilot/0.1 personal research'})
    errors = []
    try:
        scheduled = []
        for kind, source in SOURCES.items():
            for year in years:
                url = FRED_CALENDAR.format(rid=source['rid'], year=year)
                try:
                    page = _get(client, url, pause)
                    if calendar_year(page) not in (None, year):
                        continue  # FRED's calendar starts in 2025; older years return another year's page
                    for moment in parse_fred_calendar(page):
                        scheduled.append(dict(kind=kind, label=source['label'], at=moment, source=url,
                                              time_basis='FRED calendar time (US Central)'))
                except httpx.HTTPError as exc:
                    errors.append(dict(url=url, error_type=type(exc).__name__))
        try:
            for meeting in parse_fomc_calendar(_get(client, FOMC_CALENDAR, pause)):
                # Keep every later meeting too: upcoming decisions drive the lead-time features.
                if meeting['decision_at'].year >= min(years):
                    scheduled.append(dict(kind='fomc', label='FOMC rate decision', at=meeting['decision_at'],
                                          source=FOMC_CALENDAR, unscheduled=meeting['unscheduled'],
                                          time_basis='Scheduled statement time 2:00 pm Eastern (assumed)'))
        except httpx.HTTPError as exc:
            errors.append(dict(url=FOMC_CALENDAR, error_type=type(exc).__name__))
        with serialized(engine) as session:
            for item in scheduled:
                day = item['at'].astimezone(EASTERN).date().isoformat()
                key = f"{item['kind']}:{day}"
                row = session.get(MacroEvent, key)
                payload = _payload(item['kind'], item['label'], item['at'], day, item['source'], item['time_basis'],
                                   now, row.payload if row else {}, unscheduled=item.get('unscheduled', False))
                if row:
                    row.payload, row.scheduled_at = payload, payload['scheduled_at']
                else:
                    session.add(MacroEvent(id=key, kind=item['kind'], scheduled_at=payload['scheduled_at'],
                                           payload=payload))
        if backfill:
            for kind in SOURCES:
                stored = [e for e in events(engine) if e['kind'] == kind]
                calendar = [e['release_date'] for e in stored if 'reference_month' not in e]
                if not calendar:
                    continue
                done = {e['reference_month'] for e in stored if 'reference_month' in e}
                # The earliest calendar release covers the month before it; backfill everything older.
                last = pd.Timestamp(min(calendar)).replace(day=1)-pd.DateOffset(months=2)
                months = [m for m in pd.date_range(f'{min(years)-1}-12-01', last, freq='MS')
                          if m.date().isoformat() not in done]
                try:
                    backfill_history(engine, client, kind, months, now, pause)
                except httpx.HTTPError as exc:
                    errors.append(dict(url='ALFRED vintage search', error_type=type(exc).__name__))
        fetched = 0
        for event in events(engine):
            source = SOURCES.get(event['kind'])
            if not source or event['status'] != 'released':
                continue
            values, sources = dict(event['values']), dict(event['value_sources'])
            for series in source['series']:
                if series in values:
                    continue
                url = ALFRED_VINTAGE.format(series=series, day=event['release_date'])
                try:
                    values[series] = first_release(_get(client, url, pause))
                    sources[series] = dict(url=url, vintage='first release (ALFRED vintage on release day)',
                                           retrieved_at=datetime.now(timezone.utc).isoformat())
                    fetched += 1
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(dict(url=url, error_type=type(exc).__name__))
            if values != event['values']:
                with serialized(engine) as session:
                    row = session.get(MacroEvent, event['id'])
                    row.payload = {**row.payload, 'values': values, 'value_sources': sources}
    finally:
        if own:
            client.close()
    return dict(events=len(events(engine)), values_fetched=fetched, errors=errors[:50], error_count=len(errors),
                checked_at=now.isoformat())


def snapshot(engine=None, now=None, recent=12):
    now = now or datetime.now(timezone.utc)
    rows = events(engine)
    released = [e for e in rows if pd.Timestamp(e['scheduled_at']) <= now]
    upcoming = {kind: next((e for e in rows if e['kind'] == kind and pd.Timestamp(e['scheduled_at']) > now), None)
                for kind in KINDS}
    return dict(upcoming=upcoming, recent=list(reversed(released[-recent:])), total_events=len(rows),
                first_event=rows[0]['scheduled_at'] if rows else None,
                last_event=rows[-1]['scheduled_at'] if rows else None,
                surprise_status='unavailable_no_timestamped_consensus',
                scope=('Official schedules (FRED calendar for BLS releases; Federal Reserve FOMC calendar) and '
                       'first-release values from ALFRED vintages. Values exist only after release time.'))


def run(once=False):
    from quantpilot.storage.database import initialize
    engine = database()
    initialize(engine)
    while True:
        result = collect(engine)
        print(dict(macro=result['events'], values_fetched=result['values_fetched'],
                   errors=result['error_count']), flush=True)
        if once:
            return result
        time.sleep(86400)
