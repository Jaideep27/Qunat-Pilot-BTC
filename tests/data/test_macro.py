from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from quantpilot.data import macro


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def fred_row(day, time='7:30 am'):
    return (f'<tr class="odd"><td colspan="2"><span style="font-weight: bold;">{day}</span> '
            f'<span style="float: right; font-weight: bold;">Updated</span></td></tr>'
            f'<tr><td nowrap style="width:5%; text-align:right">\n\n          {time}      </td>'
            f'<td><a href="/release?rid=10">Consumer Price Index</a></td></tr>')


FRED_2025 = ('<html><table><tbody>' + fred_row('Wednesday January 15, 2025') +
             fred_row('Wednesday June 11, 2025') + '</tbody></table></html>')


def fomc_meeting(month, dates, links=True, shaded=False):
    body = ('<strong>Statement:</strong><br><a href="/newsevents/pressreleases/monetary20250129a.htm">HTML</a>'
            if links else '')
    row = 'fomc-meeting--shaded row fomc-meeting' if shaded else 'row fomc-meeting'  # the Fed alternates both
    return (f'<div class="{row}" "><div class="fomc-meeting__month col-xs-5"><strong>{month}</strong></div>'
            f'<div class="fomc-meeting__date col-xs-4">{dates}</div><div class="col-xs-12">{body}</div></div>')


FOMC_PAGE = ('<div class="panel panel-default"><div class="panel-heading"><h4><a id="1">2025 FOMC Meetings</a></h4></div>'
             + fomc_meeting('January', '28-29') + fomc_meeting('June', '17-18*', shaded=True)
             + fomc_meeting('Apr/May', '30-1') + fomc_meeting('August', '22 (notation vote)')
             + '</div><div class="panel panel-default"><div class="panel-heading"><h4><a id="2">2026 FOMC Meetings</a></h4></div>'
             + fomc_meeting('October', '27-28', links=False) + '</div>')

# Shapes of real ALFRED vintages dated on the January 2025 release days.
CPI_VINTAGE = 'observation_date,CPIAUCSL_20250212\n2024-11-01,316.449\n2024-12-01,317.603\n2025-01-01,319.086\n'
PAYEMS_VINTAGE = 'observation_date,PAYEMS_20250207\n2024-12-01,158926\n2025-01-01,159069\n'


def test_fred_calendar_times_are_central_and_daylight_saving_aware():
    # 7:30 am Central is 8:30 am Eastern: 13:30 UTC in winter, 12:30 UTC in summer.
    assert macro.parse_fred_calendar(FRED_2025) == [utc('2025-01-15T13:30'), utc('2025-06-11T12:30')]


def test_fomc_decisions_use_last_meeting_day_at_2pm_eastern_and_skip_notation_votes():
    meetings = macro.parse_fomc_calendar(FOMC_PAGE)
    assert [m['decision_at'] for m in meetings] == [
        utc('2025-01-29T19:00'), utc('2025-06-18T18:00'), utc('2025-05-01T18:00'), utc('2026-10-28T18:00')]
    assert [m['statement_published'] for m in meetings] == [True, True, True, False]


def test_first_release_reads_the_new_month_from_a_release_day_vintage():
    cpi = macro.first_release(CPI_VINTAGE)
    assert cpi['period'] == '2025-01-01' and cpi['value'] == 319.086 and cpi['previous'] == 317.603
    assert cpi['change_pct'] == pytest.approx(0.4669, abs=1e-4)
    assert macro.first_release(PAYEMS_VINTAGE)['change'] == pytest.approx(143)
    with pytest.raises(ValueError):
        macro.first_release('observation_date,CPIAUCSL\n2025-01-01,.\n')


def test_features_know_schedules_in_advance_but_values_only_after_release():
    events = [dict(kind='cpi', scheduled_at='2025-01-15T13:30:00+00:00',
                   values={'CPIAUCSL': dict(change_pct=0.4669)})]
    index = pd.DatetimeIndex(['2025-01-01T00:00', '2025-01-15T12:30', '2025-01-15T13:30', '2025-01-15T18:30'], tz='UTC')
    f = macro.macro_features(index, events)
    assert f['hours_to_next_cpi'].tolist()[1:3] == [1.0, 0.0]
    assert f['hours_to_next_cpi'].iloc[0] == 168.0  # capped: far-off dates may still be rescheduled
    assert f['cpi_window'].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert np.isnan(f['last_cpi_mom'].iloc[1])
    assert f['last_cpi_mom'].iloc[2:].tolist() == [0.4669, 0.4669]
    assert f['hours_since_cpi'].iloc[3] == 5.0


