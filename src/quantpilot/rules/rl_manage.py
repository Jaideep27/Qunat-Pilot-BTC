"""M5: trade-management reinforcement learning on playbook trade episodes (amendment A6).

An episode is one playbook trade on 5m bars from its fill to its time stop, with the futures-v1 fill rules
(stop before target inside a bar, gaps through the stop fill at the open, slippage on every fill). At each
bar close the agent chooses: hold, move the stop to break-even (entry plus round-trip costs), trail the stop
1R behind the best price, or exit at the next open. The reward is the trade's final net R. Two agents are
trained on 2021-10 -> 2023-10 trades and compared on 2023-10 -> 2025-10 trades with the fixed rule
(break-even at +1R): tabular Monte-Carlo Q-learning and PPO (stable-baselines3, CPU, small network,
checkpointed). A policy goes live only if its test mean R beats the fixed rule with a bootstrap 95%
interval above zero. Playbook aborts and funding are outside this comparison (both sides ignore them).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from quantpilot.config import settings
from quantpilot.simulation.futures import FEE, SLIPPAGE

ACTIONS = ('hold', 'breakeven', 'trail', 'exit')
STATE = ('unrealized_r', 'mfe_r', 'mae_r', 'held', 'to_target_r', 'stop_r')
SPLIT = pd.Timestamp('2023-10-20', tz='UTC')
FOLDER = settings.artifact_dir/'rule-engine'
MIN_TEST, BOOTSTRAP = 30, 2000


class Episode(NamedTuple):
    side: str
    entry: float          # fill price (slippage included)
    stop: float
    target: float
    o: np.ndarray         # bars from the fill bar on
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    max_bars: int
    hit_id: str
    time: object


class Trade:
    """One episode stepped bar by bar: `advance` processes the next bar, `act` applies a close decision."""

    def __init__(self, ep, slippage=SLIPPAGE, fee=FEE):
        self.ep, self.slip, self.fee = ep, slippage, fee
        self.s = 1 if ep.side == 'long' else -1
        self.risk = abs(ep.entry-ep.stop)
        self.current, self.best, self.worst = ep.stop, ep.entry, ep.entry
        self.k, self.pending, self.done, self.result = 0, False, False, None
        self.last = min(len(ep.c), ep.max_bars)-1

    def _finish(self, price, reason, at_open=False):
        fill = price*(1-self.s*self.slip)
        net = self.s*(fill-self.ep.entry)/self.ep.entry-2*self.fee
        self.done = True
        self.result = dict(reason=reason, exit_index=self.k, exit=fill, net=net, r=net/(self.risk/self.ep.entry),
                           mfe_r=self.s*(self.best-self.ep.entry)/self.risk, at_open=at_open)

    def state(self):
        e, r, s = self.ep.entry, self.risk, self.s
        return np.array([s*(self.ep.c[self.k]-e)/r, s*(self.best-e)/r, s*(self.worst-e)/r, (self.k+1)/self.ep.max_bars,
                         s*(self.ep.target-self.ep.c[self.k])/r, s*(self.current-e)/r], np.float32)

    def advance(self):
        ep, s, k = self.ep, self.s, self.k
        o, h, l = ep.o[k], ep.h[k], ep.l[k]
        if k > 0 and self.pending:
            return self._finish(o, 'EXIT', at_open=True)
        if k > 0 and s*(self.current-o) >= 0:
            self._extremes(h, l)
            return self._finish(o, 'STOP', at_open=True)
        self._extremes(h, l)
        if (l <= self.current) if s == 1 else (h >= self.current):
            return self._finish(self.current, 'STOP')
        if (h >= ep.target) if s == 1 else (l <= ep.target):
            return self._finish(ep.target, 'TARGET')
        return self.state()

    def _extremes(self, h, l):
        self.best, self.worst = (max(self.best, h), min(self.worst, l)) if self.s == 1 else (min(self.best, l), max(self.worst, h))

    def act(self, action):
        s, e = self.s, self.ep.entry
        if action == 'exit':
            self.pending = True
        elif action == 'breakeven':
            level = e*(1+s*(2*self.fee+2*self.slip))
            if s*(level-self.current) > 0:
                self.current = level
        elif action == 'trail':
            level = self.best-s*self.risk
            if s*(level-self.current) > 0:
                self.current = level
        if self.k >= self.last:
            return self._finish(self.ep.c[self.k], 'TIME')
        self.k += 1


def play(ep, policy, slippage=SLIPPAGE, fee=FEE):
    trade = Trade(ep, slippage, fee)
    while not trade.done:
        state = trade.advance()
        if trade.done:
            break
        trade.act(policy(state))
    return trade.result


def fixed_rule(state):
    """The registered default: move the stop to break-even once the trade has gone +1R."""
    return 'breakeven' if state[1] >= 1 else 'hold'


def _key(state):
    u, mfe, _, held, _, stop = state
    return (int(np.digitize(u, [-.5, 0, .5, 1, 1.5, 2, 3])), int(np.digitize(mfe, [.5, 1, 1.5, 2, 3])),
            int(np.digitize(held, [.25, .5, .75])), int(stop >= 0))


def train_tabular(episodes, epochs=20, seed=0, alpha=.1, epsilon=.1):
    """Every-visit Monte-Carlo control: Q(state bin, action) moves toward each episode's final R."""
    rng = np.random.default_rng(seed)
    q = {}
    for _ in range(epochs):
        for i in rng.permutation(len(episodes)):
            visited = []
            def policy(state):
                key = _key(state)
                values = q.setdefault(key, np.zeros(len(ACTIONS)))
                a = int(rng.integers(len(ACTIONS))) if rng.random() < epsilon else int(np.argmax(values))
                visited.append((key, a))
                return ACTIONS[a]
            result = play(episodes[i], policy)
            for key, a in visited:
                q[key][a] += alpha*(result['r']-q[key][a])
    return q


