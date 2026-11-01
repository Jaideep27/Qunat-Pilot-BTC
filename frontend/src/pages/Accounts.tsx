import {useMemo,useState} from 'react';
import {post,publicView} from '../lib/api';
import {Card,Collapsible,Empty,Loading,Meter,Pill,Segmented,money,num,pct,signedPct,stamp,useResource} from '../components/ui';

type TimeMachine={paths:number;days:number;stop_first:number|null;target_first:number|null;neither:number|null;median_r:number|null;
  history?:{available:boolean;trades?:number;win_rate?:number|null;note?:string}};
type Position={side:string;quantity:number;entry:number;stop:number|null;target:number|null;liquidation:number|null;leverage:number|null;entry_time:string;playbook:string;
  score:number|null;style?:string;reason?:string;time_machine?:TimeMachine|null;kind?:string;spot_qty?:number;perp_qty?:number;margin?:number;funding?:number;rebalances?:number};
type HistoryTest={status:'running'|'passed'|'failed';passed?:boolean;failed?:string[];trades?:number;win_rate?:number|null;account_return?:number;
  max_drawdown?:number;buy_and_hold?:number|null;annual_return?:number|null;note?:string};
type Last={time:string;hit_id:string;playbook:string;side:string;decision:string;score:number|null;cutoff:number;confidence:number|null;passed:number;total:number};
type Account={id:string;name:string;status:string;equity:number;balance:number;account_return:number;round:number;in_round:number;round_length:number;number:number;
  leverage:number;risk:number|null;percentile:number|null;cutoff_score:number|null;full_risk_score:number|null;daily_brake:number|null;halted_day:boolean;floor_hit:boolean;
  position:Position|null;pending:boolean;last:Last|null;expectations:{trades_per_week:number;win_rate:number|null;mean_r:number|null}|null;
  review:{id:string;round:number;status:string}|null;rules:string[];watches:string[];report?:Report|null;
  style?:string;active?:{on:boolean;reason:string}|null;history_test?:HistoryTest;round_unit?:string;
  members?:{regime:string|null;vote:number;leaders:string[];hedge:boolean;range_trader?:string;breakout?:string}|null};
type Report={trades:number;wins:number;win_rate:number|null;account_return:number;avg_win:number|null;avg_loss:number|null;
  profit_factor:number|null;max_drawdown:number;longest_drawdown_hours:number;longest_losing_streak:number;hold_return?:number;vs_hold?:number};
type Accounts={accounts:Account[];config_hash:string;model_hash:string;overlay_time:string|null;note:string;watch_now:{key:string;label:string;value:number|null}[]};
type Tick={condition?:string;text?:string;ok:boolean|null};
type Watch={name:string;value:unknown;ok:boolean|null;note:string};
type Decision={id:string;account:string;hit_id:string;time:string;decision:string;score:number|null;confidence:number|null;cutoff:number;trade_id:string|null;
  payload:{ticks?:Tick[];watch?:Watch[];sizing?:{risk_amount?:number;leverage?:number;quantity?:number}|null;style?:string;reason?:string;time_machine?:TimeMachine|null}};
type Trade={id:string;account:string;round:number;number:number;playbook:string;side:string;entry_time:string;exit_time:string;net:number;reason:string;
  diagnosis:string|null;payload:{entry?:number;exit?:number;diagnosis?:{why?:string}};explanation:{why:string;lesson:string}|null};
type Separator={name:string;effect:number;label:string;winners:number;losers:number;n_winners:number;n_losers:number};
type Review={id:string;account:string;round:number;status:string;created_at:string;payload:{
  numbers:{trades:number;wins:number;win_rate:number|null;total_net:number;mean_r:number|null;max_drawdown:number;profit_factor?:number|null;hold_return?:number|null};separators:Separator[];
  learner:{available:boolean;would_skip?:number};manager:{available:boolean;actual_mean_r?:number|null;policy_mean_r?:number|null;fixed_rule_mean_r?:number|null;hold_mean_r?:number|null};
  summary:{where_it_went_wrong:string;why:string;suggestions:string[]}|null;summary_error?:string|null}};

