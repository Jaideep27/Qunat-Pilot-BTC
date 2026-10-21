import {useState} from 'react';
import {Card,Collapsible,Empty,Loading,PageHead,Pill,Segmented,Stat,Updated,money,num,pct,signedPct,stamp,useResource} from '../ui';

type Playbook={id:string;name:string;family:string;side:string;source:string[];thesis:string;priority:number;shadow_only:boolean;
  trigger:{any:string[];within_bars:number;min_bars:number};regimes:string[];conditions:string[];abort:string[];
  stop:{anchor:string;buffer_atr:number};target:{candidates:string[];cap:string|null;fallback_r:number};time_stop_hours:number;
  breakeven_at_r:number|null;may_flip:boolean};
type Way={n:number;share:number|null;low:number|null;high:number|null};
type History={setups:number;went_their_way_1h:Way;went_their_way_4h:Way;median_move_4h:number|null;plan:{n:number;win_rate:number|null;mean_r:number|null}}|null;
type Check={playbook:string;status:string;passed:number;total:number;missing:string[];trigger:string;time:string;history?:History;
  layers?:Record<string,{verdict:string;mode:string}>};
const past=(h:History|undefined)=>!h?'—':`${pct(h.went_their_way_4h.share,0)} went its way in 4h · plan won ${pct(h.plan.win_rate,0)}, ${num(h.plan.mean_r)}R (n=${h.setups})`;
type Position={side:number;quantity:number;entry:number;stop:number;target:number;entry_time:string;max_favorable:number;max_adverse:number;plan:{playbook?:string}};
type Account={equity:number;cash:number;balance:number;account_return:number;position:Position|null;pending:{action:string}|null;trades:number;
  halted_day:boolean;drawdown_halt:boolean;paused:boolean;last_bar:string|null;last_decision:string|null;created:string|null};
type Status={rulebook:{hash:string;version:number;name:string};layers:Record<string,string>;modes:Record<string,Record<string,string>>;
  playbooks:Playbook[];account:Account|null;checklist:Check[];provenance_note:string;
  research:{run:string;passing:string[];trials:number;combined:{account_return:number;trades:number;max_drawdown_closed:number}}|null};
type Hit={id:string;time:string;playbook:string;side:string;status:string;diagnosis:string|null;
  payload:{regime?:string;confluence?:{score:number};trigger?:{type:string};plan?:{reward_risk:number|null;stop:number;target:number}|null;reasons?:string[]}};
type Trade={id:string;playbook:string;side:string;entry_time:string;exit_time:string;net:number;reason:string;diagnosis:string|null;
  payload:{entry:number;exit:number;quantity:number;diagnosis?:{why:string}}};
type Journal={hits:Hit[];trades:Trade[];triggers:{id:string;time:string;type:string;payload:{direction:number;strength:number|null}}[]};
type Report={calls:number;hit_rate:number|null;wilson_low:number|null;break_even:number|null;mean_net:number|null};
type Research={status:string;run?:string;trials?:number;hits?:number;passing?:string[];development?:string[];
  playbooks?:Record<string,{name:string;side:string;report:Report;gate:{passed:boolean;criteria:Record<string,boolean>};hits:number;tradeable:number}>;
  combined?:{report:Report;gate:{passed:boolean};account:{account_return:number;trades:number;max_drawdown_closed:number}};
  layers?:Record<string,{verdict?:string;summary?:string;live?:string[]}>};

const statusTone=(s:string)=>s==='TAKEN'?'ok':s.startsWith('VETO')||s==='GATED'||s==='COOLDOWN'?'warn':s==='PLAN_REJECTED'||s==='NOT_CONFIRMED'?'muted':'accent';
const words=(s:string)=>s.replaceAll('_',' ').toLowerCase();
const side=(n:number|string)=>(n===1||n==='long')?'Long':'Short';

