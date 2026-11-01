import {useMemo,useState} from 'react';
import {post,publicView} from '../lib/api';
import type {Route} from '../app/routes';
import {Card,Collapsible,Empty,Loading,Meter,PageHead,Pill,money,num,pct,signedPct,stamp,useResource} from '../components/ui';

type TimeMachine={paths:number;days:number;stop_first:number|null;target_first:number|null;neither:number|null;median_r:number|null};
type Position={side:string;quantity:number;entry:number;stop:number|null;target:number|null;liquidation:number|null;leverage:number|null;entry_time:string;playbook:string;
  reason?:string;time_machine?:TimeMachine|null;kind?:string;spot_qty?:number;perp_qty?:number;margin?:number;funding?:number;rebalances?:number};
type HistoryTest={status:'running'|'passed'|'failed';passed?:boolean;failed?:string[];trades?:number;win_rate?:number|null;account_return?:number;
  max_drawdown?:number;buy_and_hold?:number|null;annual_return?:number|null};
type Report={trades:number;wins:number;win_rate:number|null;account_return:number;avg_win:number|null;avg_loss:number|null;
  profit_factor:number|null;max_drawdown:number;longest_losing_streak:number;hold_return?:number};
export type Bot={id:string;name:string;status:string;equity:number;account_return:number;round:number;in_round:number;round_length:number;round_unit?:string;
  halted_day:boolean;position:Position|null;pending:boolean;review:{id:string;round:number;status:string}|null;rules:string[];report?:Report|null;
  style?:string;active?:{on:boolean;reason:string}|null;history_test?:HistoryTest;
  members?:{regime:string|null;vote:number;leaders:string[];hedge:boolean}|null};
type Bots={accounts:Bot[]};
type Decision={id:string;account:string;trade_id:string|null;payload:{sizing?:{risk_amount?:number}|null;reason?:string;time_machine?:TimeMachine|null}};
type Trade={id:string;account:string;number:number;playbook:string;side:string;entry_time:string;exit_time:string;net:number;reason:string;
  diagnosis:string|null;payload:{entry?:number;exit?:number;diagnosis?:{why?:string}};explanation:{why:string;lesson:string}|null};
type Review={id:string;account:string;round:number;created_at:string;payload:{
  numbers:{trades:number;wins:number;win_rate:number|null;total_net:number;max_drawdown:number;hold_return?:number|null};
  summary:{where_it_went_wrong:string;why:string;suggestions:string[]}|null}};

const words=(s:string)=>s.replaceAll('_',' ').toLowerCase();
const list=<T,>(v:T[]|undefined|null):T[]=>Array.isArray(v)?v:[];
const STYLE:Record<string,string>={X1:'Listens to every strategy · one trade at a time',X2:'Follows the daily trend · one trade at a time',
  T1:'Trend · holds days to weeks',R1:'Sideways markets · holds up to 4 hours',B1:'Breakouts · holds up to a day',C1:'Earns funding · no bet on direction'};
const MEMBER:Record<string,string>={T1:'Trend Rider',R1:'Range Trader',B1:'Breakout Hunter'};
const SHORT:Record<string,string>={'acct-combined':'Combined','acct-trend-bot':'Trend-led','acct-trend':'Trend','acct-range':'Range',
  'acct-breakout':'Breakout','acct-carry':'Funding'};
const CRITERIA:Record<string,string>={enough_trades:'too few trades',beats_break_even:'wins not above break-even',positive_mean:'loses on average',
  deflated_sharpe:'could be luck',most_years_positive:'not positive in most years',not_luck:'could be luck',beats_cash:'did not beat cash'};
export const BOT_KEY='qp:bot';
const remember=(id:string)=>{try{localStorage.setItem(BOT_KEY,id)}catch{/* storage may be unavailable */}};
const recall=()=>{try{return localStorage.getItem(BOT_KEY)}catch{return null}};