type Health={overall:'OK'|'WATCH'|'ALARM'|'UNKNOWN';reasons:string[];reference:string;checked_at:string;
  card:{model_hash:string;matches:boolean;kind:string;inputs:number;training_setups:number|null;trained_from:string;trained_until:string|null;
    walk_forward:{n:number;predicted:number;actual:number}[]|null;dev_brier:number|null;held_back_in_training:number|null;live_since:string|null;scored_setups:number;holdout:string};
  calibration:{n:number;wins:number;predicted:number|null;actual:number|null;lo:number|null;hi:number|null;brier:number|null;dev_brier:number|null;verdict:string;
    bins:{range:number[];n:number;predicted:number|null;actual:number|null}[]};
  drift:{verdict:string;n:number;window?:number;count?:number|null;usual?:number;top:{input:string;label:string;psi:number;live_median:number|null;train_median:number|null}[]};
  data_checks:{checked_7d:number;failed_7d:number;failed_24h:number;skipped_7d:number;latest:{time:string;setup:string;reasons:string[]}|null};
  challenger:{hash:string;n_dev:number;n_live:number;created:string;compare:{n:number;brier:number|null;challenger_brier:number|null;verdict:string}}|null};

const words=(s:string)=>s.replaceAll('_',' ').toLowerCase();
// accounts-v2 (A10): four different styles
const STYLE:Record<string,string>={X1:'All strategies · one trade at a time',T1:'Trend · holds days to weeks',R1:'Range · holds up to 4 hours',B1:'Breakout · holds up to a day',C1:'Funding · no bet on direction'};
const MEMBER:Record<string,string>={T1:'Trend Rider',R1:'Range Trader',B1:'Breakout Hunter'};
function Members({m}:{m:NonNullable<Account['members']>}){
  return <>
    <p className="account-last">Leads now: {m.leaders.length?m.leaders.map(l=>MEMBER[l]??l).join(', '):'nobody (warming up)'}</p>
    <p className="account-last">Daily trend: {m.vote===1?'up (shorts vetoed)':m.vote===-1?'down (longs vetoed)':'no clear trend (no veto)'}</p>
    {m.hedge&&<p className="account-last">Idle money is earning funding in the hedge until the next trade.</p>}
  </>;
}
const FILTERS={All:'',Bot:'acct-combined',Trend:'acct-trend',Range:'acct-range',Breakout:'acct-breakout',Funding:'acct-carry',A:'acct-a',B:'acct-b',C:'acct-c',D:'acct-d'} as const;
type Filter=keyof typeof FILTERS;
const CRITERIA:Record<string,string>={enough_trades:'too few trades',beats_break_even:'wins not above break-even',positive_mean:'loses on average',
  deflated_sharpe:'could be luck (Sharpe)',most_years_positive:'not positive in most years',not_luck:'could be luck (coin-flip test)',beats_cash:'did not beat cash'};
function HistoryLine({h}:{h?:HistoryTest}){
  if(!h)return null;
  if(h.status==='running')return <p className="source">History test (2021–2025): still running.</p>;
  return <p className="source">History test (2021–2025): <strong className={h.passed?'tone-up':'tone-down'}>{h.status}</strong>
    {' '}· {h.trades} trades{h.win_rate!=null?`, won ${pct(h.win_rate,0)}`:''} · account {signedPct(h.account_return)}{h.annual_return!=null?` (${signedPct(h.annual_return)} a year)`:''}
    {' '}· worst drop {signedPct(h.max_drawdown)}{h.buy_and_hold!=null?` · holding BTC ${signedPct(h.buy_and_hold)}`:''}
    {h.failed&&h.failed.length?` · ${h.failed.map(f=>CRITERIA[f]??words(f)).join(', ')}`:''}</p>;
}
const STATUS:Record<string,[string,'ok'|'warn'|'bad'|'muted'|'accent']>={RUNNING:['Running','ok'],PAUSED_FOR_REVIEW:['Paused for review','warn'],
  FLOOR_HIT:['Stopped: below $5,000','bad'],NO_SCORE_MODEL:['No score model','bad'],NOT_STARTED:['Starting','muted']};
const status=(a:Account):[string,'ok'|'warn'|'bad'|'muted'|'accent']=>a.status==='RUNNING'&&a.halted_day?['Daily brake','warn']
  :a.status==='RUNNING'&&a.position?['In a trade','accent']:STATUS[a.status]??[words(a.status),'muted'];
