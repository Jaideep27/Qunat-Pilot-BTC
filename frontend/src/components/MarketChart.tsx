import {useEffect,useRef,useState} from 'react';
import {createChart,CandlestickSeries,HistogramSeries,LineSeries,ColorType,type UTCTimestamp} from 'lightweight-charts';
import type {Candle} from '../lib/api';
import {preview} from '../lib/api';
import {subscribeKlines} from '../lib/liveFeed';
export default function MarketChart({candles,levels}:{candles:Candle[];levels:Record<string,number>}) {
  const container=useRef<HTMLDivElement>(null);
  const [status,setStatus]=useState(preview?'Fixture · stream disabled':'Connecting');
  const [livePrice,setLivePrice]=useState<number|null>(null);
  useEffect(()=>{
    if(!container.current || !candles.length) return;
    const chart=createChart(container.current,{autoSize:true,height:390,layout:{background:{type:ColorType.Solid,color:'#10151d'},textColor:'#7e8b9e',attributionLogo:true},
      grid:{vertLines:{color:'#19212c'},horzLines:{color:'#19212c'}},rightPriceScale:{borderColor:'#27313e'},timeScale:{borderColor:'#27313e',timeVisible:true}});
    const series=chart.addSeries(CandlestickSeries,{upColor:'#4bd6ad',downColor:'#ed7d80',borderVisible:false,wickUpColor:'#4bd6ad',wickDownColor:'#ed7d80'});
    const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume'});
    chart.priceScale('volume').applyOptions({scaleMargins:{top:.84,bottom:0},visible:false});
    const sorted=[...candles].sort((a,b)=>a.event_time.localeCompare(b.event_time));
    series.setData(sorted.map(c=>({time:Date.parse(c.event_time)/1000 as UTCTimestamp,open:c.open,high:c.high,low:c.low,close:c.close})));
    volume.setData(sorted.map(c=>({time:Date.parse(c.event_time)/1000 as UTCTimestamp,value:c.volume,color:c.close>=c.open?'#234e46':'#4a2c37'})));
    const ema=chart.addSeries(LineSeries,{color:'#b0a3fc',lineWidth:1,priceLineVisible:false,lastValueVisible:false});
    let average=sorted[0].close;
    ema.setData(sorted.map(c=>{average=average+(2/22)*(c.close-average);return {time:Date.parse(c.event_time)/1000 as UTCTimestamp,value:c.ema21??average};}));
    const last=sorted.at(-1)!;
    for(const [name,dist] of Object.entries(levels)){
      if(name.startsWith('prior_day_') && Number.isFinite(dist) && Number.isFinite(levels.atr)){
        const price=last.close+dist*levels.atr;
        series.createPriceLine({price,color:'#8d7a50',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:name.endsWith('high')?'PDH':'PDL'});
      }
    }
    chart.timeScale().fitContent();
    let stop:(()=>void)|undefined;
    if(!preview){
      let latest=Date.parse(last.event_time)/1000;
      stop=subscribeKlines('5m',tick=>{
        const time=Date.parse(tick.bar.event_time)/1000 as UTCTimestamp;
        if(time<latest)return;
        series.update({time,open:tick.bar.open,high:tick.bar.high,low:tick.bar.low,close:tick.bar.close});
        volume.update({time,value:tick.bar.volume,color:tick.bar.close>=tick.bar.open?'#234e46':'#4a2c37'});
        latest=time;setLivePrice(tick.bar.close);
      },state=>setStatus(state.status==='live'?'Connected · live candle stream':`Stream ${state.status} · last observation retained`));
    }
    return ()=>{stop?.();chart.remove();};
  },[candles,levels]);
  return <><div ref={container} className="market-chart" role="img" aria-label="BTCUSDT five-minute candlestick and volume chart"/><div className="chart-foot"><span className="dot"/>{status}{livePrice!==null&&<strong aria-label="Last live price">Live {livePrice.toFixed(2)}</strong>}<span className="legend">EMA 21 <i/> Prior day levels</span></div></>;
}

