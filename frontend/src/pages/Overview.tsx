import {useState} from 'react';
import {ArrowUpRight,CalendarClock,Newspaper,ShieldCheck,Sparkles} from 'lucide-react';
import type {Candle} from '../api';
import MarketChart from '../MarketChart';
import type {Route} from '../routes';
import {Card,Empty,Loading,Meter,PageHead,Pill,Segmented,Stat,Term,Updated,ago,money,num,pct,signedPct,until,useNow,useResource} from '../ui';

type Forecast={horizon:string;cutoff:string;status:string;probability_up:number|null;base_rate:number;return_q10:number|null;
  return_q90:number|null;range_status?:string;drop5_risk:number;regime:string;volatility:{model?:string;value?:number;ewma:number}};
type Forecasts={latest:Forecast[];forward:Record<string,{scored:number;brier:number|null;constant_brier:number|null}>};
type MacroEvent={id:string;kind:string;label:string;scheduled_at:string};
type Macro={upcoming:Record<string,MacroEvent|null>;recent:MacroEvent[]};
type NewsItem={id:string;title:string;url:string;source:string;available_at:string};
type Strategy={status:string;trials:Record<string,number>;passing:string[];holdout_evaluated:boolean;next_test:string;
  families:{id:string;hit_rate:number|null;passed:boolean}[];forward:Record<string,{rows:number}>;
  agent:{families:Record<string,{passed:boolean;calls:number;hit_rate:number|null;break_even:number|null;mean_net:number|null}>}|null};
type Ready={ready:boolean;checks:Record<string,{ok:boolean;status:string}>;worker:{status:string;next_scheduled_at:string};
  collector:{status:string;age_seconds:number|null}};

const HORIZONS=['1h','4h','24h'] as const;
const QUICK=['Up or down next 4h?','Chance of a 5% drop today?','Where will BTC be in 24h?','Why is BTC moving?'];

function PriceStrip({bars}:{bars:Candle[]}){
  if(!bars.length)return null;
  const last=bars.at(-1)!,first=bars[0];
  const high=Math.max(...bars.map(b=>b.high)),low=Math.min(...bars.map(b=>b.low));
  const change=last.close/first.open-1;
  const volume=bars.reduce((s,b)=>s+b.volume,0);
  return <div className="strip span-3" aria-label="24 hour statistics">
    <Stat label="BTC / USDT" value={money(last.close)} detail={`Last completed 5m candle · ${ago(last.available_at)}`}/>
    <Stat label="24h change" value={signedPct(change)} tone={change>=0?'up':'down'} detail={`from ${money(first.open,0)}`}/>
    <Stat label="24h range" value={`${money(low,0)} – ${money(high,0)}`} detail={`${pct(high/low-1,2)} high to low`}/>
    <Stat label="24h volume" value={num(volume,0)+' BTC'} detail={`${bars.length} five-minute bars`}/>
  </div>;
}

function Outlook({price,ask}:{price:number|undefined;ask:(q:string)=>void}){
  const forecasts=useResource<Forecasts>('forecasts',60000);
  const [horizon,setHorizon]=useState<typeof HORIZONS[number]>('4h');
  const f=forecasts.data?.latest?.find(x=>x.horizon===horizon);
  const fw=forecasts.data?.forward?.[horizon];
  const noEdge=f?.status==='no_validated_skill';
  const low=f&&price&&f.return_q10!=null?price*(1+f.return_q10):undefined;
  const high=f&&price&&f.return_q90!=null?price*(1+f.return_q90):undefined;
  return <Card title="Outlook" label="Outlook" className="span-1" actions={<Segmented label="Forecast horizon" options={HORIZONS} value={horizon} onChange={setHorizon}/>}>
    {forecasts.loading&&!f?<Loading/>:!f?<Empty title="No forecast yet">The research forecaster records every 15 minutes.</Empty>:<>
      <div className="outlook-main">
        <div><span className="muted">Chance BTC is higher in {horizon}</span><strong className="big">{pct(f.probability_up)}</strong></div>
        {noEdge?<Pill tone="warn"><Term k="no_edge">No edge</Term></Pill>:<Pill tone="ok"><Term k="validated">Validated</Term></Pill>}
      </div>
      <Meter value={f.probability_up!=null?f.probability_up*100:null} tone={noEdge?'warn':'up'}/>
      <p className="note">{noEdge?'No method beat the base rate, so this is the historical frequency: about a coin flip.':'A validated model beats the base rate here.'} <Term k="base_rate">Base rate</Term> {pct(f.base_rate)}.</p>
      <div className="range-box">
        <span className="muted"><Term k="range80">80% range</Term> · {f.range_status==='model'?'range model':'unconditional'}</span>
        <div className="range-values"><span>{money(low,0)}</span><span className="muted">{signedPct(f.return_q10)} … {signedPct(f.return_q90)}</span><span>{money(high,0)}</span></div>
        <div className="range-bar"><i/></div>
      </div>
      <dl className="facts">
        <div><dt><Term k="forward_record">Forward record</Term></dt><dd>{fw?`${fw.scored} scored · Brier ${num(fw.brier,4)} vs ${num(fw.constant_brier,4)}`:'Awaiting outcomes'}</dd></div>
        <div><dt>Market condition</dt><dd>{f.regime}</dd></div>
        {horizon==='24h'&&<div><dt>Chance of touching −5% in 24h</dt><dd>{pct(f.drop5_risk)}</dd></div>}
      </dl>
      <div className="chips">{QUICK.map(q=><button key={q} type="button" onClick={()=>ask(q)}>{q}</button>)}</div>
      <Updated resource={forecasts}/>
    </>}
  </Card>;
}