def tabular_policy(q):
    def policy(state):
        values = q.get(_key(state))
        return 'hold' if values is None or not np.any(values) else ACTIONS[int(np.argmax(values))]
    return policy


class ManageEnv:
    """Gymnasium environment over episodes; reward is the final net R, 0 on intermediate steps."""

    def __new__(cls, *args, **kwargs):
        import gymnasium as gym
        from gymnasium import spaces

        class _Env(gym.Env):
            metadata = {'render_modes': []}

            def __init__(self, episodes):
                super().__init__()
                self.episodes = list(episodes)
                self.observation_space = spaces.Box(-50.0, 50.0, (len(STATE),), np.float32)
                self.action_space = spaces.Discrete(len(ACTIONS))
                self.trade = None

            def reset(self, *, seed=None, options=None):
                super().reset(seed=seed)
                while True:
                    self.trade = Trade(self.episodes[int(self.np_random.integers(len(self.episodes)))])
                    state = self.trade.advance()
                    if not self.trade.done:
                        return np.clip(state, -50, 50).astype(np.float32), {}

            def step(self, action):
                self.trade.act(ACTIONS[int(action)])
                state = None if self.trade.done else self.trade.advance()
                if self.trade.done:
                    return np.zeros(len(STATE), np.float32), float(self.trade.result['r']), True, False, {}
                return np.clip(state, -50, 50).astype(np.float32), 0.0, False, False, {}

        return _Env(*args, **kwargs)


def train_ppo(episodes, folder, timesteps=150_000, seed=0):
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    model = PPO('MlpPolicy', ManageEnv(episodes), n_steps=512, batch_size=128, learning_rate=3e-4, gamma=1.0,
                policy_kwargs=dict(net_arch=[32, 32]), seed=seed, verbose=0, device='cpu')
    model.learn(total_timesteps=timesteps, callback=CheckpointCallback(save_freq=50_000, save_path=str(folder),
                                                                       name_prefix='ppo'))
    path = folder/'ppo.zip'
    model.save(path)
    return path


def ppo_policy(path):
    from stable_baselines3 import PPO
    model = PPO.load(str(path), device='cpu')
    return lambda state: ACTIONS[int(model.predict(np.asarray(state, np.float32), deterministic=True)[0])]


