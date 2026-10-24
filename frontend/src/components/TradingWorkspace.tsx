import {useEffect,useRef,useState,useCallback} from 'react';
import {createChart,createSeriesMarkers,CandlestickSeries,BarSeries,LineSeries,AreaSeries,HistogramSeries,ColorType,
  type IChartApi,type ISeriesApi,type SeriesType,type UTCTimestamp,type Logical} from 'lightweight-charts';
import {request,preview,type Candle} from '../lib/api';
import {subscribeKlines,type LiveTick,type LiveState} from '../lib/liveFeed';
import '../styles/workspace.css';
import TradingViewPanel from './TradingViewPanel';

type Study={id:string;label:string;pane:'overlay'|'oscillator'};
type Studies={catalogue:Study[];series:Record<string,{time:number;value:number}[]>;definitions?:string};
type Anchor={time:number;price:number};
type Drawing={id:string;tool:string;points:Anchor[];text?:string};
type Storage={engine:string;markets:{market:string;rows:number;first:string;last:string}[];
  feeds:{id:string;updated_at:string;state:{status:string;error?:string;poll_interval_seconds?:number;last_persisted_at?:string}}[]};
const tools=[['cursor','Pointer'],['trend','Trend line'],['ray','Ray'],['horizontal','Horizontal line'],
  ['vertical','Vertical line'],['rectangle','Rectangle'],['fib','Fibonacci retracement'],['measure','Measure'],['text','Text note']];
const symbols:Record<string,string>={cursor:'↖',trend:'╱',ray:'↗',horizontal:'─',vertical:'│',rectangle:'□',fib:'≡',measure:'↔',text:'T'};
const colors=['#a99afa','#f1c46b','#59c5ed','#e387ba','#72d49d','#f79564'];
type RuleLevels={overlay:Record<string,number|string|null>|null;markers:{time:string;price:number;kind:string;side:number;playbook?:string;reason?:string}[]};
// Rule-engine levels drawn as price lines: [field, label, colour].
const ruleLines:[string,string,string][]=[['support','Support','#4bd6ad'],['resistance','Resistance','#ed7d80'],
  ['vwap','VWAP','#f1c46b'],['vwap_upper2','VWAP +2σ','#8d7a50'],['vwap_lower2','VWAP −2σ','#8d7a50'],
  ['band4_high','4h 80% high','#59c5ed'],['band4_low','4h 80% low','#59c5ed']];
const safeRead=<T,>(key:string,fallback:T):T=>{try{return JSON.parse(localStorage.getItem(key)??'null')??fallback}catch{return fallback}};