function Pulse({ind}:{ind:Record<string,number>|undefined}){
  if(!ind)return <Card title="Market pulse" className="span-1"><Loading/></Card>;
  const rsi=ind['1h_rsi_14']??ind.rsi_14;
  const fg=ind.fear_greed;
  return <Card title="Market pulse" className="span-1" tag="5m features">
    <div className="pulse-row"><span><Term k="rsi">RSI 1h</Term></span><strong>{num(rsi,1)}</strong></div>
    <Meter value={rsi} tone={rsi>70?'down':rsi<30?'up':undefined}/>
    <div className="pulse-row"><span><Term k="fear_greed">Fear & Greed</Term></span><strong>{num(fg,0)} / 100</strong></div>
    <Meter value={fg} tone={fg>75?'down':fg<25?'up':'warn'}/>
    <dl className="facts">
      <div><dt><Term k="adx">Trend strength (ADX)</Term></dt><dd>{num(ind.adx,1)}</dd></div>
      <div><dt><Term k="relative_volume">Relative volume</Term></dt><dd>{num(ind.relative_volume,2)}×</dd></div>
      <div><dt><Term k="funding">Funding (8h)</Term></dt><dd>{ind.funding_rate==null?'—':(ind.funding_rate*100).toFixed(4)+'%'}</dd></div>
      <div><dt><Term k="open_interest">Open interest</Term></dt><dd>{num(ind.open_interest,0)} BTC</dd></div>
      <div><dt><Term k="long_short">Long/short (accounts)</Term></dt><dd>{num(ind.global_ratio,2)}</dd></div>
      <div><dt>Top traders' positions</dt><dd>{num(ind.top_position_ratio,2)}</dd></div>
    </dl>
  </Card>;
}

function NextMacro(){
  const macro=useResource<Macro>('macro',600000);
  const now=useNow(30000);
  const upcoming=Object.values(macro.data?.upcoming??{}).filter((e):e is MacroEvent=>!!e)
    .sort((a,b)=>a.scheduled_at.localeCompare(b.scheduled_at));
  const next=upcoming[0];
  const blackout=next&&Date.parse(next.scheduled_at)-now<=30*60000&&now-Date.parse(next.scheduled_at)<=15*60000;
  return <Card title={<><CalendarClock size={16} aria-hidden/> Next macro event</>} label="Next macro event" className="span-1">
    {macro.loading&&!macro.data?<Loading/>:!next?<Empty title="No scheduled release"/>:<>
      <div className="countdown"><strong>{until(next.scheduled_at,now)}</strong><span>{next.label}</span>
        <small className="muted">{new Date(next.scheduled_at).toLocaleString(undefined,{timeZone:'UTC',weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})} UTC</small></div>
      {blackout&&<Pill tone="warn"><Term k="blackout">Entry blackout</Term></Pill>}
      <ul className="mini-list">{upcoming.slice(1).map(e=><li key={e.id}><span>{e.label}</span><span className="muted">{until(e.scheduled_at,now)}</span></li>)}</ul>
    </>}
  </Card>;
}

function Headlines({go}:{go:(r:Route)=>void}){
  const news=useResource<{items:NewsItem[]}>('news?compact=true&limit=5',120000);
  const items=news.data?.items??[];
  return <Card title={<><Newspaper size={16} aria-hidden/> Latest headlines</>} label="Latest headlines" className="span-1"
    actions={<button type="button" className="link" onClick={()=>go('news')}>All news <ArrowUpRight size={13}/></button>}>
    {news.loading&&!news.data?<Loading/>:!items.length?<Empty title="No news yet"/>:
      <ul className="headlines">{items.slice(0,4).map(i=><li key={i.id}><a href={i.url} target="_blank" rel="noreferrer">{i.title}</a>
        <small className="muted">{i.source} · {ago(i.available_at)}</small></li>)}</ul>}
  </Card>;
}

