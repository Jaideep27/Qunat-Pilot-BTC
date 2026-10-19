import {useEffect,useRef,useState} from 'react';

// Official hosted widget. Its feed is separate from our PostgreSQL-backed chart.
export default function TradingViewPanel(){
  const host=useRef<HTMLDivElement>(null);
  const [loaded,setLoaded]=useState(false);
  useEffect(()=>{
    const element=host.current;if(!element)return;
    const script=document.createElement('script');
    script.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.async=true;script.type='text/javascript';
    script.text=JSON.stringify({autosize:true,symbol:'BINANCE:BTCUSDT.P',interval:'5',timezone:'Etc/UTC',
      theme:'dark',style:'1',locale:'en',allow_symbol_change:false,hide_side_toolbar:false,
      hide_top_toolbar:false,withdateranges:true,save_image:true,calendar:false,
      support_host:'https://www.tradingview.com'});
    element.appendChild(script);
    const observer=new MutationObserver(()=>{
      const frame=element.querySelector('iframe');if(frame)frame.addEventListener('load',()=>setLoaded(true),{once:true});
    });observer.observe(element,{childList:true,subtree:true});
    return()=>{observer.disconnect();element.replaceChildren()};
  },[]);
  return <section className="panel"><div className="panel-source">Official TradingView hosted chart: its available indicators and drawing tools use TradingView's feed. Our live chart loads PostgreSQL history and streams BTCUSDT updates from Binance. Availability follows the provider's widget capabilities.</div>
    {!loaded&&<div className="workspace-alert">Connecting to TradingView. If this network blocks the provider, use our live chart and PostgreSQL history.</div>}
    <div className="tradingview-widget-container" ref={host} style={{height:820}}><div className="tradingview-widget-container__widget" style={{height:'100%',width:'100%'}}/></div>
    <div className="panel-source"><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Chart and data by TradingView</a></div></section>;
}
