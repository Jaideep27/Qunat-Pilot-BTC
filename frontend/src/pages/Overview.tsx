import {useState} from 'react';
import {ArrowUpRight,RefreshCw} from 'lucide-react';
import {post,publicView,type Candle} from '../lib/api';
import MarketChart from '../components/MarketChart';
import type {Route} from '../app/routes';
import {AIText,Badge,STATUS_TONE,isSnapshot,type Snapshot} from '../components/analysis';
import {Card,Collapsible,Empty,Loading,PageHead,ago,money,signedPct,until,useNow,useResource} from '../components/ui';

type BriefBody={headline:string;overall:string;summary:string;trend:string;momentum:string;volatility:string;volume_flow:string;
  positioning:string;strategies:string;news_calendar:string;levels_to_watch:string[];risks:string[]};
type BriefRecord={id:string;created_at:string;model:string;status:'ok'|'failed'|'limit';reason:string;brief?:BriefBody;error?:string};
const OVERALL:Record<string,'up'|'down'|'neutral'|'quiet'>={'leaning up':'up','leaning down':'down',mixed:'neutral',quiet:'quiet'};
const SECTIONS:[keyof BriefBody,string][]=[['trend','Trend'],['momentum','Momentum'],['volatility','Volatility'],['volume_flow','Volume & flow'],
  ['positioning','Positioning'],['strategies','What the strategies say'],['news_calendar','News & calendar']];

function Brief(){
  const briefs=useResource<{briefs:BriefRecord[];every_hours:number}>('analysis/briefs?limit=5',120000);
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  const rows=Array.isArray(briefs.data?.briefs)?briefs.data!.briefs:[];
  const good=rows.find(r=>r.status==='ok'&&r.brief),latest=rows[0];
  const fresh=async()=>{setBusy('Claude is writing a new brief…');setError('');
    try{await post('analysis/brief',{});briefs.reload()}catch(e){setError(e instanceof Error?e.message:'Could not write a brief now.')}
    finally{setBusy('')}};
  return <Card title="Claude's market brief" label="Market brief" className="span-3 brief-card"
    actions={!publicView&&<button type="button" className="link" onClick={()=>void fresh()} disabled={!!busy}><RefreshCw size={13} aria-hidden/> New brief</button>}>
    {briefs.loading&&!briefs.data?<Loading lines={4}/>:!good?<Empty title="No brief yet">Claude writes one every {briefs.data?.every_hours??4} hours from the latest numbers.</Empty>:<>
      <div className="brief-head"><h3>{good.brief!.headline}</h3><Badge tone={OVERALL[good.brief!.overall]??'neutral'}>{good.brief!.overall}</Badge></div>
      <p className="brief-summary">{good.brief!.summary}</p>
      <Collapsible title="The details" summary="trend, momentum, volatility, flow, positioning, strategies, news">
        <dl className="brief-sections">{SECTIONS.map(([k,label])=><div key={k}><dt>{label}</dt><dd>{good.brief![k] as string}</dd></div>)}</dl>
      </Collapsible>
      <div className="brief-lists">
        {good.brief!.levels_to_watch.length>0&&<div><h4>Levels to watch</h4><ul>{good.brief!.levels_to_watch.map((l,i)=><li key={i}>{l}</li>)}</ul></div>}
        {good.brief!.risks.length>0&&<div><h4>Risks and doubts</h4><ul>{good.brief!.risks.map((l,i)=><li key={i}>{l}</li>)}</ul></div>}
      </div>
      <AIText model={good.model} at={good.created_at}/>
    </>}
    {latest&&latest.status!=='ok'&&<p className="muted small-print">The last attempt ({ago(latest.created_at)}) did not finish: {latest.status==='limit'?'the Claude usage limit was reached':'Claude was unavailable'}. Showing the latest good brief.</p>}
    {busy&&<p className="muted" role="status">{busy}</p>}{error&&<p role="alert" className="explain-error">{error}</p>}
  </Card>;
}

function LeanRow({name,lean,up,down,total}:{name:string;lean:string;up:number;down:number;total:number}){
  return <li className="lean-row"><span>{name}</span>
    <div className="lean-bar" role="img" aria-label={`${name}: ${up} of ${total} lean up, ${down} lean down`}>
      <i className="lean-down" style={{width:(down/Math.max(total,1))*50+'%'}}/><i className="lean-up" style={{width:(up/Math.max(total,1))*50+'%'}}/></div>
    <Badge tone={lean==='up'?'up':lean==='down'?'down':'neutral'}>{lean==='mixed'?'mixed':'leans '+lean}</Badge></li>;
}

function RangeBar({low,high,price,label}:{low:number|null;high:number|null;price:number|null;label:string}){
  if(low==null||high==null||price==null)return null;
  const at=Math.min(100,Math.max(0,(price-low)/(high-low||1)*100));
  return <div className="range4" aria-label={label}><div className="range4-track"><i style={{left:at+'%'}}/></div>
    <div className="range4-values"><span>{money(low,0)}</span><strong>{money(price,0)}</strong><span>{money(high,0)}</span></div></div>;
}