const decisionTone=(d:string)=>d==='TAKEN'?'ok':d==='BELOW_CUTOFF'||d==='LOWER_SCORE'?'muted':'warn';
const list=<T,>(v:T[]|undefined|null):T[]=>Array.isArray(v)?v:[];
const hours=(h:number)=>h<48?`${num(h,0)} h`:`${num(h/24,1)} days`;
const HEALTH:Record<string,[string,'ok'|'warn'|'bad'|'muted']>={OK:['OK','ok'],WATCH:['Watch','warn'],ALARM:['Alarm','bad'],UNKNOWN:['Unknown','muted']};
const VERDICT:Record<string,string>={TOO_FEW:'too few results yet',IN_LINE:'in line with reality',OVERCONFIDENT:'too optimistic',UNDERCONFIDENT:'too cautious',
  STABLE:'no unusual change',WATCH:'somewhat different',ALARM:'very different',NO_REFERENCE:'reference missing',CHALLENGER_BETTER:'challenger is better',NO_DIFFERENCE:'no clear difference'};
const reason=(r:string)=>{const [kind,col='']=r.split(':');return `${kind} ${col.replace(/^(f|tf)_/,'').replaceAll('_',' ')}`};
function ModelHealth(){
  const res=useResource<Health>('rules/model-health',60000);
  const h=res.data&&Array.isArray(res.data.reasons)?res.data:undefined;
  const [label,tone]=HEALTH[h?.overall??'UNKNOWN']??HEALTH.UNKNOWN;
  if(!h)return <Card title="Model health" label="Model health" className="span-3"><Loading lines={2}/></Card>;
  const c=h.calibration,d=h.drift,k=h.data_checks,m=h.card,ch=h.challenger;
  return <Card title="Model health" label="Model health" className="span-3" tag={<Pill tone={tone}>{label}</Pill>}>
    <p className="muted">Checks on the score model that gives each setup its win chance. They only watch and warn; nothing here changes how the accounts trade.</p>
    <ul className="reasons">{h.reasons.map(r=><li key={r}>{r}</li>)}</ul>
    <Collapsible title="Is it still right?" summary={`${c.n} setups with a result · ${VERDICT[c.verdict]??words(c.verdict)}`}>
      <dl className="facts">
        <div><dt>Average predicted win chance</dt><dd>{pct(c.predicted,0)}</dd></div>
        <div><dt>Actually won</dt><dd>{c.n?`${pct(c.actual,0)} (${c.wins} of ${c.n}; likely range ${pct(c.lo,0)}–${pct(c.hi,0)})`:'—'}</dd></div>
        <div><dt>Accuracy score (Brier, lower is better)</dt><dd>{num(c.brier,3)} live · {num(c.dev_brier,3)} in testing</dd></div>
      </dl>
      <p className="source">Judged after 30 finished setups. Too optimistic raises an alarm.</p>
    </Collapsible>
    <Collapsible title="Has the market changed?" summary={VERDICT[d.verdict]??words(d.verdict)}>
      {d.verdict==='TOO_FEW'?<p className="muted">{d.n} of {d.window??50} recorded setups so far; the check starts at {d.window??50}.</p>
        :d.verdict==='NO_REFERENCE'?<p className="muted">The reference file of the model is missing.</p>
        :<><p>{d.count} inputs look unusual; an ordinary stretch of the past had up to {num(d.usual,0)}.</p>
          <dl className="facts">{d.top.map(t=><div key={t.input}><dt>{t.label}</dt><dd>now {num(t.live_median,4)} · usual {num(t.train_median,4)}</dd></div>)}</dl></>}
    </Collapsible>
    <Collapsible title="Is the data clean?" summary={`${k.failed_7d} of ${k.checked_7d} held back this week`}>
      <p>A setup is held back when an input the model almost always had is missing, or a value is impossible.</p>
      {k.latest?<p>Latest: {stamp(k.latest.time)} · {k.latest.setup} · {k.latest.reasons.map(reason).join(', ')}</p>:<p className="muted">No setup has been held back this week.</p>}
      {k.skipped_7d>0&&<p className="source">{k.skipped_7d} setups this week were scored before the check existed.</p>}
    </Collapsible>
    <Collapsible title="Model record card" summary={m.model_hash}>
      <dl className="facts">
        <div><dt>Model</dt><dd>{m.kind}, {m.inputs} inputs{m.matches?'':' · file does not match the config!'}</dd></div>
        <div><dt>Learned from</dt><dd>{num(m.training_setups,0)} setups, {m.trained_from} → {m.trained_until??'—'}</dd></div>
        <div><dt>Untouched test year</dt><dd>{m.holdout}</dd></div>
        <div><dt>Live since</dt><dd>{m.live_since?stamp(m.live_since):'—'} · {m.scored_setups} setups scored</dd></div>
        <div><dt>Share of past setups the data check would hold back</dt><dd>{pct(m.held_back_in_training,1)}</dd></div>
      </dl>
      {m.walk_forward&&<p className="source">Testing: {m.walk_forward.map(b=>`said ${pct(b.predicted,0)} → won ${pct(b.actual,0)}`).join(' · ')}</p>}
    </Collapsible>
    <Collapsible title="Challenger (needs your OK to swap)" summary={ch?VERDICT[ch.compare.verdict]??words(ch.compare.verdict):'none yet'}>
      {!ch?<p className="muted">No challenger yet. Once 200 new live setups have results, a retrained model can run beside this one in practice mode. It never trades and is only swapped in with your approval.</p>
        :<dl className="facts">
          <div><dt>Trained on</dt><dd>{ch.n_dev} past + {ch.n_live} live setups ({stamp(ch.created)})</dd></div>
          <div><dt>Accuracy score (lower is better)</dt><dd>live {num(ch.compare.brier,3)} · challenger {num(ch.compare.challenger_brier,3)} on {ch.compare.n} setups</dd></div>
        </dl>}
    </Collapsible>
  </Card>;
}
function ReportCard({r,name}:{r:Report;name:string}){
  return <Collapsible title="Report card" summary={r.trades?`${r.trades} trades`:'no trades yet'} label={name+' report card'}>
    <dl className="facts">
      <div><dt>This account</dt><dd className={r.account_return<0?'tone-down':r.account_return>0?'tone-up':''}>{signedPct(r.account_return)}</dd></div>
      <div><dt>Holding BTC instead</dt><dd>{signedPct(r.hold_return)}</dd></div>
      <div><dt>Win rate</dt><dd>{r.trades?`${pct(r.win_rate,0)} (${r.wins} of ${r.trades})`:'—'}</dd></div>
      <div><dt>Profit factor (won ÷ lost)</dt><dd>{num(r.profit_factor,2)}</dd></div>
      <div><dt>Average win / loss</dt><dd>{money(r.avg_win)} / {money(r.avg_loss)}</dd></div>
      <div><dt>Worst drop</dt><dd>{signedPct(r.max_drawdown)}{r.longest_drawdown_hours?` · longest ${hours(r.longest_drawdown_hours)} below a high`:''}</dd></div>
      <div><dt>Longest losing streak</dt><dd>{r.longest_losing_streak} in a row</dd></div>
    </dl></Collapsible>;
}
const value=(v:number|null)=>v==null?'—':Math.abs(v)>=1000?num(v,0):Math.abs(v)<.01?num(v,5):num(v,3);