function Verdict({go,strategy}:{go:(r:Route)=>void;strategy:Strategy|undefined}){
  const total=Object.values(strategy?.trials??{}).reduce((a,b)=>a+b,0);
  const agent=strategy?.agent?.families?.TP;
  return <Card title={<><Sparkles size={16} aria-hidden/> Can anything predict BTC?</>} label="Research verdict" className="span-2"
    actions={<button type="button" className="link" onClick={()=>go('research')}>See the research <ArrowUpRight size={13}/></button>}>
    {!strategy?<Loading/>:<div className="verdict">
      <div><strong className={strategy.passing?.length?'tone-up':'tone-warn'}>{strategy.passing?.length?`${strategy.passing.length} strategy passed`:'Not yet: nothing has beaten costs'}</strong>
        <p>{total} pre-registered strategy configurations were tested walk-forward after fees and funding. Direction has no edge at 1h–24h;
          ranges and volatility do. {agent&&<>The structure-based trade plans won <Term k="hit_rate">{pct(agent.hit_rate)}</Term> of {agent.calls} out-of-sample trades vs a <Term k="break_even">break-even</Term> of {pct(agent.break_even)}.</>}</p></div>
      <dl className="facts">
        <div><dt><Term k="holdout">Holdout year</Term></dt><dd>{strategy.holdout_evaluated?'Evaluated once':'Untouched'}</dd></div>
        <div><dt>Next pre-registered test</dt><dd>{strategy.next_test}</dd></div>
      </dl>
    </div>}
  </Card>;
}

function Health({go,strategy}:{go:(r:Route)=>void;strategy:Strategy|undefined}){
  const ready=useResource<Ready>('ready',30000);
  const r=ready.data;
  const checks=Object.values(r?.checks??{});
  return <Card title={<><ShieldCheck size={16} aria-hidden/> System</>} label="System status" className="span-1"
    actions={<button type="button" className="link" onClick={()=>go('system')}>Details <ArrowUpRight size={13}/></button>}>
    {!r?(ready.error?<p className="note">Readiness unavailable.</p>:<Loading/>):<>
      <div className="pulse-row"><span>Overall</span>{r.ready?<Pill tone="ok">Ready</Pill>:<Pill tone="bad">Needs attention</Pill>}</div>
      <dl className="facts">
        <div><dt>Readiness checks</dt><dd>{checks.filter(c=>c.ok).length} / {checks.length} passing</dd></div>
        <div><dt>Collector</dt><dd>{r.collector?.status} · {r.collector?.age_seconds==null?'—':Math.round(r.collector.age_seconds)+'s'}</dd></div>
        <div><dt>Paper worker</dt><dd>{r.worker?.status} · next {until(r.worker?.next_scheduled_at)}</dd></div>
        {strategy&&<div><dt><Term k="liquidations">Liquidations / depth</Term></dt><dd>{num(strategy.forward?.liquidations?.rows,0)} min · {num(strategy.forward?.depth?.rows,0)} snapshots</dd></div>}
      </dl>
    </>}
  </Card>;
}

export default function Overview({go,ask}:{go:(r:Route)=>void;ask:(q:string)=>void}){
  const candles=useResource<Candle[]>('candles',60000);
  const indicators=useResource<Record<string,number>>('indicators',60000);
  const strategy=useResource<Strategy>('strategy-research',300000);
  const bars=candles.data??[];
  return <>
    <PageHead title="Overview" description="Bitcoin at a glance: price, outlook, positioning, what's next, and what the research says.">
      <Updated resource={candles} label="Prices"/>
    </PageHead>
    <div className="grid">
      <PriceStrip bars={bars}/>
      <Card title="Price · 5m" label="Price chart" className="span-2 chart-card" actions={<button type="button" className="link" onClick={()=>go('chart')}>Full chart <ArrowUpRight size={13}/></button>}>
        {bars.length?<MarketChart candles={bars} levels={indicators.data??{}}/>:candles.loading?<Loading lines={5}/>:<Empty title="No market observations yet">Start the data service to ingest completed candles.</Empty>}
      </Card>
      <Outlook price={bars.at(-1)?.close} ask={ask}/>
      <Pulse ind={indicators.data}/>
      <NextMacro/>
      <Headlines go={go}/>
      <Verdict go={go} strategy={strategy.data}/>
      <Health go={go} strategy={strategy.data}/>
    </div>
  </>;
}