def test_collection_stores_schedule_and_first_release_values_idempotently(account):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        url = str(request.url)
        if 'fomccalendars' in url:
            return httpx.Response(200, text=FOMC_PAGE)
        if 'releases/calendar' in url:
            return httpx.Response(200, text=FRED_2025 if 'rid=10' in url else '<tbody></tbody>')
        if 'alfredgraph' in url and 'vintage_date=2025-01-15' in url:
            return httpx.Response(200, text=CPI_VINTAGE)
        return httpx.Response(404)
    now = utc('2025-03-01T00:00')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        first = macro.collect(account, client, years=[2025], now=now, pause=0)
        again = macro.collect(account, client, years=[2025], now=now, pause=0)
    events = {e['id']: e for e in macro.events(account)}
    cpi = events['cpi:2025-01-15']
    assert cpi['status'] == 'released' and cpi['values']['CPIAUCSL']['period'] == '2025-01-01'
    assert cpi['surprise'] is None and cpi['surprise_status'] == 'unavailable_no_timestamped_consensus'
    assert events['cpi:2025-06-11']['status'] == 'scheduled' and events['cpi:2025-06-11']['values'] == {}
    assert events['fomc:2026-10-28']['status'] == 'scheduled'
    assert first['events'] == again['events'] == len(events)
    # Values already stored are never re-downloaded.
    assert sum('vintage_date=2025-01-15' in c for c in calls) == len(macro.SOURCES['cpi']['series'])


def test_macro_api_is_read_only_and_matches_service(account):
    from fastapi.testclient import TestClient
    from quantpilot.api import services
    from quantpilot.api.app import app
    with TestClient(app) as client:
        assert client.get('/api/v1/macro').json()['data'] == services.macro()['data']
        assert client.post('/api/v1/macro').status_code == 405


def vintage_server(series, released_on, month, previous_month, calls):
    """ALFRED stand-in: vintages from `released_on` onward contain `month`; earlier ones end at `previous_month`."""
    def respond(request):
        calls.append(str(request.url))
        params = dict(request.url.params)
        if params.get('id') != series:
            return httpx.Response(404)
        last = month if params['vintage_date'] >= released_on else previous_month
        return httpx.Response(200, text=f'observation_date,{series}\n{previous_month},100.0\n{last},101.0\n')
    return respond


def test_release_date_is_the_first_vintage_containing_the_month():
    calls = []
    respond = vintage_server('CPIAUCSL', '2023-02-14', '2023-01-01', '2022-12-01', calls)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        found = macro.discover_release(client, 'CPIAUCSL', pd.Timestamp('2023-01-01'), pause=0)
    assert found == '2023-02-14'
    assert len(calls) <= 6  # binary search over the following month


def test_history_backfill_adds_pre_calendar_releases_at_830_eastern(account):
    calls = []
    respond = vintage_server('CPIAUCSL', '2023-02-14', '2023-01-01', '2022-12-01', calls)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        added = macro.backfill_history(account, client, kind='cpi', months=[pd.Timestamp('2023-01-01')], pause=0)
    event = {e['id']: e for e in macro.events(account)}['cpi:2023-02-14']
    assert added == 1 and event['scheduled_at'] == '2023-02-14T13:30:00+00:00'  # 8:30 EST
    assert event['reference_month'] == '2023-01-01' and event['status'] == 'released'


def test_calendar_pages_for_unsupported_years_are_ignored(account):
    def respond(request):
        url = str(request.url)
        if 'releases/calendar' in url:  # FRED answers any year before 2025 with its 2025 page
            return httpx.Response(200, text=FRED_2025.replace('<html>', '<html><input name="vs" value="2025-01-01">'))
        return httpx.Response(200, text='') if 'fomccalendars' in url else httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        macro.collect(account, client, years=[2023], now=utc('2025-03-01T00:00'), pause=0, backfill=False)
    assert [e for e in macro.events(account) if e['kind'] == 'cpi'] == []