function AccountCard({a,watchNow,onReview,onResume,busy}:{a:Account;watchNow:Accounts['watch_now'];onReview:()=>void;onResume:()=>void;busy:boolean}){
  const [label,tone]=status(a);
  const p=a.position;
  const tm=p?.time_machine;
  const days=a.round_unit==='days';
  return <Card title={a.name} label={'Account '+a.name} className="span-1 account-card" tag={<Pill tone={tone}>{label}</Pill>}>
    <p className="account-tier">{a.style?<>{STYLE[a.style]??a.style}{a.risk!=null?` · ${a.leverage}× · risks ${pct(a.risk,1)}`:''}</>
      :<>Best {100-(a.percentile??0)}% of setups · {a.leverage}× · risks {pct(a.risk,1)}</>}</p>
    {a.active&&<p className="account-last"><Pill tone={a.active.on?'ok':'muted'}>{a.active.on?'On':'Off'}</Pill> {a.active.reason}</p>}
    {a.members&&<Members m={a.members}/>}
    <div className="account-figures">
      <div><span>Equity</span><strong>{money(a.equity)}</strong></div>
      <div><span>Return</span><strong className={a.account_return<0?'tone-down':a.account_return>0?'tone-up':''}>{signedPct(a.account_return)}</strong></div>
    </div>
    <div className="account-progress"><span>{days?'Day':'Trade'} {a.in_round} / {a.round_length}</span><span className="muted">Round {a.round}</span></div>
    <Meter value={a.in_round} max={a.round_length} tone={a.in_round>=a.round_length?'warn':undefined}/>
    {a.status==='PAUSED_FOR_REVIEW'&&<div className="account-actions">
      <button type="button" className="link" onClick={onReview} disabled={!a.review}>{a.review?'Open review':'Review being written…'}</button>
      {!publicView&&<button type="button" className="button" onClick={onResume} disabled={busy} aria-label={'Resume '+a.name}>Resume next round</button>}</div>}
    {p&&p.kind==='hedge'?<dl className="facts">
      <div><dt>Hedge</dt><dd>{num(p.spot_qty,4)} BTC held · {num(p.perp_qty,4)} BTC short</dd></div>
      <div><dt>Opened at</dt><dd>{money(p.entry)} · {stamp(p.entry_time)}</dd></div>
      <div><dt>Funding earned</dt><dd>{money(p.funding)}</dd></div><div><dt>Margin</dt><dd>{money(p.margin)}{p.rebalances?` · ${p.rebalances} rebalances`:''}</dd></div></dl>
      :p?<dl className="facts">
      <div><dt>{p.playbook} · {p.side}</dt><dd>{num(p.quantity,3)} BTC · {p.leverage}×</dd></div>
      <div><dt>Entry</dt><dd>{money(p.entry)}</dd></div>
      <div><dt>Stop / target</dt><dd>{money(p.stop)} / {p.target==null?'No fixed target':money(p.target)}</dd></div>
      <div><dt>Liquidation</dt><dd>{money(p.liquidation)}</dd></div><div><dt>Since</dt><dd>{stamp(p.entry_time)}</dd></div>
      {p.reason&&<div><dt>Why</dt><dd>{p.reason}</dd></div>}</dl>
      :<p className="muted account-flat">{a.pending?(a.style==='B1'?'A resting order waits for the retest (up to 1 hour).':'Order fills at the next minute.'):'No open trade.'}</p>}
    {tm&&tm.paths>0&&<p className="source">Time Machine ({num(tm.paths,0)} replays of the last {tm.days} days): {tm.target_first?`target first ${pct(tm.target_first,0)} · `:''}stop first {pct(tm.stop_first,0)}
      {' '}· neither {pct(tm.neither,0)} · median {num(tm.median_r,2)} R. A reality check, not a promise.</p>}
    {a.last&&<p className="account-last">Last setup: <strong>{a.last.playbook}</strong> <Pill tone={decisionTone(a.last.decision)}>{words(a.last.decision)}</Pill>{' '}
      score {num(a.last.score,2)} vs {num(a.last.cutoff,2)} · checklist {a.last.passed}/{a.last.total}</p>}
    <Collapsible title="Rules" summary={`${list(a.rules).length} rules`} label={a.name+' rules'} defaultOpen>
      <ul className="account-rules">{list(a.rules).map(r=><li key={r}>{r}</li>)}</ul></Collapsible>
    {list(a.watches).length>0&&<Collapsible title="What it watches" summary="live values" label={a.name+' watches'}>
      <ul className="account-rules">{list(a.watches).map(w=><li key={w}>{w}</li>)}</ul>
      <dl className="facts">{watchNow.map(w=><div key={w.key}><dt>{w.label}</dt><dd>{value(w.value)}</dd></div>)}</dl></Collapsible>}
    {a.report&&<ReportCard r={a.report} name={a.name}/>}
    <HistoryLine h={a.history_test}/>
    {a.expectations&&<p className="source">History (2021–2025): about {num(a.expectations.trades_per_week,1)} trades a week, won {pct(a.expectations.win_rate,0)},
      {' '}{num(a.expectations.mean_r,2)} R per trade.</p>}
  </Card>;
}

