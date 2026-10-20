import {Database} from 'lucide-react';
import type {Coverage} from '../api';
import PaperOperations from '../PaperOperations';
import {Card,Collapsible,Loading,PageHead,Pill,Term,Updated,ago,num,stamp,until,useResource} from '../ui';

type Ready={ready:boolean;checked_at:string;checks:Record<string,{ok:boolean;status:string;age_seconds?:number}>;
  collector:{status:string;age_seconds:number|null;poll_interval_seconds:number};worker:{status:string;next_scheduled_at:string}};
type Strategy={forward:Record<string,{rows:number;first:string|null;last:string|null}>};
const label=(s:string)=>s.replaceAll('_',' ');

export default function System(){
  const ready=useResource<Ready>('ready',30000);
  const coverage=useResource<Coverage>('coverage',600000);
  const strategy=useResource<Strategy>('strategy-research',120000);
  const r=ready.data,c=coverage.data;
  const coverageStatus=c?(c.release_ready?(c.strict_release_ready===false?'ELIGIBLE WITH QUARANTINE':'VERIFIED'):'RELEASE BLOCKED'):'';
  return <>
    <PageHead title="System health" description="Is everything collecting, scoring and running? Readiness, the paper worker, data coverage and the forward-only collectors.">
      <Updated resource={ready}/>
    </PageHead>
    <div className="grid">
      <Card title="Readiness" label="Readiness" className="span-1" tag={r?(r.ready?'Ready':'Needs attention'):undefined}>
        {!r?(ready.error?<p className="note">The API is not responding. Start the services with node scripts/start.cjs.</p>:<Loading/>):
          <ul className="checks">{Object.entries(r.checks??{}).map(([name,check])=><li key={name}>
            <span className={'dot-status '+(check.ok?'ok':'bad')} aria-hidden/><span>{label(name)}</span><strong>{label(check.status)}</strong></li>)}</ul>}
      </Card>
      <Card title="Data collection" label="Data collection" className="span-1">
        {!r?<Loading/>:<dl className="facts">
          <div><dt>Market collector</dt><dd>{r.collector?.status??'—'} · last poll {r.collector?.age_seconds==null?'—':Math.round(r.collector.age_seconds)+'s ago'}</dd></div>
          <div><dt>Poll interval</dt><dd>{r.collector?.poll_interval_seconds??'—'}s</dd></div>
          <div><dt>Paper worker</dt><dd>{r.worker?.status??'—'} · next run {until(r.worker?.next_scheduled_at)}</dd></div>
          <div><dt>Checked</dt><dd>{ago(r.checked_at)}</dd></div></dl>}
      </Card>
      <Card title="Forward-only collectors" label="Forward-only collectors" className="span-1">
        {!strategy.data?<Loading/>:<dl className="facts">{Object.entries(strategy.data.forward??{}).map(([name,f])=><div key={name}>
          <dt><Term k={name==='depth'?'depth':'liquidations'}>{name==='depth'?'Order-book depth':'Liquidations'}</Term></dt>
          <dd>{num(f.rows,0)} {name==='depth'?'snapshots':'minutes'} · {f.last?'latest '+ago(f.last):'none yet'}</dd></div>)}</dl>}
        <p className="source">Recorded live; gaps while the computer is off are permanent.</p>
      </Card>
      <div className="span-3"><PaperOperations/></div>
      <div className="span-3"><Collapsible title="Historical coverage" label="Historical coverage" defaultOpen
        summary={coverageStatus&&<Pill tone={c?.release_ready?'ok':'bad'}>{coverageStatus}</Pill>}>
        {!c?<Loading/>:<>
          <p className="note"><Database size={14} aria-hidden/> {c.reason??'Every required family must cover the same 24-month research period, plus indicator warm-up.'}</p>
          {c.quarantine&&<p className="source">{num(c.quarantine.source_rows,0)} affected source rows; excluded with {c.quarantine.feature_lookback_bars} subsequent bars; no interpolation</p>}
          {c.families&&<div className="table-scroll"><table><thead><tr><th>Feature family</th><th>Observations</th><th>Missing windows</th><th>Missing source rows</th><th>Invalid rows</th><th>Audit result</th></tr></thead>
            <tbody>{Object.entries(c.families).map(([name,f])=><tr key={name}><td>{name}</td><td>{num(f.rows,0)}</td><td>{num(f.missing_windows,0)}</td><td>{num(f.native_missing_count,0)}</td>
              <td>{num(f.invalid_rows,0)}</td><td>{f.issues.length?f.issues.join(' · '):'Verified'}</td></tr>)}</tbody></table></div>}
          <p className="source">Coverage window {stamp(c.start)} to {stamp(c.end_exclusive)}.</p>
        </>}
      </Collapsible></div>
    </div>
  </>;
}
