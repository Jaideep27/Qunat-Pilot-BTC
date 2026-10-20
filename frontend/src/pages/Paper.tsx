import {ShieldCheck} from 'lucide-react';
import type {Decision,Portfolio} from '../api';
import {Card,Empty,Loading,PageHead,Pill,Stat,Term,Updated,money,num,pct,stamp,useResource} from '../ui';

const reasonText:Record<string,string>={WEAK_FORECAST:'Forecast confidence below the 60% entry threshold',
  INSUFFICIENT_EDGE:'Expected edge does not cover fees, slippage and funding',MODEL_RELEASE_BLOCKED:'Historical coverage and frozen model artifacts are required',
  STALE_DATA:'Inputs older than 10 minutes',MISSING_CRITICAL_INPUT:'A required input is missing',DAILY_LOSS_LIMIT:'2% daily loss limit reached',
  POSITION_OPEN:'A position is already open',PAUSED:'Entries paused by the owner',ALL_GATES_PASSED:'Every gate passed'};

export default function Paper(){
  const portfolio=useResource<Portfolio>('portfolio',60000);
  const predictions=useResource<Decision>('predictions',60000);
  const p=portfolio.data,d=predictions.data;
  const reasons=d?.reasons?.length?d.reasons:['ALL_GATES_PASSED'];
  const contributions=d?.explanation?.contributions??[];
  return <>
    <PageHead title="Paper trading" description="A virtual $10,000 USD-M account run by the frozen release model. Every fill, fee and funding payment is recorded.">
      <Updated resource={portfolio}/>
    </PageHead>
    <div className="grid">
      <div className="strip span-3" aria-label="Account">
        <Stat label="Virtual equity" value={money(p?.equity)} detail="Starting balance 10,000 USDT"/>
        <Stat label="Realized P&L" value={money(p?.realized_pnl)} tone={(p?.realized_pnl??0)<0?'down':undefined} detail="Gross, before fees and funding"/>
        <Stat label="Trading fees" value={money(p?.fees)} detail="5 bps per side"/>
        <Stat label="Funding cash flow" term="funding" value={money(p?.funding_total)} detail="Credited or debited at settlement"/>
      </div>
      <Card title="Current decision" label="Current decision" className="span-1" tag="60m model">
        {!d?<Loading/>:<>
          <div className="decision"><strong className={(d.action??'NO_TRADE')==='NO_TRADE'?'tone-warn':'tone-up'}>{(d.action??'NO_TRADE').replaceAll('_',' ')}</strong>
            <span className="muted">Chance of rising (60m): {d.probability==null?'—':pct(d.probability,2)}</span></div>
          <h3>Decision gates</h3>
          <ul className="gates">{reasons.map(r=><li key={r} className={r==='ALL_GATES_PASSED'?'ok':''}><strong>{r.replaceAll('_',' ')}</strong><span>{reasonText[r]??'Recorded at the prediction cutoff.'}</span></li>)}</ul>
          <p className="source">Fee 5 bps/side · slippage 3 bps/side · edge buffer 5 bps. Model {d.forecast?.model_version??'unreleased'}.</p>
        </>}
      </Card>
      <Card title="Open position" label="Open position" className="span-1" tag="Max 25% of equity">
        {!p?<Loading/>:p.position?<div className="position"><Pill tone={p.position.side===1?'ok':'bad'}>{p.position.side===1?'LONG':'SHORT'}</Pill>
          <dl className="facts"><div><dt>Size</dt><dd>{num(p.position.quantity,6)} BTC</dd></div><div><dt>Entry</dt><dd>{money(p.position.entry)}</dd></div>
            <div><dt>Stop</dt><dd>{money(p.position.stop)}</dd></div><div><dt>Since</dt><dd>{stamp(p.position.entry_time)}</dd></div></dl></div>
          :<Empty title="No open position">The account stays in cash until a forecast clears every data, cost and risk gate.</Empty>}
        {p&&<dl className="facts"><div><dt>Entries</dt><dd>{p.paused?'Paused':p.halted?'Daily loss halt':'Enabled'}</dd></div>
          <div><dt>Last worker update</dt><dd>{stamp(p.last_worker)}</dd></div><div><dt>Schedule</dt><dd>:02 · :17 · :32 · :47 UTC</dd></div></dl>}
      </Card>
      <Card title={<><ShieldCheck size={16} aria-hidden/> Why the model leans this way</>} label="Feature contributions" className="span-1" tag="Associations">
        {!contributions.length?<Empty title="No explanation recorded">SHAP associations appear with each eligible forecast.</Empty>:
          <div className="bars">{contributions.slice(0,10).map(c=><div key={c.feature}><span>{c.feature.replaceAll('_',' ')}</span>
            <span className="bar-track"><i style={{width:Math.min(100,Math.abs(c.value)*800)+'%'}} className={c.value>0?'tone-bg-up':'tone-bg-down'}/></span><b>{c.value.toFixed(3)}</b></div>)}
            <p className="source">{d?.explanation?.method}. Associations do not establish causation.</p></div>}
      </Card>
      <Card title="Execution ledger" label="Execution ledger" className="span-3" tag="Persisted events">
        {!p?<Loading/>:p.fills?.length?<div className="table-scroll"><table><thead><tr><th>Time · UTC</th><th>Action</th><th>Side</th><th>BTC</th><th>Fill price</th><th>Fee</th><th>Gross P&L</th><th>Origin</th></tr></thead>
          <tbody>{[...p.fills].reverse().map((f,i)=><tr key={i}><td>{stamp(f.time)}</td><td>{f.action}</td><td>{f.side===1?'Long':'Short'}</td><td>{num(f.quantity,6)}</td>
            <td>{money(f.price)}</td><td>{money(f.fee)}</td><td>{money(f.pnl)}</td><td>{f.replayed?'Replayed':'Forward'}</td></tr>)}</tbody></table></div>
          :<Empty title="No fills recorded">Each fill, fee, funding event and checkpoint is persisted with a unique event ID.</Empty>}
      </Card>
      <Card title="Decision audit" label="Decision audit" className="span-3">
        {!p?<Loading/>:p.decisions?.length?<ul className="audit">{[...p.decisions].reverse().slice(0,8).map((x,i)=><li key={i}>
          <strong>{x.action.replaceAll('_',' ')}</strong><span className="muted">{stamp(x.time)}</span><span>{x.reasons.map(r=>r.replaceAll('_',' ').toLowerCase()).join(' · ')||'all gates passed'}</span></li>)}</ul>
          :<p className="note">No worker decision has been recorded.</p>}
      </Card>
      <p className="note span-3"><Term k="shadow">Research forecasts</Term> and strategy tests never trade; only this frozen release model drives the paper account.</p>
    </div>
  </>;
}