type Tone='ok'|'warn'|'bad'|'muted'|'accent';
export function botStatus(b:Bot):[string,Tone]{
  if(b.status==='PAUSED_FOR_REVIEW')return ['Paused for review','warn'];
  if(b.status==='FLOOR_HIT')return ['Stopped below $5,000','bad'];
  if(b.status==='NOT_STARTED')return ['Starting','muted'];
  if(b.halted_day)return ['Resting today','warn'];
  if(b.position)return [b.position.kind==='hedge'?'Earning funding':'In a trade','accent'];
  if(b.pending)return ['Order placed','accent'];
  return b.active&&!b.active.on?['Waiting','muted']:['Watching','ok'];
}

function PositionLine({p}:{p:Position}){
  if(p.kind==='hedge')return <p>Holding {num(p.spot_qty,4)} BTC and an equal short since {stamp(p.entry_time)}. Funding earned so far: <strong>{money(p.funding)}</strong>.</p>;
  return <p><strong>{p.side==='long'?'Long':'Short'} {num(p.quantity,3)} BTC</strong> from {money(p.entry)} · stop {money(p.stop)} ·{' '}
    {p.target==null?'no fixed target':`target ${money(p.target)}`} · since {stamp(p.entry_time)}{p.reason?<><br/><span className="muted">Why: {p.reason}</span></>:null}</p>;
}

function HistoryLine({h}:{h?:HistoryTest}){
  if(!h)return null;
  if(h.status==='running')return <p className="muted">History test (2021–2025): still running.</p>;
  return <p>History test (2021–2025): <strong className={h.passed?'tone-up':'tone-down'}>{h.status}</strong> · account {signedPct(h.account_return)}
    {h.annual_return!=null?` (${signedPct(h.annual_return)} a year)`:''} · worst drop {signedPct(h.max_drawdown)}
    {h.failed&&h.failed.length?` · ${[...new Set(h.failed.map(f=>CRITERIA[f]??words(f)))].join(', ')}`:''}</p>;
}

function BotCard({b,onResume,onReview,busy}:{b:Bot;onResume:()=>void;onReview:()=>void;busy:boolean}){
  const [label,tone]=botStatus(b);
  const p=b.position,tm=p?.time_machine,m=b.members;
  const days=b.round_unit==='days';
  return <Card title={b.name} label={'Account '+b.name} className="span-3 bot-card" tag={<Pill tone={tone}>{label}</Pill>}>
    <p className="muted">{STYLE[b.style??'']??''}</p>
    <div className="bot-figures">
      <div><span>Money</span><strong>{money(b.equity)}</strong></div>
      <div><span>Return</span><strong className={b.account_return<0?'tone-down':b.account_return>0?'tone-up':''}>{signedPct(b.account_return)}</strong></div>
    </div>
    {b.active&&<p className="bot-now"><span className={'dot '+(b.active.on?'on':'')} aria-hidden/>{b.active.reason}</p>}
    {m&&<p className="muted">Leads now: {m.leaders.length?[...new Set(m.leaders.map(l=>MEMBER[l]??'rulebook setups'))].join(', '):'nobody yet'} · daily trend {m.vote===1?'up':m.vote===-1?'down':'unclear'}
      {m.hedge?' · idle money is earning funding':''}</p>}
    {p?<PositionLine p={p}/>:<p className="muted">{b.pending?'An order is placed and waiting to fill.':'No open trade.'}</p>}
    {tm&&tm.paths>0&&<p className="muted">Before entry, 2,000 replays of recent days gave: stop first {pct(tm.stop_first,0)}
      {tm.target_first?` · target first ${pct(tm.target_first,0)}`:''} · neither {pct(tm.neither,0)}. A reality check, not a promise.</p>}
    <div className="account-progress"><span>{days?'Day':'Trade'} {b.in_round} of {b.round_length} before review</span><span className="muted">Round {b.round}</span></div>
    <Meter value={b.in_round} max={b.round_length} tone={b.in_round>=b.round_length?'warn':undefined}/>
    {b.status==='PAUSED_FOR_REVIEW'&&<div className="account-actions">
      <button type="button" className="link" onClick={onReview} disabled={!b.review}>{b.review?'Open review':'Review being written…'}</button>
      {!publicView&&<button type="button" className="button" onClick={onResume} disabled={busy} aria-label={'Resume '+b.name}>Resume next round</button>}</div>}
    <Collapsible title="How it trades" summary={`${list(b.rules).length} rules`} label={b.name+' rules'}>
      <ul className="account-rules">{list(b.rules).map(r=><li key={r}>{r}</li>)}</ul>
      <HistoryLine h={b.history_test}/>
    </Collapsible>
    {b.report&&b.report.trades>0&&<Collapsible title="Results so far" summary={`${b.report.trades} trades`} label={b.name+' results'}>
      <dl className="facts">
        <div><dt>This bot</dt><dd>{signedPct(b.report.account_return)}</dd></div>
        <div><dt>Holding BTC instead</dt><dd>{signedPct(b.report.hold_return)}</dd></div>
        <div><dt>Won</dt><dd>{pct(b.report.win_rate,0)} ({b.report.wins} of {b.report.trades})</dd></div>
        <div><dt>Average win / loss</dt><dd>{money(b.report.avg_win)} / {money(b.report.avg_loss)}</dd></div>
        <div><dt>Worst drop</dt><dd>{signedPct(b.report.max_drawdown)}</dd></div>
      </dl></Collapsible>}
  </Card>;
}

