import React,{useEffect,useRef,useState,lazy,Suspense} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity,Search} from 'lucide-react';
import {routes,fromHash,type Route} from './routes';
import {preview,type Candle} from './api';
import {subscribeKlines} from './liveFeed';
import {Boundary,Loading,money,signedPct,useResource} from './ui';
import './styles.css';

const Overview=lazy(()=>import('./pages/Overview'));
const TradingWorkspace=lazy(()=>import('./TradingWorkspace'));
const Ask=lazy(()=>import('./pages/Ask'));
const Research=lazy(()=>import('./pages/Research'));
const Paper=lazy(()=>import('./pages/Paper'));
const News=lazy(()=>import('./pages/News'));
const System=lazy(()=>import('./pages/System'));


function LivePrice(){
  const candles=useResource<Candle[]>('candles',60000);
  const [live,setLive]=useState<{price:number;status:string}|null>(null);
  useEffect(()=>{
    if(preview)return;
    return subscribeKlines('5m',tick=>setLive(l=>({price:tick.bar.close,status:l?.status??'live'})),
      state=>setLive(l=>({price:l?.price??NaN,status:state.status})));
  },[]);
  const bars=candles.data??[];
  const last=bars.at(-1)?.close,first=bars[0]?.open;
  const price=live&&Number.isFinite(live.price)?live.price:last;
  const change=price&&first?price/first-1:undefined;
  const streaming=live?.status==='live';
  return <div className="ticker" aria-label="Bitcoin price">
    <span className="coin" aria-hidden>₿</span>
    <div><strong>{money(price)}</strong><small className={change==null?'':change>=0?'tone-up':'tone-down'}>{signedPct(change)} 24h</small></div>
    <span className={'live-dot '+(streaming?'on':'')} title={streaming?'Live exchange stream':'Latest stored candle'}/>
  </div>;
}

function AskBox({onAsk}:{onAsk:(q:string)=>void}){
  const [text,setText]=useState('');
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const key=(e:KeyboardEvent)=>{
      const typing=e.target instanceof HTMLElement&&['INPUT','TEXTAREA'].includes(e.target.tagName);
      if(e.key==='/'&&!typing){e.preventDefault();input.current?.focus()}
    };
    addEventListener('keydown',key);return()=>removeEventListener('keydown',key);
  },[]);
  return <form className="askbox" role="search" onSubmit={e=>{e.preventDefault();if(text.trim()){onAsk(text.trim());setText('')}}}>
    <Search size={15} aria-hidden/>
    <input ref={input} aria-label="Ask a question" placeholder="Ask anything… e.g. chance BTC touches 90k this week" value={text}
      onChange={e=>setText(e.target.value)} maxLength={2000}/>
    <kbd aria-hidden>/</kbd>
  </form>;
}

// Fetch the other pages' code once the first page is idle, so switching tabs is instant.
const prefetch=()=>[()=>import('./pages/Research'),()=>import('./pages/Ask'),()=>import('./pages/Paper'),()=>import('./pages/News'),
  ()=>import('./pages/System'),()=>import('./TradingWorkspace')].forEach(load=>void load().catch(()=>{}));

function App(){
  const [route,setRoute]=useState<Route>(fromHash);
  const [seed,setSeed]=useState<{text:string;id:number}|null>(null);
  const health=useResource<{status:string}>('health',60000);
  useEffect(()=>{const t=setTimeout(prefetch,2500);return()=>clearTimeout(t)},[]);
  useEffect(()=>{const sync=()=>setRoute(fromHash());addEventListener('hashchange',sync);return()=>removeEventListener('hashchange',sync)},[]);
  const go=(next:Route)=>{if('#'+next!==location.hash)history.pushState(null,'','#'+next);setRoute(next);scrollTo({top:0})};
  const ask=(text:string)=>{setSeed({text,id:Date.now()});go('ask')};
  return <div className="shell">
    <nav className="sidebar" aria-label="Main navigation">
      <a className="brand" href="#overview" onClick={e=>{e.preventDefault();go('overview')}} aria-label="QuantPilot home">
        <span className="brand-mark"><Activity size={20}/></span><span className="brand-name">QuantPilot<small>BTC intelligence</small></span></a>
      <div className="nav-items">{routes.map(r=>{const Icon=r.icon;return <button key={r.id} type="button" aria-label={r.label}
        aria-current={route===r.id?'page':undefined} className={route===r.id?'active':''} onClick={()=>go(r.id)}>
        <Icon size={18} aria-hidden/><span>{r.label}</span></button>})}</div>
      <div className="sidebar-foot"><span className="pill pill-accent">Paper only</span><small>Research & simulation. No real-money execution.</small></div>
    </nav>
    <div className="main-col">
      <header className="topbar">
        <LivePrice/>
        <AskBox onAsk={ask}/>
        <a className="topbar-link" href="/docs" onClick={e=>{e.preventDefault();open('http://127.0.0.1:8000/docs','_blank')}}>API</a>
      </header>
      {preview&&<div className="banner banner-warn">INTERFACE PREVIEW · Synthetic values. No released model, measured results or paper trades.</div>}
      {health.error&&<div role="alert" className="banner banner-bad">QuantPilot services unavailable. Check the backend connection.
        <button type="button" onClick={health.reload}>Retry</button></div>}
      <main className="content">
        <Boundary key={route} name="This page"><Suspense fallback={<Loading lines={6}/>}>
          {route==='overview'&&<Overview go={go} ask={ask}/>}
          {route==='chart'&&<><div className="page-head"><div><h1>Chart</h1><p>Persistent history, 86 indicator studies, drawing tools and a live exchange stream.</p></div></div><TradingWorkspace/></>}
          {route==='ask'&&<Ask seed={seed}/>}
          {route==='research'&&<Research/>}
          {route==='paper'&&<Paper/>}
          {route==='news'&&<News/>}
          {route==='system'&&<System/>}
        </Suspense></Boundary>
      </main>
      <footer className="footer"><span>QuantPilot · BTCUSDT USD-M perpetual · research and paper simulation only</span>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
