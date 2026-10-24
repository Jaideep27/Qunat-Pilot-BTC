import {useEffect,useState} from 'react';
import {request} from '../lib/api';

type Release={period:string;value:number;previous:number|null;change:number|null;change_pct:number|null};
type MacroEvent={id:string;kind:string;label:string;scheduled_at:string;status:string;values:Record<string,Release>};
type Macro={upcoming:Record<string,MacroEvent|null>;recent:MacroEvent[];total_events:number;scope:string};
const when=(value:string)=>new Date(value).toLocaleString(undefined,{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'})+' UTC';
const signed=(n:number|null|undefined,digits=2)=>n==null?'—':(n>0?'+':'')+n.toFixed(digits);

function released(event:MacroEvent){
  const v=event.values;
  if(event.kind==='cpi')return `Headline ${signed(v.CPIAUCSL?.change_pct)}% m/m · Core ${signed(v.CPILFESL?.change_pct)}% m/m`;
  if(event.kind==='jobs')return `Payrolls ${signed(v.PAYEMS?.change,0)}k · Unemployment ${v.UNRATE?.value?.toFixed(1)??'—'}%`;
  return 'Decision time from the Federal Reserve calendar';
}

export default function MacroCalendar(){
  const [data,setData]=useState<Macro|null>(null),[error,setError]=useState(false);
  useEffect(()=>{let alive=true;
    request<Macro>('macro').then(r=>{if(alive)setData(r.data)}).catch(()=>{if(alive)setError(true)});
    return()=>{alive=false}},[]);
  return <section className="panel" aria-label="Macro calendar">
    <div className="panel-heading"><h2>Macro calendar</h2><span className="subtle-tag">OFFICIAL SCHEDULES</span></div>
    {error&&<p role="status">Macro calendar unavailable.</p>}
    {!data&&!error&&<p>Loading macro calendar...</p>}
    {data&&<>
      <div className="indicator-list">{Object.values(data.upcoming).filter((e):e is MacroEvent=>!!e).map(e=>
        <div key={e.id}><span>Next · {e.label}</span><strong>{when(e.scheduled_at)}</strong></div>)}</div>
      <h3>Recent releases (first published values)</h3>
      {data.recent.length?<div className="table-scroll"><table><thead><tr><th>Release · UTC</th><th>Event</th><th>First release</th></tr></thead>
        <tbody>{data.recent.map(e=><tr key={e.id}><td>{when(e.scheduled_at)}</td><td>{e.label}</td><td>{released(e)}</td></tr>)}</tbody></table></div>
        :<p>No releases collected yet.</p>}
      <p className="panel-source">{data.scope} Consensus surprise unavailable: no timestamped consensus source. {data.total_events} scheduled events.</p>
    </>}
  </section>;
}
