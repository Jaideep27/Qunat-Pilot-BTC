import {ArrowUpRight} from 'lucide-react';
import {type Candle} from '../lib/api';
import MarketChart from '../components/MarketChart';
import {BotList} from './Bots';
import type {Route} from '../app/routes';
import {Card,Empty,Loading,PageHead,ago,money,pct,signedPct,until,useNow,useResource} from '../components/ui';

type Forecast={horizon:string;status:string;probability_up:number|null;return_q10:number|null;return_q90:number|null;regime:string};
type MacroEvent={id:string;label:string;scheduled_at:string};
type NewsItem={id:string;title:string;url:string;source:string;available_at:string};

const MARKET:Record<string,string>={ranging:'moving sideways',trending_up:'trending up',trending_down:'trending down',volatile:'swinging hard'};

function MarketNow({bars}:{bars:Candle[]}){
  const forecasts=useResource<{latest:Forecast[]}>('forecasts',60000);
  const macro=useResource<{upcoming:Record<string,MacroEvent|null>}>('macro',600000);
  const now=useNow(60000);
  const f=forecasts.data?.latest?.find(x=>x.horizon==='4h');
  const price=bars.at(-1)?.close,first=bars[0]?.open;
  const change=price&&first?price/first-1:undefined;
  const next=Object.values(macro.data?.upcoming??{}).filter((e):e is MacroEvent=>!!e).sort((a,b)=>a.scheduled_at.localeCompare(b.scheduled_at))[0];
  const low=f&&price&&f.return_q10!=null?price*(1+f.return_q10):undefined;
  const high=f&&price&&f.return_q90!=null?price*(1+f.return_q90):undefined;
  return <Card title="Market now" label="Market now" className="span-3">
    {!price&&!f?<Loading lines={3}/>:<ul className="plain-list">
      {price&&<li><span>Bitcoin</span><strong>{money(price)} <small className={change==null?'':change>=0?'tone-up':'tone-down'}>{signedPct(change)} today</small></strong></li>}
      {f&&<li><span>The market is</span><strong>{MARKET[f.regime]??f.regime.replaceAll('_',' ')}</strong></li>}
      {f&&<li><span>Next 4 hours</span><strong>{f.status==='no_validated_skill'?`about a coin flip (${pct(f.probability_up,0)} up)`:`${pct(f.probability_up,0)} chance up`}</strong></li>}
      {low&&high&&<li><span>Likely range, next 4 hours</span><strong>{money(low,0)} – {money(high,0)}</strong></li>}
      {next&&<li><span>Next big US release</span><strong>{next.label}, {until(next.scheduled_at,now)}</strong></li>}
    </ul>}
  </Card>;
}

function Headlines({go}:{go:(r:Route)=>void}){
  const news=useResource<{items:NewsItem[]}>('news?compact=true&limit=3',120000);
  const items=news.data?.items??[];
  return <Card title="Headlines" label="Latest headlines" className="span-3"
    actions={<button type="button" className="link" onClick={()=>go('news')}>All news <ArrowUpRight size={13}/></button>}>
    {news.loading&&!news.data?<Loading/>:!items.length?<Empty title="No news yet"/>:
      <ul className="headlines">{items.slice(0,3).map(i=><li key={i.id}><a href={i.url} target="_blank" rel="noreferrer">{i.title}</a>
        <small className="muted">{i.source} · {ago(i.available_at)}</small></li>)}</ul>}
  </Card>;
}

export default function Overview({go}:{go:(r:Route)=>void;ask?:(q:string)=>void}){
  const candles=useResource<Candle[]>('candles',60000);
  const bars=Array.isArray(candles.data)?candles.data:[];
  return <div className="page-narrow">
    <PageHead title="Home" description="How your bots are doing and what Bitcoin is doing."/>
    <div className="grid">
      <BotList go={go}/>
      <MarketNow bars={bars}/>
      <Card title="Price today" label="Price chart" className="span-3 chart-card" actions={<button type="button" className="link" onClick={()=>go('chart')}>Full chart <ArrowUpRight size={13}/></button>}>
        {bars.length?<MarketChart candles={bars} levels={{}}/>:candles.loading?<Loading lines={5}/>:<Empty title="No prices yet">Start the data service.</Empty>}
      </Card>
      <Headlines go={go}/>
    </div>
  </div>;
}