export default function TradingWorkspace(){
  const host=useRef<HTMLDivElement>(null),shell=useRef<HTMLDivElement>(null);
  const chartRef=useRef<IChartApi|null>(null),priceRef=useRef<ISeriesApi<SeriesType>|null>(null);
  const volumeRef=useRef<ISeriesApi<'Histogram'>|null>(null),chartKey=useRef(''),lastPlotted=useRef(0);
  const viewRef=useRef<{key:string;range:{from:number;to:number}|null}>({key:'',range:null});
  const market='BTCUSDT';
  const [timeframe,setTimeframe]=useState('5m');
  const [bars,setBars]=useState<Candle[]>([]),[study,setStudy]=useState<Studies>({catalogue:[],series:{}});
  const [selected,setSelected]=useState<string[]>(()=>safeRead('qp:studies',['EMA 21','rsi_14']));
  const [type,setType]=useState('Candles'),[tool,setTool]=useState('cursor'),[search,setSearch]=useState('');
  const [showStudies,setShowStudies]=useState(false),[drawings,setDrawings]=useState<Drawing[]>([]),[pending,setPending]=useState<Anchor[]>([]);
  const [engine,setEngine]=useState<'stored'|'tradingview'>('stored');
  const [showRules,setShowRules]=useState(()=>safeRead('qp:rule-levels',true)),[rules,setRules]=useState<RuleLevels|null>(null);
  useEffect(()=>{localStorage.setItem('qp:rule-levels',JSON.stringify(showRules));if(!showRules)return;let alive=true;
    const get=()=>request<RuleLevels>('rules/levels').then(e=>{if(alive)setRules(e.data)}).catch(()=>{});
    get();const timer=setInterval(get,60000);return()=>{alive=false;clearInterval(timer)}},[showRules]);
  const [storage,setStorage]=useState<Storage|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const generation=useRef(0);
  const [loadedKey,setLoadedKey]=useState('');
  const [revision,setRevision]=useState(0),[cursor,setCursor]=useState<Candle|null>(null);
  const [liveTick,setLiveTick]=useState<LiveTick|null>(null),[stream,setStream]=useState<LiveState>({status:'connecting'});
  const latestTick=useRef<LiveTick|null>(null),streamRef=useRef(stream);streamRef.current=stream;
  const applyTick=useRef<(tick:LiveTick)=>void>(()=>{});
  applyTick.current=tick=>{
    const series=priceRef.current,volume=volumeRef.current,time=Date.parse(tick.bar.event_time)/1000 as UTCTimestamp;
    if(!series||!volume||tick.timeframe!==timeframe||chartKey.current!==market+':'+timeframe||time<lastPlotted.current)return;
    if(type==='Line'||type==='Area')series.update({time,value:tick.bar.close});
    else series.update({time,open:tick.bar.open,high:tick.bar.high,low:tick.bar.low,close:tick.bar.close});
    volume.update({time,value:tick.bar.volume,color:tick.bar.close>=tick.bar.open?'#214e4666':'#4f293866'});
    lastPlotted.current=time;
  };
  const drawingKey=`qp:drawings:${market}:${timeframe}`;
  const actions=useRef({tool,pending,drawings});actions.current={tool,pending,drawings};
  useEffect(()=>{setDrawings(safeRead(drawingKey,safeRead(`qp:drawings:BTCUSDT_PERP:${timeframe}`,[])));setPending([])},[drawingKey,timeframe]);
  useEffect(()=>{localStorage.setItem('qp:studies',JSON.stringify(selected))},[selected]);
  const saveDrawings=useCallback((next:Drawing[])=>{setDrawings(next);localStorage.setItem(drawingKey,JSON.stringify(next))},[drawingKey]);

  const load=useCallback(async(older=false)=>{
    const requestId=++generation.current;
    setBusy(true);
    try{
      const before=older&&bars.length?'&before='+Date.parse(bars[0].event_time):'';
        const query=`?timeframe=${timeframe}&limit=1000&forming=true&selected=${encodeURIComponent(selected.join(','))}${before}`;
        const [prices,indicators]=await Promise.all([request<Candle[]>('market/candles'+query),request<Studies>('market/studies'+query)]);
        if(requestId!==generation.current)return;
        setBars(old=>older?[...prices.data,...old].filter((bar,i,all)=>all.findIndex(b=>b.event_time===bar.event_time)===i):prices.data);
        setStudy(indicators.data);setError(prices.data.length?'':'No stored candles for this market and timeframe. History acquisition is pending.');
        setLoadedKey(market+':'+timeframe);
      setStorage((await request<Storage>('market/storage')).data);
    }catch(e){setError('Market database unavailable: '+(e instanceof Error?e.message:String(e)))}
    finally{setBusy(false)}
  },[market,timeframe,bars,selected]);
  const loadRef=useRef(load);loadRef.current=load;
  useEffect(()=>{void loadRef.current();const timer=setInterval(()=>void loadRef.current(),15000);return()=>clearInterval(timer)},[market,timeframe,selected]);

  useEffect(()=>{
    latestTick.current=null;setLiveTick(null);setCursor(null);
    if(engine!=='stored'||preview){setStream({status:'disconnected'});return}
    return subscribeKlines(timeframe,tick=>{
      latestTick.current=tick;applyTick.current(tick);setLiveTick(tick);
      setCursor(old=>old?.event_time===tick.bar.event_time?tick.bar:old);
    },setStream);
  },[timeframe,engine]);

  // Read the newest persisted prices frequently without rebuilding the study catalogue.
  useEffect(()=>{
    if(engine!=='stored')return;
    let active=true;
    const timer=setInterval(async()=>{
      if(streamRef.current.status==='live')return;
      try{
        const latest=(await request<Candle[]>(`market/candles?timeframe=${timeframe}&limit=2&forming=true`)).data;
        if(!active||!latest.length)return;
        setBars(old=>{
          if(!old.length)return old;
          const byTime=new Map(old.map(bar=>[bar.event_time,bar]));
          latest.forEach(bar=>byTime.set(bar.event_time,bar));
          return [...byTime.values()].sort((a,b)=>a.event_time.localeCompare(b.event_time));
        });
      }catch{/* Full refresh reports availability; retain the last persisted observation. */}
    },5000);
    return()=>{active=false;clearInterval(timer)};
  },[market,timeframe,engine]);

  useEffect(()=>{
    if(!host.current||!bars.length||loadedKey!==market+':'+timeframe)return;
    const viewKey=market+':'+timeframe;
    const visible=viewRef.current.key===viewKey?viewRef.current.range:null;
    const chart=createChart(host.current,{autoSize:true,height:Math.max(580,Math.min(960,500+selected.filter(id=>study.catalogue.find(s=>s.id===id)?.pane==='oscillator').length*140)),
      layout:{background:{type:ColorType.Solid,color:'#0c1017'},textColor:'#9aa8ba',attributionLogo:true},
      grid:{vertLines:{color:'#19222e'},horzLines:{color:'#19222e'}},crosshair:{mode:0},
      rightPriceScale:{borderColor:'#263242'},timeScale:{borderColor:'#263242',timeVisible:true,rightOffset:8},
      handleScroll:tool==='cursor',handleScale:true});
    chartRef.current=chart;
    const base=type==='Line'?chart.addSeries(LineSeries,{color:'#56d6ba',lineWidth:2}):type==='Area'?
      chart.addSeries(AreaSeries,{lineColor:'#56d6ba',topColor:'#276c6044',bottomColor:'#276c6000'}):type==='Bars'?
      chart.addSeries(BarSeries,{upColor:'#4bd6ad',downColor:'#ed7d80'}):chart.addSeries(CandlestickSeries,{upColor:'#4bd6ad',downColor:'#ed7d80',borderVisible:false,wickUpColor:'#4bd6ad',wickDownColor:'#ed7d80'});
    priceRef.current=base as ISeriesApi<SeriesType>;
    if(showRules&&rules?.overlay)for(const [key,title,color] of ruleLines){const price=rules.overlay[key];
      if(typeof price==='number'&&Number.isFinite(price))base.createPriceLine({price,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title})}
    if(showRules&&rules?.markers?.length){const first=Date.parse(bars[0].event_time)/1000,step=({'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400} as Record<string,number>)[timeframe]??300;
      createSeriesMarkers(base,rules.markers.map(m=>({time:Math.floor(Date.parse(m.time)/1000/step)*step as UTCTimestamp,
        position:(m.kind==='exit'?(m.side===1?'aboveBar':'belowBar'):(m.side===1?'belowBar':'aboveBar')) as 'aboveBar'|'belowBar',
        color:m.kind==='exit'?'#9aa8ba':m.side===1?'#4bd6ad':'#ed7d80',shape:(m.kind==='exit'?'square':m.side===1?'arrowUp':'arrowDown') as 'square'|'arrowUp'|'arrowDown',
        text:(m.playbook??'')+(m.kind==='exit'?' exit':'')})).filter(m=>m.time>=first).sort((a,b)=>a.time-b.time))}
    chartKey.current=viewKey;
    lastPlotted.current=Date.parse(bars.at(-1)!.event_time)/1000;
    {
      const data=bars.map(row=>({time:Date.parse(row.event_time)/1000 as UTCTimestamp,...row}));
      if(type==='Line'||type==='Area')base.setData(data.map(row=>({time:row.time,value:row.close})));
      else (base as ISeriesApi<'Candlestick'>).setData(data);
      const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
      volumeRef.current=volume;
      chart.priceScale('volume').applyOptions({scaleMargins:{top:.86,bottom:0},visible:false});
      volume.setData(data.map(row=>({time:row.time,value:row.volume,color:row.close>=row.open?'#214e4666':'#4f293866'})));
      let pane=0;
      selected.forEach((id,index)=>{
        const item=study.catalogue.find(s=>s.id===id),points=study.series[id];if(!item||!points?.length)return;
        if(item.pane==='oscillator')pane++;
        const line=chart.addSeries(LineSeries,{color:colors[index%colors.length],lineWidth:1,title:item.label,
          priceLineVisible:false,lastValueVisible:true},item.pane==='overlay'?0:pane);
        line.setData(points.map(point=>({...point,time:point.time as UTCTimestamp})));
      });
      chart.panes().forEach((p,index)=>p.setHeight(index===0?450:140));
    }
    if(streamRef.current.status==='live'&&latestTick.current)applyTick.current(latestTick.current);
    if(visible)chart.timeScale().setVisibleLogicalRange(visible);else chart.timeScale().fitContent();
    chart.timeScale().subscribeVisibleLogicalRangeChange(()=>setRevision(n=>n+1));
    chart.subscribeCrosshairMove(param=>{
      if(param.time){const streamed=latestTick.current?.bar;
        const point=streamed&&Date.parse(streamed.event_time)/1000===Number(param.time)?streamed:bars.find(row=>Date.parse(row.event_time)/1000===Number(param.time));if(point)setCursor(point)}
    });
    const observer=new ResizeObserver(()=>setRevision(n=>n+1));observer.observe(host.current);
    setRevision(n=>n+1);
    return()=>{viewRef.current={key:viewKey,range:chart.timeScale().getVisibleLogicalRange()};observer.disconnect();chart.remove();if(chartRef.current===chart){chartRef.current=null;priceRef.current=null;volumeRef.current=null;chartKey.current=''}};
  },[bars,market,timeframe,study,selected,type,tool,saveDrawings,engine,loadedKey,showRules,rules]);

  function coordinates(point:Anchor){
    const chart=chartRef.current,price=priceRef.current;if(!chart||!price)return null;
    let x=chart.timeScale().timeToCoordinate(point.time as UTCTimestamp);
    if(x==null&&bars.length){const index=bars.findIndex(b=>Date.parse(b.event_time)/1000>=point.time);if(index>=0)x=chart.timeScale().logicalToCoordinate(index as Logical)}
    const y=price.priceToCoordinate(point.price);return x==null||y==null?null:{x:Number(x),y:Number(y)};
  }
  function draw(event:React.MouseEvent<HTMLDivElement>){
    const chart=chartRef.current,series=priceRef.current,a=actions.current;
    if(!chart||!series||a.tool==='cursor'||loadedKey!==market+':'+timeframe)return;
    const rect=event.currentTarget.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
    if(y>chart.panes()[0].getHeight())return;
    const price=series.coordinateToPrice(y),time=chart.timeScale().coordinateToTime(x);
    if(price==null||typeof time!=='number')return;
    const points=[...a.pending,{time:Number(time),price:Number(price)}];
    if(['horizontal','vertical','text'].includes(a.tool)||points.length===2){
      saveDrawings([...a.drawings,{id:crypto.randomUUID(),tool:a.tool,points,
        text:a.tool==='text'?window.prompt('Chart note','Price level')??'Note':undefined}]);setPending([]);
    }else setPending(points);
  }
  function exportCSV(){const exported=new Map(bars.map(bar=>[bar.event_time,bar]));
    if(latestTick.current)exported.set(latestTick.current.bar.event_time,latestTick.current.bar);
    const csv=['event_time,open,high,low,close,volume,complete',...[...exported.values()].sort((a,b)=>a.event_time.localeCompare(b.event_time)).map(b=>[b.event_time,b.open,b.high,b.low,b.close,b.volume,b.complete??true].join(','))].join('\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download=market+'-'+timeframe+'.csv';a.click();URL.revokeObjectURL(url)}
  function screenshot(){if(!chartRef.current)return;const a=document.createElement('a');a.href=chartRef.current.takeScreenshot().toDataURL();a.download='quantpilot-chart.png';a.click()}
  const current=cursor??(stream.status==='live'?liveTick?.bar:undefined)??bars.at(-1),feed=storage?.feeds.find(f=>f.id==='live:collector');
  const live=feed?.state.status==='connected'&&Date.now()-Date.parse(feed.updated_at)<60000;
  const active=storage?.markets.find(item=>item.market===market||item.market==='BTCUSDT_PERP');
  const chartHeight=Math.max(580,Math.min(960,500+selected.filter(id=>study.catalogue.find(s=>s.id===id)?.pane==='oscillator').length*140));
  return <section className="trading-workspace" ref={shell} aria-label="Trading chart workspace">
    <div className="workspace-toolbar"><button className={engine==='stored'?'active':''} onClick={()=>setEngine('stored')}>Live chart / PostgreSQL history</button>
      <button className={engine==='tradingview'?'active':''} onClick={()=>setEngine('tradingview')}>Official TradingView chart</button></div>
    {engine==='tradingview'?<TradingViewPanel/>:<>
    <div className="workspace-toolbar"><strong aria-label="Chart market">BTCUSDT</strong>
      <div className="timeframe-buttons">{['5m','15m','30m','1h','4h','1d','1w'].map(value=><button key={value} className={timeframe===value?'active':''} onClick={()=>setTimeframe(value)}>{value}</button>)}</div>
      <select value={type} aria-label="Chart style" onChange={e=>setType(e.target.value)}>{['Candles','Bars','Line','Area'].map(t=><option key={t}>{t}</option>)}</select>
      <button onClick={()=>setShowStudies(!showStudies)}>Indicators ({selected.length})</button>
      <button aria-pressed={showRules} onClick={()=>setShowRules(!showRules)} title="Support/resistance, VWAP bands, the 4h 80% band and rule-engine trades">Rule levels</button><button onClick={()=>void loadRef.current()}>Refresh</button>
      <button onClick={()=>void shell.current?.requestFullscreen()}>Fullscreen</button></div>
    <div className="workspace-summary"><strong>{market} / {timeframe}</strong>
      <span>O {current?.open?.toFixed(2)??'-'} H {current?.high?.toFixed(2)??'-'} L {current?.low?.toFixed(2)??'-'} C {current?.close?.toFixed(2)??'-'}</span>
      <span>{loadedKey===market+':'+timeframe?'Chart ready':'Loading chart'}</span>
      <span aria-label="Last live price">Live {liveTick?.bar.close.toFixed(2)??'-'}</span>
      <span aria-label="Live stream status" data-event-time={stream.eventAt??''} data-received-at={stream.receivedAt??''} className={stream.status==='live'?'feed-live':'feed-stale'}>{stream.status==='live'?'Live stream connected':`Stream ${stream.status} · database fallback`}</span>
      <span aria-label="Last stored price">Stored {bars.at(-1)?.close.toFixed(2)??'-'}</span><span className={live?'feed-live':'feed-stale'}>{live?'Collector connected':'Collector disconnected / stale'}</span></div>
    {error&&<div role="status" className="workspace-alert">{error}</div>}
    {showStudies&&<div className="study-picker"><div><strong>Indicator catalogue: {study.catalogue.length}</strong><button onClick={()=>setShowStudies(false)}>Close</button></div>
      <input aria-label="Search indicators" placeholder="Search EMA, RSI, MACD, volume, pivots..." value={search} onChange={e=>setSearch(e.target.value)}/>
      <div className="study-options">{study.catalogue.filter(item=>item.label.toLowerCase().includes(search.toLowerCase())).map(item=><label key={item.id}>
        <input type="checkbox" checked={selected.includes(item.id)} onChange={()=>setSelected(old=>old.includes(item.id)?old.filter(id=>id!==item.id):[...old,item.id].slice(-12))}/>{item.label}<small>{item.pane}</small></label>)}</div>
      <p>Up to 12 active studies. Indicators use completed candles; normalized research features keep their own scales.</p></div>}
    <div className="workspace-body"><div className="drawing-toolbar" aria-label="Drawing tools">{tools.map(([id,label])=><button key={id} title={label} aria-label={label} disabled={loadedKey!==market+':'+timeframe} className={tool===id?'active':''} onClick={()=>{setTool(id);setPending([])}}>{symbols[id]}</button>)}
      <hr/><button title="Undo drawing" aria-label="Undo drawing" onClick={()=>saveDrawings(drawings.slice(0,-1))}>↶</button><button title="Clear drawings" aria-label="Clear drawings" onClick={()=>saveDrawings([])}>⌫</button>
      <button title="Fit chart" aria-label="Fit chart" onClick={()=>chartRef.current?.timeScale().fitContent()}>⊞</button><button title="Go to latest" aria-label="Go to latest" onClick={()=>chartRef.current?.timeScale().scrollToRealTime()}>»</button></div>
      <div className="workspace-canvas-wrap"><div ref={host} onClickCapture={draw} className="workspace-canvas" style={{height:chartHeight}} role="img" aria-label="Interactive BTCUSDT chart"/>
        {!bars.length&&<div className="workspace-empty">{busy?'Loading PostgreSQL candles...':'No data in this market yet. The historical job must complete.'}</div>}
        <svg className="drawing-layer" style={{height:chartRef.current?.panes()[0]?.getHeight()??450}} aria-label="Saved chart drawings" data-revision={revision}>
          {drawings.map(d=>{const a=coordinates(d.points[0]),b=d.points[1]?coordinates(d.points[1]):null;if(!a)return null;
            if(d.tool==='horizontal')return <line key={d.id} x1="0" x2="100%" y1={a.y} y2={a.y} stroke="#e7bd6d"/>;
            if(d.tool==='vertical')return <line key={d.id} x1={a.x} x2={a.x} y1="0" y2="100%" stroke="#8eaff8"/>;
            if(d.tool==='text')return <text key={d.id} x={a.x} y={a.y} fill="#e7bd6d" fontSize="13">{d.text}</text>;
            if(!b)return null;
            if(d.tool==='rectangle')return <rect key={d.id} x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)} width={Math.abs(b.x-a.x)} height={Math.abs(b.y-a.y)} fill="#4bd6ad18" stroke="#4bd6ad"/>;
            if(d.tool==='fib')return <g key={d.id}>{[0,.236,.382,.5,.618,.786,1].map(level=>{const y=a.y+(b.y-a.y)*level;return <g key={level}><line x1={Math.min(a.x,b.x)} x2={Math.max(a.x,b.x)} y1={y} y2={y} stroke={colors[Math.round(level*5)]}/><text x={Math.max(a.x,b.x)+4} y={y-3} fill="#c1cbd7" fontSize="10">{level} ({(d.points[0].price+(d.points[1].price-d.points[0].price)*level).toFixed(2)})</text></g>})}</g>;
            const end=d.tool==='ray'?{x:b.x+(b.x-a.x)*10,y:b.y+(b.y-a.y)*10}:b;
            return <g key={d.id}><line x1={a.x} x2={end.x} y1={a.y} y2={end.y} stroke="#6cbbe8" strokeWidth="1.5"/>{d.tool==='measure'&&<text x={(a.x+b.x)/2} y={(a.y+b.y)/2-8} fill="#e7bd6d" fontSize="12">{((d.points[1].price/d.points[0].price-1)*100).toFixed(2)}% / {Math.abs(d.points[1].time-d.points[0].time)/60} min</text>}</g>;
          })}{pending.map((point,i)=>{const p=coordinates(point);return p?<circle key={i} cx={p.x} cy={p.y} r="4" fill="#e7bd6d"/>:null})}</svg>
      </div><aside className="workspace-inspector"><h3>Workspace</h3><p>Storage <strong>{storage?.engine??'Connecting'}</strong></p><p>Stored candles <strong>{active?.rows.toLocaleString()??'0'}</strong></p>
        <small>First: {active?.first??'Pending backfill'}<br/>Last: {active?.last??'No observations'}</small><hr/>
        <h3>Live & storage</h3><p>Chart <strong>Exchange WebSocket</strong></p>
        <small>Last stream message: {liveTick?new Date(liveTick.receivedAt).toLocaleTimeString():'Waiting'}<br/>
          Last database observation: {feed?.state.last_persisted_at||bars.at(-1)?.received_at?new Date(feed?.state.last_persisted_at??bars.at(-1)!.received_at!).toLocaleTimeString():'Waiting'}<br/>
          PostgreSQL writes run independently, targeting every {feed?.state.poll_interval_seconds??5} seconds. Completed candles remain the indicator and prediction inputs.</small><hr/>
        <h3>Active indicators</h3>{selected.map((id,i)=><button className="study-chip" key={id} onClick={()=>setSelected(old=>old.filter(v=>v!==id))}><i style={{background:colors[i%colors.length]}}/>{study.catalogue.find(s=>s.id===id)?.label??id} ×</button>)}
        <h3>Saved drawings ({drawings.length})</h3><p>{pending.length?'Click the second anchor.':tool==='cursor'?'Scroll and drag to explore.':'Click on the price pane to draw.'}</p>
        <button onClick={()=>void loadRef.current(true)} disabled={busy||!bars.length}>Load older candles</button><button onClick={exportCSV} disabled={!bars.length}>Export candles CSV</button><button onClick={screenshot}>Save chart PNG</button>
        <small>Drawings and selected studies are saved in this browser. Market history and live candles are persisted in PostgreSQL. Research remains on its frozen dataset.</small></aside></div>
    <div className="workspace-bottom"><span>BTCUSDT · Binance USD-M perpetual · UTC · completed timeframes</span><a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts</a></div></>}
  </section>;
}
