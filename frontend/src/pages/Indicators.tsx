import {useEffect,useMemo,useState} from 'react';
import {LineChart} from 'lucide-react';
import {Badge,ExplainBox,Spark,ZoneBar,fmt,isCatalogue,isSnapshot,showOnChart,type Catalogue,type Indicator,type Reading,type Snapshot} from '../components/analysis';
import {Collapsible,Empty,Loading,PageHead,Segmented,ago,money,useResource} from '../components/ui';

const TFS=['15m','1h','4h','1d'] as const;
type TF=typeof TFS[number];
const readTf=():TF=>{try{const v=localStorage.getItem('qp:analysis-tf');return (TFS as readonly string[]).includes(v??'')?v as TF:'1h'}catch{return '1h'}};

function IndicatorCard({ind,r,tf}:{ind:Indicator;r:Reading|undefined;tf?:TF}){
  return <article className="ind-card" aria-label={ind.name}>
    <header><h3>{ind.name}</h3>{r?.label&&<Badge tone={r.tone}>{r.label}</Badge>}</header>
    <div className="ind-value"><strong>{fmt(r?.value,ind.unit)}</strong>
      {r?.detail&&<small className="muted">ATR {money(r.detail.atr,0)} · {(r.detail.atr_pct*100).toFixed(2)}% of price</small>}
      <Spark values={r?.spark??[]} label={`${ind.name}, recent values`}/></div>
    <ZoneBar ind={ind} value={r?.value??null}/>
    <p className="ind-sentence">{r?.sentence??'No reading available right now.'}</p>
    <Collapsible title="What is this?">
      <p>{ind.what}</p><p><strong>How to read it.</strong> {ind.how}</p><p className="muted">Settings: {ind.settings}</p>
      <ul className="zone-legend">{ind.zones.map(([upper,label,tone],i)=><li key={i}><Badge tone={tone}>{label}</Badge>
        <small className="muted">{upper==null?'above':`below ${fmt(upper,ind.unit==='pct'?'':ind.unit)}`}</small></li>)}</ul>
    </Collapsible>
    <div className="ind-actions">
      {ind.study&&<button type="button" className="link-button" onClick={()=>showOnChart(ind.study!)}><LineChart size={14} aria-hidden/>Show on chart</button>}
      <ExplainBox kind="indicator" refId={ind.id} timeframe={ind.scope==='timeframe'?tf:undefined}/>
    </div>
  </article>;
}

export default function Indicators(){
  const cat=useResource<Catalogue>('analysis/catalogue',3600000);
  const snap=useResource<Snapshot|null>('analysis/snapshot',60000);
  const [tf,setTf]=useState<TF>(readTf);
  useEffect(()=>{try{localStorage.setItem('qp:analysis-tf',tf)}catch{/* optional */}},[tf]);
  const s=isSnapshot(snap.data)?snap.data:null,c=isCatalogue(cat.data)?cat.data:null;
  const byId=useMemo(()=>{
    const m=new Map<string,Reading>();
    s?.timeframes[tf]?.items.forEach(r=>m.set(r.id,r));
    s?.market.items.forEach(r=>m.set(r.id,r));
    return m;
  },[s,tf]);
  if((cat.loading&&!cat.data)||(snap.loading&&snap.data===undefined))return <Loading lines={6}/>;
  if(!c||!s)return <div className="page-narrow"><PageHead title="Indicators" description="Every indicator, what it says now, and what it means."/>
    <Empty title="No readings yet">The analysis service builds a snapshot every 5 minutes.</Empty></div>;
  const summary=s.summary[tf]??{};
  return <div className="page-narrow">
    <PageHead title="Indicators" description="Every indicator, what it says now, and what it means. Completed candles only."/>
    <div className="toolbar-row">
      <Segmented options={TFS} value={tf} onChange={setTf} label="Timeframe"/>
      <small className="muted">Updated {ago(s.built_at)} · last {tf} candle {s.timeframes[tf]?.time?ago(s.timeframes[tf].time!):'—'}</small>
    </div>
    <section className="group-strip" aria-label="Summary by group">{c.groups.filter(g=>summary[g]).map(g=>
      <div key={g} className={'group-chip lean-'+summary[g].lean}><strong>{g}</strong><span>{summary[g].lean==='mixed'?'mixed':`leans ${summary[g].lean}`}</span>
        <small>{summary[g].up} up · {summary[g].down} down · {summary[g].total}</small></div>)}</section>
    {c.groups.map(g=>{
      const items=c.indicators.filter(i=>i.group===g);
      if(!items.length)return null;
      const live=items.every(i=>i.scope==='market');
      return <section key={g} className="ind-group" aria-label={g}>
        <h2>{g}{live&&<small className="muted"> · live reading, same on every timeframe</small>}</h2>
        <div className="ind-grid">{items.map(i=><IndicatorCard key={i.id} ind={i} r={byId.get(i.id)} tf={tf}/>)}</div>
      </section>;
    })}
    <p className="muted small-print">{s.note}</p>
  </div>;
}