function ReviewPanel({review,name}:{review:Review;name:string}){
  const n=review.payload.numbers,s=review.payload.summary,m=review.payload.manager;
  return <Card title={`${name} · round ${review.round} review`} label="Round review" className="span-3" tag={stamp(review.created_at)}>
    <div className="strip">
      <div className="stat"><span className="stat-label">Trades</span><strong>{n.trades}</strong><small>{n.wins} won ({pct(n.win_rate,0)})</small></div>
      <div className="stat"><span className="stat-label">Net</span><strong className={n.total_net<0?'tone-down':'tone-up'}>{money(n.total_net)}</strong><small>{num(n.mean_r,2)} R per trade</small></div>
      <div className="stat"><span className="stat-label">Worst drawdown</span><strong>{signedPct(n.max_drawdown)}</strong>
        <small>Profit factor {num(n.profit_factor,2)} · holding BTC {signedPct(n.hold_return)}</small></div>
      <div className="stat"><span className="stat-label">Trade management</span><strong>{num(m.policy_mean_r,2)} R</strong>
        <small>{m.available?`RL agent vs ${num(m.actual_mean_r,2)} R actual · break-even rule ${num(m.fixed_rule_mean_r,2)} R`:'No replay available'}</small></div>
    </div>
    {s?<div className="review-summary"><h3>Where it went wrong</h3><p>{s.where_it_went_wrong}</p><h3>Why</h3><p>{s.why}</p>
      <h3>Suggestions (yours to decide)</h3><ul>{s.suggestions.map(x=><li key={x}>{x}</li>)}</ul></div>
      :<p className="muted">The written summary is not available yet{review.payload.summary_error?` (${review.payload.summary_error})`:''}; the numbers below are complete.</p>}
    <h3>What separated winners from losers</h3>
    {review.payload.separators.length?<div className="table-scroll"><table><thead><tr><th>Fact</th><th>Winners</th><th>Losers</th><th>Effect</th><th>Strength</th></tr></thead>
      <tbody>{review.payload.separators.map(x=><tr key={x.name}><td>{words(x.name.replace(':',' · '))}</td><td>{value(x.winners)}</td><td>{value(x.losers)}</td>
        <td>{num(x.effect,2)}</td><td><Pill tone={x.label==='consistent'?'accent':'muted'}>{x.label}</Pill></td></tr>)}</tbody></table></div>
      :<Empty title="Nothing to compare">A round needs both winners and losers.</Empty>}
    {review.payload.learner.available&&<p className="source">The bandit learner would have skipped {review.payload.learner.would_skip} of these trades.</p>}
  </Card>;
}

