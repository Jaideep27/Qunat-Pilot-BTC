import {useMemo,useState} from 'react';
import {Check,Minus,X} from 'lucide-react';
import {Line,LineChart,ReferenceLine,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {Badge,ExplainBox,STATUS_TONE,isSnapshot,type Check as CheckItem,type History,type Playbook,type Snapshot,type StyleView} from '../components/analysis';
import {Collapsible,Empty,Loading,PageHead,ago,money,pct,signedPct,useResource} from '../components/ui';

function Checklist({items,label}:{items:CheckItem[];label:string}){
  if(!items.length)return null;
  return <ul className="checklist" aria-label={label}>{items.map((c,i)=><li key={i} className={c.ok==null?'':c.ok?'ok':'no'}>
    {c.ok==null?<Minus size={14} aria-hidden/>:c.ok?<Check size={14} aria-label="yes"/>:<X size={14} aria-label="no"/>}
    <span>{c.label}</span><small>{c.value}</small></li>)}</ul>;
}

function Versus({label,value,base,baseLabel}:{label:string;value:number|null;base:number|null;baseLabel:string}){
  const top=Math.max(Math.abs(value??0),Math.abs(base??0),.0001);
  const w=(v:number|null)=>Math.max(2,Math.abs(v??0)/top*100)+'%';
  return <div className="versus" aria-label={label}>
    <div><span>{label}</span><i className={(value??0)>=0?'bar-up':'bar-down'} style={{width:w(value)}}/><strong>{signedPct(value,1)}</strong></div>
    <div><span>{baseLabel}</span><i className="bar-base" style={{width:w(base)}}/><strong>{signedPct(base,1)}</strong></div>
  </div>;
}

function HistoryChart({h}:{h:History}){
  return <div className="history">
    <div className="history-head"><Badge tone={h.passed?'up':h.account_return!=null&&h.account_return>0?'neutral':'down'}>{h.verdict}</Badge>
      <small className="muted">{h.start} → {h.end} · {h.trades} trades</small></div>
    {h.curve.length>1&&<div className="history-chart" role="img" aria-label="Account value over the history test">
      <ResponsiveContainer width="100%" height={120}><LineChart data={h.curve} margin={{top:6,right:6,bottom:0,left:0}}>
        <XAxis dataKey="date" hide/><YAxis domain={['auto','auto']} width={52} tickFormatter={(v:number)=>'$'+Math.round(v/100)/10+'k'} tick={{fontSize:10,fill:'#8a97ab'}}/>
        <ReferenceLine y={10000} stroke="#5f6b7e" strokeDasharray="3 3"/>
        <Tooltip formatter={v=>money(Number(v),0)} labelStyle={{color:'#0f141d'}}/>
        <Line type="monotone" dataKey="equity" dot={false} strokeWidth={1.6} stroke={(h.account_return??0)>=0?'#4bd6ad':'#f07e82'}/>
      </LineChart></ResponsiveContainer></div>}
    <Versus label="This strategy" value={h.account_return} base={h.buy_and_hold} baseLabel="Holding BTC"/>
    <div className="history-stats">
      <span>Won <strong>{pct(h.win_rate,0)}</strong>{h.break_even!=null&&<> · needed <strong>{pct(h.break_even,0)}</strong></>}</span>
      <span>Worst drop <strong>{pct(h.max_drawdown,1)}</strong></span>
      {h.profit_factor!=null&&<span>Profit factor <strong>{h.profit_factor.toFixed(2)}</strong></span>}
    </div>
  </div>;
}

function StyleCard({s}:{s:StyleView}){
  return <article className="strat-card" aria-label={'Strategy '+s.name}>
    <header><h3>{s.name}</h3><Badge tone={STATUS_TONE[s.status]??s.tone}>{s.status}</Badge></header>
    <p className="strat-says">{s.says}</p>
    {s.levels&&<p className="muted small-print">Turns up above {money(s.levels.turns_up_above,0)} · turns down below {money(s.levels.turns_down_below,0)} (next daily close)</p>}
    <Checklist items={s.checklist} label={s.name+' checklist'}/>
    <Collapsible title="How it works"><p>{s.what}</p></Collapsible>
    {s.history&&<Collapsible title="History test" summary={s.history.verdict}><HistoryChart h={s.history}/></Collapsible>}
    <ExplainBox kind="strategy" refId={s.id}/>
  </article>;
}

function PlaybookCard({p}:{p:Playbook}){
  const firing=p.versions.some(v=>v.status==='firing');
  return <article className="strat-card" aria-label={'Setup '+p.name}>
    <header><h3><span className="muted">{p.id}</span> {p.name}</h3>{firing?<Badge tone="up">firing</Badge>:<Badge tone="quiet">quiet</Badge>}</header>
    <p className="strat-says">{p.what}</p>
    {p.versions.map(v=><Collapsible key={v.id} title={`${v.side==='long'?'Buy':'Sell'} version (${v.id})`}
      summary={<><Badge tone={STATUS_TONE[v.status]??v.tone}>{v.status}</Badge> {v.passed}/{v.total}</>}>
      <p className="muted small-print">Wakes up on {v.trigger.wakes}{v.trigger.seen&&<> · last seen {ago(v.trigger.seen)}</>}{v.shadow&&' · shadow only (never traded)'}</p>
      <Checklist items={v.checklist} label={v.id+' checklist'}/>
      <p className="muted small-print">Plan: {v.stop_target}</p>
      {v.history&&<p className="small-print">History (2021–2025): {v.history.setups} setups · the plan won {pct(v.history.win_rate,0)}
        {v.history.mean_r!=null&&<> · average {v.history.mean_r>0?'+':''}{v.history.mean_r.toFixed(2)} R</>}</p>}
    </Collapsible>)}
    <ExplainBox kind="strategy" refId={p.id}/>
  </article>;
}

export default function Strategies(){
  const snap=useResource<Snapshot|null>('analysis/snapshot',60000);
  const [q,setQ]=useState('');
  const s=isSnapshot(snap.data)?snap.data:null;
  const playbooks=useMemo(()=>(s?.strategies.rulebook.playbooks??[]).filter(p=>
    !q.trim()||(p.id+' '+p.name+' '+p.what).toLowerCase().includes(q.trim().toLowerCase())),[s,q]);
  if(snap.loading&&snap.data===undefined)return <Loading lines={6}/>;
  if(!s)return <div className="page-narrow"><PageHead title="Strategies" description="What each strategy says now, and how it did in testing."/>
    <Empty title="No readings yet">The analysis service builds a snapshot every 5 minutes.</Empty></div>;
  const rb=s.strategies.rulebook;
  return <div className="page-narrow">
    <PageHead title="Strategies" description="What each strategy says right now, why, and how it did in testing. Analysis only: nothing trades."/>
    <section className="ind-group" aria-label="Strategies">
      <h2>The strategies <small className="muted">· updated {ago(s.built_at)}</small></h2>
      <div className="strat-grid">{s.strategies.styles.map(st=><StyleCard key={st.id} s={st}/>)}</div>
    </section>
    <section className="ind-group" aria-label="Your rulebook">
      <h2>Your rulebook: 13 setups <small className="muted">· market type {rb.regime.replace('_',' ').toLowerCase()}</small></h2>
      <p className="muted">{rb.verdict}</p>
      {rb.firing.length>0&&<p className="strat-says">Firing now: {rb.firing.map(f=>`${f.id} ${f.name}`).join(', ')}.</p>}
      <input className="search-input" aria-label="Search setups" placeholder="Search setups, e.g. sweep, squeeze, funding" value={q} onChange={e=>setQ(e.target.value)}/>
      <div className="strat-grid">{playbooks.map(p=><PlaybookCard key={p.id} p={p}/>)}</div>
      {!playbooks.length&&<Empty title="No setup matches"/>}
    </section>
  </div>;
}