export default function Rules(){
  const status=useResource<Status>('rules',30000);
  const journal=useResource<Journal>('rules/journal?limit=100',60000);
  const research=useResource<Research>('rules/research',300000);
  const [view,setView]=useState<'Hits'|'Trades'|'Triggers'>('Hits');
  const s=status.data,a=s?.account,j=journal.data,r=research.data;
  return <>
    <PageHead title="Rule engine" description="Your rulebook as event-triggered playbooks. When a spike, dump or technical event fires, the matching playbook runs its checklist; a separate experimental paper account acts on it.">
      <Updated resource={status}/>
    </PageHead>
    <div className="banner banner-warn" role="note">Experimental paper account · rulebook v{s?.rulebook.version??1} ({s?.rulebook.hash??'…'}) · nothing here places real orders. The frozen paper account on the Paper page is unchanged.</div>
    <div className="grid">
      <div className="strip span-3" aria-label="Experimental account">
        <Stat label="Experimental equity" value={money(a?.equity)} detail={a?`Started ${stamp(a.created)}`:'Starts with the first engine cycle'}/>
        <Stat label="Return" value={signedPct(a?.account_return)} tone={(a?.account_return??0)<0?'down':undefined} detail="After fees, slippage and funding"/>
        <Stat label="Closed trades" value={num(a?.trades,0)} detail={a?.pending?`Order pending: ${a.pending.action}`:'One position at a time'}/>
        <Stat label="Last decision" value={a?.last_decision?stamp(a.last_decision):'—'} detail="At completed 15m closes"/>
      </div>
      <Card title="Live checklist" label="Live checklist" className="span-2" tag="Latest 15m close">
        {!s?<Loading/>:s.checklist.length?<div className="table-scroll"><table><thead><tr><th>Playbook</th><th>Trigger</th><th>Checks</th><th>Status</th><th>Last times this setup appeared</th><th>Learner</th><th>Missing inputs</th></tr></thead>
          <tbody>{s.checklist.map(c=><tr key={c.playbook}><td><strong>{c.playbook}</strong></td><td>{words(c.trigger)}</td><td>{c.passed}/{c.total}</td>
            <td><Pill tone={statusTone(c.status)}>{words(c.status)}</Pill></td><td>{past(c.history)}</td><td>{c.layers?.learner?words(c.layers.learner.verdict)+' ('+c.layers.learner.mode+')':'—'}</td><td className="muted">{c.missing.join(', ')||'—'}</td></tr>)}</tbody></table></div>
          :<Empty title="No trigger active">Nothing unusual at the last 15m close. Playbooks only run when a trigger fires.</Empty>}
      </Card>
      <Card title="Open position" label="Open position" className="span-1" tag={a?.position?.plan?.playbook??'Flat'}>
        {!s?<Loading/>:a?.position?<dl className="facts">
          <div><dt>Side</dt><dd><Pill tone={a.position.side===1?'ok':'bad'}>{side(a.position.side)}</Pill></dd></div>
          <div><dt>Entry</dt><dd>{money(a.position.entry)}</dd></div><div><dt>Stop</dt><dd>{money(a.position.stop)}</dd></div>
          <div><dt>Target</dt><dd>{money(a.position.target)}</dd></div><div><dt>Since</dt><dd>{stamp(a.position.entry_time)}</dd></div>
          <div><dt>Best / worst</dt><dd>{signedPct(a.position.max_favorable)} / {signedPct(a.position.max_adverse)}</dd></div></dl>
          :<Empty title="No open position">{a?.halted_day?'Daily loss halt until the next UTC day.':a?.drawdown_halt?'10% drawdown halt: owner review needed.':'Waiting for a playbook to confirm.'}</Empty>}
      </Card>
      <Card title="Playbooks" label="Playbooks" className="span-3" tag={`${s?.playbooks.length??0} in rulebook`}>
        {!s?<Loading/>:<div className="table-scroll"><table><thead><tr><th>ID</th><th>Playbook</th><th>Side</th><th>Trigger</th><th>Development trades</th><th>Hit rate</th><th>Mean net</th><th>Verdict</th></tr></thead>
          <tbody>{s.playbooks.map(p=>{const res=r?.playbooks?.[p.id];return <tr key={p.id}><td><strong>{p.id}</strong></td>
            <td><Collapsible title={p.name} summary={p.thesis}>
              <p className="note">From {p.source.join(', ')} · regimes {p.regimes.join(', ')} · priority {p.priority}{p.may_flip?' · may flip':''}{p.shadow_only?' · shadow only':''}</p>
              <ul className="gates">{p.conditions.map(c=><li key={c}><span>{c}</span></li>)}</ul>
              <p className="source">Stop {p.stop.anchor} ∓ {p.stop.buffer_atr} ATR · target nearest of {p.target.candidates.join(', ')||'none'} (cap {p.target.cap??'none'}, fallback {p.target.fallback_r}R) · time stop {p.time_stop_hours}h{p.breakeven_at_r?` · break-even at +${p.breakeven_at_r}R`:''}</p>
              {p.abort.length>0&&<p className="source">Abort: {p.abort.join(' · ')}</p>}</Collapsible></td>
            <td>{side(p.side)}</td><td>{p.trigger.any.map(words).join(' / ')}</td><td>{res?num(res.report.calls,0):'—'}</td>
            <td>{res?pct(res.report.hit_rate):'—'}</td><td>{res?signedPct(res.report.mean_net,3):'—'}</td>
            <td>{p.shadow_only?<Pill>shadow</Pill>:!res?<Pill>not run</Pill>:res.gate.passed?<Pill tone="ok">passed</Pill>:<Pill tone="bad">failed</Pill>}</td></tr>})}</tbody></table></div>}
      </Card>
      <Card title="Journal" label="Journal" className="span-2" actions={<Segmented label="Journal view" options={['Hits','Trades','Triggers'] as const} value={view} onChange={setView}/>}>
        {!j?<Loading/>:view==='Hits'?(j.hits.length?<div className="table-scroll"><table><thead><tr><th>Time · UTC</th><th>Playbook</th><th>Side</th><th>Status</th><th>R:R</th><th>Diagnosis</th></tr></thead>
          <tbody>{j.hits.map(h=><tr key={h.id}><td>{stamp(h.time)}</td><td>{h.playbook}</td><td>{side(h.side)}</td><td><Pill tone={statusTone(h.status)}>{words(h.status)}</Pill></td>
            <td>{num(h.payload.plan?.reward_risk)}</td><td>{h.diagnosis?words(h.diagnosis):<span className="muted">pending</span>}</td></tr>)}</tbody></table></div>
          :<Empty title="No confirmed setups yet">Every confirmed playbook hit is journalled here, taken or skipped, with the reason.</Empty>)
        :view==='Trades'?(j.trades.length?<div className="table-scroll"><table><thead><tr><th>Exit · UTC</th><th>Playbook</th><th>Side</th><th>Net</th><th>Exit reason</th><th>Diagnosis</th></tr></thead>
          <tbody>{j.trades.map(t=><tr key={t.id}><td>{stamp(t.exit_time)}</td><td>{t.playbook}</td><td>{side(t.side)}</td><td className={t.net<0?'tone-down':'tone-up'}>{money(t.net)}</td>
            <td>{words(t.reason)}</td><td title={t.payload.diagnosis?.why}>{t.diagnosis?words(t.diagnosis):<span className="muted">after 4h</span>}</td></tr>)}</tbody></table></div>
          :<Empty title="No closed trades yet">Closed trades appear with their diagnosis 4 hours after the exit.</Empty>)
        :(j.triggers.length?<ul className="audit">{j.triggers.slice(0,40).map(t=><li key={t.id}><strong>{words(t.type)}</strong><span className="muted">{stamp(t.time)}</span>
            <span>{t.payload.strength!=null?`move ${num(t.payload.strength,1)}σ`:''}</span></li>)}</ul>:<Empty title="No triggers recorded">Anomalies and technical events appear as they happen.</Empty>)}
      </Card>
      <Card title="Decision layers" label="Decision layers" className="span-1" tag="Earn influence by test">
        {!s?<Loading/>:<ul className="gates">{Object.entries(s.layers).map(([name,mode])=>{const live=Object.entries(s.modes[name]??{}).filter(([,m])=>m==='live').map(([k])=>k);
          return <li key={name} className={live.length?'ok':''}><strong>{({ml:'ML filter',news:'News check',learner:'Learner (RL)',manage_rl:'Trade-management RL'} as Record<string,string>)[name]??name}</strong>
            <span>{live.length?`Live for ${live.join(', ')}`:`${mode} for every playbook`}{r?.layers?.[name]?.summary?` · ${r.layers[name].summary}`:''}</span></li>})}</ul>}
        <p className="source">A layer acts only where its pre-registered test showed it improves on the playbook alone; otherwise its verdicts are recorded in shadow.</p>
      </Card>
      <Card title="Historical test (amendment A6)" label="Historical test" className="span-3" tag={r?.run??'not run'}>
        {!r?<Loading/>:r.status!=='complete'?<Empty title="Not run yet">Run <code>quantpilot research-playbooks --output DIR</code>.</Empty>:<>
          <div className="strip">
            <Stat label="Combined account" value={signedPct(r.combined?.account.account_return)} tone={(r.combined?.account.account_return??0)<0?'down':undefined} detail={`${num(r.combined?.account.trades,0)} trades · max drawdown ${signedPct(r.combined?.account.max_drawdown_closed)}`}/>
            <Stat label="Passing playbooks" value={num(r.passing?.length,0)} detail={r.passing?.join(', ')||'None passed the section 7 rule'}/>
            <Stat label="Confirmed hits" value={num(r.hits,0)} detail={`Development ${r.development?.map(d=>d.slice(0,10)).join(' → ')}`}/>
            <Stat label="Trials counted" value={num(r.trials,0)} detail="Every intraday configuration ever tested"/>
          </div>
          <p className="note">Pre-registered before evaluation, scored after fees, slippage (3× around spikes) and funding. The holdout year is untouched.</p></>}
      </Card>
    </div>
  </>;
}