function TradeList({trades,decisions}:{trades:Trade[];decisions:Map<string,Decision>}){
  if(!trades.length)return <Empty title="No closed trades yet">Each trade appears here with why it was taken; why it won or lost follows 4 hours after the exit.</Empty>;
  return <ul className="account-trades">{trades.map(t=>{const d=decisions.get(t.id);return <li key={t.id}>
    <Collapsible title={`#${t.number} · ${t.playbook} · ${t.side}`} summary={<><span className={t.net<0?'tone-down':'tone-up'}>{money(t.net)}</span> · {words(t.reason)} · {t.account.replace('acct-','').toUpperCase()}</>}>
      <p className="muted">{stamp(t.entry_time)} → {stamp(t.exit_time)} · entry {money(t.payload.entry)} · exit {money(t.payload.exit)}</p>
      {d&&d.payload.style&&<><h4>Why it was taken</h4><p>{d.payload.reason} · risked {money(d.payload.sizing?.risk_amount)}</p>
        {d.payload.time_machine&&d.payload.time_machine.paths>0&&<p className="muted">Time Machine before entry: stop first {pct(d.payload.time_machine.stop_first,0)}
          {d.payload.time_machine.target_first?` · target first ${pct(d.payload.time_machine.target_first,0)}`:''} · neither {pct(d.payload.time_machine.neither,0)}</p>}</>}
      {d&&!d.payload.style&&<><h4>Why it was taken</h4><p>Score {num(d.score,2)} (cutoff {num(d.cutoff,2)}) · win chance {pct(d.confidence,0)} · risked {money(d.payload.sizing?.risk_amount)}</p>
        <ul className="checks">{(d.payload.ticks??[]).map((c,i)=><li key={i} className={c.ok?'ok':'bad'}>{c.ok?'✓':'✗'} {c.condition??c.text}</li>)}
          {(d.payload.watch??[]).map(w=><li key={w.name} className={w.ok==null?'':w.ok?'ok':'bad'}>{w.ok==null?'·':w.ok?'✓':'✗'} {words(w.name)} <span className="muted">(watch only)</span></li>)}</ul></>}
      <h4>Why it {t.net<0?'lost':'won'}</h4>
      {t.explanation?<><p>{t.explanation.why}</p><p className="muted">Lesson: {t.explanation.lesson}</p></>
        :<p className="muted">{t.diagnosis?`${words(t.diagnosis)}: ${t.payload.diagnosis?.why??''} The written explanation follows shortly.`:'Diagnosed 4 hours after the exit.'}</p>}
    </Collapsible></li>})}</ul>;
}

