"""M5: trade-management RL on playbook trade episodes (Gymnasium environment, tabular Q and PPO)."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.simulation import outcomes
from quantpilot.rules import rl_manage


def episode(path, side='long', entry=100.0, stop=98.0, target=104.0, max_bars=60):
    """Bars after entry: (high, low, close) rows; opens follow the previous close (first open = entry)."""
    rows = list(path)+[(path[-1][2]+.05, path[-1][2]-.05, path[-1][2])]*(max_bars-len(path))
    h, l, c = (np.array(x, float) for x in zip(*rows))
    o = np.r_[entry, c[:-1]]
    return rl_manage.Episode(side=side, entry=entry, stop=stop, target=target, o=o, h=np.maximum(h, np.maximum(o, c)),
                             l=np.minimum(l, np.minimum(o, c)), c=c, max_bars=max_bars, hit_id='x', time=None)


def test_the_fixed_rule_reproduces_the_outcome_simulator():
    rng = np.random.default_rng(3)
    for _ in range(25):
        closes = 100*np.exp(np.cumsum(rng.normal(0, .004, 60)))
        path = [(c*1.002, c*.998, c) for c in closes]
        ep = episode(path)
        t = pd.date_range('2025-01-01', periods=61, freq='5min', tz='UTC')
        bars = outcomes.Bars(t=t, o=np.r_[100.0, ep.o], h=np.r_[100.0, ep.h], l=np.r_[100.0, ep.l], c=np.r_[100.0, ep.c])
        ref = outcomes.simulate(bars, 0, 'long', 98.0, 104.0, 60, breakeven_r=1.0, slippage=0.0)
        got = rl_manage.play(ep._replace(entry=100.0), rl_manage.fixed_rule, slippage=0.0)
        assert got['r'] == pytest.approx(ref['r'], abs=1e-9) and got['reason'] == ref['reason'].replace('BREAKEVEN_STOP', 'STOP')


def test_exit_and_trail_actions():
    up_then_down = [(101.0, 99.8, 100.9), (102.6, 100.8, 102.5), (102.6, 99.0, 99.2), (99.5, 97.5, 97.8)]
    exits = rl_manage.play(episode(up_then_down), lambda s: 'exit' if s[0] >= 1 else 'hold', slippage=0.0)
    assert exits['reason'] == 'EXIT' and exits['exit_index'] == 2 and exits['r'] > 1
    trail = rl_manage.play(episode(up_then_down), lambda s: 'trail', slippage=0.0)
    assert trail['reason'] == 'STOP' and trail['r'] == pytest.approx((100.6-100)/2-2*rl_manage.FEE*100/2, abs=1e-6)


def synthetic(n, seed, reversal=True):
    """Episodes that rally about +1.6R and then fall to the stop: trailing or taking profit beats break-even."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        peak = 100+2*rng.uniform(1.3, 1.9)
        up = np.linspace(100, peak, 8)
        down = np.linspace(peak, 97.5 if reversal else peak+3, 10)
        path = [(c+.1, c-.1, c) for c in np.r_[up, down]]
        out.append(episode(path))
    return out


def test_environment_follows_the_gymnasium_api():
    pytest.importorskip('gymnasium', reason='needs the optional rl extra')
    from gymnasium.utils.env_checker import check_env
    env = rl_manage.ManageEnv(synthetic(5, 0))
    check_env(env, skip_render_check=True)


def test_tabular_q_learns_to_protect_profits_and_the_gate_sees_it():
    q = rl_manage.train_tabular(synthetic(300, 1), epochs=15, seed=0)
    result = rl_manage.compare(synthetic(120, 2), rl_manage.tabular_policy(q))
    assert result['policy_mean_r'] > result['fixed_mean_r'] and result['difference']['low'] > 0 and result['live']


def test_ppo_trains_saves_and_reloads(tmp_path):
    pytest.importorskip('stable_baselines3', reason='needs the optional rl extra')
    path = rl_manage.train_ppo(synthetic(40, 3), tmp_path/'ppo', timesteps=2048, seed=0)
    policy = rl_manage.ppo_policy(path)
    assert policy(np.zeros(len(rl_manage.STATE), np.float32)) in rl_manage.ACTIONS
