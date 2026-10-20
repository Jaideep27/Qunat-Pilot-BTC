import {useEffect,useState} from 'react';
import {request} from './api';

type Forecast={horizon:string;cutoff:string;status:string;probability_up:number|null;base_rate:number;
  return_q10:number|null;return_q90:number|null;drop5_risk:number;regime:string;range_status?:string;
  volatility:{ewma:number;garch:number;model?:string;value?:number};
  champion:{group:string;training:string|null}};
type Champion={group:string;training:string|null;validation_skill:number;test_skill:number|null;configurations_tried:number};
type Data={latest:Forecast[];forward:Record<string,{scored:number;pending:number;brier:number|null;constant_brier:number|null;
  interval_coverage_80:number|null}>;champions:Record<string,Champion>;backtest_overfitting:Record<string,{pbo:number|null}>;status:string};
const pct=(n:number|null|undefined,d=1)=>n==null?'—':(n*100).toFixed(d)+'%';
const signed=(n:number|null|undefined)=>n==null?'—':(n>0?'+':'')+(n*100).toFixed(2)+'%';
const order=['1h','4h','24h'];

export default function ShadowForecasts(){
  const [data,setData]=useState<Data|null>(null),[error,setError]=useState(false);
  useEffect(()=>{let alive=true;const load=()=>request<Data>('forecasts').then(r=>{if(alive){setData(r.data);setError(false)}})
    .catch(()=>{if(alive)setError(true)});load();const timer=setInterval(load,60000);return()=>{alive=false;clearInterval(timer)}},[]);
  const latest=[...(data?.latest??[])].sort((a,b)=>order.indexOf(a.horizon)-order.indexOf(b.horizon));
  const day=latest.find(f=>f.horizon==='24h')??latest[0];
  return <section className="panel" aria-label="Research forecasts">
    <div className="panel-heading"><h2>Research forecasts</h2><span className="subtle-tag">SHADOW · NEVER TRADES</span></div>
    {error&&<p role="status">Research forecasts unavailable.</p>}
    {!data&&!error&&<p>Loading research forecasts...</p>}
    {data&&!latest.length&&<p>No research forecast recorded yet. The forecaster runs every 15 minutes once a champion exists.</p>}
    {latest.length>0&&<><div className="table-scroll"><table><thead><tr><th>Horizon</th><th>Upward probability</th><th>Base rate</th>
      <th>80% return range</th><th>Model</th><th>Forward record</th></tr></thead><tbody>{latest.map(f=>{const fw=data!.forward[f.horizon];
        const c=data!.champions[f.horizon];const pbo=data!.backtest_overfitting[f.horizon]?.pbo;return <tr key={f.horizon}>
        <td>{f.horizon}</td><td>{f.status==='no_validated_skill'?<>{pct(f.probability_up)} · No validated skill</>:pct(f.probability_up)}</td>
        <td>{pct(f.base_rate)}</td><td>{signed(f.return_q10)} to {signed(f.return_q90)}{f.range_status?' · '+(f.range_status==='model'?'range model':'unconditional range'):''}</td>
        <td>{c?(c.group==='constant'?'base rate':c.group+' / '+c.training):f.champion.group}{pbo!=null?' · PBO '+pbo.toFixed(2):''}</td>
        <td>{fw?`${fw.scored} scored · Brier ${fw.brier?.toFixed(4)??'—'} vs ${fw.constant_brier?.toFixed(4)??'—'}`:'Awaiting outcomes'}</td></tr>})}</tbody></table></div>
      {day&&<div className="indicator-list"><div><span>Chance of touching -5% within 24h</span><strong>{pct(day.drop5_risk)}</strong></div>
        <div><span>Volatility next 24h ({(day.volatility.model??'ewma').toUpperCase()} champion)</span><strong>{day.horizon==='24h'?pct(day.volatility.value??day.volatility.ewma,2):'—'}</strong></div>
        <div><span>Market condition</span><strong>{day.regime}</strong></div>
        <div><span>Forecast cutoff</span><strong>{new Date(day.cutoff).toLocaleString(undefined,{timeZone:'UTC'})} UTC</strong></div></div>}
      <p className="panel-source">Champions are chosen on validation only; "No validated skill" means no model beat the base rate,
        so the base rate is shown. PBO is the probability of backtest overfitting. This research forecaster never trades or sizes positions.</p></>}
  </section>;
}