function ReviewPanel({review,name}:{review:Review;name:string}){
  const n=review.payload.numbers,s=review.payload.summary;
  return <Card title={`${name} · round ${review.round} review`} label="Round review" className="span-3" tag={stamp(review.created_at)}>
    <p>{n.trades} trades · won {n.wins} ({pct(n.win_rate,0)}) · net <strong className={n.total_net<0?'tone-down':'tone-up'}>{money(n.total_net)}</strong>
      {' '}· worst drop {signedPct(n.max_drawdown)} · holding BTC {signedPct(n.hold_return)}</p>
    {s?<div className="review-summary"><h3>Where it went wrong</h3><p>{s.where_it_went_wrong}</p><h3>Why</h3><p>{s.why}</p>
      <h3>Suggestions (yours to decide)</h3><ul>{s.suggestions.map(x=><li key={x}>{x}</li>)}</ul></div>
      :<p className="muted">The written summary is not ready yet.</p>}
  </Card>;
}

function TradeList({trades,decisions}:{trades:Trade[];decisions:Map<string,Decision>}){
  if(!trades.length)return <Empty title="No closed trades yet">Each trade appears here with why it was taken and why it won or lost.</Empty>;
  return <ul className="account-trades">{trades.map(t=>{const d=decisions.get(t.id);return <li key={t.id}>
    <Collapsible title={`${t.playbook==='C1'?'Funding hedge':t.side==='long'?'Long':'Short'} · ${stamp(t.exit_time)}`}
      summary={<><span className={t.net<0?'tone-down':'tone-up'}>{money(t.net)}</span> · {words(t.reason)}</>}>
      <p className="muted">{stamp(t.entry_time)} → {stamp(t.exit_time)} · entry {money(t.payload.entry)} · exit {money(t.payload.exit)}</p>
      {d?.payload.reason&&<p>Why it was taken: {d.payload.reason} · risked {money(d.payload.sizing?.risk_amount)}</p>}
      {t.explanation?<><p>Why it {t.net<0?'lost':'won'}: {t.explanation.why}</p><p className="muted">Lesson: {t.explanation.lesson}</p></>
        :<p className="muted">{t.diagnosis?`${words(t.diagnosis)}. ${t.payload.diagnosis?.why??''}`:'Explained 4 hours after the exit.'}</p>}
    </Collapsible></li>})}</ul>;
}