function MarketNow({s}:{s:Snapshot}){
  const summary=s.summary['1h']??{};
  return <Card title="Market now" label="Market now" className="span-2">
    <div className="now-head"><div><strong className="big">{money(s.price.last,0)}</strong>
      <small className={(s.price.change_24h??0)>=0?'tone-up':'tone-down'}> {signedPct(s.price.change_24h)} in 24h</small></div>
      <Badge tone={s.regime.id==='TREND_UP'?'up':s.regime.id==='TREND_DOWN'?'down':s.regime.id==='PANIC'||s.regime.id==='HIGH_VOL'?'warn':'neutral'}>{s.regime.text}</Badge></div>
    <p className="muted small-print">24h range {money(s.price.low_24h,0)} – {money(s.price.high_24h,0)} · updated {ago(s.built_at)}</p>
    <h4>What the indicators say (1-hour chart)</h4>
    <ul className="lean-list">{Object.entries(summary).map(([g,v])=><LeanRow key={g} name={g} {...v}/>)}</ul>
    <h4>Likely range this 4-hour block (8 times out of 10)</h4>
    <RangeBar low={s.range_4h.band.low} high={s.range_4h.band.high} price={s.price.last} label="Likely 4-hour range"/>
  </Card>;
}

function Levels({s}:{s:Snapshot}){
  const near=s.levels.filter(l=>Math.abs(l.distance)<=.06);
  return <Card title="Key levels" label="Key levels" className="span-1">
    {!near.length?<Empty title="No levels near the price"/>:<ul className="level-list">{near.map(l=><li key={l.id} className={'lvl-'+l.kind}>
      <span>{l.label}</span><strong>{money(l.price,0)}</strong><small>{signedPct(l.distance,1)}</small></li>)}</ul>}
  </Card>;
}

function StrategiesNow({s,go}:{s:Snapshot;go:(r:Route)=>void}){
  const firing=s.strategies.rulebook.firing;
  return <Card title="What the strategies say" label="Strategies now" className="span-2"
    actions={<button type="button" className="link" onClick={()=>go('strategies')}>All strategies <ArrowUpRight size={13}/></button>}>
    <ul className="strat-now">{s.strategies.styles.map(st=><li key={st.id}><div><strong>{st.name}</strong><Badge tone={STATUS_TONE[st.status]??st.tone}>{st.status}</Badge></div>
      <small className="muted">{st.says}</small></li>)}
      <li><div><strong>Your rulebook</strong><Badge tone={firing.length?'up':'quiet'}>{firing.length?`${firing.length} firing`:'quiet'}</Badge></div>
        <small className="muted">{firing.length?firing.map(f=>f.id+' '+f.name).join(', '):'No setup is firing at the last 15-minute close.'}</small></li></ul>
  </Card>;
}

function NewsAndCalendar({s,go}:{s:Snapshot;go:(r:Route)=>void}){
  const now=useNow(60000);
  const next=s.calendar.upcoming[0];
  return <Card title="News & calendar" label="Latest headlines" className="span-1"
    actions={<button type="button" className="link" onClick={()=>go('news')}>All news <ArrowUpRight size={13}/></button>}>
    {next&&<p className="next-event"><span className="muted">Next US release</span><strong>{next.label}</strong><small>{until(next.at,now)}</small></p>}
    {!s.headlines.items.length?<Empty title="No news yet"/>:<ul className="headlines">{s.headlines.items.slice(0,3).map(i=><li key={i.id}>
      <a href={i.url} target="_blank" rel="noreferrer">{i.title}</a><small className="muted">{i.source} · {ago(i.available_at)}</small></li>)}</ul>}
  </Card>;
}

export default function Overview({go}:{go:(r:Route)=>void;ask?:(q:string)=>void}){
  const snap=useResource<Snapshot|null>('analysis/snapshot',60000);
  const candles=useResource<Candle[]>('candles',60000);
  const bars=Array.isArray(candles.data)?candles.data:[];
  const s=isSnapshot(snap.data)?snap.data:null;
  return <div className="page-narrow">
    <PageHead title="Home" description="What Bitcoin is doing, explained in plain words. Analysis only: nothing here trades."/>
    <div className="grid">
      <Brief/>
      {s?<><MarketNow s={s}/><Levels s={s}/><StrategiesNow s={s} go={go}/><NewsAndCalendar s={s} go={go}/></>:
        snap.loading?<Card className="span-3"><Loading lines={5}/></Card>:
        <Card className="span-3"><Empty title="No analysis yet">The analysis service builds a snapshot every 5 minutes.</Empty></Card>}
      <Card title="Price today" label="Price chart" className="span-3 chart-card" actions={<button type="button" className="link" onClick={()=>go('chart')}>Full chart <ArrowUpRight size={13}/></button>}>
        {bars.length?<MarketChart candles={bars} levels={{}}/>:candles.loading?<Loading lines={5}/>:<Empty title="No prices yet">Start the data service.</Empty>}
      </Card>
    </div>
  </div>;
}