def compare(episodes, policy, reps=BOOTSTRAP, seed=0):
    """Per-episode net R of `policy` against the fixed rule, with a paired bootstrap interval."""
    mine = np.array([play(e, policy)['r'] for e in episodes])
    fixed = np.array([play(e, fixed_rule)['r'] for e in episodes])
    diff = mine-fixed
    rng = np.random.default_rng(seed)
    means = rng.choice(diff, (reps, len(diff))).mean(axis=1) if len(diff) else np.array([np.nan])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return dict(episodes=len(episodes), policy_mean_r=float(mine.mean()), fixed_mean_r=float(fixed.mean()),
                difference=dict(mean=float(diff.mean()), low=low, high=high),
                live=bool(len(episodes) >= MIN_TEST and low > 0))


def episodes_from(hits, bars, book):
    """Trade episodes for every tradeable hit with a valid outcome (normal plan)."""
    max_bars = {pb['id']: int(pb['time_stop_hours']*12) for pb in book['playbooks']}
    index = pd.DatetimeIndex(bars.index)
    o, h, l, c = (bars[k].to_numpy(float) for k in ('open', 'high', 'low', 'close'))
    out = []
    for hit in hits[hits.tradeable & (hits.o_valid == True)].itertuples():  # noqa: E712
        i = index.searchsorted(hit.time)
        if i >= len(index) or index[i] != hit.time or i+1 >= len(index):
            continue
        n = max_bars.get(hit.playbook, 144)
        window = slice(i+1, min(len(index), i+1+n))
        out.append(Episode(side=hit.side, entry=float(hit.o_entry), stop=float(hit.plan_stop), target=float(hit.plan_target),
                           o=o[window], h=h[window], l=l[window], c=c[window], max_bars=n, hit_id=hit.id, time=hit.time))
    return out


def run(output, book=None, timesteps=150_000):
    """Train both agents on the run's early trades, test on the late ones; write manage-report.json and modes."""
    from quantpilot.rules import rulebook as rules
    from quantpilot.rules.meta import write_modes
    from quantpilot.research.playbooks import _load, load_hits
    book = book or rules.load()
    hits = load_hits(output)
    bars = _load(output, 'bars')
    episodes = episodes_from(hits, bars, book)
    train = [e for e in episodes if e.time < SPLIT]
    test = [e for e in episodes if e.time >= SPLIT]
    report = dict(generated=pd.Timestamp.now(tz='UTC').isoformat(), train=len(train), test=len(test), agents={})
    if len(train) >= MIN_TEST and len(test) >= MIN_TEST:
        q = train_tabular(train)
        (FOLDER/'manage-q.json').write_text(json.dumps({json.dumps(k): v.tolist() for k, v in q.items()}), encoding='utf-8')
        report['agents']['tabular'] = compare(test, tabular_policy(q))
        path = train_ppo(train, FOLDER/'manage-ppo', timesteps=timesteps)
        report['agents']['ppo'] = compare(test, ppo_policy(path))
    winner = next((name for name in ('tabular', 'ppo') if report['agents'].get(name, {}).get('live')), None)
    playbooks = sorted({h for h in hits[hits.tradeable].playbook}) if len(hits) else []
    report['policy'] = winner
    report['live'] = playbooks if winner else []
    report['summary'] = f'{winner} policy live' if winner else 'kept in shadow (no agent beat the break-even rule)'
    (Path(output)/'manage-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    write_modes('manage_rl', report['live'])
    return report


def live_manager():
    """`state -> action` from the policy that passed its gate (else the tabular policy, for shadow records)."""
    reports = sorted(FOLDER.glob('*/manage-report.json'), key=lambda p: p.stat().st_mtime)
    policy = json.loads(reports[-1].read_text(encoding='utf-8')).get('policy') if reports else None
    if policy == 'ppo' and (FOLDER/'manage-ppo'/'ppo.zip').exists():
        return ppo_policy(FOLDER/'manage-ppo'/'ppo.zip')
    if (FOLDER/'manage-q.json').exists():
        stored = json.loads((FOLDER/'manage-q.json').read_text(encoding='utf-8'))
        return tabular_policy({tuple(json.loads(k)): np.array(v) for k, v in stored.items()})
    return None