/** Home: every bot in one short list; a tap opens it on the Bots page. */
export function BotList({go}:{go:(r:Route)=>void}){
  const res=useResource<Bots>('rules/accounts',60000);
  const bots=list(res.data?.accounts);
  const open=(id:string)=>{remember(id);go('bots')};
  return <Card title="Your bots" label="Your bots" className="span-3" actions={<button type="button" className="link" onClick={()=>go('bots')}>Open</button>}>
    {!res.data?<Loading lines={3}/>:!bots.length?<p className="muted">The bots start with the next engine cycle.</p>:
      <ul className="bot-list">{bots.map(b=>{const [label,tone]=botStatus(b);return <li key={b.id}>
        <button type="button" onClick={()=>open(b.id)} aria-label={`Open ${b.name}`}>
          <span className="bot-name"><strong>{b.name}</strong><small className="muted">{b.active?.reason??STYLE[b.style??'']}</small></span>
          <span className="bot-money"><strong>{money(b.equity)}</strong><small className={b.account_return<0?'tone-down':b.account_return>0?'tone-up':'muted'}>{signedPct(b.account_return)}</small></span>
          <Pill tone={tone}>{label}</Pill>
        </button></li>})}</ul>}
    <p className="source">Paper money only. Each bot started with $10,000.</p>
  </Card>;
}

export default function BotsPage(){
  const res=useResource<Bots>('rules/accounts',30000);
  const bots=list(res.data?.accounts);
  const [chosen,setChosen]=useState<string|null>(recall);
  const bot=bots.find(b=>b.id===chosen)??bots[0];
  const trades=useResource<{trades:Trade[]}>(bot?`rules/account-trades?account=${bot.id}&limit=50`:'rules/account-trades?limit=1',60000);
  const decisions=useResource<{decisions:{id:string;account:string;trade_id:string|null;payload:Decision['payload']}[]}>('rules/decisions?limit=500',60000);
  const reviews=useResource<{reviews:Review[]}>('rules/reviews',60000);
  const [open,setOpen]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState<string|null>(null);
  const byTrade=useMemo(()=>new Map(list(decisions.data?.decisions).filter(d=>d.trade_id).map(d=>[d.trade_id!,d as Decision])),[decisions.data]);
  const pick=(id:string)=>{setChosen(id);remember(id);setOpen(null)};
  const resume=async(b:Bot)=>{setBusy(true);setMessage(null);
    try{await post(`rules/accounts/${b.id}/resume`,{});setMessage(`${b.name}: round ${b.round+1} started.`);res.reload()}
    catch(e){setMessage(`${b.name}: ${(e as Error).message}`)}finally{setBusy(false)}};
  const review=list(reviews.data?.reviews).find(r=>r.id===open);
  return <div className="page-narrow">
    <PageHead title="Bots" description="Six paper bots, $10,000 each. Pick one to see what it is doing and why."/>
    {message&&<div className="banner" role="status">{message}</div>}
    <div className="grid">
      {!res.data?<Card title="Bots" className="span-3"><Loading/></Card>:<>
        <div className="bot-picker span-3" role="group" aria-label="Choose a bot">{bots.map(b=>
          <button key={b.id} type="button" aria-pressed={b.id===bot?.id} className={b.id===bot?.id?'on':''} onClick={()=>pick(b.id)}>{SHORT[b.id]??b.name}</button>)}</div>
        {bot&&<BotCard b={bot} busy={busy} onResume={()=>resume(bot)} onReview={()=>setOpen(bot.review?.id??null)}/>}
        {review&&bot&&<ReviewPanel review={review} name={bot.name}/>}
        {bot&&<Card title="Trades" label="Account trades" className="span-3">
          {!trades.data?<Loading/>:<TradeList trades={[...list(trades.data.trades)].filter(t=>t.account===bot.id)} decisions={byTrade}/>}
        </Card>}
      </>}
    </div>
  </div>;
}
