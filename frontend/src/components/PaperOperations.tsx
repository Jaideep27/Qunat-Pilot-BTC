import {useEffect,useState} from 'react';
import {request,preview} from '../lib/api';

type Run={id:string;started_at:string;finished_at:string|null;status:string;stage:string;duration_seconds:number|null;
  error_type:string|null;details:{action?:string;reason_codes?:string[]}};
type Operations={ready:boolean;checks:Record<string,{ok:boolean;status:string}>;
  worker:{status:string;last_checkpoint:string|null;next_scheduled_at:string;run_totals:Record<string,number>;runs:Run[];
    duration_p95_seconds:number|null;latency_sample_count:number;latency_sample_scope:string};
  forward_evidence:{forward_decisions:number;forward_fills:number;replayed_fills:number;observed_span_hours:number}};
const stamp=(value:string|null)=>value?new Date(value).toLocaleString(undefined,{timeZone:'UTC'})+' UTC':'Awaiting observation';
const label=(value:string)=>value.replaceAll('_',' ');

export default function PaperOperations(){
  const [data,setData]=useState<Operations|null>(null),[error,setError]=useState(false),[revision,setRevision]=useState(0);
  useEffect(()=>{
    if(preview)return;
    let active=true;
    const load=async()=>{
      try{const result=await request<Operations>('operations?limit=10');
        if(typeof result.data?.ready!=='boolean'||!Array.isArray(result.data.worker?.runs))throw new Error('Invalid monitoring response');
        if(active){setData(result.data);setError(false)}}
      catch{if(active)setError(true)}
    };
    void load();const timer=setInterval(()=>void load(),15000);
    return()=>{active=false;clearInterval(timer)};
  },[revision]);
  return <section className="panel" aria-label="Paper worker monitoring">
    <div className="panel-heading"><h2>Worker monitoring</h2>
      <span className="subtle-tag">{error?'MONITOR UNAVAILABLE':data?(data.ready?'READY':'NEEDS ATTENTION'):'PERSISTENT RUN HISTORY'}</span></div>
    {preview?<div className="panel-source">Interface preview. Measured worker runs appear after the real service is connected.</div>:
      error?<div className="panel-source" role="status">Worker monitoring is unavailable. Start or reload the updated API to inspect run history.
        <button className="text-button" onClick={()=>setRevision(value=>value+1)}>Retry monitor</button></div>:
      !data?<div className="panel-source">Loading recorded worker runs...</div>:<>
        <div className="indicator-list"><div><span>Worker status</span><strong>{label(data.worker.status).toUpperCase()}</strong></div>
          <div><span>Last successful checkpoint</span><strong>{stamp(data.worker.last_checkpoint)}</strong></div>
          <div><span>Next scheduled slot</span><strong>{stamp(data.worker.next_scheduled_at)}</strong></div>
          <div><span>Successful run duration · p95</span><strong>{data.worker.duration_p95_seconds==null?'Awaiting measured runs':data.worker.duration_p95_seconds.toFixed(3)+' s'}</strong></div></div>
        <p className="control-note">{data.worker.latency_sample_count} successful duration samples from the latest 100 recorded runs. Scheduled starts may be delayed.</p>
        <ul className="operations-checks" aria-label="Operational readiness checks">{Object.entries(data.checks).map(([name,check])=><li key={name}>
          <span>{label(name)}</span><strong className={check.ok?'mint':''}>{label(check.status)}</strong></li>)}</ul>
        <div className="indicator-list"><div><span>Forward decisions · full ledger</span><strong>{data.forward_evidence.forward_decisions.toLocaleString()}</strong></div>
          <div><span>Forward / replayed fills</span><strong>{data.forward_evidence.forward_fills} / {data.forward_evidence.replayed_fills}</strong></div>
          <div><span>Observed forward span</span><strong>{data.forward_evidence.observed_span_hours.toFixed(2)} hours</strong></div>
          <div><span>Recorded successful / failed runs</span><strong>{data.worker.run_totals.succeeded??0} / {data.worker.run_totals.failed??0}</strong></div></div>
        <p className="control-note">Observation span measures first decision to latest checkpoint. It is not an uptime percentage. Interrupted runs are detected on the next worker start.</p>
        <h3>Recent worker runs</h3>{data.worker.runs.length?<div className="table-scroll"><table aria-label="Recorded paper worker runs">
          <thead><tr><th>Started · UTC</th><th>Outcome</th><th>Stage</th><th>Duration</th><th>Decision / failure</th></tr></thead>
          <tbody>{data.worker.runs.map(run=><tr key={run.id}><td>{stamp(run.started_at)}</td><td>{label(run.status)}</td><td>{label(run.stage)}</td>
            <td>{run.duration_seconds==null?'Unmeasured':run.duration_seconds.toFixed(3)+' s'}</td>
            <td>{run.error_type??label(run.details.action??'Awaiting decision')}{!run.error_type&&run.details.reason_codes?.length?' · '+run.details.reason_codes.map(label).join(' · '):''}</td></tr>)}</tbody>
        </table></div>:<div className="panel-source">Run instrumentation has not started. Existing checkpoint and ledger evidence remain visible.</div>}
      </>}
  </section>;
}
