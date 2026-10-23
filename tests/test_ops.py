"""Ops safety: watchdog checks and decisions, email alerts, backups."""
import pandas as pd

from quantpilot import ops

NOW = pd.Timestamp('2026-10-23 12:00', tz='UTC')
HEALTHY = dict(ready=dict(ready=True), engine_at=NOW-pd.Timedelta(seconds=40), candle_age_s=120.0, model='OK', disk_gb=100.0,
               backup_at=NOW-pd.Timedelta(hours=10))


def keys(problems):
    return sorted(p['key'] for p in problems)


def step(state, problems, minutes):
    return ops.tick(state, problems, NOW+pd.Timedelta(minutes=minutes))


def kinds(actions):
    return [a[0] for a in actions]


def test_check_flags_each_condition():
    assert ops.check(HEALTHY, NOW) == []
    assert keys(ops.check(dict(HEALTHY, ready=None), NOW)) == ['api']
    assert keys(ops.check(dict(HEALTHY, ready=dict(ready=False)), NOW)) == ['api']
    stale = ops.check(dict(HEALTHY, engine_at=NOW-pd.Timedelta(minutes=9)), NOW)
    assert keys(stale) == ['engine'] and stale[0]['restart'] and '9 minutes' in stale[0]['text']
    data = ops.check(dict(HEALTHY, candle_age_s=30*60.0), NOW)
    assert keys(data) == ['data'] and not data[0]['restart']
    assert keys(ops.check(dict(HEALTHY, candle_age_s=30*60.0, engine_at=None), NOW)) == ['engine']   # data age is not separate news
    assert keys(ops.check(dict(HEALTHY, model='ALARM'), NOW)) == ['model']
    assert keys(ops.check(dict(HEALTHY, disk_gb=3.2), NOW)) == ['disk']
    assert keys(ops.check(dict(HEALTHY, backup_at=NOW-pd.Timedelta(hours=40)), NOW)) == ['backup']


def test_one_bad_tick_does_not_restart_two_do():
    down = ops.check(dict(HEALTHY, ready=None), NOW)
    state, actions = step({}, down, 0)
    assert actions == []
    state, actions = step(state, [], 5)                     # it recovered on its own
    assert actions == []
    state, _ = step(state, down, 10)
    state, actions = step(state, down, 15)
    assert kinds(actions) == ['restart', 'email']
    assert 'restarting' in actions[1][2].lower() and actions[1][1].startswith('QuantPilot:')
    state, actions = step(state, down, 20)                  # streak was reset by the restart
    assert 'restart' not in kinds(actions)


def test_restart_budget_stops_at_three_in_two_hours_and_emails_once():
    down = ops.check(dict(HEALTHY, ready=None), NOW)
    state, times, budget_mails = {}, [], 0
    for i in range(36):                                     # three hours of a server that will not come back
        state, actions = step(state, down, 5*i)
        times += [5*i]*kinds(actions).count('restart')
        if 5*i < 120:
            budget_mails += sum(1 for a in actions if a[0] == 'email' and 'gave up' in a[2])
    assert times[:3] == [5, 15, 25] and len(times) > 3     # it tries again only once the 2-hour window frees up
    assert all(sum(t-120 < u <= t for u in times) <= 3 for t in times)
    assert budget_mails == 1


def test_new_problem_emails_reminds_after_six_hours_and_resolves():
    disk = ops.check(dict(HEALTHY, disk_gb=3.0), NOW)
    state, actions = step({}, disk, 0)
    assert actions == []
    state, actions = step(state, disk, 5)
    assert kinds(actions) == ['email'] and 'disk' in actions[0][2].lower()
    state, actions = step(state, disk, 60)
    assert actions == []
    state, actions = step(state, disk, 6*60+10)
    assert kinds(actions) == ['email'] and 'still' in actions[0][2].lower()
    state, actions = step(state, [], 6*60+15)
    assert kinds(actions) == ['email'] and 'resolved' in actions[0][1].lower()
    assert state['active'] == {}


def test_a_problem_that_never_confirmed_does_not_send_resolved():
    disk = ops.check(dict(HEALTHY, disk_gb=3.0), NOW)
    state, _ = step({}, disk, 0)
    state, actions = step(state, [], 5)
    assert actions == []


class FakeSMTP:
    sent, fail = [], None

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.fail:
            raise FakeSMTP.fail(f'535 bad credentials for {user} {password}')

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_email_never_raises_and_hides_the_password():
    cfg = dict(email='owner@example.com', password='abcd efgh ijkl mnop')
    FakeSMTP.sent, FakeSMTP.fail = [], None
    ok, error = ops.send_email('QuantPilot: test', 'body', cfg, smtp_factory=FakeSMTP)
    assert ok and error is None and FakeSMTP.sent[0]['To'] == 'owner@example.com'
    FakeSMTP.fail = RuntimeError
    ok, error = ops.send_email('QuantPilot: test', 'body', cfg, smtp_factory=FakeSMTP)
    assert not ok and 'abcd efgh' not in error and 'RuntimeError' in error


def test_no_settings_means_log_only():
    ok, error = ops.send_email('QuantPilot: test', 'body', dict(email=None, password=None), smtp_factory=FakeSMTP)
    assert not ok and error == 'email not configured'
