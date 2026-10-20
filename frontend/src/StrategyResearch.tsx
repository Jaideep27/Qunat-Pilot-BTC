import {useEffect,useState} from 'react';
import {request} from './api';

type Family={id:string;track:string;kind:string;configurations:number;calls:number;call_rate:number|null;hit_rate:number|null;
  wilson_low:number|null;wilson_high:number|null;break_even:number|null;mean_net:number|null;deflated_sharpe:number;
  pbo:number|null;passed:boolean;failed:string[]};
type Feed={rows:number;first:string|null;last:string|null};
type Data={status:string;run:string|null;trials:Record<string,number>;passing:string[];holdout_evaluated:boolean;
  next_test:string;families:Family[];forward:Record<string,Feed>};
const pct=(n:number|null|undefined,d=1)=>n==null?'—':(n*100).toFixed(d)+'%';
const names:Record<string,string>={S1:'Breakout',S2:'Momentum agreement',S3:'Funding crowding',S4:'Volatility squeeze',
  S5:'Coinbase premium',I1:'Flush-out rebound',I2:'Crowding fade',I3:'Session momentum',I4:'Taker flow',I5:'Coinbase premium (hourly)',
  M1:'Trend + meta-label',M2:'24h momentum + meta-label'};
const unit=(track:string)=>track==='swing'?'days':'hours';

export default function StrategyResearch(){
  const [data,setData]=useState<Data|null>(null),[error,setError]=useState(false);
  useEffect(()=>{request<Data>('strategy-research').then(r=>setData(r.data)).catch(()=>setError(true))},[]);
  const total=Object.values(data?.trials??{}).reduce((a,b)=>a+b,0);
  return <section className="panel" aria-label="Strategy research">
    <div className="panel-heading"><h2>Strategy research</h2><span className="subtle-tag">PRE-REGISTERED · NEVER TRADES</span></div>
    {error&&<p role="status">Strategy research unavailable.</p>}
    {!data&&!error&&<p>Loading strategy research...</p>}
    {data?.status==='not_run'&&<p>No strategy research run yet. Run <code>quantpilot research-strategies</code>.</p>}
    {data?.status==='complete'&&<>
      <p><strong>{data.passing.length?`Passed: ${data.passing.join(', ')}`:'No strategy passed.'}</strong> {total} configurations tested
        walk-forward, scored as trades after fees and funding. Hit rate is the share of calls that made money, shown with how often the rule calls.</p>
      <div className="table-scroll"><table><thead><tr><th>Strategy</th><th>Calls</th><th>How often it calls</th><th>Hit rate (95% CI)</th>
        <th>Break-even</th><th>Net per call</th><th>Verdict</th></tr></thead><tbody>{data.families.map(f=><tr key={f.id}>
        <td>{names[f.id]??f.id} <small>{f.track}</small></td><td>{f.calls}</td><td>{pct(f.call_rate)} of {unit(f.track)}</td>
        <td>{pct(f.hit_rate)} ({f.wilson_low==null?'—':(f.wilson_low*100).toFixed(1)}–{pct(f.wilson_high)})</td><td>{pct(f.break_even)}</td><td>{pct(f.mean_net,2)}</td>
        <td>{f.passed?'Passed':'Failed: '+f.failed.map(c=>c.replaceAll('_',' ')).join(' · ')}</td></tr>)}</tbody></table></div>
      <div className="indicator-list">{Object.entries(data.forward).map(([name,feed])=><div key={name}>
        <span>{name==='depth'?'Order-book depth':'Liquidations'} (forward only)</span>
        <strong>{feed.rows?`${feed.rows.toLocaleString()} ${name==='depth'?'snapshots':'minutes with liquidations'}`:'not recording yet'}</strong></div>)}
        <div><span>Next pre-registered test</span><strong>{data.next_test}</strong></div>
        <div><span>Holdout</span><strong>{data.holdout_evaluated?'Evaluated once':'Holdout year untouched'}</strong></div></div>
      <p className="panel-source">A pass needs enough calls, a hit rate whose lower bound beats break-even, a deflated Sharpe ratio of at least 0.95
        given every configuration tried, PBO below 0.3 and most years positive. Details: docs/strategy-research.md.</p></>}
  </section>;
}