export default function AccountsSection(){
  const res=useResource<Accounts>('rules/accounts',30000);
  const [filter,setFilter]=useState<Filter>('All');
  const account=FILTERS[filter]?`?account=${FILTERS[filter]}`:'';
  const trades=useResource<{trades:Trade[]}>('rules/account-trades'+account,60000);
  const decisions=useResource<{decisions:Decision[]}>('rules/decisions?limit=500',60000);
  const reviews=useResource<{reviews:Review[]}>('rules/reviews',60000);
  const [open,setOpen]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState<string|null>(null);
  const byTrade=useMemo(()=>new Map(list(decisions.data?.decisions).filter(d=>d.trade_id).map(d=>[d.trade_id!,d])),[decisions.data]);
  const data=Array.isArray(res.data?.accounts)?res.data:undefined;
  const resume=async(a:Account)=>{setBusy(true);setMessage(null);
    try{await post(`rules/accounts/${a.id}/resume`,{});setMessage(`${a.name}: round ${a.round+1} started.`);res.reload()}
    catch(e){setMessage(`${a.name}: ${(e as Error).message}`)}finally{setBusy(false)}};
  const review=list(reviews.data?.reviews).find(r=>r.id===open);
  const styled=!!data?.accounts.some(a=>a.style);
  const options:Filter[]=styled?['All','Bot','Trend','Range','Breakout','Funding']:['All','A','B','C','D'];
  return <>
    <div className="banner span-3" role="status">{styled
      ?'Five paper accounts, $10,000 each. The Combined Bot listens to every strategy and holds one trade at a time. Beside it, each strategy also trades on its own: Trend Rider follows long trends, Range Trader works sideways markets, Breakout Hunter waits for quiet spells to break, and the Funding Collector earns funding without betting on direction. Each says when it is on or off, and pauses for a review with you. Paper money only.'
      :`Four paper accounts, $10,000 each, from careful to aggressive. Each takes the rulebook's confirmed setups whose score clears its cutoff, sizes by its own risk and leverage, and pauses after ${data?.accounts[0]?.round_length??30} trades for a review. Paper money only.`}
      {message&&<strong> {message}</strong>}</div>
    {!data?<Card title="Paper accounts" className="span-3"><Loading/></Card>
      :data.accounts.map(a=><AccountCard key={a.id} a={a} watchNow={list(data.watch_now)} busy={busy} onResume={()=>resume(a)}
        onReview={()=>setOpen(a.review?.id??null)}/>)}
    {data&&!styled&&<ModelHealth/>}
    {review&&<ReviewPanel review={review} name={data?.accounts.find(a=>a.id===review.account)?.name??review.account}/>}
    <Card title="Account trades" label="Account trades" className="span-3" actions={<Segmented label="Account filter" options={options} value={filter} onChange={setFilter}/>}>
      {!trades.data?<Loading/>:<TradeList trades={list(trades.data.trades)} decisions={byTrade}/>}
    </Card>
  </>;
}

export function AccountsStrip({go}:{go:()=>void}){
  const res=useResource<Accounts>('rules/accounts',60000);
  const health=useResource<Health>('rules/model-health',120000);
  const accounts=list(res.data?.accounts);
  const styled=accounts.some(a=>a.style);
  const [hl,ht]=HEALTH[health.data?.overall??'']??['…','muted'];
  return <Card title="Paper accounts" label="Paper accounts" className="span-3" tag={health.data?.overall&&!styled?<Pill tone={ht}>Model: {hl}</Pill>:undefined}
    actions={<button type="button" className="link" onClick={go}>Details</button>}>
    {!res.data?<Loading lines={2}/>:!accounts.length?<p className="muted">The four accounts start with the next rule-engine cycle.</p>:<div className="strip">{accounts.map(a=>{const [label]=status(a);return <div className="stat" key={a.id}>
      <span className="stat-label">{a.name}{a.style?'':` · ${a.leverage}×`}</span><strong className={a.account_return<0?'tone-down':a.account_return>0?'tone-up':''}>{money(a.equity)}</strong>
      <small>{a.in_round}/{a.round_length} {a.round_unit==='days'?'days':'trades'} · {label.startsWith('Paused')?'Paused':a.active&&!a.active.on&&!a.position?'Off':label}</small></div>})}</div>}
  </Card>;
}
